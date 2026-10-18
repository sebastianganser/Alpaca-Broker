"""Database access of the context pack (the only module that queries).

:class:`PackLoader` is the interface the generator needs;
:class:`SqlPackLoader` implements it with **batch queries** (one per source
for all selected tickers, never per ticker x section): the nightly chain
generates the pack at ~04:30 and has to finish long before the open.

Point-in-time: the SQL bounds below are deliberately a *superset* (coarse
dates, the same predicates as ``derived/feature_pipeline.py``); the exact rule
is applied afterwards by :func:`trading_signals.derived.context_pack.pit.apply_pit`.
The cross-sectional queries (snapshots, fundamentals, closes) use only rows
dated on or before the session.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from typing import Any, Protocol

import pandas as pd
from sqlalchemy import (
    Boolean,
    DateTime,
    Text,
    and_,
    cast,
    func,
    or_,
    select,
    tuple_,
    type_coerce,
)
from sqlalchemy.dialects.postgresql import ARRAY, array
from sqlalchemy.orm import Session

from trading_signals.derived.context_pack.constants import (
    BARS_LOOKBACK_DAYS,
    EARNINGS_LOOKBACK_DAYS,
    FINANCIALS_LOOKBACK_DAYS,
    HEADLINE_WINDOW_DAYS,
    HOLDER_ROWS,
    INSIDER_WINDOW_DAYS,
    POLITICIAN_WINDOW_DAYS,
    RATING_WINDOW_DAYS,
    SEC_TEXT_FETCH_CHARS,
    SEC_WINDOW_DAYS,
    SNAPSHOT_MAX_AGE_DAYS,
)
from trading_signals.derived.context_pack.model import UniverseInfo
from trading_signals.derived.context_pack.pit import (
    EARNINGS_FORWARD_DAYS,
    session_close_et,
)
from trading_signals.derived.context_pack.rows import (
    ArkRow,
    BarRow,
    EarningsRow,
    EstimateRow,
    HeadlineRow,
    HolderRow,
    Holders13F,
    InsiderTradeRow,
    IvRow,
    MembershipRow,
    PoliticianRow,
    QuarterRow,
    RatingRow,
    RawSources,
    SecFilingRow,
    SecTextRow,
    ShortInterestRow,
    SourceStat,
    TechnicalRow,
)
from trading_signals.derived.feature_pipeline import (
    FORM13F_MAX_AGE_DAYS,
    SENTIMENT_MODEL_VERSION,
    news_cutoff_utc,
)
from trading_signals.derived.short_interest_features import publication_cutoff
from trading_signals.utils.logging import get_logger
from trading_signals.utils.price_targets import (
    KEY_ACTION,
    KEY_NEW,
    KEY_OLD,
    parse_price_target,
    parse_target_action,
)

logger = get_logger(__name__)

#: Rows per ticker kept of the scored headlines (the section shows fewer).
HEADLINE_FETCH_PER_TICKER = 15
#: Rows per ticker kept of the recent estimate snapshots.
IV_LOOKBACK_DAYS = 370
ARK_LOOKBACK_DAYS = 30
SHORT_INTEREST_LOOKBACK_DAYS = 85
#: Columns of ``fundamentals_snapshot`` that hold text (everything else is numeric).
FUNDAMENTALS_TEXT_COLUMNS = ("recommendation_key",)


class PackLoader(Protocol):
    """What the generator needs from the database (tests provide a fake)."""

    def snapshots(self, d: date) -> pd.DataFrame:
        """Feature snapshots of session ``d`` (features only, no targets)."""

    def universe(self, tickers: Sequence[str]) -> dict[str, UniverseInfo]:
        """Company / sector / industry per ticker."""

    def fundamentals(self, d: date) -> pd.DataFrame:
        """Latest fundamentals snapshot per ticker (<= 14 days old), by ticker."""

    def closes(self, refs: Sequence[date]) -> pd.DataFrame:
        """Real closes on the given dates: columns ``ticker, trade_date, close``."""

    def raw_sources(self, d: date, tickers: Sequence[str]) -> RawSources:
        """Raw-table rows of the selected tickers (superset, see module doc)."""

    def has_pack_candidates(self, d: date) -> bool:
        """True if ``pack_candidates`` already holds rows for session ``d``."""


def _f(x: Any) -> float | None:
    return None if x is None else float(x)


def _first_target(column: Any, raw_text: Any) -> float | None:
    """A price target: the column if it holds one, else the ``raw_data`` text."""
    value = parse_price_target(column)
    return value if value is not None else parse_price_target(raw_text)


def _frame(rows: Iterable[Any], columns: Sequence[str], table) -> pd.DataFrame:
    """DataFrame of query rows: numerics as float64, booleans kept as objects."""
    df = pd.DataFrame([tuple(r) for r in rows], columns=list(columns))
    for col in columns:
        if col in ("snapshot_date", "ticker"):
            continue
        if isinstance(table.c[col].type, Boolean):
            df[col] = df[col].map(lambda v: None if v is None else bool(v))
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _tickers_overlap(column: Any, tickers: Sequence[str]) -> Any:
    """``column && ARRAY[tickers]`` with both sides as ``text[]``.

    ``sec_filings.tickers`` is ``varchar(20)[]``; psycopg2 sends a Python list as
    an untyped ``ARRAY['A', 'B']`` (``text[]``) and PostgreSQL does not compare
    ``varchar[]`` with ``text[]``, so both operands are cast explicitly.
    """
    return cast(column, ARRAY(Text)).overlap(array(list(tickers), type_=Text))


class SqlPackLoader:
    """:class:`PackLoader` on a SQLAlchemy session (read-only)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ── Cross-section ────────────────────────────────────────────────

    def has_pack_candidates(self, d: date) -> bool:
        from trading_signals.db.models.forecasts import PackCandidate

        return (
            self.session.execute(
                select(PackCandidate.ticker)
                .where(PackCandidate.session_date == d)
                .limit(1)
            ).first()
            is not None
        )

    def snapshots(self, d: date) -> pd.DataFrame:
        from trading_signals.db.models.features import (
            FEATURE_COLUMNS,
            FeatureSnapshot,
        )

        table = FeatureSnapshot.__table__
        cols = ["snapshot_date", "ticker", *FEATURE_COLUMNS]
        rows = self.session.execute(
            select(*[table.c[c] for c in cols]).where(
                FeatureSnapshot.snapshot_date == d
            )
        ).all()
        return _frame(rows, cols, table)

    def universe(self, tickers: Sequence[str]) -> dict[str, UniverseInfo]:
        from trading_signals.db.models.universe import Universe

        rows = self.session.execute(
            select(
                Universe.ticker,
                Universe.company_name,
                Universe.sector,
                Universe.industry,
            ).where(Universe.ticker.in_(list(tickers)))
        ).all()
        return {
            t: UniverseInfo(name, sector, industry)
            for t, name, sector, industry in rows
        }

    def fundamentals(self, d: date) -> pd.DataFrame:
        from trading_signals.db.models.fundamentals import FundamentalsSnapshot as F

        rows = (
            self.session.execute(
                select(F)
                .where(F.snapshot_date <= d)
                .where(F.snapshot_date >= d - timedelta(days=SNAPSHOT_MAX_AGE_DAYS))
                .distinct(F.ticker)
                .order_by(F.ticker, F.snapshot_date.desc())
            )
            .scalars()
            .all()
        )
        cols = [c.name for c in F.__table__.columns if c.name != "fetched_at"]
        data = [{c: getattr(r, c) for c in cols} for r in rows]
        df = pd.DataFrame(data, columns=cols)
        for c in cols:
            if c not in (
                "ticker",
                "snapshot_date",
                "most_recent_quarter",
                *FUNDAMENTALS_TEXT_COLUMNS,
            ):
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.set_index("ticker", drop=False)

    def closes(self, refs: Sequence[date]) -> pd.DataFrame:
        from trading_signals.db.models.prices import PriceDaily as P

        rows = self.session.execute(
            select(P.ticker, P.trade_date, P.close)
            .where(P.trade_date.in_(list(refs)))
            .where(P.is_extrapolated.is_not(True))
            .where(P.close.is_not(None))
        ).all()
        df = pd.DataFrame(
            [tuple(r) for r in rows], columns=["ticker", "trade_date", "close"]
        )
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return df

    # ── Raw tables of the selected tickers ───────────────────────────

    def raw_sources(self, d: date, tickers: Sequence[str]) -> RawSources:
        tickers = sorted(set(tickers))
        raw = RawSources()
        if not tickers:
            return raw
        for loader in (
            self._insider,
            self._ratings,
            self._politicians,
            self._headlines,
            self._earnings,
            self._estimates,
            self._short_interest,
            self._ark,
            self._iv,
            self._technical,
            self._memberships,
            self._holders,
            self._bars,
            self._sec_filings,
            self._sec_coverage,
            self._financials,
            self._stats,
        ):
            loader(d, tickers, raw)
        return raw

    def _insider(self, d: date, tickers: list[str], raw: RawSources) -> None:
        from trading_signals.db.models.insider import InsiderTrade as T

        close_d = session_close_et(d)
        since = d - timedelta(days=INSIDER_WINDOW_DAYS)
        placed = func.coalesce(T.filing_date, T.transaction_date)
        rows = self.session.execute(
            select(
                T.ticker,
                T.insider_name,
                T.insider_title,
                T.transaction_type,
                T.transaction_date,
                T.filing_date,
                T.acceptance_datetime,
                T.shares,
                T.price_per_share,
                T.total_value,
                T.shares_owned_after,
            )
            .where(T.ticker.in_(tickers))
            .where(T.transaction_type.in_(("P", "S")))
            .where(T.is_derivative.is_(False))
            .where(placed >= since)
            # superset of "public by d" (pit.insider_public_by decides)
            .where(
                or_(
                    T.acceptance_datetime < close_d,
                    T.filing_date <= d,
                    and_(T.filing_date.is_(None), T.acceptance_datetime.is_(None)),
                )
            )
        ).all()
        for r in rows:
            raw.get(r[0]).insider.append(
                InsiderTradeRow(
                    ticker=r[0],
                    name=r[1],
                    title=r[2],
                    transaction_type=r[3],
                    transaction_date=r[4],
                    filing_date=r[5],
                    acceptance=r[6],
                    shares=_f(r[7]),
                    price=_f(r[8]),
                    value=_f(r[9]),
                    shares_owned_after=_f(r[10]),
                )
            )

    def _ratings(self, d: date, tickers: list[str], raw: RawSources) -> None:
        from trading_signals.db.models.fundamentals import AnalystRating as R

        rows = self.session.execute(
            select(
                R.ticker,
                R.rating_date,
                R.firm,
                R.action,
                R.rating_old,
                R.rating_new,
                R.price_target_old,
                R.price_target_new,
                # Yahoo's own fields, stored as text in raw_data: the columns of
                # older rows are still NULL (scripts/repair/
                # backfill_analyst_price_targets.py fills them)
                R.raw_data[KEY_OLD].astext,
                R.raw_data[KEY_NEW].astext,
                R.raw_data[KEY_ACTION].astext,
            )
            .where(R.ticker.in_(tickers))
            .where(R.rating_date <= d)
            .where(R.rating_date >= d - timedelta(days=RATING_WINDOW_DAYS))
        ).all()
        for r in rows:
            raw.get(r[0]).ratings.append(
                RatingRow(
                    ticker=r[0],
                    rating_date=r[1],
                    firm=r[2],
                    action=r[3],
                    rating_old=r[4],
                    rating_new=r[5],
                    # the column wins, raw_data is the fallback
                    target_old=_first_target(r[6], r[8]),
                    target_new=_first_target(r[7], r[9]),
                    target_action=parse_target_action(r[10]),
                )
            )

    def _politicians(self, d: date, tickers: list[str], raw: RawSources) -> None:
        from trading_signals.db.models.politicians import PoliticianTrade as P

        rows = self.session.execute(
            select(
                P.ticker,
                P.politician_name,
                P.chamber,
                P.party,
                P.state,
                P.transaction_type,
                P.transaction_date,
                P.disclosure_date,
                P.amount_range,
                P.owner,
            )
            .where(P.ticker.in_(tickers))
            .where(P.disclosure_date.is_not(None))
            .where(P.disclosure_date <= d)
            .where(P.disclosure_date >= d - timedelta(days=POLITICIAN_WINDOW_DAYS))
        ).all()
        for r in rows:
            raw.get(r[0]).politicians.append(
                PoliticianRow(
                    ticker=r[0],
                    name=r[1],
                    chamber=r[2],
                    party=r[3],
                    state=r[4],
                    transaction_type=r[5],
                    transaction_date=r[6],
                    disclosure_date=r[7],
                    amount_range=r[8],
                    owner=r[9],
                )
            )

    def _headlines(self, d: date, tickers: list[str], raw: RawSources) -> None:
        from trading_signals.db.models.news import NewsArticle as A
        from trading_signals.db.models.news import NewsSentiment as S

        cutoff = news_cutoff_utc(d)
        rn = (
            func.row_number()
            .over(partition_by=S.ticker, order_by=A.published_at.desc())
            .label("rn")
        )
        inner = (
            select(
                S.ticker,
                # bypass TZDateTime (it returns naive Berlin wall clock): the
                # point-in-time check needs the absolute, timezone-aware instant
                type_coerce(A.published_at, DateTime(timezone=True)).label(
                    "published_at"
                ),
                A.headline,
                A.source,
                S.sentiment_label,
                S.sentiment_score,
                rn,
            )
            .join(A, S.article_id == A.id)
            .where(S.ticker.in_(tickers))
            .where(S.model_version == SENTIMENT_MODEL_VERSION)
            .where(A.published_at < cutoff)
            .where(A.published_at >= cutoff - timedelta(days=HEADLINE_WINDOW_DAYS))
        ).subquery()
        rows = self.session.execute(
            select(inner).where(inner.c.rn <= HEADLINE_FETCH_PER_TICKER)
        ).all()
        for r in rows:
            raw.get(r[0]).headlines.append(
                HeadlineRow(
                    ticker=r[0],
                    published_at=r[1],
                    headline=r[2],
                    source=r[3],
                    label=r[4],
                    score=_f(r[5]),
                )
            )

    def _earnings(self, d: date, tickers: list[str], raw: RawSources) -> None:
        from trading_signals.db.models.fundamentals import EarningsCalendar as E

        rows = self.session.execute(
            select(
                E.ticker,
                E.earnings_date,
                E.time_of_day,
                E.eps_estimate,
                E.eps_actual,
                E.surprise_pct,
                E.first_seen,
            )
            .where(E.ticker.in_(tickers))
            .where(E.earnings_date >= d - timedelta(days=EARNINGS_LOOKBACK_DAYS))
            .where(E.earnings_date <= d + timedelta(days=EARNINGS_FORWARD_DAYS))
        ).all()
        for r in rows:
            raw.get(r[0]).earnings.append(
                EarningsRow(
                    ticker=r[0],
                    earnings_date=r[1],
                    time_of_day=r[2],
                    eps_estimate=_f(r[3]),
                    eps_actual=_f(r[4]),
                    surprise_pct=_f(r[5]),
                    first_seen=r[6],
                )
            )

    def _estimates(self, d: date, tickers: list[str], raw: RawSources) -> None:
        from trading_signals.db.models.estimates import EstimatesSnapshot as E

        rows = self.session.execute(
            select(
                E.ticker,
                E.as_of,
                E.period,
                E.eps_avg,
                E.eps_n_analysts,
                E.eps_current,
                E.eps_7d_ago,
                E.eps_30d_ago,
                E.eps_90d_ago,
                E.rev_up_7d,
                E.rev_down_7d,
                E.rev_up_30d,
                E.rev_down_30d,
                E.revenue_avg,
                E.revenue_growth,
                E.eps_low,
                E.eps_high,
                E.eps_year_ago,
                E.eps_growth,
                E.revenue_low,
                E.revenue_high,
                E.revenue_n_analysts,
                E.revenue_year_ago,
            )
            .where(E.ticker.in_(tickers))
            .where(E.as_of <= d)
            .where(E.as_of >= d - timedelta(days=SNAPSHOT_MAX_AGE_DAYS))
        ).all()
        for r in rows:
            raw.get(r[0]).estimates.append(
                EstimateRow(
                    ticker=r[0],
                    as_of=r[1],
                    period=r[2],
                    eps_avg=_f(r[3]),
                    eps_n_analysts=r[4],
                    eps_current=_f(r[5]),
                    eps_7d_ago=_f(r[6]),
                    eps_30d_ago=_f(r[7]),
                    eps_90d_ago=_f(r[8]),
                    rev_up_7d=r[9],
                    rev_down_7d=r[10],
                    rev_up_30d=r[11],
                    rev_down_30d=r[12],
                    revenue_avg=_f(r[13]),
                    revenue_growth=_f(r[14]),
                    eps_low=_f(r[15]),
                    eps_high=_f(r[16]),
                    eps_year_ago=_f(r[17]),
                    eps_growth=_f(r[18]),
                    revenue_low=_f(r[19]),
                    revenue_high=_f(r[20]),
                    revenue_n_analysts=r[21],
                    revenue_year_ago=_f(r[22]),
                )
            )

    def _short_interest(self, d: date, tickers: list[str], raw: RawSources) -> None:
        from trading_signals.db.models.short_interest import ShortInterest as S

        cutoff = publication_cutoff(d)
        if cutoff is None:
            return
        rows = self.session.execute(
            select(
                S.ticker,
                S.settlement_date,
                S.short_interest,
                S.days_to_cover,
                S.fetched_at,
            )
            .where(S.ticker.in_(tickers))
            .where(S.settlement_date <= cutoff)
            .where(
                S.settlement_date
                >= cutoff - timedelta(days=SHORT_INTEREST_LOOKBACK_DAYS)
            )
            .order_by(S.settlement_date, S.fetched_at)
        ).all()
        # a later fetch of the same settlement wins (as in the feature pipeline)
        latest: dict[tuple[str, date], ShortInterestRow] = {}
        for r in rows:
            latest[(r[0], r[1])] = ShortInterestRow(
                ticker=r[0],
                settlement_date=r[1],
                short_interest=_f(r[2]),
                days_to_cover=_f(r[3]),
            )
        for row in latest.values():
            raw.get(row.ticker).short_interest.append(row)

    def _ark(self, d: date, tickers: list[str], raw: RawSources) -> None:
        from trading_signals.db.models.ark import ARKHolding as H

        rows = self.session.execute(
            select(H.ticker, H.snapshot_date, H.etf_ticker, H.weight_pct, H.shares)
            .where(H.ticker.in_(tickers))
            .where(H.snapshot_date <= d)
            .where(H.snapshot_date >= d - timedelta(days=ARK_LOOKBACK_DAYS))
        ).all()
        for r in rows:
            raw.get(r[0]).ark.append(
                ArkRow(
                    ticker=r[0],
                    snapshot_date=r[1],
                    etf=r[2],
                    weight_pct=_f(r[3]),
                    shares=_f(r[4]),
                )
            )

    def _iv(self, d: date, tickers: list[str], raw: RawSources) -> None:
        from trading_signals.db.models.options_iv import OptionsIVSnapshot as O

        rows = self.session.execute(
            select(
                O.ticker,
                O.snapshot_date,
                O.atm_iv_30d,
                O.skew_25d,
                O.term_slope,
                O.put_call_oi,
            )
            .where(O.ticker.in_(tickers))
            .where(O.snapshot_date <= d)
            .where(O.snapshot_date >= d - timedelta(days=IV_LOOKBACK_DAYS))
        ).all()
        for r in rows:
            raw.get(r[0]).iv.append(
                IvRow(
                    ticker=r[0],
                    snapshot_date=r[1],
                    atm_iv_30d=_f(r[2]),
                    skew_25d=_f(r[3]),
                    term_slope=_f(r[4]),
                    put_call_oi=_f(r[5]),
                )
            )

    def _technical(self, d: date, tickers: list[str], raw: RawSources) -> None:
        from trading_signals.db.models.technical_indicators import (
            TechnicalIndicator as T,
        )

        rows = self.session.execute(
            select(T.ticker, T.trade_date, T.macd, T.macd_signal, T.macd_histogram)
            .where(T.ticker.in_(tickers))
            .where(T.trade_date <= d)
            .where(T.trade_date >= d - timedelta(days=SNAPSHOT_MAX_AGE_DAYS))
            .distinct(T.ticker)
            .order_by(T.ticker, T.trade_date.desc())
        ).all()
        for r in rows:
            raw.get(r[0]).technical = TechnicalRow(
                ticker=r[0],
                trade_date=r[1],
                macd=_f(r[2]),
                macd_signal=_f(r[3]),
                macd_histogram=_f(r[4]),
            )

    def _memberships(self, d: date, tickers: list[str], raw: RawSources) -> None:
        from trading_signals.db.models.index_membership import IndexMembership as M

        rows = self.session.execute(
            select(M.ticker, M.index_name, M.valid_from, M.valid_to, M.source)
            .where(M.ticker.in_(tickers))
            .where(M.valid_from <= d)
            .where(or_(M.valid_to.is_(None), M.valid_to > d))
        ).all()
        for r in rows:
            raw.get(r[0]).memberships.append(
                MembershipRow(
                    ticker=r[0],
                    index_name=r[1],
                    valid_from=r[2],
                    valid_to=r[3],
                    source=r[4],
                )
            )

    def _holders(self, d: date, tickers: list[str], raw: RawSources) -> None:
        """13F: latest public period vs the one before, plus the top holders.

        Three queries on ``form13f_holdings``: (1) per-period summaries of
        every ticker, (2) the top holders of each ticker's latest period,
        (3) those holders' positions in the previous period.
        """
        from trading_signals.db.models.form13f import Form13FHolding as H

        visible = and_(H.filing_date <= d, H.put_call == "SH")
        floor = d - timedelta(days=FORM13F_MAX_AGE_DAYS + 120)
        rows = self.session.execute(
            select(
                H.ticker,
                H.report_period,
                func.count(func.distinct(H.filer_cik)),
                func.sum(H.shares),
                func.max(H.filing_date),
            )
            .where(H.ticker.in_(tickers))
            .where(visible)
            .where(H.report_period >= floor)
            .group_by(H.ticker, H.report_period)
        ).all()
        periods: dict[str, list[tuple]] = {}
        for r in rows:
            periods.setdefault(r[0], []).append(r[1:])
        latest: dict[str, tuple] = {}
        previous: dict[str, tuple] = {}
        for ticker, items in periods.items():
            items.sort(key=lambda x: x[0], reverse=True)
            latest[ticker] = items[0]
            if len(items) > 1:
                previous[ticker] = items[1]
        if not latest:
            return
        # the "N" of "x of N tracked funds": every filer that filed for the period
        tracked = {
            r[0]: int(r[1])
            for r in self.session.execute(
                select(H.report_period, func.count(func.distinct(H.filer_cik)))
                .where(visible)
                .where(H.report_period >= floor)
                .group_by(H.report_period)
            ).all()
        }

        grouped = (
            select(
                H.ticker.label("ticker"),
                H.report_period.label("period"),
                H.filer_cik.label("cik"),
                func.max(H.filer_name).label("name"),
                func.sum(H.shares).label("shares"),
                func.sum(H.market_value).label("value"),
            )
            .where(
                tuple_(H.ticker, H.report_period).in_(
                    [(t, v[0]) for t, v in latest.items()]
                )
            )
            .where(visible)
            .group_by(H.ticker, H.report_period, H.filer_cik)
        ).subquery()
        ranked = select(
            grouped,
            func.row_number()
            .over(
                partition_by=grouped.c.ticker,
                order_by=grouped.c.value.desc().nulls_last(),
            )
            .label("rn"),
        ).subquery()
        top_rows = self.session.execute(
            select(ranked).where(ranked.c.rn <= HOLDER_ROWS)
        ).all()
        top_by_ticker: dict[str, list[tuple]] = {}
        for r in top_rows:
            top_by_ticker.setdefault(r.ticker, []).append(r)

        prev_shares: dict[tuple[str, str], float | None] = {}
        wanted = [
            (t, previous[t][0], r.cik)
            for t, rs in top_by_ticker.items()
            if t in previous
            for r in rs
        ]
        if wanted:
            prev_rows = self.session.execute(
                select(H.ticker, H.filer_cik, func.sum(H.shares))
                .where(tuple_(H.ticker, H.report_period, H.filer_cik).in_(wanted))
                .where(visible)
                .group_by(H.ticker, H.filer_cik)
            ).all()
            prev_shares = {(r[0], r[1]): _f(r[2]) for r in prev_rows}

        for ticker, (period, n_filers, shares_total, max_filing) in latest.items():
            prev = previous.get(ticker)
            top = tuple(
                HolderRow(
                    filer_name=r.name,
                    shares=_f(r.shares),
                    value=_f(r.value),
                    prev_shares=prev_shares.get((ticker, r.cik)) if prev else None,
                )
                for r in sorted(top_by_ticker.get(ticker, []), key=lambda x: x.rn)
            )
            raw.get(ticker).holders = Holders13F(
                report_period=period,
                max_filing_date=max_filing,
                n_filers=int(n_filers),
                shares_total=_f(shares_total),
                prev_period=prev[0] if prev else None,
                prev_n_filers=int(prev[1]) if prev else None,
                prev_shares_total=_f(prev[2]) if prev else None,
                top=top,
                n_tracked=tracked.get(period),
                prev_n_tracked=tracked.get(prev[0]) if prev else None,
            )

    def _bars(self, d: date, tickers: list[str], raw: RawSources) -> None:
        """Real daily bars up to ``d`` (chart, ranges, volatility, reactions)."""
        from trading_signals.db.models.prices import PriceDaily as P

        rows = self.session.execute(
            select(P.ticker, P.trade_date, P.open, P.high, P.low, P.close, P.volume)
            .where(P.ticker.in_(tickers))
            .where(P.trade_date <= d)
            .where(P.trade_date >= d - timedelta(days=BARS_LOOKBACK_DAYS))
            .where(P.is_extrapolated.is_not(True))
            .where(P.close.is_not(None))
            .order_by(P.ticker, P.trade_date)
        ).all()
        for r in rows:
            raw.get(r[0]).bars.append(
                BarRow(
                    trade_date=r[1],
                    open=_f(r[2]),
                    high=_f(r[3]),
                    low=_f(r[4]),
                    close=_f(r[5]),
                    volume=_f(r[6]),
                )
            )

    def _sec_filings(self, d: date, tickers: list[str], raw: RawSources) -> None:
        """SEC filings of the last 90 days plus the texts of those that have one.

        The coarse bound is ``filing_date <= d`` (EDGAR's filing date is never
        before the acceptance date, so every filing accepted before 16:00 ET on
        ``d`` is inside); :func:`~.pit.apply_pit` applies the exact acceptance
        rule. Share classes of one CIK (GOOG / GOOGL) share the row: it is
        attached to every selected ticker of its ``tickers`` array.
        """
        from trading_signals.db.models.sec import SecFiling as F
        from trading_signals.db.models.sec import SecFilingText as X

        wanted = set(tickers)
        rows = self.session.execute(
            select(
                F.accession_number,
                F.tickers,
                F.form,
                F.filing_date,
                F.acceptance_datetime,
                F.items,
                F.is_amendment,
            )
            .where(_tickers_overlap(F.tickers, tickers))
            .where(F.filing_date <= d)
            .where(F.filing_date >= d - timedelta(days=SEC_WINDOW_DAYS))
        ).all()
        if not rows:
            return
        texts: dict[str, list[SecTextRow]] = {}
        text_rows = self.session.execute(
            select(
                X.accession_number,
                X.exhibit,
                # only the head of the excerpt: the section prints ~500 characters
                func.left(X.excerpt, SEC_TEXT_FETCH_CHARS),
            )
            .where(X.accession_number.in_([r[0] for r in rows]))
            .order_by(X.accession_number, X.exhibit)
        ).all()
        for accession, exhibit, excerpt in text_rows:
            if excerpt:
                texts.setdefault(accession, []).append(SecTextRow(exhibit, excerpt))
        for accession, members, form, filed, accepted, items, amendment in rows:
            for ticker in sorted(wanted.intersection(members or ())):
                raw.get(ticker).sec_filings.append(
                    SecFilingRow(
                        ticker=ticker,
                        accession_number=accession,
                        form=form,
                        filing_date=filed,
                        acceptance=accepted,
                        items=tuple(items or ()),
                        is_amendment=bool(amendment),
                        texts=tuple(texts.get(accession, ())),
                    )
                )

    def _sec_coverage(self, d: date, tickers: list[str], raw: RawSources) -> None:
        """First / last filing date and number of stored filings per ticker.

        Tells "the source covers this company but nothing was filed in the last
        90 days" from "the source knows nothing about it" (no CIK, never
        collected). Grouped by the ``tickers`` array, so a share class that has
        no row under its own ``ticker`` is covered through its siblings' rows.
        """
        from trading_signals.db.models.sec import SecFiling as F

        rows = self.session.execute(
            select(
                F.tickers,
                func.min(F.filing_date),
                func.max(F.filing_date),
                func.count(),
            )
            .where(_tickers_overlap(F.tickers, tickers))
            # the EDGAR rule of pit.edgar_public_by: a filing accepted after the
            # close of d must not even show up in the counts
            .where(F.filing_date <= d)
            .where(
                or_(
                    F.acceptance_datetime.is_(None),
                    F.acceptance_datetime < session_close_et(d),
                )
            )
            .group_by(F.tickers)
        ).all()
        wanted = set(tickers)
        merged: dict[str, tuple[date, date, int]] = {}
        for members, first, last, n in rows:
            for ticker in wanted.intersection(members or ()):
                old = merged.get(ticker)
                merged[ticker] = (
                    (first, last, int(n))
                    if old is None
                    else (min(old[0], first), max(old[1], last), old[2] + int(n))
                )
        for ticker, (first, last, n) in merged.items():
            raw.get(ticker).stats["sec_filings"] = SourceStat(first, last, n)

    def _financials(self, d: date, tickers: list[str], raw: RawSources) -> None:
        """Quarterly XBRL financials published by ``d`` (``filed <= d``)."""
        from trading_signals.db.models.sec import FinancialsQuarterly as Q

        rows = self.session.execute(
            select(
                Q.ticker,
                Q.period_end,
                Q.filed,
                Q.fiscal_period,
                Q.form,
                Q.revenue,
                Q.gross_profit,
                Q.operating_income,
                Q.net_income,
                Q.eps_diluted,
                Q.operating_cash_flow,
                Q.capex,
                Q.free_cash_flow,
                Q.cash_and_equivalents,
                Q.total_debt,
                Q.shares_outstanding,
            )
            .where(Q.ticker.in_(tickers))
            .where(Q.filed <= d)
            .where(Q.period_end >= d - timedelta(days=FINANCIALS_LOOKBACK_DAYS))
        ).all()
        for r in rows:
            raw.get(r[0]).quarters.append(
                QuarterRow(
                    period_end=r[1],
                    filed=r[2],
                    fiscal_period=r[3],
                    form=r[4],
                    revenue=_f(r[5]),
                    gross_profit=_f(r[6]),
                    operating_income=_f(r[7]),
                    net_income=_f(r[8]),
                    eps_diluted=_f(r[9]),
                    operating_cash_flow=_f(r[10]),
                    capex=_f(r[11]),
                    free_cash_flow=_f(r[12]),
                    cash_and_equivalents=_f(r[13]),
                    total_debt=_f(r[14]),
                    shares_outstanding=_f(r[15]),
                )
            )

    def _stats(self, d: date, tickers: list[str], raw: RawSources) -> None:
        """First / last observation and observation days of the young sources."""
        from trading_signals.db.models.estimates import EstimatesSnapshot as E
        from trading_signals.db.models.news import NewsArticle as A
        from trading_signals.db.models.news import NewsSentiment as S
        from trading_signals.db.models.options_iv import OptionsIVSnapshot as O
        from trading_signals.db.models.prices import PriceDaily as P
        from trading_signals.db.models.short_interest import ShortVolume as V

        news_day = func.date(A.published_at)
        specs: list[tuple[str, Any]] = [
            (
                "prices",
                select(
                    P.ticker,
                    func.min(P.trade_date),
                    func.max(P.trade_date),
                    func.count(),
                )
                .where(
                    P.ticker.in_(tickers),
                    P.trade_date <= d,
                    P.is_extrapolated.is_not(True),
                )
                .group_by(P.ticker),
            ),
            (
                "estimates",
                select(
                    E.ticker,
                    func.min(E.as_of),
                    func.max(E.as_of),
                    func.count(func.distinct(E.as_of)),
                )
                .where(E.ticker.in_(tickers), E.as_of <= d)
                .group_by(E.ticker),
            ),
            (
                "options_iv",
                select(
                    O.ticker,
                    func.min(O.snapshot_date),
                    func.max(O.snapshot_date),
                    func.count(),
                )
                .where(O.ticker.in_(tickers), O.snapshot_date <= d)
                .group_by(O.ticker),
            ),
            (
                "short_volume",
                select(
                    V.ticker,
                    func.min(V.trade_date),
                    func.max(V.trade_date),
                    func.count(),
                )
                .where(V.ticker.in_(tickers), V.trade_date <= d)
                .group_by(V.ticker),
            ),
            (
                "news",
                select(
                    S.ticker,
                    func.min(news_day),
                    func.max(news_day),
                    func.count(func.distinct(news_day)),
                )
                .join(A, S.article_id == A.id)
                .where(
                    S.ticker.in_(tickers),
                    S.model_version == SENTIMENT_MODEL_VERSION,
                    A.published_at < news_cutoff_utc(d),
                )
                .group_by(S.ticker),
            ),
        ]
        for source, stmt in specs:
            for ticker, first, last, n in self.session.execute(stmt).all():
                raw.get(ticker).stats[source] = SourceStat(first, last, int(n or 0))


__all__ = ["PackLoader", "SqlPackLoader"]
