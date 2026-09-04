"""FeatureSnapshot ORM model – daily feature vector per ticker.

The heart of the project. Aggregates all raw and derived signal data
into a single wide table (~86 columns) for ML training. Each row
represents one ticker on one trading day with all available features.

All feature columns are nullable – features self-activate when
sufficient data exists. No imputation at this level.

Feature groups:
  - ARK: Point-in-time (7) + temporal (4) = 11 features
  - Insider: Point-in-time (4) + temporal (4) = 8 features
  - Analyst: Point-in-time (3) + temporal (4) = 7 features
  - Politician: Dual-date variants (4) = 4 features
  - 13F: Point-in-time (2) = 2 features
  - Fundamentals: Point-in-time (6) + temporal (2) = 8 features
  - Technical: Point-in-time (6) = 6 features
  - Earnings: Context (3) = 3 features
  - Sentiment: News-based (6) = 6 features
  - Options IV: point-in-time (4) = 4 features
  - Estimates: revision signals (5) = 5 features
  - Targets: Forward returns (4) = 4 target variables
"""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base


class FeatureSnapshot(Base):
    """Daily feature vector for a single ticker.

    Composite primary key (snapshot_date, ticker) ensures one row
    per ticker per day. UPSERT pattern allows idempotent recomputation.
    """

    __tablename__ = "feature_snapshots"
    __table_args__ = (
        Index("idx_features_date", "snapshot_date"),
        Index("idx_features_ticker", "ticker"),
        {"schema": "signals"},
    )

    # ── Primary Key ──────────────────────────────────────────────────
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)

    # ── ARK Features (point-in-time) ─────────────────────────────────
    ark_in_etf_count: Mapped[int | None] = mapped_column(Integer)
    ark_total_weight: Mapped[float | None] = mapped_column(Numeric(10, 4))
    ark_weight_delta_1d: Mapped[float | None] = mapped_column(Numeric(10, 4))
    ark_weight_delta_5d: Mapped[float | None] = mapped_column(Numeric(10, 4))
    ark_weight_delta_20d: Mapped[float | None] = mapped_column(Numeric(10, 4))
    ark_conviction_score: Mapped[float | None] = mapped_column(Numeric(10, 4))
    ark_multi_etf_signal: Mapped[bool | None] = mapped_column(Boolean)

    # ── ARK Temporal Features ────────────────────────────────────────
    ark_increase_days_10d: Mapped[int | None] = mapped_column(Integer)
    ark_increase_days_20d: Mapped[int | None] = mapped_column(Integer)
    ark_conviction_streak: Mapped[int | None] = mapped_column(Integer)
    ark_weight_trend_20d: Mapped[float | None] = mapped_column(Numeric(10, 6))

    # ── Insider Features (point-in-time) ─────────────────────────────
    insider_net_buy_count_30d: Mapped[int | None] = mapped_column(Integer)
    insider_buy_value_30d: Mapped[float | None] = mapped_column(Numeric(20, 2))
    insider_cluster_active: Mapped[bool | None] = mapped_column(Boolean)
    insider_cluster_score: Mapped[float | None] = mapped_column(Numeric(10, 4))

    # ── Insider Temporal Features ────────────────────────────────────
    cluster_count_30d: Mapped[int | None] = mapped_column(Integer)
    cluster_count_60d: Mapped[int | None] = mapped_column(Integer)
    cluster_score_sum_60d: Mapped[float | None] = mapped_column(Numeric(10, 4))
    days_since_last_cluster: Mapped[int | None] = mapped_column(Integer)
    insider_buy_ratio_30d: Mapped[float | None] = mapped_column(Numeric(6, 4))  # Sprint 9.5b E2
    insider_buy_ratio_90d: Mapped[float | None] = mapped_column(Numeric(6, 4))  # Sprint 9.5b E2

    # ── Analyst Features (point-in-time) ─────────────────────────────
    analyst_rating_score: Mapped[float | None] = mapped_column(Numeric(10, 4))
    analyst_upgrades_30d: Mapped[int | None] = mapped_column(Integer)
    analyst_price_target_upside: Mapped[float | None] = mapped_column(
        Numeric(10, 4)
    )

    # ── Analyst Temporal Features ────────────────────────────────────
    analyst_downgrades_30d: Mapped[int | None] = mapped_column(Integer)
    analyst_net_sentiment_30d: Mapped[int | None] = mapped_column(Integer)
    analyst_net_sentiment_60d: Mapped[int | None] = mapped_column(Integer)
    analyst_upgrade_streak: Mapped[int | None] = mapped_column(Integer)

    # ── Politician Features (dual-date variants) ─────────────────────
    # Disclosure-based (when information became public)
    politician_buy_count_60d_disclosure: Mapped[int | None] = mapped_column(
        Integer
    )
    politician_distinct_90d_disclosure: Mapped[int | None] = mapped_column(
        Integer
    )
    # Transaction-based (when trade actually occurred)
    politician_buy_count_60d_transaction: Mapped[int | None] = mapped_column(
        Integer
    )
    politician_distinct_90d_transaction: Mapped[int | None] = mapped_column(
        Integer
    )

    # ── 13F Features ─────────────────────────────────────────────────
    form13f_top_holder_count: Mapped[int | None] = mapped_column(Integer)
    form13f_new_positions_count: Mapped[int | None] = mapped_column(Integer)
    form13f_exited_positions_count: Mapped[int | None] = mapped_column(Integer)  # Sprint 9.5b E1
    form13f_holder_delta_qoq: Mapped[float | None] = mapped_column(Numeric(10, 4))  # Sprint 9.5b E1

    # ── Fundamentals (point-in-time) ─────────────────────────────────
    pe_ratio: Mapped[float | None] = mapped_column(Numeric(16, 4))
    forward_pe: Mapped[float | None] = mapped_column(Numeric(16, 4))
    ps_ratio: Mapped[float | None] = mapped_column(Numeric(16, 4))
    revenue_growth_yoy: Mapped[float | None] = mapped_column(Numeric(10, 6))
    profit_margin: Mapped[float | None] = mapped_column(Numeric(10, 6))
    debt_to_equity: Mapped[float | None] = mapped_column(Numeric(16, 4))

    # ── Fundamentals Temporal Features ───────────────────────────────
    pe_trend_4w: Mapped[float | None] = mapped_column(Numeric(10, 6))
    margin_trend_4w: Mapped[float | None] = mapped_column(Numeric(10, 6))

    # ── Technical Indicators ─────────────────────────────────────────
    price_vs_sma50: Mapped[float | None] = mapped_column(Numeric(10, 4))
    price_vs_sma200: Mapped[float | None] = mapped_column(Numeric(10, 4))
    rsi_14: Mapped[float | None] = mapped_column(Numeric(10, 4))
    relative_strength_spy: Mapped[float | None] = mapped_column(Numeric(10, 4))
    volume_ratio_20d: Mapped[float | None] = mapped_column(Numeric(10, 4))
    atr_14_pct: Mapped[float | None] = mapped_column(Numeric(10, 4))

    # ── Earnings Features ─────────────────────────────────────────────
    earnings_days_until: Mapped[int | None] = mapped_column(Integer)
    consecutive_beats: Mapped[int | None] = mapped_column(Integer)
    surprise_trend_3q: Mapped[float | None] = mapped_column(Numeric(10, 4))
    sue_last: Mapped[float | None] = mapped_column(Numeric(10, 4))  # Sprint 9.5b B2
    days_since_last_earnings: Mapped[int | None] = mapped_column(Integer)  # Sprint 9.5b B2

    # ── Sentiment Features (news-based, Sprint 8c) ───────────────────
    sentiment_avg_7d: Mapped[float | None] = mapped_column(Numeric(6, 4))
    sentiment_avg_30d: Mapped[float | None] = mapped_column(Numeric(6, 4))
    sentiment_momentum: Mapped[float | None] = mapped_column(Numeric(6, 4))
    sentiment_neg_count_7d: Mapped[int | None] = mapped_column(Integer)
    sentiment_article_count_7d: Mapped[int | None] = mapped_column(Integer)
    market_sentiment_7d: Mapped[float | None] = mapped_column(Numeric(6, 4))
    news_volume_ratio_7d: Mapped[float | None] = mapped_column(Numeric(10, 4))  # Sprint 9.5b E3

    # ── Liquidity Features (Sprint 9.5b E4) ──────────────────────────
    dollar_volume_20d: Mapped[float | None] = mapped_column(Numeric(20, 0))
    amihud_illiquidity_20d: Mapped[float | None] = mapped_column(Numeric(16, 6))

    # ── Macro Features (market-wide, Sprint 9.5b) ────────────────────
    macro_yield_spread: Mapped[float | None] = mapped_column(Numeric(10, 4))
    macro_vix: Mapped[float | None] = mapped_column(Numeric(10, 2))
    macro_vix_regime: Mapped[int | None] = mapped_column(Integer)
    macro_hy_spread: Mapped[float | None] = mapped_column(Numeric(10, 4))
    macro_dollar_index: Mapped[float | None] = mapped_column(Numeric(10, 2))
    macro_inflation_expectation: Mapped[float | None] = mapped_column(Numeric(10, 4))

    # ── Market Breadth Features (Sprint 9.5b D2) ─────────────────────
    breadth_advance_decline: Mapped[float | None] = mapped_column(Numeric(6, 4))
    breadth_pct_above_sma50: Mapped[float | None] = mapped_column(Numeric(6, 4))

    # ── Sector-Relative Features (Sprint 9.5b B4) ────────────────────
    sector_relative_return_20d: Mapped[float | None] = mapped_column(Numeric(10, 6))
    sector_relative_momentum: Mapped[float | None] = mapped_column(Numeric(10, 6))

    # ── Short Interest Features (Sprint 9.5c B5) ─────────────────────
    short_volume_ratio_5d: Mapped[float | None] = mapped_column(Numeric(10, 4))
    short_volume_ratio_20d: Mapped[float | None] = mapped_column(Numeric(10, 4))
    short_volume_change_20d: Mapped[float | None] = mapped_column(Numeric(10, 4))

    # ── Options IV Features (Sprint 9.5b D3 → feature integration) ────
    options_iv_atm_30d: Mapped[float | None] = mapped_column(Numeric(8, 4))
    options_iv_skew_25d: Mapped[float | None] = mapped_column(Numeric(8, 4))
    options_iv_term_slope: Mapped[float | None] = mapped_column(Numeric(8, 4))
    options_iv_put_call_oi: Mapped[float | None] = mapped_column(Numeric(8, 4))

    # ── Estimates Features (Sprint 9.5a B1 → feature integration) ─────
    eps_revision_pct_30d: Mapped[float | None] = mapped_column(Numeric(10, 6))
    eps_revision_pct_90d: Mapped[float | None] = mapped_column(Numeric(10, 6))
    revenue_revision_pct_30d: Mapped[float | None] = mapped_column(Numeric(10, 6))
    eps_revisions_net_7d: Mapped[int | None] = mapped_column(Integer)
    eps_revisions_net_30d: Mapped[int | None] = mapped_column(Integer)

    # ── Target Variables (backfilled retrospectively) ────────────────
    return_1d: Mapped[float | None] = mapped_column(Numeric(10, 6))
    return_5d: Mapped[float | None] = mapped_column(Numeric(10, 6))
    return_20d: Mapped[float | None] = mapped_column(Numeric(10, 6))
    return_60d: Mapped[float | None] = mapped_column(Numeric(10, 6))

    # ── Metadata ─────────────────────────────────────────────────────
    computed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<FeatureSnapshot(date={self.snapshot_date}, "
            f"ticker={self.ticker!r}, "
            f"ark_score={self.ark_conviction_score}, "
            f"insider_active={self.insider_cluster_active})>"
        )
