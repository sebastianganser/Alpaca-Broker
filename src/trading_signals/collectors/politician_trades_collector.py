"""Politician Trades Collector – US Congress stock disclosures.

Collects Periodic Transaction Reports (PTRs, STOCK Act) of members of
Congress.

**House (primary source):** the official House Clerk site
(``disclosures-clerk.house.gov``, see ``house_disclosures``). The yearly
index lists every filing; electronic PTR PDFs are downloaded, converted to
text with pdfplumber and parsed. Paper filings are scans without text and are
skipped (counted). Runs daily.

**Senate (disabled by default):** the Senate eFD portal
(``efdsearch.senate.gov``) has blocked this network with HTTP 403 since
2026-10-18 (Akamai; never circumvent it). The Senate code stays intact and
runs when ``POLITICIAN_SENATE_ENABLED=true``.

Strategy per chamber:
  1. List the PTR filings in the lookback window
  2. Skip filings whose ``source_url`` is already stored (M6 – avoids
     re-downloading a whole year of reports on every run)
  3. For each new filing → parse the transactions
  4. Store with ON CONFLICT DO NOTHING (dedup via unique constraint that
     includes ``owner`` – Self/Spouse trades no longer collide)

House trades never add tickers to the universe (only Senate rows are passed to
the onboarder, design decision 2026-11-09) and are stored even when the ticker
is not in the universe.

Note: a filing that contained no valid stock transaction leaves no row
behind and is therefore re-checked on later runs (House: within the
60-day window, cheap, rare). House filings are stored all or nothing: one
with a parse mismatch or an unmappable stock row stores no row (run
``partial`` with its DocID) and is fetched again by later runs inside the
window or by a backfill rerun.

Schedule: daily 02:30 MEZ (the Clerk updates the index daily; trades are
30-45 days delayed anyway)
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from trading_signals.collectors._db import release_transaction
from trading_signals.collectors._parsing import parse_date
from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.disclosure_client import TICKER_RE, DisclosureClient
from trading_signals.collectors.house_disclosures import (
    HouseDisclosureClient,
    HouseFiling,
    PtrRow,
    extract_pdf_text,
    parse_house_index,
    parse_ptr_text,
)
from trading_signals.data.congress_members import lookup_member
from trading_signals.db.models.politicians import PoliticianTrade
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retention import data_start_date

logger = get_logger(__name__)

#: Owner values meaning "the filer himself" (eFD leaves the cell empty or "--").
_SELF_OWNER_ALIASES = {"", "--", "SELF"}

_TICKER_RE = TICKER_RE


def _normalize_owner(owner: str | None) -> str:
    """Map empty/placeholder owners to 'Self' (owner is part of the key)."""
    value = (owner or "").strip()
    if value.upper() in _SELF_OWNER_ALIASES:
        return "Self"
    return value[:50]


#: House owner codes -> stored owner values.
_HOUSE_OWNERS = {None: "Self", "SP": "Spouse", "JT": "Joint", "DC": "Child"}

#: House transaction types (``S (partial)`` is a sale as well).
_HOUSE_TYPES = {"P": "Purchase", "S": "Sale", "S (partial)": "Sale", "E": "Exchange"}

#: Default House window: filings of the last N days are (re-)checked.
HOUSE_WINDOW_DAYS = 60

#: At most this many DocIDs are named in a parse-mismatch report.
_MAX_REPORTED_DOC_IDS = 10


@dataclass
class HouseStats:
    """Counters of one House fetch (one summary log line per run)."""

    filings_in_window: int = 0
    already_stored: int = 0
    new: int = 0
    paper_skipped: int = 0
    unreadable: int = 0
    parsed: int = 0
    stock_rows: int = 0
    non_stock_rows: int = 0
    rows_without_ticker: int = 0
    rows_invalid: int = 0
    errors: int = 0
    mismatches: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"House: {self.filings_in_window} PTR filings in window, "
            f"{self.already_stored} already stored, {self.new} new, "
            f"{self.paper_skipped} paper skipped, {self.unreadable} unreadable, "
            f"{self.parsed} parsed, {self.stock_rows} stock rows, "
            f"{self.non_stock_rows} non-stock rows, "
            f"{self.rows_without_ticker} rows without ticker, "
            f"{self.rows_invalid} invalid rows, "
            f"{len(self.mismatches)} parse mismatches, {self.errors} errors"
        )


class PoliticianTradesCollector(BaseCollector):
    """Collect politician stock trades from official disclosure portals."""

    name = "politician_trades_collector"

    def __init__(
        self,
        lookback_days: int = 365,
        house_since: date | None = None,
        senate_enabled: bool | None = None,
        house_client: HouseDisclosureClient | None = None,
    ) -> None:
        """Initialize the collector.

        Args:
            lookback_days: Senate lookback window in days. Default 365 ensures
                          we catch delayed disclosures (STOCK Act allows up
                          to 45 days).
            house_since: Earliest House filing date to consider. Default:
                         ``max(today - 60 days, data_start_date())`` (the
                         backfill passes an explicit date).
            senate_enabled: Override of ``POLITICIAN_SENATE_ENABLED``.
            house_client: House Clerk client (injectable for tests).
        """
        self.lookback_days = lookback_days
        self.house_since = house_since
        self.senate_enabled = senate_enabled
        self.house_client = house_client or HouseDisclosureClient()
        self.house_stats = HouseStats()
        self._senate_client: DisclosureClient | None = None

    @property
    def client(self) -> DisclosureClient:
        """Senate eFD client (created on first use: Senate is off by default)."""
        if self._senate_client is None:
            self._senate_client = DisclosureClient()
        return self._senate_client

    @client.setter
    def client(self, value: DisclosureClient) -> None:
        self._senate_client = value

    def _senate_is_enabled(self) -> bool:
        if self.senate_enabled is not None:
            return self.senate_enabled
        from trading_signals.config import get_settings

        return get_settings().POLITICIAN_SENATE_ENABLED

    def _stored_source_urls(
        self, session: Session, chamber: str = "Senate"
    ) -> set[str]:
        """Source URLs of a chamber's filings already stored (read before HTTP)."""
        rows = session.execute(
            select(distinct(PoliticianTrade.source_url)).where(
                PoliticianTrade.chamber == chamber,
                PoliticianTrade.source_url.is_not(None),
            )
        ).all()
        urls = {r[0] for r in rows if r and isinstance(r[0], str)}
        release_transaction(session)
        return urls

    def fetch(self, session: Session) -> list[dict]:
        """Fetch politician trades from the House Clerk (Senate eFD if enabled).

        Returns:
            List of transaction dicts ready for storage.
        """
        trades: list[dict] = []
        if self._senate_is_enabled():
            trades.extend(self._fetch_senate(session))
        else:
            logger.info(
                f"[{self.name}] Senate eFD skipped (POLITICIAN_SENATE_ENABLED is "
                f"off; blocked with HTTP 403 since 2026-10-18)"
            )
        trades.extend(self.fetch_house(session))
        return trades

    def _fetch_senate(self, session: Session) -> list[dict]:
        """Fetch politician trades from Senate eFD."""
        today = date.today()
        since_date = max(today - timedelta(days=self.lookback_days), data_start_date())
        known_urls = self._stored_source_urls(session, "Senate")
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

        return all_trades

    # ── House Clerk ─────────────────────────────────────────────────

    def _resolve_house_since(self, today: date) -> date:
        # never before the retention cut-off: purged data must not come back
        if self.house_since is not None:
            return max(self.house_since, data_start_date())
        return max(today - timedelta(days=HOUSE_WINDOW_DAYS), data_start_date())

    def fetch_house(
        self, session: Session, years: list[int] | None = None
    ) -> list[dict]:
        """Fetch the new House PTRs (index -> PDF -> text -> rows).

        Args:
            session: Session for reading the stored source URLs (the read
                transaction is released before any HTTP work).
            years: Index years to read. Default: ``house_since.year`` to the
                current year. The backfill passes one year at a time.

        The counters of the call are in ``self.house_stats``.
        """
        today = date.today()
        since = self._resolve_house_since(today)
        known_urls = self._stored_source_urls(session, "House")
        stats = self.house_stats = HouseStats()
        index_years = (
            sorted(years) if years is not None else range(since.year, today.year + 1)
        )
        trades: list[dict] = []

        for year in index_years:
            try:
                filings = parse_house_index(self.house_client.fetch_index(year), year)
                self.record_success()
            except Exception as e:
                logger.error(f"[{self.name}] Failed to fetch House index {year}: {e}")
                stats.errors += 1
                self.record_error()
                continue

            seen: set[str] = set()
            for filing in filings:
                if not filing.is_ptr or filing.filing_date < since:
                    continue
                if filing.url in seen:
                    continue
                seen.add(filing.url)
                stats.filings_in_window += 1
                if filing.url in known_urls:
                    stats.already_stored += 1
                    continue
                stats.new += 1
                if not filing.is_electronic:
                    stats.paper_skipped += 1
                    continue
                trades.extend(self._house_filing_trades(filing, stats))

        if stats.mismatches:
            shown = ", ".join(stats.mismatches[:_MAX_REPORTED_DOC_IDS])
            more = len(stats.mismatches) - _MAX_REPORTED_DOC_IDS
            self.mark_partial(
                f"House PTR parse mismatch, not stored: {len(stats.mismatches)} "
                f"filings "
                f"(DocIDs: {shown}{f', +{more} more' if more > 0 else ''})"
            )
        logger.info(f"[{self.name}] {stats.summary()}")
        return trades

    def _house_filing_trades(
        self, filing: HouseFiling, stats: HouseStats
    ) -> list[dict]:
        """Download, parse and map one electronic House PTR (errors are counted)."""
        try:
            text = extract_pdf_text(
                self.house_client.fetch_ptr_pdf(filing.year, filing.doc_id)
            )
            self.record_success()
        except Exception as e:
            logger.info(
                f"[{self.name}] Failed to read House PTR {filing.doc_id} "
                f"({filing.name}): {e}"
            )
            stats.errors += 1
            self.record_error()
            return []

        if not text:
            stats.unreadable += 1
            return []

        parsed = parse_ptr_text(text)
        stats.parsed += 1

        trades: list[dict] = []
        invalid = 0
        for row in parsed.rows:
            if row.asset_code != "ST":
                stats.non_stock_rows += 1
                continue
            if not row.ticker or not _TICKER_RE.match(row.ticker):
                stats.rows_without_ticker += 1
                continue
            trade = self._house_trade(filing, row)
            if trade is None:
                invalid += 1
                continue
            trades.append(trade)
        stats.rows_invalid += invalid

        # All or nothing per filing: a stored source_url marks the filing as
        # done (it is never fetched again), so an incomplete filing must not
        # store its other rows - it stays visible (run ``partial``) and is
        # fetched again by the next run inside the window or a backfill rerun.
        if parsed.mismatch or invalid:
            stats.mismatches.append(filing.doc_id)
            logger.warning(
                f"[{self.name}] House PTR {filing.doc_id}: incomplete parse "
                f"({parsed.codes_found} asset codes, {len(parsed.rows)} rows, "
                f"{parsed.incomplete_amounts} incomplete amounts, "
                f"{parsed.stray_rows} missed transaction lines, {invalid} "
                f"unmappable stock rows) - nothing stored, fetched again later"
            )
            return []
        stats.stock_rows += len(trades)
        return trades

    def _house_trade(self, filing: HouseFiling, row: PtrRow) -> dict | None:
        """Map a parsed House row to a storage dict (``None`` = unusable row)."""
        txn_type = _HOUSE_TYPES.get(row.raw_type)
        if txn_type is None:
            logger.info(
                f"[{self.name}] House PTR {filing.doc_id}: unknown transaction "
                f"type {row.raw_type!r} for {row.ticker}, row skipped"
            )
            return None
        if row.transaction_date is None or not row.amount:
            logger.info(
                f"[{self.name}] House PTR {filing.doc_id}: row for {row.ticker} "
                f"without date or amount, skipped"
            )
            return None
        raw_data = {
            "asset_type": row.asset_code,
            "doc_id": filing.doc_id,
            "state_dst": filing.state_dst,
            "notification_date": (
                row.notification_date.isoformat() if row.notification_date else None
            ),
            "raw_transaction_type": row.raw_type,
        }
        if row.subholding:
            raw_data["subholding"] = row.subholding
        return {
            "politician_name": filing.name,
            "chamber": "House",
            "party": None,  # the congress_members lookup is Senate-only
            "state": filing.state,
            "ticker": row.ticker,
            "transaction_date": row.transaction_date,
            "disclosure_date": filing.filing_date,
            "transaction_type": txn_type,
            "amount_range": row.amount,
            "owner": _HOUSE_OWNERS.get(row.owner_code, row.owner_code or "Self"),
            "asset_description": row.asset_text,
            "comment": row.description or "",
            "source_url": filing.url,
            "raw_data": raw_data,
        }

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

        # Universe expansion uses Senate tickers only: House trades never add
        # tickers to the universe (design decision 2026-11-09).
        all_tickers: set[str] = {
            t["ticker"]
            for t in data
            if t.get("ticker") and t.get("chamber") == "Senate"
        }
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

        # Expand universe with new Senate tickers + auto-backfill
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
