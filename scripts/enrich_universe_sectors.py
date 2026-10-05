"""Enrich universe with sector and industry data from yfinance.

Finds all active tickers with missing sector/industry data and
enriches them using Yahoo Finance.

Uses the shared :func:`trading_signals.scheduler.sector_enrichment.enrich_sectors`
(same logic as the index_sync job and the UI backfill): existing values are
never overwritten with empty ones, non-equity tickers (ETFs, funds) are
blacklisted and deactivated.

Usage:
    uv run python scripts/enrich_universe_sectors.py
    uv run python scripts/enrich_universe_sectors.py --dry-run
"""

import argparse

from sqlalchemy import select

from trading_signals.db.models.universe import Universe
from trading_signals.db.session import get_session
from trading_signals.scheduler.sector_enrichment import enrich_sectors
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
            .where((Universe.sector.is_(None)) | (Universe.sector == ""))
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

    result = enrich_sectors(missing, source="manual_enrichment")

    print(f"\n{'=' * 60}")
    print("ERGEBNIS")
    print(f"{'=' * 60}")
    print(f"Ticker ohne Sektor gefunden:    {result.checked}")
    print(f"Universe-Einträge aktualisiert: {result.enriched}")
    print(f"Blacklisted (kein EQUITY):      {len(result.deactivated)}")
    print(f"Nicht gefunden:                 {result.failed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Enrich universe with sector/industry data"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
