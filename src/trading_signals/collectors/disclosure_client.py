"""Client for scraping US congressional financial disclosure portals.

Handles the Senate eFD (efdsearch.senate.gov) portal for now.
House Clerk PTRs are PDF-only and would require PDF parsing (future sprint).

Senate eFD workflow:
  1. GET /search/ → agree to terms → get session cookie + CSRF token
  2. POST /search/ with report_type filters → HTML table of filings
  3. GET /search/view/ptr/{guid}/ → HTML table with transactions

Rate limit: max 2 req/s (conservative, government site).
"""

import re
import time
from datetime import date, datetime
from typing import Any

import requests
from bs4 import BeautifulSoup

from trading_signals.utils.logging import get_logger
from trading_signals.utils.retry import retry

logger = get_logger(__name__)

# Be conservative with government sites
RATE_LIMIT_DELAY = 0.5  # seconds between requests (2 req/s max)

SENATE_BASE_URL = "https://efdsearch.senate.gov"
SENATE_SEARCH_URL = f"{SENATE_BASE_URL}/search/"
SENATE_SEARCH_HOME = f"{SENATE_BASE_URL}/search/home/"

# User-Agent following the same approach as SEC EDGAR
USER_AGENT = "TradingSignals/1.0 (sebastian.ganser@hotmail.com)"


