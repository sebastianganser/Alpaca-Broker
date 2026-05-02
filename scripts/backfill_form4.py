"""Backfill historical Form 4 insider trades from SEC EDGAR.

Run inside the Docker container:
    docker exec -it alpaca-broker uv run python scripts/backfill_form4.py

This script fetches Form 4 filings going back ~3 years for all active
universe tickers. It uses the existing Form4Collector infrastructure
with a large lookback window. SEC rate limiting is handled automatically
by SECClient (10 req/s).

Expected runtime: 30-60 minutes (depends on universe size and SEC load).
Safe to re-run: uses ON CONFLICT DO NOTHING dedup.

After completion, re-run the insider cluster backfill:
    docker exec -it alpaca-broker uv run python scripts/backfill_insider_clusters.py
"""

import sys
import time
from datetime import date, timedelta

from sqlalchemy import select, func

from trading_signals.collectors.sec_client import SECClient
from trading_signals.collectors.form4_collector import Form4Collector, parse_form4_xml
from trading_signals.db.models.insider import InsiderTrade
from trading_signals.db.models.universe import Universe
from trading_signals.db.session import get_session
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# How far back to look (in days) — ~3 years
LOOKBACK_DAYS = 1100

# Progress reporting interval
REPORT_EVERY = 25


def main():
    since_date = date.today() - timedelta(days=LOOKBACK_DAYS)

    print(f"\n{'='*60}")
    print(f"  Form 4 Historical Backfill")
    print(f"  Lookback: {LOOKBACK_DAYS} days (since {since_date})")
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
    print(f"  Active tickers to process: {len(tickers)}")
    print(f"  Estimated time: {len(tickers) * 0.15:.0f}-{len(tickers) * 0.3:.0f} seconds")
    print()

    # Initialize SEC client and load CIK mapping
    sec_client = SECClient()
    sec_client.load_cik_mapping()

    total_fetched = 0
    total_written = 0
    total_filings = 0
    tickers_with_filings = 0
    errors = 0
    start_time = time.time()

    for i, ticker in enumerate(tickers):
        cik = sec_client.get_cik(ticker)
        if not cik:
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
                print(
                    f"  [{i+1}/{len(tickers)}] {ticker:6} — "
                    f"no filings | total: {total_written:,} written, "
                    f"{errors} errors ({elapsed:.0f}s)"
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
            from sqlalchemy.dialects.postgresql import insert as pg_insert

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
            eta = (elapsed / (i + 1)) * (len(tickers) - i - 1)
            print(
                f"  [{i+1}/{len(tickers)}] {ticker:6} — "
                f"{len(filings):3} filings, "
                f"{len(ticker_transactions):4} txns | "
                f"total: {total_written:,} written ({elapsed:.0f}s, "
                f"ETA {eta:.0f}s)"
            )

    elapsed = time.time() - start_time

    # Get count after
    with get_session() as session:
        count_after = session.execute(
            select(func.count()).select_from(InsiderTrade)
        ).scalar() or 0

    print(f"\n{'='*60}")
    print(f"  BACKFILL COMPLETE")
    print(f"{'='*60}")
    print(f"  Duration:         {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"  Tickers processed:{len(tickers):,}")
    print(f"  Tickers w/filings:{tickers_with_filings:,}")
    print(f"  Total filings:    {total_filings:,}")
    print(f"  Transactions:     {total_fetched:,} fetched, {total_written:,} new")
    print(f"  Errors:           {errors}")
    print(f"  DB count:         {count_before:,} → {count_after:,} (+{count_after-count_before:,})")
    print()
    print(f"  Next step: re-compute insider clusters:")
    print(f"    docker exec -it alpaca-broker uv run python scripts/backfill_insider_clusters.py")


if __name__ == "__main__":
    main()
