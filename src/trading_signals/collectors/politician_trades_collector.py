"""Politician Trades Collector – US Congress stock disclosures.

Collects Periodic Transaction Reports (PTRs) from the official
Senate Electronic Financial Disclosure portal (efdsearch.senate.gov).

Strategy:
  1. Search Senate eFD for PTR filings in the lookback window
  2. Skip filings whose ``source_url`` is already stored (M6 – avoids
     re-downloading a whole year of reports every week)
  3. For each new electronic filing → parse HTML transaction table
  4. Store with ON CONFLICT DO NOTHING (dedup via unique constraint that
     includes ``owner`` – Self/Spouse trades no longer collide)

House PTRs are PDF-only and not yet supported (future enhancement).

Note: a filing that contained no valid stock transaction leaves no row
behind and is therefore re-checked on later runs (cheap, rare).

Schedule: Weekly Sunday 11:00 MEZ (trades are 30-45 days delayed anyway)
"""

import re
from datetime import date, timedelta

from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from trading_signals.collectors._db import release_transaction
from trading_signals.collectors._parsing import parse_date
from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.disclosure_client import DisclosureClient
from trading_signals.data.congress_members import lookup_member
from trading_signals.db.models.politicians import PoliticianTrade
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retention import data_start_date

logger = get_logger(__name__)

#: Owner values meaning "the filer himself" (eFD leaves the cell empty or "--").
_SELF_OWNER_ALIASES = {"", "--", "SELF"}

_TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


def _normalize_owner(owner: str | None) -> str:
    """Map empty/placeholder owners to 'Self' (owner is part of the key)."""
    value = (owner or "").strip()
    if value.upper() in _SELF_OWNER_ALIASES:
        return "Self"
    return value[:50]


