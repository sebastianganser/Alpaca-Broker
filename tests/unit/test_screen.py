"""Unit tests for trading_signals.analysis.screen (synthetic frames, no DB)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
import pytest

from tests.unit.screen_data import cross_section
from trading_signals.analysis import screen as sc
from trading_signals.analysis.feature_groups import feature_group_of, is_market_wide
from trading_signals.db.models.features import FEATURE_COLUMNS

# ── a tiny, hand-checkable screen: 3 groups, 10 tickers ───────────────

TINY_GROUPS = (
    sc.GroupSpec("A", (sc.FeatureSpec("f1", 1), sc.FeatureSpec("f2", -1)),
                 ("Other",), "test"),
    sc.GroupSpec("B", (sc.FeatureSpec("f3", 1),), ("Other",), "test"),
    sc.GroupSpec("C", (sc.FeatureSpec("f4", -1),), ("Other",), "test"),
)
TINY = sc.ScreenConfig(
    groups=TINY_GROUPS, events=(), min_feature_obs=2, k_bull=2, k_bear=2,
    k_random=0, bearish_enabled=True,
)


def tiny_frame(n: int = 10) -> pd.DataFrame:
    """Every group scores ``i/(n-1)`` for ticker ``T<i>`` (see comments)."""
    i = np.arange(n, dtype=float)
    return pd.DataFrame({
        "snapshot_date": pd.Timestamp("2024-03-01"),
        "ticker": [f"T{k}" for k in range(n)],
        "f1": i,          # sign +  -> oriented rank i/(n-1)
        "f2": (n - 1) - i,  # sign - -> 1 - rank((n-1)-i) = i/(n-1)
        "f3": i,          # sign +
        "f4": -i,         # sign - -> 1 - rank(-i) = i/(n-1)
        "dollar_volume_20d": 5e7,
        "price_vs_sma50": 0.1,
    })


# ── definition ────────────────────────────────────────────────────────


def test_every_feature_is_scored_or_documented_as_unscored():
    scored, unscored = set(sc.scored_features()), set(sc.UNSCORED_FEATURES)
    # a new feature column forces a conscious decision (score it or say why not)
    undecided = [f for f in FEATURE_COLUMNS if f not in scored | unscored]
    assert undecided == []
    assert scored <= set(FEATURE_COLUMNS)
    assert unscored <= set(FEATURE_COLUMNS)
    assert not scored & unscored
    assert all(reason for reason in sc.UNSCORED_FEATURES.values())


def test_scored_features_unique_not_market_wide_and_in_matching_group():
    names = sc.scored_features()
    assert len(names) == len(set(names))  # no feature in two groups
    for group in sc.SCREEN_GROUPS:
        assert group.rationale and group.features
        for spec in group.features:
            assert spec.sign in (-1, 1)
            assert not is_market_wide(spec.name)
            assert feature_group_of(spec.name) in group.reporting_groups, spec.name
    assert sc.GROUP_NAMES == tuple(g.name for g in sc.SCREEN_GROUPS)


def test_event_rules_reference_existing_features():
    for rule in sc.EVENT_RULES:
        assert rule.feature in FEATURE_COLUMNS
        assert rule.direction in ("bull", "bear")
    # symmetric where the data allows it: both directions are present
    assert {r.direction for r in sc.EVENT_RULES} == {"bull", "bear"}


def test_screen_signs_are_frozen_literature_priors():
    """Tripwire (like the H14 constants): signs are NOT tuned on project data.

    Changing a sign, group or threshold needs a new ``SCREEN_VERSION`` and a
    new pre-registration; edit this test consciously together with it.
    """
    assert sc.SCREEN_VERSION == "screen/v1"
    signs = {g.name: tuple(s.sign for s in g.features) for g in sc.SCREEN_GROUPS}
    assert signs == {
        "trend": (1, 1, 1),
        "reversal": (-1, -1, -1, -1, -1, -1),
        "value": (1, 1, 1),  # on the inverted multiple (= yield)
        "quality": (1, -1, 1),
        "revisions": (1, 1, 1, 1, 1),
        "surprise": (1, 1, 1, 1),
        "analyst": (1, 1, -1, 1, 1, 1),
        "insider": (1, 1, 1, 1, 1),
        "institutional": (1, 1, -1),
        "short_interest": (-1, -1),
        "short_volume": (-1, -1, -1),
        "sentiment": (1, 1, 1),
    }
    transforms = {s.name: s.transform for g in sc.SCREEN_GROUPS for s in g.features
                  if s.transform != "none"}
    assert transforms == {
        "pe_ratio": "inverse", "forward_pe": "inverse", "ps_ratio": "inverse",
        "debt_to_equity": "nonneg",
    }
    loss_proxies = {s.name: s.loss_proxy for g in sc.SCREEN_GROUPS for s in g.features
                    if s.loss_proxy}
    assert loss_proxies == {"pe_ratio": "profit_margin"}
    cfg = sc.DEFAULT_CONFIG
    assert cfg.min_feature_fraction == pytest.approx(2 / 3)
    assert (cfg.k_bull, cfg.k_bear, cfg.k_random) == (10, 10, 5)
    assert cfg.bearish_enabled is False  # decision 2026-10-17: initially off
    assert (cfg.bull_percentile, cfg.bear_percentile) == (0.80, 0.20)
    assert cfg.min_group_fraction == 0.5 and cfg.min_price_sessions == 60
    assert cfg.min_groups_for_breadth == 3


def test_definition_rows_flat_table():
    rows = sc.definition_rows()
    assert len(rows) == len(sc.scored_features())
    assert {"group", "feature", "sign", "transform"} == set(rows[0])


# ── building blocks ───────────────────────────────────────────────────


def test_apply_transform():
    x = pd.Series([2.0, -4.0, 0.0, np.nan, 5.0])
    inv = sc.apply_transform(x, "inverse")
    assert inv.iloc[0] == pytest.approx(0.5) and inv.iloc[1] == pytest.approx(-0.25)
    assert np.isnan(inv.iloc[2]) and np.isnan(inv.iloc[3])  # zero -> missing
    nn = sc.apply_transform(x, "nonneg")
    assert np.isnan(nn.iloc[1]) and nn.iloc[0] == 2.0 and nn.iloc[2] == 0.0
    assert sc.apply_transform(x, "none").equals(x)
    with pytest.raises(ValueError):
        sc.apply_transform(x, "log")


def test_pct_rank_average_ties_and_minimum_observations():
    s = pd.Series([1.0, 2.0, 2.0, 4.0, np.nan])
    r = sc.pct_rank(s, min_obs=2)
    # ranks 1, 2.5, 2.5, 4 -> (r-1)/(n-1)
    assert r.iloc[:4].tolist() == pytest.approx([0.0, 0.5, 0.5, 1.0])
    assert np.isnan(r.iloc[4])
    assert sc.pct_rank(s, min_obs=5).isna().all()  # too few observations


# ── gate ──────────────────────────────────────────────────────────────


def _gate(df, config=TINY):
    res = sc.screen_cross_section(df, config=config)
    return res.set_index("ticker")


def test_gate_passes_clean_rows():
    res = _gate(tiny_frame())
    assert res["gate_ok"].all() and (res["gate_reasons"] == "").all()


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda d: d.__setitem__("dollar_volume_20d", 4_999_999.0), "low_liquidity"),
        (lambda d: d.__setitem__("dollar_volume_20d", np.nan), "low_liquidity"),
        (lambda d: d.__setitem__("price_vs_sma50", np.nan), "short_history"),
        (lambda d: d.__setitem__("in_universe", False), "not_in_universe"),
    ],
)
def test_gate_reasons(mutate, reason):
    df = tiny_frame()
    row = df.index[3]
    col = df.copy()
    mutate(_Cell(col, row))
    res = _gate(col)
    assert not res.loc["T3", "gate_ok"] and reason in res.loc["T3", "gate_reasons"]
    assert res.drop(index="T3")["gate_ok"].all()


class _Cell:
    """Assign a value to one row of a column (keeps the other rows)."""

    def __init__(self, df: pd.DataFrame, row: int):
        self.df, self.row = df, row

    def __setitem__(self, col: str, value) -> None:
        if col not in self.df.columns:
            self.df[col] = True
        self.df.loc[self.row, col] = value


def test_gate_price_history_column_beats_proxy():
    df = tiny_frame()
    df["price_history_sessions"] = 250
    df.loc[2, "price_history_sessions"] = 59  # one session short of 60
    df.loc[4, "price_history_sessions"] = 60
    df.loc[5, "price_vs_sma50"] = np.nan  # proxy would fail, column says fine
    res = _gate(df)
    assert not res.loc["T2", "gate_ok"]
    assert "short_history" in res.loc["T2", "gate_reasons"]
    assert res.loc["T4", "gate_ok"] and res.loc["T5", "gate_ok"]


def test_gate_requires_half_of_the_groups_with_data():
    df = tiny_frame()
    # T0 only has group C (1 of 3 groups < ceil(0.5 * 3) = 2)
    df.loc[0, ["f1", "f2", "f3"]] = np.nan
    # T1 has groups B and C (2 of 3) -> still passes
    df.loc[1, ["f1", "f2"]] = np.nan
    res = _gate(df)
    assert not res.loc["T0", "gate_ok"]
    assert "few_groups" in res.loc["T0", "gate_reasons"]
    assert res.loc["T1", "gate_ok"] and res.loc["T1", "n_groups_data"] == 2


def test_gate_failures_do_not_influence_the_percentiles_of_others():
    base = tiny_frame()
    ref = sc.screen_cross_section(base, config=TINY).set_index("ticker")
    extra = pd.DataFrame({
        "snapshot_date": base["snapshot_date"].iloc[0],
        "ticker": ["JUNK1", "JUNK2"],
        "f1": [1e9, -1e9], "f2": [-1e9, 1e9], "f3": [1e9, -1e9], "f4": [-1e9, 1e9],
        "dollar_volume_20d": 10.0,  # fails the liquidity gate
        "price_vs_sma50": 0.1,
    })
    res = sc.screen_cross_section(pd.concat([base, extra], ignore_index=True),
                                  config=TINY).set_index("ticker")
    assert not res.loc[["JUNK1", "JUNK2"], "gate_ok"].any()
    assert res.loc[["JUNK1", "JUNK2"], "evidence_breadth"].isna().all()
    cols = ["gs_A", "gp_A", "composite", "evidence_breadth"]
    pd.testing.assert_frame_equal(res.loc[ref.index, cols], ref[cols])
    assert (res.loc[["JUNK1", "JUNK2"], "bucket"] == "").all()


# ── orientation, group scores, breadth ────────────────────────────────


def test_sign_orientation_group_score_and_percentile():
    res = sc.screen_cross_section(tiny_frame(), config=TINY).set_index("ticker")
    expected = np.arange(10) / 9.0
    for g in "ABC":
        # group A mixes a + and a - feature, both oriented to i/9
        assert res[f"gs_{g}"].to_numpy() == pytest.approx(expected)
        assert res[f"gp_{g}"].to_numpy() == pytest.approx(expected)
    assert res["composite"].to_numpy() == pytest.approx(expected)
    assert res["composite_pct"].to_numpy() == pytest.approx(expected)


def test_single_sign_flip_changes_the_orientation():
    df = tiny_frame()
    flipped = replace(TINY, groups=(
        sc.GroupSpec("A", (sc.FeatureSpec("f1", -1), sc.FeatureSpec("f2", 1)),
                     ("Other",), "test"),
        *TINY_GROUPS[1:],
    ))
    a = sc.screen_cross_section(df, config=TINY).set_index("ticker")
    b = sc.screen_cross_section(df, config=flipped).set_index("ticker")
    assert b["gs_A"].to_numpy() == pytest.approx(1.0 - a["gs_A"].to_numpy())


def test_breadth_counts_bullish_minus_bearish_groups():
    res = sc.screen_cross_section(tiny_frame(), config=TINY).set_index("ticker")
    # percentile i/9: >= 0.8 for i in (8, 9), <= 0.2 for i in (0, 1)
    assert res["evidence_breadth"].to_dict() == {
        "T0": -3, "T1": -3, "T2": 0, "T3": 0, "T4": 0, "T5": 0, "T6": 0, "T7": 0,
        "T8": 3, "T9": 3,
    }
    assert res.loc["T9", "bull_groups"] == "A,B,C"
    assert res.loc["T9", "bear_groups"] == ""
    assert res.loc["T0", "bear_groups"] == "A,B,C"
    assert res.loc["T9", "n_bull_groups"] == 3 and res.loc["T0", "n_bear_groups"] == 3


def test_breadth_needs_three_groups_with_data():
    df = tiny_frame()
    df.loc[9, ["f1", "f2"]] = np.nan  # T9: groups B and C only
    cfg = replace(TINY, min_group_fraction=0.0)  # let the gate pass
    res = sc.screen_cross_section(df, config=cfg).set_index("ticker")
    assert res.loc["T9", "n_groups"] == 2
    assert np.isnan(res.loc["T9", "evidence_breadth"])
    assert np.isfinite(res.loc["T9", "composite"])  # composite still defined
    assert "T9" not in set(res.loc[res["bucket"] == "bull"].index)


def test_group_with_too_small_a_cross_section_has_no_score():
    cfg = replace(TINY, min_feature_obs=11)  # only 10 tickers
    res = sc.screen_cross_section(tiny_frame(), config=cfg)
    assert res["gs_A"].isna().all() and res["evidence_breadth"].isna().all()


# ── buckets ───────────────────────────────────────────────────────────


def test_bull_and_bear_buckets_and_minimum_breadth():
    res = sc.screen_cross_section(tiny_frame(), config=TINY).set_index("ticker")
    assert res.loc[["T9", "T8"], "bucket"].tolist() == ["bull", "bull"]
    assert res.loc["T9", "bucket_rank"] == 1 and res.loc["T8", "bucket_rank"] == 2
    assert res.loc[["T0", "T1"], "bucket"].tolist() == ["bear", "bear"]
    assert res.loc["T0", "bucket_rank"] == 1  # lowest composite first
    # breadth 0 never makes a bullish / bearish candidate even if K is larger
    big = replace(TINY, k_bull=6, k_bear=6)
    res = sc.screen_cross_section(tiny_frame(), config=big).set_index("ticker")
    assert (res["bucket"] == "bull").sum() == 2 and (res["bucket"] == "bear").sum() == 2


def test_bearish_bucket_is_off_by_default_but_evidence_is_computed():
    cfg = replace(TINY, bearish_enabled=False)
    res = sc.screen_cross_section(tiny_frame(), config=cfg).set_index("ticker")
    assert (res["bucket"] == "bear").sum() == 0
    assert res.loc["T0", "evidence_breadth"] == -3  # still reported
    assert sc.DEFAULT_CONFIG.bearish_enabled is False


def test_ties_in_breadth_are_broken_by_composite_then_ticker():
    df = tiny_frame()
    # identical feature rows for T8/T9 -> equal scores; ticker order decides
    df.loc[9, ["f1", "f2", "f3", "f4"]] = df.loc[8, ["f1", "f2", "f3", "f4"]].to_numpy()
    cfg = replace(TINY, k_bull=1)
    res = sc.screen_cross_section(df, config=cfg).set_index("ticker")
    assert res.loc["T8", "bucket"] == "bull" and res.loc["T9", "bucket"] == ""


def test_requested_tickers_get_a_bucket_unless_already_selected():
    cfg = replace(TINY, k_bull=1, bearish_enabled=False)
    res = sc.screen_cross_section(
        tiny_frame(), config=cfg, requested=["T5", "t9"]
    ).set_index("ticker")
    assert res.loc["T5", "bucket"] == "requested" and res.loc["T5", "is_requested"]
    # T9 is the bull pick: keeps its bucket, but is flagged as requested
    assert res.loc["T9", "bucket"] == "bull" and res.loc["T9", "is_requested"]
    assert not res.loc["T4", "is_requested"]


# ── random controls ───────────────────────────────────────────────────


def test_random_controls_are_deterministic_per_date_and_order_independent():
    tickers = [f"T{i:03d}" for i in range(100)]
    d = date(2024, 3, 1)
    a = sc.random_controls(tickers, d, 5)
    assert a == sc.random_controls(list(reversed(tickers)), d, 5)
    assert a == sc.random_controls(tickers, pd.Timestamp(d), 5)
    assert a == sorted(a) and len(set(a)) == 5  # alphabetical, no duplicates
    assert a != sc.random_controls(tickers, date(2024, 3, 4), 5)
    assert a != sc.random_controls(tickers, d, 5, seed=1)
    assert sc.random_controls(tickers, d, 0) == []
    assert sc.random_controls([], d, 5) == []
    assert len(sc.random_controls(["A", "B"], d, 5)) == 2  # pool smaller than k


def test_random_controls_inside_the_screen_use_gated_unselected_tickers():
    df = cross_section(n=150)
    df.loc[:9, "dollar_volume_20d"] = 1e5  # 10 tickers fail the gate
    res = sc.screen_cross_section(df, config=replace(sc.DEFAULT_CONFIG,
                                                     bearish_enabled=True))
    rnd = res[res["bucket"] == "random"]
    assert len(rnd) == 5
    assert rnd["gate_ok"].all()
    assert not set(rnd["ticker"]) & set(res.loc[res["bucket"].isin(
        ["bull", "bear"]), "ticker"])
    again = sc.screen_cross_section(
        df.sample(frac=1.0, random_state=3),
        config=replace(sc.DEFAULT_CONFIG, bearish_enabled=True),
    )
    assert set(again.loc[again["bucket"] == "random", "ticker"]) == set(rnd["ticker"])


# ── symmetry ──────────────────────────────────────────────────────────


def _plain_config(**kw):
    groups = tuple(
        sc.GroupSpec(f"g{i}", (sc.FeatureSpec(f"a{i}", 1), sc.FeatureSpec(f"b{i}", -1)),
                     ("Other",), "test")
        for i in range(5)
    )
    return sc.ScreenConfig(groups=groups, events=(), k_bull=8, k_bear=8, k_random=0,
                           bearish_enabled=True, min_feature_obs=2)


def test_bull_and_bear_are_mirror_images():
    cfg = _plain_config()
    rng = np.random.default_rng(5)
    n = 120
    z = rng.normal(size=n)
    data = {"snapshot_date": pd.Timestamp("2024-03-01"),
            "ticker": [f"T{i:03d}" for i in range(n)],
            "dollar_volume_20d": 5e7, "price_vs_sma50": 0.1}
    for i in range(5):
        data[f"a{i}"] = z + rng.normal(scale=1.0, size=n)
        data[f"b{i}"] = -z + rng.normal(scale=1.0, size=n)
    df = pd.DataFrame(data)
    neg = df.copy()
    for i in range(5):
        neg[f"a{i}"], neg[f"b{i}"] = -df[f"a{i}"], -df[f"b{i}"]

    a = sc.screen_cross_section(df, config=cfg).set_index("ticker")
    b = sc.screen_cross_section(neg, config=cfg).set_index("ticker")
    assert (a["bucket"] == "bull").sum() == 8 and (a["bucket"] == "bear").sum() == 8
    # negating every raw value swaps the two sides exactly
    assert set(a.index[a["bucket"] == "bull"]) == set(b.index[b["bucket"] == "bear"])
    assert set(a.index[a["bucket"] == "bear"]) == set(b.index[b["bucket"] == "bull"])
    assert (a["evidence_breadth"] == -b["evidence_breadth"]).all()
    assert a["composite"].to_numpy() == pytest.approx(1.0 - b["composite"].to_numpy())
    order_a = a[a["bucket"] == "bull"].sort_values("bucket_rank").index.tolist()
    order_b = b[b["bucket"] == "bear"].sort_values("bucket_rank").index.tolist()
    assert order_a == order_b


# ── full definition on synthetic data ─────────────────────────────────


def test_full_screen_ranks_high_latent_bullishness_first():
    df = cross_section(n=200, seed=3, noise=0.4)
    cfg = replace(sc.DEFAULT_CONFIG, bearish_enabled=True, k_random=0)
    res = sc.screen_cross_section(df, config=cfg)
    res["z"] = df["z"].to_numpy()
    bull = res[res["bucket"] == "bull"]
    bear = res[res["bucket"] == "bear"]
    assert len(bull) == 10 and len(bear) == 10
    assert bull["z"].mean() > 1.0 > -1.0 > bear["z"].mean()
    assert res["gate_ok"].all()
    assert res["n_groups"].eq(len(sc.SCREEN_GROUPS)).all()
    # the literature signs of the inverted / non-negative transforms hold
    assert res["evidence_breadth"].corr(res["z"]) > 0.9


def test_event_flags_and_counts():
    df = cross_section(n=60)
    df["insider_cluster_active"] = False
    df.loc[0, "insider_cluster_active"] = True
    df["ark_weight_delta_5d"] = np.nan
    df.loc[1, "ark_weight_delta_5d"] = 0.3
    df.loc[2, "ark_weight_delta_5d"] = -0.2
    df["analyst_net_sentiment_30d"] = 0
    df.loc[3, "analyst_net_sentiment_30d"] = 2
    df.loc[4, "analyst_net_sentiment_30d"] = -1
    df["eps_revisions_net_30d"] = 0
    df.loc[5, "eps_revisions_net_30d"] = 1
    df["form13f_new_positions_count"] = 0
    df.loc[6, "form13f_new_positions_count"] = 2
    df["form13f_exited_positions_count"] = 0
    df.loc[7, "form13f_exited_positions_count"] = 1
    df["insider_net_buy_count_30d"] = 0
    df.loc[8, "insider_net_buy_count_30d"] = -2
    res = sc.screen_cross_section(df).set_index("ticker")
    assert res.loc["T000", "ev_insider_cluster"]
    assert res["ev_insider_cluster"].sum() == 1
    assert res.loc["T001", "ev_ark_increase"] and res.loc["T002", "ev_ark_reduction"]
    assert res.loc["T003", "ev_analyst_upgrades"]
    assert res.loc["T004", "ev_analyst_downgrades"]
    assert res.loc["T005", "ev_positive_revisions"]
    assert res.loc["T006", "ev_new_13f_positions"]
    assert res.loc["T007", "ev_exited_13f_positions"]
    assert res.loc["T008", "ev_insider_net_selling"]
    assert res.loc["T000", "n_bull_events"] == 1
    assert res.loc["T008", "n_bear_events"] == 1
    assert res.loc["T009", "n_bull_events"] == 0  # NaN / 0 -> no flag


def test_missing_columns_and_nan_do_not_crash():
    df = cross_section(n=40)[["snapshot_date", "ticker", "dollar_volume_20d",
                              "price_vs_sma50", "price_vs_sma200"]]
    res = sc.screen_cross_section(df)
    assert not res["gate_ok"].any()  # fewer than half of the groups
    assert res["gate_reasons"].str.contains("few_groups").all()
    assert (res["bucket"] == "").all()


def test_cross_section_needs_one_date_and_a_ticker_column():
    df = pd.concat([cross_section("2024-03-01", n=30),
                    cross_section("2024-03-04", n=30)])
    with pytest.raises(ValueError, match="exactly one"):
        sc.screen_cross_section(df)
    sc.screen_cross_section(df, as_of=date(2024, 3, 1))  # explicit date is accepted
    with pytest.raises(ValueError, match="ticker"):
        sc.screen_cross_section(df.drop(columns="ticker"))


# ── many dates ────────────────────────────────────────────────────────


def test_screen_dates_runs_every_date_and_keeps_columns():
    df = pd.concat([cross_section(d, n=60, seed=i)
                    for i, d in enumerate(["2024-03-01", "2024-03-04", "2024-03-05"])],
                   ignore_index=True)
    df["return_20d"] = np.linspace(-0.1, 0.1, len(df))
    out = sc.screen_dates(df, keep=["return_20d", "missing_col"])
    assert len(out) == len(df)
    assert out["snapshot_date"].nunique() == 3
    assert out["return_20d"].to_numpy() == pytest.approx(df["return_20d"].to_numpy())
    assert "missing_col" not in out.columns
    # per date the random controls differ and the same date is reproducible
    random_rows = out[out["bucket"] == "random"]
    rnd = random_rows.groupby("snapshot_date")["ticker"].apply(frozenset)
    assert len(set(rnd)) == 3
    again = sc.screen_dates(df, keep=["return_20d"])
    pd.testing.assert_frame_equal(out, again)
    empty = sc.screen_dates(df.iloc[:0])
    assert empty.empty


# ── groups with partly missing features (review M4) ───────────────────

G3 = (
    sc.GroupSpec("G", (sc.FeatureSpec("a", 1), sc.FeatureSpec("b", 1),
                       sc.FeatureSpec("c", 1)), ("Other",), "test"),
    sc.GroupSpec("H", (sc.FeatureSpec("d", 1),), ("Other",), "test"),
    sc.GroupSpec("I", (sc.FeatureSpec("e", 1),), ("Other",), "test"),
)
G3_CFG = sc.ScreenConfig(groups=G3, events=(), min_feature_obs=2, k_random=0,
                         min_group_fraction=0.0)


def _g3_frame(n: int = 101) -> pd.DataFrame:
    i = np.arange(n, dtype=float)
    return pd.DataFrame({
        "snapshot_date": pd.Timestamp("2024-03-01"),
        "ticker": [f"T{k:03d}" for k in range(n)],
        "a": i, "b": i, "c": i, "d": i, "e": i,
        "dollar_volume_20d": 5e7, "price_vs_sma50": 0.1,
    })


def test_group_needs_two_thirds_of_its_features_to_have_data():
    assert sc.group_min_features(G3[0], 2 / 3) == 2   # 2 of 3
    assert sc.group_min_features(G3[1], 2 / 3) == 1   # 1 of 1
    two = sc.GroupSpec("X", (sc.FeatureSpec("p", 1), sc.FeatureSpec("q", 1)),
                       ("Other",), "test")
    five = sc.GroupSpec("Y", tuple(sc.FeatureSpec(f"f{k}", 1) for k in range(5)),
                        ("Other",), "test")
    assert sc.group_min_features(two, 2 / 3) == 2     # ceil(1.33)
    assert sc.group_min_features(five, 2 / 3) == 4    # ceil(3.33)
    df = _g3_frame()
    df.loc[10, ["b", "c"]] = np.nan   # one of three features: no group data
    df.loc[11, ["c"]] = np.nan        # two of three: group has data
    res = sc.screen_cross_section(df, config=G3_CFG).set_index("ticker")
    assert np.isnan(res.loc["T010", "gs_G"]) and np.isnan(res.loc["T010", "gp_G"])
    assert res.loc["T010", "n_groups"] == 2 and res.loc["T010", "n_groups_data"] == 2
    assert res.loc["T011", "n_groups"] == 3 and np.isfinite(res.loc["T011", "gs_G"])


def test_missing_feature_counts_as_no_evidence_not_as_the_mean_of_the_others():
    df = _g3_frame()
    df.loc[100, "c"] = np.nan   # the most bullish ticker, one feature missing
    res = sc.screen_cross_section(df, config=G3_CFG).set_index("ticker")
    full = res.loc["T099", "gs_G"]
    top_partial = res.loc["T100", "gs_G"]
    # ranks of a, b are 1.0; c is missing -> (1 + 1 + 0.5) / 3, not 1.0
    assert top_partial == pytest.approx((1.0 + 1.0 + 0.5) / 3)
    assert top_partial < res["gs_G"].max()
    # T099: a and b ranked among 101 values (99 / 100), c among 100 (99 / 99)
    assert full == pytest.approx((99 / 100 + 99 / 100 + 1.0) / 3)


def test_partial_coverage_is_not_over_represented_in_the_extremes():
    rng = np.random.default_rng(11)
    n = 600
    df = _g3_frame(n)
    for col in "abcde":
        df[col] = rng.normal(size=n)
    masked = rng.random(n) < 0.5
    df.loc[masked, "c"] = np.nan   # two of three features: still scored
    res = sc.screen_cross_section(df, config=G3_CFG)
    gs = res["gs_G"].to_numpy()
    assert np.isfinite(gs).all()
    # shrinkage towards 0.5: the partly covered group scores spread less ...
    assert gs[masked].std() < gs[~masked].std()
    # ... and take fewer of the extreme group percentiles than their share
    gp = res["gp_G"].to_numpy()
    extreme = (gp >= 0.8) | (gp <= 0.2)
    assert extreme[masked].mean() < extreme[~masked].mean()


# ── loss-makers have no P/E (review N1) ───────────────────────────────


def test_feature_values_ranks_a_loss_makers_missing_pe_as_worst():
    spec = sc.FeatureSpec("pe_ratio", 1, "inverse", "profit_margin")
    df = pd.DataFrame({"pe_ratio": [10.0, 20.0, np.nan, np.nan, np.nan],
                       "profit_margin": [0.1, 0.2, -0.1, 0.05, np.nan]})
    v = sc.feature_values(df, spec)
    assert v.iloc[0] == pytest.approx(0.1) and v.iloc[1] == pytest.approx(0.05)
    assert v.iloc[2] < v.iloc[:2].min()   # loss-maker: below every real yield
    assert np.isnan(v.iloc[3]) and np.isnan(v.iloc[4])  # profitable / unknown: NULL
    # without the proxy column nothing changes
    assert np.isnan(sc.feature_values(df.drop(columns="profit_margin"), spec)[2])
    # a missing column gives all-NaN, as before
    assert sc.feature_values(df.drop(columns="pe_ratio"), spec).isna().all()


def test_loss_maker_does_not_look_cheap_in_the_value_group():
    df = cross_section(n=100, seed=5)
    df["pe_ratio"] = np.linspace(8.0, 60.0, 100)
    df.loc[df.index[:10], "pe_ratio"] = np.nan   # 10 names without a P/E
    quiet = df.assign(profit_margin=0.1)         # profitable: the NULL stays NULL
    loss = quiet.copy()
    loss.loc[loss.index[:10], "profit_margin"] = -0.2   # loss-makers
    a = sc.screen_cross_section(quiet).set_index("ticker")
    b = sc.screen_cross_section(loss).set_index("ticker")
    first = a.index[:10]
    # no P/E and profitable: neutral (0.5) P/E part; loss-maker: worst P/E rank
    assert (b.loc[first, "gs_value"] < a.loc[first, "gs_value"] - 0.1).all()
    assert (b.loc[first, "n_groups"] == a.loc[first, "n_groups"]).all()
    # everybody else is untouched (P/E ranks are over 100 instead of 90 values,
    # which moves them only slightly)
    rest = a.index[10:]
    assert (b.loc[rest, "gs_value"] - a.loc[rest, "gs_value"]).abs().max() < 0.05


# ── requested tickers keyed by date / string / Timestamp (review N5) ───


@pytest.mark.parametrize("key", [date(2024, 3, 4), "2024-03-04",
                                 pd.Timestamp("2024-03-04")])
def test_screen_dates_accepts_requested_keys_of_any_date_type(key):
    df = pd.concat([cross_section(d, n=60, seed=i)
                    for i, d in enumerate(["2024-03-01", "2024-03-04"])],
                   ignore_index=True)
    out = sc.screen_dates(df, requested={key: ["T007"]})
    flagged = out[out["is_requested"]]
    assert flagged["snapshot_date"].dt.strftime("%Y-%m-%d").unique().tolist() == [
        "2024-03-04"]
    assert flagged["ticker"].tolist() == ["T007"]
