"""Unit tests for analysis.screen_backtest and analysis.benchmark (no DB)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tests.unit.screen_data import panel
from trading_signals.analysis import screen_backtest as sb
from trading_signals.analysis.benchmark import benchmark_forward_returns


@pytest.fixture(scope="module")
def planted():
    """120 dates x 100 tickers; return_h = 0.02 * z + date shock + noise.

    120 dates hold three 40-date batches at 20 sessions (an interval exists, but
    no verdict marker: that needs 200 dates) and one 120-date batch at 60
    sessions (no interval at all).
    """
    df = panel(n_dates=120, n=100, seed=1, signal=0.02)
    return df, sb.run_screen_backtest(df)


@pytest.fixture(scope="module")
def null_panel():
    df = panel(n_dates=120, n=100, seed=2, signal=0.0)
    return sb.run_screen_backtest(df)


def _bucket(res, horizon, label):
    b = res["horizons"][horizon]["buckets"]
    return b[b["bucket"] == label].iloc[0]


def test_planted_signal_is_found_absolute_and_relative(planted):
    _, res = planted
    for h in (20, 60):
        bull = _bucket(res, h, "Bullisch (Top)")
        bear = _bucket(res, h, "Bärisch (Bottom)")
        rnd = _bucket(res, h, "Zufalls-Kontrollen")
        assert bull["n_obs"] > 0 and bull["names_per_date"] == pytest.approx(10.0)
        assert bear["names_per_date"] == pytest.approx(10.0)
        assert rnd["names_per_date"] == pytest.approx(5.0)
        # relative: excess over the same-day gated-universe mean
        assert bull["ex_uni"] > 0.02 and bear["ex_uni"] < -0.02
        assert bull["ex_uni_t"] > 3 and bear["ex_uni_t"] < -3
        if h == 20:  # 3 batches of 40 dates; at 60 sessions fewer than 2 batches
            assert bull["ex_uni_lo"] > 0 and bear["ex_uni_hi"] < 0
        else:
            assert np.isnan(bull["ex_uni_lo"]) and np.isnan(bear["ex_uni_hi"])
        # absolute: the date shock is common, so raw means order the same way
        assert bull["raw_mean"] > bull["uni_mean"] > bear["raw_mean"]
        # random controls carry no information: far weaker than the buckets
        assert abs(rnd["ex_uni"]) < 0.25 * bull["ex_uni"]
        # hit rate beats the base rate (bear hits = negative returns)
        assert bull["hit_edge"] > 0 and bear["hit_edge"] > 0
        if h == 20:
            assert bull["hit_edge_lo"] > 0 and bear["hit_edge_lo"] > 0
        assert bull["hit_rate"] > bull["base_rate"]
    spreads = res["horizons"][20]["spreads"].set_index("spread")
    s = spreads.loc["Bullisch minus Bärisch"]
    assert s["diff"] > 0.04 and s["lo"] > 0 and s["n_dates"] == 120
    assert spreads.loc["Bullisch minus Zufall", "lo"] > 0


def test_universe_row_is_neutral_by_construction(planted):
    _, res = planted
    uni = _bucket(res, 20, "Universum (Gate)")
    assert uni["ex_uni"] == pytest.approx(0.0, abs=1e-12)
    assert uni["hit_edge"] == pytest.approx(0.0, abs=1e-12)
    assert uni["names_per_date"] == pytest.approx(100.0)


def test_null_panel_gives_no_false_positive(null_panel):
    for h in (20, 60):
        spread = null_panel["horizons"][h]["spreads"].set_index("spread")
        s = spread.loc["Bullisch minus Bärisch"]
        assert abs(s["t_nw"]) < 3 and abs(s["diff"]) < 0.02
        bull = _bucket(null_panel, h, "Bullisch (Top)")
        assert abs(bull["ex_uni_t"]) < 3 and abs(bull["ex_uni"]) < 0.01


def test_spy_relative_evaluation(planted):
    df, _ = planted
    dates = pd.DatetimeIndex(sorted(df["snapshot_date"].unique()))
    spy = pd.DataFrame({"return_20d": 0.003, "return_60d": 0.01}, index=dates)
    screened = planted[1]["screened"]
    h20 = sb.evaluate_buckets(screened, 20, spy)
    assert h20["has_spy"]
    bull = h20["buckets"].set_index("bucket").loc["Bullisch (Top)"]
    # excess over SPY = raw mean - 0.003
    assert bull["ex_spy"] == pytest.approx(bull["raw_mean"] - 0.003)
    assert bull["ex_spy_lo"] < bull["ex_spy"] < bull["ex_spy_hi"]
    # the same frame with a "snapshot_date" column instead of an index works too
    again = sb.evaluate_buckets(screened, 20, spy.reset_index(names="snapshot_date"))
    assert again["has_spy"]
    # without SPY the SPY columns are absent
    assert "ex_spy" not in planted[1]["horizons"][20]["buckets"].columns


def test_rows_failing_the_gate_stay_out_of_universe_and_buckets():
    df = panel(n_dates=30, n=120, seed=3, signal=0.0)
    junk = df.groupby("snapshot_date").head(10).index
    df.loc[junk, "dollar_volume_20d"] = 1.0  # fail the liquidity gate
    df.loc[junk, ["return_20d", "return_60d"]] = 5.0  # absurd returns
    res = sb.run_screen_backtest(df)
    uni = _bucket(res, 20, "Universum (Gate)")
    assert uni["names_per_date"] == pytest.approx(110.0)
    assert abs(uni["raw_mean"]) < 0.1  # the 500 % returns are not in the universe
    screened = res["screened"]
    assert not screened.loc[~screened["gate_ok"], "bucket"].ne("").any()


def test_returns_are_only_used_where_realised():
    df = panel(n_dates=40, n=100, seed=4, signal=0.02)
    late = df["snapshot_date"] > df["snapshot_date"].unique()[25]
    df.loc[late, "return_60d"] = np.nan
    res = sb.run_screen_backtest(df)
    assert res["horizons"][20]["n_dates"] == 40
    assert res["horizons"][60]["n_dates"] == 26  # newest dates have no 60d return
    assert res["horizons"][60]["independent_periods"] == pytest.approx(26 / 60)


def test_backtest_config_turns_the_bearish_bucket_on():
    assert sb.backtest_config().bearish_enabled is True
    assert sb.backtest_config().k_bull == 10


def test_group_diagnostics_show_working_signs_on_a_planted_signal(planted):
    _, res = planted
    groups = res["horizons"][20]["groups"].set_index("group")
    assert {"trend", "value", "composite", "evidence_breadth"} <= set(groups.index)
    for name in ("trend", "value", "quality", "insider", "short_interest", "composite"):
        # the literature sign held: positive IC although some features have sign -1
        assert groups.loc[name, "mean_ic"] > 0.1, name
        assert groups.loc[name, "ic_t_nw"] > 3, name
    assert groups.loc["composite", "spread"] > 0
    assert groups.loc["composite", "spread_t"] > 3
    assert (groups["coverage"] == 1.0).all()  # complete synthetic data


def test_coverage_tables(planted):
    _, res = planted
    cov = res["coverage"]
    years = cov["years"].iloc[0]
    assert years["dates"] == 120 and years["rows"] == pytest.approx(100)
    assert years["gated"] == pytest.approx(100)
    assert years["bull"] == pytest.approx(10)
    assert years["bull_full"] == pytest.approx(1.0)
    gc = cov["group_coverage"]
    assert gc.shape[1] == 1 + 12 and (gc.drop(columns="year").iloc[0] == 1.0).all()
    assert cov["gate_reasons"].empty


def test_report_renders_german_markdown(planted):
    df, res = planted
    text = sb.render_report(res, date(2026, 10, 17))
    assert text.startswith("# Screen-Backtest")
    for needle in ("Horizont 20 Handelstage", "Horizont 60 Handelstage",
                   "Bullisch (Top)", "Bärisch (Bottom)", "Zufalls-Kontrollen",
                   "Trefferquote gegen die Basisrate", "Je Jahr",
                   "Gruppen-Diagnose", "Abdeckung", "Batch-Jackknife"):
        assert needle in text
    assert "vs. SPY" not in text  # no SPY frame given
    # 120 dates are below 10 x 20 sessions: intervals, but no verdict markers
    assert "✅" not in text and "❌" not in text
    dates = pd.DatetimeIndex(sorted(df["snapshot_date"].unique()))
    spy = pd.DataFrame({"return_20d": 0.0, "return_60d": 0.0}, index=dates)
    screened = res["screened"]
    spy_res = {**res, "horizons": {
        h: {**sb.evaluate_buckets(screened, h, spy),
            "groups": res["horizons"][h]["groups"]} for h in (20, 60)}}
    assert "Überschuss vs. SPY" in sb.render_report(spy_res, date(2026, 10, 17))


def test_groups_seen_in_the_feature_analysis_are_marked_in_sample(planted):
    _, res = planted
    text = sb.render_report(res, date(2026, 10, 17))
    assert "| insider † |" in text and "| short_interest † |" in text
    assert "| trend |" in text and "trend †" not in text
    assert "in-sample bezüglich der Feature-Analyse vom 2026-10-17" in text
    assert sb.IN_SAMPLE_GROUPS == ("insider", "short_interest")


def _synthetic_screened(n_dates: int, seed: int = 0) -> pd.DataFrame:
    """Screen-output-like frame without running the screen (fast)."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n_dates)
    n = 40
    rows = []
    for d in dates:
        bull = np.arange(n) < 10
        ret = 0.03 * bull + rng.normal(scale=0.02) + rng.normal(scale=0.04, size=n)
        rows.append(pd.DataFrame({
            "snapshot_date": d, "ticker": [f"T{i:02d}" for i in range(n)],
            "gate_ok": True, "bucket": np.where(bull, "bull", ""),
            "return_20d": ret,
        }))
    return pd.concat(rows, ignore_index=True)


