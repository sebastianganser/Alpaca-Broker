"""Point-in-time visibility of the raw-table rows shown in a ticker file.

A pack for session ``d`` may only show what was public at the close of ``d``
(16:00 New York) - the same cut-offs as the feature pipeline
(``derived/feature_pipeline.py`` module docstring, DECISIONS_FEATURES
"Information Cut-offs"):

* Form 4: ``acceptance_datetime`` < 16:00 ET on ``d`` when known, else
  ``filing_date <= d``, else ``transaction_date + 4 days <= d``;
* SEC filings (``sec_filings``): the same EDGAR rule without the third step -
  ``acceptance_datetime`` < 16:00 ET on ``d`` when known, else
  ``filing_date <= d`` (:func:`edgar_public_by`, shared with Form 4);
* quarterly financials (``financials_quarterly``): ``filed <= d`` (a date-only
  source, like 13F ``filing_date``);
* news: ``published_at`` < 16:00 ET on ``d`` (:func:`news_cutoff_utc`);
* 13F: ``filing_date <= d``;
* politicians: ``disclosure_date <= d``;
* analyst ratings: ``rating_date <= d``;
* earnings: a *future* date only if it was first seen by ``d``;
* FINRA short interest: ``settlement_date`` <= the 9th NYSE session before
  ``d`` (publication lag, ``short_interest_features.publication_cutoff``);
* snapshot-type sources (estimates, IV, fundamentals, ARK): ``as_of`` /
  ``snapshot_date <= d``.
* real daily price bars (chart, ranges, volatility, earnings reactions):
  ``trade_date <= d``.

The loader (``loader.py``) fetches a superset with coarse SQL bounds; this
module is the single, unit-tested place where the exact rule is applied
(:func:`apply_pit`), so a leaky loader cannot reach a section.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from trading_signals.derived.context_pack.rows import (
    EarningsRow,
    InsiderTradeRow,
    PoliticianRow,
    QuarterRow,
    SecFilingRow,
    SourceStat,
    TickerRaw,
)
from trading_signals.derived.feature_pipeline import (
    MARKET_CLOSE_ET,
    NY_TZ,
    news_cutoff_utc,
)
from trading_signals.derived.insider_clusters import FALLBACK_FILING_LAG_DAYS
from trading_signals.derived.short_interest_features import publication_cutoff

#: A future earnings date is only shown within this many days (as the
#: ``earnings_days_until`` feature).
EARNINGS_FORWARD_DAYS = 90


def _aware(dt: datetime) -> datetime:
    """Naive timestamps are taken as UTC (the DB columns are timestamptz)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def session_close_et(d: date) -> datetime:
    """16:00 America/New_York on ``d`` (timezone-aware)."""
    return datetime.combine(d, MARKET_CLOSE_ET, tzinfo=NY_TZ)


def edgar_public_by(
    acceptance: datetime | None, filing_date: date | None, d: date
) -> bool | None:
    """The EDGAR rule shared by Form 4 and the SEC filings table.

    Public at the close of ``d`` if EDGAR accepted the filing before 16:00 ET on
    ``d`` (``acceptance``, strict: accepted AT the close is not yet public); if
    the acceptance time is unknown the filing date decides (``filing_date <= d``).
    ``None`` = neither is known (the caller applies its own fallback).
    """
    if acceptance is not None:
        return _aware(acceptance) < session_close_et(d)
    if filing_date is not None:
        return filing_date <= d
    return None


def insider_public_by(t: InsiderTradeRow, d: date) -> bool:
    """Was this Form 4 trade public at the close of ``d``?"""
    decided = edgar_public_by(t.acceptance, t.filing_date, d)
    if decided is not None:
        return decided
    return t.transaction_date is not None and t.transaction_date <= d - timedelta(
        days=FALLBACK_FILING_LAG_DAYS
    )


def sec_filing_public_by(f: SecFilingRow, d: date) -> bool:
    """Was this SEC filing public at the close of ``d``? (the Form 4 rule)"""
    return bool(edgar_public_by(f.acceptance, f.filing_date, d))


def quarter_public_by(q: QuarterRow, d: date) -> bool:
    """Was this quarterly row public by ``d``? (``filed`` is a date: ``filed <= d``)"""
    return q.filed <= d


def news_public_by(published_at: datetime, d: date) -> bool:
    """Was this article published before 16:00 ET on ``d``?"""
    return _aware(published_at) < news_cutoff_utc(d)


def politician_public_by(p: PoliticianRow, d: date) -> bool:
    """Disclosed by ``d`` (rows without both dates cannot be placed)."""
    return (
        p.disclosure_date is not None
        and p.transaction_date is not None
        and p.disclosure_date <= d
    )


def earnings_visible(e: EarningsRow, d: date) -> bool:
    """Is this earnings row shown for session ``d``?

    Past rows (``earnings_date < d``) need a reported ``eps_actual``. A date
    on or after ``d`` is an announced date: it counts only within
    :data:`EARNINGS_FORWARD_DAYS` and if it was first seen by ``d`` (rows
    first seen only after their own date were collected retroactively and keep
    the documented lookahead of the feature pipeline, M2).
    """
    if e.earnings_date < d:
        return e.eps_actual is not None
    if e.earnings_date > d + timedelta(days=EARNINGS_FORWARD_DAYS):
        return False
    if e.first_seen is None:
        return True
    seen = e.first_seen.date() if isinstance(e.first_seen, datetime) else e.first_seen
    return seen <= d or seen > e.earnings_date


def _stat_visible(stat: SourceStat, d: date) -> bool:
    return stat.last is None or stat.last <= d


def apply_pit(raw: TickerRaw, d: date) -> TickerRaw:
    """The part of ``raw`` that was public at the close of ``d``."""
    cutoff = publication_cutoff(d)
    holders = raw.holders
    if holders is not None and holders.max_filing_date > d:
        holders = None
    technical = raw.technical
    if technical is not None and technical.trade_date > d:
        technical = None
    return replace(
        raw,
        insider=[t for t in raw.insider if insider_public_by(t, d)],
        ratings=[r for r in raw.ratings if r.rating_date <= d],
        politicians=[p for p in raw.politicians if politician_public_by(p, d)],
        headlines=[h for h in raw.headlines if news_public_by(h.published_at, d)],
        earnings=[e for e in raw.earnings if earnings_visible(e, d)],
        estimates=[e for e in raw.estimates if e.as_of <= d],
        short_interest=[
            s
            for s in raw.short_interest
            if cutoff is not None and s.settlement_date <= cutoff
        ],
        ark=[a for a in raw.ark if a.snapshot_date <= d],
        iv=[i for i in raw.iv if i.snapshot_date <= d],
        technical=technical,
        memberships=[
            m
            for m in raw.memberships
            if m.valid_from <= d and (m.valid_to is None or m.valid_to > d)
        ],
        holders=holders,
        bars=[b for b in raw.bars if b.trade_date <= d],
        sec_filings=[f for f in raw.sec_filings if sec_filing_public_by(f, d)],
        quarters=[q for q in raw.quarters if quarter_public_by(q, d)],
        stats={k: v for k, v in raw.stats.items() if _stat_visible(v, d)},
    )


__all__ = [
    "EARNINGS_FORWARD_DAYS",
    "apply_pit",
    "earnings_visible",
    "edgar_public_by",
    "insider_public_by",
    "news_public_by",
    "politician_public_by",
    "quarter_public_by",
    "sec_filing_public_by",
    "session_close_et",
]
