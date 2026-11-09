"""Backfill House PTRs (stock trades of House members) from the House Clerk site.

The daily ``politician_trades_collector`` only looks at the last 60 days. This
script loads the history back to ``--since`` (default: the retention cut-off,
``data_start_date()``) with the very same code path: per index year it runs the
collector's House fetch (index -> electronic PTR PDFs -> pdfplumber text ->
rows) and stores the rows. House trades never add tickers to the universe.

* **Dry run (default):** read-only database session (``SET TRANSACTION READ
  ONLY`` and ``default_transaction_read_only = on``). Per year it prints PTR
  total / electronic / paper / already stored / to fetch - one small index
  download per year, no PDF download, nothing written. ``--sample N``
  additionally downloads and parses N random electronic, not yet stored
  filings and prints the parse statistics (N x 0.5 s of requests).
* **``--apply``:** per year fetch + store + **commit** (resumable: stored
  source URLs are skipped on a rerun, an interrupted run loses at most the
  current year). At ~2 requests/s the 400-500 electronic filings of a year
  (2022: 505) take about 5-10 minutes; the whole window (2021-10 to today,
  ~2,300 filings) about 30-60 minutes. Needs Sebastian's OK on
  production; there are no backups. Do not run it between 22:15 and 05:15
  (nightly chain; ``alpaca-ops run-bg`` refuses anyway).

Paper filings (DocIDs starting with 8/9) are scans without text and are only
counted. Filings without a stock row, and filings with a parse mismatch (stored
all or nothing, listed per year), leave no row behind and are fetched again by
a rerun (cheap). ``--since`` before the retention cut-off is refused.

Usage (inside the container, ``alpaca-ops run`` / ``run-bg``)::

    scripts/backfill_house_ptrs.py --sample 20                 # dry run
    scripts/backfill_house_ptrs.py --since 2024-01-01 --apply
    scripts/backfill_house_ptrs.py --year 2023 --apply
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass, field
from datetime import date

from trading_signals.collectors.house_disclosures import (
    HouseDisclosureClient,
    HouseFiling,
    extract_pdf_text,
    parse_house_index,
    parse_ptr_text,
)
from trading_signals.utils.logging import setup_logging
from trading_signals.utils.repair_support import announce_db_target, out, repair_session
from trading_signals.utils.retention import data_start_date

SCRIPT = "backfill_house_ptrs"


@dataclass
class YearPlan:
    """What a year's index contains within the window (dry-run report)."""

    year: int
    ptr_total: int = 0
    electronic: int = 0
    paper: int = 0
    already_stored: int = 0
    to_fetch: list[HouseFiling] = field(default_factory=list)

    def line(self) -> str:
        return (
            f"{self.year}: {self.ptr_total} PTR filings, {self.electronic} electronic, "
            f"{self.paper} paper (skipped), {self.already_stored} already stored, "
            f"{len(self.to_fetch)} to fetch"
        )


def plan_year(
    year: int, filings: list[HouseFiling], since: date, stored_urls: set[str]
) -> YearPlan:
    """Classify a year's PTR filings in the window (pure)."""
    plan = YearPlan(year)
    seen: set[str] = set()
    for f in filings:
        if not f.is_ptr or f.filing_date < since or f.url in seen:
            continue
        seen.add(f.url)
        plan.ptr_total += 1
        if not f.is_electronic:
            plan.paper += 1
            continue
        plan.electronic += 1
        if f.url in stored_urls:
            plan.already_stored += 1
        else:
            plan.to_fetch.append(f)
    return plan


def years_in_window(since: date, today: date, year: int | None) -> list[int]:
    """Index years to process (``--year`` restricts to one of them)."""
    years = list(range(since.year, today.year + 1))
    if year is not None:
        return [year] if year in years else []
    return years


def year_window_start(since: date, year: int) -> date:
    """``max(since, Jan 1 of year)``: the collector's ``house_since`` for a year."""
    return max(since, date(year, 1, 1))


@dataclass
class SampleStats:
    downloaded: int = 0
    unreadable: int = 0
    errors: int = 0
    rows: int = 0
    stock_rows: int = 0
    non_stock_rows: int = 0
    rows_without_ticker: int = 0
    mismatches: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        return [
            f"sample: {self.downloaded} downloaded, {self.unreadable} unreadable, "
            f"{self.errors} errors",
            f"sample rows: {self.rows} total, {self.stock_rows} stock, "
            f"{self.non_stock_rows} non-stock, {self.rows_without_ticker} stock "
            f"without ticker",
            f"sample parse mismatches: {len(self.mismatches)}"
            + (f" ({', '.join(self.mismatches)})" if self.mismatches else ""),
        ]