def test_verdict_markers_need_ten_horizons_of_dates():
    screened = _synthetic_screened(220)
    long = sb.evaluate_buckets(screened, 20)
    bull = long["buckets"].set_index("bucket").loc["Bullisch (Top)"]
    assert bull["ex_uni_n"] == 220 and bull["ex_uni_lo"] > 0
    text = "\n".join(sb._horizon_section(20, long))
    assert "✅" in text and "Signifikanz-Marker erst ab 200 Tagen" in text
    short = sb.evaluate_buckets(
        screened[screened["snapshot_date"] <= screened["snapshot_date"].unique()[119]],
        20)
    sb_bull = short["buckets"].set_index("bucket").loc["Bullisch (Top)"]
    assert sb_bull["ex_uni_n"] == 120 and sb_bull["ex_uni_lo"] > 0  # interval exists
    short_text = "\n".join(sb._horizon_section(20, short))
    assert "✅" not in short_text and "❌" not in short_text
    assert "[+" in short_text  # the interval itself is shown


def test_empty_input_does_not_crash():
    res = sb.run_screen_backtest(panel(n_dates=2, n=30).iloc[:0])
    assert res["n_dates"] == 0 and res["horizons"] == {}
    assert sb.render_report(res, date(2026, 10, 17)).startswith("# Screen-Backtest")


