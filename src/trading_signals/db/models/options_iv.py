"""ORM model for the signals.options_iv_snapshot table.

Stores daily implied volatility snapshots per ticker, collected from
Alpaca Options API. Used to compute IV-Rank (needs 1 year of data),
IV-Percentile, Put/Call Skew, and Term Structure features.

Sprint 9.5b D3.
"""

from datetime import date

from sqlalchemy import Date, Index, Numeric, BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base


class OptionsIVSnapshot(Base):
    """Daily implied volatility snapshot for a ticker."""

    __tablename__ = "options_iv_snapshot"

    # Primary key: ticker + snapshot_date
    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)

    # ATM implied volatility (annualized, nearest monthly expiry)
    atm_iv_30d: Mapped[float | None] = mapped_column(Numeric(8, 4))
    # ATM IV for ~60d expiry (for term structure)
    atm_iv_60d: Mapped[float | None] = mapped_column(Numeric(8, 4))

    # Put/Call skew: IV of 25-delta put minus IV of 25-delta call
    # Positive = puts more expensive (fear/hedging)
    skew_25d: Mapped[float | None] = mapped_column(Numeric(8, 4))

    # Term structure slope: atm_iv_60d - atm_iv_30d
    # Negative = backwardation (near-term stress)
    term_slope: Mapped[float | None] = mapped_column(Numeric(8, 4))

    # Open interest totals
    total_oi_call: Mapped[int | None] = mapped_column(BigInteger)
    total_oi_put: Mapped[int | None] = mapped_column(BigInteger)

    # Put/Call OI ratio
    put_call_oi: Mapped[float | None] = mapped_column(Numeric(8, 4))

    __table_args__ = (
        Index("idx_options_iv_ticker_date", "ticker", "snapshot_date"),
    )

    def __repr__(self) -> str:
        return (
            f"<OptionsIVSnapshot(ticker='{self.ticker}', "
            f"date={self.snapshot_date}, atm_iv={self.atm_iv_30d})>"
        )
