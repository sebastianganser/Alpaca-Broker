"""Earnings Calendar Collector – weekly earnings dates via yfinance.

Collects past and upcoming earnings dates along with EPS estimates,
actual EPS, and surprise percentages for all active universe tickers.

Strategy:
  1. Load active tickers from universe table
  2. Fetch ticker.get_earnings_dates() via YFinanceClient
  3. Store with ON CONFLICT DO UPDATE (UPSERT – eps_actual gets filled post-earnings)

Schedule: Weekly Sunday 02:00 MEZ
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.yfinance_client import YFinanceClient
from trading_signals.db.models.fundamentals import EarningsCalendar
from trading_signals.db.models.universe import Universe
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# Columns that can be updated on upsert (excluding PKs)
_UPSERT_COLUMNS = [
    "time_of_day", "eps_estimate", "eps_actual",
    "revenue_estimate", "revenue_actual", "surprise_pct",
]


class EarningsCalendarCollector(BaseCollector):
    """Collects past and future earnings dates with EPS surprise data."""

    name = "earnings_calendar_collector"

    def __init__(
        self,
        earnings_limit: int = 4,
        batch_size: int = 50,
        delay_between_tickers: float = 0.5,
        delay_between_batches: float = 3.0,
    ) -> None:
        """Initialize with earnings limit and rate-limiting params.

        Args:
            earnings_limit: Max earnings dates to fetch per ticker.
            batch_size: Number of tickers per batch.
            delay_between_tickers: Seconds between individual ticker calls.
            delay_between_batches: Seconds between batches.
        """
        self.earnings_limit = earnings_limit
        self.client = YFinanceClient(
            batch_size=batch_size,
            delay_between_tickers=delay_between_tickers,
            delay_between_batches=delay_between_batches,
        )

    def fetch(self, session: Session) -> list[dict]:
        """Fetch earnings calendar data for all active universe tickers.

        Returns:
            List of dicts with earnings date records.
        """
        # Load active tickers
        stmt = select(Universe.ticker).where(Universe.is_active.is_(True))
        tickers = [row[0] for row in session.execute(stmt).all()]

        logger.info(
            f"[{self.name}] Fetching earnings dates for {len(tickers)} "
            f"active tickers (limit={self.earnings_limit} per ticker)"
        )

        return self.client.fetch_earnings_dates(
            tickers, limit=self.earnings_limit
        )

    def store(self, session: Session, data: list[dict]) -> tuple[int, int]:
        """Store earnings calendar with UPSERT.

        Uses ON CONFLICT DO UPDATE because eps_actual and surprise_pct
        are only available after the earnings call happens.

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = len(data)
        records_written = 0

        for record in data:
            values = {
                "ticker": record["ticker"],
                "earnings_date": record["earnings_date"],
                "time_of_day": record.get("time_of_day"),
                "eps_estimate": record.get("eps_estimate"),
                "eps_actual": record.get("eps_actual"),
                "revenue_estimate": record.get("revenue_estimate"),
                "revenue_actual": record.get("revenue_actual"),
                "surprise_pct": record.get("surprise_pct"),
            }

            stmt = (
                pg_insert(EarningsCalendar)
                .values(**values)
                .on_conflict_do_update(
                    constraint="pk_earnings_calendar",
                    set_={col: values[col] for col in _UPSERT_COLUMNS},
                )
            )
            result = session.execute(stmt)
            if result.rowcount > 0:
                records_written += 1

        session.flush()

        logger.info(
            f"[{self.name}] Stored {records_written}/{records_fetched} "
            f"earnings calendar entries"
        )
        return records_fetched, records_written
