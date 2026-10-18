"""Cross-section statistics and the per-ticker context of the pack (pure).

* :class:`CrossSection` - percentiles of a raw value among the tickers that
  passed the quality gate (the ``screen.csv`` population), universe-wide and
  within a sector. The percentile is the share of gate tickers with a smaller
  value (ties count half): a high percentile means a high *raw value*, not
  "bullish".
* :class:`TickerContext` - everything one ticker file is rendered from.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from trading_signals.analysis.screen import (
    DEFAULT_CONFIG,
    ScreenConfig,
    feature_values,
    group_min_features,
)
from trading_signals.derived.context_pack.constants import (
    MIN_PERCENTILE_OBS,
    MIN_SECTOR_OBS,
)
from trading_signals.derived.context_pack.fmt import DASH, NA, pct_cell
from trading_signals.derived.context_pack.fmt import to_float as f
from trading_signals.derived.context_pack.rows import PeerTable, TickerRaw
from trading_signals.derived.context_pack.technicals import PriceAnalysis


def mid_rank_pct(
    sorted_values: np.ndarray, x: float, min_obs: int = MIN_PERCENTILE_OBS
) -> float | None:
    """Percentile (0-100) of ``x`` among ``sorted_values``, ties count half.

    None if fewer than ``min_obs`` observations.
    """
    n = len(sorted_values)
    if n < max(1, min_obs):
        return None
    lo = int(np.searchsorted(sorted_values, x, side="left"))
    hi = int(np.searchsorted(sorted_values, x, side="right"))
    return 100.0 * (lo + hi) / 2.0 / n


class CrossSection:
    """Percentile lookups over the gate tickers of ONE session."""

    def __init__(
        self, table: pd.DataFrame, gated: pd.Series, sectors: pd.Series
    ) -> None:
        """Build the lookups.

        Args:
            table: numeric columns indexed by ticker (all snapshot tickers).
            gated: bool per ticker - the quality gate (the population).
            sectors: sector name per ticker (missing = no sector percentile).
        """
        self._table = table
        self._gated = gated.reindex(table.index).fillna(False).astype(bool)
        self._sectors = sectors.reindex(table.index)
        self._univ: dict[str, np.ndarray] = {}
        self._sect: dict[tuple[str, str], np.ndarray] = {}

    @property
    def n_universe(self) -> int:
        return int(self._gated.sum())

    def _sorted(self, mask: pd.Series, col: str) -> np.ndarray:
        if col not in self._table.columns:
            return np.array([])
        vals = pd.to_numeric(self._table.loc[mask, col], errors="coerce")
        arr = vals.to_numpy(dtype="float64")
        return np.sort(arr[np.isfinite(arr)])

    def _universe_values(self, col: str) -> np.ndarray:
        if col not in self._univ:
            self._univ[col] = self._sorted(self._gated, col)
        return self._univ[col]

    def _sector_values(self, col: str, sector: str) -> np.ndarray:
        key = (col, sector)
        if key not in self._sect:
            self._sect[key] = self._sorted(self._gated & (self._sectors == sector), col)
        return self._sect[key]

    def universe_pct(self, col: str, value: Any) -> float | None:
        v = f(value)
        if v is None:
            return None
        return mid_rank_pct(self._universe_values(col), v)

    def sector_pct(self, col: str, value: Any, sector: str | None) -> float | None:
        v = f(value)
        if v is None or not sector or pd.isna(sector):
            return None
        return mid_rank_pct(self._sector_values(col, sector), v, MIN_SECTOR_OBS)

    def sector_median(self, col: str, sector: str | None) -> tuple[float | None, int]:
        """``(median, n)`` of a column among the gate tickers of a sector."""
        if not sector or pd.isna(sector):
            return None, 0
        arr = self._sector_values(col, sector)
        if len(arr) < MIN_SECTOR_OBS:
            return None, len(arr)
        return float(np.median(arr)), len(arr)


@dataclass
class UniverseInfo:
    """Static ticker information from ``signals.universe``."""

    company_name: str | None = None
    sector: str | None = None
    industry: str | None = None


@dataclass
class TickerContext:
    """Everything one ticker file is rendered from."""

    ticker: str
    session: date
    row: Mapping[str, Any]
    info: UniverseInfo
    gate_ok: bool
    gate_reasons: str
    xs: CrossSection
    fund: Mapping[str, Any]
    fund_date: date | None
    close: float | None
    returns: Mapping[int, float | None]
    spy_returns: Mapping[int, float | None]
    raw: TickerRaw
    market: Mapping[str, float | None]
    coverage: Mapping[str, tuple[int, int]]
    #: Ascending NYSE sessions up to the pack day (earnings reactions, volatility).
    sessions: Sequence[date] = ()
    #: Facts computed from the real bars (None = no bars at all).
    price: PriceAnalysis | None = None
    #: Name of the chart file next to the ticker file (None = no chart written).
    chart_file: str | None = None
    #: Peer section (None = not computed).
    peer_table: PeerTable | None = None

    def val(self, col: str) -> float | None:
        """Snapshot feature as float (None = no coverage)."""
        return f(self.row.get(col))

    def fval(self, col: str) -> float | None:
        """Fundamentals-snapshot value as float."""
        return f(self.fund.get(col))

    def pct(self, col: str, value: Any, sector: bool = False) -> str:
        """Percentile cell for ``value`` of column ``col``."""
        v = f(value)
        if v is None:
            return DASH
        return pct_cell(
            self.xs.universe_pct(col, v),
            self.xs.sector_pct(col, v, self.info.sector) if sector else None,
        )

    def metric(
        self,
        label: str,
        col: str,
        fmt: Callable[[Any], str],
        sector: bool = False,
        value: Any = None,
        use_value: bool = False,
        na: str = NA,
    ) -> list[str]:
        """``[label, formatted value, percentile cell]`` of one table row.

        ``value`` overrides the snapshot value of ``col`` (``use_value=True``),
        e.g. for the fundamentals-snapshot columns (``fund_<col>``) or the
        computed returns. A missing value renders ``na`` and no percentile.
        """
        v = f(value) if use_value else self.val(col)
        if v is None:
            return [label, na, DASH]
        return [label, fmt(v), self.pct(col, v, sector)]

    def fund_metric(
        self,
        label: str,
        name: str,
        fmt: Callable[[Any], str],
        sector: bool = False,
    ) -> list[str]:
        """Like :meth:`metric` for a fundamentals-snapshot column ``name``."""
        return self.metric(
            label, f"fund_{name}", fmt, sector, value=self.fval(name), use_value=True
        )


def metric_text(label: str, value: str, pct: str) -> str:
    """``label: value (P · S)``; a missing percentile is left out."""
    return f"{label}: {value}" + ("" if pct == DASH else f" ({pct})")


def metric_lines(rows: list[list[str]]) -> list[str]:
    """One bullet line per metric row of :meth:`TickerContext.metric`."""
    return ["- " + metric_text(*row) for row in rows]


def metric_group(rows: list[list[str]]) -> str:
    """Several metrics on ONE bullet line (the most compact form)."""
    return "- " + " · ".join(metric_text(*row) for row in rows)


def group_coverage(
    frame: pd.DataFrame, config: ScreenConfig = DEFAULT_CONFIG
) -> pd.DataFrame:
    """Present-feature count per evidence group and row.

    The same feature values the screen uses (:func:`feature_values`), so the
    coverage shown in a ticker file is the screen's own view of its data.
    Returns a frame with one column per group (positional row index).
    """
    frame = frame.reset_index(drop=True)
    return pd.DataFrame(
        {
            g.name: sum(
                feature_values(frame, s).notna().astype(int) for s in g.features
            )
            for g in config.groups
        },
        index=frame.index,
    )


def group_sizes(config: ScreenConfig = DEFAULT_CONFIG) -> dict[str, tuple[int, int]]:
    """``{group: (features, features needed for data)}``."""
    return {
        g.name: (len(g.features), group_min_features(g, config.min_feature_fraction))
        for g in config.groups
    }


#: Market-wide columns of the regime block.
MARKET_COLUMNS: tuple[str, ...] = (
    "macro_vix",
    "macro_vix_regime",
    "macro_yield_spread",
    "macro_hy_spread",
    "macro_dollar_index",
    "macro_inflation_expectation",
    "breadth_advance_decline",
    "breadth_pct_above_sma50",
    "market_sentiment_7d",
)


def market_regime(snap: pd.DataFrame) -> dict[str, float | None]:
    """Market-wide values (identical for all tickers of a session).

    The median over the cross-section, so a ticker with a missing macro value
    does not hide it.
    """
    out: dict[str, float | None] = {}
    for col in MARKET_COLUMNS:
        if col not in snap.columns:
            out[col] = None
            continue
        vals = pd.to_numeric(snap[col], errors="coerce").dropna()
        out[col] = None if vals.empty else float(vals.median())
    return out


VIX_REGIMES = ("Low Vol", "Medium", "High Vol")


def vix_regime_label(regime: float | None) -> str:
    r = f(regime)
    if r is None or not math.isfinite(r):
        return NA
    i = int(round(r))
    return VIX_REGIMES[i] if 0 <= i < len(VIX_REGIMES) else NA
