"""Price-based facts of a ticker file, computed from real daily bars (pure).

Everything here uses ONLY bars dated on or before the pack session ``d`` (the
loader and :func:`~.pit.apply_pit` cut them; every function here also takes
``d`` and ignores anything later), so a figure never contains information that
was not public at the close of ``d``.

* :func:`range_52w` - 52-week high / low and the position of the close in
  that range (last :data:`RANGE_SESSIONS` real sessions).
* :func:`realised_vol` - annualised standard deviation of the daily log
  close-to-close returns over the last 20 / 60 sessions.
* :func:`support_resistance` - support / resistance levels from swing highs
  and lows (algorithm below).
* :func:`earnings_reactions` - price reaction of the last reports on day 0 and
  day +1, using the report timing (BMO / AMC) and real closes.

**Support / resistance algorithm** (deterministic, no parameters fitted):

1. Window: the last :data:`LEVEL_SESSIONS` (252) real bars up to ``d``.
2. Swing high at bar ``i``: ``high[i]`` is higher than every high of the
   :data:`SWING_K` (5) bars before it and not lower than every high of the 5
   bars after it (so a flat top counts once, at its first bar). Swing low:
   the mirror image on the lows. A pivot needs 5 later bars to be confirmed, so
   the newest 5 bars never form a pivot - nothing is "pre-announced".
3. All pivot prices (highs and lows together - an old resistance becomes
   support once price is above it) are sorted and clustered: a pivot joins
   the cluster that started at the lowest price ``p`` if it is at most
   ``p * (1 + tol)``. ``tol`` is half of the 14-bar average true range as a
   share of the last close, limited to 0.8 % ... 3 %.
4. A cluster is a level if it has at least :data:`MIN_TOUCHES` (2) pivots. The
   level price is the mean of its pivots; "touches" counts the pivots,
   "last touch" is the date of the newest one.
5. Levels below the last close are supports, levels above are resistances;
   the nearest :data:`MAX_LEVELS_PER_SIDE` (3) of each side are kept. If
   price is at a 52-week extreme, one side is empty - that is information, not
   an error.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from trading_signals.derived.context_pack.rows import BarRow, EarningsRow

#: Sessions of the 52-week range (as ``st_dist_52w_high``) and the minimum.
RANGE_SESSIONS = 252
RANGE_MIN_SESSIONS = 126
#: Trading days per year for the annualisation of the volatility.
TRADING_DAYS_PER_YEAR = 252
VOL_WINDOWS = (20, 60)

LEVEL_SESSIONS = 252
#: A level window needs at least this many bars.
LEVEL_MIN_BARS = 60
SWING_K = 5
MIN_TOUCHES = 2
MAX_LEVELS_PER_SIDE = 3
ATR_WINDOW = 14
ATR_TOL_FACTOR = 0.5
MIN_TOL = 0.008
MAX_TOL = 0.03

#: Report timings with a known reaction day: before the open / during the day
#: (the move happens on the report day) or after the close (next session).
SAME_DAY_TIMINGS = ("BMO", "DMH")
AFTER_CLOSE_TIMING = "AMC"

#: Why a reaction cell has no number.
REASON_TIMING = "timing"  # report time unknown
REASON_OPEN = "open"  # the session has not happened yet (after the pack day)
REASON_PRICES = "prices"  # a needed real bar is missing


# ── bars ─────────────────────────────────────────────────────────────


def bars_frame(bars: Sequence[BarRow]) -> pd.DataFrame:
    """Real bars as a frame ``date, open, high, low, close, volume`` (ascending).

    One row per date (the last one wins); a bar without a positive
    open / high / low / close is unusable and dropped (never repaired with a
    made-up value). Volume may be NaN.
    """
    columns = ["date", "open", "high", "low", "close", "volume"]
    if not bars:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(
        [(b.trade_date, b.open, b.high, b.low, b.close, b.volume) for b in bars],
        columns=columns,
    )
    for col in columns[1:]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
    frame = frame.sort_values("date", kind="mergesort").drop_duplicates(
        "date", keep="last"
    )
    ok = (frame[["open", "high", "low", "close"]] > 0).all(axis=1)
    return frame[ok].reset_index(drop=True)


# ── 52-week range ────────────────────────────────────────────────────


@dataclass(frozen=True)
class RangePosition:
    """52-week range and where the last close sits in it."""

    low: float
    high: float
    low_date: date
    high_date: date
    close: float
    sessions: int

    @property
    def position(self) -> float | None:
        """0 = at the low, 1 = at the high (None for a flat range)."""
        span = self.high - self.low
        if span <= 0:
            return None
        return min(1.0, max(0.0, (self.close - self.low) / span))

    @property
    def dist_to_low(self) -> float:
        return self.close / self.low - 1.0

    @property
    def dist_to_high(self) -> float:
        return self.close / self.high - 1.0


def range_52w(frame: pd.DataFrame, d: date) -> RangePosition | None:
    """52-week range up to ``d``; None if ``d`` has no bar or history is short."""
    f = frame[frame["date"] <= d]
    if f.empty or f["date"].iloc[-1] != d:
        return None
    win = f.tail(RANGE_SESSIONS)
    if len(win) < RANGE_MIN_SESSIONS:
        return None
    hi, lo = win["high"].idxmax(), win["low"].idxmin()
    return RangePosition(
        low=float(win.loc[lo, "low"]),
        high=float(win.loc[hi, "high"]),
        low_date=win.loc[lo, "date"],
        high_date=win.loc[hi, "date"],
        close=float(win["close"].iloc[-1]),
        sessions=len(win),
    )


# ── realised volatility ──────────────────────────────────────────────


def realised_vol(
    frame: pd.DataFrame, sessions: Sequence[date], n: int, d: date
) -> float | None:
    """Annualised st. dev. of the last ``n`` daily log returns up to ``d``.

    Needs a real bar for each of the last ``n + 1`` NYSE sessions (a gap would
    turn a two-day move into a one-day move); otherwise None. ``sessions`` is
    the ascending NYSE session list.
    """
    wanted = [s for s in sessions if s <= d][-(n + 1) :]
    if len(wanted) < n + 1:
        return None
    closes = dict(zip(frame["date"], frame["close"], strict=True))
    try:
        values = np.array([closes[s] for s in wanted], dtype="float64")
    except KeyError:
        return None
    if not np.all(np.isfinite(values)) or np.any(values <= 0):
        return None
    returns = np.diff(np.log(values))
    return float(np.std(returns, ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR))


# ── support / resistance ─────────────────────────────────────────────


@dataclass(frozen=True)
class Level:
    """A support or resistance level."""

    price: float
    kind: str  # "support" | "resistance"
    touches: int
    last_touch: date
    distance: float  # level / last close - 1


def _pivots(high: np.ndarray, low: np.ndarray, k: int) -> list[tuple[int, float]]:
    """``(bar index, price)`` of all confirmed swing highs and lows."""
    n = len(high)
    out: list[tuple[int, float]] = []
    for i in range(k, n - k):
        left_h, right_h = high[i - k : i], high[i + 1 : i + k + 1]
        if high[i] > left_h.max() and high[i] >= right_h.max():
            out.append((i, float(high[i])))
        left_l, right_l = low[i - k : i], low[i + 1 : i + k + 1]
        if low[i] < left_l.min() and low[i] <= right_l.min():
            out.append((i, float(low[i])))
    return out


def _atr_pct(frame: pd.DataFrame) -> float | None:
    """14-bar average true range as a share of the last close."""
    f = frame.tail(ATR_WINDOW + 1)
    if len(f) < ATR_WINDOW + 1:
        return None
    high, low = f["high"].to_numpy()[1:], f["low"].to_numpy()[1:]
    prev = f["close"].to_numpy()[:-1]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    close = float(f["close"].iloc[-1])
    return float(tr.mean() / close) if close > 0 else None


def support_resistance(frame: pd.DataFrame, d: date) -> tuple[Level, ...]:
    """Support / resistance levels up to ``d`` (module docstring: algorithm)."""
    f = frame[frame["date"] <= d].tail(LEVEL_SESSIONS).reset_index(drop=True)
    if len(f) < LEVEL_MIN_BARS:
        return ()
    atr = _atr_pct(f)
    tol = MIN_TOL if atr is None else min(MAX_TOL, max(MIN_TOL, ATR_TOL_FACTOR * atr))
    dates = f["date"].to_list()
    pivots = sorted(
        _pivots(f["high"].to_numpy(), f["low"].to_numpy(), SWING_K),
        key=lambda p: (p[1], p[0]),
    )
    clusters: list[list[tuple[int, float]]] = []
    for pivot in pivots:
        if clusters and pivot[1] <= clusters[-1][0][1] * (1.0 + tol):
            clusters[-1].append(pivot)
        else:
            clusters.append([pivot])
    close = float(f["close"].iloc[-1])
    supports: list[Level] = []
    resistances: list[Level] = []
    for cluster in clusters:
        if len(cluster) < MIN_TOUCHES:
            continue
        price = float(np.mean([p[1] for p in cluster]))
        level = Level(
            price=price,
            kind="support" if price < close else "resistance",
            touches=len(cluster),
            last_touch=dates[max(p[0] for p in cluster)],
            distance=price / close - 1.0,
        )
        (supports if price < close else resistances).append(level)
    supports.sort(key=lambda lv: -lv.price)  # nearest below first
    resistances.sort(key=lambda lv: lv.price)  # nearest above first
    return tuple(supports[:MAX_LEVELS_PER_SIDE] + resistances[:MAX_LEVELS_PER_SIDE])


# ── bundle ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PriceAnalysis:
    """Price facts of one ticker at the close of the pack session."""

    range52: RangePosition | None
    rv20: float | None
    rv60: float | None
    levels: tuple[Level, ...]
    n_bars: int
    last_bar: date | None

    @property
    def supports(self) -> tuple[Level, ...]:
        return tuple(lv for lv in self.levels if lv.kind == "support")

    @property
    def resistances(self) -> tuple[Level, ...]:
        return tuple(lv for lv in self.levels if lv.kind == "resistance")


def analyse_prices(
    bars: Sequence[BarRow], sessions: Sequence[date], d: date
) -> PriceAnalysis | None:
    """All price facts from the bars up to ``d`` (None without any bar)."""
    frame = bars_frame([b for b in bars if b.trade_date <= d])
    if frame.empty:
        return None
    return PriceAnalysis(
        range52=range_52w(frame, d),
        rv20=realised_vol(frame, sessions, VOL_WINDOWS[0], d),
        rv60=realised_vol(frame, sessions, VOL_WINDOWS[1], d),
        levels=support_resistance(frame, d),
        n_bars=len(frame),
        last_bar=frame["date"].iloc[-1],
    )


# ── earnings reactions ───────────────────────────────────────────────


@dataclass(frozen=True)
class EarningsReaction:
    """Price reaction of one report (close-to-close)."""

    earnings_date: date
    timing: str | None
    #: First session that trades on the report (BMO / DMH: the report day, AMC:
    #: the next session) versus the close before it; None = see ``reason0``.
    day0: float | None
    #: The following session versus the close of day 0.
    day1: float | None
    reason0: str | None = None
    reason1: str | None = None


def earnings_reactions(
    events: Sequence[EarningsRow],
    bars: Sequence[BarRow],
    sessions: Sequence[date],
    d: date,
) -> list[EarningsReaction]:
    """Reactions of ``events`` (newest first), from real closes up to ``d``.

    ``sessions`` is the ascending NYSE session list. Only sessions up to ``d``
    exist for this function: a day that lies after the pack day is reported as
    ``REASON_OPEN``, never guessed. A report with an unknown time (not BMO /
    AMC / DMH) has no attributable reaction day (``REASON_TIMING``).
    """
    frame = bars_frame([b for b in bars if b.trade_date <= d])
    closes = dict(zip(frame["date"], frame["close"], strict=True))
    seq = [s for s in sessions if s <= d]
    pos = {s: i for i, s in enumerate(seq)}

    def close_at(i: int) -> float | None:
        if i < 0 or i >= len(seq):
            return None
        return closes.get(seq[i])

    out: list[EarningsReaction] = []
    for e in sorted(events, key=lambda e: e.earnings_date, reverse=True):
        timing = (e.time_of_day or "").upper() or None
        if timing is not None and timing not in (*SAME_DAY_TIMINGS, AFTER_CLOSE_TIMING):
            timing = None
        i = pos.get(e.earnings_date)
        if timing is None:
            out.append(
                EarningsReaction(
                    e.earnings_date, None, None, None, REASON_TIMING, REASON_TIMING
                )
            )
            continue
        if i is None:  # not an NYSE session (or older than the session list)
            out.append(
                EarningsReaction(
                    e.earnings_date,
                    timing,
                    None,
                    None,
                    REASON_PRICES,
                    REASON_PRICES,
                )
            )
            continue
        prev_i, s0_i = (i - 1, i) if timing in SAME_DAY_TIMINGS else (i, i + 1)
        day0, reason0 = _session_return(close_at, prev_i, s0_i, len(seq))
        day1, reason1 = _session_return(close_at, s0_i, s0_i + 1, len(seq))
        out.append(
            EarningsReaction(e.earnings_date, timing, day0, day1, reason0, reason1)
        )
    return out


def _session_return(
    close_at, i_from: int, i_to: int, n_sessions: int
) -> tuple[float | None, str | None]:
    """``(return, reason)`` of the close at ``i_to`` versus the close at ``i_from``."""
    if i_to >= n_sessions:
        return None, REASON_OPEN
    a, b = close_at(i_from), close_at(i_to)
    if a is None or b is None or a <= 0:
        return None, REASON_PRICES
    return b / a - 1.0, None


def mean_abs_move(reactions: Sequence[EarningsReaction]) -> tuple[float | None, int]:
    """``(mean |day 0|, n)`` over the reports with a day-0 reaction."""
    values = [abs(r.day0) for r in reactions if r.day0 is not None]
    if not values:
        return None, 0
    return float(np.mean(values)), len(values)


__all__ = [
    "EarningsReaction",
    "Level",
    "PriceAnalysis",
    "RangePosition",
    "analyse_prices",
    "bars_frame",
    "earnings_reactions",
    "mean_abs_move",
    "range_52w",
    "realised_vol",
    "support_resistance",
]
