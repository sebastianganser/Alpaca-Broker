"""SPY (benchmark) forward returns on the project's target definition.

``feature_snapshots`` has no rows for benchmark ETFs, so the market-relative
evaluations (screen backtest, forecast evaluation, event study) compute the
SPY return with the same rule as the targets of every other ticker
(``derived/target_backfill.compute_forward_returns``)::

    return_h = close(d+h) / open(d+1) - 1     (NYSE sessions)

* :func:`benchmark_forward_returns` - pure, unit-tested.
* :func:`load_benchmark_returns` - read-only DB loader for the scripts.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import pandas as pd

from trading_signals.derived.target_backfill import compute_forward_returns

BENCHMARK_TICKER = "SPY"
DEFAULT_HORIZONS = (5, 20, 60)


def benchmark_forward_returns(
    prices: pd.DataFrame,
    sessions: Sequence[date],
    ticker: str = BENCHMARK_TICKER,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> pd.DataFrame:
    """Forward returns of one ticker per NYSE session.

    Args:
        prices: ``ticker``, ``trade_date``, ``open``, ``close`` and optionally
            ``is_extrapolated`` (extrapolated rows are ignored, as in the
            targets).
        sessions: Sorted NYSE sessions covering the dates of interest and the
            ``max(horizons)`` sessions after them.

    Returns:
        Frame indexed by ``snapshot_date`` (Timestamp) with one column
        ``return_<h>d`` per horizon; NaN where the window is incomplete.
    """
    days = [pd.Timestamp(d).date() for d in sessions]
    cols = [(f"return_{h}d", int(h)) for h in horizons]
    px = prices[prices["ticker"] == ticker].copy()
    px["trade_date"] = pd.to_datetime(px["trade_date"]).dt.date
    if "is_extrapolated" not in px.columns:
        px["is_extrapolated"] = False
    snaps = pd.DataFrame({"ticker": ticker, "snapshot_date": days})
    out = compute_forward_returns(snaps, px, days, cols)
    out["snapshot_date"] = pd.to_datetime(out["snapshot_date"])
    return out.drop(columns="ticker").set_index("snapshot_date")


def load_benchmark_returns(
    session,
    start: date,
    ticker: str = BENCHMARK_TICKER,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> pd.DataFrame:
    """Read-only: SPY forward returns for the sessions from ``start`` on."""
    from sqlalchemy import text

    from trading_signals.utils.market_calendar import trading_days

    prices = pd.read_sql(
        text(
            "SELECT ticker, trade_date, open, close, is_extrapolated "
            "FROM signals.prices_daily "
            "WHERE ticker = :ticker AND trade_date >= :start"
        ),
        session.bind,
        params={"ticker": ticker, "start": start},
    )
    if prices.empty:
        return pd.DataFrame(columns=[f"return_{h}d" for h in horizons])
    for c in ("open", "close"):
        prices[c] = pd.to_numeric(prices[c], errors="coerce")
    last = pd.Timestamp(prices["trade_date"].max()).date()
    return benchmark_forward_returns(
        prices, trading_days(start, last), ticker, horizons
    )


__all__ = [
    "BENCHMARK_TICKER",
    "DEFAULT_HORIZONS",
    "benchmark_forward_returns",
    "load_benchmark_returns",
]
