"""ORM model for the signals.ticker_blacklist table.

The blacklist is a learning filter that accumulates tickers identified
as non-equity (ETFs, mutual funds, commodity trusts, etc.) via yfinance
quoteType checks during sector enrichment.

It is checked by the onboarder before adding new tickers to the universe,
providing an ever-growing filter that prevents known non-equities from
re-entering the system.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base


class TickerBlacklist(Base):
    """A ticker that has been identified as non-equity and should not
    be in the universe."""

    __tablename__ = "ticker_blacklist"

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    reason: Mapped[str] = mapped_column(String(50), nullable=False)
    quote_type: Mapped[str | None] = mapped_column(String(20))
    source: Mapped[str | None] = mapped_column(String(50))
    detected_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<TickerBlacklist(ticker='{self.ticker}', "
            f"reason='{self.reason}', quote_type='{self.quote_type}')>"
        )
