"""Tests for the short-term price features (concept 2026-10-06 §7.1, A2)."""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from trading_signals.analysis.feature_groups import feature_group_of
from trading_signals.db.models.features import FEATURE_COLUMNS
from trading_signals.derived.short_term_features import (
    LOOKBACK_SESSIONS,
    PRICE_COLUMNS,
    SHORT_TERM_COLUMNS,
    compute_short_term_frame,
    short_term_features_at,
)


def _business_days(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _prices(n: int = 320, seed: int = 7, start: date = date(2024, 1, 2)) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = _business_days(start, n)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, n)))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, n))
    vol = rng.integers(1_000_000, 3_000_000, n)
    return pd.DataFrame(
        {"trade_date": days, "open": open_, "high": high, "low": low,
         "close": close, "volume": vol}
    )


def _simple(rows: list[tuple]) -> pd.DataFrame:
    """rows = (open, high, low, close, volume) on consecutive business days."""
    days = _business_days(date(2025, 3, 3), len(rows))
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])
    df.insert(0, "trade_date", days)
    return df


class TestDefinitions:
    def test_columns_registered_and_grouped(self):
        for col in SHORT_TERM_COLUMNS:
            assert col in FEATURE_COLUMNS
            assert feature_group_of(col) == "Short Term"
        assert len(SHORT_TERM_COLUMNS) == 9

    def test_return_gap_close_location(self):
        f = compute_short_term_frame(_simple([
            (100, 101, 99, 100, 1000),
            (102, 104, 100, 103, 1000),
        ]))
        last = f.iloc[-1]
        assert last["st_return_1d"] == pytest.approx(0.03)
        assert last["st_gap"] == pytest.approx(0.02)
        assert last["st_close_location"] == pytest.approx(0.75)  # (103-100)/(104-100)
        assert math.isnan(last["st_return_5d"])  # needs 5 prior sessions

    def test_return_5d(self):
        rows = [(100, 101, 99, c, 1000) for c in (100, 101, 102, 103, 104, 110)]
        f = compute_short_term_frame(_simple(rows))
        assert f.iloc[-1]["st_return_5d"] == pytest.approx(0.10)

    def test_rsi_2_simple_means(self):
        # closes 100 → 102 (+2) → 101 (−1): gain mean 1.0, loss mean 0.5 → 66.67
        rows = [(100, 103, 99, c, 1000) for c in (100, 102, 101)]
        f = compute_short_term_frame(_simple(rows))
        assert f.iloc[-1]["st_rsi_2"] == pytest.approx(66.666667, abs=1e-5)
        flat = compute_short_term_frame(_simple([(100, 101, 99, 100, 1000)] * 3))
        assert flat.iloc[-1]["st_rsi_2"] == pytest.approx(50.0)

    def test_bollinger_and_flat_series(self):
        flat = compute_short_term_frame(_simple([(100, 101, 99, 100, 1000)] * 25))
        assert math.isnan(flat.iloc[-1]["st_bollinger_pctb"])  # σ = 0
        p = _prices(60)
        f = compute_short_term_frame(p)
        c = p["close"].iloc[-20:]
        m, s = c.mean(), c.std(ddof=0)
        expected = (c.iloc[-1] - (m - 2 * s)) / (4 * s)
        assert f.iloc[-1]["st_bollinger_pctb"] == pytest.approx(expected, abs=1e-6)

    def test_signed_volume_shock(self):
        rows = [(100, 101, 99, 100, 1000)] * 20
        up = compute_short_term_frame(_simple([*rows, (100, 102, 99, 101, 2000)]))
        down = compute_short_term_frame(_simple([*rows, (100, 101, 98, 99, 2000)]))
        assert up.iloc[-1]["st_signed_volume_shock"] == pytest.approx(math.log(2), abs=1e-6)
        assert down.iloc[-1]["st_signed_volume_shock"] == pytest.approx(-math.log(2), abs=1e-6)

    def test_dist_52w_high_needs_min_history(self):
        short = compute_short_term_frame(_prices(100))
        assert short["st_dist_52w_high"].isna().all()
        p = _prices(300)
        f = compute_short_term_frame(p)
        expected = p["close"].iloc[-1] / p["high"].iloc[-252:].max() - 1
        assert f.iloc[-1]["st_dist_52w_high"] == pytest.approx(expected, abs=1e-6)
        assert (f["st_dist_52w_high"].dropna() <= 1e-9).all()

    def test_invalid_prices_and_volume_become_nan(self):
        f = compute_short_term_frame(_simple([
            (100, 101, 99, 100, 1000),
            (0, 101, 99, 100, 0),
        ]))
        last = f.iloc[-1]
        assert math.isnan(last["st_gap"])
        assert math.isnan(last["st_signed_volume_shock"])

    def test_empty_input(self):
        f = compute_short_term_frame(pd.DataFrame(columns=list(PRICE_COLUMNS)))
        assert f.empty
        assert list(f.columns) == ["trade_date", *SHORT_TERM_COLUMNS]


