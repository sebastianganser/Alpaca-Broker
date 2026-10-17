# Candidate Screen – quality gate, evidence groups, buckets

Status: `screen/v1`, implemented 2026-10-17 and reviewed the same day (partly covered groups, loss-makers, see §2 and §4; not deployed, so no version bump) (plan [2026-10-17_Plan_Strategieneutral.md](2026-10-17_Plan_Strategieneutral.md), step 2). Code: [`analysis/screen.py`](../src/trading_signals/analysis/screen.py); historical validation: [`analysis/screen_backtest.py`](../src/trading_signals/analysis/screen_backtest.py) + `scripts/analysis/screen_backtest.py`. The context pack (step 3) will call the screen; the forecasts it produces are evaluated as described in [FORECASTS.md](FORECASTS.md).

## 1. What the screen is for

Claude cannot read ~550–600 ticker files per day. The screen decides which tickers get a **full** ticker file (option D of the plan, decision of Sebastian 2026-10-17) and gives Claude a compact table (`screen.csv`) for all the others. It is **neutral**: it does not know a trade rule, an ATR window or an earnings blackout; it only ranks how many independent kinds of evidence point in the same direction. It replaces the hand-weighted `PRELIMINARY_WEIGHTS` score and the stage-1 risk pre-filter of the barrier concept.

The screen is a **filter, not a forecast**: whether a bullish candidate is expected to rise is Claude's call (see FORECASTS.md); whether the screen itself selects better than chance is measured by the backtest (§7) and, live, by the comparison "shortlist vs random controls vs universe".

## 2. Pipeline (one snapshot date)

1. **Quality gate** (§3) – drops rows the downstream bot could not use or that lack data.
2. **Percentile ranks** of every scored feature over the gated tickers of the date (average ties, `(rank-1)/(n-1)`), oriented by the a-priori sign (§4): a high oriented rank is bullish evidence. A feature needs at least 20 observations on the date, otherwise it has no rank.
3. **Group score** = mean of the oriented ranks of the group's features, a missing feature counting as **0.5** (NULL = no evidence). A group has **data** only if at least **⅔ of its features, rounded up,** are present (3 features: 2; 2 features: 2; 5 features: 4; 1 feature: 1); otherwise it has no score. Why: a group scored from one feature of several has a much wider score distribution (no averaging) and would be over-represented in the extreme percentiles, hence in breadth and buckets; with the 0.5 fill a partly covered ticker is shrunk towards the middle and can never be more extreme than a fully covered ticker with the same evidence. **Group percentile** = rank of the group score over the gated tickers. A group is **bullish** at percentile ≥ 0.80 and **bearish** at ≤ 0.20.
4. **Evidence breadth** = number of bullish groups − number of bearish groups, defined only with ≥ 3 groups with a score. **Composite** = mean of the group scores; `composite_pct` is its percentile.
5. **Event triggers** (§5) – flags only, they do not enter breadth or composite.
6. **Buckets** (§6).

All inputs are the point-in-time `feature_snapshots` of the date itself (see the information cut-offs in [DECISIONS_FEATURES.md](DECISIONS_FEATURES.md)); the screen adds no lookahead.

## 3. Quality gate (thresholds PROPOSED – awaiting Sebastian's confirmation)

| Check | Reason code | Rule |
|---|---|---|
| Universe | `not_in_universe` | the row is an active, tradable member of the point-in-time universe (input flag `in_universe`; absent = all `feature_snapshots` rows, which already are the point-in-time universe) |
| Liquidity | `low_liquidity` | `dollar_volume_20d` (20-session mean of close × volume) ≥ **USD 5 m**; NULL fails. Rationale: a downstream bot needs positions it can enter and leave; USD 5 m only removes micro caps and the thinnest ARK names, the S&P 500 / Nasdaq 100 core is far above |
| Price history | `short_history` | ≥ **60** real (non-extrapolated) price sessions. `feature_snapshots` does not store the count: the optional input column `price_history_sessions` (the context pack can supply it) is used when present, otherwise the proxy "`price_vs_sma50` is not NULL" (SMA(50) needs 50 real sessions, so the proxy is slightly weaker than 60) |
| Data coverage | `few_groups` | at least **50 %** of the 12 evidence groups (6) have data for the ticker (a group has data when ≥ ⅔ of its features, rounded up, are present, §2) |

Rows that fail the gate get no scores, no bucket and do not influence the percentiles of the others. Gate reasons are reported per ticker (`gate_reasons`, `;`-separated) so `screen.csv` can show why a ticker is missing.

