"""
Feature Analysis Engine.
Automated monthly feature analysis for trading signals.

Methodology (rebuilt for review finding H4):

* Data: only the columns that are needed (keys, ``FEATURE_COLUMNS``,
  ``TARGET_COLUMNS``) for ``snapshot_date >= ml_start_date()``.
* Targets are converted to **excess returns** (minus the per-date
  cross-sectional mean). Target definition (by target_backfill):
  ``return_h = close(d+h) / open(d+1) - 1``.
* Correlations are **mean daily rank ICs** (Spearman per date); p-values
  come from a Newey-West t-stat with lag ``h-1`` (overlapping targets),
  significance is Bonferroni-corrected over all feature x target tests.
* RF / LASSO use per-date ranked features, median imputation and scaling
  inside purged walk-forward folds (purge = 20 dates for return_20d).
* Hypothesis tests use per-date group differences / daily IC differences
  with Newey-West t-stats instead of row-iid Welch t-tests.
"""
import base64
import re
import time
import traceback
from datetime import date, datetime
from io import BytesIO
from typing import Any

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import seaborn as sns  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.dialects.postgresql import insert  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.sql.elements import TextClause  # noqa: E402

from trading_signals.analysis.feature_groups import group_features  # noqa: E402
from trading_signals.analysis.modeling import (  # noqa: E402
    prepare_model_frame,
    purged_lasso,
    walk_forward_rf,
)
from trading_signals.analysis.stats import (  # noqa: E402
    DATE_COL,
    available_features,
    coerce_numeric,
    daily_group_difference,
    daily_rank_ic,
    excess_returns,
    horizon_from_target,
    ic_difference,
    ic_summary,
    mean_test,
    nw_lag_for_horizon,
)
from trading_signals.db.models.analysis import AnalysisReport  # noqa: E402
from trading_signals.db.models.features import (  # noqa: E402
    FEATURE_COLUMNS,
    KEY_COLUMNS,
    TARGET_COLUMNS,
)
from trading_signals.utils.logging import get_logger  # noqa: E402
from trading_signals.utils.retention import ml_start_date  # noqa: E402

logger = get_logger(__name__)

#: Reporting groups derived from the model (every feature included,
#: "Other" as fallback).
FEATURE_GROUPS: dict[str, list[str]] = group_features(FEATURE_COLUMNS)

TARGET_RETURNS: list[str] = list(TARGET_COLUMNS)

ALL_FEATURES: list[str] = list(FEATURE_COLUMNS)

#: Target / horizon used for RF, LASSO and hypothesis tests.
MODEL_TARGET = 'return_20d'
MODEL_HORIZON = 20
#: Minimum cross-section per date for a daily IC.
MIN_OBS_PER_DATE = 10
#: Minimum number of daily ICs / daily differences for a test.
MIN_TEST_DATES = 10
#: Minimum number of *independent* (non-overlapping) horizon periods before a
#: result may be called significant / confirmed. Daily observations of an
#: h-day return overlap, so n dates carry only ~n/h independent periods;
#: Newey-West on fewer is unreliable (e.g. 10 dates of 20d returns ≈ 0.5).
MIN_INDEPENDENT_PERIODS = 10
#: Row cap for RF/LASSO (rows are subsampled uniformly, all dates kept).
MAX_MODEL_ROWS = 300_000
#: Number of purged walk-forward test folds.
N_SPLITS = 4

_IDENT_RE = re.compile(r'^[a-z_][a-z0-9_]*$')

_REPORT_CSS = """
body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
       margin: 0; padding: 20px; color: #333; }
h1, h2, h3 { color: #2c3e50; }
.header { background-color: #34495e; color: white; padding: 20px;
          border-radius: 5px; margin-bottom: 20px; }
.header h1 { color: white; margin: 0; }
table { border-collapse: collapse; width: 100%; margin-bottom: 30px;
        box-shadow: 0 0 10px rgba(0,0,0,0.1); }
th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
th { background-color: #f2f2f2; color: #333; }
tr:nth-child(even) { background-color: #f9f9f9; }
img { max-width: 100%; height: auto; margin-bottom: 20px; border: 1px solid #eee;
      box-shadow: 0 0 5px rgba(0,0,0,0.1); }
.card { background: white; padding: 20px; margin-bottom: 20px; border-radius: 5px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
"""


def _fig_to_base64(fig) -> str:
    buf = BytesIO()
    fig.savefig(buf, format='png', dpi=100, bbox_inches='tight')
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode('utf-8')
    plt.close(fig)
    return f'data:image/png;base64,{encoded}'


