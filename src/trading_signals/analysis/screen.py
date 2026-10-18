"""Neutral cross-sectional screen: quality gate, evidence groups, candidates.

Plan 2026-10-17 (strategy-neutral rework, step 2), documented in
``docs/SCREEN.md``. The screen picks the tickers that get a full context-pack
file; it replaces the hand-weighted ``PRELIMINARY_WEIGHTS`` score and the ATR /
earnings risk pre-filter of the old barrier trade.

Pure functions on a cross-section of ``feature_snapshots`` rows of ONE date
(:func:`screen_cross_section`) plus a helper for many dates
(:func:`screen_dates`). No DB access, no lookahead: a row only uses the
features of its own date, which the feature pipeline already restricts to
information public by then.

Method (all thresholds are a-priori constants, see below):

1. **Quality gate** - universe flag, liquidity, price history, at least
   :data:`MIN_GROUP_FRACTION` of the evidence groups with data.
2. **Percentile ranks** of every scored feature over the gated tickers of
   the date (average ties), oriented by the a-priori sign
   (:data:`SCREEN_GROUPS`): high rank = bullish evidence.
3. **Group score** = mean of the oriented ranks of the group's features,
   where a missing feature counts as 0.5 (NULL = no evidence, so a ticker with
   fewer features is shrunk towards the middle and cannot be more extreme than
   a fully covered ticker with the same evidence); a group counts as "with
   data" only if at least :data:`MIN_FEATURE_FRACTION` (two thirds, rounded
   up) of its features are present. **Group percentile** = rank of the group
   score over the gated tickers. A group is bullish at ``>= BULL_PERCENTILE``
   and bearish at ``<= BEAR_PERCENTILE``.
4. ``evidence_breadth`` = number of bullish groups - number of bearish groups
   (needs :data:`MIN_GROUPS_FOR_BREADTH` groups with data); ``composite`` =
   mean group score.
5. **Buckets** (option D): top :data:`K_BULL` by breadth then composite,
   bottom :data:`K_BEAR` (only if bearish selection is enabled),
   :data:`K_RANDOM` random controls (deterministic per date), plus requested
   tickers.

DATA SNOOPING RULE: the signs below come from the finance literature
(``rationale`` per group), NOT from this project's IC results. They are frozen
with :data:`SCREEN_VERSION`; changing a sign, a threshold or a group after
looking at ``screen_backtest`` results needs a new version and a new
pre-registration.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from trading_signals.analysis.stats import to_float

#: Bump on ANY change of groups, signs, transforms or thresholds.
SCREEN_VERSION = "screen/v1"

# ── Candidate selection (decision 2026-10-17, option D) ──────────────

K_BULL = 10
K_BEAR = 10
#: The bearish bucket is switched off at go-live (decision 2026-10-17); the
#: screen still computes the bearish evidence.
BEARISH_ENABLED = False
K_RANDOM = 5
#: Base of the per-date seed of the random controls.
RANDOM_SEED = 20261017

# ── Quality gate (liquidity confirmed by Sebastian 2026-10-18; the price-history
# and coverage thresholds are technical defaults set on 2026-10-18) ──

#: Minimum 20-session mean daily dollar volume (USD) - CONFIRMED 2026-10-18. A
#: forecast list for a downstream bot should be tradable; USD 5 m only removes
#: micro caps / the thinnest ARK names, the S&P 500 / Nasdaq 100 core is far
#: above. Pinned by ``test_confirmed_gate_liquidity_and_breadth_constants``.
MIN_DOLLAR_VOLUME_20D = 5_000_000.0
#: Minimum real (non-extrapolated) price sessions.
MIN_PRICE_SESSIONS = 60
#: Minimum share of the evidence groups with data.
MIN_GROUP_FRACTION = 0.5

# ── Screen thresholds (a priori) ─────────────────────────────────────

BULL_PERCENTILE = 0.80
BEAR_PERCENTILE = 0.20
#: Minimum number of groups with a score for an evidence breadth.
MIN_GROUPS_FOR_BREADTH = 3
#: A bullish / bearish candidate needs net positive / negative evidence. On days
#: with fewer qualifying names the list stays shorter (no top-up to ``K_BULL``);
#: confirmed by Sebastian 2026-10-18, pinned by the same tripwire test.
MIN_BREADTH_BULL = 1
MAX_BREADTH_BEAR = -1
#: Cross-section size below which a feature has no rank on a date.
MIN_FEATURE_OBS = 20
#: Share of a group's features (rounded up) that must be present for the group
#: to have data and a score. Groups scored from a single feature of several
#: have a much wider score distribution (no averaging) and would be
#: over-represented in the extreme percentiles, i.e. in breadth and buckets.
MIN_FEATURE_FRACTION = 2.0 / 3.0
#: Score of a missing feature inside a group that has data: no evidence.
MISSING_FEATURE_RANK = 0.5

#: Input columns the screen reads besides the scored features.
COL_IN_UNIVERSE = "in_universe"
COL_DOLLAR_VOLUME = "dollar_volume_20d"
#: Optional exact count of real price sessions (the context pack can supply
#: it). ``feature_snapshots`` does not store it; without the column the proxy
#: ``price_vs_sma50`` is not NULL (>= 50 real sessions, pandas_ta SMA(50)
#: needs 50 observations) is used - slightly weaker than 60 sessions.
COL_PRICE_SESSIONS = "price_history_sessions"
COL_HISTORY_PROXY = "price_vs_sma50"

BUCKET_BULL = "bull"
BUCKET_BEAR = "bear"
BUCKET_RANDOM = "random"
#: Requested tickers that are in no other bucket.
BUCKET_REQUESTED = "requested"


# ── Definition: groups, signs, literature ────────────────────────────


@dataclass(frozen=True)
class FeatureSpec:
    """One scored feature.

    ``sign`` +1: a high value is bullish evidence; -1: a high value is bearish.
    ``transform``: ``"none"``; ``"inverse"`` = ``1/x`` for ``x != 0`` (valuation
    multiples become yields; a negative multiple gives a negative yield = worst
    rank); ``"nonneg"`` = negative values become missing (negative equity).
    ``loss_proxy``: column that marks a loss-maker when it is negative. The
    collector stores a trailing P/E only in ``0 .. 10000``, so a loss-maker
    has a NULL ``pe_ratio`` - which would drop it from the value group and
    let it look cheap on P/S alone. Where the feature is missing and the
    proxy is negative, the feature gets the worst rank of the date instead;
    without a negative proxy a missing value stays missing.
    """

    name: str
    sign: int
    transform: str = "none"
    loss_proxy: str = ""


@dataclass(frozen=True)
class GroupSpec:
    """An evidence group: features with a literature sign and their source."""

    name: str
    features: tuple[FeatureSpec, ...]
    #: ``analysis.feature_groups`` reporting groups the features belong to.
    reporting_groups: tuple[str, ...]
    rationale: str


def _f(
    name: str, sign: int, transform: str = "none", loss_proxy: str = ""
) -> FeatureSpec:
    return FeatureSpec(name, sign, transform, loss_proxy)


#: The screen definition. Signs are literature priors (author, year), never
#: derived from this project's data - see the module docstring.
SCREEN_GROUPS: tuple[GroupSpec, ...] = (
    GroupSpec(
        "trend",
        (_f("price_vs_sma50", 1), _f("price_vs_sma200", 1),
         _f("st_dist_52w_high", 1)),
        ("Technical", "Short Term"),
        "Intermediate trend / momentum persists: Jegadeesh & Titman (1993), "
        "Brock, Lakonishok & LeBaron (1992) moving-average rules, "
        "Moskowitz, Ooi & Pedersen (2012) time-series momentum; closeness to "
        "the 52-week high: George & Hwang (2004).",
    ),
    GroupSpec(
        "reversal",
        (_f("st_return_5d", -1), _f("st_rsi_2", -1),
         _f("st_bollinger_pctb", -1), _f("rsi_14", -1),
         _f("relative_strength_spy", -1),
         _f("sector_relative_return_20d", -1)),
        ("Short Term", "Technical", "Sector"),
        "Short-horizon (about one month) returns reverse: Jegadeesh (1990), "
        "Lehmann (1990), Lo & MacKinlay (1990); stronger after removing the "
        "industry move: Moskowitz & Grinblatt (1999). Documented mainly in "
        "small / illiquid stocks - expected to be weak in a large-cap "
        "universe, the prior sign is kept.",
    ),
    GroupSpec(
        "value",
        (_f("pe_ratio", 1, "inverse", "profit_margin"),
         _f("forward_pe", 1, "inverse"),
         _f("ps_ratio", 1, "inverse")),
        ("Fundamentals",),
        "Cheap stocks (high earnings / sales yield) outperform: Basu (1977), "
        "Fama & French (1992), Lakonishok, Shleifer & Vishny (1994). Ranked "
        "over the whole universe (not sector-neutral, universe.sector is not "
        "point-in-time).",
    ),
    GroupSpec(
        "quality",
        (_f("profit_margin", 1), _f("debt_to_equity", -1, "nonneg"),
         _f("margin_trend_4w", 1)),
        ("Fundamentals",),
        "Profitable, low-leverage firms outperform: Novy-Marx (2013), Fama & "
        "French (2015) profitability, Asness, Frazzini & Pedersen (2019) "
        "quality-minus-junk; improving margins: Piotroski (2000).",
    ),
    GroupSpec(
        "revisions",
        (_f("eps_revision_pct_30d", 1), _f("eps_revision_pct_90d", 1),
         _f("revenue_revision_pct_30d", 1), _f("eps_revisions_net_7d", 1),
         _f("eps_revisions_net_30d", 1)),
        ("Estimates",),
        "Upward analyst estimate revisions predict returns (earnings "
        "momentum): Chan, Jegadeesh & Lakonishok (1996), Gleason & Lee "
        "(2003).",
    ),
    GroupSpec(
        "surprise",
        (_f("sue_last", 1), _f("surprise_trend_3q", 1),
         _f("consecutive_beats", 1), _f("st_earnings_reaction", 1)),
        ("Earnings", "Short Term"),
        "Post-earnings-announcement drift: Bernard & Thomas (1989), Foster, "
        "Olsen & Shevlin (1984); streaks of beats: Loh & Warachka (2012); "
        "announcement return: Chan, Jegadeesh & Lakonishok (1996).",
    ),
    GroupSpec(
        "analyst",
        (_f("analyst_rating_score", 1), _f("analyst_upgrades_30d", 1),
         _f("analyst_downgrades_30d", -1),
         _f("analyst_net_sentiment_30d", 1),
         _f("analyst_net_sentiment_60d", 1),
         _f("analyst_upgrade_streak", 1)),
        ("Analyst",),
        "Recommendation changes carry information and drift: Womack (1996), "
        "Barber, Lehavy, McNichols & Trueman (2001), Jegadeesh, Kim, Krische "
        "& Lee (2004).",
    ),
    GroupSpec(
        "insider",
        (_f("insider_buy_ratio_30d", 1), _f("insider_buy_ratio_90d", 1),
         _f("insider_net_buy_count_30d", 1), _f("insider_buy_value_30d", 1),
         _f("cluster_score_sum_60d", 1)),
        ("Insider",),
        "Insider purchases (esp. opportunistic clusters) predict returns, "
        "sales much less: Seyhun (1986), Lakonishok & Lee (2001), Jeng, "
        "Metrick & Zeckhauser (2003), Cohen, Malloy & Pomorski (2012).",
    ),
    GroupSpec(
        "institutional",
        (_f("form13f_holder_delta_qoq", 1), _f("form13f_new_positions_count", 1),
         _f("form13f_exited_positions_count", -1)),
        ("13F",),
        "Increases in the breadth of institutional ownership predict higher "
        "returns, decreases lower: Chen, Hong & Stein (2002), Sias, Starks & "
        "Titman (2006). Only the change is scored (the level is a size proxy).",
    ),
    GroupSpec(
        "short_interest",
        (_f("short_interest_days_to_cover", -1),
         _f("short_interest_change_1m", -1)),
        ("Short Interest",),
        "Heavily / increasingly shorted stocks underperform: Desai et al. "
        "(2002), Asquith, Pathak & Ritter (2005), Hong, Li, Ni, Scheinkman & "
        "Yan (2015) for days-to-cover.",
    ),
    GroupSpec(
        "short_volume",
        (_f("short_volume_ratio_5d", -1), _f("short_volume_ratio_20d", -1),
         _f("short_volume_change_20d", -1)),
        ("Short Interest",),
        "Short-selling flow is informed: Boehmer, Jones & Zhang (2008), "
        "Diether, Lee & Werner (2009). Caveat: daily FINRA short volume also "
        "contains market-maker hedging.",
    ),
    GroupSpec(
        "sentiment",
        (_f("sentiment_avg_7d", 1), _f("sentiment_avg_30d", 1),
         _f("sentiment_momentum", 1)),
        ("Sentiment",),
        "News tone predicts returns: Tetlock (2007), Tetlock, Saar-Tsechansky "
        "& Macskassy (2008). Caveat: the effect decays within days, weak at "
        "20 / 60 sessions.",
    ),
)

GROUP_NAMES: tuple[str, ...] = tuple(g.name for g in SCREEN_GROUPS)

#: Features that are NOT scored, with the reason (literature or data). The
#: test suite requires every ``FEATURE_COLUMNS`` entry to be scored or listed
#: here, so a new feature forces a conscious decision.
UNSCORED_FEATURES: dict[str, str] = {
    "ark_in_etf_count": "sparse (only ARK-held names): event flag only",
    "ark_total_weight": "sparse (only ARK-held names): event flag only",
    "ark_weight_delta_1d": "sparse (only ARK-held names): event flag only",
    "ark_weight_delta_5d": "sparse: used for the ARK event flags",
    "ark_weight_delta_20d": "sparse (only ARK-held names): event flag only",
    "ark_conviction_score": "sparse (only ARK-held names): event flag only",
    "ark_multi_etf_signal": "sparse (only ARK-held names): event flag only",
    "ark_increase_days_10d": "sparse (only ARK-held names): event flag only",
    "ark_increase_days_20d": "sparse (only ARK-held names): event flag only",
    "ark_conviction_streak": "sparse (only ARK-held names): event flag only",
    "ark_weight_trend_20d": "sparse (only ARK-held names): event flag only",
    "insider_cluster_active": "boolean: used for the insider-cluster event",
    "insider_cluster_score": "overlaps cluster_score_sum_60d",
    "cluster_count_30d": "overlaps cluster_score_sum_60d",
    "cluster_count_60d": "overlaps cluster_score_sum_60d",
    "days_since_last_cluster": "NULL semantics (never) unclear for ranking",
    "analyst_price_target_upside": "no clear sign: optimism bias of targets",
    "politician_buy_count_60d_disclosure": "sparse, mixed evidence after 2012",
    "politician_distinct_90d_disclosure": "sparse, mixed evidence after 2012",
    "politician_buy_count_60d_transaction": "sparse, mixed evidence after 2012",
    "politician_distinct_90d_transaction": "sparse, mixed evidence after 2012",
    "form13f_top_holder_count": "level of ownership = size / index proxy",
    "revenue_growth_yoy": "no robust sign (glamour vs growth)",
    "pe_trend_4w": "no literature sign",
    "volume_ratio_20d": "ambiguous sign (volume shocks)",
    "atr_14_pct": "risk information, not direction",
    "earnings_days_until": "calendar information (shown, not scored)",
    "days_since_last_earnings": "calendar information (shown, not scored)",
    "sentiment_neg_count_7d": "count depends on news volume",
    "sentiment_article_count_7d": "news volume, ambiguous sign",
    "market_sentiment_7d": "market-wide (no cross-sectional information)",
    "news_volume_ratio_7d": "ambiguous sign (attention)",
    "dollar_volume_20d": "liquidity: quality gate only",
    "amihud_illiquidity_20d": "liquidity / size information, not direction",
    "macro_yield_spread": "market-wide",
    "macro_vix": "market-wide",
    "macro_vix_regime": "market-wide",
    "macro_hy_spread": "market-wide",
    "macro_dollar_index": "market-wide",
    "macro_inflation_expectation": "market-wide",
    "breadth_advance_decline": "market-wide",
    "breadth_pct_above_sma50": "market-wide",
    "sector_relative_momentum": "overlaps trend, sector is not point-in-time",
    "options_iv_atm_30d": "less than 3 months of history (later version)",
    "options_iv_skew_25d": "less than 3 months of history (later version)",
    "options_iv_term_slope": "less than 3 months of history (later version)",
    "options_iv_put_call_oi": "less than 3 months of history (later version)",
    "st_return_1d": "1-day reversal is microstructure (bid-ask bounce)",
    "st_gap": "no robust sign",
    "st_close_location": "no robust sign",
    "st_signed_volume_shock": "ambiguous sign (volume shocks)",
}


@dataclass(frozen=True)
class EventRule:
    """Information-only trigger flag (``ev_<name>``) with a direction."""

    name: str
    direction: str  # "bull" | "bear"
    feature: str
    op: str  # ">" | ">=" | "<" | "<=" | "true"
    threshold: float = 0.0


#: Event triggers (symmetric where the data allows it). Flags only - they do
#: not enter breadth or composite.
EVENT_RULES: tuple[EventRule, ...] = (
    EventRule("insider_cluster", "bull", "insider_cluster_active", "true"),
    EventRule("insider_net_selling", "bear", "insider_net_buy_count_30d", "<=", -1),
    EventRule("ark_increase", "bull", "ark_weight_delta_5d", ">", 0),
    EventRule("ark_reduction", "bear", "ark_weight_delta_5d", "<", 0),
    EventRule("analyst_upgrades", "bull", "analyst_net_sentiment_30d", ">=", 1),
    EventRule("analyst_downgrades", "bear", "analyst_net_sentiment_30d", "<=", -1),
    EventRule("positive_revisions", "bull", "eps_revisions_net_30d", ">=", 1),
    EventRule("negative_revisions", "bear", "eps_revisions_net_30d", "<=", -1),
    EventRule("new_13f_positions", "bull", "form13f_new_positions_count", ">=", 1),
    EventRule("exited_13f_positions", "bear", "form13f_exited_positions_count",
              ">=", 1),
)


@dataclass(frozen=True)
class ScreenConfig:
    """All tunable constants of one screen run (defaults = module constants)."""

    k_bull: int = K_BULL
    k_bear: int = K_BEAR
    bearish_enabled: bool = BEARISH_ENABLED
    k_random: int = K_RANDOM
    random_seed: int = RANDOM_SEED
    min_dollar_volume: float = MIN_DOLLAR_VOLUME_20D
    min_price_sessions: int = MIN_PRICE_SESSIONS
    min_group_fraction: float = MIN_GROUP_FRACTION
    bull_percentile: float = BULL_PERCENTILE
    bear_percentile: float = BEAR_PERCENTILE
    min_groups_for_breadth: int = MIN_GROUPS_FOR_BREADTH
    min_breadth_bull: int = MIN_BREADTH_BULL
    max_breadth_bear: int = MAX_BREADTH_BEAR
    min_feature_obs: int = MIN_FEATURE_OBS
    min_feature_fraction: float = MIN_FEATURE_FRACTION
    groups: tuple[GroupSpec, ...] = SCREEN_GROUPS
    events: tuple[EventRule, ...] = EVENT_RULES


DEFAULT_CONFIG = ScreenConfig()


def scored_features(groups: Iterable[GroupSpec] = SCREEN_GROUPS) -> list[str]:
    """All scored feature names in group order."""
    return [s.name for g in groups for s in g.features]


def definition_rows(
    groups: Iterable[GroupSpec] = SCREEN_GROUPS,
) -> list[dict[str, object]]:
    """Flat ``group / feature / sign / transform`` table (docs, report)."""
    return [
        {"group": g.name, "feature": s.name, "sign": s.sign,
         "transform": s.transform}
        for g in groups for s in g.features
    ]


# ── Building blocks ──────────────────────────────────────────────────


def apply_transform(values: pd.Series, transform: str) -> pd.Series:
    """Feature transform of :class:`FeatureSpec` (NaN stays NaN)."""
    x = to_float(values)
    if transform == "none":
        return x
    if transform == "inverse":
        return 1.0 / x.where(x != 0)
    if transform == "nonneg":
        return x.where(x >= 0)
    raise ValueError(f"unknown transform {transform!r}")


def group_min_features(group: GroupSpec, fraction: float) -> int:
    """Features a group needs for data: ``ceil(fraction * n)``, at least 1."""
    return max(1, math.ceil(fraction * len(group.features) - 1e-9))


def feature_values(frame: pd.DataFrame, spec: FeatureSpec) -> pd.Series:
    """Transformed values of one scored feature (NaN where missing / invalid).

    Applies :func:`apply_transform` and the loss-maker rule of
    :attr:`FeatureSpec.loss_proxy` (a missing value of a loss-maker gets a
    value below every other one, so all loss-makers share the worst rank).
    """
    if spec.name not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype="float64")
    x = apply_transform(frame[spec.name], spec.transform)
    if spec.loss_proxy and spec.loss_proxy in frame.columns:
        loss = to_float(frame[spec.loss_proxy]) < 0
        worst = (float(x.min()) - 1.0) if x.notna().any() else -1.0
        x = x.where(~(x.isna() & loss), worst)
    return x


def pct_rank(values: pd.Series, min_obs: int = MIN_FEATURE_OBS) -> pd.Series:
    """Percentile rank in [0, 1] (average ties, ``(rank-1)/(n-1)``).

    All-NaN if fewer than ``max(2, min_obs)`` observations. NaN is preserved.
    """
    n = int(values.count())
    if n < max(2, int(min_obs)):
        return pd.Series(np.nan, index=values.index, dtype="float64")
    return (values.rank(method="average") - 1.0) / (n - 1.0)


def random_controls(
    tickers: Iterable[str], as_of: date, k: int = K_RANDOM, seed: int = RANDOM_SEED
) -> list[str]:
    """``k`` random tickers, deterministic per ``(seed, as_of)``.

    The pool is sorted first, so the draw does not depend on row order; the
    result is returned alphabetically (no ordering information).
    """
    pool = sorted(set(tickers))
    if k <= 0 or not pool:
        return []
    day = pd.Timestamp(as_of).date()
    rng = np.random.default_rng([int(seed), day.toordinal()])
    idx = rng.choice(len(pool), size=min(int(k), len(pool)), replace=False)
    return [pool[i] for i in sorted(idx)]


def _event_flag(frame: pd.DataFrame, rule: EventRule) -> pd.Series:
    if rule.feature not in frame.columns:
        return pd.Series(False, index=frame.index)
    x = to_float(frame[rule.feature])
    if rule.op == "true":
        flag = x == 1.0
    elif rule.op == ">":
        flag = x > rule.threshold
    elif rule.op == ">=":
        flag = x >= rule.threshold
    elif rule.op == "<":
        flag = x < rule.threshold
    elif rule.op == "<=":
        flag = x <= rule.threshold
    else:
        raise ValueError(f"unknown event operator {rule.op!r}")
    return flag.fillna(False)


def _resolve_date(frame: pd.DataFrame, as_of: date | None) -> date:
    if as_of is not None:
        return pd.Timestamp(as_of).date()
    if "snapshot_date" not in frame.columns:
        raise ValueError("as_of is required when the frame has no snapshot_date")
    days = pd.to_datetime(frame["snapshot_date"]).dt.normalize().unique()
    if len(days) != 1:
        raise ValueError("a cross-section must contain exactly one snapshot_date")
    return pd.Timestamp(days[0]).date()


def quality_gate(
    frame: pd.DataFrame, group_has_data: pd.DataFrame, config: ScreenConfig
) -> tuple[pd.Series, pd.Series]:
    """``(gate_ok, gate_reasons)`` for a cross-section.

    Reasons (``;``-joined): ``not_in_universe`` (``in_universe`` flag false;
    column absent = all rows are universe members), ``low_liquidity`` (20d
    dollar volume below the minimum or unknown), ``short_history`` (fewer
    than ``min_price_sessions`` real sessions; proxy see
    :data:`COL_PRICE_SESSIONS`), ``few_groups`` (fewer than
    ``min_group_fraction`` of the groups have data).
    """
    idx = frame.index
    if COL_IN_UNIVERSE in frame.columns:
        in_universe = frame[COL_IN_UNIVERSE].fillna(False).astype(bool)
    else:
        in_universe = pd.Series(True, index=idx)
    if COL_DOLLAR_VOLUME in frame.columns:
        liquid = to_float(frame[COL_DOLLAR_VOLUME]) >= config.min_dollar_volume
    else:
        liquid = pd.Series(False, index=idx)
    if COL_PRICE_SESSIONS in frame.columns:
        history = to_float(frame[COL_PRICE_SESSIONS]) >= config.min_price_sessions
    elif COL_HISTORY_PROXY in frame.columns:
        history = to_float(frame[COL_HISTORY_PROXY]).notna()
    else:
        history = pd.Series(False, index=idx)
    min_groups = math.ceil(config.min_group_fraction * len(config.groups) - 1e-9)
    enough = group_has_data.sum(axis=1) >= min_groups

    checks = [
        ("not_in_universe", ~in_universe), ("low_liquidity", ~liquid),
        ("short_history", ~history), ("few_groups", ~enough),
    ]
    bad = np.column_stack([c.to_numpy() for _, c in checks])
    labels = [name for name, _ in checks]
    reasons = pd.Series(
        [";".join(lab for lab, b in zip(labels, row, strict=True) if b)
         for row in bad],
        index=idx,
    )
    return pd.Series(~bad.any(axis=1), index=idx), reasons


# ── Screen ───────────────────────────────────────────────────────────


def screen_cross_section(
    df: pd.DataFrame,
    as_of: date | None = None,
    config: ScreenConfig = DEFAULT_CONFIG,
    requested: Iterable[str] = (),
) -> pd.DataFrame:
    """Gate, evidence groups, breadth, composite and buckets for ONE date.

    Args:
        df: ``feature_snapshots`` rows of a single date (column ``ticker``;
            optional ``in_universe`` flag and ``price_history_sessions``).
        as_of: Date (seed of the random controls); default: the single
            ``snapshot_date`` of ``df``.
        requested: Tickers that always get a full file (bucket
            ``requested`` unless they are in another bucket).

    Returns:
        One row per input row (same order): ``ticker``, ``gate_ok``,
        ``gate_reasons``, ``n_groups_data`` / ``n_groups``, ``gs_<group>``
        (score) and ``gp_<group>`` (percentile) per group, ``bull_groups`` /
        ``bear_groups`` (comma separated), ``n_bull_groups``,
        ``n_bear_groups``, ``evidence_breadth``, ``composite``,
        ``composite_pct``, ``ev_<event>`` flags, ``n_bull_events``,
        ``n_bear_events``, ``bucket``, ``bucket_rank``, ``is_requested``.
        Scores are NaN for tickers that fail the gate.
    """
    if "ticker" not in df.columns:
        raise ValueError("df needs a 'ticker' column")
    day = _resolve_date(df, as_of)
    frame = df.reset_index(drop=True)
    groups = config.groups
    idx = frame.index

    # transformed feature values (NaN where missing / invalid)
    values: dict[str, pd.Series] = {
        s.name: feature_values(frame, s) for g in groups for s in g.features
    }
    need = {g.name: group_min_features(g, config.min_feature_fraction)
            for g in groups}
    group_has_data = pd.DataFrame(
        {
            g.name: sum(values[s.name].notna().astype(int) for s in g.features)
            >= need[g.name]
            for g in groups
        },
        index=idx,
    )
    gate_ok, reasons = quality_gate(frame, group_has_data, config)

    # oriented percentile ranks over the gated tickers, group scores / pcts
    scores: dict[str, pd.Series] = {}
    pcts: dict[str, pd.Series] = {}
    for g in groups:
        oriented = []
        for s in g.features:
            p = pct_rank(values[s.name].where(gate_ok), config.min_feature_obs)
            oriented.append(p if s.sign > 0 else 1.0 - p)
        ranks = pd.concat(oriented, axis=1)
        # a missing feature is "no evidence" (0.5); rounding makes equal sums
        # of ranks tie reliably (float noise would otherwise break ties
        # differently for mirrored inputs)
        score = ranks.fillna(MISSING_FEATURE_RANK).mean(axis=1).round(12)
        scores[g.name] = score.where(ranks.notna().sum(axis=1) >= need[g.name])
        pcts[g.name] = pct_rank(scores[g.name], config.min_feature_obs)
    gs = pd.DataFrame(scores, index=idx)
    gp = pd.DataFrame(pcts, index=idx)

    bull_mask = gp >= config.bull_percentile
    bear_mask = gp <= config.bear_percentile
    n_groups = gs.notna().sum(axis=1)
    n_bull = bull_mask.sum(axis=1)
    n_bear = bear_mask.sum(axis=1)
    breadth = (n_bull - n_bear).astype("float64")
    breadth = breadth.where(n_groups >= config.min_groups_for_breadth)
    composite = gs.mean(axis=1).round(12).where(n_groups >= 1)

    out = pd.DataFrame({"ticker": frame["ticker"].astype(str)})
    out["gate_ok"] = gate_ok
    out["gate_reasons"] = reasons
    out["n_groups_data"] = group_has_data.sum(axis=1)
    out["n_groups"] = n_groups
    for g in groups:
        out[f"gs_{g.name}"] = gs[g.name]
    for g in groups:
        out[f"gp_{g.name}"] = gp[g.name]
    names = np.array([g.name for g in groups])
    out["bull_groups"] = [",".join(names[row]) for row in bull_mask.to_numpy()]
    out["bear_groups"] = [",".join(names[row]) for row in bear_mask.to_numpy()]
    out["n_bull_groups"] = n_bull
    out["n_bear_groups"] = n_bear
    out["evidence_breadth"] = breadth
    out["composite"] = composite
    out["composite_pct"] = pct_rank(composite, config.min_feature_obs)

    bull_events = pd.Series(0, index=idx)
    bear_events = pd.Series(0, index=idx)
    for rule in config.events:
        flag = _event_flag(frame, rule)
        out[f"ev_{rule.name}"] = flag
        if rule.direction == "bull":
            bull_events = bull_events + flag.astype(int)
        else:
            bear_events = bear_events + flag.astype(int)
    out["n_bull_events"] = bull_events
    out["n_bear_events"] = bear_events

    return assign_buckets(out, day, config, requested)


def assign_buckets(
    res: pd.DataFrame,
    as_of: date,
    config: ScreenConfig = DEFAULT_CONFIG,
    requested: Iterable[str] = (),
) -> pd.DataFrame:
    """Add ``bucket`` / ``bucket_rank`` / ``is_requested`` to a screen result.

    * ``bull``: gated, breadth >= ``min_breadth_bull``, top ``k_bull`` by
      breadth then composite (descending, ties by ticker);
    * ``bear``: mirror image (breadth <= ``max_breadth_bear``, ascending),
      only if ``config.bearish_enabled``;
    * ``random``: ``k_random`` gated tickers outside both buckets
      (:func:`random_controls`);
    * ``requested``: requested tickers that are in no other bucket.
    """
    out = res.copy()
    out["bucket"] = ""
    out["bucket_rank"] = 0
    req = {str(t).upper() for t in requested}
    out["is_requested"] = out["ticker"].str.upper().isin(req)

    gated = out["gate_ok"] & out["evidence_breadth"].notna()
    cols = ["ticker", "evidence_breadth", "composite"]

    def _take(mask: pd.Series, ascending: bool, k: int) -> list[int]:
        sub = out.loc[mask, cols]
        sub = sub.sort_values(
            ["evidence_breadth", "composite", "ticker"],
            ascending=[ascending, ascending, True], kind="stable",
        )
        return list(sub.index[: max(0, int(k))])

    bull_idx = _take(gated & (out["evidence_breadth"] >= config.min_breadth_bull),
                     False, config.k_bull)
    bear_idx: list[int] = []
    if config.bearish_enabled:
        bear_idx = _take(gated & (out["evidence_breadth"] <= config.max_breadth_bear),
                         True, config.k_bear)
    for rank, i in enumerate(bull_idx, start=1):
        out.loc[i, ["bucket", "bucket_rank"]] = [BUCKET_BULL, rank]
    for rank, i in enumerate(bear_idx, start=1):
        out.loc[i, ["bucket", "bucket_rank"]] = [BUCKET_BEAR, rank]

    pool = out.loc[out["gate_ok"] & (out["bucket"] == ""), "ticker"]
    chosen = set(random_controls(pool, as_of, config.k_random, config.random_seed))
    is_random = out["ticker"].isin(chosen) & (out["bucket"] == "")
    out.loc[is_random, "bucket"] = BUCKET_RANDOM
    out.loc[is_random, "bucket_rank"] = (
        out.loc[is_random, "ticker"].rank(method="first").astype(int)
    )
    only_requested = out["is_requested"] & (out["bucket"] == "")
    out.loc[only_requested, "bucket"] = BUCKET_REQUESTED
    return out


def screen_dates(
    df: pd.DataFrame,
    config: ScreenConfig = DEFAULT_CONFIG,
    keep: Iterable[str] = (),
    requested: Mapping[object, Iterable[str]] | None = None,
) -> pd.DataFrame:
    """Run :func:`screen_cross_section` for every ``snapshot_date`` of ``df``.

    Args:
        keep: Input columns copied to the result (e.g. the forward returns).
        requested: Optional ``{date: tickers}`` of requested tickers.

    Returns:
        Concatenated results with a ``snapshot_date`` column first.
    """
    keep = [c for c in keep if c in df.columns]
    # keys may be dates, strings or Timestamps; a date never equals a Timestamp
    # (LEARNINGS 2026-04-16), so look everything up as a normalised Timestamp
    req = {pd.Timestamp(k).normalize(): v for k, v in (requested or {}).items()}
    parts = []
    for day, g in df.groupby("snapshot_date", sort=True):
        res = screen_cross_section(
            g, as_of=pd.Timestamp(day).date(), config=config,
            requested=req.get(pd.Timestamp(day).normalize(), ()),
        )
        res.insert(0, "snapshot_date", day)
        for c in keep:
            res[c] = g[c].to_numpy()
        parts.append(res)
    if not parts:
        return pd.DataFrame(columns=["snapshot_date", "ticker", *keep])
    return pd.concat(parts, ignore_index=True)


__all__ = [
    "BEARISH_ENABLED",
    "BUCKET_BEAR",
    "BUCKET_BULL",
    "BUCKET_RANDOM",
    "BUCKET_REQUESTED",
    "DEFAULT_CONFIG",
    "EVENT_RULES",
    "GROUP_NAMES",
    "SCREEN_GROUPS",
    "SCREEN_VERSION",
    "UNSCORED_FEATURES",
    "EventRule",
    "FeatureSpec",
    "GroupSpec",
    "ScreenConfig",
    "apply_transform",
    "assign_buckets",
    "definition_rows",
    "feature_values",
    "group_min_features",
    "pct_rank",
    "quality_gate",
    "random_controls",
    "scored_features",
    "screen_cross_section",
    "screen_dates",
]