def run_sample(client, filings: list[HouseFiling]) -> SampleStats:
    """Download and parse ``filings`` (nothing is stored)."""
    stats = SampleStats()
    for f in filings:
        try:
            text = extract_pdf_text(client.fetch_ptr_pdf(f.year, f.doc_id))
        except Exception as e:
            stats.errors += 1
            out(f"  {f.doc_id}: ERROR {type(e).__name__}: {e}")
            continue
        stats.downloaded += 1
        if not text:
            stats.unreadable += 1
            out(f"  {f.doc_id}: no text (unreadable)")
            continue
        parsed = parse_ptr_text(text)
        stock = [r for r in parsed.rows if r.asset_code == "ST"]
        with_ticker = [r for r in stock if r.ticker]
        stats.rows += len(parsed.rows)
        stats.stock_rows += len(with_ticker)
        stats.non_stock_rows += len(parsed.rows) - len(stock)
        stats.rows_without_ticker += len(stock) - len(with_ticker)
        flag = ""
        if parsed.mismatch:
            stats.mismatches.append(f.doc_id)
            flag = (
                f"  MISMATCH ({parsed.codes_found} codes, "
                f"{parsed.incomplete_amounts} incomplete amounts, "
                f"{parsed.stray_rows} missed transaction lines)"
            )
        out(
            f"  {f.doc_id} {f.name}: {len(parsed.rows)} rows, "
            f"{len(with_ticker)} stock{flag}"
        )
    return stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--since",
        type=date.fromisoformat,
        help="earliest filing date, YYYY-MM-DD (default: retention cut-off)",
    )
    p.add_argument("--year", type=int, help="process only this index year")
    p.add_argument(
        "--sample",
        type=int,
        default=0,
        metavar="N",
        help="dry run: also download and parse N random electronic filings",
    )
    p.add_argument("--seed", type=int, help="seed of the --sample draw")
    p.add_argument("--apply", action="store_true", help="write (default: dry run)")
    return p.parse_args(argv)


def _dry_run(args, since: date, years: list[int], client) -> int:
    from sqlalchemy import text

    from trading_signals.collectors.politician_trades_collector import (
        PoliticianTradesCollector,
    )

    collector = PoliticianTradesCollector(senate_enabled=False, house_client=client)
    with repair_session(read_only=True) as session:
        session.execute(text("SET default_transaction_read_only = on"))
        stored = collector._stored_source_urls(session, "House")
    out(f"[{SCRIPT}] stored House source URLs: {len(stored)}")

    plans: list[YearPlan] = []
    failed = False
    for year in years:
        try:
            filings = parse_house_index(client.fetch_index(year), year)
        except Exception as e:
            out(f"{year}: ERROR fetching the index: {type(e).__name__}: {e}")
            failed = True
            continue
        plan = plan_year(year, filings, year_window_start(since, year), stored)
        plans.append(plan)
        out(plan.line())

    out(
        f"total: {sum(p.ptr_total for p in plans)} PTR filings, "
        f"{sum(p.electronic for p in plans)} electronic, "
        f"{sum(p.paper for p in plans)} paper, "
        f"{sum(p.already_stored for p in plans)} already stored, "
        f"{sum(len(p.to_fetch) for p in plans)} to fetch"
    )
    if args.sample:
        pool = [f for p in plans for f in p.to_fetch]
        rng = random.Random(args.seed)
        picked = rng.sample(pool, min(args.sample, len(pool)))
        out(f"[{SCRIPT}] sampling {len(picked)} of {len(pool)} filings to fetch")
        for line in run_sample(client, picked).lines():
            out(line)
    out("dry run done: nothing written; --apply would fetch and store the rows")
    return 1 if failed else 0


def _apply(since: date, years: list[int], client) -> int:
    from trading_signals.collectors.politician_trades_collector import (
        PoliticianTradesCollector,
    )

    failed = False
    for year in years:
        collector = PoliticianTradesCollector(
            house_since=year_window_start(since, year),
            senate_enabled=False,
            house_client=client,
        )
        collector._reset_run_state()
        with repair_session(read_only=False) as session:
            trades = collector.fetch_house(session, years=[year])
            fetched, written = collector.store(session, trades)
            session.commit()
        stats = collector.house_stats
        out(f"{year}: {stats.summary()}")
        out(f"{year}: stored {written}/{fetched} rows (committed)")
        if stats.errors:
            failed = True
            out(f"{year}: WARNING {stats.errors} errors - rerun to retry them")
        if stats.mismatches:
            out(f"{year}: parse mismatches: {', '.join(stats.mismatches)}")
    return 1 if failed else 0


def main(argv: list[str] | None = None, client=None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")
    announce_db_target(SCRIPT)
    if args.since is not None and args.since < data_start_date():
        out(
            f"REFUSED: --since {args.since} is before the retention cut-off "
            f"{data_start_date()} (data_retention purges older rows; backfills "
            "must not collect them again)"
        )
        return 2
    since = args.since or data_start_date()
    years = years_in_window(since, date.today(), args.year)
    out(
        f"[{SCRIPT}] since {since}, years {years or 'none'}, "
        f"mode: {'APPLY' if args.apply else 'dry run'}"
    )
    if not years:
        out("REFUSED: no index year in the window (check --since/--year)")
        return 2
    client = client or HouseDisclosureClient()
    if args.apply:
        return _apply(since, years, client)
    return _dry_run(args, since, years, client)


if __name__ == "__main__":
    sys.exit(main())
