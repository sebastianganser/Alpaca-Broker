"""Price facts of the context pack: ranges, volatility, support / resistance,
earnings reactions (pure functions, synthetic bars, no DB)."""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pytest

from trading_signals.derived.context_pack.rows import BarRow, EarningsRow
from trading_signals.derived.context_pack.technicals import (
    LEVEL_MIN_BARS,
    MAX_LEVELS_PER_SIDE,
    RANGE_MIN_SESSIONS,
    REASON_OPEN,
    REASON_PRICES,
    REASON_TIMING,
    analyse_prices,
    bars_frame,
    earnings_reactions,
    mean_abs_move,
    range_52w,
    realised_vol,
    support_resistance,
)
from trading_signals.utils.market_calendar import trading_days

D = date(2026, 10, 16)  # a Friday, NYSE session
SESSIONS = trading_days(D - timedelta(days=900), D)


def bars(closes, dates=None, spread=1.0, volume=1000.0):
    """One bar per date: open = previous close, high / low = close +- spread."""
    dates = dates or SESSIONS[-len(closes) :]
    out = []
    prev = closes[0]
    for d, c in zip(dates, closes, strict=True):
        out.append(
            BarRow(d, prev, max(prev, c) + spread, min(prev, c) - spread, c, volume)
        )
        prev = c
    return out


def flat_bars(n=300, close=100.0, overrides=None):
    """``n`` bars with a constant close, high / low = close +- 1."""
    dates = SESSIONS[-n:]
    closes = [close] * n
    if overrides:
        for d, c in overrides.items():
            closes[dates.index(d)] = c
    return bars(closes, dates)


# ── bars frame ────────────────────────────────────────────────────────


def test_bars_frame_sorts_deduplicates_and_drops_unusable_bars():
    a, b, c = SESSIONS[-3:]
    rows = [
        BarRow(c, 3, 4, 2, 3.5, 10),
        BarRow(a, 1, 2, 0.5, 1.5, 10),
        BarRow(b, 2, 3, 1, 2.5, None),
        BarRow(b, 2, 3, 1, 2.6, 20),  # same date again: the last one wins
        BarRow(SESSIONS[-4], 0, 1, 0.5, 0.8, 5),  # a zero open is unusable
        BarRow(SESSIONS[-5], 1, None, 0.5, 0.8, 5),  # a missing high is unusable
    ]
    frame = bars_frame(rows)
    assert list(frame["date"]) == [a, b, c]
    assert frame.loc[1, "close"] == 2.6 and frame.loc[1, "volume"] == 20
    assert bars_frame([]).empty


# ── 52-week range ─────────────────────────────────────────────────────


def test_range_52w_uses_the_last_252_sessions_and_positions_the_close():
    closes = list(np.linspace(50.0, 150.0, 300))
    frame = bars_frame(bars(closes, spread=0.5))
    rng = range_52w(frame, D)
    assert rng is not None and rng.sessions == 252
    window = closes[-252:]
    assert rng.low == pytest.approx(window[0] - 0.5, abs=1.0)
    assert rng.high == pytest.approx(150.5)
    assert rng.close == pytest.approx(150.0)
    assert rng.position == pytest.approx((150.0 - rng.low) / (rng.high - rng.low))
    assert rng.dist_to_high == pytest.approx(150.0 / 150.5 - 1.0)
    assert rng.dist_to_low == pytest.approx(150.0 / rng.low - 1.0)
    # the 52-week window really is 252 sessions: older lows do not count
    assert rng.low_date >= SESSIONS[-252]
    assert rng.low > 50.0


def test_range_52w_needs_a_bar_on_the_session_and_enough_history():
    frame = bars_frame(flat_bars(300))
    assert range_52w(frame, D) is not None
    assert range_52w(frame[frame["date"] < D], D) is None  # stale: no bar on d
    short = bars_frame(flat_bars(RANGE_MIN_SESSIONS - 1))
    assert range_52w(short, D) is None
    assert range_52w(bars_frame(flat_bars(RANGE_MIN_SESSIONS)), D) is not None


