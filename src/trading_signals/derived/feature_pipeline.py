"""Feature Pipeline – aggregates raw signals into daily feature vectors.

The heart of the ML data preparation. For each active ticker on a given
trading day, computes ~49 features across 8 signal groups and stores
them in the feature_snapshots table via UPSERT.

Feature Groups:
  - ARK (11): ETF presence, weights, deltas, temporal trends
  - Insider (8): Net buys, cluster activity, temporal patterns
  - Analyst (7): Rating scores, upgrades, price targets, sentiment
  - Politician (4): Buy counts + distinct politicians (dual-date)
  - 13F (2): Top holder count, new positions
  - Fundamentals (8): Valuation ratios, margins, temporal trends
  - Technical (6): Price vs SMA, RSI, volume ratio, ATR
  - Earnings (3): Days until, consecutive beats, surprise trend
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.db.models.ark import ARKDelta, ARKHolding
from trading_signals.db.models.features import FeatureSnapshot
from trading_signals.db.models.form13f import Form13FHolding
from trading_signals.db.models.fundamentals import (
    AnalystRating,
    EarningsCalendar,
    FundamentalsSnapshot,
)
from trading_signals.db.models.insider import InsiderCluster, InsiderTrade
from trading_signals.db.models.politicians import PoliticianTrade
from trading_signals.db.models.prices import PriceDaily
from trading_signals.db.models.technical_indicators import TechnicalIndicator
from trading_signals.db.models.universe import Universe
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# Rating action -> numeric score mapping for analyst signals
_RATING_SCORES = {
    "up": 1.0, "main": 0.5, "reit": 0.3, "init": 0.0, "down": -1.0,
}


class FeaturePipeline:
    """Compute daily feature snapshots for all active tickers."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def compute_daily(self, target_date: date) -> int:
        """Compute feature snapshots for all active tickers on target_date.

        Returns:
            Number of feature snapshot rows written/updated.
        """
        tickers = self._get_active_tickers()
        logger.info(
            f"[feature_pipeline] Computing features for {len(tickers)} "
            f"tickers on {target_date}"
        )

        written = 0
        for ticker in tickers:
            features = self._compute_ticker(ticker, target_date)
            if features:
                self._upsert(ticker, target_date, features)
                written += 1

        self.session.flush()
        logger.info(
            f"[feature_pipeline] {target_date}: {written}/{len(tickers)} "
            f"snapshots written"
        )
        return written

    def _get_active_tickers(self) -> list[str]:
        stmt = (
            select(Universe.ticker)
            .where(Universe.is_active.is_(True))
            .order_by(Universe.ticker)
        )
        return [r[0] for r in self.session.execute(stmt).all()]

    def _compute_ticker(self, ticker: str, d: date) -> dict:
        """Compute all features for one ticker, gracefully degrading."""
        features: dict = {}
        for name, method in [
            ("ark", self._ark_features),
            ("insider", self._insider_features),
            ("analyst", self._analyst_features),
            ("politician", self._politician_features),
            ("13f", self._13f_features),
            ("fundamentals", self._fundamentals_features),
            ("technical", self._technical_features),
            ("earnings", self._earnings_features),
        ]:
            try:
                features.update(method(ticker, d))
            except Exception as e:
                logger.warning(
                    f"[feature_pipeline] {ticker} {name} failed: {e}"
                )
        return features

    # ── ARK Features (11) ────────────────────────────────────────────

    def _ark_features(self, ticker: str, d: date) -> dict:
        # Point-in-time: latest holdings snapshot on or before d
        latest = (
            select(func.max(ARKHolding.snapshot_date))
            .where(ARKHolding.ticker == ticker)
            .where(ARKHolding.snapshot_date <= d)
        )
        latest_date = self.session.execute(latest).scalar()
        if not latest_date:
            return {}

        holdings = list(
            self.session.execute(
                select(ARKHolding)
                .where(ARKHolding.ticker == ticker)
                .where(ARKHolding.snapshot_date == latest_date)
            ).scalars().all()
        )
        if not holdings:
            return {}

        etf_count = len(holdings)
        total_weight = sum(float(h.weight_pct or 0) for h in holdings)

        # Weight deltas (1d, 5d, 20d) via ark_deltas
        def _weight_delta(days: int) -> float | None:
            since = d - timedelta(days=days)
            row = self.session.execute(
                select(func.sum(ARKDelta.weight_delta))
                .where(ARKDelta.ticker == ticker)
                .where(ARKDelta.delta_date.between(since, d))
            ).scalar()
            return float(row) if row is not None else None

        wd1 = _weight_delta(1)
        wd5 = _weight_delta(5)
        wd20 = _weight_delta(20)

        # Conviction score: weight * etf_count (higher = stronger signal)
        conviction = round(total_weight * etf_count, 4) if total_weight else None
        multi_etf = etf_count >= 2

        # Temporal: increase days in rolling windows
        def _increase_days(days: int) -> int | None:
            since = d - timedelta(days=days)
            row = self.session.execute(
                select(func.count(func.distinct(ARKDelta.delta_date)))
                .where(ARKDelta.ticker == ticker)
                .where(ARKDelta.delta_type == "increased")
                .where(ARKDelta.delta_date.between(since, d))
            ).scalar()
            return int(row) if row else None

        # Conviction streak: consecutive days of increase
        deltas_recent = list(self.session.execute(
            select(ARKDelta.delta_date, ARKDelta.delta_type)
            .where(ARKDelta.ticker == ticker)
            .where(ARKDelta.delta_date <= d)
            .order_by(ARKDelta.delta_date.desc())
            .limit(30)
        ).all())
        streak = 0
        for row in deltas_recent:
            if row[1] == "increased":
                streak += 1
            else:
                break

        # Weight trend (linear slope of weight_delta over 20d)
        trend_rows = list(self.session.execute(
            select(ARKDelta.weight_delta)
            .where(ARKDelta.ticker == ticker)
            .where(ARKDelta.delta_date.between(d - timedelta(days=20), d))
            .order_by(ARKDelta.delta_date)
        ).all())
        weight_trend = None
        if len(trend_rows) >= 3:
            vals = [float(r[0] or 0) for r in trend_rows]
            n = len(vals)
            x_mean = (n - 1) / 2
            y_mean = sum(vals) / n
            num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(vals))
            den = sum((i - x_mean) ** 2 for i in range(n))
            weight_trend = round(num / den, 6) if den else None

        return {
            "ark_in_etf_count": etf_count,
            "ark_total_weight": round(total_weight, 4),
            "ark_weight_delta_1d": wd1,
            "ark_weight_delta_5d": wd5,
            "ark_weight_delta_20d": wd20,
            "ark_conviction_score": conviction,
            "ark_multi_etf_signal": multi_etf,
            "ark_increase_days_10d": _increase_days(10),
            "ark_increase_days_20d": _increase_days(20),
            "ark_conviction_streak": streak if streak else None,
            "ark_weight_trend_20d": weight_trend,
        }

    # ── Insider Features (8) ─────────────────────────────────────────

    def _insider_features(self, ticker: str, d: date) -> dict:
        since_30 = d - timedelta(days=30)
        since_60 = d - timedelta(days=60)

        # Net buy count (buys - sells) in 30d
        buys_30 = self.session.execute(
            select(func.count())
            .where(InsiderTrade.ticker == ticker)
            .where(InsiderTrade.transaction_type == "P")
            .where(InsiderTrade.is_derivative.is_(False))
            .where(InsiderTrade.transaction_date.between(since_30, d))
        ).scalar() or 0
        sells_30 = self.session.execute(
            select(func.count())
            .where(InsiderTrade.ticker == ticker)
            .where(InsiderTrade.transaction_type == "S")
            .where(InsiderTrade.is_derivative.is_(False))
            .where(InsiderTrade.transaction_date.between(since_30, d))
        ).scalar() or 0
        net_buy = buys_30 - sells_30

        # Buy value in 30d
        buy_val = self.session.execute(
            select(func.sum(InsiderTrade.total_value))
            .where(InsiderTrade.ticker == ticker)
            .where(InsiderTrade.transaction_type == "P")
            .where(InsiderTrade.is_derivative.is_(False))
            .where(InsiderTrade.transaction_date.between(since_30, d))
        ).scalar()

        # Active cluster check
        active_cluster = self.session.execute(
            select(InsiderCluster)
            .where(InsiderCluster.ticker == ticker)
            .where(InsiderCluster.cluster_end >= since_30)
            .where(InsiderCluster.cluster_start <= d)
            .order_by(InsiderCluster.cluster_score.desc())
            .limit(1)
        ).scalar()

        # Temporal: cluster counts + days since last
        c30 = self.session.execute(
            select(func.count()).select_from(InsiderCluster)
            .where(InsiderCluster.ticker == ticker)
            .where(InsiderCluster.cluster_start.between(since_30, d))
        ).scalar() or 0
        c60 = self.session.execute(
            select(func.count()).select_from(InsiderCluster)
            .where(InsiderCluster.ticker == ticker)
            .where(InsiderCluster.cluster_start.between(since_60, d))
        ).scalar() or 0
        score_sum = self.session.execute(
            select(func.sum(InsiderCluster.cluster_score))
            .where(InsiderCluster.ticker == ticker)
            .where(InsiderCluster.cluster_start.between(since_60, d))
        ).scalar()

        last_cluster_date = self.session.execute(
            select(func.max(InsiderCluster.cluster_end))
            .where(InsiderCluster.ticker == ticker)
            .where(InsiderCluster.cluster_end <= d)
        ).scalar()
        days_since = (d - last_cluster_date).days if last_cluster_date else None

        return {
            "insider_net_buy_count_30d": net_buy if (buys_30 or sells_30) else None,
            "insider_buy_value_30d": float(buy_val) if buy_val else None,
            "insider_cluster_active": active_cluster is not None,
            "insider_cluster_score": (
                float(active_cluster.cluster_score)
                if active_cluster and active_cluster.cluster_score else None
            ),
            "cluster_count_30d": c30 or None,
            "cluster_count_60d": c60 or None,
            "cluster_score_sum_60d": float(score_sum) if score_sum else None,
            "days_since_last_cluster": days_since,
        }

    # ── Analyst Features (7) ─────────────────────────────────────────

    def _analyst_features(self, ticker: str, d: date) -> dict:
        since_30 = d - timedelta(days=30)
        since_60 = d - timedelta(days=60)

        # Latest rating score
        latest = self.session.execute(
            select(AnalystRating.action)
            .where(AnalystRating.ticker == ticker)
            .where(AnalystRating.rating_date <= d)
            .order_by(AnalystRating.rating_date.desc())
            .limit(1)
        ).scalar()
        rating_score = _RATING_SCORES.get(latest) if latest else None

        # Upgrades & downgrades in 30d
        upgrades_30 = self.session.execute(
            select(func.count())
            .where(AnalystRating.ticker == ticker)
            .where(AnalystRating.action == "up")
            .where(AnalystRating.rating_date.between(since_30, d))
        ).scalar() or 0
        downgrades_30 = self.session.execute(
            select(func.count())
            .where(AnalystRating.ticker == ticker)
            .where(AnalystRating.action == "down")
            .where(AnalystRating.rating_date.between(since_30, d))
        ).scalar() or 0

        # Price target upside from fundamentals
        fund = self.session.execute(
            select(FundamentalsSnapshot.target_price_mean)
            .where(FundamentalsSnapshot.ticker == ticker)
            .where(FundamentalsSnapshot.snapshot_date <= d)
            .order_by(FundamentalsSnapshot.snapshot_date.desc())
            .limit(1)
        ).scalar()
        price = self.session.execute(
            select(PriceDaily.close)
            .where(PriceDaily.ticker == ticker)
            .where(PriceDaily.trade_date <= d)
            .order_by(PriceDaily.trade_date.desc())
            .limit(1)
        ).scalar()
        upside = None
        if fund and price and float(price) > 0:
            upside = round((float(fund) / float(price)) - 1, 4)

        # Temporal: net sentiment, streak
        net_30 = upgrades_30 - downgrades_30
        downgrades_60 = self.session.execute(
            select(func.count())
            .where(AnalystRating.ticker == ticker)
            .where(AnalystRating.action == "down")
            .where(AnalystRating.rating_date.between(since_60, d))
        ).scalar() or 0
        upgrades_60 = self.session.execute(
            select(func.count())
            .where(AnalystRating.ticker == ticker)
            .where(AnalystRating.action == "up")
            .where(AnalystRating.rating_date.between(since_60, d))
        ).scalar() or 0
        net_60 = upgrades_60 - downgrades_60

        # Upgrade streak
        recent = list(self.session.execute(
            select(AnalystRating.action)
            .where(AnalystRating.ticker == ticker)
            .where(AnalystRating.rating_date <= d)
            .order_by(AnalystRating.rating_date.desc())
            .limit(10)
        ).all())
        streak = 0
        for r in recent:
            if r[0] == "up":
                streak += 1
            else:
                break

        has_data = rating_score is not None or upgrades_30 or downgrades_30
        return {
            "analyst_rating_score": rating_score,
            "analyst_upgrades_30d": upgrades_30 if has_data else None,
            "analyst_price_target_upside": upside,
            "analyst_downgrades_30d": downgrades_30 if has_data else None,
            "analyst_net_sentiment_30d": net_30 if has_data else None,
            "analyst_net_sentiment_60d": net_60 if has_data else None,
            "analyst_upgrade_streak": streak if streak else None,
        }

    # ── Politician Features (4) ──────────────────────────────────────

    def _politician_features(self, ticker: str, d: date) -> dict:
        since_60 = d - timedelta(days=60)
        since_90 = d - timedelta(days=90)

        def _count(date_col, since, txn_type="Purchase"):
            return self.session.execute(
                select(func.count())
                .where(PoliticianTrade.ticker == ticker)
                .where(PoliticianTrade.transaction_type == txn_type)
                .where(date_col.between(since, d))
            ).scalar() or 0

        def _distinct(date_col, since):
            return self.session.execute(
                select(func.count(func.distinct(PoliticianTrade.politician_name)))
                .where(PoliticianTrade.ticker == ticker)
                .where(date_col.between(since, d))
            ).scalar() or 0

        bc_disc = _count(PoliticianTrade.disclosure_date, since_60)
        dp_disc = _distinct(PoliticianTrade.disclosure_date, since_90)
        bc_txn = _count(PoliticianTrade.transaction_date, since_60)
        dp_txn = _distinct(PoliticianTrade.transaction_date, since_90)

        has_data = bc_disc or dp_disc or bc_txn or dp_txn
        return {
            "politician_buy_count_60d_disclosure": bc_disc if has_data else None,
            "politician_distinct_90d_disclosure": dp_disc if has_data else None,
            "politician_buy_count_60d_transaction": bc_txn if has_data else None,
            "politician_distinct_90d_transaction": dp_txn if has_data else None,
        }

    # ── 13F Features (2) ─────────────────────────────────────────────

    def _13f_features(self, ticker: str, d: date) -> dict:
        # Latest report period
        latest_period = self.session.execute(
            select(func.max(Form13FHolding.report_period))
            .where(Form13FHolding.ticker == ticker)
            .where(Form13FHolding.report_period <= d)
        ).scalar()
        if not latest_period:
            return {}

        top_holders = self.session.execute(
            select(func.count(func.distinct(Form13FHolding.filer_cik)))
            .where(Form13FHolding.ticker == ticker)
            .where(Form13FHolding.report_period == latest_period)
        ).scalar() or 0

        # Previous quarter for new positions comparison
        prev_period = self.session.execute(
            select(func.max(Form13FHolding.report_period))
            .where(Form13FHolding.ticker == ticker)
            .where(Form13FHolding.report_period < latest_period)
        ).scalar()

        new_positions = 0
        if prev_period:
            current_filers = set(r[0] for r in self.session.execute(
                select(Form13FHolding.filer_cik)
                .where(Form13FHolding.ticker == ticker)
                .where(Form13FHolding.report_period == latest_period)
            ).all())
            prev_filers = set(r[0] for r in self.session.execute(
                select(Form13FHolding.filer_cik)
                .where(Form13FHolding.ticker == ticker)
                .where(Form13FHolding.report_period == prev_period)
            ).all())
            new_positions = len(current_filers - prev_filers)

        return {
            "form13f_top_holder_count": top_holders or None,
            "form13f_new_positions_count": new_positions or None,
        }

    # ── Fundamentals Features (8) ────────────────────────────────────

    def _fundamentals_features(self, ticker: str, d: date) -> dict:
        latest = self.session.execute(
            select(FundamentalsSnapshot)
            .where(FundamentalsSnapshot.ticker == ticker)
            .where(FundamentalsSnapshot.snapshot_date <= d)
            .order_by(FundamentalsSnapshot.snapshot_date.desc())
            .limit(1)
        ).scalar()
        if not latest:
            return {}

        result = {
            "pe_ratio": float(latest.pe_ratio) if latest.pe_ratio else None,
            "forward_pe": float(latest.forward_pe) if latest.forward_pe else None,
            "ps_ratio": float(latest.ps_ratio) if latest.ps_ratio else None,
            "revenue_growth_yoy": (
                float(latest.revenue_growth_yoy)
                if latest.revenue_growth_yoy else None
            ),
            "profit_margin": (
                float(latest.profit_margin) if latest.profit_margin else None
            ),
            "debt_to_equity": (
                float(latest.debt_to_equity) if latest.debt_to_equity else None
            ),
        }

        # Temporal: 4-week trends (linear regression slope)
        since_4w = d - timedelta(weeks=4)
        snapshots = list(self.session.execute(
            select(
                FundamentalsSnapshot.snapshot_date,
                FundamentalsSnapshot.pe_ratio,
                FundamentalsSnapshot.profit_margin,
            )
            .where(FundamentalsSnapshot.ticker == ticker)
            .where(FundamentalsSnapshot.snapshot_date.between(since_4w, d))
            .order_by(FundamentalsSnapshot.snapshot_date)
        ).all())

        if len(snapshots) >= 2:
            def _slope(values):
                clean = [(i, float(v)) for i, v in enumerate(values) if v is not None]
                if len(clean) < 2:
                    return None
                n = len(clean)
                x_m = sum(x for x, _ in clean) / n
                y_m = sum(y for _, y in clean) / n
                num = sum((x - x_m) * (y - y_m) for x, y in clean)
                den = sum((x - x_m) ** 2 for x, _ in clean)
                return round(num / den, 6) if den else None

            result["pe_trend_4w"] = _slope([s[1] for s in snapshots])
            result["margin_trend_4w"] = _slope([s[2] for s in snapshots])
        else:
            result["pe_trend_4w"] = None
            result["margin_trend_4w"] = None

        return result

    # ── Technical Features (6) ───────────────────────────────────────

    def _technical_features(self, ticker: str, d: date) -> dict:
        ti = self.session.execute(
            select(TechnicalIndicator)
            .where(TechnicalIndicator.ticker == ticker)
            .where(TechnicalIndicator.trade_date <= d)
            .order_by(TechnicalIndicator.trade_date.desc())
            .limit(1)
        ).scalar()
        if not ti:
            return {}

        price = self.session.execute(
            select(PriceDaily.close, PriceDaily.volume)
            .where(PriceDaily.ticker == ticker)
            .where(PriceDaily.trade_date <= d)
            .order_by(PriceDaily.trade_date.desc())
            .limit(1)
        ).first()
        if not price or not price[0]:
            return {}

        close = float(price[0])
        vol = int(price[1]) if price[1] else None

        def _rel(sma_val):
            if sma_val and float(sma_val) > 0:
                return round(close / float(sma_val) - 1, 4)
            return None

        vol_ratio = None
        if vol and ti.volume_sma_20 and float(ti.volume_sma_20) > 0:
            vol_ratio = round(vol / float(ti.volume_sma_20), 4)

        atr_pct = None
        if ti.atr_14 and close > 0:
            atr_pct = round(float(ti.atr_14) / close, 4)

        return {
            "price_vs_sma50": _rel(ti.sma_50),
            "price_vs_sma200": _rel(ti.sma_200),
            "rsi_14": float(ti.rsi_14) if ti.rsi_14 else None,
            "relative_strength_spy": (
                float(ti.relative_strength_spy)
                if ti.relative_strength_spy else None
            ),
            "volume_ratio_20d": vol_ratio,
            "atr_14_pct": atr_pct,
        }

    # ── Earnings Features (3) ────────────────────────────────────────

    def _earnings_features(self, ticker: str, d: date) -> dict:
        # Days until next earnings
        next_earn = self.session.execute(
            select(func.min(EarningsCalendar.earnings_date))
            .where(EarningsCalendar.ticker == ticker)
            .where(EarningsCalendar.earnings_date >= d)
        ).scalar()
        days_until = (next_earn - d).days if next_earn else None

        # Past earnings for beat streak + surprise trend
        past = list(self.session.execute(
            select(
                EarningsCalendar.earnings_date,
                EarningsCalendar.eps_estimate,
                EarningsCalendar.eps_actual,
                EarningsCalendar.surprise_pct,
            )
            .where(EarningsCalendar.ticker == ticker)
            .where(EarningsCalendar.earnings_date < d)
            .where(EarningsCalendar.eps_actual.isnot(None))
            .order_by(EarningsCalendar.earnings_date.desc())
            .limit(4)
        ).all())

        consecutive_beats = 0
        for row in past:
            est, act = row[1], row[2]
            if est is not None and act is not None and float(act) > float(est):
                consecutive_beats += 1
            else:
                break

        # Surprise trend: avg of last 3 surprise_pct
        surprises = [float(r[3]) for r in past[:3] if r[3] is not None]
        surprise_trend = round(sum(surprises) / len(surprises), 4) if surprises else None

        return {
            "earnings_days_until": days_until,
            "consecutive_beats": consecutive_beats or None,
            "surprise_trend_3q": surprise_trend,
        }

    # ── UPSERT ───────────────────────────────────────────────────────

    def _upsert(self, ticker: str, d: date, features: dict) -> None:
        """Insert or update a feature snapshot row."""
        values = {"snapshot_date": d, "ticker": ticker, **features}
        # Remove None values to avoid overwriting existing data with NULL
        update_cols = {k: v for k, v in features.items() if v is not None}
        if not update_cols:
            return

        stmt = (
            pg_insert(FeatureSnapshot)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["snapshot_date", "ticker"],
                set_=update_cols,
            )
        )
        self.session.execute(stmt)
