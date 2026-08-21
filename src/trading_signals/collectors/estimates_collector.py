"""Estimates Collector – daily EPS/Revenue consensus and revisions via yfinance.

Collects analyst consensus estimates, revision counts, and trend data
for all active tickers in the universe. This is the most time-critical
collector in the system because Yahoo's 90-day rolling window means
history is lost daily if not captured.

Strategy:
  1. Load active tickers from universe table
  2. Fetch eps_trend, eps_revisions, earnings_estimate, revenue_estimate
     via YFinanceClient (batched, rate-limited)
  3. Store with ON CONFLICT DO NOTHING (one snapshot per ticker+date+period)

Schedule: Daily 01:30 CET (after analyst_ratings at 01:00, before feature_pipeline at 02:00)
Sprint: 9.5a (Data Hardening)
"""

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.yfinance_client import YFinanceClient, _clean_numeric
from trading_signals.db.models.estimates import EstimatesSnapshot
from trading_signals.db.models.universe import Universe
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# Periods we collect for each ticker
PERIODS = ["0q", "+1q", "0y", "+1y"]

# Map yfinance earnings_estimate index names to our period codes
_ESTIMATE_PERIOD_MAP = {
    "0q": "0q",
    "+1q": "+1q",
    "0y": "0y",
    "+1y": "+1y",
}


class EstimatesCollector(BaseCollector):
    """Collects analyst consensus estimates and revisions via yfinance.

    This is the most time-critical collector: Yahoo provides a rolling
    90-day window for EPS revisions. Every day of delay permanently
    loses one day of revision history.
    """

    name = "estimates_collector"

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
        """Fetch estimate data for all active universe tickers.

        Returns:
            List of dicts with estimate data per ticker per period.
        """
        stmt = select(Universe.ticker).where(Universe.is_active.is_(True))
        tickers = [row[0] for row in session.execute(stmt).all()]

        logger.info(
            f"[{self.name}] Fetching estimates for {len(tickers)} active tickers"
        )

        return self.client.fetch_estimates(tickers)

    def store(self, session: Session, data: list[dict]) -> tuple[int, int]:
        """Store estimates with ON CONFLICT DO NOTHING.

        Each (ticker, as_of, period, source) combination is stored once.
        If we re-run on the same day, duplicates are silently skipped.

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = len(data)
        records_written = 0
        today = date.today()

        for record in data:
            values = {
                "ticker": record["ticker"],
                "as_of": today,
                "period": record["period"],
                "source": "yfinance",
                # EPS consensus
                "eps_avg": record.get("eps_avg"),
                "eps_low": record.get("eps_low"),
                "eps_high": record.get("eps_high"),
                "eps_n_analysts": record.get("eps_n_analysts"),
                "eps_year_ago": record.get("eps_year_ago"),
                "eps_growth": record.get("eps_growth"),
                # EPS trend (rolling window)
                "eps_current": record.get("eps_current"),
                "eps_7d_ago": record.get("eps_7d_ago"),
                "eps_30d_ago": record.get("eps_30d_ago"),
                "eps_60d_ago": record.get("eps_60d_ago"),
                "eps_90d_ago": record.get("eps_90d_ago"),
                # Revision counts
                "rev_up_7d": record.get("rev_up_7d"),
                "rev_up_30d": record.get("rev_up_30d"),
                "rev_down_7d": record.get("rev_down_7d"),
                "rev_down_30d": record.get("rev_down_30d"),
                # Revenue consensus
                "revenue_avg": record.get("revenue_avg"),
                "revenue_low": record.get("revenue_low"),
                "revenue_high": record.get("revenue_high"),
                "revenue_n_analysts": record.get("revenue_n_analysts"),
                "revenue_year_ago": record.get("revenue_year_ago"),
                "revenue_growth": record.get("revenue_growth"),
                # Raw data for future-proofing
                "raw": record.get("raw"),
            }

            stmt = (
                pg_insert(EstimatesSnapshot)
                .values(**values)
                .on_conflict_do_nothing(
                    constraint="uq_estimates_snapshot_dedup",
                )
            )
            result = session.execute(stmt)
            if result.rowcount > 0:
                records_written += 1

        session.flush()

        logger.info(
            f"[{self.name}] Stored {records_written}/{records_fetched} "
            f"estimate snapshots for {today}"
        )
        return records_fetched, records_written
