"""TechnicalIndicator ORM model – derived technical analysis indicators.

Stores one row per ticker per trading day with all computed TA indicators.
Part of the Derived Layer (recomputable from prices_daily).

Indicators computed by TechnicalIndicatorsComputer (Sprint 6):
  - SMA 20/50/200, EMA 12/26
  - RSI 14, MACD (line/signal/histogram)
  - Bollinger Bands (upper/lower), ATR 14
  - Volume SMA 20, Relative Strength vs SPY
"""

from datetime import date

from sqlalchemy import (
    Date,
    ForeignKey,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base


class TechnicalIndicator(Base):
    """Technical analysis indicators for a ticker on a given trading day."""

    __tablename__ = "technical_indicators"

    ticker: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("signals.universe.ticker"),
        primary_key=True,
    )
    trade_date: Mapped[date] = mapped_column(
        Date,
        primary_key=True,
    )

    # Moving Averages
    sma_20: Mapped[float | None] = mapped_column(Numeric(16, 4))
    sma_50: Mapped[float | None] = mapped_column(Numeric(16, 4))
    sma_200: Mapped[float | None] = mapped_column(Numeric(16, 4))
    ema_12: Mapped[float | None] = mapped_column(Numeric(16, 4))
    ema_26: Mapped[float | None] = mapped_column(Numeric(16, 4))

    # Momentum
    rsi_14: Mapped[float | None] = mapped_column(Numeric(10, 4))
    macd: Mapped[float | None] = mapped_column(Numeric(16, 4))
    macd_signal: Mapped[float | None] = mapped_column(Numeric(16, 4))
    macd_histogram: Mapped[float | None] = mapped_column(Numeric(16, 4))

    # Volatility
    bollinger_upper: Mapped[float | None] = mapped_column(Numeric(16, 4))
    bollinger_lower: Mapped[float | None] = mapped_column(Numeric(16, 4))
    atr_14: Mapped[float | None] = mapped_column(Numeric(16, 4))

    # Volume
    volume_sma_20: Mapped[float | None] = mapped_column(Numeric(20, 2))

    # Relative Performance
    relative_strength_spy: Mapped[float | None] = mapped_column(Numeric(10, 4))

    def __repr__(self) -> str:
        return (
            f"<TechnicalIndicator(ticker={self.ticker!r}, "
            f"date={self.trade_date}, rsi={self.rsi_14})>"
        )
