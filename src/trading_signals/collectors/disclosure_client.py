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

# Senate eFD requires a browser-like User-Agent (returns 403 for bot-like agents)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class DisclosureClient:
    """Client for fetching politician trade disclosures from official portals."""

    def __init__(self, user_agent: str = USER_AGENT) -> None:
        self.session = requests.Session()
        # Senate eFD requires full browser-like headers, not just User-Agent.
        # Without these, the site returns 403 Forbidden.
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        })
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

        The Senate site requires accepting the prohibition agreement
        which sets a session cookie. This method:
          1. GET /search/home/ → receive csrftoken cookie + HTML form
          2. POST /search/home/ with CSRF token + prohibition_agreement=1
          3. Verify we got redirected to /search/ (agreement accepted)
        """
        if self._senate_agreed:
            return

        logger.info("[disclosure_client] Starting Senate eFD agreement flow...")

        # Step 1: GET the home page to get CSRF cookie and form token
        self._rate_limit()
        resp = self.session.get(SENATE_SEARCH_HOME, allow_redirects=True)
        resp.raise_for_status()

        logger.info(
            f"[disclosure_client] GET /search/home/ -> "
            f"status={resp.status_code}, url={resp.url}, "
            f"cookies={list(self.session.cookies.keys())}"
        )

        # If we were redirected to /search/ directly, we're already agreed
        if "/search/home/" not in resp.url:
            self._csrf_token = self.session.cookies.get("csrftoken", "")
            self._senate_agreed = True
            logger.info("[disclosure_client] Already agreed (redirected to search)")
            return

        # Extract CSRF token from the hidden form field
        soup = BeautifulSoup(resp.text, "lxml")
        csrf_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
        form_csrf = csrf_input.get("value", "") if csrf_input else ""

        # Also get CSRF from cookie
        cookie_csrf = self.session.cookies.get("csrftoken", "")

        # Prefer form token, fall back to cookie
        self._csrf_token = form_csrf or cookie_csrf

        logger.info(
            f"[disclosure_client] CSRF tokens: "
            f"form={form_csrf[:20]}{'...' if len(form_csrf) > 20 else ''}, "
            f"cookie={cookie_csrf[:20]}{'...' if len(cookie_csrf) > 20 else ''}"
        )

        if not self._csrf_token:
            logger.error("[disclosure_client] No CSRF token found!")
            raise RuntimeError("Senate eFD: No CSRF token found in form or cookies")

        # Step 2: POST to agree to terms
        self._rate_limit()
        agree_resp = self.session.post(
            SENATE_SEARCH_HOME,
            data={
                "csrfmiddlewaretoken": self._csrf_token,
                "prohibition_agreement": "1",
            },
            headers={
                "Referer": SENATE_SEARCH_HOME,
                "Origin": SENATE_BASE_URL,
            },
            allow_redirects=True,
        )
        agree_resp.raise_for_status()

        logger.info(
            f"[disclosure_client] POST /search/home/ -> "
            f"status={agree_resp.status_code}, "
            f"final_url={agree_resp.url}, "
            f"cookies={list(self.session.cookies.keys())}"
        )

        # Update CSRF token from response
        if "csrftoken" in self.session.cookies:
            self._csrf_token = self.session.cookies["csrftoken"]

        self._senate_agreed = True
        logger.info("[disclosure_client] Agreed to Senate eFD terms successfully")

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

        # Refresh CSRF token from cookies (may have been updated)
        if "csrftoken" in self.session.cookies:
            self._csrf_token = self.session.cookies["csrftoken"]

        logger.info(
            f"[disclosure_client] Searching PTRs from "
            f"{from_date.strftime('%m/%d/%Y')} to {to_date.strftime('%m/%d/%Y')}"
        )

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
                "Origin": SENATE_BASE_URL,
            },
        )
        resp.raise_for_status()

        logger.info(
            f"[disclosure_client] Senate search response: "
            f"status={resp.status_code}, content_length={len(resp.text)}, "
            f"final_url={resp.url}"
        )

        # Detect if we were redirected back to the agreement page
        if "prohibition_agreement" in resp.text:
            logger.warning(
                "[disclosure_client] Search response contains agreement form! "
                "CSRF/session may have failed. Resetting agreement state."
            )
            self._senate_agreed = False
            # Try to re-agree and retry
            raise RuntimeError(
                "Senate eFD returned agreement page instead of search results"
            )

        return self._parse_senate_search_results(resp.text)

    def _parse_senate_search_results(self, html: str) -> list[dict]:
        """Parse Senate eFD search results HTML into filing metadata."""
        soup = BeautifulSoup(html, "lxml")

        # Log the page title for debugging
        title = soup.find("title")
        title_text = title.get_text(strip=True) if title else "NO TITLE"
        logger.info(f"[disclosure_client] Response page title: '{title_text}'")

        table = soup.find("table", class_="table")
        if not table:
            # Also try finding by id (the table has id="filedReports")
            table = soup.find("table", id="filedReports")

        if not table:
            logger.info(
                f"[disclosure_client] No results table found in HTML "
                f"(length={len(html)}, has '<table': {'<table' in html.lower()}, "
                f"title='{title_text}')"
            )
            # Log first 500 chars for debugging
            logger.info(f"[disclosure_client] HTML preview: {html[:500]}")
            return []

        tbody = table.find("tbody")
        if not tbody:
            logger.info("[disclosure_client] Table found but no <tbody>")
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
