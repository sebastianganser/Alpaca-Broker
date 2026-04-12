"""Validate universe against Alpaca and deactivate non-tradeable tickers.

This script checks every active ticker in our universe against Alpaca's
asset list. Tickers that are not found or not tradeable get deactivated.

Usage:
    uv run python scripts/validate_universe.py
    uv run python scripts/validate_universe.py --dry-run   # Only check, don't deactivate
"""

import argparse
import sys

from trading_signals.db.session import get_session
from trading_signals.universe.alpaca_validator import AlpacaAssetValidator
from trading_signals.universe.manager import UniverseManager
from trading_signals.utils.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)


def main(dry_run: bool = False) -> None:
    logger.info("=" * 60)
    logger.info("Universe Validation against Alpaca Assets")
    logger.info("=" * 60)

    with get_session() as session:
        # Get all active tickers
        manager = UniverseManager(session)
        active = manager.get_active_tickers()
        tickers = [t.ticker for t in active]
        logger.info(f"Active tickers in universe: {len(tickers)}")

        # Validate against Alpaca
        validator = AlpacaAssetValidator()
        result = validator.validate_tickers(tickers)

        # Report
        print(f"\n{'='*60}")
        print(f"VALIDATION RESULT")
        print(f"{'='*60}")
        print(f"Total checked:     {result.total_checked}")
        print(f"Active/Tradeable:  {result.active_tradeable}")
        print(f"Not found:         {len(result.not_found)}")
        print(f"Not tradeable:     {len(result.not_tradeable)}")

        if result.not_found:
            print(f"\nTicker NOT FOUND in Alpaca:")
            for ticker in result.not_found:
                print(f"  - {ticker}")

        if result.not_tradeable:
            print(f"\nTicker NOT TRADEABLE on Alpaca:")
            for ticker in result.not_tradeable:
                detail = result.details[ticker]
                print(f"  - {ticker} (exchange: {detail.get('exchange', '?')})")

        # Deactivate if not dry-run
        to_deactivate = result.not_found + result.not_tradeable
        if to_deactivate:
            if dry_run:
                print(f"\nDRY RUN: Would deactivate {len(to_deactivate)} tickers")
            else:
                print(f"\nDeactivating {len(to_deactivate)} tickers...")
                for ticker in to_deactivate:
                    manager.deactivate_ticker(ticker)
                    logger.info(f"  Deactivated: {ticker}")
                print(f"Done. {len(to_deactivate)} tickers deactivated.")
        else:
            print("\nAll tickers are tradeable on Alpaca!")

        # Show enrichment opportunity
        tradeable_details = {
            t: d for t, d in result.details.items()
            if d.get("status") == "active"
        }
        missing_names = [
            t for t in active
            if t.ticker in tradeable_details
            and not t.company_name
            and tradeable_details[t.ticker].get("name")
        ]
        if missing_names:
            print(f"\n{len(missing_names)} tickers could be enriched with Alpaca company names")

    print(f"\nActive tickers after validation: {result.active_tradeable}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate universe against Alpaca")
    parser.add_argument("--dry-run", action="store_true", help="Only check, don't deactivate")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
