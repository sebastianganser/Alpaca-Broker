"""Re-download the full price history for all universe tickers (C3 repair).

Why: the price collector used ``adjustment=all`` with ON CONFLICT DO NOTHING
and the IEX feed. After splits/dividends the stored history therefore has
mixed adjustment bases, and IEX closes differ from the consolidated (SIP)
close. This script re-fetches every ticker's history from
``data_start_date()`` (rolling 20-quarter window) via the SIP feed
(``ALPACA_DATA_FEED``), upserts it (ON CONFLICT DO UPDATE on all OHLCV
columns) and finally calls ``recompute_after_price_refresh`` for all
refreshed tickers (TA + forward-return targets).

Default is a DRY RUN that only reports what would happen.

Usage (inside the container / with .env configured):
    uv run python scripts/repair/collectors_refetch_prices.py            # dry run
    uv run python scripts/repair/collectors_refetch_prices.py --apply
    uv run python scripts/repair/collectors_refetch_prices.py --apply \
        --tickers AAPL,MSFT --batch 50 --no-recompute

Each batch of tickers is committed separately so an interruption keeps
the progress made so far; the script is idempotent and can be re-run.
"""

from __future__ import annotations

import argparse
import sys

from trading_signals.utils.logging import get_logger, setup_logging

logger = get_logger("repair.refetch_prices")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--apply", action="store_true", help="Write to the DB (default: dry run)"
    )
    p.add_argument("--tickers", default="", help="Comma separated subset of tickers")
    p.add_argument("--batch", type=int, default=100, help="Tickers per commit")
    p.add_argument(
        "--include-inactive",
        action="store_true",
        help="Also refresh inactive universe tickers (default: active only)",
    )
    p.add_argument(
        "--no-recompute",
        action="store_true",
        help="Skip recompute_after_price_refresh (run it later separately)",
    )
    p.add_argument("--feed", default=None, help="Override ALPACA_DATA_FEED (sip|iex)")
    return p.parse_args(argv)


def select_tickers(session, args: argparse.Namespace) -> list[str]:
    from sqlalchemy import select

    from trading_signals.db.models.universe import Universe

    if args.tickers:
        return sorted({t.strip().upper() for t in args.tickers.split(",") if t.strip()})
    stmt = select(Universe.ticker).order_by(Universe.ticker)
    if not args.include_inactive:
        stmt = stmt.where(Universe.is_active.is_(True))
    return [r[0] for r in session.execute(stmt).all()]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")

    from trading_signals.collectors.prices_alpaca import PriceCollectorAlpaca
    from trading_signals.db.session import get_session
    from trading_signals.utils.retention import data_start_date

    with get_session() as session:
        tickers = select_tickers(session, args)

    collector = PriceCollectorAlpaca(feed=args.feed)
    logger.info(
        f"Refetch full price history for {len(tickers)} tickers from "
        f"{data_start_date()} (feed={collector.feed}, "
        f"recompute={'no' if args.no_recompute else 'yes'})"
    )
    if not args.apply:
        logger.info(f"DRY RUN – first tickers: {tickers[:20]}. Use --apply to write.")
        return 0

    total = 0
    refreshed: list[str] = []
    for i in range(0, len(tickers), max(1, args.batch)):
        batch = tickers[i : i + args.batch]
        with get_session() as session:
            written = collector.refresh_full_history(session, batch, recompute=False)
        total += written
        refreshed.extend(batch)
        logger.info(
            f"Batch {i // args.batch + 1}: {written} rows upserted "
            f"({len(refreshed)}/{len(tickers)} tickers done)"
        )

    if not args.no_recompute and refreshed:
        from trading_signals.derived.recompute import recompute_after_price_refresh

        for i in range(0, len(refreshed), max(1, args.batch)):
            batch = refreshed[i : i + args.batch]
            with get_session() as session:
                stats = recompute_after_price_refresh(session, batch)
            logger.info(f"Recompute batch {i // args.batch + 1}: {stats}")

    logger.info(f"Done: {total} price rows upserted for {len(refreshed)} tickers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
