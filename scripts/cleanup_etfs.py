"""Seed the ticker_blacklist and clean up the universe.

Uses yfinance quoteType as the authoritative source.

Usage:
    # Dry-run (only shows what would happen):
    python scripts/cleanup_etfs.py

    # Apply (blacklist + deactivate):
    python scripts/cleanup_etfs.py --apply
"""

import sys
import time

import psycopg2
import yfinance as yf
from dotenv import load_dotenv
import os

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}

# Tickers that MUST stay active regardless of quoteType (benchmarks)
PROTECTED_TICKERS = {"SPY"}


def main():
    apply = "--apply" in sys.argv

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # Check if blacklist table exists
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'signals'
            AND table_name = 'ticker_blacklist'
        )
    """)
    table_exists = cur.fetchone()[0]

    if not table_exists and apply:
        print("Creating signals.ticker_blacklist table...")
        cur.execute("""
            CREATE TABLE signals.ticker_blacklist (
                ticker VARCHAR(20) PRIMARY KEY,
                reason VARCHAR(50) NOT NULL,
                quote_type VARCHAR(20),
                source VARCHAR(50),
                detected_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
    elif not table_exists:
        print("⚠️  Table signals.ticker_blacklist does not exist yet.")
        print("   Run with --apply to create it, or deploy migration 015 first.\n")

    # Get all active tickers
    cur.execute(
        "SELECT ticker, company_name FROM signals.universe "
        "WHERE is_active = true ORDER BY ticker"
    )
    tickers = cur.fetchall()

    print(f"Prüfe {len(tickers)} aktive Ticker gegen yfinance quoteType...\n")

    etfs = []

    for i, (ticker, name) in enumerate(tickers):
        try:
            info = yf.Ticker(ticker).info
            qt = (info.get("quoteType", "UNKNOWN") if info else "UNKNOWN")

            if qt.upper() != "EQUITY":
                etfs.append((ticker, name or "", qt))
                protected = " 🛡️ PROTECTED" if ticker in PROTECTED_TICKERS else ""
                print(f"  ❌ {ticker:6s}  {qt:12s}  {name}{protected}")
        except Exception:
            pass

        time.sleep(0.25)
        if (i + 1) % 50 == 0:
            print(f"  ... {i+1}/{len(tickers)} geprüft")
            time.sleep(2.0)

    to_deactivate = [(t, n, qt) for t, n, qt in etfs if t not in PROTECTED_TICKERS]
    protected = [(t, n, qt) for t, n, qt in etfs if t in PROTECTED_TICKERS]

    print(f"\n{'='*60}")
    print(f"Ergebnis: {len(etfs)} Non-Equities von {len(tickers)} Tickern")
    print(f"  Zu blacklisten + deaktivieren: {len(to_deactivate)}")
    print(f"  Geschützt (Benchmark):         {len(protected)}")
    print(f"{'='*60}")

    if apply and to_deactivate:
        # Insert into blacklist
        for ticker, name, qt in to_deactivate:
            cur.execute("""
                INSERT INTO signals.ticker_blacklist (ticker, reason, quote_type, source)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (ticker) DO NOTHING
            """, (ticker, f"quoteType={qt}", qt, "initial_cleanup"))

        # Also blacklist protected ETFs (but don't deactivate them)
        for ticker, name, qt in protected:
            cur.execute("""
                INSERT INTO signals.ticker_blacklist (ticker, reason, quote_type, source)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (ticker) DO NOTHING
            """, (ticker, f"quoteType={qt} (protected)", qt, "initial_cleanup"))

        # Deactivate non-protected ETFs in universe
        deactivate_tickers = [t for t, _, _ in to_deactivate]
        cur.execute(
            "UPDATE signals.universe SET is_active = false WHERE ticker = ANY(%s)",
            (deactivate_tickers,)
        )

        conn.commit()
        print(f"\n✅ {len(to_deactivate)} Ticker blacklisted + deaktiviert.")
        print(f"✅ {len(protected)} geschützte Ticker nur in Blacklist (bleiben aktiv).")
    elif to_deactivate:
        print(f"\n⚠️  Dry-run. Starte mit --apply zum Ausführen.")

    conn.close()


if __name__ == "__main__":
    main()
