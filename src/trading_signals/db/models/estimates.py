"""ORM model for analyst estimates (EPS/Revenue consensus and revisions).

EstimatesSnapshot: Raw layer – daily point-in-time snapshot of consensus
estimates, revision counts, and trend data from yfinance.

This is the most time-critical data source in the system: the 90-day
rolling window at Yahoo means every day of delay costs one day of
irrecoverable history.

Source: yfinance eps_trend, eps_revisions, earnings_estimate, revenue_estimate
Sprint: 9.5a (Data Hardening)
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base


class EstimatesSnapshot(Base):
    """Daily point-in-time snapshot of analyst consensus estimates.

    Captures EPS/Revenue consensus, revision counts, and trend data.
    The rolling 90-day window from Yahoo provides backward-looking
    revisions (7d, 30d, 60d, 90d ago) which are critical for computing
    Revisions Momentum — one of the strongest short-term predictive factors.

    Point-in-Time: as_of records when we fetched the data.
    The period field distinguishes forecast horizons (current quarter,
    next quarter, current year, next year).
    """

    __tablename__ = "estimates_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "ticker", "as_of", "period", "source",
            name="uq_estimates_snapshot_dedup",
        ),
        Index("idx_estimates_ticker", "ticker"),
        Index("idx_estimates_as_of", "as_of"),
        {"schema": "signals"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)  # Fetch date (point-in-time)
    period: Mapped[str] = mapped_column(String(10), nullable=False)  # '0q','+1q','0y','+1y'

    # ── EPS Consensus ────────────────────────────────────────────────
    eps_avg: Mapped[float | None] = mapped_column(Numeric(16, 4))
    eps_low: Mapped[float | None] = mapped_column(Numeric(16, 4))
    eps_high: Mapped[float | None] = mapped_column(Numeric(16, 4))
    eps_n_analysts: Mapped[int | None] = mapped_column(Integer)
    eps_year_ago: Mapped[float | None] = mapped_column(Numeric(16, 4))
    eps_growth: Mapped[float | None] = mapped_column(Numeric(10, 6))

    # ── EPS Trend (rolling 90-day window) ────────────────────────────
    # These are the consensus EPS values as they stood N days ago.
    # The difference between today and N days ago IS the revision signal.
    eps_current: Mapped[float | None] = mapped_column(Numeric(16, 4))
    eps_7d_ago: Mapped[float | None] = mapped_column(Numeric(16, 4))
    eps_30d_ago: Mapped[float | None] = mapped_column(Numeric(16, 4))
    eps_60d_ago: Mapped[float | None] = mapped_column(Numeric(16, 4))
    eps_90d_ago: Mapped[float | None] = mapped_column(Numeric(16, 4))

    # ── Revision Counts ──────────────────────────────────────────────
    # Number of analysts who revised their estimates up/down
    rev_up_7d: Mapped[int | None] = mapped_column(Integer)
    rev_up_30d: Mapped[int | None] = mapped_column(Integer)
    rev_down_7d: Mapped[int | None] = mapped_column(Integer)
    rev_down_30d: Mapped[int | None] = mapped_column(Integer)

    # ── Revenue Consensus ────────────────────────────────────────────
    revenue_avg: Mapped[float | None] = mapped_column(Numeric(20, 2))
    revenue_low: Mapped[float | None] = mapped_column(Numeric(20, 2))
    revenue_high: Mapped[float | None] = mapped_column(Numeric(20, 2))
    revenue_n_analysts: Mapped[int | None] = mapped_column(Integer)
    revenue_year_ago: Mapped[float | None] = mapped_column(Numeric(20, 2))
    revenue_growth: Mapped[float | None] = mapped_column(Numeric(10, 6))

    # ── Metadata ─────────────────────────────────────────────────────
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="yfinance")
    raw: Mapped[dict | None] = mapped_column(JSONB)  # Complete raw response for future-proofing
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<EstimatesSnapshot(ticker={self.ticker!r}, "
            f"as_of={self.as_of}, period={self.period!r}, "
            f"eps_avg={self.eps_avg})>"
        )
