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

Point-in-time (review finding C2, 2026-10):
  Clusters are built on ``transaction_date`` but a Form 4 only becomes
  public on its ``filing_date`` (up to 2 business days later, late filers
  much later). Every stored cluster therefore carries ``known_date`` =
  max(filing_date) of its member trades – the first day on which the
  complete cluster was observable. The feature pipeline does NOT use the
  stored clusters; it re-runs :func:`find_clusters` as of each snapshot
  date on the trades with ``filing_date <= d`` (see
  ``FeaturePipeline._insider_features``).

Run after each Form4Collector run to keep clusters up to date.
"""

import math
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.db.models.insider import InsiderCluster, InsiderTrade
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retention import data_start_date

logger = get_logger(__name__)

# Rolling window for cluster detection (calendar days)
CLUSTER_WINDOW_DAYS = 21

# Minimum number of distinct insiders for a cluster
MIN_INSIDERS = 2

# Fallback publication lag if a trade has no filing_date (Form 4 is due
# within 2 business days ≈ 4 calendar days incl. a weekend).
FALLBACK_FILING_LAG_DAYS = 4

_NY_TZ = ZoneInfo("America/New_York")
_CLOSE_ET = time(16, 0)


class _PurchaseLike(Protocol):
    insider_name: Any
    transaction_date: Any
    total_value: Any


def availability_date(txn: Any) -> date | None:
    """Day a trade became public (usable at that day's close).

    Order: EDGAR ``acceptance_datetime`` (accepted at/after 16:00 ET →
    next day), else ``filing_date``, else ``transaction_date`` + lag.
    """
    accepted = getattr(txn, "acceptance_datetime", None)
    if isinstance(accepted, datetime):
        if accepted.tzinfo is not None:
            accepted = accepted.astimezone(_NY_TZ)
        day = accepted.date()
        return day + timedelta(days=1) if accepted.time() >= _CLOSE_ET else day
    filing = getattr(txn, "filing_date", None)
    if isinstance(filing, date):
        return filing
    if txn.transaction_date is None:
        return None
    return txn.transaction_date + timedelta(days=FALLBACK_FILING_LAG_DAYS)


def find_clusters(purchases: list[_PurchaseLike]) -> list[dict]:
    """Find clusters in a list of purchase transactions (pure function).

    The input is sorted by transaction_date internally. Greedy, non-overlapping:
    each trade belongs to at most one cluster.

    Returns a list of cluster dicts with keys cluster_start, cluster_end,
    known_date, n_insiders, n_buys, total_buy_value, score.
    """
    purchases = sorted(
        (p for p in purchases if p.transaction_date is not None),
        key=lambda p: p.transaction_date,
    )
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

        # Collect all purchases within CLUSTER_WINDOW_DAYS of this one
        window_end = txn_i.transaction_date + timedelta(days=CLUSTER_WINDOW_DAYS)
        window_txns = [txn_i]
        window_indices = {i}

        for j in range(i + 1, n):
            txn_j = purchases[j]
            if txn_j.transaction_date > window_end:
                break
            if j in used_in_cluster:
                continue
            window_txns.append(txn_j)
            window_indices.add(j)

        # Check if we have multiple distinct insiders
        insiders = {txn.insider_name for txn in window_txns if txn.insider_name}

        if len(insiders) >= MIN_INSIDERS:
            cluster_start = min(t.transaction_date for t in window_txns)
            cluster_end = max(t.transaction_date for t in window_txns)
            avail = [a for a in (availability_date(t) for t in window_txns) if a]
            known_date = max(avail) if avail else None

            # Calculate values
            total_buy_value = sum(
                float(t.total_value) for t in window_txns if t.total_value
            )

            # Score: n_insiders * log(1 + total_value / 10000)
            score = len(insiders) * math.log(1 + total_buy_value / 10_000)

            clusters.append(
                {
                    "cluster_start": cluster_start,
                    "cluster_end": cluster_end,
                    "known_date": known_date,
                    "n_insiders": len(insiders),
                    "n_buys": len(window_txns),
                    "total_buy_value": total_buy_value,
                    "score": round(score, 4),
                }
            )

            # Mark all as used to avoid overlapping clusters
            used_in_cluster.update(window_indices)

    return clusters


def _has_known_date_column() -> bool:
    """``insider_clusters.known_date`` is added by migration 029."""
    return "known_date" in InsiderCluster.__table__.columns


class InsiderClusterComputer:
    """Compute insider buying clusters from Form 4 transactions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def compute_new(self, since_date: date | None = None) -> int:
        """Recompute clusters for all tickers with purchases since ``since_date``.

        Per ticker the clusters overlapping the window are deleted and
        rebuilt (review finding M5: a rolling ``since_date`` used to create
        overlapping clusters next to the stored ones).

        Args:
            since_date: Only look at transactions on or after this date.
                       Defaults to 365 days ago to capture a full year
                       of cluster history (13F quarterly context).

        Returns:
            Number of cluster records written.
        """
        if since_date is None:
            since_date = date.today() - timedelta(days=365)
        since_date = max(since_date, data_start_date())

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

    def rebuild_all(self) -> int:
        """Delete all clusters and rebuild them from data_start_date()."""
        self.session.execute(delete(InsiderCluster))
        return self.compute_new(since_date=data_start_date())

    def _rebuild_anchor(self, ticker: str, since_date: date) -> date:
        """Earliest start of a stored cluster still overlapping ``since_date``."""
        earliest = self.session.execute(
            select(func.min(InsiderCluster.cluster_start))
            .where(InsiderCluster.ticker == ticker)
            .where(InsiderCluster.cluster_end >= since_date)
        ).scalar()
        if isinstance(earliest, date) and earliest < since_date:
            return earliest
        return since_date

    def _compute_for_ticker(self, ticker: str, since_date: date) -> int:
        """Delete-and-rebuild clusters for a single ticker from an anchor date."""
        anchor = self._rebuild_anchor(ticker, since_date)

        # Get all non-derivative purchases for this ticker
        stmt = (
            select(InsiderTrade)
            .where(
                and_(
                    InsiderTrade.ticker == ticker,
                    InsiderTrade.transaction_type == "P",
                    InsiderTrade.is_derivative == False,  # noqa: E712
                    InsiderTrade.transaction_date >= anchor,
                )
            )
            .order_by(InsiderTrade.transaction_date)
        )
        purchases = list(self.session.execute(stmt).scalars().all())

        # Remove stale clusters in the rebuild window (no overlaps).
        self.session.execute(
            delete(InsiderCluster)
            .where(InsiderCluster.ticker == ticker)
            .where(InsiderCluster.cluster_start >= anchor)
        )

        if len(purchases) < MIN_INSIDERS:
            return 0

        clusters = self._find_clusters(purchases)

        written = 0
        for cluster in clusters:
            if self._store_cluster(ticker, cluster):
                written += 1

        if written > 0:
            self.session.flush()
            logger.info(f"[insider_clusters] {ticker}: {written} clusters detected")

        return written

    def _find_clusters(self, purchases: list[InsiderTrade]) -> list[dict]:
        """Backward-compatible wrapper around :func:`find_clusters`."""
        return find_clusters(purchases)

    def _store_cluster(self, ticker: str, cluster: dict) -> bool:
        """Store a single cluster via UPSERT on (ticker, cluster_start)."""
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
        if _has_known_date_column():
            values["known_date"] = cluster.get("known_date")
        update_cols = {
            k: v for k, v in values.items() if k not in ("ticker", "cluster_start")
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