# ── benchmark (SPY) forward returns ───────────────────────────────────


def test_benchmark_forward_returns_follow_the_target_definition():
    sessions = pd.bdate_range("2024-01-02", periods=6)
    opens = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    closes = [100.5, 101.5, 102.5, 103.5, 104.5, 105.5]
    px = pd.DataFrame({
        "ticker": "SPY", "trade_date": sessions, "open": opens, "close": closes,
    })
    other = px.assign(ticker="QQQ", open=1.0, close=2.0)
    out = benchmark_forward_returns(
        pd.concat([px, other], ignore_index=True), sessions, horizons=(1, 2))
    assert list(out.columns) == ["return_1d", "return_2d"]
    assert out.index.name == "snapshot_date"
    # d = day 0: entry open(day1) = 101, exit close(day 1 / day 2)
    assert out["return_1d"].iloc[0] == pytest.approx(101.5 / 101.0 - 1, abs=1e-6)
    assert out["return_2d"].iloc[0] == pytest.approx(102.5 / 101.0 - 1, abs=1e-6)
    # the newest sessions have no complete window
    assert np.isnan(out["return_1d"].iloc[-1])
    assert np.isnan(out["return_2d"].iloc[-2])


def test_benchmark_ignores_extrapolated_rows():
    sessions = pd.bdate_range("2024-01-02", periods=4)
    px = pd.DataFrame({
        "ticker": "SPY", "trade_date": sessions, "open": [10.0, 11.0, 12.0, 13.0],
        "close": [10.0, 11.0, 12.0, 13.0],
        "is_extrapolated": [False, False, True, False],
    })
    out = benchmark_forward_returns(px, sessions, horizons=(2,))
    # exit close(day 2) is an extrapolated filler -> NULL, not 12/11-1
    assert np.isnan(out["return_2d"].iloc[0])


# ── CLI wiring with mocked loaders (no DB) ────────────────────────────


