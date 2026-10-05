"""Earnings Calendar Collector – weekly earnings dates via yfinance.

Collects past and upcoming earnings dates along with EPS estimates,
actual EPS, and surprise percentages for all active universe tickers.

Strategy:
  1. Load active tickers from universe table
  2. Fetch ticker.get_earnings_dates() via YFinanceClient
  3. Store with ON CONFLICT DO UPDATE (UPSERT – eps_actual gets filled post-earnings)

Upsert semantics (M4): ``col = COALESCE(excluded.col, existing.col)`` – a
later fetch never wipes a known value with NULL. ``fetched_at`` and
``last_seen`` are bumped on every run, ``first_seen`` is set on insert only.

Schedule: Weekly Sunday 02:00 MEZ
"""

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.yfinance_client import YFinanceClient
from trading_signals.db.models.fundamentals import EarningsCalendar
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# Columns that can be updated on upsert (excluding PKs)
_UPSERT_COLUMNS = [
    "time_of_day", "eps_estimate", "eps_actual",
    "revenue_estimate", "revenue_actual", "surprise_pct",
]

#: Rows per multi-row INSERT statement.
_CHUNK = 1000


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
        tickers = self.get_active_tickers(session)

        logger.info(
            f"[{self.name}] Fetching earnings dates for {len(tickers)} "
            f"active tickers (limit={self.earnings_limit} per ticker)"
        )

        return self.client.fetch_earnings_dates(
            tickers,
            limit=self.earnings_limit,
            on_success=self.record_success,
            on_error=self.record_error,
        )

    def store(self, session: Session, data: list[dict]) -> tuple[int, int]:
        """Store earnings calendar with a COALESCE UPSERT (multi-row).

        Uses ON CONFLICT DO UPDATE because eps_actual and surprise_pct
        are only available after the earnings call happens.

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = len(data)
        now = datetime.now()

        # De-duplicate on the PK (last wins) – PostgreSQL rejects a
        # DO UPDATE statement that touches the same row twice.
        dedup: dict[tuple, dict] = {}
        for record in data:
            values = {
                "ticker": record["ticker"],
                "earnings_date": record["earnings_date"],
                **{col: record.get(col) for col in _UPSERT_COLUMNS},
                "fetched_at": now,
                "first_seen": now,
                "last_seen": now,
            }
            dedup[(values["ticker"], values["earnings_date"])] = values
        rows = list(dedup.values())

        records_written = 0
        table = EarningsCalendar.__table__
        for i in range(0, len(rows), _CHUNK):
            stmt = pg_insert(EarningsCalendar).values(rows[i : i + _CHUNK])
            set_ = {
                col: func.coalesce(stmt.excluded[col], table.c[col])
                for col in _UPSERT_COLUMNS
            }
            set_["fetched_at"] = stmt.excluded.fetched_at
            set_["last_seen"] = stmt.excluded.last_seen
            # first_seen intentionally NOT updated (insert-only)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ticker", "earnings_date"], set_=set_
            )
            result = session.execute(stmt)
            rc = getattr(result, "rowcount", 0)
            if isinstance(rc, int) and rc > 0:
                records_written += rc

        session.flush()

        logger.info(
            f"[{self.name}] Stored {records_written}/{records_fetched} "
            f"earnings calendar entries"
        )
        return records_fetched, records_written
