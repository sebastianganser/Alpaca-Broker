"""Tests for the next-open target definition (review finding C4/H3/H7).

Also pins the "only missing" row selection of the nightly chain step: a row is
processed while one of the four ``return_*`` columns is NULL, nothing else
(plan 2026-10-17 step 6 removed the barrier label that used to be part of it).
"""

import math
from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest
from sqlalchemy.dialects import postgresql

from trading_signals.db.models.features import TARGET_COLUMNS, FeatureSnapshot
from trading_signals.derived.target_backfill import (
    HORIZONS,
    NULL_CHECK_COLS,
    TargetBackfillComputer,
    compute_forward_returns,
    missing_target_clause,
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


# ── row selection of the nightly chain step ──────────────────────────

RETURNS = ["return_1d", "return_5d", "return_20d", "return_60d"]


def _sql(stmt) -> str:
    return " ".join(str(stmt.compile(dialect=postgresql.dialect())).split())


def _is_null(col: str) -> str:
    return f"signals.feature_snapshots.{col} IS NULL"


class TestRowSelection:
    def test_rule_is_exactly_the_four_forward_returns(self):
        assert [name for name, _ in HORIZONS] == RETURNS
        assert list(NULL_CHECK_COLS) == RETURNS == list(TARGET_COLUMNS)
        # a name the ORM does not know would crash the chain step at run time
        assert all(hasattr(FeatureSnapshot, c) for c in NULL_CHECK_COLS)
        assert _sql(missing_target_clause()).split(" OR ") == [
            _is_null(c) for c in RETURNS
        ]

    def test_backfill_all_picks_tickers_by_that_rule(self):
        session = MagicMock()
        session.execute.return_value.all.return_value = []
        assert TargetBackfillComputer(session).backfill_all() == 0
        sql = _sql(session.execute.call_args_list[0].args[0])
        assert "SELECT DISTINCT signals.feature_snapshots.ticker" in sql
        for col in RETURNS:
            assert _is_null(col) in sql

    @pytest.mark.parametrize("only_missing", [True, False])
    def test_chunk_filters_rows_only_in_only_missing_mode(self, only_missing):
        session = MagicMock()
        session.execute.return_value.all.return_value = []
        TargetBackfillComputer(session)._process_chunk(["AAA"], only_missing)
        sql = _sql(session.execute.call_args_list[0].args[0])
        for col in RETURNS:  # recompute_* overwrite everything, incl. filled rows
            assert (_is_null(col) in sql) is only_missing

    def test_recent_rows_fill_step_by_step_and_open_horizons_stay_null(self):
        """Run on 2026-07-08: the 2026-06-29 row has 1d + 5d, the 07-07 row only 1d.

        Both keep NULLs for the open horizons, so ``missing_target_clause`` selects
        them again on the next run (and fills the next horizon as soon as it has
        realised); only a row with all four returns set drops out.
        """
        snaps = [("AAA", date(2026, 6, 29)), ("AAA", date(2026, 7, 7))]
        bars = {  # Fri 2026-07-03 is a holiday
            date(2026, 6, 30): (100.0, 101.0), date(2026, 7, 1): (101.0, 102.0),
            date(2026, 7, 2): (102.0, 103.0), date(2026, 7, 6): (103.0, 104.0),
            date(2026, 7, 7): (104.0, 105.0), date(2026, 7, 8): (110.0, 121.0),
        }
        price_rows = [("AAA", d, o, c, False) for d, (o, c) in bars.items()]
        results = []
        for rows in (snaps, price_rows):
            r = MagicMock()
            r.all.return_value = rows
            results.append(r)
        session = MagicMock()
        session.execute.side_effect = [*results, MagicMock()]

        filled = TargetBackfillComputer(session)._process_chunk(["AAA"], True)

        assert filled == 3
        prices_sql = _sql(session.execute.call_args_list[1].args[0])
        assert "prices_daily.open" in prices_sql and "prices_daily.close" in prices_sql
        assert "prices_daily.high" not in prices_sql  # only open/close are needed
        params = session.execute.call_args_list[2].args[1]
        assert params == [
            {   # entry open(06-30) = 100; d+1 close 101, d+5 = 07-07 close 105
                "snapshot_date": date(2026, 6, 29), "ticker": "AAA",
                "return_1d": pytest.approx(101 / 100 - 1, abs=1e-6),
                "return_5d": pytest.approx(105 / 100 - 1, abs=1e-6),
                "return_20d": None, "return_60d": None,
            },
            {   # entry open(07-08) = 110, d+1 close 121; nothing else realised
                "snapshot_date": date(2026, 7, 7), "ticker": "AAA",
                "return_1d": pytest.approx(121 / 110 - 1, abs=1e-6),
                "return_5d": None, "return_20d": None, "return_60d": None,
            },
        ]
        # nothing but the key and the four returns is ever written
        assert all(set(p) == {"snapshot_date", "ticker", *RETURNS} for p in params)