def _finite_or_none(val: Any) -> float | None:
    """JSON-safe float (JSONB rejects NaN/inf)."""
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def min_dates_for_horizon(horizon: int) -> int:
    """Dates needed for MIN_INDEPENDENT_PERIODS non-overlapping periods."""
    return max(MIN_TEST_DATES, MIN_INDEPENDENT_PERIODS * max(1, int(horizon)))


def _verdict(
    pval: float | None,
    effect: float | None,
    alpha: float = 0.05,
    n_dates: int | None = None,
    horizon: int = 1,
) -> str:
    if n_dates is not None and n_dates < min_dates_for_horizon(horizon):
        return "insufficient_data"
    if pval is None or effect is None or not np.isfinite(pval):
        return "inconclusive"
    if pval < alpha and effect > 0:
        return "confirmed"
    if pval < alpha and effect < 0:
        return "rejected"
    return "inconclusive"


def build_load_query(
    table_columns: list[str] | set[str], start: date
) -> tuple[TextClause, dict[str, Any]]:
    """Parameterised SELECT of keys + features + targets present in the table.

    Column names come from the ORM model constants and are additionally
    validated as plain identifiers, so no user input reaches the SQL text.
    """
    present = set(table_columns)
    missing_keys = [c for c in KEY_COLUMNS if c not in present]
    if missing_keys:
        raise ValueError(f"feature_snapshots lacks key columns: {missing_keys}")
    wanted = [*KEY_COLUMNS, *FEATURE_COLUMNS, *TARGET_COLUMNS]
    cols = [c for c in wanted if c in present and _IDENT_RE.match(c)]
    col_sql = ", ".join(f'"{c}"' for c in cols)
    sql = text(
        f"SELECT {col_sql} FROM signals.feature_snapshots "
        "WHERE snapshot_date >= :start ORDER BY snapshot_date, ticker"
    )
    return sql, {"start": start}


def prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Type-coerce features (bool -> float), convert targets to excess returns.

    Idempotent (excess of excess == excess). Sets ``attrs['ts_prepared']``.
    """
    out = df.copy()
    out[DATE_COL] = pd.to_datetime(out[DATE_COL])
    feats = [c for c in FEATURE_COLUMNS if c in out.columns]
    if feats:
        out[feats] = coerce_numeric(out, feats).astype('float32')
    tgts = [c for c in TARGET_COLUMNS if c in out.columns]
    out = excess_returns(out, tgts, DATE_COL)
    sort_cols = [c for c in (DATE_COL, 'ticker') if c in out.columns]
    out = out.sort_values(sort_cols, kind='stable').reset_index(drop=True)
    out.attrs['ts_prepared'] = True
    return out


class FeatureAnalysisEngine:
    """Automated monthly feature analysis."""

    def __init__(self, session: Session):
        self.session = session
        self._model_cache: dict[tuple, Any] = {}
        self.diagnostics: dict[str, Any] = {}
        sns.set_theme(style='whitegrid')

    def run(self) -> AnalysisReport | None:
        """Run the full analysis pipeline and store results."""
        start_time = time.time()
        logger.info("Starting FeatureAnalysisEngine pipeline")

        # 1. Load data
        df = self._load_data()
        if df.empty:
            logger.warning("Empty dataframe, aborting analysis.")
            return None
        df = self._ensure_prepared(df)

        unique_dates = df['snapshot_date'].nunique()
        if unique_dates < 30:
            logger.warning(
                f"Not enough distinct snapshot dates (found {unique_dates}, min 30). "
                "Aborting."
            )
            return None

        snapshot_count = len(df)
        ticker_count = df['ticker'].nunique()
        date_range_start = df['snapshot_date'].min()
        date_range_end = df['snapshot_date'].max()

        logger.info(
            f"Loaded {snapshot_count} rows for {ticker_count} tickers "
            f"from {date_range_start} to {date_range_end}"
        )

        results = {
            'feature_correlations': {},
            'feature_importance_rf': {},
            'feature_importance_lasso': {},
            'hypothesis_results': {},
            'consensus_features': []
        }

        # 2. Compute correlations
        try:
            results['feature_correlations'] = self._compute_correlations(df)
        except Exception as e:
            logger.error(f"Error computing correlations: {e}")
            logger.debug(traceback.format_exc())

        # 3. Compute RF feature importance
        try:
            results['feature_importance_rf'] = self._compute_rf_importance(df)
        except Exception as e:
            logger.error(f"Error computing RF importance: {e}")
            logger.debug(traceback.format_exc())

        # 4. Compute LASSO feature importance
        try:
            results['feature_importance_lasso'] = self._compute_lasso_importance(df)
        except Exception as e:
            logger.error(f"Error computing LASSO importance: {e}")
            logger.debug(traceback.format_exc())

        # 5. Test hypotheses
        try:
            results['hypothesis_results'] = self._test_hypotheses(df)
        except Exception as e:
            logger.error(f"Error testing hypotheses: {e}")
            logger.debug(traceback.format_exc())

        # 6. Build consensus rankings
        try:
            results['consensus_features'] = self._build_consensus(results)
        except Exception as e:
            logger.error(f"Error building consensus: {e}")
            logger.debug(traceback.format_exc())

        computation_time = time.time() - start_time

        # 7. Generate HTML report
        html_report = ""
        try:
            html_report = self._generate_html_report(
                df, results, snapshot_count, ticker_count,
                date_range_start, date_range_end, computation_time
            )
        except Exception as e:
            logger.error(f"Error generating HTML report: {e}")
            logger.error(traceback.format_exc())

        # 8. Store in DB
        report_obj = None
        try:
            report_obj = self._store_results(
                date_range_start, date_range_end, snapshot_count, ticker_count,
                results, html_report, computation_time
            )
        except Exception as e:
            logger.error(f"Error storing results to DB: {e}")
            logger.debug(traceback.format_exc())

        # Fallback: return a transient object if DB store failed
        if report_obj is None:
            report_obj = AnalysisReport(
                report_date=date.today(),
                snapshot_count=snapshot_count,
                ticker_count=ticker_count,
                date_range_start=self._to_date(date_range_start),
                date_range_end=self._to_date(date_range_end),
                computation_time_seconds=computation_time,
            )
            logger.warning("Returning transient report (DB store may have failed)")

        logger.info(
            f"FeatureAnalysisEngine pipeline completed in {computation_time:.1f}s"
        )
        return report_obj

    # ── Data loading ─────────────────────────────────────────────────

    def _table_columns(self) -> list[str]:
        """Columns of signals.feature_snapshots in the live DB."""
        try:
            rows = self.session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = :table"
                ),
                {"schema": "signals", "table": "feature_snapshots"},
            ).scalars().all()
            cols = [str(r) for r in rows]
        except Exception as e:
            logger.warning(f"Could not inspect feature_snapshots columns: {e}")
            cols = []
        if not cols:
            cols = [*KEY_COLUMNS, *FEATURE_COLUMNS, *TARGET_COLUMNS]
        return cols

    def _load_data(self) -> pd.DataFrame:
        try:
            sql, params = build_load_query(self._table_columns(), ml_start_date())
            df = pd.read_sql(sql, self.session.bind, params=params)
            if not df.empty and 'snapshot_date' in df.columns:
                df = prepare_frame(df)
            return df
        except Exception as e:
            logger.error(f"Error loading data: {e}")
            return pd.DataFrame()

    @staticmethod
    def _ensure_prepared(df: pd.DataFrame) -> pd.DataFrame:
        if df.attrs.get('ts_prepared'):
            return df
        return prepare_frame(df)

    def _model_data(self, df: pd.DataFrame):
        """Ranked model matrix for MODEL_TARGET (cached per frame)."""
        key = (id(df), len(df))
        if key not in self._model_cache:
            feats = available_features(df)
            self._model_cache = {
                key: prepare_model_frame(
                    df, feats, MODEL_TARGET, max_rows=MAX_MODEL_ROWS
                )
            }
        return self._model_cache[key]

    # ── Correlations (daily rank IC) ─────────────────────────────────

    def _compute_correlations(self, df: pd.DataFrame) -> dict:
        logger.info("Computing mean daily rank ICs (excess returns, Newey-West)...")
        df = self._ensure_prepared(df)
        valid_features = available_features(df, ALL_FEATURES)
        targets = [t for t in TARGET_RETURNS if t in df.columns and df[t].notna().any()]

        summaries: dict[tuple[str, str], dict] = {}
        for feature in valid_features:
            for target in targets:
                ic = daily_rank_ic(df, feature, target, min_obs=MIN_OBS_PER_DATE)
                if len(ic) < MIN_TEST_DATES:
                    continue
                s = ic_summary(ic, horizon_from_target(target))
                if not np.isfinite(s['mean_ic']):
                    continue
                summaries[(feature, target)] = s

        num_tests = max(len(summaries), 1)
        res: dict[str, dict] = {}
        for (feature, target), s in summaries.items():
            pval = _finite_or_none(s['pvalue'])
            n_dates = int(s['n_dates'])
            reliable = n_dates >= min_dates_for_horizon(horizon_from_target(target))
            res.setdefault(feature, {})[target] = {
                'rho': float(s['mean_ic']),
                'pvalue': pval,
                'significant': bool(
                    reliable and pval is not None and pval * num_tests < 0.05
                ),
                'reliable': reliable,
                'icir': _finite_or_none(s['icir']),
                't_nw': _finite_or_none(s['t_nw']),
                'n_dates': n_dates,
            }
        return res

    # ── Model-based importance ───────────────────────────────────────

    def _compute_rf_importance(self, df: pd.DataFrame) -> dict:
        logger.info("Computing RF feature importance (purged walk-forward)...")
        df = self._ensure_prepared(df)
        if MODEL_TARGET not in df.columns:
            return {}
        x, y, dates = self._model_data(df)
        if len(y) < 100 or x.shape[1] == 0:
            return {}
        out = walk_forward_rf(x, y, dates, horizon=MODEL_HORIZON, n_splits=N_SPLITS)
        self.diagnostics['rf_folds'] = out.get('folds', [])
        return {
            f: {'importance': _finite_or_none(v['importance']) or 0.0,
                'std': _finite_or_none(v['std']) or 0.0}
            for f, v in out.get('importance', {}).items()
        }

    def _compute_lasso_importance(self, df: pd.DataFrame) -> dict:
        logger.info("Computing LASSO feature importance (purged walk-forward CV)...")
        df = self._ensure_prepared(df)
        if MODEL_TARGET not in df.columns:
            return {}
        x, y, dates = self._model_data(df)
        if len(y) < 100 or x.shape[1] == 0:
            return {}
        out = purged_lasso(x, y, dates, horizon=MODEL_HORIZON, n_splits=N_SPLITS)
        self.diagnostics['lasso_alpha'] = out.get('alpha')
        self.diagnostics['lasso_folds'] = out.get('folds', [])
        return {
            f: float(c) for f, c in out.get('coef', {}).items()
            if np.isfinite(c) and abs(c) > 1e-6
        }

    # ── Hypotheses ───────────────────────────────────────────────────

    def _test_hypotheses(self, df: pd.DataFrame) -> dict:
        logger.info("Testing hypotheses (per-date differences, Newey-West)...")
        df = self._ensure_prepared(df)
        results: dict[str, dict] = {}
        target = MODEL_TARGET
        if target not in df.columns:
            return results
        lag = nw_lag_for_horizon(MODEL_HORIZON)

        def has(*cols: str) -> bool:
            return all(c in df.columns for c in cols)

        def _add_result(hid: str, test: dict, detail: str):
            pval = _finite_or_none(test['pvalue'])
            effect = _finite_or_none(test['mean'])
            n = int(test.get('n') or 0)
            results[hid] = {
                'verdict': _verdict(pval, effect, n_dates=n, horizon=MODEL_HORIZON),
                'pvalue': pval,
                'effect_size': effect,
                'n_dates': n,
                'detail': detail,
            }

        def _group_test(hid: str, mask: pd.Series, valid: pd.Series, label: str):
            group = mask.astype(float).where(valid)
            diff = daily_group_difference(df, group, target, min_group=3)
            if len(diff) < MIN_TEST_DATES:
                return
            t = mean_test(diff, lag)
            _add_result(
                hid, t,
                f"{label}: mean daily excess-return diff {t['mean']:.4f} "
                f"(t_NW={t['t_nw']:.2f}, {t['n']} dates)",
            )

        def _ic_test(hid: str, fa: str, fb: str, label: str, use_abs: bool = False):
            ic_a, ic_b = ic_difference(df, fa, fb, target, min_obs=MIN_OBS_PER_DATE)
            if len(ic_a) < MIN_TEST_DATES:
                return
            if use_abs:
                sa = np.sign(ic_a.mean()) or 1.0
                sb = np.sign(ic_b.mean()) or 1.0
                diff = sa * ic_a - sb * ic_b  # mean == |IC_a| - |IC_b|
            else:
                diff = ic_a - ic_b
            t = mean_test(diff, lag)
            _add_result(
                hid, t,
                f"{label}: mean IC {fa}={ic_a.mean():.4f}, {fb}={ic_b.mean():.4f} "
                f"(t_NW of daily diff={t['t_nw']:.2f}, {t['n']} dates)",
            )

        # H1: ARK Multi-ETF
        if has('ark_multi_etf_signal'):
            x = df['ark_multi_etf_signal']
            _group_test('H1', x == 1, x.notna(), "Multi-ETF vs single-ETF")

        # H2: Insider Cluster > Single
        if has('insider_cluster_score', 'insider_buy_value_30d'):
            _ic_test('H2', 'insider_cluster_score', 'insider_buy_value_30d',
                     "Cluster vs single-buy IC")

        # H3: ARK + Form4 combined
        if has('ark_conviction_score', 'insider_cluster_active'):
            a, b = df['ark_conviction_score'], df['insider_cluster_active']
            _group_test('H3', (a > 0) & (b == 1), a.notna() & b.notna(),
                        "ARK+insider combined vs rest")

        # H4: Weight > Shares
        if has('ark_weight_delta_20d', 'ark_conviction_score'):
            _ic_test('H4', 'ark_weight_delta_20d', 'ark_conviction_score',
                     "Weight-delta vs conviction IC")

        # H9: Downgrades > Upgrades (|IC|, sign-adjusted daily difference)
        if has('analyst_downgrades_30d', 'analyst_upgrades_30d'):
            _ic_test('H9', 'analyst_downgrades_30d', 'analyst_upgrades_30d',
                     "|Down| vs |Up| IC", use_abs=True)

        # H10: Insider after Earnings Drop
        # consecutive_beats is 0 when the ticker has earnings coverage but no
        # current beat streak (NULL only without coverage) -> "== 0".
        if has('insider_cluster_active', 'consecutive_beats'):
            ica, cb = df['insider_cluster_active'], df['consecutive_beats']
            _group_test('H10', (ica == 1) & (cb == 0), ica.notna() & cb.notna(),
                        "Insider cluster after miss vs rest")

        # H11: Recurring Clusters
        if has('cluster_count_60d'):
            c = df['cluster_count_60d']
            _group_test('H11', c > 1, c >= 1, ">1 vs 1 cluster")

        # H12: Persistent ARK
        if has('ark_conviction_streak'):
            s = df['ark_conviction_streak']
            _group_test('H12', s >= 3, s > 0, ">=3 vs <3 streak")

        # H13: Multi-Source Convergence (daily IC of source count)
        sources = []
        if has('ark_conviction_score'):
            sources.append(df['ark_conviction_score'] > 0)
        if has('insider_cluster_active'):
            sources.append(df['insider_cluster_active'] == 1)
        if has('analyst_net_sentiment_30d'):
            sources.append(df['analyst_net_sentiment_30d'] > 0)
        if has('politician_buy_count_60d_disclosure'):
            sources.append(df['politician_buy_count_60d_disclosure'] > 0)
        if has('sentiment_avg_7d'):
            sources.append(df['sentiment_avg_7d'] > 0.1)
        if sources:
            h13 = pd.DataFrame({
                DATE_COL: df[DATE_COL],
                'source_count': pd.concat(sources, axis=1).sum(axis=1).astype(float),
                target: df[target],
            })
            ic = daily_rank_ic(h13, 'source_count', target, min_obs=MIN_OBS_PER_DATE)
            if len(ic) >= MIN_TEST_DATES:
                t = mean_test(ic, lag)
                _add_result(
                    'H13', t,
                    f"Source count mean daily IC: {t['mean']:.4f} "
                    f"(t_NW={t['t_nw']:.2f}, {t['n']} dates)",
                )

        return results

    def _build_consensus(self, results: dict) -> list[dict]:
        logger.info("Building consensus rankings...")
        correlations = results.get('feature_correlations', {})
        rf_imp = results.get('feature_importance_rf', {})
        lasso_imp = results.get('feature_importance_lasso', {})

        all_feats = set(correlations) | set(rf_imp) | set(lasso_imp)
        total_feats = len(all_feats)
        if total_feats == 0:
            return []

        def _sp_score(f: str) -> float:
            c = correlations.get(f, {}).get('return_20d', {})
            if not c.get('reliable', True):  # too short history (see MIN_INDEPENDENT_PERIODS)
                return 0.0
            return abs(c.get('rho', 0) or 0)

        spearman_scores = {f: _sp_score(f) for f in all_feats}
        rf_scores = {f: rf_imp.get(f, {}).get('importance', 0) for f in all_feats}
        lasso_scores = {f: abs(lasso_imp.get(f, 0)) for f in all_feats}

        def _get_ranks(scores, reverse=True):
            sorted_feats = sorted(scores, key=lambda k: scores[k], reverse=reverse)
            ranks = {}
            for i, f in enumerate(sorted_feats):
                # if score is exactly 0, assign max rank
                if scores[f] == 0:
                    ranks[f] = total_feats
                else:
                    ranks[f] = i + 1
            return ranks

        sp_ranks = _get_ranks(spearman_scores)
        rf_ranks = _get_ranks(rf_scores)
        la_ranks = _get_ranks(lasso_scores)

        consensus = []
        for f in all_feats:
            avg_rank = (sp_ranks[f] + rf_ranks[f] + la_ranks[f]) / 3.0
            consensus.append({
                'feature': f,
                'spearman_rank': sp_ranks[f],
                'rf_rank': rf_ranks[f],
                'lasso_rank': la_ranks[f],
                'avg_rank': avg_rank
            })

        consensus.sort(key=lambda x: x['avg_rank'])
        return consensus

    def _generate_html_report(
        self, df: pd.DataFrame, results: dict, snaps: int, tickers: int,
        d_start, d_end, comp_time: float,
    ) -> str:
        logger.info("Generating HTML report...")
        ic_label = "Mean daily rank IC"
        correlations = results.get('feature_correlations', {})

        # 1. Correlation Heatmap
        corr_data = []
        for feat, rets in correlations.items():
            row = {'Feature': feat}
            for ret_col in TARGET_RETURNS:
                row[ret_col] = rets.get(ret_col, {}).get('rho', np.nan)
            corr_data.append(row)

        heatmap_img = ""
        corr_df = None
        if corr_data:
            try:
                corr_df = pd.DataFrame(corr_data).set_index('Feature')
                corr_df = corr_df.dropna(axis=1, how='all')
                corr_df_abs = corr_df.abs().max(axis=1)
                top_idx = corr_df_abs.sort_values(ascending=False).head(20).index
                top_corr = corr_df.loc[top_idx]

                fig, ax = plt.subplots(figsize=(8, 10))
                sns.heatmap(
                    top_corr, annot=True, cmap='coolwarm', center=0, fmt='.3f', ax=ax
                )
                ax.set_title(f"Top 20 Features – {ic_label} (excess returns)")
                plt.tight_layout()
                heatmap_img = _fig_to_base64(fig)
            except Exception as e:
                logger.error(f"Error creating heatmap: {e}")

        horizons = (
            [t for t in TARGET_RETURNS if t in corr_df.columns]
            if corr_df is not None else []
        )

        # 2. Top-15 Features per Horizon
        horizon_imgs = []
        for tgt in horizons:
            try:
                fig, ax = plt.subplots(figsize=(8, 6))
                order = corr_df[tgt].abs().sort_values(ascending=False)
                plot_data = corr_df.loc[order.head(15).index, tgt].dropna()
                colors = ['#e74c3c' if v < 0 else '#2ecc71' for v in plot_data]
                ax.barh(range(len(plot_data)), plot_data.values, color=colors)
                ax.set_yticks(range(len(plot_data)))
                ax.set_yticklabels(plot_data.index)
                ax.invert_yaxis()
                ax.set_title(f"Top 15 Features by |{ic_label}| for {tgt}")
                ax.set_xlabel(ic_label)
                plt.tight_layout()
                horizon_imgs.append(_fig_to_base64(fig))
            except Exception as e:
                logger.error(f"Error creating horizon plot for {tgt}: {e}")

        # 3. Feature Group Comparison
        group_img = ""
        if corr_df is not None:
            try:
                group_avg = []
                for gname, gfeats in FEATURE_GROUPS.items():
                    g_df = corr_df[corr_df.index.isin(gfeats)]
                    if not g_df.empty:
                        for tgt in horizons:
                            group_avg.append({
                                'Group': gname,
                                'Horizon': tgt,
                                'Avg_Abs_IC': g_df[tgt].abs().mean()
                            })
                if group_avg:
                    gdf = pd.DataFrame(group_avg)
                    fig, ax = plt.subplots(figsize=(10, 6))
                    sns.barplot(
                        data=gdf, x='Group', y='Avg_Abs_IC', hue='Horizon', ax=ax
                    )
                    ax.set_title(f"Average |{ic_label}| by Feature Group")
                    plt.xticks(rotation=45)
                    plt.tight_layout()
                    group_img = _fig_to_base64(fig)
            except Exception as e:
                logger.error(f"Error creating group comparison: {e}")

        # 4. RF vs LASSO
        rf_lasso_img = ""
        consensus = results.get('consensus_features', [])
        if consensus:
            try:
                rf_imp = results.get('feature_importance_rf', {})
                lasso_imp = results.get('feature_importance_lasso', {})

                top_rf = sorted(
                    [c['feature'] for c in consensus if c['feature'] in rf_imp],
                    key=lambda f: abs(rf_imp[f].get('importance', 0)),
                    reverse=True,
                )[:10]
                top_la = sorted(
                    [c['feature'] for c in consensus if lasso_imp.get(c['feature'])],
                    key=lambda f: abs(lasso_imp[f]),
                    reverse=True,
                )[:10]

                fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 5))
                if top_rf:
                    rf_vals = [rf_imp[f]['importance'] for f in top_rf]
                    ax1.barh(range(len(rf_vals)), rf_vals)
                    ax1.set_yticks(range(len(rf_vals)))
                    ax1.set_yticklabels(top_rf)
                    ax1.invert_yaxis()
                    ax1.set_title('Top 10 RF Permutation Importance (last OOS fold)')
                if top_la:
                    la_vals = [lasso_imp[f] for f in top_la]
                    ax2.barh(range(len(la_vals)), la_vals)
                    ax2.set_yticks(range(len(la_vals)))
                    ax2.set_yticklabels(top_la)
                    ax2.invert_yaxis()
                    ax2.set_title('Top 10 LASSO Coefficients (ranked features)')
                plt.tight_layout()
                rf_lasso_img = _fig_to_base64(fig)
            except Exception as e:
                logger.error(f"Error creating RF/LASSO plot: {e}")

        # 5. Tables
        def _row(cells) -> str:
            return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>\n"

        def _header(cells) -> str:
            return "<tr>" + "".join(f"<th>{c}</th>" for c in cells) + "</tr>"

        def _fmt(val, spec='.3f') -> str:
            return format(val, spec) if val is not None else 'N/A'

        cons_rows = "".join(
            _row([i + 1, c['feature'], f"{c['avg_rank']:.1f}", c['spearman_rank'],
                  c['rf_rank'], c['lasso_rank']])
            for i, c in enumerate(consensus[:20])
        )

        ic20 = [
            (f, v[MODEL_TARGET]) for f, v in correlations.items()
            if MODEL_TARGET in v
        ]
        ic20.sort(key=lambda fv: abs(fv[1].get('t_nw') or 0), reverse=True)
        ic_rows = "".join(
            _row([f, _fmt(v.get('rho'), '.4f'), _fmt(v.get('icir')),
                  _fmt(v.get('t_nw'), '.2f'), _fmt(v.get('pvalue'), '.4f'),
                  v.get('n_dates', ''), 'yes' if v.get('significant') else 'no'])
            for f, v in ic20[:20]
        )

        colors = {'confirmed': 'green', 'rejected': 'red'}
        hyp_rows = ""
        for hid, hres in results.get('hypothesis_results', {}).items():
            color = colors.get(hres['verdict'], 'gray')
            verdict = (
                f"<span style='color:{color};font-weight:bold;'>"
                f"{hres['verdict']}</span>"
            )
            hyp_rows += _row([hid, verdict, _fmt(hres['pvalue'], '.4f'),
                              _fmt(hres['effect_size'], '.4f'), hres['detail']])

        diag = []
        for fold in self.diagnostics.get('rf_folds', []):
            diag.append(
                f"RF fold {fold['fold']}: OOS R² {fold['r2']:.4f}, "
                f"OOS mean daily IC {fold['mean_ic']:.4f} (n_test={fold['n_test']})"
            )
        if self.diagnostics.get('lasso_alpha') is not None:
            diag.append(f"LASSO purged-CV alpha: {self.diagnostics['lasso_alpha']:.6g}")
        diag_html = "".join(f"<li>{d}</li>" for d in diag)

        def _img(src: str) -> str:
            return f'<img src="{src}"><br>' if src else ''

        method_items = [
            "Window: snapshot_date &ge; ml_start_date(). Targets: excess returns "
            "(return minus per-date cross-sectional mean), "
            "return_h = close(d+h)/open(d+1) - 1.",
            f"Correlations: {ic_label} (Spearman per date, averaged over dates); "
            "p-values from Newey-West t-stats with lag h-1; Bonferroni over all tests.",
            "RF / LASSO: per-date ranked features, imputer+scaler fit inside purged "
            f"walk-forward folds (purge {MODEL_HORIZON} dates), "
            f"target excess {MODEL_TARGET}.",
            "Hypotheses: per-date group differences / daily IC differences, "
            "Newey-West t.",
            "Market-wide features (macro_*, breadth_*) have no cross-sectional "
            "variation and are not evaluated here.",
        ]
        method_html = "".join(f"<li>{m}</li>" for m in method_items)

        hyp_header = _header(
            ["Hypothesis", "Verdict", "p-value (NW)", "Effect Size", "Detail"]
        )
        cons_header = _header(
            ["Rank", "Feature", "Avg Rank", "IC Rank", "RF Rank", "LASSO Rank"]
        )
        ic_header = _header(
            ["Feature", ic_label, "ICIR", "t (NW)", "p-value", "Dates",
             "Bonferroni sig."]
        )

        html = f"""
        <html>
        <head>
            <style>{_REPORT_CSS}</style>
        </head>
        <body>
            <div class="header">
                <h1>Feature Analysis Report</h1>
                <p>Generated on: {date.today()}</p>
                <p>Data Range: {d_start} to {d_end} | Tickers: {tickers} |
                   Snapshots: {snaps}</p>
                <p>Computation Time: {comp_time:.1f}s</p>
            </div>

            <div class="card">
                <h2>Methodology</h2>
                <ul>{method_html}</ul>
                <ul>{diag_html}</ul>
            </div>

            <div class="card">
                <h2>Hypothesis Tests Results</h2>
                <table>
                    {hyp_header}
                    {hyp_rows}
                </table>
            </div>

            <div class="card">
                <h2>Consensus Feature Ranking (Top 20)</h2>
                <table>
                    {cons_header}
                    {cons_rows}
                </table>
            </div>

            <div class="card">
                <h2>Daily Rank IC – {MODEL_TARGET} (Top 20 by |t_NW|)</h2>
                <table>
                    {ic_header}
                    {ic_rows}
                </table>
            </div>

            <div class="card">
                <h2>Correlation Analysis ({ic_label})</h2>
                {_img(heatmap_img)}
                {_img(group_img)}
                <h3>Top Features per Horizon</h3>
                {"".join(_img(img) for img in horizon_imgs)}
            </div>

            <div class="card">
                <h2>Feature Importance (RF &amp; LASSO)</h2>
                {_img(rf_lasso_img)}
            </div>
        </body>
        </html>
        """
        return html

    @staticmethod
    def _to_date(val) -> date:
        """Convert pandas Timestamp or datetime to Python date."""
        if hasattr(val, 'date'):
            return val.date() if callable(val.date) else val.date
        return val

    def _store_results(
        self, d_start, d_end, snaps, tickers, results, html, comp_time
    ) -> AnalysisReport:
        logger.info("Storing results in DB...")

        report_date = date.today()
        d_start_py = self._to_date(d_start)
        d_end_py = self._to_date(d_end)

        stmt = insert(AnalysisReport).values(
            report_date=report_date,
            snapshot_count=snaps,
            ticker_count=tickers,
            date_range_start=d_start_py,
            date_range_end=d_end_py,
            feature_correlations=results.get('feature_correlations', {}),
            feature_importance_rf=results.get('feature_importance_rf', {}),
            feature_importance_lasso=results.get('feature_importance_lasso', {}),
            hypothesis_results=results.get('hypothesis_results', {}),
            consensus_features=results.get('consensus_features', []),
            html_report=html,
            computation_time_seconds=comp_time,
            computed_at=datetime.utcnow()
        )

        update_dict = {
            'snapshot_count': stmt.excluded.snapshot_count,
            'ticker_count': stmt.excluded.ticker_count,
            'date_range_start': stmt.excluded.date_range_start,
            'date_range_end': stmt.excluded.date_range_end,
            'feature_correlations': stmt.excluded.feature_correlations,
            'feature_importance_rf': stmt.excluded.feature_importance_rf,
            'feature_importance_lasso': stmt.excluded.feature_importance_lasso,
            'hypothesis_results': stmt.excluded.hypothesis_results,
            'consensus_features': stmt.excluded.consensus_features,
            'html_report': stmt.excluded.html_report,
            'computation_time_seconds': stmt.excluded.computation_time_seconds,
            'computed_at': stmt.excluded.computed_at
        }

        stmt = stmt.on_conflict_do_update(
            index_elements=[AnalysisReport.__table__.c.report_date],
            set_=update_dict
        )

        self.session.execute(stmt)
        self.session.flush()

        # Fetch and return the persisted object
        return (
            self.session.query(AnalysisReport)
            .filter_by(report_date=report_date)
            .first()
        )
