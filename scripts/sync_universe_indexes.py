"""Sync S&P 500 and Nasdaq 100 index membership into the universe.

Usage:
    uv run python scripts/sync_universe_indexes.py
    uv run python scripts/sync_universe_indexes.py --dry-run
"""

import argparse

from trading_signals.db.session import get_session
from trading_signals.universe.index_sync import IndexSyncer
from trading_signals.utils.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)


def main(dry_run: bool = False) -> None:
    logger.info("=" * 60)
    logger.info("Index Universe Sync (S&P 500 + Nasdaq 100)")
    logger.info("=" * 60)

    with get_session() as session:
        syncer = IndexSyncer(session)
        result = syncer.sync(dry_run=dry_run)

        print(f"\n{'='*60}")
        print("SYNC RESULT")
        print(f"{'='*60}")
        print(f"S&P 500 tickers:       {result.sp500_count}")
        print(f"Nasdaq 100 tickers:    {result.nasdaq100_count}")
        print(f"Already in universe:   {result.already_existed}")
        print(f"Newly added:           {result.newly_added}")
        print(f"Not tradeable (skip):  {result.not_tradeable}")
        print(f"Memberships updated:   {result.membership_updated}")

        if result.new_tickers:
            print(f"\nNew tickers added ({len(result.new_tickers)}):")
            for t in result.new_tickers[:20]:
                print(f"  + {t}")
            if len(result.new_tickers) > 20:
                print(f"  ... and {len(result.new_tickers) - 20} more")

        if dry_run:
            print("\nDRY RUN - no changes were made")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync index membership")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
