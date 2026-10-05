"""Estimates Collector – daily EPS/Revenue consensus and revisions via yfinance.

Collects analyst consensus estimates, revision counts, and trend data
for all active tickers in the universe. This is the most time-critical
collector in the system because Yahoo's 90-day rolling window means
history is lost daily if not captured.

Strategy:
  1. Determine the target session (last completed NYSE session) and load
     active tickers that are not yet stored for it
  2. Fetch eps_trend, eps_revisions, earnings_estimate, revenue_estimate
     via YFinanceClient (batched, rate-limited)
  3. Store with ON CONFLICT DO NOTHING (one snapshot per ticker+date+period)

``as_of`` = last completed NYSE session at run time (H3). The 01:30-Berlin
run on Tuesday stores Monday's session; Sunday/Monday runs find Friday
already stored and return immediately.

Schedule: Daily 01:30 CET (after analyst_ratings at 01:00,
before feature_pipeline at 02:00)
Sprint: 9.5a (Data Hardening)
"""

from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from trading_signals.collectors._db import release_transaction
from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.yfinance_client import YFinanceClient
from trading_signals.db.models.estimates import EstimatesSnapshot
from trading_signals.utils import market_calendar
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

SOURCE = "yfinance"

#: Skip the run if this share of active tickers is already stored for the
#: target session (otherwise only the missing tickers are fetched).
SKIP_IF_STORED_SHARE = 0.9

_VALUE_COLUMNS = [
    # EPS consensus
    "eps_avg", "eps_low", "eps_high", "eps_n_analysts", "eps_year_ago",
    "eps_growth",
    # EPS trend (rolling window)
    "eps_current", "eps_7d_ago", "eps_30d_ago", "eps_60d_ago", "eps_90d_ago",
    # Revision counts
    "rev_up_7d", "rev_up_30d", "rev_down_7d", "rev_down_30d",
    # Revenue consensus
    "revenue_avg", "revenue_low", "revenue_high", "revenue_n_analysts",
    "revenue_year_ago", "revenue_growth",
    # Raw data for future-proofing
    "raw",
]


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
        """Fetch estimate data for active tickers not yet stored for the session.

        Returns:
            List of dicts with estimate data per ticker per period; each
            record carries its ``as_of`` (session date).
        """
        as_of = market_calendar.last_completed_session()
        stored = {
            r[0]
            for r in session.execute(
                select(distinct(EstimatesSnapshot.ticker)).where(
                    EstimatesSnapshot.as_of == as_of,
                    EstimatesSnapshot.source == SOURCE,
                )
            ).all()
        }
        release_transaction(session)
        all_tickers = self.get_active_tickers(session)

        if all_tickers and len(stored) >= SKIP_IF_STORED_SHARE * len(all_tickers):
            logger.info(
                f"[{self.name}] Session {as_of} already stored for "
                f"{len(stored)}/{len(all_tickers)} tickers — skipping"
            )
            return []
        tickers = [t for t in all_tickers if t not in stored]

        logger.info(
            f"[{self.name}] Fetching estimates for {len(tickers)} active tickers "
            f"(as_of={as_of}, {len(stored)} already stored)"
        )

        records = self.client.fetch_estimates(
            tickers, on_success=self.record_success, on_error=self.record_error
        )
        for r in records:
            r["as_of"] = as_of
        return records

    def store(self, session: Session, data: list[dict]) -> tuple[int, int]:
        """Store estimates with ON CONFLICT DO NOTHING (multi-row insert).

        Each (ticker, as_of, period, source) combination is stored once.
        If we re-run for the same session, duplicates are silently skipped.

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = len(data)
        if not data:
            session.flush()
            return 0, 0

        default_as_of = None
        rows = []
        for record in data:
            as_of = record.get("as_of")
            if as_of is None:
                if default_as_of is None:
                    default_as_of = market_calendar.last_completed_session()
                as_of = default_as_of
            row = {
                "ticker": record["ticker"],
                "as_of": as_of,
                "period": record["period"],
                "source": SOURCE,
            }
            for col in _VALUE_COLUMNS:
                row[col] = record.get(col)
            rows.append(row)

        records_written = self._bulk_insert(
            session,
            EstimatesSnapshot,
            rows,
            conflict_cols=["ticker", "as_of", "period", "source"],
        )
        session.flush()

        logger.info(
            f"[{self.name}] Stored {records_written}/{records_fetched} "
            f"estimate snapshots"
        )
        return records_fetched, records_written
