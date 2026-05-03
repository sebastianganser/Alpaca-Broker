"""Backfill historical Form 4 insider trades from SEC EDGAR.

Run inside the Docker container (use tmux/screen so it survives SSH disconnect!):

    # Option 1: tmux (recommended — you can reattach later)
    docker exec -it alpaca-broker bash
    tmux new -s backfill
    uv run python scripts/backfill_form4.py
    # Detach: Ctrl+B, then D
    # Reattach: docker exec -it alpaca-broker tmux attach -t backfill

    # Option 2: nohup (fire and forget, output in logfile)
    docker exec alpaca-broker bash -c \
        'nohup uv run python scripts/backfill_form4.py > /tmp/backfill_form4.log 2>&1 &'
    # Check progress:
    docker exec alpaca-broker tail -f /tmp/backfill_form4.log

This script fetches Form 4 filings going back ~3 years for all active
universe tickers. It uses the existing Form4Collector infrastructure
with a large lookback window.

Resume-safe: On startup, queries the DB for tickers that already have
insider_trades within the lookback window and skips them entirely.
Additionally uses ON CONFLICT DO NOTHING for row-level dedup.

SEC rate limiting strategy:
  - SECClient enforces 0.11s between requests (10 req/s)
  - Additional 2s delay between filing downloads within a ticker
  - 10s pause between tickers to let the rate limit window reset
  - 5 retry attempts with exponential backoff for 429 errors

Expected runtime: 8-12 hours full / much less on resume.

After completion, re-run the insider cluster backfill:
    docker exec -it alpaca-broker uv run python scripts/backfill_insider_clusters.py
"""

import time
from datetime import date, timedelta

from sqlalchemy import select, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from trading_signals.collectors.sec_client import SECClient
from trading_signals.collectors.form4_collector import parse_form4_xml
from trading_signals.db.models.insider import InsiderTrade
from trading_signals.db.models.universe import Universe
from trading_signals.db.session import get_session
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# How far back to look (in days) — ~3 years
LOOKBACK_DAYS = 1100

# Delay between individual filing downloads (seconds)
# Conservative: ~2s per filing keeps us well under SEC's limit
FILING_DELAY = 2.0

# Delay between tickers to let SEC rate limit window reset (seconds)
TICKER_DELAY = 10.0

# Progress reporting interval
REPORT_EVERY = 10


def get_already_backfilled_tickers(since_date: date) -> set[str]:
    """Query the DB for tickers that have DEEP backfill data.

    A ticker is considered "backfilled" only if its earliest
    filing_date is within the first ~9 months of the lookback window.
    This distinguishes tickers that were fully backfilled from those
    that only have recent data from the daily Form4Collector.

    Tickers with only recent data (e.g., from the daily collector's
    7-day lookback) will NOT be skipped, ensuring they get a full
    historical backfill.

    Returns:
        Set of ticker symbols that can be skipped.
    """
    # Cutoff: tickers with earliest filing_date ≤ this are "deep"
    deep_cutoff = date(2023, 12, 31)

    with get_session() as session:
        result = session.execute(text("""
            SELECT ticker
            FROM signals.insider_trades
            WHERE ticker IS NOT NULL
            GROUP BY ticker
            HAVING MIN(filing_date) <= :deep_cutoff
        """), {"deep_cutoff": deep_cutoff})
        return {row[0] for row in result}


