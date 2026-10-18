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
    SHARES_FLAT_BAND,
    YEAR_DAYS,
    YEAR_TOLERANCE_DAYS,
)
from trading_signals.derived.context_pack.fmt import to_float
from trading_signals.derived.context_pack.rows import QuarterRow


def newest_first(quarters: Sequence[QuarterRow]) -> list[QuarterRow]:
    """One row per quarter end (the latest ``filed`` wins), newest quarter first."""
    best: dict[date, QuarterRow] = {}
    for q in quarters:
        cur = best.get(q.period_end)
        if cur is None or q.filed > cur.filed:
            best[q.period_end] = q
    return [best[end] for end in sorted(best, reverse=True)]


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
    "SharesChange",
    "free_cash_flow",
    "margin",
    "newest_first",
    "prior_year_quarter",
    "revenue_growth_yoy",
    "shares_change",
]
