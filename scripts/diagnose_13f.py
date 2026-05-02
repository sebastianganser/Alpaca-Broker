"""Diagnostic script for the 13F collector silence problem.

Run inside the Docker container:
    docker exec -it alpaca-broker uv run python scripts/diagnose_13f.py

Run locally (with venv activated):
    python scripts/diagnose_13f.py

Checks:
  1. What's actually in the DB? Latest report_period, filing_date
  2. Can we reach SEC EDGAR? (User-Agent check)
  3. Do the top filers have new 13F-HR filings since 2025-01-01?
  4. Can we download + parse the infotable XML?
  5. Would the dedup constraint block new data?
"""

from datetime import date, timedelta
from trading_signals.collectors.sec_client import SECClient
from trading_signals.collectors.form13f_collector import (
    TOP_FILERS,
    parse_13f_infotable,
)


def section(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def check_database() -> date | None:
    """Check what's in the form13f_holdings table."""
    section("1. DATABASE STATUS")
    try:
        from trading_signals.db.session import get_session
        from sqlalchemy import text

        with get_session() as session:
            # Latest report_period
            result = session.execute(text("""
                SELECT
                    MAX(report_period) AS max_report_period,
                    MAX(filing_date)   AS max_filing_date,
                    COUNT(*)           AS total_rows,
                    COUNT(DISTINCT filer_cik) AS distinct_filers,
                    COUNT(DISTINCT report_period) AS distinct_periods
                FROM signals.form13f_holdings
            """)).first()

            print(f"  Total rows:           {result[2]:,}")
            print(f"  Distinct filers:      {result[3]}")
            print(f"  Distinct periods:     {result[4]}")
            print(f"  Latest report_period: {result[0]}")
            print(f"  Latest filing_date:   {result[1]}")

            # Show per-period counts
            periods = session.execute(text("""
                SELECT report_period, COUNT(*) AS cnt,
                       COUNT(DISTINCT filer_cik) AS filers
                FROM signals.form13f_holdings
                GROUP BY report_period
                ORDER BY report_period DESC
                LIMIT 6
            """)).fetchall()

            print(f"\n  Recent periods:")
            for p in periods:
                print(f"    {p[0]}  →  {p[1]:>6,} holdings from {p[2]} filers")

            # Check collection_log for recent runs
            logs = session.execute(text("""
                SELECT started_at, status, records_fetched, records_written, notes
                FROM signals.collection_log
                WHERE collector_name = 'form13f_collector'
                ORDER BY started_at DESC
                LIMIT 5
            """)).fetchall()

            print(f"\n  Last 5 collection_log entries:")
            for log in logs:
                print(f"    {log[0]}  status={log[1]}  "
                      f"fetched={log[2]}  written={log[3]}")
                if log[4]:
                    print(f"      notes: {log[4][:100]}")

            return result[0]  # max report_period

    except Exception as e:
        print(f"  ❌ DB connection failed: {e}")
        print(f"     (This is fine if running locally without DB access)")
        return None


def check_sec_connectivity() -> SECClient | None:
    """Test basic SEC EDGAR API connectivity."""
    section("2. SEC EDGAR CONNECTIVITY")

    client = SECClient()
    print(f"  User-Agent: {client._user_agent}")

    # Test 1: company_tickers.json (basic connectivity)
    try:
        data = client._get_json("https://www.sec.gov/files/company_tickers.json")
        print(f"  ✅ company_tickers.json: {len(data)} entries")
    except Exception as e:
        print(f"  ❌ company_tickers.json FAILED: {e}")
        print(f"     → This is fatal. SEC is blocking us.")
        print(f"     → Check User-Agent header and IP rate limits.")
        return None

    # Test 2: Submissions API
    test_cik = "0001067983"  # Berkshire Hathaway
    try:
        subs = client.get_submissions(test_cik)
        company = subs.get("name", "???")
        recent = subs.get("filings", {}).get("recent", {})
        total_filings = len(recent.get("form", []))
        print(f"  ✅ Submissions API: {company} has {total_filings} recent filings")
    except Exception as e:
        print(f"  ❌ Submissions API FAILED for CIK {test_cik}: {e}")
        return None

    return client


def check_filing_availability(
    client: SECClient, db_max_period: date | None
) -> None:
    """Check which filers have new 13F filings."""
    section("3. FILING AVAILABILITY CHECK")

    # Look back far enough to catch Q4 2025 + Q1 2026
    since_date = date(2025, 1, 1)
    print(f"  Checking for 13F-HR filings since {since_date}")
    print(f"  DB max report_period: {db_max_period}")
    print()

    filers_with_new = 0
    filers_checked = 0
    all_filings = []

    for cik, name in TOP_FILERS.items():
        filers_checked += 1
        try:
            filings = client.get_recent_13f_filings(cik, since_date=since_date)
        except Exception as e:
            print(f"  ⚠️  {name}: error: {e}")
            continue

        if not filings:
            print(f"  ⬜ {name}: no 13F filings since {since_date}")
            continue

        # Show latest filing
        latest = filings[0]
        filing_date = latest.get("filing_date", "???")
        report_period = latest.get("report_period", "???")
        accession = latest.get("accession_number", "???")

        # Is this newer than what's in DB?
        is_new = True
        if db_max_period and report_period:
            try:
                rp = date.fromisoformat(report_period)
                is_new = rp > db_max_period
            except ValueError:
                pass

        marker = "🆕" if is_new else "✅"
        print(
            f"  {marker} {name}: "
            f"report_period={report_period}, "
            f"filed={filing_date}, "
            f"filings_found={len(filings)}"
        )

        if is_new:
            filers_with_new += 1
            all_filings.append((cik, name, latest))

    print(f"\n  Summary: {filers_checked} filers checked, "
          f"{filers_with_new} with NEW data (beyond DB)")

    return all_filings


def check_xml_parsing(client: SECClient, filings: list) -> None:
    """Try to download and parse an actual infotable XML."""
    section("4. XML PARSING CHECK")

    if not filings:
        print("  ⏭️  No new filings to test parsing on.")
        return

    # Test with the first new filing
    cik, name, filing = filings[0]
    accession = filing["accession_number"]
    report_period = filing.get("report_period", "???")

    print(f"  Testing with: {name}")
    print(f"  Accession: {accession}")
    print(f"  Report Period: {report_period}")

    # Step 1: Find infotable document
    try:
        infotable_doc = client.find_infotable_document(cik, accession)
        if not infotable_doc:
            infotable_doc = filing.get("primary_document", "")
        print(f"  ✅ Infotable document: {infotable_doc}")
    except Exception as e:
        print(f"  ❌ find_infotable_document FAILED: {e}")
        return

    if not infotable_doc:
        print(f"  ❌ No infotable document found!")
        return

    # Step 2: Download XML
    try:
        xml_content = client.download_filing_document(cik, accession, infotable_doc)
        print(f"  ✅ Downloaded XML: {len(xml_content):,} bytes")
        # Show first 500 chars for inspection
        print(f"  First 300 chars:")
        print(f"  {xml_content[:300]}")
    except Exception as e:
        print(f"  ❌ download_filing_document FAILED: {e}")
        return

    # Step 3: Parse XML
    try:
        holdings = parse_13f_infotable(xml_content, filer_name=name, filer_cik=cik)
        print(f"\n  ✅ Parsed {len(holdings)} holdings from XML")
        if holdings:
            h = holdings[0]
            print(f"     Sample: cusip={h['cusip']}, shares={h['shares']}, "
                  f"value={h['market_value']}")
    except Exception as e:
        print(f"  ❌ parse_13f_infotable FAILED: {e}")


def check_dedup_constraint(filings: list) -> None:
    """Check if dedup constraint would block new inserts."""
    section("5. DEDUP CONSTRAINT CHECK")

    if not filings:
        print("  ⏭️  No new filings to check dedup for.")
        return

    try:
        from trading_signals.db.session import get_session
        from sqlalchemy import text

        cik, name, filing = filings[0]
        report_period = filing.get("report_period", "???")

        with get_session() as session:
            existing = session.execute(text("""
                SELECT COUNT(*)
                FROM signals.form13f_holdings
                WHERE filer_cik = :cik AND report_period = :rp
            """), {"cik": cik, "rp": report_period}).scalar()

            if existing > 0:
                print(f"  ⚠️  {name} already has {existing} rows for "
                      f"report_period={report_period}")
                print(f"     → Dedup constraint would block re-inserts!")
                print(f"     → But this means data WAS collected at some point.")
            else:
                print(f"  ✅ No existing data for {name} / {report_period}")
                print(f"     → Dedup would NOT block new inserts.")

    except Exception as e:
        print(f"  ⏭️  DB check skipped: {e}")


def check_lookback_window() -> None:
    """Verify the lookback window covers the right range."""
    section("6. LOOKBACK WINDOW ANALYSIS")

    lookback_days = 120
    since_date = date.today() - timedelta(days=lookback_days)

    print(f"  Today:          {date.today()}")
    print(f"  Lookback days:  {lookback_days}")
    print(f"  Since date:     {since_date}")
    print()

    # Q1 2026 filings are due by May 15, 2026
    # Most big filers file in mid-February for Q4,
    # and mid-May for Q1
    q4_2025_deadline = date(2026, 2, 14)  # ~45 days after Dec 31
    q1_2026_deadline = date(2026, 5, 15)  # ~45 days after Mar 31

    print(f"  Q4 2025 deadline: ~{q4_2025_deadline} (most filed by now)")
    print(f"  Q1 2026 deadline: ~{q1_2026_deadline} (NOT YET DUE!)")

    if since_date > q4_2025_deadline:
        print(f"\n  ⚠️  PROBLEM: lookback window starts AFTER Q4 2025 filings!")
        print(f"     The 90-day window from today ({date.today()}) = {since_date}")
        print(f"     Q4 2025 filings were filed around Feb 2026.")
        print(f"     If today > May 14, the window misses those filings!")
    else:
        print(f"\n  ✅ Lookback window covers Q4 2025 filing dates")

    # Check if Q1 2026 has even been due yet
    if date.today() < q1_2026_deadline:
        print(f"\n  ℹ️  Q1 2026 filings are NOT YET DUE (deadline ~{q1_2026_deadline})")
        print(f"     Some early filers may have filed, but most haven't.")


def main():
    print("\n" + "🔍 " * 20)
    print("  13F COLLECTOR DIAGNOSTIC")
    print("🔍 " * 20)

    # 1. Check database
    db_max_period = check_database()

    # 2. Check SEC connectivity
    client = check_sec_connectivity()
    if not client:
        print("\n❌ Cannot proceed without SEC connectivity.")
        return

    # 3. Check filing availability
    new_filings = check_filing_availability(client, db_max_period)

    # 4. Test XML parsing
    check_xml_parsing(client, new_filings)

    # 5. Check dedup constraint
    check_dedup_constraint(new_filings)

    # 6. Lookback window analysis
    check_lookback_window()

    # Final summary
    section("DIAGNOSIS SUMMARY")
    if not new_filings:
        print("  ℹ️  No new 13F filings found beyond what's in the DB.")
        print("     This is likely EXPECTED if:")
        print("     - Q1 2026 deadline hasn't passed yet (May 15, 2026)")
        print("     - Q4 2025 data was already collected when it was filed")
        print()
        print("  ⚠️  But if Q4 2025 data is MISSING from DB (report_period")
        print("     2025-12-31 not present), then the collector failed silently")
        print("     during the Feb 2026 window. Check collection_log for errors.")
    else:
        print(f"  🚨 Found {len(new_filings)} filers with NEW data not in DB!")
        print("     The collector is failing to pick these up.")
        print("     → Check collection_log for error details")
        print("     → Run the collector manually to reproduce")


if __name__ == "__main__":
    main()