def test_a_flat_range_has_no_position():
    frame = bars_frame([BarRow(d, 10, 10, 10, 10, 1) for d in SESSIONS[-200:]])
    rng = range_52w(frame, D)
    assert rng is not None and rng.position is None


def test_range_ignores_bars_after_the_session():
    leaked = [*flat_bars(300), BarRow(D + timedelta(days=3), 100, 999, 1, 100, 1)]
    rng = range_52w(bars_frame([b for b in leaked if b.trade_date <= D]), D)
    assert rng is not None and rng.high == 101.0 and rng.low == 99.0
    # analyse_prices cuts the future itself
    analysis = analyse_prices(leaked, SESSIONS, D)
    assert analysis is not None and analysis.range52.high == 101.0
    assert analysis.last_bar == D


# ── realised volatility ───────────────────────────────────────────────


def test_realised_vol_is_the_annualised_std_of_log_returns():
    rng = np.random.default_rng(1)
    closes = list(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 120))))
    frame = bars_frame(bars(closes))
    for n in (20, 60):
        last = np.array(closes[-(n + 1) :])
        expected = float(np.std(np.diff(np.log(last)), ddof=1) * math.sqrt(252))
        assert realised_vol(frame, SESSIONS, n, D) == pytest.approx(expected)
    assert realised_vol(bars_frame(flat_bars(120)), SESSIONS, 20, D) == 0.0


def test_realised_vol_needs_every_session_and_ignores_the_future():
    rng = np.random.default_rng(2)
    closes = list(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 120))))
    full = bars(closes)
    gap = [b for b in full if b.trade_date != SESSIONS[-5]]  # a missing real bar
    assert realised_vol(bars_frame(gap), SESSIONS, 20, D) is None
    assert realised_vol(bars_frame(gap), SESSIONS, 60, D) is None
    assert realised_vol(bars_frame(full[-10:]), SESSIONS, 20, D) is None  # too short
    leaked = [*full, BarRow(D + timedelta(days=3), 1, 500, 1, 400, 1)]
    frame = bars_frame([b for b in leaked if b.trade_date <= D])
    assert realised_vol(frame, SESSIONS, 20, D) == pytest.approx(
        realised_vol(bars_frame(full), SESSIONS, 20, D)
    )


# ── support / resistance ──────────────────────────────────────────────


def structured_bars(peaks=(), troughs=(), n=150, close=100.0):
    """Flat bars (high 101, low 99) with single spikes at the given indexes.

    A spike is ``(index, price)``; flat bars never form a pivot (a pivot must be
    strictly above the five bars before it).
    """
    dates = SESSIONS[-n:]
    out = [BarRow(d, close, close + 1, close - 1, close, 1000.0) for d in dates]
    for i, price in peaks:
        out[i] = BarRow(dates[i], close, price, close - 1, close, 1000.0)
    for i, price in troughs:
        out[i] = BarRow(dates[i], close, close + 1, price, close, 1000.0)
    return out


def test_two_swing_highs_make_a_resistance_and_two_lows_a_support():
    data = structured_bars(
        peaks=[(40, 110.0), (90, 110.0)], troughs=[(60, 90.0), (110, 90.0)]
    )
    levels = support_resistance(bars_frame(data), D)
    by_kind = {lv.kind: lv for lv in levels}
    assert len(levels) == 2
    res, sup = by_kind["resistance"], by_kind["support"]
    assert res.price == pytest.approx(110.0) and res.touches == 2
    assert res.distance == pytest.approx(0.10)
    assert sup.price == pytest.approx(90.0) and sup.touches == 2
    assert sup.distance == pytest.approx(-0.10)
    dates = SESSIONS[-150:]
    assert res.last_touch == dates[90] and sup.last_touch == dates[110]


