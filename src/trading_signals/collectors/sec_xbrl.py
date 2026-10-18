"""XBRL ``companyfacts`` -> standardised quarterly financials (pure functions).

Input is the JSON of ``https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json``
(one request per company, all facts since the first XBRL filing). Output is one
dict per fiscal quarter for :class:`~trading_signals.db.models.sec.FinancialsQuarterly`.
No I/O, no database: everything here is unit-tested with small fixtures.

How a quarter is built
----------------------
* **Only us-gaap/dei facts in USD** (EPS in USD/shares) of ``10-Q``, ``10-K``
  (and ``/A``) filings, no dimensional (segment) facts - companyfacts contains
  none. IFRS filers (20-F/40-F) yield no rows.
* **As first reported:** if several filings report the same period (the
  original 10-Q, the comparative column of next year's 10-Q, a restating
  10-K/A), the fact with the earliest ``filed`` date wins. Restatements are
  ignored - the value is what the market saw first, and ``filed`` is the
  date it became public (point-in-time key).
* **Income-statement items** are reported as 3-month values in every 10-Q
  (Q1-Q3) and as a 12-month value in the 10-K. **Cash-flow items** are
  year-to-date only (3, 6, 9, 12 months). Both are turned into standalone
  quarters: a direct 3-month fact is used as is; otherwise the quarter is the
  difference between a cumulative fact and the next shorter cumulative fact of
  the same fiscal year (Q2 = 6M - 3M, Q3 = 9M - 6M, **Q4 = FY - 9M**). A
  difference is only accepted if it spans 80-105 days. EPS is derived the same
  way (an approximation: share counts change within a year); weighted-average
  shares are never derived.
* **Balance-sheet items** (cash, debt, shares) are instant facts taken at the
  quarter end.
* **Tags:** every figure has candidate us-gaap tags (``FLOW_SPECS`` /
  ``INSTANT_SPECS``). The base tag is the one with the most recent quarter among
  the well-covered ones; other tags only fill quarters the base lacks, and only
  if they agree with the base (1 %) on all common quarters (:func:`merge_series`).
  So a taxonomy hand-over is stitched, while different measures are never
  mixed (JPM's ``Revenues`` is another number than ``RevenuesNetOfInterestExpense``,
  XOM's ``Revenues`` another than ``RevenueFromContract...``).
* ``total_debt`` is the sum of the non-current and current long-term debt (plus
  commercial paper / short-term borrowings if the current portion is tagged
  separately); ``gross_profit`` falls back to revenue minus cost of revenue.
* **Point-in-time:** ``filed`` of a row is the latest ``filed`` of its
  income-statement / cash-flow figures. Balance-sheet figures (cash, debt,
  shares) that were first reported in a later filing are left NULL.
  ``raw_tags`` lists the tag of every figure and the figures that were derived.
* Known limit: a company that re-incorporated under a new CIK (holding-company
  reorganisation, e.g. ExxonMobil Holdings 2026) only has the quarters its new
  registrant filed.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

#: Forms whose facts are used (annual and quarterly reports and amendments).
PERIOD_FORMS = frozenset(
    {"10-Q", "10-K", "10-Q/A", "10-K/A", "10-KT", "10-KT/A", "10-QT"}
)

#: Duration classes in days (52/53-week years and 4-4-5 calendars included).
_LENGTHS = (("Q", 80, 105), ("H1", 165, 200), ("M9", 255, 290), ("FY", 340, 380))
#: Cumulative class -> the next shorter class it is differenced against.
_PREVIOUS = {"H1": "Q", "M9": "H1", "FY": "M9"}
#: Tolerance (days) for "same fiscal year start".
_START_TOLERANCE = 10
#: A derived quarter must span this many days.
_QUARTER_DAYS = (80, 105)

#: Abs. values above this are corrupt data (the columns are NUMERIC(24,2)).
_MAX_ABS = 1e21

#: figure -> (us-gaap tags in priority order, unit, additive)
FLOW_SPECS: dict[str, tuple[tuple[str, ...], str, bool]] = {
    "revenue": (
        (
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
            "RevenuesNetOfInterestExpense",
        ),
        "USD",
        True,
    ),
    "cost_of_revenue": (
        ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"),
        "USD",
        True,
    ),
    "gross_profit": (("GrossProfit",), "USD", True),
    "operating_income": (("OperatingIncomeLoss",), "USD", True),
    "net_income": (
        (
            "NetIncomeLoss",
            "ProfitLoss",
            "NetIncomeLossAvailableToCommonStockholdersBasic",
        ),
        "USD",
        True,
    ),
    "eps_diluted": (
        (
            "EarningsPerShareDiluted",
            "IncomeLossFromContinuingOperationsPerDilutedShare",
            "EarningsPerShareBasicAndDiluted",
            "EarningsPerShareBasic",
        ),
        "USD/shares",
        True,
    ),
    "operating_cash_flow": (
        (
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
        "USD",
        True,
    ),
    "capex": (
        (
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
            "PaymentsForCapitalImprovements",
        ),
        "USD",
        True,
    ),
}

#: figure -> (us-gaap tags in priority order, unit)
INSTANT_SPECS: dict[str, tuple[tuple[str, ...], str]] = {
    "cash_and_equivalents": (
        (
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "CashAndDueFromBanks",
            "Cash",
        ),
        "USD",
    ),
    "shares_outstanding": (("CommonStockSharesOutstanding",), "shares"),
}

#: Total-debt components (instant, USD).
DEBT_COMBINED = "DebtLongtermAndShorttermCombinedAmount"
DEBT_NONCURRENT = "LongTermDebtNoncurrent"
DEBT_CURRENT_TAGS = ("LongTermDebtCurrent", "DebtCurrent")
DEBT_SHORT_TAGS = ("ShortTermBorrowings", "CommercialPaper")
DEBT_TOTAL_FALLBACK = "LongTermDebt"

#: Figures stored in the row (in the order of the anchor choice).
ANCHOR_ORDER = (
    "revenue",
    "net_income",
    "eps_diluted",
    "operating_income",
    "operating_cash_flow",
)

#: Value columns of ``financials_quarterly`` (for the upsert).
VALUE_COLUMNS = (
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "eps_diluted",
    "operating_cash_flow",
    "capex",
    "free_cash_flow",
    "cash_and_equivalents",
    "total_debt",
    "shares_outstanding",
)


@dataclass(frozen=True)
class Fact:
    """One fact of a companyfacts concept."""

    start: date | None
    end: date
    val: float
    filed: date
    accn: str
    form: str
    fy: int | None
    fp: str | None


@dataclass(frozen=True)
class QPoint:
    """One fiscal quarter of one figure."""

    end: date
    val: float
    filed: date
    accn: str
    form: str
    fy: int | None
    fp: str | None  # Q1..Q4 (company's own fiscal labelling)
    derived: bool  # difference of cumulative facts


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    if math.isnan(f) or math.isinf(f) or abs(f) >= _MAX_ABS:
        return None
    return f


def load_facts(concept: dict | None, unit: str) -> list[Fact]:
    """Facts of a concept in ``unit`` from periodic reports, parsed and validated."""
    if not concept:
        return []
    entries = (concept.get("units") or {}).get(unit) or []
    facts: list[Fact] = []
    for e in entries:
        if not isinstance(e, dict) or e.get("form") not in PERIOD_FORMS:
            continue
        end, filed = _parse_date(e.get("end")), _parse_date(e.get("filed"))
        val = _number(e.get("val"))
        if end is None or filed is None or val is None:
            continue
        fy = e.get("fy")
        facts.append(
            Fact(
                start=_parse_date(e.get("start")),
                end=end,
                val=val,
                filed=filed,
                accn=str(e.get("accn") or ""),
                form=e["form"],
                fy=fy if isinstance(fy, int) else None,
                fp=e.get("fp") if isinstance(e.get("fp"), str) else None,
            )
        )
    return facts


def _earlier(a: Fact, b: Fact) -> bool:
    """True if ``a`` was filed before ``b`` (ties by accession number)."""
    return (a.filed, a.accn) < (b.filed, b.accn)


def _first_reported(facts: Iterable[Fact], key) -> dict:
    """The earliest-filed fact per ``key(fact)``."""
    best: dict = {}
    for f in facts:
        k = key(f)
        cur = best.get(k)
        if cur is None or _earlier(f, cur):
            best[k] = f
    return best


def _length_class(start: date, end: date) -> str | None:
    days = (end - start).days
    for name, low, high in _LENGTHS:
        if low <= days <= high:
            return name
    return None


def _label(fp: str | None) -> str | None:
    """Fiscal quarter label of a filing's ``fp`` (``FY`` -> the fourth quarter)."""
    if fp == "FY":
        return "Q4"
    return fp if fp in ("Q1", "Q2", "Q3", "Q4") else None


