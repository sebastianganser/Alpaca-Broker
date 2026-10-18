"""Initial backfill of SEC XBRL quarterly financials (``signals.financials_quarterly``).

Runs ``SecFinancialsCollector`` once, exactly like the weekly
``sec_financials_collector`` job: the XBRL ``companyfacts`` JSON of every active
universe company is turned into standalone fiscal quarters (revenue, gross
profit, operating income, net income, diluted EPS, operating cash flow, capex,
free cash flow, cash, debt, shares; Q4 = FY - 9M; values as first reported)
for the quarters inside the retention window (``data_start_date()``). The
collector always reads the full fact history, so the weekly job IS the
backfill - this script only adds a dry run and the ``--tickers`` filter.

**Dry run (default):** read-only database session, SEC GET requests
(``companyfacts``, nothing is written). Prints rows per ticker group, the date
range and the coverage of every figure, plus the quarters of a few sample
tickers.

**``--apply``:** runs the collector for real (one ``collection_log`` row,
collector ``sec_financials``). Upsert on (ticker, period_end), safe to repeat.
Needs Sebastian's OK on production.

Request volume: 1 + ~650 ``companyfacts`` requests (0.3-0.5 MB each) at
<= 9 requests/s (SEC fair access: 10/s) -> ~5-10 minutes, ~250 MB. Run it
detached on Unraid (``alpaca-ops run-bg``), outside 22:15-05:15 and not at
Sunday 14:00 / 04:00 (the scheduled SEC jobs).

Usage (inside the container / with .env configured)::

    PY=.venv/bin/python
    $PY scripts/repair/backfill_sec_financials.py                      # dry run
    $PY scripts/repair/backfill_sec_financials.py --apply
    $PY scripts/repair/backfill_sec_financials.py --tickers AAPL,WMT,JPM --apply
"""

from __future__ import annotations

import argparse
import sys

from trading_signals.collectors.sec_xbrl import VALUE_COLUMNS
from trading_signals.utils import job_status
from trading_signals.utils.logging import setup_logging
from trading_signals.utils.repair_support import (
    announce_db_target,
    out,
    repair_session,
)

SAMPLE_TICKERS = ("AAPL", "MSFT", "WMT", "JPM")
SECONDS_PER_REQUEST = 0.5


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="write (default: dry run)")
    p.add_argument(
        "--tickers", help="comma separated universe tickers (default: all active)"
    )
    return p.parse_args(argv)


def summarize(rows: list[dict]) -> list[str]:
    """Human-readable dry-run report of the ``fetch`` result."""
    tickers = sorted({r["ticker"] for r in rows})
    lines = [f"rows: {len(rows)} for {len(tickers)} tickers"]
    if not rows:
        return lines
    lines.append(
        f"period_end: {min(r['period_end'] for r in rows)} .. "
        f"{max(r['period_end'] for r in rows)}; "
        f"filed: {min(r['filed'] for r in rows)} .. {max(r['filed'] for r in rows)}"
    )
    lines.append("coverage of the figures (share of rows with a value):")
    for column in VALUE_COLUMNS:
        n = sum(1 for r in rows if r.get(column) is not None)
        lines.append(f"  {column:<22}{n:>7}  {100 * n / len(rows):5.1f} %")
    for ticker in SAMPLE_TICKERS:
        own = [r for r in rows if r["ticker"] == ticker][-4:]
        if own:
            lines.append(f"last quarters of {ticker}:")
            for r in own:
                lines.append(
                    f"  {r['period_end']} {r['fiscal_year']}{r['fiscal_period']} "
                    f"filed {r['filed']} revenue={r['revenue']} "
                    f"net_income={r['net_income']} fcf={r['free_cash_flow']}"
                )
    return lines


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")
    announce_db_target("backfill_sec_financials")

    from sqlalchemy import text

    from trading_signals.collectors.sec_financials_collector import (
        SecFinancialsCollector,
    )

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else None
    )
    scope = ", ".join(tickers) if tickers else "all active"
    out(
        f"[backfill_sec_financials] tickers: {scope}, "
        f"mode: {'APPLY' if args.apply else 'dry run'}"
    )
    collector = SecFinancialsCollector(only_tickers=tickers)

    if args.apply:
        log = collector.run()
        out(
            f"collection_log #{log.id}: {log.collector_name} status={log.status} "
            f"fetched={log.records_fetched} written={log.records_written} "
            f"notes={log.notes!r}"
        )
        return 0 if log.status in (job_status.SUCCESS, job_status.PARTIAL) else 1

    with repair_session(read_only=True) as session:
        # keep the session read-only after the collector's commit as well
        session.execute(text("SET default_transaction_read_only = on"))
        rows = collector.fetch(session)
    for line in summarize(rows):
        out(line)
    out(f"dry run done: {collector._attempts} SEC requests, nothing written "
        f"(--apply: ~{collector._attempts * SECONDS_PER_REQUEST / 60:.0f} minutes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
