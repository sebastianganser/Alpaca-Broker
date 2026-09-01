"""Feature Pipeline – aggregates raw signals into daily feature vectors.

The heart of the ML data preparation. For each active ticker on a given
trading day, computes ~70 features across 11 signal groups and stores
them in the feature_snapshots table via UPSERT.

Feature Groups:
  - ARK (11): ETF presence, weights, deltas, temporal trends
  - Insider (8): Net buys, cluster activity, temporal patterns
  - Analyst (7): Rating scores, upgrades, price targets, sentiment
  - Politician (4): Buy counts + distinct politicians (dual-date)
  - 13F (2): Top holder count, new positions
  - Fundamentals (8): Valuation ratios, margins, temporal trends
  - Technical (6): Price vs SMA, RSI, volume ratio, ATR
  - Liquidity (2): Dollar volume 20d, Amihud illiquidity (Sprint 9.5b E4)
  - Earnings (5): Days until, beats, surprise trend, SUE, PEAD (Sprint 9.5b B2)
  - Sentiment (7): News sentiment, momentum, attention, volume ratio (Sprint 9.5b E3)
  - Macro (6): VIX, yields, HY spread, dollar, inflation (Sprint 9.5b D1)
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
from trading_signals.db.models.macro_series import MacroSeries
from trading_signals.db.models.news import NewsSentiment
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

# B4 Sprint 9.5b: yfinance sector name -> GICS Sector SPDR ETF ticker
# Maps the sector names from Universe.sector (populated by yfinance/sector
# enrichment) to the corresponding Select Sector SPDR ETFs (from B3).
_SECTOR_ETF_MAP = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Industrials": "XLI",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Communication Services": "XLC",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Basic Materials": "XLB",
    "Energy": "XLE",
    # GICS names (from seed_benchmark_etfs, edge cases)
    "Information Technology": "XLK",
    "Health Care": "XLV",
    "Financials": "XLF",
    "Consumer Staples": "XLP",
    "Consumer Discretionary": "XLY",
    "Materials": "XLB",
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
        tickers = self._get_active_tickers(target_date)
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

    def _get_active_tickers(self, target_date: date) -> list[str]:
        """Return tickers that were active on target_date.

        Uses point-in-time index membership data if available,
        otherwise falls back to Universe.is_active == True.
        """
        from trading_signals.universe.manager import UniverseManager

        manager = UniverseManager(self.session)
        return manager.get_universe_as_of(target_date)

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
            ("sentiment", self._sentiment_features),
            ("macro", self._macro_features),
            ("breadth", self._breadth_features),
            ("sector", self._sector_features),
            ("short_interest", self._short_interest_features),
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
        # Point-in-time: only count trades whose Form 4 was filed by day d
        buys_30 = self.session.execute(
            select(func.count())
            .where(InsiderTrade.ticker == ticker)
            .where(InsiderTrade.transaction_type == "P")
            .where(InsiderTrade.is_derivative.is_(False))
            .where(InsiderTrade.transaction_date.between(since_30, d))
            .where(InsiderTrade.filing_date <= d)
        ).scalar() or 0
        sells_30 = self.session.execute(
            select(func.count())
            .where(InsiderTrade.ticker == ticker)
            .where(InsiderTrade.transaction_type == "S")
            .where(InsiderTrade.is_derivative.is_(False))
            .where(InsiderTrade.transaction_date.between(since_30, d))
            .where(InsiderTrade.filing_date <= d)
        ).scalar() or 0
        net_buy = buys_30 - sells_30

        # Buy value in 30d
        buy_val = self.session.execute(
            select(func.sum(InsiderTrade.total_value))
            .where(InsiderTrade.ticker == ticker)
            .where(InsiderTrade.transaction_type == "P")
            .where(InsiderTrade.is_derivative.is_(False))
            .where(InsiderTrade.transaction_date.between(since_30, d))
            .where(InsiderTrade.filing_date <= d)
        ).scalar()

        # Active cluster check
        active_cluster = self.session.execute(
            select(InsiderCluster)
            .where(InsiderCluster.ticker == ticker)
            .where(InsiderCluster.cluster_start <= d)
            .where(InsiderCluster.cluster_end >= d)
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
            **self._insider_ratio_features(ticker, d),
        }

    # ── Insider Ratio (Sprint 9.5b E2) ───────────────────────────────

    def _insider_ratio_features(self, ticker: str, d: date) -> dict:
        """Continuous insider buy ratio: buy_volume / (buy + sell volume).

        Volume-weighted over 30d and 90d windows.
        Values close to 1.0 = mostly buying, close to 0.0 = mostly selling.
        All queries use filing_date <= d for point-in-time correctness.
        """
        def _buy_ratio(days: int) -> float | None:
            since = d - timedelta(days=days)
            buy_vol = self.session.execute(
                select(func.sum(InsiderTrade.total_value))
                .where(InsiderTrade.ticker == ticker)
                .where(InsiderTrade.transaction_type == "P")
                .where(InsiderTrade.is_derivative.is_(False))
                .where(InsiderTrade.transaction_date.between(since, d))
                .where(InsiderTrade.filing_date <= d)
            ).scalar()
            sell_vol = self.session.execute(
                select(func.sum(InsiderTrade.total_value))
                .where(InsiderTrade.ticker == ticker)
                .where(InsiderTrade.transaction_type == "S")
                .where(InsiderTrade.is_derivative.is_(False))
                .where(InsiderTrade.transaction_date.between(since, d))
                .where(InsiderTrade.filing_date <= d)
            ).scalar()

            buy_f = float(buy_vol) if buy_vol else 0
            sell_f = float(sell_vol) if sell_vol else 0
            total = buy_f + sell_f
            if total > 0:
                return round(buy_f / total, 4)
            return None

        return {
            "insider_buy_ratio_30d": _buy_ratio(30),
            "insider_buy_ratio_90d": _buy_ratio(90),
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
        """Politician trade features with STOCK Act lag filter.
        
        C1 Sprint 9.5c: Only count trades where disclosure_lag <= 45 days.
        Trades with extreme delays (e.g. 800+ days) are noise, not signal.
        """
        since_60 = d - timedelta(days=60)
        since_90 = d - timedelta(days=90)
        max_lag_days = 45  # STOCK Act maximum reporting deadline

        def _lag_filter():
            """Filter: disclosure_date - transaction_date <= 45 days."""
            return and_(
                PoliticianTrade.transaction_date.isnot(None),
                PoliticianTrade.disclosure_date.isnot(None),
                PoliticianTrade.transaction_date >= (
                    PoliticianTrade.disclosure_date - timedelta(days=max_lag_days)
                ),
            )

        def _count(date_col, since, txn_type="Purchase"):
            return self.session.execute(
                select(func.count())
                .where(PoliticianTrade.ticker == ticker)
                .where(PoliticianTrade.transaction_type == txn_type)
                .where(date_col.between(since, d))
                .where(_lag_filter())  # C1: STOCK Act lag filter
            ).scalar() or 0

        def _distinct(date_col, since):
            return self.session.execute(
                select(func.count(func.distinct(PoliticianTrade.politician_name)))
                .where(PoliticianTrade.ticker == ticker)
                .where(date_col.between(since, d))
                .where(_lag_filter())  # C1: STOCK Act lag filter
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
        # Latest report period whose filing was public by day d
        # NOTE: filing_date is when the 13F was submitted to EDGAR,
        # which can be up to 45 days after report_period (quarter end).
        latest_period = self.session.execute(
            select(func.max(Form13FHolding.report_period))
            .where(Form13FHolding.ticker == ticker)
            .where(Form13FHolding.filing_date <= d)  # point-in-time: publicly filed
        ).scalar()
        if not latest_period:
            return {}

        top_holders = self.session.execute(
            select(func.count(func.distinct(Form13FHolding.filer_cik)))
            .where(Form13FHolding.ticker == ticker)
            .where(Form13FHolding.report_period == latest_period)
            .where(Form13FHolding.filing_date <= d)
        ).scalar() or 0

        # Previous quarter for new positions comparison
        prev_period = self.session.execute(
            select(func.max(Form13FHolding.report_period))
            .where(Form13FHolding.ticker == ticker)
            .where(Form13FHolding.report_period < latest_period)
            .where(Form13FHolding.filing_date <= d)
        ).scalar()

        new_positions = 0
        exited_positions = 0
        holder_delta_qoq = None
        if prev_period:
            current_filers = set(r[0] for r in self.session.execute(
                select(Form13FHolding.filer_cik)
                .where(Form13FHolding.ticker == ticker)
                .where(Form13FHolding.report_period == latest_period)
                .where(Form13FHolding.filing_date <= d)
            ).all())
            prev_filers = set(r[0] for r in self.session.execute(
                select(Form13FHolding.filer_cik)
                .where(Form13FHolding.ticker == ticker)
                .where(Form13FHolding.report_period == prev_period)
                .where(Form13FHolding.filing_date <= d)
            ).all())
            new_positions = len(current_filers - prev_filers)
            exited_positions = len(prev_filers - current_filers)

            # E1 Sprint 9.5b: Net holder change QoQ
            prev_count = len(prev_filers)
            if prev_count > 0:
                holder_delta_qoq = round(
                    (len(current_filers) - prev_count) / prev_count, 4
                )

        return {
            "form13f_top_holder_count": top_holders or None,
            "form13f_new_positions_count": new_positions or None,
            "form13f_exited_positions_count": exited_positions or None,
            "form13f_holder_delta_qoq": holder_delta_qoq,
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
            **self._liquidity_features(ticker, d),
        }

    # ── Liquidity Features (2, Sprint 9.5b E4) ───────────────────────

    def _liquidity_features(self, ticker: str, d: date) -> dict:
        """Dollar volume and Amihud illiquidity ratio over 20 trading days.

        dollar_volume_20d: 20-day SMA of Close * Volume (in USD)
        amihud_illiquidity_20d: mean(|daily_return| / dollar_volume)
        Higher Amihud = less liquid = higher price impact per dollar traded.
        """
        since_20 = d - timedelta(days=30)  # calendar days → ~20 trading days
        prices = list(self.session.execute(
            select(PriceDaily.close, PriceDaily.volume)
            .where(PriceDaily.ticker == ticker)
            .where(PriceDaily.trade_date.between(since_20, d))
            .order_by(PriceDaily.trade_date)
        ).all())

        if len(prices) < 5:
            return {}

        # Dollar volume: Close * Volume for each day
        dollar_vols = []
        for row in prices:
            c, v = row[0], row[1]
            if c and v and float(c) > 0 and int(v) > 0:
                dollar_vols.append(float(c) * int(v))

        dollar_volume_20d = None
        if dollar_vols:
            dollar_volume_20d = round(sum(dollar_vols) / len(dollar_vols), 0)

        # Amihud: mean(|return| / dollar_volume)
        amihud = None
        if len(dollar_vols) >= 2:
            amihud_vals = []
            for i in range(1, len(prices)):
                prev_c, curr_c = prices[i - 1][0], prices[i][0]
                curr_v = prices[i][1]
                if prev_c and curr_c and curr_v:
                    prev_c, curr_c = float(prev_c), float(curr_c)
                    dv = curr_c * int(curr_v)
                    if prev_c > 0 and dv > 0:
                        daily_ret = abs((curr_c - prev_c) / prev_c)
                        amihud_vals.append(daily_ret / dv)
            if amihud_vals:
                # Scale by 1e6 for readability (Amihud values are tiny)
                amihud = round(
                    sum(amihud_vals) / len(amihud_vals) * 1e6, 6
                )

        return {
            "dollar_volume_20d": dollar_volume_20d,
            "amihud_illiquidity_20d": amihud,
        }

    # ── Earnings Features (5, Sprint 9.5b) ───────────────────────────

    def _earnings_features(self, ticker: str, d: date) -> dict:
        # Days until next earnings
        # NOTE (Lookahead): This assumes future earnings dates were known as of d.
        # In practice, companies announce dates ~3 weeks ahead. For robust
        # backtesting, an announced_at / available_from field would be needed
        # on earnings_calendar. Limit forward window to 90 days to reduce risk.
        max_forward = d + timedelta(days=90)
        next_earn = self.session.execute(
            select(func.min(EarningsCalendar.earnings_date))
            .where(EarningsCalendar.ticker == ticker)
            .where(EarningsCalendar.earnings_date >= d)
            .where(EarningsCalendar.earnings_date <= max_forward)
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

        # B2 Sprint 9.5b: SUE (Standardized Unexpected Earnings)
        # SUE = (EPS_actual - EPS_estimate) / stdev(historical surprises)
        # Normalizes surprise for cross-sectional comparison
        sue_last = None
        if past and past[0][1] is not None and past[0][2] is not None:
            raw_surprise = float(past[0][2]) - float(past[0][1])
            # Historical surprise stdev from available quarters
            hist_surprises = [
                float(r[2]) - float(r[1])
                for r in past if r[1] is not None and r[2] is not None
            ]
            if len(hist_surprises) >= 2:
                mean_s = sum(hist_surprises) / len(hist_surprises)
                stdev_s = (
                    sum((s - mean_s) ** 2 for s in hist_surprises)
                    / (len(hist_surprises) - 1)
                ) ** 0.5
                if stdev_s > 0.001:  # avoid division by near-zero
                    sue_last = round(raw_surprise / stdev_s, 4)
            elif raw_surprise != 0:
                # Only 1 quarter: use raw surprise as-is (no normalization)
                sue_last = round(raw_surprise, 4)

        # B2: Days since last earnings (PEAD effect lasts 1-60 trading days)
        days_since = None
        if past:
            days_since = (d - past[0][0]).days

        return {
            "earnings_days_until": days_until,
            "consecutive_beats": consecutive_beats or None,
            "surprise_trend_3q": surprise_trend,
            "sue_last": sue_last,
            "days_since_last_earnings": days_since,
        }

    # ── UPSERT ───────────────────────────────────────────────────────

    # ── Sentiment Features (6, Sprint 8c) ────────────────────────────

    def _sentiment_features(self, ticker: str, d: date) -> dict:
        """Sentiment features from news articles.

        Computes rolling-window averages and counts from the
        news_sentiment table, joined with news_articles to filter
        by publication date (not scoring date). Also includes a global
        market sentiment feature as contextual signal.
        """
        from trading_signals.db.models.news import NewsArticle

        since_7 = d - timedelta(days=7)
        since_30 = d - timedelta(days=30)
        end = d + timedelta(days=1)  # inclusive upper bound

        # Helper: query sentiment scores for a ticker in a date range
        # Uses published_at from NewsArticle (not scored_at from NewsSentiment)
        def _avg_sentiment(t: str | None, since: date) -> tuple[float | None, int, int]:
            """Returns (avg_score, neg_count, total_count) for ticker in window."""
            base_join = (
                select(
                    func.avg(NewsSentiment.sentiment_score),
                    func.count(),
                )
                .select_from(NewsSentiment)
                .join(NewsArticle, NewsSentiment.article_id == NewsArticle.id)
                .where(NewsArticle.published_at >= since)
                .where(NewsArticle.published_at < end)
            )
            if t is not None:
                base_join = base_join.where(NewsSentiment.ticker == t)
            else:
                base_join = base_join.where(NewsSentiment.ticker.is_(None))

            row = self.session.execute(base_join).first()
            avg_val = float(row[0]) if row and row[0] is not None else None
            total = int(row[1]) if row else 0

            # Count negative articles
            neg_join = (
                select(func.count())
                .select_from(NewsSentiment)
                .join(NewsArticle, NewsSentiment.article_id == NewsArticle.id)
                .where(NewsSentiment.sentiment_label == "negative")
                .where(NewsArticle.published_at >= since)
                .where(NewsArticle.published_at < end)
            )
            if t is not None:
                neg_join = neg_join.where(NewsSentiment.ticker == t)
            else:
                neg_join = neg_join.where(NewsSentiment.ticker.is_(None))

            neg_count = self.session.execute(neg_join).scalar() or 0

            return avg_val, neg_count, total

        # Ticker-specific sentiment
        avg_7d, neg_7d, count_7d = _avg_sentiment(ticker, since_7)
        avg_30d, _, _ = _avg_sentiment(ticker, since_30)

        # Momentum: short-term vs long-term sentiment
        momentum = None
        if avg_7d is not None and avg_30d is not None:
            momentum = round(avg_7d - avg_30d, 4)

        # Global market sentiment (articles without ticker association)
        market_7d, _, _ = _avg_sentiment(None, since_7)

        has_data = avg_7d is not None or avg_30d is not None or count_7d > 0

        # E3 Sprint 9.5b: News volume ratio (spike detection)
        # 7d article count vs 90d average → unusual news activity
        news_vol_ratio = None
        if count_7d > 0:
            since_90 = d - timedelta(days=90)
            _, _, count_90d = _avg_sentiment(ticker, since_90)
            if count_90d > 0:
                avg_weekly_90d = count_90d / (90 / 7)
                if avg_weekly_90d > 0:
                    news_vol_ratio = round(count_7d / avg_weekly_90d, 4)

        return {
            "sentiment_avg_7d": round(avg_7d, 4) if avg_7d is not None else None,
            "sentiment_avg_30d": round(avg_30d, 4) if avg_30d is not None else None,
            "sentiment_momentum": momentum,
            "sentiment_neg_count_7d": neg_7d if has_data else None,
            "sentiment_article_count_7d": count_7d if has_data else None,
            "market_sentiment_7d": (
                round(market_7d, 4) if market_7d is not None else None
            ),
            "news_volume_ratio_7d": news_vol_ratio,
        }

    # ── Macro Features (6, Sprint 9.5b) ──────────────────────────────

    _macro_cache: dict = {}

    def _macro_features(self, ticker: str, d: date) -> dict:
        """Market-wide macro features from FRED data.

        These are the same for every ticker on a given day, so we cache
        the result after the first call per target_date.
        """
        if d in self._macro_cache:
            return self._macro_cache[d]

        def _latest_value(series_id: str) -> float | None:
            """Get the most recent observation for a series on or before d."""
            val = self.session.execute(
                select(MacroSeries.value)
                .where(MacroSeries.series_id == series_id)
                .where(MacroSeries.obs_date <= d)
                .order_by(MacroSeries.obs_date.desc())
                .limit(1)
            ).scalar()
            return float(val) if val is not None else None

        dgs2 = _latest_value("DGS2")
        dgs10 = _latest_value("DGS10")
        vix = _latest_value("VIXCLS")
        hy_spread = _latest_value("BAMLH0A0HYM2")
        dollar = _latest_value("DTWEXBGS")
        inflation = _latest_value("T10YIE")

        # Derived: yield spread (10Y - 2Y), negative = inverted curve
        yield_spread = None
        if dgs10 is not None and dgs2 is not None:
            yield_spread = round(dgs10 - dgs2, 4)

        # Derived: VIX regime classification
        vix_regime = None
        if vix is not None:
            if vix < 15:
                vix_regime = 0  # low volatility
            elif vix < 25:
                vix_regime = 1  # medium
            else:
                vix_regime = 2  # high volatility

        result = {
            "macro_yield_spread": yield_spread,
            "macro_vix": round(vix, 2) if vix is not None else None,
            "macro_vix_regime": vix_regime,
            "macro_hy_spread": round(hy_spread, 4) if hy_spread is not None else None,
            "macro_dollar_index": round(dollar, 2) if dollar is not None else None,
            "macro_inflation_expectation": round(inflation, 4) if inflation is not None else None,
        }
        self._macro_cache[d] = result
        return result

    # ── Sector-Relative Features (B4, Sprint 9.5b) ───────────────────

    _sector_etf_cache: dict = {}  # (etf_ticker, date) -> {return_20d, pct_sma50}

    def _sector_features(self, ticker: str, d: date) -> dict:
        """Sector-relative features for stock-vs-sector neutralization.

        - sector_relative_return_20d: stock 20d return minus sector ETF 20d return
        - sector_relative_momentum: stock price_vs_sma50 minus sector ETF's

        Returns empty dict for benchmark ETFs or stocks without sector mapping.
        """
        # Get the stock's sector from Universe
        uni = self.session.execute(
            select(Universe.sector).where(Universe.ticker == ticker)
        ).scalar()

        if not uni or uni == "Benchmark":
            return {}

        etf_ticker = _SECTOR_ETF_MAP.get(uni)
        if not etf_ticker:
            return {}

        # Get sector ETF data (cached per ETF+date)
        cache_key = (etf_ticker, d)
        if cache_key not in self._sector_etf_cache:
            self._sector_etf_cache[cache_key] = self._get_etf_metrics(
                etf_ticker, d
            )

        etf_data = self._sector_etf_cache[cache_key]
        if not etf_data:
            return {}

        result = {}

        # Sector-relative return: stock 20d return minus ETF 20d return
        stock_price = self.session.execute(
            select(PriceDaily.close)
            .where(PriceDaily.ticker == ticker)
            .where(PriceDaily.trade_date <= d)
            .order_by(PriceDaily.trade_date.desc())
            .limit(1)
        ).scalar()

        stock_price_20d = self.session.execute(
            select(PriceDaily.close)
            .where(PriceDaily.ticker == ticker)
            .where(PriceDaily.trade_date <= d - timedelta(days=28))
            .order_by(PriceDaily.trade_date.desc())
            .limit(1)
        ).scalar()

        if stock_price and stock_price_20d and float(stock_price_20d) > 0:
            stock_ret = (float(stock_price) - float(stock_price_20d)) / float(
                stock_price_20d
            )
            if etf_data.get("return_20d") is not None:
                result["sector_relative_return_20d"] = round(
                    stock_ret - etf_data["return_20d"], 6
                )

        # Sector-relative momentum: stock price_vs_sma50 minus ETF's
        ti = self.session.execute(
            select(TechnicalIndicator.sma_50)
            .where(TechnicalIndicator.ticker == ticker)
            .where(TechnicalIndicator.trade_date <= d)
            .order_by(TechnicalIndicator.trade_date.desc())
            .limit(1)
        ).scalar()

        if stock_price and ti and float(ti) > 0:
            stock_vs_sma50 = (float(stock_price) - float(ti)) / float(ti)
            if etf_data.get("pct_vs_sma50") is not None:
                result["sector_relative_momentum"] = round(
                    stock_vs_sma50 - etf_data["pct_vs_sma50"], 6
                )

        return result

    def _get_etf_metrics(self, etf_ticker: str, d: date) -> dict | None:
        """Compute return_20d and pct_vs_sma50 for a sector ETF."""
        price = self.session.execute(
            select(PriceDaily.close)
            .where(PriceDaily.ticker == etf_ticker)
            .where(PriceDaily.trade_date <= d)
            .order_by(PriceDaily.trade_date.desc())
            .limit(1)
        ).scalar()

        if not price:
            return None

        price_20d = self.session.execute(
            select(PriceDaily.close)
            .where(PriceDaily.ticker == etf_ticker)
            .where(PriceDaily.trade_date <= d - timedelta(days=28))
            .order_by(PriceDaily.trade_date.desc())
            .limit(1)
        ).scalar()

        sma50 = self.session.execute(
            select(TechnicalIndicator.sma_50)
            .where(TechnicalIndicator.ticker == etf_ticker)
            .where(TechnicalIndicator.trade_date <= d)
            .order_by(TechnicalIndicator.trade_date.desc())
            .limit(1)
        ).scalar()

        result = {}
        if price_20d and float(price_20d) > 0:
            result["return_20d"] = (float(price) - float(price_20d)) / float(
                price_20d
            )
        if sma50 and float(sma50) > 0:
            result["pct_vs_sma50"] = (float(price) - float(sma50)) / float(
                sma50
            )
        return result if result else None

    # ── Market Breadth Features (3, Sprint 9.5b D2) ──────────────────

    _breadth_cache: dict = {}

    def _breadth_features(self, ticker: str, d: date) -> dict:
        """Market-wide breadth features computed from prices_daily.

        Same for every ticker on a given day, cached after first call.
        - advance_decline_ratio: advances / (advances + declines) on day d
        - pct_above_sma50: % of universe tickers with close > SMA50

        Uses a SAVEPOINT so that SQL errors don't poison the outer transaction.
        """
        if d in self._breadth_cache:
            return self._breadth_cache[d]

        from sqlalchemy.sql import text

        ad_ratio = None
        pct_above = None

        try:
            # Use nested transaction (SAVEPOINT) so errors don't kill the session
            nested = self.session.begin_nested()

            # Advance/Decline: count tickers with positive vs negative daily return
            prev_day = d - timedelta(days=5)  # lookback for prev close
            ad_query = text("""
                WITH daily_returns AS (
                    SELECT ticker,
                           close,
                           LAG(close) OVER (PARTITION BY ticker ORDER BY trade_date) AS prev_close
                    FROM signals.prices_daily
                    WHERE trade_date BETWEEN :prev_day AND :target_date
                )
                SELECT
                    SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END) AS advances,
                    SUM(CASE WHEN close < prev_close THEN 1 ELSE 0 END) AS declines
                FROM daily_returns
                WHERE prev_close IS NOT NULL
                  AND close IS NOT NULL
            """)
            ad_result = self.session.execute(
                ad_query, {"prev_day": prev_day, "target_date": d}
            ).first()

            if ad_result and ad_result[0] is not None:
                advances = int(ad_result[0])
                declines = int(ad_result[1])
                total = advances + declines
                if total > 0:
                    ad_ratio = round(advances / total, 4)

            # % above SMA50: use technical_indicators table
            above_sma50_query = text("""
                SELECT
                    COUNT(*) FILTER (WHERE p.close > t.sma_50) AS above,
                    COUNT(*) AS total
                FROM signals.technical_indicators t
                JOIN signals.prices_daily p
                  ON t.ticker = p.ticker AND t.trade_date = p.trade_date
                WHERE t.trade_date = (
                    SELECT MAX(trade_date)
                    FROM signals.technical_indicators
                    WHERE trade_date <= :target_date
                )
                AND t.sma_50 IS NOT NULL
            """)
            sma_result = self.session.execute(
                above_sma50_query, {"target_date": d}
            ).first()

            if sma_result and sma_result[1] and int(sma_result[1]) > 0:
                pct_above = round(int(sma_result[0]) / int(sma_result[1]), 4)

            nested.commit()
        except Exception as e:
            nested.rollback()
            logger.warning(f"[feature_pipeline] breadth query failed: {e}")

        result = {
            "breadth_advance_decline": ad_ratio,
            "breadth_pct_above_sma50": pct_above,
        }
        self._breadth_cache[d] = result
        return result

    def _short_interest_features(self, ticker: str, d: date) -> dict:
        """Short interest features from daily short volume data.
        
        - short_volume_ratio_5d: 5-day average of short_volume / total_volume
        - short_volume_ratio_20d: 20-day average
        - short_volume_change_20d: change in 20d ratio vs previous 20d period (momentum)
        """
        from trading_signals.db.models.short_interest import ShortVolume
        
        since_20 = d - timedelta(days=30)  # calendar days for ~20 trading days
        since_5 = d - timedelta(days=8)
        since_40 = d - timedelta(days=60)  # for previous 20d period
        
        # Get short volume ratios for last 60 calendar days
        rows = list(self.session.execute(
            select(ShortVolume.trade_date, ShortVolume.short_volume_ratio)
            .where(ShortVolume.ticker == ticker)
            .where(ShortVolume.trade_date.between(since_40, d))
            .where(ShortVolume.short_volume_ratio.isnot(None))
            .order_by(ShortVolume.trade_date)
        ).all())
        
        if not rows:
            return {}
            
        from datetime import datetime
        
        # Split into recent 5d, recent 20d, and previous 20d
        ratios_5d = [float(r[1]) for r in rows if r[0] >= (since_5.date() if isinstance(since_5, datetime) else since_5)]
        ratios_20d = [float(r[1]) for r in rows if r[0] >= (since_20.date() if isinstance(since_20, datetime) else since_20)]
        ratios_prev_20d = [float(r[1]) for r in rows if r[0] < (since_20.date() if isinstance(since_20, datetime) else since_20)]
        
        avg_5d = round(sum(ratios_5d) / len(ratios_5d), 4) if ratios_5d else None
        avg_20d = round(sum(ratios_20d) / len(ratios_20d), 4) if ratios_20d else None
        avg_prev_20d = round(sum(ratios_prev_20d) / len(ratios_prev_20d), 4) if ratios_prev_20d else None
        
        change = None
        if avg_20d is not None and avg_prev_20d is not None:
            change = round(avg_20d - avg_prev_20d, 4)
            
        return {
            "short_volume_ratio_5d": avg_5d,
            "short_volume_ratio_20d": avg_20d,
            "short_volume_change_20d": change,
        }

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