def _point(f: Fact, value: float, derived: bool) -> QPoint:
    return QPoint(
        end=f.end,
        val=value,
        filed=f.filed,
        accn=f.accn,
        form=f.form,
        fy=f.fy,
        fp=_label(f.fp),
        derived=derived,
    )


def _families(facts: list[Fact]) -> list[list[Fact]]:
    """Group duration facts of the same fiscal-year start (tolerance 10 days)."""
    families: list[list[Fact]] = []
    anchor: date | None = None
    for f in sorted(facts, key=lambda x: (x.start, x.end)):
        if anchor is not None and (f.start - anchor).days <= _START_TOLERANCE:
            families[-1].append(f)
        else:
            families.append([f])
            anchor = f.start
    return families


def flow_quarters(facts: list[Fact], additive: bool = True) -> dict[date, QPoint]:
    """Standalone quarters of a duration concept, keyed by quarter end.

    See the module docstring: direct 3-month facts first, then differences of
    cumulative facts (``additive`` series only). The earliest-filed fact per
    (start, end) wins.
    """
    periods = _first_reported(
        (f for f in facts if f.start is not None and f.end > f.start),
        lambda f: (f.start, f.end),
    )
    classified = [
        (cls, f)
        for f in periods.values()
        if (cls := _length_class(f.start, f.end)) is not None
    ]
    out: dict[date, QPoint] = {}
    direct: dict[date, Fact] = {}
    for cls, f in classified:
        if cls == "Q" and (f.end not in direct or _earlier(f, direct[f.end])):
            direct[f.end] = f
    for end, f in direct.items():
        out[end] = _point(f, f.val, derived=False)
    if not additive:
        return out

    for family in _families([f for _, f in classified]):
        family_ids = {id(f) for f in family}
        members = [(cls, f) for cls, f in classified if id(f) in family_ids]
        for cls, f in members:
            prev_cls = _PREVIOUS.get(cls)
            if prev_cls is None or f.end in out:
                continue
            earlier = [g for c, g in members if c == prev_cls and g.end < f.end]
            if not earlier:
                continue
            prev = max(earlier, key=lambda g: g.end)
            gap = (f.end - prev.end).days
            if _QUARTER_DAYS[0] <= gap <= _QUARTER_DAYS[1]:
                out[f.end] = _point(f, f.val - prev.val, derived=True)
    return out


