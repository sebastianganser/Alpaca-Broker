"""Initial backfill of FINRA short interest (settlement-date positions).

Runs ``ShortInterestPositionsCollector`` once, exactly like the weekly
``short_interest_positions_collector`` job: lists the settlement dates of the
reference ticker, then fetches every date inside the retention window
(``data_start_date()``) that is not stored yet, plus the two most recent
stored dates (FINRA revisions). One bulk request per date returns all tickers;
rows are filtered to the universe (incl. inactive tickers) and upserted.

The initial run needs ~120 requests (one per settlement date since the start
of the retention window) at 12 s each (free tier: 5 calls/min), i.e. ~25
minutes. All rows are written at the end, so let the run finish (no deploy or
restart meanwhile). Re-running is safe: rows are upserted on
(ticker, settlement_date, source) and already stored dates are skipped.

Use ``--refetch-all`` to fetch every date again, e.g. so tickers that joined
the universe later get their history (another ~25 minutes, still an upsert).

Usage (inside the container / with .env configured):
    uv run python scripts/repair/collect_short_interest.py
    uv run python scripts/repair/collect_short_interest.py --refetch-all

Writes one ``collection_log`` row (collector ``short_interest_positions``).
"""

from __future__ import annotations

import argparse
import sys

from trading_signals.utils import job_status
from trading_signals.utils.logging import setup_logging


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--refetch-all",
        action="store_true",
        help="Fetch every settlement date in the retention window again "
        "(default: missing dates + the 2 most recent stored ones)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")

    from trading_signals.collectors.short_interest_positions_collector import (
        ShortInterestPositionsCollector,
    )

    collector = ShortInterestPositionsCollector(refetch_all=args.refetch_all)
    log = collector.run()

    print(
        f"collection_log #{log.id}: {log.collector_name} status={log.status} "
        f"fetched={log.records_fetched} written={log.records_written} "
        f"notes={log.notes!r}"
    )
    return 0 if log.status in (job_status.SUCCESS, job_status.PARTIAL) else 1


if __name__ == "__main__":
    sys.exit(main())