## 4. Evidence groups and a-priori signs

**Data-snooping rule.** The signs come from the finance literature, **not** from this project's IC results. They are frozen with `SCREEN_VERSION = "screen/v1"` and pinned by a tripwire test (`test_screen_signs_are_frozen_literature_priors`, same idea as the H14 constants). The backtest prints a per-group diagnosis (IC, top-minus-bottom spread) for transparency, but a sign, group or threshold is **never** changed because of it. A deliberate change needs a new `SCREEN_VERSION`, an entry in DECISIONS_FEATURES.md and a re-run of the backtest as a new baseline; results from before the change are not comparable.

Sign `+`: a high value is bullish evidence; `-`: a high value is bearish. `inverse`: the multiple is inverted to a yield first (`1/x`; `0` = missing); `nonneg`: negative values become missing (negative equity).

**Loss-makers (value group).** The collector stores a trailing P/E only in 0 … 10,000 (`yfinance_client` plausibility rule), so a loss-maker has a **NULL** `pe_ratio` – left alone it would drop out of the P/E part of the value group and look cheap on P/S alone. Rule (`FeatureSpec.loss_proxy`): where `pe_ratio` is NULL **and** `profit_margin` < 0 (the only loss indicator among the features), the P/E gets the **worst rank of the date** (all loss-makers tie there); NULL with a non-negative or unknown margin stays NULL. The forward P/E is stored with its negative values, so a negative forward P/E already is the worst yield and needs no rule.

| Group | Feature (sign) | Source / rationale |
|---|---|---|
| `trend` | `price_vs_sma50` (+), `price_vs_sma200` (+), `st_dist_52w_high` (+) | Intermediate trend / momentum persists: Jegadeesh & Titman (1993), Brock, Lakonishok & LeBaron (1992) moving-average rules, Moskowitz, Ooi & Pedersen (2012) time-series momentum; closeness to the 52-week high: George & Hwang (2004) |
| `reversal` | `st_return_5d` (-), `st_rsi_2` (-), `st_bollinger_pctb` (-), `rsi_14` (-), `relative_strength_spy` (-), `sector_relative_return_20d` (-) | Short-horizon (about one month) returns reverse: Jegadeesh (1990), Lehmann (1990), Lo & MacKinlay (1990); stronger after removing the industry move: Moskowitz & Grinblatt (1999). Documented mainly in small / illiquid stocks – expected to be weak in a large-cap universe, the prior sign is kept |
| `value` | `pe_ratio` (+, inverse), `forward_pe` (+, inverse), `ps_ratio` (+, inverse) | Cheap stocks (high earnings / sales yield) outperform: Basu (1977), Fama & French (1992), Lakonishok, Shleifer & Vishny (1994). Ranked over the whole universe (not sector-neutral: `universe.sector` is not point-in-time) |
| `quality` | `profit_margin` (+), `debt_to_equity` (-, nonneg), `margin_trend_4w` (+) | Profitable, low-leverage firms outperform: Novy-Marx (2013), Fama & French (2015) profitability, Asness, Frazzini & Pedersen (2019) quality-minus-junk; improving margins: Piotroski (2000) |
| `revisions` | `eps_revision_pct_30d` (+), `eps_revision_pct_90d` (+), `revenue_revision_pct_30d` (+), `eps_revisions_net_7d` (+), `eps_revisions_net_30d` (+) | Upward analyst estimate revisions predict returns (earnings momentum): Chan, Jegadeesh & Lakonishok (1996), Gleason & Lee (2003) |
| `surprise` | `sue_last` (+), `surprise_trend_3q` (+), `consecutive_beats` (+), `st_earnings_reaction` (+) | Post-earnings-announcement drift: Bernard & Thomas (1989), Foster, Olsen & Shevlin (1984); streaks of beats: Loh & Warachka (2012); announcement return: Chan, Jegadeesh & Lakonishok (1996) |
| `analyst` | `analyst_rating_score` (+), `analyst_upgrades_30d` (+), `analyst_downgrades_30d` (-), `analyst_net_sentiment_30d` (+), `analyst_net_sentiment_60d` (+), `analyst_upgrade_streak` (+) | Recommendation changes carry information and drift: Womack (1996), Barber, Lehavy, McNichols & Trueman (2001), Jegadeesh, Kim, Krische & Lee (2004) |
| `insider` | `insider_buy_ratio_30d` (+), `insider_buy_ratio_90d` (+), `insider_net_buy_count_30d` (+), `insider_buy_value_30d` (+), `cluster_score_sum_60d` (+) | Insider purchases (esp. opportunistic clusters) predict returns, sales much less: Seyhun (1986), Lakonishok & Lee (2001), Jeng, Metrick & Zeckhauser (2003), Cohen, Malloy & Pomorski (2012) |
| `institutional` | `form13f_holder_delta_qoq` (+), `form13f_new_positions_count` (+), `form13f_exited_positions_count` (-) | Increases in the breadth of institutional ownership predict higher returns, decreases lower: Chen, Hong & Stein (2002), Sias, Starks & Titman (2006). Only the change is scored (the level is a size proxy) |
| `short_interest` | `short_interest_days_to_cover` (-), `short_interest_change_1m` (-) | Heavily / increasingly shorted stocks underperform: Desai et al. (2002), Asquith, Pathak & Ritter (2005), Hong, Li, Ni, Scheinkman & Yan (2015) for days-to-cover |
| `short_volume` | `short_volume_ratio_5d` (-), `short_volume_ratio_20d` (-), `short_volume_change_20d` (-) | Short-selling flow is informed: Boehmer, Jones & Zhang (2008), Diether, Lee & Werner (2009). Caveat: daily FINRA short volume also contains market-maker hedging |
| `sentiment` | `sentiment_avg_7d` (+), `sentiment_avg_30d` (+), `sentiment_momentum` (+) | News tone predicts returns: Tetlock (2007), Tetlock, Saar-Tsechansky & Macskassy (2008). Caveat: the effect decays within days, weak at 20 / 60 sessions |