def instant_values(facts: list[Fact]) -> dict[date, Fact]:
    """First-reported instant facts keyed by balance-sheet date."""
    return _first_reported((f for f in facts if f.start is None), lambda f: f.end)


#: Two tags of a figure are stitched only if their values agree on the overlap.
_STITCH_TOLERANCE = 0.01
#: A base tag needs at least this share of the best in-window coverage.
_BASE_MIN_COVERAGE = 0.5


def _agrees(a: float, b: float) -> bool:
    return abs(a - b) <= _STITCH_TOLERANCE * max(abs(a), abs(b))


def merge_series(
    candidates: dict[str, dict[date, Any]],
    priority: tuple[str, ...],
    window_start: date,
) -> dict[date, tuple[str, Any]]:
    """One series per figure: ``end -> (tag, point)`` (see the module docstring).

    The **base tag** is the one with the most recent quarter among the tags
    that cover at least half of the best coverage inside the window (ties:
    coverage, then the priority order). Other tags only fill quarters the base
    lacks - and only if they agree with the base (1 %) on every quarter both
    have, so a taxonomy hand-over (GOOGL: ``RevenueFromContract...`` ->
    ``Revenues``) is stitched while different measures (XOM ``Revenues`` vs
    ``RevenueFromContract...``, JPM ``Revenues`` vs ``RevenuesNetOfInterestExpense``)
    are not mixed.
    """
    live = {tag: series for tag, series in candidates.items() if series}
    if not live:
        return {}
    coverage = {
        tag: sum(1 for end in series if end >= window_start)
        for tag, series in live.items()
    }
    best = max(coverage.values())
    threshold = _BASE_MIN_COVERAGE * best
    eligible = [t for t in live if coverage[t] >= threshold] or list(live)
    base = max(
        eligible,
        key=lambda t: (max(live[t]), coverage[t], -priority.index(t)),
    )
    merged: dict[date, tuple[str, Any]] = {
        end: (base, point) for end, point in live[base].items()
    }
    others = sorted(
        (t for t in live if t != base), key=lambda t: (-coverage[t], priority.index(t))
    )
    for tag in others:
        series = live[tag]
        overlap = [end for end in series if end in merged]
        if not overlap or not all(
            _agrees(merged[end][1].val, series[end].val) for end in overlap
        ):
            continue
        for end, point in series.items():
            merged.setdefault(end, (tag, point))
    return merged


