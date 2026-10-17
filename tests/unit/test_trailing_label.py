"""Tests for the trailing-stop trade label (migration 037), no DB."""

import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import Boolean, SmallInteger

import trading_signals.analysis.feature_report as fr
from trading_signals.analysis.stats import horizon_from_target
from trading_signals.analysis.trailing import MAIN_TRAILING, TrailingParams
from trading_signals.db.models.features import (
    FEATURE_COLUMNS,
    LABEL_COLUMNS,
    TARGET_COLUMNS,
    FeatureSnapshot,
)
from trading_signals.derived.target_backfill import (
    NULL_CHECK_COLS,
    TargetBackfillComputer,
    compute_trailing_labels,
)
from trading_signals.utils.market_calendar import trading_days

ROOT = Path(__file__).resolve().parents[2]

# Mon 2026-06-29 .. Tue 2026-07-07 (Fri 2026-07-03 = holiday)
SESSIONS = [date(2026, 6, 29), date(2026, 6, 30), date(2026, 7, 1),
            date(2026, 7, 2), date(2026, 7, 6), date(2026, 7, 7)]
P3 = TrailingParams(atr_mult=3.0, atr_period=14, max_days=3, cost=0.0)
PX_COLS = ["ticker", "trade_date", "open", "high", "low", "close", "is_extrapolated"]
FLAT = (100, 100.5, 99.5, 100)


def _px(rows, ticker="AAA"):
    """rows: (session index, o, h, l, c[, extrapolated])."""
    out = []
    for r in rows:
        i, o, h, lo, c = r[:5]
        ext = r[5] if len(r) > 5 else False
        out.append((ticker, SESSIONS[i], o, h, lo, c, ext))
    return pd.DataFrame(out, columns=PX_COLS)


def _flat(n=6, ticker="AAA"):
    return _px([(i, *FLAT) for i in range(n)], ticker)


def _snaps(dates, ticker="AAA"):
    return pd.DataFrame({"ticker": [ticker] * len(dates), "snapshot_date": dates})


def _atr(values, ticker="AAA"):
    """values: {session index: atr}."""
    return pd.DataFrame(
        [(ticker, SESSIONS[i], v) for i, v in values.items()],
        columns=["ticker", "trade_date", "atr_14"],
    )


def _trail_path(px):
    """Entry 100 (session 1), HWM 106 -> stop 103 on session 2, exit at 103."""
    px.loc[1, ["high", "close"]] = [106, 105]
    px.loc[2, ["open", "high", "low", "close"]] = [104, 104.5, 102, 103]
    return px


def _label(snaps, px, atr, params=P3):
    return compute_trailing_labels(snaps, px, SESSIONS, atr, params)


# ── compute_trailing_labels ──────────────────────────────────────────


def test_trailing_stop_on_second_window_day():
    out = _label(_snaps([SESSIONS[0]]), _trail_path(_flat()), _atr({0: 1.0}))
    assert out.loc[0, "trail_stopped"] is True
    assert out.loc[0, "trail_exit_day"] == 2
    assert out.loc[0, "return_trail_60d"] == pytest.approx(0.03)


def test_atr_of_the_signal_date_is_used_not_the_next_days():
    px = _trail_path(_flat())
    # ATR(d) = 1 -> D = 3 -> stopped; the (future) ATR of d+1 would be D = 30
    out = _label(_snaps([SESSIONS[0]]), px, _atr({0: 1.0, 1: 10.0}))
    assert out.loc[0, "trail_stopped"] is True
    # with only the later ATR available there is no label at all (no lookahead)
    out = _label(_snaps([SESSIONS[0]]), px, _atr({1: 1.0}))
    assert out.loc[0, "trail_stopped"] is None


