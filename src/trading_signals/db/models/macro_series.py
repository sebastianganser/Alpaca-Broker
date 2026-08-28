"""ORM model for macroeconomic time series data.

MacroSeries: Daily observations from FRED (Federal Reserve Economic Data)
for key macroeconomic indicators used as contextual features.

Indicators tracked:
- DGS2/DGS10: Treasury yields (yield curve / recession indicator)
- BAMLH0A0HYM2: High Yield OAS (credit risk appetite)
- VIXCLS: VIX (volatility regime)
- DTWEXBGS: Dollar Index (macro headwind)
- T10YIE: Breakeven Inflation (valuation pressure)

Source: FRED API (https://fred.stlouisfed.org)
Sprint: 9.5b (Data Extension)
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base


class MacroSeries(Base):
    """Daily macro indicator observation from FRED.

    Each row stores one observation for one series on one date.
    The as_of field records when the data was fetched (point-in-time),
    which is important because FRED sometimes revises values retroactively.
    """

    __tablename__ = "macro_series"
    __table_args__ = (
        UniqueConstraint(
            "series_id", "obs_date",
            name="uq_macro_series_dedup",
        ),
        Index("idx_macro_series_id", "series_id"),
        Index("idx_macro_obs_date", "obs_date"),
        {"schema": "signals"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    series_id: Mapped[str] = mapped_column(String(30), nullable=False)
    obs_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[float | None] = mapped_column(Numeric(16, 6))
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="fred")
    as_of: Mapped[date] = mapped_column(Date, nullable=False)  # When we fetched it
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<MacroSeries({self.series_id!r}, "
            f"date={self.obs_date}, value={self.value})>"
        )