def test_a_single_pivot_is_no_level_and_the_newest_bars_cannot_be_pivots():
    n = 150
    one = structured_bars(peaks=[(40, 110.0)])
    assert support_resistance(bars_frame(one), D) == ()
    # a spike in the last 5 bars is not confirmed yet: no pivot, no lookahead
    unconfirmed = structured_bars(peaks=[(40, 110.0), (n - 3, 110.0)])
    assert support_resistance(bars_frame(unconfirmed), D) == ()
    confirmed = structured_bars(peaks=[(40, 110.0), (n - 7, 110.0)])
    assert [lv.kind for lv in support_resistance(bars_frame(confirmed), D)] == [
        "resistance"
    ]


def test_close_pivots_merge_into_one_level_and_far_ones_do_not():
    merged = structured_bars(peaks=[(40, 110.0), (90, 110.4)])
    (level,) = support_resistance(bars_frame(merged), D)
    assert level.touches == 2 and level.price == pytest.approx(110.2)
    far = structured_bars(peaks=[(40, 110.0), (90, 118.0)])
    assert support_resistance(bars_frame(far), D) == ()


def test_the_nearest_levels_of_each_side_are_kept_and_sorted_by_distance():
    peaks = []
    for k, price in enumerate((104.0, 108.0, 112.0, 116.0)):  # four resistances
        peaks += [(10 + 12 * k, price), (70 + 12 * k, price)]
    data = structured_bars(peaks=peaks, n=150)
    levels = support_resistance(bars_frame(data), D)
    resistances = [lv for lv in levels if lv.kind == "resistance"]
    assert len(resistances) == MAX_LEVELS_PER_SIDE == 3
    assert [round(lv.price) for lv in resistances] == [104, 108, 112]


def test_levels_need_enough_history_and_only_use_bars_up_to_d():
    short = structured_bars(peaks=[(10, 110.0), (30, 110.0)], n=LEVEL_MIN_BARS - 1)
    assert support_resistance(bars_frame(short), D) == ()
    data = structured_bars(peaks=[(40, 110.0), (90, 110.0)])
    # a leaky spike after the session does not create (or move) a level
    leaked = [
        *data,
        BarRow(D + timedelta(days=3), 100, 130, 99, 100, 1),
    ]
    analysis = analyse_prices(leaked, SESSIONS, D)
    assert analysis is not None
    assert [round(lv.price) for lv in analysis.resistances] == [110]
    assert analysis.supports == ()


def test_levels_are_deterministic():
    data = structured_bars(
        peaks=[(40, 110.0), (90, 110.0)], troughs=[(60, 90.0), (110, 90.0)]
    )
    first = support_resistance(bars_frame(data), D)
    second = support_resistance(bars_frame(list(reversed(data))), D)
    assert first == second


# ── earnings reactions ────────────────────────────────────────────────


def earning(day, timing):
    return EarningsRow("T", day, timing, 1.0, 1.1, 10.0)


E = SESSIONS[-30]  # an earnings day inside the window
IE = SESSIONS.index(E)


def reaction_bars(**overrides):
    """Constant 100 except the given session offsets from ``E`` (``p1`` = E+1 ...)."""
    prices = {}
    for key, value in overrides.items():
        offset = int(key[1:]) * (-1 if key[0] == "m" else 1)
        prices[SESSIONS[IE + offset]] = value
    return flat_bars(300, 100.0, prices)


def test_a_report_before_the_open_reacts_on_the_report_day():
    data = reaction_bars(p0=110.0, p1=121.0)  # +10 % on E, +10 % the day after
    (r,) = earnings_reactions([earning(E, "BMO")], data, SESSIONS, D)
    assert r.timing == "BMO"
    assert r.day0 == pytest.approx(0.10) and r.day1 == pytest.approx(0.10)
    assert r.reason0 is None and r.reason1 is None
    # during market hours the move also happens on the report day
    (dmh,) = earnings_reactions([earning(E, "DMH")], data, SESSIONS, D)
    assert dmh.day0 == pytest.approx(0.10)


