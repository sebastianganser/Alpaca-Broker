"""Fundamentals Collector – weekly financial metrics via yfinance.

Collects P/E, margins, revenue growth, EPS, and other fundamental
data for all active tickers in the universe.

Strategy:
  1. Load active tickers from universe table
  2. Fetch ticker.info via YFinanceClient (batched, rate-limited)
  3. Store with ON CONFLICT DO UPDATE (UPSERT – fundamentals change)

Schedule: Weekly Sunday 01:00 MEZ
"""

from datetime import date

from sqlalchemy.orm import Session

from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.yfinance_client import YFinanceClient
from trading_signals.db.models.fundamentals import FundamentalsSnapshot
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# All columns that can be updated on conflict (excluding PKs and fetched_at)
_UPSERT_COLUMNS = [
    "market_cap", "pe_ratio", "forward_pe", "ps_ratio", "pb_ratio",
    "ev_ebitda", "profit_margin", "operating_margin", "return_on_equity",
    "revenue_ttm", "revenue_growth_yoy", "eps_ttm", "eps_growth_yoy",
    "debt_to_equity", "current_ratio", "dividend_yield", "beta",
    "target_price_low", "target_price_mean", "target_price_median",
    "target_price_high", "most_recent_quarter",
]


class FundamentalsCollectorYF(BaseCollector):
    """Collects fundamental data (P/E, margins, etc.) via yfinance."""

    name = "fundamentals_yf"

    def __init__(
        self,
        batch_size: int = 50,
        delay_between_tickers: float = 0.5,
        delay_between_batches: float = 3.0,
    ) -> None:
        self.client = YFinanceClient(
            batch_size=batch_size,
            delay_between_tickers=delay_between_tickers,
            delay_between_batches=delay_between_batches,
        )

    def fetch(self, session: Session) -> list[dict]:
        """Fetch fundamental data for all active universe tickers.

        Returns:
            List of dicts with fundamental metrics per ticker.
        """
        tickers = self.get_active_tickers(session)

        logger.info(
            f"[{self.name}] Fetching fundamentals for {len(tickers)} active tickers"
        )

        return self.client.fetch_fundamentals(
            tickers, on_success=self.record_success, on_error=self.record_error
        )

    def store(self, session: Session, data: list[dict]) -> tuple[int, int]:
        """Store fundamentals with UPSERT (multi-row ON CONFLICT DO UPDATE).

        Fundamentals change over time (e.g., after earnings reports),
        so we update existing records for the same (ticker, snapshot_date).

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = len(data)
        today = date.today()

        rows = []
        for record in data:
            values = {
                "ticker": record["ticker"],
                "snapshot_date": today,
            }
            # Add all fundamental fields
            for col in _UPSERT_COLUMNS:
                values[col] = record.get(col)
            rows.append(values)

        records_written = self._bulk_insert(
            session,
            FundamentalsSnapshot,
            rows,
            conflict_cols=["ticker", "snapshot_date"],
            update_cols=_UPSERT_COLUMNS,
        )
        session.flush()

        logger.info(
            f"[{self.name}] Stored {records_written}/{records_fetched} "
            f"fundamentals snapshots for {today}"
        )
        return records_fetched, records_written
