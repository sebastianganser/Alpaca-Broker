"""
Feature Analysis Engine.
Automated monthly feature analysis for trading signals.
"""
import time
import base64
import traceback
from datetime import date
from io import BytesIO
from typing import Dict, List, Any, Optional

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import pandas as pd
import scipy.stats as stats
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sklearn.ensemble import RandomForestRegressor
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LassoCV
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

from trading_signals.utils.logging import get_logger
from trading_signals.db.models.analysis import AnalysisReport

logger = get_logger(__name__)

FEATURE_GROUPS = {
    'ARK': ['ark_in_etf_count', 'ark_total_weight', 'ark_weight_delta_1d', 'ark_weight_delta_5d', 'ark_weight_delta_20d', 'ark_conviction_score', 'ark_multi_etf_signal', 'ark_increase_days_10d', 'ark_increase_days_20d', 'ark_conviction_streak', 'ark_weight_trend_20d'],
    'Insider': ['insider_net_buy_count_30d', 'insider_buy_value_30d', 'insider_cluster_active', 'insider_cluster_score', 'cluster_count_30d', 'cluster_count_60d', 'cluster_score_sum_60d', 'days_since_last_cluster'],
    'Analyst': ['analyst_rating_score', 'analyst_upgrades_30d', 'analyst_price_target_upside', 'analyst_downgrades_30d', 'analyst_net_sentiment_30d', 'analyst_net_sentiment_60d', 'analyst_upgrade_streak'],
    'Politician': ['politician_buy_count_60d_disclosure', 'politician_distinct_90d_disclosure', 'politician_buy_count_60d_transaction', 'politician_distinct_90d_transaction'],
    'Fundamentals': ['pe_ratio', 'forward_pe', 'ps_ratio', 'revenue_growth_yoy', 'profit_margin', 'debt_to_equity', 'pe_trend_4w', 'margin_trend_4w'],
    'Technical': ['price_vs_sma50', 'price_vs_sma200', 'rsi_14', 'relative_strength_spy', 'volume_ratio_20d', 'atr_14_pct'],
    'Earnings': ['earnings_days_until', 'consecutive_beats', 'surprise_trend_3q'],
    'Sentiment': ['sentiment_avg_7d', 'sentiment_avg_30d', 'sentiment_momentum', 'sentiment_neg_count_7d', 'sentiment_article_count_7d', 'market_sentiment_7d'],
}

TARGET_RETURNS = ['return_1d', 'return_5d', 'return_20d']
EXCLUDED_FEATURES = ['form13f_top_holder_count', 'form13f_new_positions_count']

ALL_FEATURES = [f for group in FEATURE_GROUPS.values() for f in group]

def _fig_to_base64(fig) -> str:
    buf = BytesIO()
    fig.savefig(buf, format='png', dpi=100, bbox_inches='tight')
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode('utf-8')
    plt.close(fig)
    return f'data:image/png;base64,{encoded}'