def test_atr_multiple_scales_the_distance():
    wide = TrailingParams(atr_mult=10.0, atr_period=14, max_days=3, cost=0.0)
    out = _label(_snaps([SESSIONS[0]]), _trail_path(_flat()), _atr({0: 1.0}), wide)
    assert out.loc[0, "trail_stopped"] is False  # D = 10: stop 96 never touched
    assert out.loc[0, "trail_exit_day"] == 3


def test_time_stop_labels_exit_day_and_close_return():
    px = _flat()
    px.loc[3, "close"] = 101  # window day 3 of d = session 0
    out = _label(_snaps([SESSIONS[0]]), px, _atr({0: 1.0}))
    assert out.loc[0, "trail_stopped"] is False
    assert out.loc[0, "trail_exit_day"] == 3
    assert out.loc[0, "return_trail_60d"] == pytest.approx(0.01)


def test_cost_is_applied():
    p = TrailingParams(atr_mult=3.0, atr_period=14, max_days=3, cost=0.0005)
    out = _label(_snaps([SESSIONS[0]]), _trail_path(_flat()), _atr({0: 1.0}), p)
    assert out.loc[0, "return_trail_60d"] == pytest.approx(0.03 - 0.0005)


def test_decimal_inputs_as_returned_by_the_database():
    px = _trail_path(_flat())
    for c in ("open", "high", "low", "close"):
        px[c] = px[c].map(lambda v: Decimal(str(v)))
    atr = _atr({0: Decimal("1.0000")})
    out = _label(_snaps([SESSIONS[0]]), px, atr)
    assert out.loc[0, "return_trail_60d"] == pytest.approx(0.03)


@pytest.mark.parametrize("bad", [None, float("nan"), 0.0, -1.0])
def test_missing_or_unusable_atr_is_null(bad):
    atr = pd.DataFrame(
        {"ticker": ["AAA"], "trade_date": [SESSIONS[0]], "atr_14": [bad]}
    )
    out = _label(_snaps([SESSIONS[0]]), _flat(), atr)
    assert np.isnan(out.loc[0, "return_trail_60d"])
    assert out.loc[0, "trail_exit_day"] is None and out.loc[0, "trail_stopped"] is None


def test_ticker_without_any_atr_row_is_null():
    out = _label(_snaps([SESSIONS[0]]), _flat(), _atr({0: 1.0}, ticker="ZZZ"))
    assert out.loc[0, "trail_stopped"] is None
    out = _label(_snaps([SESSIONS[0]]), _flat(), _atr({}))
    assert out.loc[0, "trail_stopped"] is None


def test_window_beyond_data_is_null():
    out = _label(_snaps([SESSIONS[3]]), _flat(), _atr({3: 1.0}))
    assert np.isnan(out.loc[0, "return_trail_60d"])
    assert out.loc[0, "trail_stopped"] is None


def test_stopped_trade_is_not_labelled_before_the_window_is_complete():
    """Same rule as the barrier label: the whole window must exist.

    d = session 1 -> window = sessions 2..4. The stop is hit on session 2, but
    session 4 has no price row yet -> NULL (not an early label).
    """
    px = _flat().drop(index=4)
    px.loc[2, ["open", "high", "low", "close"]] = [100, 100.5, 96, 98]
    out = _label(_snaps([SESSIONS[1]]), px.reset_index(drop=True), _atr({1: 1.0}))
    assert out.loc[0, "trail_stopped"] is None
    complete = _label(_snaps([SESSIONS[1]]), _flat().assign(
        low=[99.5, 99.5, 96, 99.5, 99.5, 99.5]), _atr({1: 1.0}))
    assert complete.loc[0, "trail_stopped"] is True  # full window -> labelled
    assert complete.loc[0, "trail_exit_day"] == 1
    assert complete.loc[0, "return_trail_60d"] == pytest.approx(97 / 100 - 1)


