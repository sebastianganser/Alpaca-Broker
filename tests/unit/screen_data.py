"""Synthetic ``feature_snapshots`` cross-sections for the screen tests (no DB)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from trading_signals.analysis.screen import SCREEN_GROUPS, scored_features


def _raw_value(sign: int, transform: str, signed: np.ndarray) -> np.ndarray:
    """Raw feature values whose ORIENTED percentile rank increases with z.

    ``signed`` is ``sign * z + noise``: for sign +1 high raw = bullish, for
    sign -1 low raw = bullish. ``inverse`` features are multiples (their
    yield ``1/x`` must increase with z), ``nonneg`` features stay positive.
    """
    if transform == "inverse":
        return np.exp(-signed)  # 1/x = exp(signed), increasing in z
    if transform == "nonneg":
        return np.exp(signed)  # positive, oriented by sign
    return signed


def cross_section(
    day: str = "2024-03-01",
    n: int = 120,
    seed: int = 0,
    z: np.ndarray | None = None,
    noise: float = 0.5,
    dollar_volume: float = 5e7,
) -> pd.DataFrame:
    """One date of synthetic snapshots with a latent bullishness ``z``.

    Every scored feature is driven by ``z`` (in the direction of its a-priori
    sign) plus noise, so the screen should rank high-``z`` tickers bullish.
    The latent ``z`` is returned in column ``z``.
    """
    rng = np.random.default_rng(seed)
    z = rng.normal(size=n) if z is None else np.asarray(z, dtype=float)
    data: dict[str, object] = {
        "snapshot_date": pd.Timestamp(day),
        "ticker": [f"T{i:03d}" for i in range(n)],
        "z": z,
        "dollar_volume_20d": np.full(n, dollar_volume),
    }
    for group in SCREEN_GROUPS:
        for spec in group.features:
            signed = spec.sign * z + rng.normal(scale=noise, size=n)
            data[spec.name] = _raw_value(spec.sign, spec.transform, signed)
    df = pd.DataFrame(data)
    assert set(scored_features()) <= set(df.columns)
    return df


def panel(
    n_dates: int = 60,
    n: int = 150,
    seed: int = 0,
    signal: float = 0.0,
    ret_noise: float = 0.04,
) -> pd.DataFrame:
    """Panel with forward returns ``return_20d`` / ``return_60d``.

    ``return_<h>d = signal * z + noise`` (+ a date shock shared by all
    tickers), so ``signal = 0`` is a null panel and ``signal > 0`` plants a
    signal that the screen can find.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n_dates)
    frames = []
    for i, d in enumerate(dates):
        df = cross_section(str(d.date()), n=n, seed=seed * 1000 + i)
        shock = rng.normal(scale=0.02)
        for h in (20, 60):
            df[f"return_{h}d"] = (
                signal * df["z"] + shock + rng.normal(scale=ret_noise, size=n)
            )
        frames.append(df)
    return pd.concat(frames, ignore_index=True)