def test_a_report_after_the_close_reacts_on_the_next_session():
    data = reaction_bars(p0=100.0, p1=95.0, p2=99.75)  # -5 % next day, +5 % after
    (r,) = earnings_reactions([earning(E, "AMC")], data, SESSIONS, D)
    assert r.day0 == pytest.approx(-0.05) and r.day1 == pytest.approx(0.05)
    # the report day itself carries no reaction of an AMC report
    spike_on_report_day = reaction_bars(p0=130.0, p1=130.0, p2=130.0)
    (r2,) = earnings_reactions([earning(E, "AMC")], spike_on_report_day, SESSIONS, D)
    assert r2.day0 == pytest.approx(0.0)  # E+1 close / E close


def test_unknown_timing_or_a_non_session_has_no_reaction_and_says_why():
    data = reaction_bars(p0=110.0)
    (none,) = earnings_reactions([earning(E, None)], data, SESSIONS, D)
    assert none.day0 is None and none.day1 is None
    assert none.reason0 == REASON_TIMING and none.reason1 == REASON_TIMING
    (odd,) = earnings_reactions([earning(E, "TAS")], data, SESSIONS, D)
    assert odd.reason0 == REASON_TIMING
    sunday = E + timedelta(days=(6 - E.weekday()) % 7 or 7)
    (weekend,) = earnings_reactions([earning(sunday, "BMO")], data, SESSIONS, D)
    assert weekend.day0 is None and weekend.reason0 == REASON_PRICES


def test_a_missing_real_bar_gives_no_number_instead_of_a_guess():
    data = [b for b in reaction_bars(p0=110.0) if b.trade_date != E]
    (r,) = earnings_reactions([earning(E, "BMO")], data, SESSIONS, D)
    assert r.day0 is None and r.reason0 == REASON_PRICES
    assert r.day1 is None and r.reason1 == REASON_PRICES


def test_reactions_use_only_sessions_up_to_the_pack_day():
    # report after the close on the day BEFORE the pack day: day 0 is the pack
    # day, day +1 has not happened yet - even if a leaky bar after d exists
    prev = SESSIONS[-2]
    data = flat_bars(300, 100.0, {D: 104.0})
    leaked = [*data, BarRow(D + timedelta(days=3), 104, 500, 1, 400.0, 1)]
    (r,) = earnings_reactions([earning(prev, "AMC")], leaked, SESSIONS, D)
    assert r.day0 == pytest.approx(0.04)
    assert r.day1 is None and r.reason1 == REASON_OPEN
    # a report ON the pack day after the close: nothing is known yet
    (today,) = earnings_reactions([earning(D, "AMC")], leaked, SESSIONS, D)
    assert today.day0 is None and today.reason0 == REASON_OPEN
    # a report before the open on the pack day: day 0 is known, day +1 is not
    (bmo,) = earnings_reactions([earning(D, "BMO")], leaked, SESSIONS, D)
    assert bmo.day0 == pytest.approx(0.04) and bmo.reason1 == REASON_OPEN


def test_reactions_come_newest_first_and_the_mean_move_ignores_gaps():
    older, newer = SESSIONS[-60], SESSIONS[-20]
    io, inew = SESSIONS.index(older), SESSIONS.index(newer)
    data = flat_bars(300, 100.0, {SESSIONS[io]: 106.0, SESSIONS[inew]: 97.0})
    events = [earning(older, "BMO"), earning(newer, "BMO"), earning(E, None)]
    reactions = earnings_reactions(events, data, SESSIONS, D)
    assert [r.earnings_date for r in reactions] == sorted(
        [older, newer, E], reverse=True
    )
    mean, n = mean_abs_move(reactions)
    assert n == 2 and mean == pytest.approx((0.06 + 0.03) / 2)
    assert mean_abs_move([]) == (None, 0)
    assert mean_abs_move([r for r in reactions if r.day0 is None]) == (None, 0)