def test_script_writes_the_report(tmp_path):
    import importlib.util
    import sys
    from contextlib import contextmanager
    from pathlib import Path
    from unittest.mock import MagicMock, patch

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "screen_backtest_script", root / "scripts" / "analysis" / "screen_backtest.py")
    script = importlib.util.module_from_spec(spec)
    sys.modules["screen_backtest_script"] = script
    spec.loader.exec_module(script)

    df = panel(n_dates=45, n=60, seed=9, signal=0.02)
    dates = pd.DatetimeIndex(sorted(df["snapshot_date"].unique()))
    spy = pd.DataFrame({"return_20d": 0.001, "return_60d": 0.002}, index=dates)

    @contextmanager
    def no_db():
        yield MagicMock()

    with (patch("trading_signals.db.session.get_session", no_db),
          patch.object(script, "load_features", return_value=df),
          patch("trading_signals.analysis.benchmark.load_benchmark_returns",
                return_value=spy)):
        assert script.main(["--out", str(tmp_path)]) == 0
        report = next(tmp_path.glob("screen_backtest_*.md")).read_text(
            encoding="utf-8")
        assert "Screen-Backtest" in report and "Überschuss vs. SPY" in report
        # --no-spy skips the benchmark loader
        out2 = tmp_path / "nospy"
        assert script.main(["--out", str(out2), "--no-spy"]) == 0
        assert "Überschuss vs. SPY" not in next(out2.glob("*.md")).read_text(
            encoding="utf-8")
        # no snapshots -> error exit code
        with patch.object(script, "load_features", return_value=df.iloc[:0]):
            assert script.main(["--out", str(tmp_path / "e")]) == 1


def test_script_load_features_builds_a_float_cast_whitelist_query():
    import importlib.util
    import sys
    from pathlib import Path
    from unittest.mock import MagicMock, patch

    from trading_signals.analysis.screen import DEFAULT_CONFIG, scored_features
    from trading_signals.db.models.features import FEATURE_COLUMNS

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "screen_backtest_script2", root / "scripts" / "analysis" / "screen_backtest.py")
    script = importlib.util.module_from_spec(spec)
    sys.modules["screen_backtest_script2"] = script
    spec.loader.exec_module(script)

    session = MagicMock()
    # the live table lacks one scored column -> it is skipped with a warning
    present = [c for c in FEATURE_COLUMNS if c != "sentiment_momentum"]
    session.execute.return_value.scalars.return_value.all.return_value = present
    captured = {}

    def fake_read_sql(sql, bind, params=None):
        captured["sql"], captured["params"] = str(sql), params
        cols = ["snapshot_date", "ticker", "return_20d", "return_60d", *present]
        return pd.DataFrame(columns=cols)

    with patch.object(script.pd, "read_sql", fake_read_sql):
        df = script.load_features(session, date(2022, 7, 28), date(2026, 10, 16))
    sql = captured["sql"]
    assert captured["params"] == {"start": date(2022, 7, 28), "end": date(2026, 10, 16)}
    assert "snapshot_date >= :start AND snapshot_date <= :end" in sql
    assert "return_20d::float8 AS return_20d" in sql
    assert "return_60d::float8 AS return_60d" in sql
    assert "insider_cluster_active::int::float8 AS insider_cluster_active" in sql
    assert "pe_ratio::float8 AS pe_ratio" in sql
    assert "sentiment_momentum" not in sql
    # only what the screen needs, never targets other than 20d / 60d
    assert "return_barrier_14d" not in sql and "return_1d" not in sql
    wanted = set(scored_features(DEFAULT_CONFIG.groups)) - {"sentiment_momentum"}
    assert all(f"{c}::" in sql for c in wanted)
    assert df.empty


def test_load_benchmark_returns_uses_nyse_sessions_and_the_target_rule():
    from unittest.mock import MagicMock, patch

    from trading_signals.analysis import benchmark

    days = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
                           "2024-01-08", "2024-01-09"])
    prices = pd.DataFrame({
        "ticker": "SPY", "trade_date": days.date,
        "open": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
        "close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
        "is_extrapolated": False,
    })
    session = MagicMock()
    with patch.object(benchmark.pd, "read_sql", return_value=prices) as read:
        out = benchmark.load_benchmark_returns(session, date(2024, 1, 2), horizons=(2,))
    assert "ticker = :ticker" in str(read.call_args.args[0])
    assert read.call_args.kwargs["params"] == {"ticker": "SPY",
                                               "start": date(2024, 1, 2)}
    assert list(out.index.strftime("%Y-%m-%d")) == [d.strftime("%Y-%m-%d")
                                                    for d in days]
    # d = 2024-01-02: entry open(Jan 3) = 101, exit close(Jan 4) = 102.5
    assert out["return_2d"].iloc[0] == pytest.approx(102.5 / 101.0 - 1, abs=1e-6)
    empty = MagicMock()
    with patch.object(benchmark.pd, "read_sql", return_value=prices.iloc[:0]):
        assert benchmark.load_benchmark_returns(empty, date(2024, 1, 2)).empty
