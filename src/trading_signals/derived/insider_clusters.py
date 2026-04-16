"""Insider Cluster Computer – detects cluster buying patterns.

A "cluster buy" occurs when multiple different insiders purchase stock
in the same company within a short time window. This is one of the
strongest insider trading signals.

Cluster Definition:
  - ≥2 different insiders
  - All buying (transaction_type = 'P') within 21 calendar days
  - Only non-derivative, open-market purchases

Cluster Score:
  score = n_insiders * log(1 + total_buy_value / 10_000)

Run after each Form4Collector run to keep clusters up to date.
"""

import math
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select, func, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.db.models.insider import InsiderCluster, InsiderTrade
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# Rolling window for cluster detection (calendar days)
CLUSTER_WINDOW_DAYS = 21

# Minimum number of distinct insiders for a cluster
MIN_INSIDERS = 2


class InsiderClusterComputer:
    """Compute insider buying clusters from Form 4 transactions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def compute_new(self, since_date: date | None = None) -> int:
        """Compute clusters for all tickers with recent purchases.

        Args:
            since_date: Only look at transactions on or after this date.
                       Defaults to 90 days ago.

        Returns:
            Number of cluster records written.
        """
        if since_date is None:
            since_date = date.today() - timedelta(days=90)

        # Get all tickers with purchase transactions since since_date
        stmt = (
            select(InsiderTrade.ticker)
            .where(
                and_(
                    InsiderTrade.transaction_type == "P",
                    InsiderTrade.is_derivative == False,  # noqa: E712
                    InsiderTrade.transaction_date >= since_date,
                    InsiderTrade.ticker.isnot(None),
                )
            )
            .distinct()
        )
        tickers = [row[0] for row in self.session.execute(stmt).all()]

        total_written = 0
        for ticker in tickers:
            written = self._compute_for_ticker(ticker, since_date)
            total_written += written

        logger.info(
            f"[insider_clusters] Computed {total_written} clusters "
            f"across {len(tickers)} tickers"
        )
        return total_written

    def _compute_for_ticker(self, ticker: str, since_date: date) -> int:
        """Detect clusters for a single ticker.

        Uses a rolling window approach: for each purchase transaction,
        look backward CLUSTER_WINDOW_DAYS to see if there are other
        insiders buying.
        """
        # Get all non-derivative purchases for this ticker
        stmt = (
            select(InsiderTrade)
            .where(
                and_(
                    InsiderTrade.ticker == ticker,
                    InsiderTrade.transaction_type == "P",
                    InsiderTrade.is_derivative == False,  # noqa: E712
                    InsiderTrade.transaction_date >= since_date,
                )
            )
            .order_by(InsiderTrade.transaction_date)
        )
        purchases = list(self.session.execute(stmt).scalars().all())

        if len(purchases) < MIN_INSIDERS:
            return 0

        # Group purchases by insider name to detect distinct insiders
        # Build clusters using a rolling window
        clusters = self._find_clusters(purchases)

        written = 0
        for cluster in clusters:
            if self._store_cluster(ticker, cluster):
                written += 1

        if written > 0:
            self.session.flush()
            logger.info(
                f"[insider_clusters] {ticker}: {written} clusters detected"
            )

        return written

    def _find_clusters(
        self, purchases: list[InsiderTrade]
    ) -> list[dict]:
        """Find clusters in a list of sorted purchase transactions.

        Returns a list of cluster dicts with:
          - start_date, end_date
          - insiders: set of insider names
          - buys: list of purchase transactions
        """
        if not purchases:
            return []

        clusters: list[dict] = []

        # For each purchase, look at all purchases within the window
        n = len(purchases)
        used_in_cluster: set[int] = set()

        for i in range(n):
            if i in used_in_cluster:
                continue

            txn_i = purchases[i]
            if txn_i.transaction_date is None:
                continue

            # Collect all purchases within CLUSTER_WINDOW_DAYS of this one
            window_end = txn_i.transaction_date + timedelta(days=CLUSTER_WINDOW_DAYS)
            window_txns = [txn_i]
            window_indices = {i}

            for j in range(i + 1, n):
                txn_j = purchases[j]
                if txn_j.transaction_date is None:
                    continue
                if txn_j.transaction_date > window_end:
                    break
                window_txns.append(txn_j)
                window_indices.add(j)

            # Check if we have multiple distinct insiders
            insiders = {
                txn.insider_name for txn in window_txns
                if txn.insider_name
            }

            if len(insiders) >= MIN_INSIDERS:
                cluster_start = min(
                    t.transaction_date for t in window_txns
                    if t.transaction_date
                )
                cluster_end = max(
                    t.transaction_date for t in window_txns
                    if t.transaction_date
                )

                # Calculate values
                total_buy_value = sum(
                    float(t.total_value) for t in window_txns
                    if t.total_value
                )

                # Score: n_insiders * log(1 + total_value / 10000)
                score = len(insiders) * math.log(1 + total_buy_value / 10_000)

                clusters.append({
                    "cluster_start": cluster_start,
                    "cluster_end": cluster_end,
                    "n_insiders": len(insiders),
                    "n_buys": len(window_txns),
                    "total_buy_value": total_buy_value,
                    "score": round(score, 4),
                })

                # Mark all as used to avoid overlapping clusters
                used_in_cluster.update(window_indices)

        return clusters

    def _store_cluster(self, ticker: str, cluster: dict) -> bool:
        """Store a single cluster via UPSERT on (ticker, cluster_start).

        If a cluster with the same ticker and start date already exists,
        update it (it might have grown with new transactions).
        """
        values = dict(
            ticker=ticker,
            cluster_start=cluster["cluster_start"],
            cluster_end=cluster["cluster_end"],
            n_insiders=cluster["n_insiders"],
            n_buys=cluster["n_buys"],
            n_sells=0,  # We only track purchase clusters
            total_buy_value=cluster["total_buy_value"],
            total_sell_value=0,
            cluster_score=cluster["score"],
        )
        update_cols = {
            k: v for k, v in values.items()
            if k not in ("ticker", "cluster_start")
        }
        stmt = (
            pg_insert(InsiderCluster)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_insider_cluster_ticker_start",
                set_=update_cols,
            )
        )
        result = self.session.execute(stmt)
        return result.rowcount > 0