def test_missing_extrapolated_or_non_positive_bar_in_window_is_null():
    atr = _atr({0: 1.0})
    out = _label(_snaps([SESSIONS[0]]), _flat().drop(index=2), atr)
    assert out.loc[0, "trail_stopped"] is None
    px = _flat()
    px.loc[2, "is_extrapolated"] = True
    assert _label(_snaps([SESSIONS[0]]), px, atr).loc[0, "trail_stopped"] is None
    px = _flat()
    px.loc[2, "low"] = 0.0
    assert _label(_snaps([SESSIONS[0]]), px, atr).loc[0, "trail_stopped"] is None


def test_null_extrapolated_flag_counts_as_real():
    px = _flat()
    px["is_extrapolated"] = None
    out = _label(_snaps([SESSIONS[0]]), px, _atr({0: 1.0}))
    assert out.loc[0, "trail_stopped"] is False


def test_non_session_snapshot_is_null():
    out = _label(_snaps([date(2026, 7, 4)]), _flat(), _atr({0: 1.0}))
    assert out.loc[0, "trail_stopped"] is None


def test_tickers_are_independent():
    a = _flat(ticker="AAA")
    b = _flat(ticker="BBB")
    b.loc[1, "low"] = 96.0  # stop on entry day for BBB (D = 3 -> 97)
    snaps = pd.concat([_snaps([SESSIONS[0]], "AAA"), _snaps([SESSIONS[0]], "BBB")])
    atr = pd.concat([_atr({0: 1.0}, "AAA"), _atr({0: 1.0}, "BBB")])
    out = _label(snaps, pd.concat([a, b]), atr)
    assert list(out["trail_stopped"]) == [False, True]
    assert list(out["trail_exit_day"]) == [3, 1]


def test_only_the_stored_atr14_is_supported():
    other = TrailingParams(atr_mult=3.0, atr_period=20, max_days=3)
    with pytest.raises(ValueError):
        _label(_snaps([SESSIONS[0]]), _flat(), _atr({0: 1.0}), other)


def test_empty_inputs():
    assert _label(_snaps([]), _flat(), _atr({0: 1.0})).empty
    out = _label(_snaps([SESSIONS[0]]), _flat().iloc[0:0], _atr({0: 1.0}))
    assert out.loc[0, "trail_stopped"] is None


# ── TargetBackfillComputer integration (mock session) ────────────────


def _result(rows):
    r = MagicMock()
    r.all.return_value = rows
    return r


def _fake_db_rows():
    """One snapshot, 60 real bars after it, a trailing exit on window day 2."""
    cal = trading_days(date(2026, 3, 2), date(2026, 9, 30))
    d0, window = cal[0], cal[1:61]
    bars = [(100, 101, 99, 100)] * 60
    bars[0] = (100, 108, 99.5, 107)  # HWM 108
    bars[1] = (107, 107, 100, 101)  # stop = 108 - 6 = 102 -> exit at 102
    price_rows = [("AAA", d, *bar, False) for d, bar in zip(window, bars)]
    return d0, [("AAA", d0)], price_rows, [("AAA", d0, Decimal("2.0000"))]


def test_recompute_writes_trailing_label_next_to_the_other_targets():
    d0, snaps, prices, atr = _fake_db_rows()
    session = MagicMock()
    session.execute.side_effect = [
        _result(snaps), _result(prices), _result(atr), MagicMock(),
    ]
    filled = TargetBackfillComputer(session).recompute_tickers(["AAA"])
    update_params = session.execute.call_args_list[3][0][1]
    assert len(update_params) == 1
    row = update_params[0]
    assert row["snapshot_date"] == d0 and row["ticker"] == "AAA"
    assert row["return_trail_60d"] == pytest.approx(0.02 - 0.0005)
    assert row["trail_exit_day"] == 2 and row["trail_stopped"] is True
    assert row["return_barrier_14d"] is not None  # barrier written in the same UPDATE
    assert row["return_60d"] == pytest.approx(0.0)
    assert filled == 6  # 4 horizons + barrier + trailing label