46 of the 97 feature columns are scored. **Every other column is listed with a reason in `UNSCORED_FEATURES`**, and a test requires each `FEATURE_COLUMNS` entry to be scored or listed – a new feature column forces a conscious decision. Reasons in short:

| Unscored | Reason |
|---|---|
| `ark_*` (11) | sparse (only ARK-held names, `NULL` elsewhere): a percentile among ~30 held names is not a universe percentile; used as an event flag |
| `politician_*` (4) | sparse, mixed evidence after the 2012 STOCK Act |
| `insider_cluster_active`, `insider_cluster_score`, `cluster_count_*`, `days_since_last_cluster` | boolean used as event flag / overlaps `cluster_score_sum_60d` / NULL semantics unclear |
| `options_iv_*` (4) | less than 3 months of history: a later screen version with pre-registered signs |
| `atr_14_pct`, `amihud_illiquidity_20d`, `dollar_volume_20d` | risk / size / liquidity information, not direction (liquidity is the gate) |
| `macro_*`, `breadth_*`, `market_sentiment_7d` | market-wide, identical for all tickers of a date |
| `volume_ratio_20d`, `st_signed_volume_shock`, `news_volume_ratio_7d`, `sentiment_article_count_7d`, `sentiment_neg_count_7d` | ambiguous sign (volume / attention) or depends on news volume |
| `st_return_1d`, `st_gap`, `st_close_location` | microstructure / no robust sign |
| `analyst_price_target_upside`, `revenue_growth_yoy`, `pe_trend_4w`, `form13f_top_holder_count`, `sector_relative_momentum` | no clear literature sign, size proxy, or overlap with `trend` |
| `earnings_days_until`, `days_since_last_earnings` | calendar information (shown in the ticker file, not scored) |

## 5. Event triggers (flags, information only)

`ev_<name>` columns plus `n_bull_events` / `n_bear_events`; symmetric where the data allows it.

| Bullish | Rule | Bearish | Rule |
|---|---|---|---|
| `insider_cluster` | `insider_cluster_active` | `insider_net_selling` | `insider_net_buy_count_30d` ≤ −1 |
| `ark_increase` | `ark_weight_delta_5d` > 0 | `ark_reduction` | `ark_weight_delta_5d` < 0 |
| `analyst_upgrades` | `analyst_net_sentiment_30d` ≥ 1 | `analyst_downgrades` | `analyst_net_sentiment_30d` ≤ −1 |
| `positive_revisions` | `eps_revisions_net_30d` ≥ 1 | `negative_revisions` | `eps_revisions_net_30d` ≤ −1 |
| `new_13f_positions` | `form13f_new_positions_count` ≥ 1 | `exited_13f_positions` | `form13f_exited_positions_count` ≥ 1 |

## 6. Buckets (option D) and configuration