class TestEarningsReaction:
    def test_two_day_window_and_validity(self):
        df = _simple([(100, 101, 99, 100, 1000)] * 5 + [(110, 111, 109, 110, 1000)] * 60)
        e = df["trade_date"].iloc[5]  # earnings on the session of the jump
        f = compute_short_term_frame(df, [e])
        # pre = session 4 (close 100), post = session 6 (close 110)
        assert f["st_earnings_reaction"].iloc[:6].isna().all()
        assert f["st_earnings_reaction"].iloc[6] == pytest.approx(0.10)
        within = f[(pd.to_datetime(f["trade_date"]) - pd.Timestamp(e)).dt.days <= 60]
        after = f[(pd.to_datetime(f["trade_date"]) - pd.Timestamp(e)).dt.days > 60]
        assert within["st_earnings_reaction"].iloc[6:].notna().all()
        assert after["st_earnings_reaction"].isna().all()

    def test_newer_event_overwrites_and_gap_guard(self):
        df = _prices(120)
        e1, e2 = df["trade_date"].iloc[20], df["trade_date"].iloc[40]
        f = compute_short_term_frame(df, [e2, e1])
        c = df["close"]
        assert f["st_earnings_reaction"].iloc[21] == pytest.approx(c.iloc[21] / c.iloc[19] - 1, abs=1e-6)
        assert f["st_earnings_reaction"].iloc[41] == pytest.approx(c.iloc[41] / c.iloc[39] - 1, abs=1e-6)
        # earnings date far outside the price history → ignored
        far = compute_short_term_frame(df, [date(2020, 1, 1)])
        assert far["st_earnings_reaction"].isna().all()


class TestPointInTime:
    def test_no_lookahead(self):
        p = _prices(320)
        e = [p["trade_date"].iloc[150], p["trade_date"].iloc[250]]
        full = compute_short_term_frame(p, e)
        cut = 200
        part = compute_short_term_frame(p.iloc[:cut], e)
        pd.testing.assert_frame_equal(
            full.iloc[:cut].reset_index(drop=True), part.reset_index(drop=True),
            check_exact=False, atol=1e-9,
        )

    def test_daily_path_matches_backfill(self):
        p = _prices(400)
        e = [p["trade_date"].iloc[370]]
        full = compute_short_term_frame(p, e)
        for idx in (300, 371, 399):
            d = p["trade_date"].iloc[idx]
            window = p.iloc[max(0, idx + 1 - LOOKBACK_SESSIONS) : idx + 1]
            got = short_term_features_at(window.iloc[::-1], [x for x in e if x < d], d)
            for col in SHORT_TERM_COLUMNS:
                want = full.iloc[idx][col]
                if pd.isna(want):
                    assert got[col] is None, col
                else:
                    assert got[col] == pytest.approx(want, abs=2e-6), col

    def test_missing_session_returns_empty(self):
        p = _prices(30)
        d_missing = p["trade_date"].iloc[-1] + timedelta(days=1)
        assert short_term_features_at(p, [], d_missing) == {}