def main():
    since_date = date.today() - timedelta(days=LOOKBACK_DAYS)

    print(f"\n{'='*60}")
    print(f"  Form 4 Historical Backfill")
    print(f"  Lookback: {LOOKBACK_DAYS} days (since {since_date})")
    print(f"  Filing delay: {FILING_DELAY}s | Ticker delay: {TICKER_DELAY}s")
    print(f"{'='*60}\n")

    # Get count before
    with get_session() as session:
        count_before = session.execute(
            select(func.count()).select_from(InsiderTrade)
        ).scalar() or 0
        print(f"  Current insider_trades count: {count_before:,}")

    # Get active tickers
    with get_session() as session:
        tickers = [
            row[0] for row in session.execute(
                select(Universe.ticker)
                .where(Universe.is_active == True)  # noqa: E712
                .order_by(Universe.ticker)
            ).all()
        ]
    print(f"  Active tickers in universe: {len(tickers)}")

    # ── Resume logic: skip tickers already backfilled ────────────
    already_done = get_already_backfilled_tickers(since_date)
    tickers_to_process = [t for t in tickers if t not in already_done]
    skipped_count = len(tickers) - len(tickers_to_process)

    if skipped_count > 0:
        print(f"  ✅ Already backfilled (skipping): {skipped_count} tickers")
        print(f"  🔄 Remaining to process: {len(tickers_to_process)} tickers")
    else:
        print(f"  Fresh start — no previously backfilled tickers found")

    print(f"  Safe to abort and re-run (resume + dedup via ON CONFLICT)")
    print()

    # Initialize SEC client and load CIK mapping
    sec_client = SECClient()
    sec_client.load_cik_mapping()

    total_fetched = 0
    total_written = 0
    total_filings = 0
    tickers_with_filings = 0
    no_cik_count = 0
    errors = 0
    start_time = time.time()

    for i, ticker in enumerate(tickers_to_process):
        cik = sec_client.get_cik(ticker)
        if not cik:
            no_cik_count += 1
            continue

        try:
            filings = sec_client.get_recent_form4_filings(
                cik, since_date=since_date
            )
        except Exception as e:
            logger.info(f"[backfill_form4] {ticker} (CIK {cik}): error: {e}")
            errors += 1
            continue

        if not filings:
            if (i + 1) % REPORT_EVERY == 0:
                elapsed = time.time() - start_time
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                eta = (len(tickers_to_process) - i - 1) / rate if rate > 0 else 0
                print(
                    f"  [{i+1}/{len(tickers_to_process)}] {ticker:6} — "
                    f"no filings | total: {total_written:,} written, "
                    f"{errors} errors ({elapsed:.0f}s, ETA ~{eta/60:.0f} min)"
                )
            continue

        tickers_with_filings += 1
        ticker_transactions = []

        for filing in filings:
            accession = filing["accession_number"]
            doc_name = filing["primary_document"]

            if not accession or not doc_name:
                continue

            # Strip XSLT prefix (same as Form4Collector)
            if "/" in doc_name:
                doc_name = doc_name.rsplit("/", 1)[-1]

            # Throttle between filing downloads
            time.sleep(FILING_DELAY)

            try:
                xml_content = sec_client.download_filing_document(
                    cik, accession, doc_name
                )
            except Exception as e:
                logger.info(
                    f"[backfill_form4] {ticker}: download error "
                    f"({accession}): {e}"
                )
                errors += 1
                continue

            filing_date = None
            if filing.get("filing_date"):
                try:
                    filing_date = date.fromisoformat(filing["filing_date"])
                except ValueError:
                    pass

            acc_no_dashes = accession.replace("-", "")
            form4_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{sec_client.pad_cik(cik)}/{acc_no_dashes}/{doc_name}"
            )

            try:
                transactions = parse_form4_xml(
                    xml_content,
                    ticker=ticker,
                    filing_date=filing_date,
                    form4_url=form4_url,
                )
                ticker_transactions.extend(transactions)
            except Exception as e:
                logger.info(
                    f"[backfill_form4] {ticker}: parse error "
                    f"({accession}): {e}"
                )
                errors += 1

        total_filings += len(filings)
        total_fetched += len(ticker_transactions)

        # Store in batches per ticker
        if ticker_transactions:
            with get_session() as session:
                written = 0
                for txn in ticker_transactions:
                    stmt = (
                        pg_insert(InsiderTrade)
                        .values(**txn)
                        .on_conflict_do_nothing(
                            constraint="uq_insider_trade_dedup"
                        )
                    )
                    result = session.execute(stmt)
                    if result.rowcount > 0:
                        written += 1
                session.commit()
                total_written += written

        if (i + 1) % REPORT_EVERY == 0 or len(filings) > 10:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (len(tickers_to_process) - i - 1) / rate if rate > 0 else 0
            print(
                f"  [{i+1}/{len(tickers_to_process)}] {ticker:6} — "
                f"{len(filings):3} filings, "
                f"{len(ticker_transactions):4} txns | "
                f"total: {total_written:,} written ({elapsed:.0f}s, "
                f"ETA ~{eta/60:.0f} min)"
            )

        # Throttle between tickers
        time.sleep(TICKER_DELAY)

    elapsed = time.time() - start_time

    # Get count after
    with get_session() as session:
        count_after = session.execute(
            select(func.count()).select_from(InsiderTrade)
        ).scalar() or 0

    print(f"\n{'='*60}")
    print(f"  BACKFILL COMPLETE")
    print(f"{'='*60}")
    print(f"  Duration:          {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"  Skipped (resume):  {skipped_count:,} tickers")
    print(f"  Processed:         {len(tickers_to_process):,} tickers")
    print(f"  No CIK found:     {no_cik_count:,}")
    print(f"  Tickers w/filings: {tickers_with_filings:,}")
    print(f"  Total filings:     {total_filings:,}")
    print(f"  Transactions:      {total_fetched:,} fetched, {total_written:,} new")
    print(f"  Errors:            {errors}")
    print(f"  DB count:          {count_before:,} → {count_after:,} "
          f"(+{count_after-count_before:,})")
    print()
    print(f"  Next step: re-compute insider clusters:")
    print(f"    docker exec -it alpaca-broker uv run python "
          f"scripts/backfill_insider_clusters.py")


if __name__ == "__main__":
    main()
