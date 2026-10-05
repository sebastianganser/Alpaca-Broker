"""Gap Detection & Repair for time-series data.

Before each collector run, the GapDetector checks for missing
trading days and attempts to repair them:
  1. Fetch missing data from the source (real data)
  2. If source has no data: extrapolate via forward-fill

Extrapolated data is always marked with is_extrapolated=TRUE
so downstream features can filter or weight it appropriately.

Rules (M2):
  * Detection is bounded by the retention window (``data_start_date()``)
    and uses ONE grouped query for all tickers; per-date lookups are only
    done for tickers whose row count is below the expected session count.
  * Real data fetched later replaces extrapolated rows
    (``ON CONFLICT DO UPDATE … WHERE prices_daily.is_extrapolated``) but
    never overwrites real rows.
  * Extrapolation only happens if the source request *succeeded* but had
    no bar for the date AND the date is at least
    ``min_extrapolate_age_days`` old – a single transient empty answer for
    a recent session never produces synthetic data.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd
import pandas_market_calendars as mcal
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_signals.collectors._db import bulk_insert
from trading_signals.collectors._parsing import safe_float, safe_int
from trading_signals.db.models.prices import PriceDaily
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

#: Columns overwritten when real data replaces an extrapolated row.
_PRICE_UPDATE_COLS = (
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "source",
    "is_extrapolated",
    "fetched_at",
)


@dataclass
class GapRepairResult:
    """Summary of gap detection and repair for a single run."""

    gaps_detected: int = 0
    gaps_repaired: int = 0  # successfully fetched from source
    gaps_extrapolated: int = 0  # forward-filled
    gaps_unfixable: int = 0  # should be 0
    details: dict[str, list[str]] = field(default_factory=dict)


class GapDetector:
    """Detect and repair data gaps in price history.

    Uses the NYSE trading calendar to determine expected trading days.
    Only checks from a ticker's first data point onward – so a ticker
    that started collecting yesterday won't report "all of history" as gaps.
    """

    def __init__(self, session: Session, source: str = "yfinance") -> None:
        self.session = session
        self.source = source
        self._calendar = mcal.get_calendar("NYSE")

    def get_expected_trading_days(self, start: date, end: date) -> list[date]:
        """Return list of expected NYSE trading days in the range [start, end]."""
        schedule = self._calendar.schedule(
            start_date=pd.Timestamp(start),
            end_date=pd.Timestamp(end),
        )
        return [ts.date() for ts in schedule.index]

    def detect_gaps(
        self,
        ticker: str,
        start_bound: date | None = None,
        end: date | None = None,
    ) -> list[date]:
        """Find missing trading days for a ticker.

        Looks from the ticker's earliest data point (but not before
        ``start_bound``) to ``end`` (default: yesterday – today's data may
        not be available yet).
        """
        # Find the ticker's first and last data point
        stmt = select(
            func.min(PriceDaily.trade_date),
            func.max(PriceDaily.trade_date),
        ).where(PriceDaily.ticker == ticker)
        if start_bound:
            stmt = stmt.where(PriceDaily.trade_date >= start_bound)
        result = self.session.execute(stmt).one()
        first_date, last_date = result

        if first_date is None:
            # No data at all – not a gap, ticker just hasn't been collected yet
            return []

        check_end = self._check_end(last_date, end)
        if first_date >= check_end:
            return []

        expected_days = set(self.get_expected_trading_days(first_date, check_end))
        actual_days = self._actual_days([ticker], first_date, check_end).get(
            ticker, set()
        )
        return sorted(expected_days - actual_days)

    def detect_gaps_bulk(
        self,
        tickers: list[str],
        start_bound: date | None = None,
        end: date | None = None,
    ) -> dict[str, list[date]]:
        """Detect gaps for multiple tickers with one grouped query.

        Returns only tickers that have gaps (empty dict = no gaps).
        """
        if not tickers:
            return {}
        limit = end or (date.today() - timedelta(days=1))
        stmt = (
            select(
                PriceDaily.ticker,
                func.min(PriceDaily.trade_date),
                func.max(PriceDaily.trade_date),
                func.count(),
            )
            .where(PriceDaily.ticker.in_(tickers))
            .where(PriceDaily.trade_date <= limit)
            .group_by(PriceDaily.ticker)
        )
        if start_bound:
            stmt = stmt.where(PriceDaily.trade_date >= start_bound)
        stats = self.session.execute(stmt).all()

        # Tickers whose row count is below the expected session count
        ranges = [
            (ticker, first_date, last_date, count)
            for ticker, first_date, last_date, count in stats
            if first_date is not None and first_date < last_date
        ]
        if not ranges:
            return {}
        all_days = self.get_expected_trading_days(
            min(r[1] for r in ranges), max(r[2] for r in ranges)
        )
        candidates: dict[str, tuple[date, date]] = {}
        for ticker, first_date, last_date, count in ranges:
            n_expected = sum(1 for d in all_days if first_date <= d <= last_date)
            if count < n_expected:
                candidates[ticker] = (first_date, last_date)
        if not candidates:
            return {}

        lo = min(v[0] for v in candidates.values())
        hi = max(v[1] for v in candidates.values())
        actual = self._actual_days(list(candidates), lo, hi)
        gaps: dict[str, list[date]] = {}
        for ticker, (first_date, check_end) in candidates.items():
            expected = {d for d in all_days if first_date <= d <= check_end}
            missing = sorted(expected - actual.get(ticker, set()))
            if missing:
                gaps[ticker] = missing
        return gaps

    @staticmethod
    def _check_end(last_date: date | None, end: date | None) -> date:
        # Check up to yesterday (today may not be available yet)
        limit = end or (date.today() - timedelta(days=1))
        return min(last_date, limit) if last_date else limit

    def _actual_days(
        self, tickers: list[str], start: date, end: date
    ) -> dict[str, set[date]]:
        stmt = (
            select(PriceDaily.ticker, PriceDaily.trade_date)
            .where(PriceDaily.ticker.in_(tickers))
            .where(PriceDaily.trade_date >= start)
            .where(PriceDaily.trade_date <= end)
        )
        out: dict[str, set[date]] = {}
        for row in self.session.execute(stmt).all():
            out.setdefault(row[0], set()).add(row[1])
        return out

    def repair_gaps(
        self,
        gaps: dict[str, list[date]],
        fetch_fn: Callable[[list[str], date, date], pd.DataFrame] | None = None,
        *,
        batch_fetch_fn: Callable[[list[str], date, date], dict[str, pd.DataFrame]]
        | None = None,
        batch_size: int = 100,
        min_extrapolate_age_days: int = 0,
        today: date | None = None,
    ) -> GapRepairResult:
        """Attempt to repair detected gaps.

        Strategy:
          1. Try to fetch real data via ``batch_fetch_fn`` (many tickers per
             request, returns ``{ticker: DataFrame}``) or ``fetch_fn``
             (one ticker per request, returns a DataFrame).
          2. Extrapolate (forward-fill) remaining dates – only for tickers
             whose fetch succeeded and only dates older than
             ``min_extrapolate_age_days``.

        All HTTP work happens before the first write so that no write
        transaction is held open during network calls.

        Args:
            gaps: Dict of {ticker: [missing_dates]}.
            fetch_fn: Optional callable(tickers, start, end) -> DataFrame.
                      If None (and no batch_fetch_fn), skips directly to
                      extrapolation.
        """
        result = GapRepairResult()
        total_gaps = sum(len(dates) for dates in gaps.values())
        result.gaps_detected = total_gaps

        if total_gaps == 0:
            return result

        logger.info(f"Gap repair: {total_gaps} gaps across {len(gaps)} tickers")

        # ── Phase 1: fetch (HTTP only, no writes) ──
        frames: dict[str, pd.DataFrame | None] = {}
        failed: set[str] = set()
        tickers = sorted(gaps)
        if batch_fetch_fn:
            for i in range(0, len(tickers), batch_size):
                batch = tickers[i : i + batch_size]
                start = min(min(gaps[t]) for t in batch)
                end = max(max(gaps[t]) for t in batch) + timedelta(days=1)
                try:
                    data = batch_fetch_fn(batch, start, end) or {}
                    for t in batch:
                        frames[t] = data.get(t)
                except Exception as e:
                    failed.update(batch)
                    logger.warning(
                        f"Gap repair batch fetch failed ({len(batch)} tickers): {e}"
                    )
        elif fetch_fn:
            for t in tickers:
                try:
                    start = min(gaps[t])
                    end = max(gaps[t]) + timedelta(days=1)  # yfinance end is exclusive
                    frames[t] = fetch_fn([t], start, end)
                except Exception as e:
                    failed.add(t)
                    logger.warning(f"Gap repair fetch failed for {t}: {e}")

        # ── Phase 2: write ──
        cutoff = (today or date.today()) - timedelta(days=min_extrapolate_age_days)
        for ticker in tickers:
            missing_dates = gaps[ticker]
            result.details[ticker] = []

            # Step 1: store fetched real data
            fetched_dates: set[date] = set()
            df = frames.get(ticker)
            if df is not None and not getattr(df, "empty", True):
                fetched_dates = self._store_frame(ticker, df, set(missing_dates))
                result.gaps_repaired += len(fetched_dates)
                if fetched_dates:
                    result.details[ticker].append(
                        f"Fetched {len(fetched_dates)} days from source"
                    )

            if ticker in failed:
                continue  # never extrapolate on a failed request

            # Step 2: Extrapolate remaining (old enough) gaps
            remaining = [
                d for d in missing_dates if d not in fetched_dates and d <= cutoff
            ]
            if remaining:
                extrapolated = self._extrapolate(ticker, remaining)
                result.gaps_extrapolated += extrapolated
                if extrapolated:
                    result.details[ticker].append(f"Extrapolated {extrapolated} days")

        unfixable = (
            result.gaps_detected - result.gaps_repaired - result.gaps_extrapolated
        )
        result.gaps_unfixable = max(0, unfixable)

        logger.info(
            f"Gap repair complete: "
            f"{result.gaps_repaired} repaired, "
            f"{result.gaps_extrapolated} extrapolated, "
            f"{result.gaps_unfixable} unfixable"
        )
        return result

    def _try_fetch(
        self,
        ticker: str,
        missing_dates: list[date],
        fetch_fn: Callable,
    ) -> set[date]:
        """Try to fetch real data for specific missing dates."""
        start = min(missing_dates)
        end = max(missing_dates) + timedelta(days=1)  # yfinance end is exclusive

        df = fetch_fn([ticker], start, end)
        if df is None or df.empty:
            return set()
        return self._store_frame(ticker, df, set(missing_dates))

    def _store_frame(
        self, ticker: str, df: pd.DataFrame, wanted: set[date]
    ) -> set[date]:
        """Upsert rows of ``df`` whose date is in ``wanted``.

        Real data replaces extrapolated rows, never real ones.
        """
        rows: list[dict] = []
        fetched: set[date] = set()
        for trade_date, row in df.iterrows():
            d = trade_date.date() if hasattr(trade_date, "date") else trade_date
            if d not in wanted:
                continue
            close = safe_float(row.get("Close"))
            if close is None:
                continue
            adj = safe_float(row.get("Adj Close"))
            rows.append(
                {
                    "ticker": ticker,
                    "trade_date": d,
                    "open": safe_float(row.get("Open")),
                    "high": safe_float(row.get("High")),
                    "low": safe_float(row.get("Low")),
                    "close": close,
                    "adj_close": adj if adj is not None else close,
                    "volume": safe_int(row.get("Volume")),
                    "source": self.source,
                    "is_extrapolated": False,
                    "fetched_at": func.now(),
                }
            )
            fetched.add(d)

        if rows:
            bulk_insert(
                self.session,
                PriceDaily,
                rows,
                ["ticker", "trade_date"],
                update_cols=_PRICE_UPDATE_COLS,
                update_where=PriceDaily.is_extrapolated.is_(True),
            )
            self.session.flush()
        return fetched

    def _extrapolate(self, ticker: str, missing_dates: list[date]) -> int:
        """Fill missing dates using forward-fill from last known value.

        Rules:
          - close/adj_close = last known close (forward-fill)
          - open/high/low = same as close (flat candle = no trade)
          - volume = 0
          - is_extrapolated = TRUE
          - source = 'extrapolated'
        """
        # Find the last known real price before the gaps
        earliest_gap = min(missing_dates)
        stmt = (
            select(PriceDaily)
            .where(PriceDaily.ticker == ticker)
            .where(PriceDaily.trade_date < earliest_gap)
            .where(PriceDaily.is_extrapolated.is_(False))
            .order_by(PriceDaily.trade_date.desc())
            .limit(1)
        )
        last_real = self.session.execute(stmt).scalar_one_or_none()

        if last_real is None:
            # No prior data to extrapolate from – can't do anything
            logger.warning(f"Cannot extrapolate {ticker}: no prior real data")
            return 0

        fill_close = last_real.close
        fill_adj_close = last_real.adj_close

        rows = [
            {
                "ticker": ticker,
                "trade_date": gap_date,
                "open": fill_close,
                "high": fill_close,
                "low": fill_close,
                "close": fill_close,
                "adj_close": fill_adj_close,
                "volume": 0,
                "source": "extrapolated",
                "is_extrapolated": True,
            }
            for gap_date in sorted(missing_dates)
        ]
        # DO NOTHING: synthetic rows never overwrite anything
        bulk_insert(self.session, PriceDaily, rows, ["ticker", "trade_date"])
        count = len(rows)

        if count:
            self.session.flush()
        return count