class DisclosureClient:
    """Client for fetching politician trade disclosures from official portals."""

    def __init__(self, user_agent: str = USER_AGENT) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self._last_request_time: float = 0.0
        self._senate_agreed = False

    def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < RATE_LIMIT_DELAY:
            time.sleep(RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    def _agree_to_senate_terms(self) -> None:
        """Visit the Senate eFD home page and agree to terms.

        The Senate site requires clicking "I agree" which sets a session
        cookie. We simulate this by visiting the home page and extracting
        the CSRF token.
        """
        if self._senate_agreed:
            return

        self._rate_limit()
        resp = self.session.get(SENATE_SEARCH_HOME)
        resp.raise_for_status()

        # Extract CSRF token from the form
        soup = BeautifulSoup(resp.text, "lxml")
        csrf_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
        if csrf_input:
            self._csrf_token = csrf_input.get("value", "")
        else:
            # Try from cookies
            self._csrf_token = self.session.cookies.get("csrftoken", "")

        # POST to agree to terms
        self._rate_limit()
        agree_resp = self.session.post(
            SENATE_SEARCH_HOME,
            data={
                "csrfmiddlewaretoken": self._csrf_token,
                "prohibition_agreement": "1",
            },
            headers={
                "Referer": SENATE_SEARCH_HOME,
            },
        )
        agree_resp.raise_for_status()

        # Update CSRF token from response if available
        if "csrftoken" in self.session.cookies:
            self._csrf_token = self.session.cookies["csrftoken"]

        self._senate_agreed = True
        logger.info("[disclosure_client] Agreed to Senate eFD terms")

    @retry(max_attempts=3, base_delay=2.0)
    def fetch_senate_ptrs(
        self,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[dict]:
        """Fetch list of Senate Periodic Transaction Reports.

        Args:
            from_date: Start date for search. Defaults to 1 year ago.
            to_date: End date for search. Defaults to today.

        Returns:
            List of filing metadata dicts with keys:
            first_name, last_name, office, report_type, date_filed, ptr_link
        """
        self._agree_to_senate_terms()

        if from_date is None:
            from_date = date(date.today().year - 1, 1, 1)
        if to_date is None:
            to_date = date.today()

        self._rate_limit()
        resp = self.session.post(
            SENATE_SEARCH_URL,
            data={
                "csrfmiddlewaretoken": self._csrf_token,
                "first_name": "",
                "last_name": "",
                "filer_type": "1",  # Senator
                "report_type": "11",  # Periodic Transactions
                "submitted_start_date": from_date.strftime("%m/%d/%Y"),
                "submitted_end_date": to_date.strftime("%m/%d/%Y"),
            },
            headers={
                "Referer": SENATE_SEARCH_URL,
            },
        )
        resp.raise_for_status()

        logger.info(
            f"[disclosure_client] Senate search response: "
            f"status={resp.status_code}, content_length={len(resp.text)}"
        )

        return self._parse_senate_search_results(resp.text)

    def _parse_senate_search_results(self, html: str) -> list[dict]:
        """Parse Senate eFD search results HTML into filing metadata."""
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table", class_="table")
        if not table:
            logger.info(
                f"[disclosure_client] No results table found in HTML "
                f"(length={len(html)}, has 'table' tag: {'<table' in html.lower()})"
            )
            return []

        tbody = table.find("tbody")
        if not tbody:
            return []

        filings = []
        for row in tbody.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            # Extract link
            link_tag = cells[3].find("a")
            if not link_tag:
                continue

            href = link_tag.get("href", "")
            # We only want electronic filings (/ptr/) not paper ones (/paper/)
            if "/ptr/" not in href:
                continue

            filing = {
                "first_name": cells[0].get_text(strip=True),
                "last_name": cells[1].get_text(strip=True),
                "office": cells[2].get_text(strip=True),
                "report_type": cells[3].get_text(strip=True),
                "date_filed": cells[4].get_text(strip=True),
                "ptr_link": href if href.startswith("http") else f"{SENATE_BASE_URL}{href}",
            }
            filings.append(filing)

        logger.info(
            f"[disclosure_client] Found {len(filings)} Senate PTR filings"
        )
        return filings

    @retry(max_attempts=3, base_delay=2.0)
    def fetch_senate_ptr_transactions(self, ptr_url: str) -> list[dict]:
        """Fetch individual transactions from a Senate PTR filing.

        Args:
            ptr_url: Full URL to the electronic PTR filing page.

        Returns:
            List of transaction dicts with keys:
            transaction_date, owner, ticker, asset_name, asset_type,
            transaction_type, amount, comment
        """
        self._agree_to_senate_terms()

        self._rate_limit()
        resp = self.session.get(
            ptr_url,
            headers={"Referer": SENATE_SEARCH_URL},
        )
        resp.raise_for_status()

        return self._parse_senate_ptr_page(resp.text, ptr_url)

    def _parse_senate_ptr_page(
        self, html: str, source_url: str
    ) -> list[dict]:
        """Parse a Senate PTR detail page into transaction records."""
        soup = BeautifulSoup(html, "lxml")

        transactions = []

        # Find all transaction tables on the page
        # Senate PTR pages have a section with transaction rows
        # The table headers vary but typically contain:
        # #, Transaction Date, Owner, Ticker, Asset Name, Asset Type,
        # Type, Amount, Comment
        tables = soup.find_all("table")

        for table in tables:
            headers = []
            thead = table.find("thead")
            if thead:
                headers = [
                    th.get_text(strip=True).lower()
                    for th in thead.find_all("th")
                ]

            # Skip tables that don't look like transaction tables
            if not any("transaction" in h or "ticker" in h for h in headers):
                continue

            tbody = table.find("tbody")
            if not tbody:
                continue

            for row in tbody.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 7:
                    continue

                txn = self._parse_transaction_row(cells, headers)
                if txn:
                    transactions.append(txn)

        return transactions

    def _parse_transaction_row(
        self, cells: list, headers: list[str]
    ) -> dict | None:
        """Parse a single transaction table row.

        Handles variable column ordering by using headers when available,
        with a fallback to positional indexing.
        """

        def cell_text(idx: int) -> str:
            if idx < len(cells):
                return cells[idx].get_text(strip=True)
            return ""

        # Build a mapping from header keywords to cell index
        col_map: dict[str, int] = {}
        for i, header in enumerate(headers):
            if "transaction date" in header or header == "transaction date":
                col_map["date"] = i
            elif header == "owner":
                col_map["owner"] = i
            elif header == "ticker":
                col_map["ticker"] = i
            elif "asset name" in header or "asset" in header and "type" not in header:
                col_map["asset_name"] = i
            elif "asset type" in header:
                col_map["asset_type"] = i
            elif header == "type" or header == "transaction type":
                col_map["type"] = i
            elif header == "amount":
                col_map["amount"] = i
            elif header == "comment":
                col_map["comment"] = i

        # Fall back to positional if headers couldn't be parsed
        # Typical Senate order:
        # 0=#, 1=Transaction Date, 2=Owner, 3=Ticker, 4=Asset Name,
        # 5=Asset Type, 6=Type, 7=Amount, 8=Comment
        if not col_map:
            col_map = {
                "date": 1,
                "owner": 2,
                "ticker": 3,
                "asset_name": 4,
                "asset_type": 5,
                "type": 6,
                "amount": 7,
                "comment": 8,
            }

        ticker = cell_text(col_map.get("ticker", 3))
        txn_type = cell_text(col_map.get("type", 6))
        amount = cell_text(col_map.get("amount", 7))

        # Skip if no meaningful data
        if not ticker or ticker == "--":
            return None

        # Skip non-stock assets if identifiable
        asset_type = cell_text(col_map.get("asset_type", 5))
        if asset_type and asset_type.lower() in (
            "municipal security", "corporate bond", "other securities",
            "non-public stock", "book value",
        ):
            return None

        # Parse transaction date
        date_str = cell_text(col_map.get("date", 1))
        txn_date = _parse_date(date_str)

        return {
            "transaction_date": txn_date,
            "owner": cell_text(col_map.get("owner", 2)),
            "ticker": _normalize_ticker(ticker),
            "asset_name": cell_text(col_map.get("asset_name", 4)),
            "asset_type": asset_type,
            "transaction_type": _normalize_transaction_type(txn_type),
            "amount": amount,
            "comment": cell_text(col_map.get("comment", 8)),
        }


def _parse_date(date_str: str) -> date | None:
    """Parse various date formats from disclosure filings."""
    if not date_str:
        return None

    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_ticker(ticker: str) -> str:
    """Normalize a ticker symbol from disclosure filings.

    Handles edge cases like ticker annotations, whitespace, etc.
    """
    # Remove common annotations
    ticker = ticker.strip().upper()
    # Remove parenthetical notes like "(Common Stock)"
    ticker = re.sub(r"\s*\(.*?\)\s*", "", ticker)
    # Remove trailing dots or dashes
    ticker = ticker.rstrip(".-")
    return ticker


def _normalize_transaction_type(txn_type: str) -> str:
    """Normalize transaction type strings to standard values."""
    txn_type = txn_type.strip()

    # Map common variations
    type_map = {
        "purchase": "Purchase",
        "sale": "Sale",
        "sale (full)": "Sale",
        "sale (partial)": "Sale",
        "exchange": "Exchange",
    }
    return type_map.get(txn_type.lower(), txn_type)