def test_missing_atr_rows_leave_only_the_trailing_label_null():
    d0, snaps, prices, _ = _fake_db_rows()
    session = MagicMock()
    session.execute.side_effect = [
        _result(snaps), _result(prices), _result([]), MagicMock(),
    ]
    filled = TargetBackfillComputer(session).recompute_tickers(["AAA"])
    row = session.execute.call_args_list[3][0][1][0]
    assert row["return_trail_60d"] is None
    assert row["trail_exit_day"] is None and row["trail_stopped"] is None
    assert row["return_60d"] is not None and row["return_barrier_14d"] is not None
    assert filled == 5


def test_atr_query_is_restricted_to_the_chunk_and_the_snapshot_range():
    d0, snaps, prices, atr = _fake_db_rows()
    session = MagicMock()
    session.execute.side_effect = [
        _result(snaps), _result(prices), _result(atr), MagicMock(),
    ]
    TargetBackfillComputer(session).recompute_tickers(["AAA"])
    sql = str(session.execute.call_args_list[2][0][0])
    assert "technical_indicators" in sql and "atr_14" in sql
    assert "ticker IN" in sql and "trade_date >=" in sql and "trade_date <=" in sql


def test_backfill_all_reprocesses_rows_with_only_the_trailing_label_missing():
    assert "return_trail_60d" in NULL_CHECK_COLS
    d0, snaps, prices, atr = _fake_db_rows()
    session = MagicMock()
    session.execute.side_effect = [
        _result([("AAA",)]), _result(snaps), _result(prices), _result(atr),
        MagicMock(),
    ]
    TargetBackfillComputer(session).backfill_all()
    assert "return_trail_60d IS NULL" in str(session.execute.call_args_list[0][0][0])
    assert "return_trail_60d IS NULL" in str(session.execute.call_args_list[1][0][0])


def test_bulk_update_writes_trailing_columns_and_nulls():
    session = MagicMock()
    result = pd.DataFrame({
        "ticker": ["AAA", "BBB"], "snapshot_date": [SESSIONS[0]] * 2,
        "return_1d": [float("nan")] * 2, "return_5d": [float("nan")] * 2,
        "return_20d": [float("nan")] * 2, "return_60d": [float("nan")] * 2,
        "return_trail_60d": [0.0295, float("nan")],
        "trail_exit_day": [37, None], "trail_stopped": [True, None],
    })
    filled = TargetBackfillComputer(session)._bulk_update(result)
    assert filled == 1
    params = session.execute.call_args[0][1]
    assert params[0]["return_trail_60d"] == pytest.approx(0.0295)
    assert params[0]["trail_exit_day"] == 37 and params[0]["trail_stopped"] is True
    assert params[1]["return_trail_60d"] is None
    assert params[1]["trail_exit_day"] is None and params[1]["trail_stopped"] is None
    assert "return_barrier_14d" not in params[0]


# ── model columns / migration ────────────────────────────────────────


def test_trailing_columns_are_targets_and_labels_never_features():
    assert "return_trail_60d" in TARGET_COLUMNS
    assert {"trail_exit_day", "trail_stopped"} <= set(LABEL_COLUMNS)
    for col in ("return_trail_60d", "trail_exit_day", "trail_stopped"):
        assert col not in FEATURE_COLUMNS
    cols = FeatureSnapshot.__table__.columns
    num = cols["return_trail_60d"].type
    assert (num.precision, num.scale) == (10, 6)
    assert isinstance(cols["trail_exit_day"].type, SmallInteger)
    assert isinstance(cols["trail_stopped"].type, Boolean)


