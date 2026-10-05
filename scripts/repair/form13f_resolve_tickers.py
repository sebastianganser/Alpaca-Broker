"""Resolve 13F CUSIPs to tickers (and optionally back-fill 13F history).

Why: the 13F collector stored only CUSIPs (``ticker`` always NULL), so all
13F features were empty. This script

  1. (optional, ``--backfill``) fetches the 13F filings of every tracked
     filer back to ``data_start_date()`` – filer by filer and in yearly
     filing-date windows to bound memory. SEC's submissions API only lists
     the most recent ~1000 filings per filer, so very active filers may not
     reach back the full window.
  2. resolves all distinct CUSIPs without ticker via ``CusipResolver``
     (cache → ARK holdings/universe → OpenFIGI) and
  3. fills ``form13f_holdings.ticker`` from ``cusip_map``.

Default is a DRY RUN that only prints statistics. Afterwards rebuild the
feature snapshots (13F columns):
    features_rebuild.py --apply --skip-ta --skip-clusters

Usage (inside the container):
    .venv/bin/python scripts/repair/form13f_resolve_tickers.py              # stats
    .venv/bin/python scripts/repair/form13f_resolve_tickers.py --apply
    .venv/bin/python scripts/repair/form13f_resolve_tickers.py --apply --backfill
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from trading_signals.utils.logging import get_logger, setup_logging

logger = get_logger("repair.form13f_tickers")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="Write to the DB (default: dry run)")
    p.add_argument(
        "--backfill", action="store_true",
        help="First fetch 13F history back to data_start_date() (slow, SEC rate limit)",
    )
    p.add_argument(
        "--window-days", type=int, default=365,
        help="Filing-date window per backfill step (memory bound)",
    )
    p.add_argument("--no-api", action="store_true", help="Skip OpenFIGI (local sources only)")
    return p.parse_args(argv)


def backfill_windows(start: date, end: date, days: int) -> list[tuple[date, date]]:
    """Consecutive [from, until] filing-date windows covering start..end."""
    days = max(1, days)
    out: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        until = min(cur + timedelta(days=days - 1), end)
        out.append((cur, until))
        cur = until + timedelta(days=1)
    return out


def print_stats(session) -> None:
    from sqlalchemy import text

    total, with_ticker, cusips_open = session.execute(text("""
        SELECT count(*), count(ticker),
               count(DISTINCT cusip) FILTER (WHERE ticker IS NULL)
        FROM signals.form13f_holdings
    """)).one()
    logger.info(
        f"form13f_holdings: {total} rows, {with_ticker} with ticker, "
        f"{cusips_open} distinct CUSIPs without ticker"
    )
    for period, rows, filers, tick in session.execute(text("""
        SELECT report_period, count(*), count(DISTINCT filer_cik), count(ticker)
        FROM signals.form13f_holdings
        GROUP BY report_period ORDER BY report_period
    """)).all():
        logger.info(f"  period {period}: {rows} rows, {filers} filers, {tick} with ticker")
    try:
        n_map, n_pos = session.execute(text(
            "SELECT count(*), count(ticker) FROM signals.cusip_map"
        )).one()
        logger.info(f"cusip_map: {n_map} CUSIPs cached, {n_pos} with ticker")
    except Exception as e:  # migration 032 missing
        logger.warning(f"cusip_map not available: {e}")
        session.rollback()


def run_backfill(window_days: int) -> None:
    from trading_signals.collectors.cusip_resolver import CusipResolver
    from trading_signals.collectors.form13f_collector import (
        TOP_FILERS,
        Form13FCollector,
    )
    from trading_signals.db.session import get_session
    from trading_signals.utils.retention import data_start_date

    today = date.today()
    start = data_start_date()
    lookback = (today - start).days + 1
    resolver = CusipResolver()
    windows = backfill_windows(start, today, window_days)
    for n, (cik, name) in enumerate(TOP_FILERS.items(), 1):
        for _, until in windows:
            collector = Form13FCollector(
                lookback_days=lookback, filers={cik: name},
                cusip_resolver=resolver, until_date=until,
            )
            with get_session() as session:
                data = collector.fetch(session)
            with get_session() as session:
                fetched, written = collector.store(session, data)
            logger.info(
                f"[{n}/{len(TOP_FILERS)}] {name} filed ≤ {until}: "
                f"{fetched} lines, {written} rows written"
            )
            del data


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")

    from sqlalchemy import text

    from trading_signals.collectors.cusip_resolver import CusipResolver
    from trading_signals.db.session import get_session

    with get_session() as session:
        print_stats(session)

    if not args.apply:
        logger.info("DRY RUN – use --apply (optionally --backfill) to write.")
        return 0

    if args.backfill:
        run_backfill(args.window_days)

    resolver = CusipResolver()
    with get_session() as session:
        cusips = [r[0] for r in session.execute(text(
            "SELECT DISTINCT cusip FROM signals.form13f_holdings "
            "WHERE ticker IS NULL AND cusip IS NOT NULL"
        )).all()]
        logger.info(
            f"Resolving {len(cusips)} CUSIPs "
            f"(OpenFIGI {'off' if args.no_api else 'on'}, "
            f"key {'set' if resolver.api_key else 'not set'})"
        )
        resolver.resolve(session, cusips, use_api=not args.no_api)
    with get_session() as session:
        resolver.apply_to_holdings(session)
    with get_session() as session:
        print_stats(session)
    logger.info(
        "Done. Next: features_rebuild.py --apply --skip-ta --skip-clusters"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
