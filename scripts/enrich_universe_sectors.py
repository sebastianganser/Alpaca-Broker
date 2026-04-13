"""Enrich universe with sector and industry data from yfinance.

Finds all active tickers with missing sector/industry data and
enriches them using Yahoo Finance.

Usage:
    uv run python scripts/enrich_universe_sectors.py
    uv run python scripts/enrich_universe_sectors.py --dry-run
"""

import argparse

from sqlalchemy import select, update

from trading_signals.collectors.yfinance_client import YFinanceClient
from trading_signals.db.models.universe import Universe
from trading_signals.db.session import get_session
from trading_signals.utils.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)


def main(dry_run: bool = False) -> None:
    logger.info("=" * 60)
    logger.info("Universe Sector Enrichment (yfinance)")
    logger.info("=" * 60)

    with get_session() as session:
        # Find tickers with missing sector
        stmt = (
            select(Universe.ticker)
            .where(Universe.is_active.is_(True))
            .where(
                (Universe.sector.is_(None)) | (Universe.sector == "")
            )
            .order_by(Universe.ticker)
        )
        missing = [row[0] for row in session.execute(stmt).all()]

        print(f"\nTicker ohne Sektor: {len(missing)}")

        if not missing:
            print("Alle Ticker haben bereits einen Sektor.")
            return

        if dry_run:
            print(f"\nDRY RUN – würde {len(missing)} Ticker enrichen:")
            for t in missing[:20]:
                print(f"  {t}")
            if len(missing) > 20:
                print(f"  ... und {len(missing) - 20} weitere")
            return

        # Fetch sector data
        client = YFinanceClient(
            batch_size=50,
            delay_between_tickers=0.5,
            delay_between_batches=3.0,
        )
        results = client.fetch_sector_info(missing)

        # Update universe
        updated = 0
        for record in results:
            session.execute(
                update(Universe)
                .where(Universe.ticker == record["ticker"])
                .values(
                    sector=record.get("sector"),
                    industry=record.get("industry"),
                )
            )
            updated += 1

        session.commit()

        print(f"\n{'='*60}")
        print("ERGEBNIS")
        print(f"{'='*60}")
        print(f"Ticker ohne Sektor gefunden: {len(missing)}")
        print(f"Sektoren von yfinance erhalten: {len(results)}")
        print(f"Universe-Einträge aktualisiert: {updated}")
        print(f"Nicht gefunden (ETFs, etc.):    {len(missing) - len(results)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Enrich universe with sector/industry data"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
