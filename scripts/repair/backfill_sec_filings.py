"""Initial backfill of SEC filings (``signals.sec_filings`` / ``sec_filing_texts``).

Runs ``SecFilingsCollector`` once with a longer lookback (default 120 days,
never before ``data_start_date()``), exactly like the daily
``sec_filings_collector`` job: submissions JSON of every active universe
company, whitelisted forms (``collectors/sec_filings_collector.FORMS``), press
release / 8-K body excerpts for 8-Ks with results or event items.

**Dry run (default):** read-only database session, SEC GET requests for the
submissions only (no filing text downloads, nothing is written). Prints the new
filings per form, the 8-K items, how many text requests an ``--apply`` run
would make and a time estimate.

**``--apply``:** runs the collector for real (one ``collection_log`` row,
collector ``sec_filings``). Every row is written at the end, so let the run
finish; it is safe to repeat (unique accession number, text upsert). Needs
Sebastian's OK on production.

Request volume: 1 + ~650 submissions (dry run) + ~1 request per 8-K text on top
for ``--apply`` (120 days: roughly 2,000-4,000), at <= 9 requests/s
(SEC fair access: 10/s) -> a dry run takes ~2-5 minutes, ``--apply`` ~15-40
minutes. Run it detached on Unraid (``alpaca-ops run-bg``), outside 22:15-05:15
and not at Sunday 14:00 (``sec_financials_collector``), 04:00
(``sec_filings_collector``) or during the seal runs.

Usage (inside the container / with .env configured)::

    PY=.venv/bin/python
    $PY scripts/repair/backfill_sec_filings.py                 # dry run
    $PY scripts/repair/backfill_sec_filings.py --apply
    $PY scripts/repair/backfill_sec_filings.py --days 30 --tickers AAPL,MSFT --apply
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date, timedelta

from trading_signals.utils import job_status
from trading_signals.utils.logging import setup_logging
from trading_signals.utils.repair_support import (
    announce_db_target,
    out,
    repair_session,
)

#: Seconds per request used for the time estimate (rate limit + transfer).
SECONDS_PER_REQUEST = 0.35


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="write (default: dry run)")
    p.add_argument(
        "--days", type=int, default=120, help="lookback window in days (default 120)"
    )
    p.add_argument(
        "--tickers", help="comma separated universe tickers (default: all active)"
    )
    return p.parse_args(argv)


def summarize(entries: list[dict]) -> list[str]:
    """Human-readable dry-run report of the ``fetch`` result."""
    from trading_signals.collectors.sec_filings_collector import text_plan

    new = [e["filing"] for e in entries if e.get("filing")]
    forms = Counter(f["form"] for f in new)
    items = Counter(i for f in new for i in (f["items"] or []))
    text_requests = sum(
        1
        for f in new
        if any(text_plan(f["form"], f["items"]))
    )
    lines = [f"new filings: {len(new)} (companies: {len({f['cik'] for f in new})})"]
    lines.append("by form:")
    lines += [f"  {form:<16}{n:>7}" for form, n in forms.most_common()]
    lines.append("8-K items (top 15):")
    lines += [f"  {item:<8}{n:>7}" for item, n in items.most_common(15)]
    lines.append(f"text requests an --apply run would make: {text_requests}")
    if new:
        lines.append(
            f"filing dates: {min(f['filing_date'] for f in new)} .. "
            f"{max(f['filing_date'] for f in new)}"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")
    announce_db_target("backfill_sec_filings")

    from sqlalchemy import text

    from trading_signals.collectors.sec_filings_collector import SecFilingsCollector

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else None
    )
    since = date.today() - timedelta(days=args.days)
    out(f"[backfill_sec_filings] lookback {args.days} days (from {since}), "
        f"tickers: {', '.join(tickers) if tickers else 'all active'}, "
        f"mode: {'APPLY' if args.apply else 'dry run'}")

    if args.apply:
        collector = SecFilingsCollector(
            lookback_days=args.days, fetch_texts=True, only_tickers=tickers
        )
        log = collector.run()
        out(
            f"collection_log #{log.id}: {log.collector_name} status={log.status} "
            f"fetched={log.records_fetched} written={log.records_written} "
            f"notes={log.notes!r}"
        )
        return 0 if log.status in (job_status.SUCCESS, job_status.PARTIAL) else 1

    collector = SecFilingsCollector(
        lookback_days=args.days, fetch_texts=False, only_tickers=tickers
    )
    with repair_session(read_only=True) as session:
        # keep the session read-only after the collector's commit as well
        session.execute(text("SET default_transaction_read_only = on"))
        entries = collector.fetch(session)
    for line in summarize(entries):
        out(line)
    n_requests = collector._attempts
    out(f"dry run done: {n_requests} SEC requests, nothing written. "
        f"--apply would need ~{n_requests} + text requests "
        f"(estimate {n_requests * SECONDS_PER_REQUEST / 60:.0f}+ minutes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