class PoliticianTradesCollector(BaseCollector):
    """Collect politician stock trades from official disclosure portals."""

    name = "politician_trades_collector"

    def __init__(self, lookback_days: int = 365) -> None:
        """Initialize with a lookback window.

        Args:
            lookback_days: How many days back to search for filings.
                          Default 365 ensures we catch delayed disclosures
                          (STOCK Act allows up to 45 days).
        """
        self.lookback_days = lookback_days
        self.client = DisclosureClient()

    def _stored_source_urls(self, session: Session) -> set[str]:
        """Source URLs of Senate filings already stored (read before HTTP)."""
        rows = session.execute(
            select(distinct(PoliticianTrade.source_url)).where(
                PoliticianTrade.chamber == "Senate",
                PoliticianTrade.source_url.is_not(None),
            )
        ).all()
        urls = {r[0] for r in rows if r and isinstance(r[0], str)}
        release_transaction(session)
        return urls

    def fetch(self, session: Session) -> list[dict]:
        """Fetch politician trades from Senate eFD.

        Returns:
            List of transaction dicts ready for storage.
        """
        today = date.today()
        since_date = max(today - timedelta(days=self.lookback_days), data_start_date())
        known_urls = self._stored_source_urls(session)
        all_trades: list[dict] = []
        errors = 0

        # ── Senate PTRs ──────────────────────────────────────────
        logger.info(f"[{self.name}] Fetching Senate PTR filings since {since_date}...")

        try:
            filings = self.client.fetch_senate_ptrs(
                from_date=since_date,
                to_date=today,
            )
            self.record_success()
        except Exception as e:
            logger.error(f"[{self.name}] Failed to fetch Senate PTR list: {e}")
            filings = []
            errors += 1
            self.record_error()

        new_filings = [f for f in filings if f.get("ptr_link") not in known_urls]
        logger.info(
            f"[{self.name}] Senate PTR search returned {len(filings)} filings "
            f"({len(filings) - len(new_filings)} already stored, "
            f"{len(new_filings)} new)"
        )
        for i, f in enumerate(new_filings[:5]):  # Log first 5 for debugging
            logger.info(
                f"[{self.name}]   Filing {i+1}: "
                f"{f.get('first_name', '')} {f.get('last_name', '')} "
                f"({f.get('date_filed', '')}) -> {f.get('ptr_link', '')}"
            )

        filings_processed = 0
        for filing in new_filings:
            try:
                transactions = self.client.fetch_senate_ptr_transactions(
                    filing["ptr_link"]
                )
                self.record_success()
            except Exception as e:
                logger.info(
                    f"[{self.name}] Failed to parse PTR for "
                    f"{filing.get('first_name', '')} {filing.get('last_name', '')}: {e}"
                )
                errors += 1
                self.record_error()
                continue

            filings_processed += 1
            politician_name = (
                f"{filing.get('first_name', '')} {filing.get('last_name', '')}"
            ).strip()

            # Parse disclosure date
            disclosure_date = self._parse_disclosure_date(
                filing.get("date_filed", "")
            )

            # C3: Enrich with party affiliation from lookup (current party –
            # see congress_members docstring)
            member_info = lookup_member(politician_name)

            for txn in transactions:
                trade = {
                    "politician_name": politician_name,
                    "chamber": "Senate",
                    "party": None,  # Enriched below via congress_members lookup
                    "state": self._extract_state(filing.get("office", "")),
                    "ticker": txn.get("ticker", ""),
                    "transaction_date": txn.get("transaction_date"),
                    "disclosure_date": disclosure_date,
                    "transaction_type": txn.get("transaction_type", ""),
                    "amount_range": txn.get("amount", ""),
                    "owner": _normalize_owner(txn.get("owner")),
                    "asset_description": txn.get("asset_name", ""),
                    "comment": txn.get("comment", ""),
                    "source_url": filing.get("ptr_link", ""),
                    "raw_data": {
                        "asset_type": txn.get("asset_type", ""),
                        "office": filing.get("office", ""),
                        "report_type": filing.get("report_type", ""),
                    },
                }

                if member_info:
                    trade["party"] = member_info.get("party")
                    # Also update state if lookup has better data
                    if member_info.get("state"):
                        trade["state"] = member_info["state"]

                # C2: Validate ticker format (1-5 uppercase letters, optional dot class)
                # Rejects malformed tickers like "--", "123", empty strings
                if trade["ticker"] and _TICKER_RE.match(trade["ticker"]):
                    all_trades.append(trade)
                elif trade["ticker"]:
                    logger.info(
                        f"[{self.name}] Skipped malformed ticker: "
                        f"{trade['ticker']!r} from {politician_name}"
                    )

        logger.info(
            f"[{self.name}] Senate: processed {filings_processed}/{len(new_filings)} "
            f"new filings, found {len(all_trades)} stock transactions. "
            f"Errors: {errors}"
        )

        # ── House PTRs (future) ──────────────────────────────────
        # House filings are PDF-only. To be implemented when PDF
        # parsing is added. For now, Senate-only.
        logger.info(
            f"[{self.name}] House PTRs skipped (PDF-only, not yet supported)"
        )

        return all_trades

    def store(
        self, session: Session, data: list[dict]
    ) -> tuple[int, int]:
        """Store politician trades with dedup via unique constraint.

        After storing, checks all traded tickers against the universe
        and auto-onboards any new tickers (Alpaca validation + backfill).

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = len(data)

        # Collect all unique tickers for universe expansion
        all_tickers: set[str] = {t["ticker"] for t in data if t.get("ticker")}
        rows = [{**t, "owner": _normalize_owner(t.get("owner"))} for t in data]

        records_written = self._bulk_insert(
            session,
            PoliticianTrade,
            rows,
            conflict_cols=[
                "politician_name", "ticker", "transaction_date",
                "transaction_type", "amount_range", "owner",
            ],
        )
        session.flush()

        logger.info(
            f"[{self.name}] Stored {records_written}/{records_fetched} "
            f"trades ({records_fetched - records_written} already existed)"
        )

        # Expand universe with new tickers + auto-backfill
        if all_tickers:
            from trading_signals.universe.onboarder import NewTickerOnboarder

            onboarder = NewTickerOnboarder(session)
            new_tickers = onboarder.onboard(
                tickers=all_tickers,
                source="politician_trades",
            )
            if new_tickers:
                logger.info(
                    f"[{self.name}] Auto-onboarded {len(new_tickers)} new "
                    f"tickers: {new_tickers}"
                )

        return records_fetched, records_written

    @staticmethod
    def _parse_disclosure_date(date_str: str) -> date | None:
        """Parse the disclosure/filing date from search results."""
        return parse_date(date_str, formats=("%m/%d/%Y", "%Y-%m-%d"))

    @staticmethod
    def _extract_state(office_str: str) -> str | None:
        """Extract 2-letter state code from office string.

        Senate office strings often contain state info like
        'United States Senate (CA)' or similar patterns.
        """
        if not office_str:
            return None

        # Look for 2-letter state code in parentheses
        match = re.search(r"\(([A-Z]{2})\)", office_str.upper())
        if match:
            return match.group(1)

        # Some formats list the state directly
        state_match = re.search(r"\b([A-Z]{2})\b", office_str.upper())
        if state_match and len(office_str) <= 5:
            return state_match.group(1)

        return None
