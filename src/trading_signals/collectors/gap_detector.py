"""Gap Detection & Repair for time-series data.

Before each collector run, the GapDetector checks for missing
trading days and attempts to repair them:
  1. Fetch missing data from the source (real data)
  2. If source has no data: extrapolate via forward-fill

Extrapolated data is always marked with is_extrapolated=TRUE
so downstream features can filter or weight it appropriately.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd
import pandas_market_calendars as mcal
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.db.models.prices import PriceDaily
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class GapRepairResult:
    """Summary of gap detection and repair for a single run."""

    gaps_detected: int = 0
    gaps_repaired: int = 0        # successfully fetched from source
    gaps_extrapolated: int = 0    # forward-filled
    gaps_unfixable: int = 0       # should be 0
    details: dict[str, list[str]] = field(default_factory=dict)


class GapDetector:
    """Detect and repair data gaps in price history.

    Uses the NYSE trading calendar to determine expected trading days.
    Only checks from a ticker's first data point onward – so a ticker
    that started collecting yesterday won't report "all of history" as gaps.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self._calendar = mcal.get_calendar("NYSE")

    def get_expected_trading_days(
        self, start: date, end: date
    ) -> list[date]:
        """Return list of expected NYSE trading days in the range [start, end]."""
        schedule = self._calendar.schedule(
            start_date=pd.Timestamp(start),
            end_date=pd.Timestamp(end),
        )
        return [ts.date() for ts in schedule.index]

    def detect_gaps(self, ticker: str) -> list[date]:
        """Find missing trading days for a ticker.

        Looks from the ticker's earliest data point to yesterday
        (today's data may not be available yet).
        """
        # Find the ticker's first and last data point
        stmt = select(
            func.min(PriceDaily.trade_date),
            func.max(PriceDaily.trade_date),
        ).where(PriceDaily.ticker == ticker)
        result = self.session.execute(stmt).one()
        first_date, last_date = result

        if first_date is None:
            # No data at all – not a gap, ticker just hasn't been collected yet
            return []

        # Check up to yesterday (today may not be available yet)
        yesterday = date.today() - timedelta(days=1)
        check_end = min(last_date, yesterday) if last_date else yesterday

        if first_date >= check_end:
            return []

        # Get expected trading days from NYSE calendar
        expected_days = set(
            self.get_expected_trading_days(first_date, check_end)
        )

        # Get actual days we have in the DB
        stmt = (
            select(PriceDaily.trade_date)
            .where(PriceDaily.ticker == ticker)
            .where(PriceDaily.trade_date >= first_date)
            .where(PriceDaily.trade_date <= check_end)
        )
        actual_days = set(
            row[0] for row in self.session.execute(stmt).all()
        )

        # Gaps = expected - actual
        missing = sorted(expected_days - actual_days)
        return missing

    def detect_gaps_bulk(
        self, tickers: list[str]
    ) -> dict[str, list[date]]:
        """Detect gaps for multiple tickers efficiently.

        Returns only tickers that have gaps (empty dict = no gaps).
        """
        gaps = {}
        for ticker in tickers:
            ticker_gaps = self.detect_gaps(ticker)
            if ticker_gaps:
                gaps[ticker] = ticker_gaps
        return gaps

    def repair_gaps(
        self,
        gaps: dict[str, list[date]],
        fetch_fn: Callable[[list[str], date, date], pd.DataFrame] | None = None,
    ) -> GapRepairResult:
        """Attempt to repair detected gaps.

        Strategy:
          1. Try to fetch real data via fetch_fn (e.g., yfinance)
          2. If fetch_fn returns no data: extrapolate (forward-fill)

        Args:
            gaps: Dict of {ticker: [missing_dates]}.
            fetch_fn: Optional callable(tickers, start, end) -> DataFrame.
                      If None, skips directly to extrapolation.
        """
        result = GapRepairResult()
        total_gaps = sum(len(dates) for dates in gaps.values())
        result.gaps_detected = total_gaps

        if total_gaps == 0:
            return result

        logger.info(f"Gap repair: {total_gaps} gaps across {len(gaps)} tickers")

        for ticker, missing_dates in gaps.items():
            result.details[ticker] = []

            # Step 1: Try to fetch from source
            fetched_dates = set()
            if fetch_fn and missing_dates:
                try:
                    fetched_dates = self._try_fetch(
                        ticker, missing_dates, fetch_fn
                    )
                    result.gaps_repaired += len(fetched_dates)
                    if fetched_dates:
                        result.details[ticker].append(
                            f"Fetched {len(fetched_dates)} days from source"
                        )
                except Exception as e:
                    logger.warning(
                        f"Gap repair fetch failed for {ticker}: {e}"
                    )

            # Step 2: Extrapolate remaining gaps
            remaining = [d for d in missing_dates if d not in fetched_dates]
            if remaining:
                extrapolated = self._extrapolate(ticker, remaining)
                result.gaps_extrapolated += extrapolated
                if extrapolated:
                    result.details[ticker].append(
                        f"Extrapolated {extrapolated} days"
                    )

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

        fetched = set()
        for trade_date, row in df.iterrows():
            d = trade_date.date() if hasattr(trade_date, "date") else trade_date
            if d not in missing_dates:
                continue

            stmt = (
                pg_insert(PriceDaily)
                .values(
                    ticker=ticker,
                    trade_date=d,
                    open=_safe_float(row.get("Open")),
                    high=_safe_float(row.get("High")),
                    low=_safe_float(row.get("Low")),
                    close=_safe_float(row.get("Close")),
                    adj_close=_safe_float(row.get("Adj Close")),
                    volume=_safe_int(row.get("Volume")),
                    source="yfinance",
                    is_extrapolated=False,
                )
                .on_conflict_do_nothing(
                    index_elements=["ticker", "trade_date"]
                )
            )
            self.session.execute(stmt)
            fetched.add(d)

        if fetched:
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
            logger.warning(
                f"Cannot extrapolate {ticker}: no prior real data"
            )
            return 0

        count = 0
        fill_close = last_real.close
        fill_adj_close = last_real.adj_close

        for gap_date in sorted(missing_dates):
            stmt = (
                pg_insert(PriceDaily)
                .values(
                    ticker=ticker,
                    trade_date=gap_date,
                    open=fill_close,
                    high=fill_close,
                    low=fill_close,
                    close=fill_close,
                    adj_close=fill_adj_close,
                    volume=0,
                    source="extrapolated",
                    is_extrapolated=True,
                )
                .on_conflict_do_nothing(
                    index_elements=["ticker", "trade_date"]
                )
            )
            self.session.execute(stmt)
            count += 1

        if count:
            self.session.flush()
        return count


def _safe_float(value) -> float | None:
    """Convert a value to float, handling NaN and None."""
    if value is None:
        return None
    try:
        f = float(value)
        if pd.isna(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def _safe_int(value) -> int | None:
    """Convert a value to int, handling NaN and None."""
    if value is None:
        return None
    try:
        f = float(value)
        if pd.isna(f):
            return None
        return int(f)
    except (ValueError, TypeError):
        return None
