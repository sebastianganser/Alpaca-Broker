"""Universe Manager – add, deactivate, and query tickers.

The universe grows organically as new signals appear. Once a ticker
is added, it is never deleted – only marked as inactive if it
disappears from all signal sources for an extended period.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.db.models.universe import Universe


class UniverseManager:
    """Manage the dynamic ticker universe."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_ticker(
        self,
        ticker: str,
        company_name: str | None = None,
        added_by: str = "manual",
        exchange: str | None = None,
        sector: str | None = None,
        industry: str | None = None,
    ) -> Universe:
        """Add a ticker to the universe (idempotent).

        If the ticker already exists, updates last_seen and re-activates it.
        """
        stmt = (
            pg_insert(Universe)
            .values(
                ticker=ticker,
                company_name=company_name,
                added_date=date.today(),
                added_by=added_by,
                is_active=True,
                last_seen=date.today(),
                exchange=exchange,
                sector=sector,
                industry=industry,
            )
            .on_conflict_do_update(
                index_elements=["ticker"],
                set_={
                    "last_seen": date.today(),
                    "is_active": True,
                    # Update name only if we have a new one
                    "company_name": company_name or Universe.company_name,
                },
            )
            .returning(Universe)
        )
        result = self.session.execute(stmt)
        return result.scalar_one()

    def add_tickers_bulk(
        self,
        tickers: list[dict],
        added_by: str = "manual",
    ) -> int:
        """Add multiple tickers at once (idempotent).

        Args:
            tickers: List of dicts with at least 'ticker' key.
                     Optional: 'company_name', 'exchange', 'sector', 'industry'.
            added_by: Source identifier (e.g., 'manual', 'ark_etf', 'form4').

        Returns:
            Number of tickers processed.
        """
        today = date.today()
        values = []
        for t in tickers:
            values.append({
                "ticker": t["ticker"],
                "company_name": t.get("company_name"),
                "exchange": t.get("exchange"),
                "sector": t.get("sector"),
                "industry": t.get("industry"),
                "added_date": today,
                "added_by": added_by,
                "is_active": True,
                "last_seen": today,
            })

        if not values:
            return 0

        stmt = (
            pg_insert(Universe)
            .values(values)
            .on_conflict_do_update(
                index_elements=["ticker"],
                set_={
                    "last_seen": today,
                    "is_active": True,
                },
            )
        )
        self.session.execute(stmt)
        return len(values)

    def deactivate_ticker(self, ticker: str) -> None:
        """Mark a ticker as inactive (never delete!)."""
        stmt = select(Universe).where(Universe.ticker == ticker)
        universe_entry = self.session.execute(stmt).scalar_one_or_none()
        if universe_entry:
            universe_entry.is_active = False

    def get_active_tickers(self) -> list[Universe]:
        """Return all active tickers in the universe."""
        stmt = (
            select(Universe)
            .where(Universe.is_active.is_(True))
            .order_by(Universe.ticker)
        )
        return list(self.session.execute(stmt).scalars().all())

    def count_active(self) -> int:
        """Return count of active tickers."""
        from sqlalchemy import func
        stmt = select(func.count()).select_from(Universe).where(
            Universe.is_active.is_(True)
        )
        return self.session.execute(stmt).scalar_one()
