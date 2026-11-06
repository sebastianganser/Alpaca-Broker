"""Quarterly financials of section 4 (pure functions, no I/O).

Source: ``financials_quarterly`` (SEC XBRL ``companyfacts``, weekly collector,
``collectors/sec_xbrl.py``). A row is one standalone fiscal quarter in USD as
FIRST reported; the point-in-time key is ``filed`` (applied by :mod:`.pit`).

Nothing here invents a number: a derived figure is ``None`` unless every input
exists (``None`` = not reported / not derivable, never zero).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from trading_signals.derived.context_pack.constants import (
    QUARTER_STALE_DAYS_10K,
    QUARTER_STALE_DAYS_10Q,
    SHARES_FLAT_BAND,
    YEAR_DAYS,
    YEAR_TOLERANCE_DAYS,
)
from trading_signals.derived.context_pack.fmt import to_float
from trading_signals.derived.context_pack.rows import QuarterRow, SecFilingRow

#: The reports that carry the figures of a quarter.
PERIODIC_FORMS = frozenset({"10-Q", "10-K"})


def newest_first(quarters: Sequence[QuarterRow]) -> list[QuarterRow]:
    """One row per quarter end (the latest ``filed`` wins), newest quarter first."""
    best: dict[date, QuarterRow] = {}
    for q in quarters:
        cur = best.get(q.period_end)
        if cur is None or q.filed > cur.filed:
            best[q.period_end] = q
    return [best[end] for end in sorted(best, reverse=True)]


def filings_after_table(
    quarters: Sequence[QuarterRow], filings: Sequence[SecFilingRow]
) -> list[SecFilingRow]:
    """Visible 10-Qs / 10-Ks filed after the newest quarter's first report.

    Newest filing first.

    The table can lag behind section 15: the collector runs weekly, the SEC
    publishes the XBRL facts of a filing with a delay, and some filings never reach
    its ``companyfacts`` feed (2026-10-30: 20 of 40 sampled tickers).

    The reference is the earliest ``filed`` of the newest quarter end: a row's
    ``filed`` is the latest ``filed`` of its figures, so an older quarter completed
    by a late amendment, or a restated row of the newest quarter, would otherwise
    hide a missing report. An original 10-Q / 10-K filed after that first report
    belongs to a later quarter.
    """
    if not quarters:
        return []
    newest_end = max(q.period_end for q in quarters)
    first_report = min(q.filed for q in quarters if q.period_end == newest_end)
    newer = [
        f
        for f in filings
        if f.form in PERIODIC_FORMS and f.filing_date > first_report
    ]
    return sorted(newer, key=lambda f: f.filing_date, reverse=True)


def stale_table_days(quarters: Sequence[QuarterRow], session: date) -> int | None:
    """Age of the newest quarter end if a newer report should exist by now, else None.

    Catches the lag that :func:`filings_after_table` cannot see: a missing report
    filed before the 90-day window of section 15. After fiscal Q3 the next report
    is the 10-K with its longer deadline; an unknown fiscal period gets that
    lenient limit, too (a false alarm misleads more than a late one).
    """
    if not quarters:
        return None
    newest = newest_first(quarters)[0]
    limit = (
        QUARTER_STALE_DAYS_10Q
        if newest.fiscal_period in ("Q1", "Q2", "Q4")
        else QUARTER_STALE_DAYS_10K
    )
    age = (session - newest.period_end).days
    return age if age > limit else None


def free_cash_flow(q: QuarterRow) -> float | None:
    """The stored free cash flow, else operating cash flow minus capex.

    The collector already derives ``free_cash_flow = operating_cash_flow -
    abs(capex)`` whenever both exist (``sec_xbrl.build_quarterly_rows``), so the
    fallback only covers a row that lacks the stored figure; it uses the same
    formula and never guesses a missing input.
    """
    stored = to_float(q.free_cash_flow)
    if stored is not None:
        return stored
    ocf, capex = to_float(q.operating_cash_flow), to_float(q.capex)
    if ocf is None or capex is None:
        return None
    return ocf - abs(capex)


def margin(part: float | None, revenue: float | None) -> float | None:
    """``part / revenue`` (a fraction); ``None`` without both or with revenue <= 0."""
    p, r = to_float(part), to_float(revenue)
    if p is None or r is None or r <= 0:
        return None
    return p / r


def prior_year_quarter(
    q: QuarterRow, quarters: Sequence[QuarterRow]
) -> QuarterRow | None:
    """The quarter about one year before ``q`` (``None`` if there is none).

    Its end lies ``YEAR_DAYS`` +/- ``YEAR_TOLERANCE_DAYS`` days before ``q.period_end``
    (52/53-week fiscal calendars move a quarter end by a few days); the closest one
    wins.
    """
    target = q.period_end - timedelta(days=YEAR_DAYS)
    near = [
        c
        for c in quarters
        if abs((c.period_end - target).days) <= YEAR_TOLERANCE_DAYS
        and c.period_end < q.period_end
    ]
    if not near:
        return None
    return min(near, key=lambda c: (abs((c.period_end - target).days), c.period_end))


def revenue_growth_yoy(q: QuarterRow, quarters: Sequence[QuarterRow]) -> float | None:
    """Revenue growth against the same quarter a year earlier (a fraction).

    ``None`` if that quarter does not exist, or one of the two revenues is missing
    or the earlier one is not positive.
    """
    prior = prior_year_quarter(q, quarters)
    now = to_float(q.revenue)
    before = to_float(prior.revenue) if prior is not None else None
    if now is None or before is None or before <= 0:
        return None
    return now / before - 1.0


@dataclass(frozen=True)
class SharesChange:
    """Change of the share count between the newest quarter and ~1 year before."""

    latest_end: date
    latest: float
    prior_end: date
    prior: float

    @property
    def change(self) -> float:
        return self.latest / self.prior - 1.0

    @property
    def verdict(self) -> str:
        """Net buyback / dilution / hardly changed (a band absorbs date noise)."""
        if self.change <= -SHARES_FLAT_BAND:
            return "netto Aktienrückkäufe"
        if self.change >= SHARES_FLAT_BAND:
            return "netto Verwässerung"
        return "kaum verändert"


def shares_change(quarters: Sequence[QuarterRow]) -> SharesChange | None:
    """Share count of the newest quarter that reports one vs the quarter ~1 year before.

    ``None`` if there is no share count, no quarter a year earlier, or that quarter
    has none.
    """
    ordered = newest_first(quarters)
    latest = next((q for q in ordered if _positive(q.shares_outstanding)), None)
    if latest is None:
        return None
    prior = prior_year_quarter(latest, ordered)
    if prior is None or not _positive(prior.shares_outstanding):
        return None
    return SharesChange(
        latest_end=latest.period_end,
        latest=float(latest.shares_outstanding),
        prior_end=prior.period_end,
        prior=float(prior.shares_outstanding),
    )


def _positive(x: float | None) -> bool:
    v = to_float(x)
    return v is not None and v > 0


__all__ = [
    "PERIODIC_FORMS",
    "SharesChange",
    "filings_after_table",
    "free_cash_flow",
    "margin",
    "newest_first",
    "prior_year_quarter",
    "revenue_growth_yoy",
    "shares_change",
    "stale_table_days",
]
