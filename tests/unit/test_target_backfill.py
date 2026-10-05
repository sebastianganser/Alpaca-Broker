"""Tests for the next-open target definition (review finding C4/H3/H7)."""

import math
from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from trading_signals.derived.target_backfill import (
    TargetBackfillComputer,
    compute_forward_returns,
)

# Mon 2026-06-29 .. Tue 2026-07-07 (Fri 2026-07-03 = Independence Day observed)
SESSIONS = [
    date(2026, 6, 29),
    date(2026, 6, 30),
    date(2026, 7, 1),
    date(2026, 7, 2),
    date(2026, 7, 6),
    date(2026, 7, 7),
]
H = [("r1", 1), ("r2", 2), ("r3", 3)]


def _prices(rows):
    return pd.DataFrame(
        rows, columns=["ticker", "trade_date", "open", "close", "is_extrapolated"]
    )


def _snaps(dates, ticker="AAA"):
    return pd.DataFrame({"ticker": [ticker] * len(dates), "snapshot_date": dates})


def _std_prices(ticker="AAA"):
    # open = 100 + i, close = 110 + i for each session
    return _prices(
        [(ticker, d, 100.0 + i, 110.0 + i, False) for i, d in enumerate(SESSIONS)]
    )


class TestComputeForwardReturns:
    def test_entry_is_next_open(self):
        out = compute_forward_returns(_snaps([SESSIONS[0]]), _std_prices(), SESSIONS, H)
        # entry = open(d+1) = 101; r1 exit = close(d+1) = 111
        assert out.loc[0, "r1"] == pytest.approx(111 / 101 - 1, abs=1e-6)
        # r2 exit = close(d+2) = 112
        assert out.loc[0, "r2"] == pytest.approx(112 / 101 - 1, abs=1e-6)

    def test_holiday_counted_on_calendar(self):
        # From 07-02: d+1 = 07-06 (skips the 07-03 holiday), d+2 = 07-07
        out = compute_forward_returns(_snaps([SESSIONS[3]]), _std_prices(), SESSIONS, H)
        assert out.loc[0, "r1"] == pytest.approx(114 / 104 - 1, abs=1e-6)
        assert out.loc[0, "r2"] == pytest.approx(115 / 104 - 1, abs=1e-6)
        assert math.isnan(out.loc[0, "r3"])  # horizon beyond data

    def test_non_session_snapshot_gets_nan(self):
        out = compute_forward_returns(
            _snaps([date(2026, 7, 3), date(2026, 7, 4)]), _std_prices(), SESSIONS, H
        )
        assert out[["r1", "r2", "r3"]].isna().all().all()

    def test_missing_price_row_does_not_shift_horizon(self):
        px = _std_prices()
        px = px[px["trade_date"] != SESSIONS[2]]  # drop 07-01 row
        out = compute_forward_returns(_snaps([SESSIONS[0]]), px, SESSIONS, H)
        assert out.loc[0, "r1"] == pytest.approx(111 / 101 - 1, abs=1e-6)
        assert math.isnan(out.loc[0, "r2"])  # exit day missing -> NaN, not shifted
        assert out.loc[0, "r3"] == pytest.approx(113 / 101 - 1, abs=1e-6)

    def test_extrapolated_exit_is_nan(self):
        px = _std_prices()
        px.loc[px["trade_date"] == SESSIONS[2], "is_extrapolated"] = True
        out = compute_forward_returns(_snaps([SESSIONS[0]]), px, SESSIONS, H)
        assert math.isnan(out.loc[0, "r2"])

    def test_extrapolated_entry_is_nan(self):
        px = _std_prices()
        px.loc[px["trade_date"] == SESSIONS[1], "is_extrapolated"] = True
        out = compute_forward_returns(_snaps([SESSIONS[0]]), px, SESSIONS, H)
        assert out[["r1", "r2", "r3"]].isna().all().all()

    def test_non_positive_price_is_nan(self):
        px = _std_prices()
        px.loc[px["trade_date"] == SESSIONS[1], "open"] = 0.0
        out = compute_forward_returns(_snaps([SESSIONS[0]]), px, SESSIONS, H)
        assert math.isnan(out.loc[0, "r1"])

    def test_null_extrapolated_flag_counts_as_real(self):
        px = _std_prices()
        px["is_extrapolated"] = None
        out = compute_forward_returns(_snaps([SESSIONS[0]]), px, SESSIONS, H)
        assert not math.isnan(out.loc[0, "r1"])

    def test_tickers_independent(self):
        px = pd.concat([_std_prices("AAA"), _std_prices("BBB")])
        px.loc[(px["ticker"] == "BBB"), "close"] *= 2
        snaps = pd.concat([_snaps([SESSIONS[0]], "AAA"), _snaps([SESSIONS[0]], "BBB")])
        out = compute_forward_returns(snaps, px, SESSIONS, H)
        assert out.loc[0, "r1"] == pytest.approx(111 / 101 - 1, abs=1e-6)
        assert out.loc[1, "r1"] == pytest.approx(222 / 101 - 1, abs=1e-6)

    def test_empty_inputs(self):
        out = compute_forward_returns(_snaps([]), _std_prices(), SESSIONS, H)
        assert out.empty
        out = compute_forward_returns(_snaps([SESSIONS[0]]), _prices([]), SESSIONS, H)
        assert out[["r1", "r2", "r3"]].isna().all().all()


class TestBulkUpdate:
    def test_writes_all_targets_including_null(self):
        session = MagicMock()
        comp = TargetBackfillComputer(session)
        result = pd.DataFrame(
            {
                "ticker": ["AAA"],
                "snapshot_date": [SESSIONS[0]],
                "return_1d": [0.01],
                "return_5d": [float("nan")],
                "return_20d": [float("nan")],
                "return_60d": [float("nan")],
            }
        )
        filled = comp._bulk_update(result)
        assert filled == 1
        params = session.execute.call_args[0][1]
        assert params == [
            {
                "snapshot_date": SESSIONS[0],
                "ticker": "AAA",
                "return_1d": 0.01,
                "return_5d": None,
                "return_20d": None,
                "return_60d": None,
            }
        ]

    def test_recompute_tickers_empty(self):
        session = MagicMock()
        assert TargetBackfillComputer(session).recompute_tickers([]) == 0
        session.commit.assert_not_called()
