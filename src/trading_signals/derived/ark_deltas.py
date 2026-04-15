"""ARK Delta Computer – calculates daily changes in ARK ETF positions.

Compares holdings snapshots between consecutive trading days to detect:
  - new_position: Ticker appeared (not in previous day)
  - closed: Ticker disappeared (was in previous day)
  - increased: Shares went up
  - decreased: Shares went down
  - unchanged: Shares stayed the same

Run after each ARKHoldingsCollector run to keep deltas up to date.
"""

from datetime import date

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.db.models.ark import ARKDelta, ARKHolding
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)


class ARKDeltaComputer:
    """Compute daily changes in ARK ETF holdings."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def compute_for_date(
        self, target_date: date, etf_ticker: str
    ) -> int:
        """Compute deltas between target_date and the previous snapshot date.

        Args:
            target_date: The date to compute deltas for.
            etf_ticker: The ARK ETF ticker (e.g., "ARKK").

        Returns:
            Number of delta records written.
        """
        # Find the previous snapshot date for this ETF
        prev_date = self._get_previous_snapshot_date(target_date, etf_ticker)
        if prev_date is None:
            logger.info(
                f"[ark_deltas] {etf_ticker} {target_date}: no previous snapshot, "
                f"skipping delta computation"
            )
            return 0

        # Get holdings for both dates
        current = self._get_holdings(target_date, etf_ticker)
        previous = self._get_holdings(prev_date, etf_ticker)

        if not current:
            logger.warning(
                f"[ark_deltas] {etf_ticker} {target_date}: no current holdings"
            )
            return 0

        # Build lookup by ticker
        curr_map = {h.ticker: h for h in current}
        prev_map = {h.ticker: h for h in previous}

        all_tickers = set(curr_map.keys()) | set(prev_map.keys())
        written = 0

        for ticker in all_tickers:
            curr = curr_map.get(ticker)
            prev = prev_map.get(ticker)

            delta_type, shares_delta, weight_delta = self._classify(curr, prev)

            # Skip unchanged positions – only track actual movements
            if delta_type == "unchanged":
                continue

            stmt = (
                pg_insert(ARKDelta)
                .values(
                    delta_date=target_date,
                    etf_ticker=etf_ticker,
                    ticker=ticker,
                    delta_type=delta_type,
                    shares_prev=float(prev.shares) if prev and prev.shares else None,
                    shares_curr=float(curr.shares) if curr and curr.shares else None,
                    shares_delta=shares_delta,
                    weight_prev=float(prev.weight_pct) if prev and prev.weight_pct else None,
                    weight_curr=float(curr.weight_pct) if curr and curr.weight_pct else None,
                    weight_delta=weight_delta,
                )
                .on_conflict_do_nothing(
                    index_elements=["delta_date", "etf_ticker", "ticker"]
                )
            )
            result = self.session.execute(stmt)
            if result.rowcount > 0:
                written += 1

        self.session.flush()
        logger.info(
            f"[ark_deltas] {etf_ticker} {target_date}: {written} deltas "
            f"(vs {prev_date})"
        )
        return written

    def compute_all(self) -> int:
        """Compute deltas for all ETFs and all unprocessed dates.

        Finds snapshot dates that don't have corresponding deltas yet
        and computes them. Safe to run multiple times (idempotent).

        Returns:
            Total number of delta records written.
        """
        # Get all distinct (snapshot_date, etf_ticker) combos with data
        stmt = (
            select(ARKHolding.snapshot_date, ARKHolding.etf_ticker)
            .distinct()
            .order_by(ARKHolding.snapshot_date, ARKHolding.etf_ticker)
        )
        snapshots = self.session.execute(stmt).all()

        total_written = 0
        for snapshot_date, etf_ticker in snapshots:
            written = self.compute_for_date(snapshot_date, etf_ticker)
            total_written += written

        logger.info(f"[ark_deltas] Total: {total_written} deltas computed")
        return total_written

    def _get_previous_snapshot_date(
        self, target_date: date, etf_ticker: str
    ) -> date | None:
        """Find the most recent snapshot date before target_date."""
        stmt = (
            select(func.max(ARKHolding.snapshot_date))
            .where(ARKHolding.etf_ticker == etf_ticker)
            .where(ARKHolding.snapshot_date < target_date)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def _get_holdings(
        self, snapshot_date: date, etf_ticker: str
    ) -> list[ARKHolding]:
        """Get all holdings for a specific date and ETF."""
        stmt = (
            select(ARKHolding)
            .where(ARKHolding.snapshot_date == snapshot_date)
            .where(ARKHolding.etf_ticker == etf_ticker)
        )
        return list(self.session.execute(stmt).scalars().all())

    @staticmethod
    def _classify(
        curr: ARKHolding | None, prev: ARKHolding | None
    ) -> tuple[str, float | None, float | None]:
        """Classify the type of change between two snapshots.

        Returns:
            Tuple of (delta_type, shares_delta, weight_delta).
        """
        if curr and not prev:
            return ("new_position", None, None)

        if prev and not curr:
            return ("closed", None, None)

        # Both exist – compare shares
        curr_shares = float(curr.shares) if curr.shares else 0
        prev_shares = float(prev.shares) if prev.shares else 0
        shares_delta = curr_shares - prev_shares

        curr_weight = float(curr.weight_pct) if curr.weight_pct else 0
        prev_weight = float(prev.weight_pct) if prev.weight_pct else 0
        weight_delta = curr_weight - prev_weight

        if shares_delta > 0:
            delta_type = "increased"
        elif shares_delta < 0:
            delta_type = "decreased"
        else:
            delta_type = "unchanged"

        return (delta_type, shares_delta, weight_delta)
