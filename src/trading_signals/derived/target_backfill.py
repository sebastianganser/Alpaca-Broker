"""Target Backfill Computer – fills forward returns retrospectively.

For each feature_snapshots row where return_Xd is NULL, checks if
the required future price data exists and computes:

    return_Xd = (future_price / current_price) - 1

Trading-day calculation uses actual prices_daily entries (no
calendar assumptions about weekends/holidays).

Horizons: 1d, 5d, 20d, 60d (trading days, not calendar days).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import and_, select, update
from sqlalchemy.orm import Session

from trading_signals.db.models.features import FeatureSnapshot
from trading_signals.db.models.prices import PriceDaily
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# Forward-return horizons in trading days
HORIZONS = [
    ("return_1d", 1),
    ("return_5d", 5),
    ("return_20d", 20),
    ("return_60d", 60),
]


class TargetBackfillComputer:
    """Backfill forward returns into feature_snapshots."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def backfill_all(self) -> int:
        """Backfill all missing target variables.

        Returns:
            Total number of individual return values filled.
        """
        total = 0
        for col_name, horizon in HORIZONS:
            filled = self._backfill_horizon(col_name, horizon)
            total += filled
            if filled:
                logger.info(
                    f"[target_backfill] {col_name}: {filled} values filled"
                )

        self.session.flush()
        logger.info(f"[target_backfill] Total: {total} return values filled")
        return total

    def _backfill_horizon(self, col_name: str, horizon: int) -> int:
        """Backfill a single return horizon for all rows missing it."""
        col = getattr(FeatureSnapshot, col_name)

        # Get all (date, ticker) pairs where this return is NULL
        missing = list(self.session.execute(
            select(FeatureSnapshot.snapshot_date, FeatureSnapshot.ticker)
            .where(col.is_(None))
            .order_by(FeatureSnapshot.snapshot_date)
        ).all())

        if not missing:
            return 0

        # Build a map of all trading dates per ticker for efficient lookup
        # Get unique tickers from missing rows
        tickers = list({row[1] for row in missing})

        filled = 0
        # Process in batches by ticker for efficient price lookups
        for ticker in tickers:
            ticker_rows = [(d, t) for d, t in missing if t == ticker]
            if not ticker_rows:
                continue

            # Get all trading dates + close prices for this ticker
            prices = dict(self.session.execute(
                select(PriceDaily.trade_date, PriceDaily.close)
                .where(PriceDaily.ticker == ticker)
                .where(PriceDaily.close.isnot(None))
                .order_by(PriceDaily.trade_date)
            ).all())

            if not prices:
                continue

            # Sorted trading dates for offset calculation
            sorted_dates = sorted(prices.keys())
            date_to_idx = {d: i for i, d in enumerate(sorted_dates)}

            for snap_date, _ in ticker_rows:
                if snap_date not in date_to_idx:
                    # Find closest trading day on or before snap_date
                    base_idx = None
                    for i, td in enumerate(sorted_dates):
                        if td <= snap_date:
                            base_idx = i
                        else:
                            break
                    if base_idx is None:
                        continue
                else:
                    base_idx = date_to_idx[snap_date]

                future_idx = base_idx + horizon
                if future_idx >= len(sorted_dates):
                    continue  # Future price not yet available

                base_price = float(prices[sorted_dates[base_idx]])
                future_price = float(prices[sorted_dates[future_idx]])

                if base_price <= 0:
                    continue

                ret = round((future_price / base_price) - 1, 6)

                self.session.execute(
                    update(FeatureSnapshot)
                    .where(
                        and_(
                            FeatureSnapshot.snapshot_date == snap_date,
                            FeatureSnapshot.ticker == ticker,
                        )
                    )
                    .values({col_name: ret})
                )
                filled += 1

        return filled
