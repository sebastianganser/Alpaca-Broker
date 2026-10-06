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

Barrier label (migration 033, ``analysis.barrier.MAIN_BARRIER``): the actual
short-term trade – entry open(d+1), take profit +1 %, stop −2 %, time stop
14 sessions – stored as ``return_barrier_14d`` / ``barrier_outcome`` /
``barrier_ambiguous`` with the same session and price rules.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from trading_signals.analysis.barrier import (
    MAIN_BARRIER,
    BarrierParams,
    simulate_windows,
)
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
#: Barrier label columns (written together with the return targets).
BARRIER_COLS = ["return_barrier_14d", "barrier_outcome", "barrier_ambiguous"]
#: A row is (re)processed while any of these is NULL.
NULL_CHECK_COLS = [*TARGET_COLS, "return_barrier_14d"]

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


def compute_barrier_labels(
    snapshots: pd.DataFrame,
    prices: pd.DataFrame,
    sessions: list[date],
    params: BarrierParams = MAIN_BARRIER,
) -> pd.DataFrame:
    """Barrier trade label per snapshot (pure function).

    Same rules as :func:`compute_forward_returns`: the window is the
    ``params.max_days`` NYSE sessions after ``d`` (entry = open of d+1);
    non-session snapshot dates, extrapolated or missing price rows and
    non-positive prices inside the window leave the label NULL.

    Args:
        snapshots: columns ``ticker``, ``snapshot_date`` (python dates).
        prices: columns ``ticker``, ``trade_date``, ``open``, ``high``,
            ``low``, ``close``, ``is_extrapolated``.
        sessions: sorted NYSE session dates (see compute_forward_returns).
        params: barrier definition (default :data:`MAIN_BARRIER`).

    Returns:
        ``snapshots`` keys plus ``return_barrier_14d`` (float/NaN),
        ``barrier_outcome`` (int/None) and ``barrier_ambiguous`` (bool/None).
    """
    out = snapshots[["ticker", "snapshot_date"]].copy().reset_index(drop=True)
    out["return_barrier_14d"] = float("nan")
    out["barrier_outcome"] = pd.Series([None] * len(out), dtype=object)
    out["barrier_ambiguous"] = pd.Series([None] * len(out), dtype=object)
    if out.empty or prices.empty or not sessions:
        return out

    ohlc = ["open", "high", "low", "close"]
    sess_idx = {d: i for i, d in enumerate(sessions)}
    n_sess = len(sessions)
    m = params.max_days

    px = prices.copy()
    px["is_extrapolated"] = px["is_extrapolated"].fillna(False).astype(bool)
    px = px[~px["is_extrapolated"]]
    for c in ohlc:
        px[c] = pd.to_numeric(px[c], errors="coerce")
    px["sess"] = px["trade_date"].map(sess_idx)
    px = px[px["sess"].notna()]
    px_by_ticker = dict(tuple(px.groupby("ticker", sort=False)))

    base_all = out["snapshot_date"].map(sess_idx)
    offsets = np.arange(1, m + 1)
    for ticker, g in out.groupby("ticker", sort=False):
        p = px_by_ticker.get(ticker)
        if p is None or p.empty:
            continue
        grid = np.full((n_sess, 4), np.nan)
        grid[p["sess"].astype(int).to_numpy()] = p[ohlc].to_numpy(dtype=float)

        base = base_all.loc[g.index]
        ok = base.notna() & (base + m < n_sess)
        if not ok.any():
            continue
        rows = g.index[ok.to_numpy()]
        win = grid[base[ok].astype(int).to_numpy()[:, None] + offsets[None, :]]
        complete = ~np.isnan(win).any(axis=(1, 2)) & (win > 0).all(axis=(1, 2))
        if not complete.any():
            continue
        win = win[complete]
        outcome, net, _, amb = simulate_windows(
            win[:, :, 0], win[:, :, 1], win[:, :, 2], win[:, :, 3], params
        )
        idx = rows[complete]
        out.loc[idx, "return_barrier_14d"] = np.round(net, 6)
        out.loc[idx, "barrier_outcome"] = pd.Series(
            [int(x) for x in outcome], index=idx, dtype=object
        )
        out.loc[idx, "barrier_ambiguous"] = pd.Series(
            [bool(x) for x in amb], index=idx, dtype=object
        )
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
        null_any = or_(*[getattr(FeatureSnapshot, c).is_(None) for c in NULL_CHECK_COLS])
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
                or_(*[getattr(FeatureSnapshot, c).is_(None) for c in NULL_CHECK_COLS])
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
                PriceDaily.high,
                PriceDaily.low,
                PriceDaily.close,
                PriceDaily.is_extrapolated,
            )
            .where(PriceDaily.ticker.in_(tickers))
            .where(PriceDaily.trade_date > start)
        ).all()
        prices = pd.DataFrame(
            price_rows,
            columns=["ticker", "trade_date", "open", "high", "low", "close",
                     "is_extrapolated"],
        )
        end = snaps["snapshot_date"].max() + timedelta(days=MAX_HORIZON * 2 + 10)
        sessions = trading_days(start, end)

        result = compute_forward_returns(snaps, prices, sessions)
        labels = compute_barrier_labels(snaps, prices, sessions)
        result = pd.concat([result, labels[BARRIER_COLS]], axis=1)
        return self._bulk_update(result)

    def _bulk_update(self, result: pd.DataFrame) -> int:
        """Write all target + barrier columns (NaN → NULL) by primary key."""
        params = []
        filled = 0
        for row in result.to_dict("records"):
            p = {"snapshot_date": row["snapshot_date"], "ticker": row["ticker"]}
            for col in TARGET_COLS:
                v = row[col]
                if pd.isna(v):
                    p[col] = None
                else:
                    p[col] = float(v)
                    filled += 1
            if "return_barrier_14d" in row:
                v = row["return_barrier_14d"]
                ok = v is not None and not pd.isna(v)
                p["return_barrier_14d"] = float(v) if ok else None
                p["barrier_outcome"] = int(row["barrier_outcome"]) if ok else None
                p["barrier_ambiguous"] = bool(row["barrier_ambiguous"]) if ok else None
                filled += int(ok)
            params.append(p)
        for i in range(0, len(params), UPDATE_BATCH):
            self.session.execute(update(FeatureSnapshot), params[i : i + UPDATE_BATCH])
        return filled


__all__ = [
    "HORIZONS",
    "MAIN_BARRIER",
    "TargetBackfillComputer",
    "compute_barrier_labels",
    "compute_forward_returns",
]