class FeatureAnalysisEngine:
    """Automated monthly feature analysis."""
    
    def __init__(self, session: Session):
        self.session = session
        sns.set_theme(style='whitegrid')
        
    def run(self) -> Optional[AnalysisReport]:
        """Run the full analysis pipeline and store results."""
        start_time = time.time()
        logger.info("Starting FeatureAnalysisEngine pipeline")
        
        # 1. Load data
        df = self._load_data()
        if df.empty:
            logger.warning("Empty dataframe, aborting analysis.")
            return None
            
        unique_dates = df['snapshot_date'].nunique()
        if unique_dates < 30:
            logger.warning(f"Not enough distinct snapshot dates (found {unique_dates}, min 30). Aborting.")
            return None
            
        snapshot_count = len(df)
        ticker_count = df['ticker'].nunique()
        date_range_start = df['snapshot_date'].min()
        date_range_end = df['snapshot_date'].max()
        
        logger.info(f"Loaded {snapshot_count} rows for {ticker_count} tickers from {date_range_start} to {date_range_end}")
        
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
            logger.debug(traceback.format_exc())
            
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
            
        logger.info(f"FeatureAnalysisEngine pipeline completed in {computation_time:.1f}s")
        return report_obj

    def _load_data(self) -> pd.DataFrame:
        query = text("SELECT * FROM signals.feature_snapshots")
        try:
            df = pd.read_sql(query, self.session.bind)
            if not df.empty and 'snapshot_date' in df.columns:
                df['snapshot_date'] = pd.to_datetime(df['snapshot_date'])
                df.sort_values('snapshot_date', inplace=True)
            return df
        except Exception as e:
            logger.error(f"Error loading data: {e}")
            return pd.DataFrame()

    def _compute_correlations(self, df: pd.DataFrame) -> Dict:
        logger.info("Computing Spearman correlations...")
        res = {}
        valid_features = [f for f in ALL_FEATURES if f in df.columns]
        num_tests = len(valid_features) * len(TARGET_RETURNS)
        
        for feature in valid_features:
            res[feature] = {}
            for target in TARGET_RETURNS:
                if target not in df.columns:
                    continue
                temp_df = df[[feature, target]].dropna()
                if len(temp_df) < 30:
                    continue
                rho, pval = stats.spearmanr(temp_df[feature], temp_df[target])
                if np.isnan(rho):
                    continue
                    
                corrected_pval = pval * num_tests
                res[feature][target] = {
                    'rho': float(rho),
                    'pvalue': float(pval),
                    'significant': bool(corrected_pval < 0.05)
                }
        return res

    def _compute_rf_importance(self, df: pd.DataFrame) -> Dict:
        logger.info("Computing Random Forest feature importance...")
        if 'return_20d' not in df.columns:
            return {}
            
        valid_features = [f for f in ALL_FEATURES if f in df.columns]
        model_df = df[valid_features + ['return_20d', 'snapshot_date']].dropna(subset=['return_20d'])
        
        if len(model_df) < 100:
            return {}
            
        # Chronological split: 70/30 of dates
        dates = np.sort(model_df['snapshot_date'].unique())
        split_idx = int(len(dates) * 0.7)
        split_date = dates[split_idx]
        
        train_mask = model_df['snapshot_date'] <= split_date
        test_mask = model_df['snapshot_date'] > split_date
        
        X_train = model_df.loc[train_mask, valid_features]
        y_train = model_df.loc[train_mask, 'return_20d']
        X_test = model_df.loc[test_mask, valid_features]
        y_test = model_df.loc[test_mask, 'return_20d']
        
        # Convert booleans to int, fill NAs with median
        X_train = X_train.astype(float).fillna(X_train.median())
        X_test = X_test.astype(float).fillna(X_train.median()) # Use train medians
        
        rf = RandomForestRegressor(n_estimators=500, max_depth=10, min_samples_leaf=20, random_state=42, n_jobs=-1)
        rf.fit(X_train, y_train)
        
        r2 = rf.score(X_test, y_test)
        mae = np.mean(np.abs(rf.predict(X_test) - y_test))
        logger.info(f"RF Test R2: {r2:.4f}, MAE: {mae:.4f}")
        
        perm_imp = permutation_importance(rf, X_test, y_test, n_repeats=10, random_state=42, n_jobs=-1)
        
        res = {}
        for i, feature in enumerate(valid_features):
            res[feature] = {
                'importance': float(perm_imp.importances_mean[i]),
                'std': float(perm_imp.importances_std[i])
            }
        return res

    def _compute_lasso_importance(self, df: pd.DataFrame) -> Dict:
        logger.info("Computing LASSO feature importance...")
        if 'return_20d' not in df.columns:
            return {}
            
        valid_features = [f for f in ALL_FEATURES if f in df.columns]
        model_df = df[valid_features + ['return_20d', 'snapshot_date']].dropna(subset=['return_20d'])
        model_df.sort_values('snapshot_date', inplace=True)
        
        if len(model_df) < 100:
            return {}
            
        X = model_df[valid_features]
        y = model_df['return_20d']
        
        X = X.astype(float).fillna(X.median())
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        tscv = TimeSeriesSplit(n_splits=5)
        lasso = LassoCV(cv=tscv, random_state=42, n_jobs=-1)
        lasso.fit(X_scaled, y)
        
        logger.info(f"LASSO optimal alpha: {lasso.alpha_:.6f}")
        
        res = {}
        for i, feature in enumerate(valid_features):
            coef = float(lasso.coef_[i])
            if abs(coef) > 1e-6:
                res[feature] = coef
        return res

    def _test_hypotheses(self, df: pd.DataFrame) -> Dict:
        logger.info("Testing hypotheses...")
        results = {}
        
        def _add_result(hid: str, verdict: str, pval: float, effect: float, detail: str):
            results[hid] = {
                'verdict': verdict,
                'pvalue': float(pval) if not np.isnan(pval) else None,
                'effect_size': float(effect) if not np.isnan(effect) else None,
                'detail': detail
            }

        if 'return_20d' in df.columns:
            # H1: ARK Multi-ETF
            if 'ark_multi_etf_signal' in df.columns:
                h1_df = df[['ark_multi_etf_signal', 'return_20d']].dropna()
                g1 = h1_df[h1_df['ark_multi_etf_signal'] == True]['return_20d']
                g2 = h1_df[h1_df['ark_multi_etf_signal'] == False]['return_20d']
                if len(g1) > 10 and len(g2) > 10:
                    t_stat, pval = stats.ttest_ind(g1, g2, equal_var=False)
                    effect = g1.mean() - g2.mean()
                    verdict = "confirmed" if (pval < 0.05 and effect > 0) else "rejected" if pval < 0.05 else "inconclusive"
                    _add_result('H1', verdict, pval, effect, f"Multi-ETF signal return diff: {effect:.4f}")

            # H2: Insider Cluster > Single
            if 'insider_cluster_score' in df.columns and 'insider_buy_value_30d' in df.columns:
                h2_df = df[['insider_cluster_score', 'insider_buy_value_30d', 'return_20d']].dropna()
                rho_cluster, pval_c = stats.spearmanr(h2_df['insider_cluster_score'], h2_df['return_20d'])
                rho_single, pval_s = stats.spearmanr(h2_df['insider_buy_value_30d'], h2_df['return_20d'])
                effect = rho_cluster - rho_single
                verdict = "confirmed" if (pval_c < 0.05 and rho_cluster > rho_single) else "inconclusive"
                _add_result('H2', verdict, pval_c, effect, f"Cluster rho: {rho_cluster:.4f}, Single rho: {rho_single:.4f}")

            # H3: ARK + Form4 combined
            if 'ark_conviction_score' in df.columns and 'insider_cluster_active' in df.columns:
                h3_df = df[['ark_conviction_score', 'insider_cluster_active', 'return_20d']].dropna()
                combined = (h3_df['ark_conviction_score'] > 0) & (h3_df['insider_cluster_active'] == True)
                g1 = h3_df[combined]['return_20d']
                g2 = h3_df[~combined]['return_20d']
                if len(g1) > 10 and len(g2) > 10:
                    t_stat, pval = stats.ttest_ind(g1, g2, equal_var=False)
                    effect = g1.mean() - g2.mean()
                    verdict = "confirmed" if (pval < 0.05 and effect > 0) else "rejected" if pval < 0.05 else "inconclusive"
                    _add_result('H3', verdict, pval, effect, f"Combined return diff: {effect:.4f}")

            # H4: Weight > Shares
            if 'ark_weight_delta_20d' in df.columns and 'ark_conviction_score' in df.columns:
                h4_df = df[['ark_weight_delta_20d', 'ark_conviction_score', 'return_20d']].dropna()
                rho_w, pval_w = stats.spearmanr(h4_df['ark_weight_delta_20d'], h4_df['return_20d'])
                rho_s, pval_s = stats.spearmanr(h4_df['ark_conviction_score'], h4_df['return_20d'])
                effect = rho_w - rho_s
                verdict = "confirmed" if (pval_w < 0.05 and rho_w > rho_s) else "inconclusive"
                _add_result('H4', verdict, pval_w, effect, f"Weight rho: {rho_w:.4f}, Score rho: {rho_s:.4f}")

            # H9: Downgrades > Upgrades
            if 'analyst_downgrades_30d' in df.columns and 'analyst_upgrades_30d' in df.columns:
                h9_df = df[['analyst_downgrades_30d', 'analyst_upgrades_30d', 'return_20d']].dropna()
                rho_down, pval_d = stats.spearmanr(h9_df['analyst_downgrades_30d'], h9_df['return_20d'])
                rho_up, pval_u = stats.spearmanr(h9_df['analyst_upgrades_30d'], h9_df['return_20d'])
                effect = abs(rho_down) - abs(rho_up)
                verdict = "confirmed" if (pval_d < 0.05 and abs(rho_down) > abs(rho_up)) else "inconclusive"
                _add_result('H9', verdict, pval_d, effect, f"|Down| rho: {abs(rho_down):.4f}, |Up| rho: {abs(rho_up):.4f}")

            # H10: Insider after Earnings Drop
            if 'insider_cluster_active' in df.columns and 'consecutive_beats' in df.columns:
                h10_df = df[['insider_cluster_active', 'consecutive_beats', 'return_20d']].dropna()
                drop_mask = (h10_df['insider_cluster_active'] == True) & (h10_df['consecutive_beats'] <= 0)
                g1 = h10_df[drop_mask]['return_20d']
                g2 = h10_df[~drop_mask]['return_20d']
                if len(g1) > 10 and len(g2) > 10:
                    t_stat, pval = stats.ttest_ind(g1, g2, equal_var=False)
                    effect = g1.mean() - g2.mean()
                    verdict = "confirmed" if (pval < 0.05 and effect > 0) else "rejected" if pval < 0.05 else "inconclusive"
                    _add_result('H10', verdict, pval, effect, f"Insider post-miss diff: {effect:.4f}")

            # H11: Recurring Clusters
            if 'cluster_count_60d' in df.columns:
                h11_df = df[['cluster_count_60d', 'return_20d']].dropna()
                g1 = h11_df[h11_df['cluster_count_60d'] > 1]['return_20d']
                g2 = h11_df[h11_df['cluster_count_60d'] == 1]['return_20d']
                if len(g1) > 10 and len(g2) > 10:
                    t_stat, pval = stats.ttest_ind(g1, g2, equal_var=False)
                    effect = g1.mean() - g2.mean()
                    verdict = "confirmed" if (pval < 0.05 and effect > 0) else "rejected" if pval < 0.05 else "inconclusive"
                    _add_result('H11', verdict, pval, effect, f">1 vs 1 cluster diff: {effect:.4f}")

            # H12: Persistent ARK
            if 'ark_conviction_streak' in df.columns:
                h12_df = df[['ark_conviction_streak', 'return_20d']].dropna()
                g1 = h12_df[h12_df['ark_conviction_streak'] >= 3]['return_20d']
                g2 = h12_df[(h12_df['ark_conviction_streak'] < 3) & (h12_df['ark_conviction_streak'] > 0)]['return_20d']
                if len(g1) > 10 and len(g2) > 10:
                    t_stat, pval = stats.ttest_ind(g1, g2, equal_var=False)
                    effect = g1.mean() - g2.mean()
                    verdict = "confirmed" if (pval < 0.05 and effect > 0) else "rejected" if pval < 0.05 else "inconclusive"
                    _add_result('H12', verdict, pval, effect, f">=3 vs <3 streak diff: {effect:.4f}")

            # H13: Multi-Source Convergence
            sources = []
            if 'ark_conviction_score' in df.columns: sources.append(df['ark_conviction_score'] > 0)
            if 'insider_cluster_active' in df.columns: sources.append(df['insider_cluster_active'] == True)
            if 'analyst_net_sentiment_30d' in df.columns: sources.append(df['analyst_net_sentiment_30d'] > 0)
            if 'politician_buy_count_60d_disclosure' in df.columns: sources.append(df['politician_buy_count_60d_disclosure'] > 0)
            if 'sentiment_avg_7d' in df.columns: sources.append(df['sentiment_avg_7d'] > 0.1)
            
            if sources:
                source_count = pd.concat(sources, axis=1).sum(axis=1)
                h13_df = pd.DataFrame({'source_count': source_count, 'return_20d': df['return_20d']}).dropna()
                rho_sc, pval_sc = stats.spearmanr(h13_df['source_count'], h13_df['return_20d'])
                verdict = "confirmed" if (pval_sc < 0.05 and rho_sc > 0) else "rejected" if pval_sc < 0.05 else "inconclusive"
                _add_result('H13', verdict, pval_sc, rho_sc, f"Source count rho: {rho_sc:.4f}")

        return results

    def _build_consensus(self, results: Dict) -> List[Dict]:
        logger.info("Building consensus rankings...")
        correlations = results.get('feature_correlations', {})
        rf_imp = results.get('feature_importance_rf', {})
        lasso_imp = results.get('feature_importance_lasso', {})
        
        all_feats = set(correlations.keys()) | set(rf_imp.keys()) | set(lasso_imp.keys())
        total_feats = len(all_feats)
        if total_feats == 0:
            return []
            
        spearman_scores = {f: abs(correlations.get(f, {}).get('return_20d', {}).get('rho', 0)) for f in all_feats}
        rf_scores = {f: rf_imp.get(f, {}).get('importance', 0) for f in all_feats}
        lasso_scores = {f: abs(lasso_imp.get(f, 0)) for f in all_feats}
        
        def _get_ranks(scores, reverse=True):
            sorted_feats = sorted(scores.keys(), key=lambda k: scores[k], reverse=reverse)
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

    def _generate_html_report(self, df: pd.DataFrame, results: Dict, snaps: int, tickers: int, 
                              d_start, d_end, comp_time: float) -> str:
        logger.info("Generating HTML report...")
        
        # 1. Correlation Heatmap
        corr_data = []
        for feat, rets in results.get('feature_correlations', {}).items():
            row = {'Feature': feat}
            for ret_col in TARGET_RETURNS:
                row[ret_col] = rets.get(ret_col, {}).get('rho', 0)
            corr_data.append(row)
            
        heatmap_img = ""
        if corr_data:
            corr_df = pd.DataFrame(corr_data).set_index('Feature')
            corr_df['max_abs'] = corr_df.abs().max(axis=1)
            top_corr = corr_df.sort_values('max_abs', ascending=False).drop('max_abs', axis=1).head(20)
            
            fig, ax = plt.subplots(figsize=(8, 10))
            sns.heatmap(top_corr, annot=True, cmap='coolwarm', center=0, fmt='.3f', ax=ax)
            ax.set_title("Top 20 Features Correlation Heatmap")
            plt.tight_layout()
            heatmap_img = _fig_to_base64(fig)
            
        # 2. Top-15 Features per Horizon
        horizon_imgs = []
        if corr_data:
            for tgt in TARGET_RETURNS:
                fig, ax = plt.subplots(figsize=(8, 6))
                df_sorted = corr_df.sort_values(tgt, key=abs, ascending=False).head(15)
                sns.barplot(x=df_sorted[tgt], y=df_sorted.index, ax=ax, palette='viridis')
                ax.set_title(f"Top 15 Features by |Spearman| for {tgt}")
                plt.tight_layout()
                horizon_imgs.append(_fig_to_base64(fig))
                
        # 3. Feature Group Comparison
        group_img = ""
        if corr_data:
            group_avg = []
            for gname, gfeats in FEATURE_GROUPS.items():
                g_df = corr_df[corr_df.index.isin(gfeats)]
                if not g_df.empty:
                    for tgt in TARGET_RETURNS:
                        group_avg.append({
                            'Group': gname,
                            'Horizon': tgt,
                            'Avg_Abs_Rho': g_df[tgt].abs().mean()
                        })
            if group_avg:
                gdf = pd.DataFrame(group_avg)
                fig, ax = plt.subplots(figsize=(10, 6))
                sns.barplot(data=gdf, x='Group', y='Avg_Abs_Rho', hue='Horizon', ax=ax)
                ax.set_title("Average |Spearman Correlation| by Feature Group")
                plt.xticks(rotation=45)
                plt.tight_layout()
                group_img = _fig_to_base64(fig)
                
        # 4. RF vs LASSO
        rf_lasso_img = ""
        consensus = results.get('consensus_features', [])
        if consensus:
            top_rf = sorted([f for f in consensus if f['rf_rank'] < 999], key=lambda x: x['rf_rank'])[:10]
            top_la = sorted([f for f in consensus if f['lasso_rank'] < 999], key=lambda x: x['lasso_rank'])[:10]
            
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 5))
            if top_rf:
                sns.barplot(x=[results['feature_importance_rf'][f['feature']]['importance'] for f in top_rf],
                            y=[f['feature'] for f in top_rf], ax=ax1, palette='mako')
                ax1.set_title('Top 10 RF Permutation Importance')
            if top_la:
                sns.barplot(x=[results['feature_importance_lasso'][f['feature']] for f in top_la],
                            y=[f['feature'] for f in top_la], ax=ax2, palette='rocket')
                ax2.set_title('Top 10 LASSO Coefficients')
            plt.tight_layout()
            rf_lasso_img = _fig_to_base64(fig)

        # 5. Tables
        cons_rows = ""
        for i, c in enumerate(consensus[:20]):
            cons_rows += f"<tr><td>{i+1}</td><td>{c['feature']}</td><td>{c['avg_rank']:.1f}</td><td>{c['spearman_rank']}</td><td>{c['rf_rank']}</td><td>{c['lasso_rank']}</td></tr>\n"
            
        hyp_rows = ""
        for hid, hres in results.get('hypothesis_results', {}).items():
            color = "green" if hres['verdict'] == 'confirmed' else "red" if hres['verdict'] == 'rejected' else "gray"
            pval_str = f"{hres['pvalue']:.4f}" if hres['pvalue'] is not None else "N/A"
            eff_str = f"{hres['effect_size']:.4f}" if hres['effect_size'] is not None else "N/A"
            hyp_rows += f"<tr><td>{hid}</td><td style='color:{color};font-weight:bold;'>{hres['verdict']}</td><td>{pval_str}</td><td>{eff_str}</td><td>{hres['detail']}</td></tr>\n"

        html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 20px; color: #333; }}
                h1, h2, h3 {{ color: #2c3e50; }}
                .header {{ background-color: #34495e; color: white; padding: 20px; border-radius: 5px; margin-bottom: 20px; }}
                .header h1 {{ color: white; margin: 0; }}
                table {{ border-collapse: collapse; width: 100%; margin-bottom: 30px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }}
                th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
                th {{ background-color: #f2f2f2; color: #333; }}
                tr:nth-child(even) {{ background-color: #f9f9f9; }}
                img {{ max-width: 100%; height: auto; margin-bottom: 20px; border: 1px solid #eee; box-shadow: 0 0 5px rgba(0,0,0,0.1); }}
                .card {{ background: white; padding: 20px; margin-bottom: 20px; border-radius: 5px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>Feature Analysis Report</h1>
                <p>Generated on: {date.today()}</p>
                <p>Data Range: {d_start} to {d_end} | Tickers: {tickers} | Snapshots: {snaps}</p>
                <p>Computation Time: {comp_time:.1f}s</p>
            </div>
            
            <div class="card">
                <h2>Hypothesis Tests Results</h2>
                <table>
                    <tr><th>Hypothesis</th><th>Verdict</th><th>p-value</th><th>Effect Size</th><th>Detail</th></tr>
                    {hyp_rows}
                </table>
            </div>
            
            <div class="card">
                <h2>Consensus Feature Ranking (Top 20)</h2>
                <table>
                    <tr><th>Rank</th><th>Feature</th><th>Avg Rank</th><th>Spearman Rank</th><th>RF Rank</th><th>LASSO Rank</th></tr>
                    {cons_rows}
                </table>
            </div>
            
            <div class="card">
                <h2>Correlation Analysis</h2>
                {f'<img src="{heatmap_img}"><br>' if heatmap_img else ''}
                {f'<img src="{group_img}"><br>' if group_img else ''}
                <h3>Top Features per Horizon</h3>
                {"".join([f'<img src="{img}"><br>' for img in horizon_imgs])}
            </div>
            
            <div class="card">
                <h2>Feature Importance (RF & LASSO)</h2>
                {f'<img src="{rf_lasso_img}"><br>' if rf_lasso_img else ''}
            </div>
        </body>
        </html>
        """
        return html

    def _store_results(self, d_start, d_end, snaps, tickers, results, html, comp_time) -> AnalysisReport:
        logger.info("Storing results in DB...")
        stmt = insert(AnalysisReport).values(
            report_date=date.today(),
            snapshot_count=snaps,
            ticker_count=tickers,
            date_range_start=d_start,
            date_range_end=d_end,
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
            index_elements=['report_date'],
            set_=update_dict
        )
        
        self.session.execute(stmt)
        self.session.commit()
        
        # Fetch and return the updated object
        return self.session.query(AnalysisReport).filter_by(report_date=date.today()).first()