def _debt_at(
    end: date, series: dict[str, dict[date, Fact]], max_filed: date
) -> tuple[float, list[Fact], str] | None:
    """Total debt at a balance-sheet date, its source facts and a description.

    Components first (non-current + current long-term debt, plus commercial
    paper / short-term borrowings if the current portion does not already
    include them), then the combined tags. Facts first reported after
    ``max_filed`` are ignored (point-in-time).
    """

    def at(tag: str) -> Fact | None:
        fact = series.get(tag, {}).get(end)
        return fact if fact is not None and fact.filed <= max_filed else None

    noncurrent = at(DEBT_NONCURRENT)
    if noncurrent is not None:
        facts = [noncurrent]
        names = [DEBT_NONCURRENT]
        total = noncurrent.val
        for tag in DEBT_CURRENT_TAGS:
            cur = at(tag)
            if cur is None:
                continue
            total += cur.val
            facts.append(cur)
            names.append(tag)
            if tag == "LongTermDebtCurrent":
                # DebtCurrent already includes short-term borrowings
                for short_tag in DEBT_SHORT_TAGS:
                    short = at(short_tag)
                    if short is not None:
                        total += short.val
                        facts.append(short)
                        names.append(short_tag)
                        break
            break
        return total, facts, "+".join(names)
    for tag in (DEBT_COMBINED, DEBT_TOTAL_FALLBACK):
        fact = at(tag)
        if fact is not None:
            return fact.val, [fact], tag
    return None