| Constant | Value | Meaning |
|---|---|---|
| `K_BULL` | 10 | top bullish candidates: highest breadth, then composite (ties: ticker A→Z); needs breadth ≥ `MIN_BREADTH_BULL` = 1 |
| `K_BEAR` / `BEARISH_ENABLED` | 10 / **False** | bearish candidates (mirror image, breadth ≤ `MAX_BREADTH_BEAR` = −1); **initially switched off** (decision 2026-10-17), the bearish evidence is still computed and shown |
| `K_RANDOM` | 5 | random controls from the gated tickers outside the two buckets; deterministic per date: `numpy.default_rng([RANDOM_SEED, date.toordinal()])` over the alphabetically sorted pool (`RANDOM_SEED` = 20261017), so a rerun reproduces the same names |
| requested tickers | – | always get a full file (`bucket = requested`, or flagged `is_requested` if already in a bucket) |
| `BULL_PERCENTILE` / `BEAR_PERCENTILE` | 0.80 / 0.20 | group is bullish / bearish |
| `MIN_GROUPS_FOR_BREADTH` | 3 | groups with a score needed for a breadth |
| `MIN_FEATURE_OBS` | 20 | cross-section size needed to rank a feature |
| `MIN_FEATURE_FRACTION` | ⅔ | share of a group's features (rounded up) that must be present for the group to have data and a score |
| `MISSING_FEATURE_RANK` | 0.5 | rank of a missing feature inside a group that has data (no evidence) |

Fewer than `K_BULL` bullish names on a day is a valid outcome (breadth must be positive); the plan's rule "files alphabetical, group only in the DB" (no ranking anchor for Claude) is implemented by the context pack, not by the screen.

## 7. Validation before go-live

```
.venv/bin/python scripts/analysis/screen_backtest.py --out /app/repair_logs
```

Applies gate and screen to every snapshot date of the ML window (`ml_start_date()`, ≈ 1,060 sessions) and compares, for `return_20d` and `return_60d`, the bullish bucket, the bearish bucket (evaluated even though it is off at go-live), the random controls and the gated universe: absolute (raw) and relative (excess over the same-day gated-universe mean and over SPY), hit rates against the base rate, bull-minus-bear spread, a per-year table, a per-group diagnosis and the coverage of each group by year (young sources are missing in early years). Date-weighted; intervals are 95 % **batch-jackknife** intervals (batches of 2 × horizon dates, t critical value – the date-block bootstrap was shown to be too narrow at this number of independent periods, [FORECASTS.md](FORECASTS.md) §6), Newey-West t. Fewer than two batches give no interval; a significance marker next to an interval appears only from 10 × horizon dates with data for that figure. The group diagnosis marks **`insider` and `short_interest` with †: in-sample with respect to the 2026-10-17 feature analysis** (insider buy ratio / value and days-to-cover had already stood out there on the very data of this backtest), so a good result for them is not independent evidence. Read-only.

How to read it: the screen is only worth using if the bullish bucket beats the random controls and the universe *and* the bearish bucket does the opposite; with ~1,000 overlapping dates the 20-session result carries ≈ 50 independent periods, the 60-session result ≈ 17. A neutral result is a legitimate finding – the screen then is a readability device (it makes the pack small) and no claim about edge is made.

## 8. Known limits

- **13F filing season (checked 2026-10-17, no artefact):** `form13f_holder_delta_qoq`, `form13f_new_positions_count` and `form13f_exited_positions_count` were inspected on production data around the Q4 2025 → Q1 2026 transition. They stay constant through the quarter end and switch only around the filing deadline (2026-02-18 for Q4 2025); there are no mass negative deltas (the half-filed quarter does not show up as a wave of "exits"). The `institutional` group therefore stays in `screen/v1`.
- Percentiles are taken over the whole gated universe, not within sectors (`universe.sector` is not point-in-time). Valuation and profitability therefore partly encode sector.
- Young sources (estimates, options IV, sentiment, short volume have only months of history) have unstable percentiles and are missing in the backtest's early years; the gate's 50 % rule and the coverage table show how much of the history is really multi-group.
- Highly tied features (counts, zeros) cluster around the middle percentile; their groups can only be bullish when there is real activity.
- Several groups are correlated (e.g. `trend` and `reversal` on price, `analyst` and `revisions`). Breadth counts groups, not independent signals.
- `MIN_PRICE_SESSIONS` uses a 50-session proxy unless the caller supplies `price_history_sessions`.