def _load_migration():
    path = ROOT / "src/alembic/versions/037_trailing_label.py"
    spec = importlib.util.spec_from_file_location("mig037", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_037_is_idempotent_and_mirrors_the_orm():
    mig = _load_migration()
    assert mig.revision == "037" and mig.down_revision == "036"
    executed: list[str] = []
    op = MagicMock()
    op.execute.side_effect = lambda sql: executed.append(str(sql))
    with patch.object(mig, "op", op):
        mig.upgrade()
    assert executed == [
        "ALTER TABLE signals.feature_snapshots ADD COLUMN IF NOT EXISTS "
        "return_trail_60d NUMERIC(10, 6)",
        "ALTER TABLE signals.feature_snapshots ADD COLUMN IF NOT EXISTS "
        "trail_exit_day SMALLINT",
        "ALTER TABLE signals.feature_snapshots ADD COLUMN IF NOT EXISTS "
        "trail_stopped BOOLEAN",
    ]
    executed.clear()
    with patch.object(mig, "op", op):
        mig.downgrade()
    assert [s.split(" EXISTS ")[1] for s in executed] == [
        "trail_stopped", "trail_exit_day", "return_trail_60d",
    ]
    assert all("DROP COLUMN IF EXISTS" in s for s in executed)


# ── feature_report: model target fallback chain ──────────────────────


def _frame(trail, barrier=None, n20=None):
    n = len(trail)
    df = pd.DataFrame({fr.FALLBACK_TARGET: n20 if n20 is not None else [0.1] * n})
    df[fr.MODEL_TARGET] = trail
    if barrier is not None:
        df[fr.BARRIER_TARGET] = barrier
    return df


def test_model_target_constants():
    assert fr.MODEL_TARGET == "return_trail_60d" and fr.MODEL_HORIZON == 60
    assert fr.BARRIER_TARGET == "return_barrier_14d" and fr.BARRIER_HORIZON == 14
    assert fr.FALLBACK_TARGET == "return_20d" and fr.FALLBACK_HORIZON == 20
    for target, horizon in fr.MODEL_TARGET_CHAIN:
        assert target in TARGET_COLUMNS
        assert horizon == horizon_from_target(target)  # NW lag / purge consistent
    assert fr.MODEL_TARGET_CHAIN[-1][0] == fr.FALLBACK_TARGET
    assert MAIN_TRAILING.max_days == fr.MODEL_HORIZON


def test_trail_wins_when_covered():
    nan = float("nan")
    df = _frame(trail=[0.1, 0.2, nan, 0.1], barrier=[0.1, 0.2, 0.3, 0.4])
    assert fr.resolve_model_target(df) == ("return_trail_60d", 60)  # 75 % coverage


def test_falls_back_to_barrier_when_trail_coverage_is_low():
    nan = float("nan")
    df = _frame(trail=[nan, nan, nan, 0.1], barrier=[0.1, 0.2, 0.3, 0.4])
    assert fr.resolve_model_target(df) == ("return_barrier_14d", 14)  # 25 % trail
    df = _frame(trail=[nan] * 4, barrier=[0.1, 0.2, nan, 0.4])
    assert fr.resolve_model_target(df) == ("return_barrier_14d", 14)  # empty trail


def test_falls_back_to_return_20d_when_both_labels_are_sparse():
    nan = float("nan")
    df = _frame(trail=[nan, nan, nan, 0.1], barrier=[nan, nan, nan, 0.1])
    assert fr.resolve_model_target(df) == ("return_20d", 20)
    df = _frame(trail=[nan] * 4)  # no barrier column at all
    assert fr.resolve_model_target(df) == ("return_20d", 20)
    assert fr.resolve_model_target(pd.DataFrame({"x": [1]})) == ("return_20d", 20)


def test_trail_column_absent_uses_barrier_chain_as_before():
    df = pd.DataFrame({
        "return_20d": [0.1] * 4, "return_barrier_14d": [0.1, 0.2, float("nan"), 0.1],
    })
    assert fr.resolve_model_target(df) == ("return_barrier_14d", 14)


def test_trail_label_is_an_excess_return_and_labels_stay_untouched():
    df = pd.DataFrame({
        "snapshot_date": [date(2026, 1, 5)] * 3 + [date(2026, 1, 6)] * 3,
        "ticker": ["A", "B", "C"] * 2,
        "return_trail_60d": [0.10, 0.00, -0.10, 0.20, 0.10, 0.00],
        "trail_exit_day": [60, 12, 5, 60, 60, 33],
        "trail_stopped": [False, True, True, False, False, True],
    })
    out = fr.prepare_frame(df)
    assert out["return_trail_60d"].round(6).tolist() == [0.1, 0.0, -0.1] * 2
    assert out["trail_exit_day"].tolist() == df["trail_exit_day"].tolist()
    assert out["trail_stopped"].tolist() == df["trail_stopped"].tolist()


def test_load_query_selects_the_trail_target_but_no_label_columns():
    table_cols = [*fr.KEY_COLUMNS, "rsi_14", "return_20d", "return_trail_60d",
                  "trail_exit_day", "trail_stopped", "barrier_outcome"]
    sql, _ = fr.build_load_query(table_cols, date(2021, 10, 1))
    s = str(sql)
    assert '"return_trail_60d"' in s
    assert "trail_exit_day" not in s and "trail_stopped" not in s
    assert "barrier_outcome" not in s


def _panel(n_dates=70, n_tickers=40, seed=1, trail_beta=0.5):
    """Panel where ``return_trail_60d`` (not return_20d) depends on the flag."""
    rng = np.random.default_rng(seed)
    frames = []
    for d in pd.bdate_range("2026-01-05", periods=n_dates):
        flag = (rng.random(n_tickers) < 0.5).astype(float)
        frames.append(pd.DataFrame({
            "snapshot_date": d.date(),
            "ticker": [f"T{i:02d}" for i in range(n_tickers)],
            "ark_multi_etf_signal": flag,
            "return_20d": rng.normal(size=n_tickers),
            "return_barrier_14d": rng.normal(size=n_tickers),
            "return_trail_60d": trail_beta * flag + rng.normal(size=n_tickers),
        }))
    return pd.concat(frames, ignore_index=True)


@pytest.fixture()
def engine(monkeypatch):
    # synthetic panels are short: disable the 10 x horizon independent-period guard
    monkeypatch.setattr(fr, "MIN_INDEPENDENT_PERIODS", 0)
    return fr.FeatureAnalysisEngine(MagicMock())


def test_engine_runs_exploratory_hypotheses_on_the_trail_target(engine):
    prepared = fr.prepare_frame(_panel())
    res = engine._test_exploratory_hypotheses(prepared)
    assert engine.model_target == "return_trail_60d" and engine.model_horizon == 60
    assert res["H1"]["verdict"] == "confirmed"
    assert res["H1"]["effect_size"] > 0.3  # planted +0.5 on the trail label only


def test_engine_horizon_follows_the_resolved_target(engine):
    nan = float("nan")
    panel = _panel()
    panel["return_trail_60d"] = nan  # label not back-filled yet
    engine._test_exploratory_hypotheses(fr.prepare_frame(panel))
    assert (engine.model_target, engine.model_horizon) == ("return_barrier_14d", 14)
    panel["return_barrier_14d"] = nan
    engine._test_exploratory_hypotheses(fr.prepare_frame(panel))
    assert (engine.model_target, engine.model_horizon) == ("return_20d", 20)


def test_engine_correlations_include_the_trail_target_with_60_day_horizon(engine):
    prepared = fr.prepare_frame(_panel())
    corr = engine._compute_correlations(prepared)
    entry = corr["ark_multi_etf_signal"]["return_trail_60d"]
    assert entry["rho"] > 0.1 and entry["n_dates"] == 70
    assert entry["t_nw"] is not None


def test_h14_block_is_unchanged_by_the_model_target_switch():
    assert fr.H14_TARGET == "return_20d" and fr.H14_HORIZON == 20
    assert fr.H14_SECONDARY_TARGET == "return_barrier_14d"