def build_quarterly_rows(company_facts: dict, window_start: date) -> list[dict]:
    """Quarterly rows (without ticker/cik) from a companyfacts JSON.

    Only quarters ending on or after ``window_start`` (the retention window)
    are returned, oldest first. A row is only returned if at least one
    income-statement or cash-flow figure exists for the quarter.
    """
    facts = company_facts.get("facts") if isinstance(company_facts, dict) else None
    facts = facts or {}
    gaap: dict = facts.get("us-gaap") or {}
    dei: dict = facts.get("dei") or {}

    flows: dict[str, dict[date, tuple[str, QPoint]]] = {}
    for figure, (tags, unit, additive) in FLOW_SPECS.items():
        per_tag = {}
        for tag in tags:
            loaded = load_facts(gaap.get(tag), unit)
            if loaded:
                per_tag[tag] = flow_quarters(loaded, additive=additive)
        merged = merge_series(per_tag, tags, window_start)
        if merged:
            flows[figure] = merged

    instants: dict[str, dict[date, tuple[str, Fact]]] = {}
    for figure, (tags, unit) in INSTANT_SPECS.items():
        per_tag = {}
        for tag in tags:
            loaded = load_facts(gaap.get(tag), unit)
            if loaded:
                per_tag[tag] = instant_values(loaded)
        merged = merge_series(per_tag, tags, window_start)
        if merged:
            instants[figure] = merged

    debt_series = {
        tag: instant_values(load_facts(gaap.get(tag), "USD"))
        for tag in (
            DEBT_COMBINED,
            DEBT_NONCURRENT,
            *DEBT_CURRENT_TAGS,
            *DEBT_SHORT_TAGS,
            DEBT_TOTAL_FALLBACK,
        )
    }
    # cover-page share count (dei) by filing: fallback for shares_outstanding
    cover_shares: dict[str, float] = {}
    for f in sorted(
        load_facts(dei.get("EntityCommonStockSharesOutstanding"), "shares"),
        key=lambda x: x.end,
    ):
        cover_shares[f.accn] = f.val

    ends = sorted(
        {end for series in flows.values() for end in series if end >= window_start}
    )
    rows: list[dict] = []
    for end in ends:
        values: dict[str, float | None] = {}
        tags_used: dict[str, str] = {}
        derived: list[str] = []
        points: dict[str, QPoint] = {}
        sources: list[tuple[date, str]] = []  # (filed, accn) of every used value

        for figure in FLOW_SPECS:
            entry = flows.get(figure, {}).get(end)
            if entry is None:
                continue
            tag, point = entry
            values[figure] = point.val
            tags_used[figure] = tag
            points[figure] = point
            sources.append((point.filed, point.accn))
            if point.derived:
                derived.append(figure)

        if (
            "gross_profit" not in values
            and "revenue" in values
            and "cost_of_revenue" in values
        ):
            values["gross_profit"] = values["revenue"] - values["cost_of_revenue"]
            tags_used["gross_profit"] = (
                f"{tags_used['revenue']}-{tags_used['cost_of_revenue']}"
            )
            derived.append("gross_profit")

        anchor = next((points[f] for f in ANCHOR_ORDER if f in points), None)
        if anchor is None:
            continue

        # Point-in-time: the row is public when its income-statement / cash-flow
        # figures are (`filed`). Balance-sheet figures that were first reported
        # in a LATER filing (e.g. a debt tag that only the next 10-Q repeats
        # for the prior year-end) are left NULL instead of delaying the row.
        core_filed = max(filed for filed, _ in sources)

        for figure, series in instants.items():
            entry = series.get(end)
            if entry is not None and entry[1].filed <= core_filed:
                tag, fact = entry
                values[figure] = fact.val
                tags_used[figure] = tag
                sources.append((fact.filed, fact.accn))
        if "shares_outstanding" not in values and anchor.accn in cover_shares:
            values["shares_outstanding"] = cover_shares[anchor.accn]
            tags_used["shares_outstanding"] = "dei:EntityCommonStockSharesOutstanding"
        debt = _debt_at(end, debt_series, core_filed)
        if debt is not None:
            total, debt_facts, description = debt
            values["total_debt"] = total
            tags_used["total_debt"] = description
            sources.extend((f.filed, f.accn) for f in debt_facts)

        capex, ocf = values.get("capex"), values.get("operating_cash_flow")
        if capex is not None and ocf is not None:
            values["free_cash_flow"] = ocf - abs(capex)
            tags_used["free_cash_flow"] = "operating_cash_flow-abs(capex)"
            derived.append("free_cash_flow")

        filed, accn = max(sources)
        row: dict[str, Any] = {
            "period_end": end,
            "fiscal_year": anchor.fy,
            "fiscal_period": anchor.fp,
            "form": anchor.form,
            "filed": filed,
            "accession_number": accn or None,
            "raw_tags": {"tags": tags_used, "derived": sorted(set(derived))},
        }
        for column in VALUE_COLUMNS:
            value = values.get(column)
            row[column] = None if value is None else round(value, 4)
        rows.append(row)
    return rows
