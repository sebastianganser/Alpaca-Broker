"""Target Backfill Computer – fills forward returns retrospectively.

Target definition (FEATURE_VERSION >= 2026.10-1, review finding C4)
-------------------------------------------------------------------
Features for snapshot date ``d`` use information up to the close of ``d``
(and the pipeline itself runs at 02:00 CET, i.e. after the close), so a
signal is only tradable at the **open of the next session**. Hence::

    entry  = OPEN  of the 1st NYSE session after d        (d+1)
    exit   = CLOSE of the h-th NYSE session after d       (d+h)
    return_h = close(d+h) / open(d+1) - 1,   h ∈ {1, 5, 20, 60}

(``return_1d`` is therefore the intraday open→close return of d+1.)

Rules:
  * Sessions are counted on the NYSE calendar (``utils.market_calendar``),
    not on the rows present in prices_daily – a missing price row can no
    longer shift the horizon.
  * Snapshot dates that are not NYSE sessions get no target (H3).
  * Entry/exit prices from rows flagged ``is_extrapolated`` (gap filler)
    are not used – the target stays NULL (H7).
  * Prices must be > 0.

Performance (P2): per ticker chunk one query for snapshot keys and one for
prices, vectorised pandas computation, and a single executemany bulk
UPDATE by primary key.

Horizons: 1d, 5d, 20d, 60d (trading days, not calendar days).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from trading_signals.db.models.features import FeatureSnapshot
from trading_signals.db.models.prices import PriceDaily
from trading_signals.utils.logging import get_logger
from trading_signals.utils.market_calendar import trading_days

logger = get_logger(__name__)

# Forward-return horizons in trading days
HORIZONS = [
    ("return_1d", 1),
    ("return_5d", 5),
    ("return_20d", 20),
    ("return_60d", 60),
]

TARGET_COLS = [name for name, _ in HORIZONS]
MAX_HORIZON = max(h for _, h in HORIZONS)

# Tickers per processing chunk (bounds memory and statement size)
TICKER_CHUNK = 50
# Rows per executemany batch
UPDATE_BATCH = 5000


def compute_forward_returns(
    snapshots: pd.DataFrame,
    prices: pd.DataFrame,
    sessions: list[date],
    horizons: list[tuple[str, int]] = HORIZONS,
) -> pd.DataFrame:
    """Compute next-open → close(d+h) returns (pure function).

    Args:
        snapshots: columns ``ticker``, ``snapshot_date`` (python dates).
        prices: columns ``ticker``, ``trade_date``, ``open``, ``close``,
            ``is_extrapolated``.
        sessions: sorted NYSE session dates covering the snapshot dates
            and at least ``max(h)`` sessions after them where available.
        horizons: ``[(column, h), ...]``.

    Returns:
        ``snapshots`` with one float/NaN column per horizon.
    """
    out = snapshots[["ticker", "snapshot_date"]].copy().reset_index(drop=True)
    for col, _ in horizons:
        out[col] = float("nan")
    if out.empty or prices.empty or not sessions:
        return out

    sess_idx = {d: i for i, d in enumerate(sessions)}
    n_sess = len(sessions)

    px = prices.copy()
    px["is_extrapolated"] = px["is_extrapolated"].fillna(False).astype(bool)
    px = px[~px["is_extrapolated"]]
    px["open"] = pd.to_numeric(px["open"], errors="coerce")
    px["close"] = pd.to_numeric(px["close"], errors="coerce")
    opens = {
        (t, d): o
        for t, d, o in zip(px["ticker"], px["trade_date"], px["open"])
        if pd.notna(o) and o > 0
    }
    closes = {
        (t, d): c
        for t, d, c in zip(px["ticker"], px["trade_date"], px["close"])
        if pd.notna(c) and c > 0
    }

    base = out["snapshot_date"].map(sess_idx)  # NaN for non-sessions (H3)

    def _session_at(offset: int) -> pd.Series:
        def _f(i):
            if pd.isna(i):
                return None
            j = int(i) + offset
            return sessions[j] if j < n_sess else None

        return base.map(_f)

    entry_dates = _session_at(1)
    entry_px = [
        opens.get((t, d)) if d is not None else None
        for t, d in zip(out["ticker"], entry_dates)
    ]
    for col, h in horizons:
        exit_dates = _session_at(h)
        vals = []
        for t, ed, ep in zip(out["ticker"], exit_dates, entry_px):
            if ed is None or ep is None:
                vals.append(float("nan"))
                continue
            cp = closes.get((t, ed))
            vals.append(
                round(float(cp) / float(ep) - 1, 6) if cp is not None else float("nan")
            )
        out[col] = vals
    return out


class TargetBackfillComputer:
    """Backfill forward returns into feature_snapshots."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ── Public API ───────────────────────────────────────────────────

    def backfill_all(self) -> int:
        """Fill targets for all rows where at least one target is NULL.

        Returns:
            Total number of individual (non-NULL) return values written.
        """
        null_any = or_(*[getattr(FeatureSnapshot, c).is_(None) for c in TARGET_COLS])
        tickers = [
            r[0]
            for r in self.session.execute(
                select(FeatureSnapshot.ticker).where(null_any).distinct()
            ).all()
        ]
        total = self._process(tickers, only_missing=True)
        logger.info(f"[target_backfill] Total: {total} return values filled")
        return total

    def recompute_tickers(self, tickers: Iterable[str]) -> int:
        """Recompute ALL targets of the given tickers (overwrites, incl. NULL).

        Used after a price-history refresh (split/dividend re-adjustment).
        """
        total = self._process(sorted(set(tickers)), only_missing=False)
        logger.info(f"[target_backfill] Recomputed {total} return values")
        return total

    def recompute_all(self) -> int:
        """Recompute ALL targets of ALL rows (after a definition change)."""
        tickers = [
            r[0]
            for r in self.session.execute(
                select(FeatureSnapshot.ticker).distinct()
            ).all()
        ]
        return self.recompute_tickers(tickers)

    # ── Internals ────────────────────────────────────────────────────

    def _process(self, tickers: list[str], only_missing: bool) -> int:
        total = 0
        for i in range(0, len(tickers), TICKER_CHUNK):
            total += self._process_chunk(tickers[i : i + TICKER_CHUNK], only_missing)
        self.session.flush()
        return total

    def _process_chunk(self, tickers: list[str], only_missing: bool) -> int:
        if not tickers:
            return 0
        stmt = select(FeatureSnapshot.ticker, FeatureSnapshot.snapshot_date).where(
            FeatureSnapshot.ticker.in_(tickers)
        )
        if only_missing:
            stmt = stmt.where(
                or_(*[getattr(FeatureSnapshot, c).is_(None) for c in TARGET_COLS])
            )
        snaps = pd.DataFrame(
            self.session.execute(stmt).all(), columns=["ticker", "snapshot_date"]
        )
        if snaps.empty:
            return 0

        start = snaps["snapshot_date"].min()
        price_rows = self.session.execute(
            select(
                PriceDaily.ticker,
                PriceDaily.trade_date,
                PriceDaily.open,
                PriceDaily.close,
                PriceDaily.is_extrapolated,
            )
            .where(PriceDaily.ticker.in_(tickers))
            .where(PriceDaily.trade_date > start)
        ).all()
        prices = pd.DataFrame(
            price_rows,
            columns=["ticker", "trade_date", "open", "close", "is_extrapolated"],
        )
        end = snaps["snapshot_date"].max() + timedelta(days=MAX_HORIZON * 2 + 10)
        sessions = trading_days(start, end)

        result = compute_forward_returns(snaps, prices, sessions)
        return self._bulk_update(result)

    def _bulk_update(self, result: pd.DataFrame) -> int:
        """Write all target columns (NaN → NULL) by primary key."""
        params = []
        filled = 0
        for row in result.itertuples(index=False):
            p = {"snapshot_date": row.snapshot_date, "ticker": row.ticker}
            for col in TARGET_COLS:
                v = getattr(row, col)
                if pd.isna(v):
                    p[col] = None
                else:
                    p[col] = float(v)
                    filled += 1
            params.append(p)
        for i in range(0, len(params), UPDATE_BATCH):
            self.session.execute(update(FeatureSnapshot), params[i : i + UPDATE_BATCH])
        return filled


__all__ = [
    "HORIZONS",
    "TargetBackfillComputer",
    "compute_forward_returns",
]
