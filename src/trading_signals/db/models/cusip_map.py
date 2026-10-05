"""ORM model for the CUSIP → ticker lookup cache.

13F infotables identify securities only by CUSIP. ``CusipMap`` caches the
resolved ticker per CUSIP (from local sources such as ARK holdings or from
the OpenFIGI mapping API) so every CUSIP is looked up only once. Rows with
``ticker`` NULL are negative results; they are retried after
``cusip_resolver.NEGATIVE_TTL_DAYS``.

Lookup table without a date dimension → not part of the data-retention job.
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base


class CusipMap(Base):
    """Resolved ticker for one 9-character CUSIP."""

    __tablename__ = "cusip_map"
    __table_args__ = (Index("ix_cusip_map_ticker", "ticker"),)

    cusip: Mapped[str] = mapped_column(String(12), primary_key=True)
    ticker: Mapped[str | None] = mapped_column(String(20))
    name: Mapped[str | None] = mapped_column(String(200))
    exch_code: Mapped[str | None] = mapped_column(String(10))
    security_type: Mapped[str | None] = mapped_column(String(50))
    # 'ark_holdings' | 'universe' | 'openfigi'
    source: Mapped[str] = mapped_column(String(20))
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<CusipMap({self.cusip!r} → {self.ticker!r}, {self.source})>"
