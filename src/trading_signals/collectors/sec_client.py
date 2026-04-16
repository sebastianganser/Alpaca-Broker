"""Centralized SEC EDGAR API client with rate limiting and CIK mapping.

Handles:
  - Rate limiting (max 10 requests/second per SEC policy)
  - User-Agent header management
  - CIK ↔ Ticker mapping via company_tickers.json
  - Submissions API access for filing metadata
  - Filing document downloads

SEC EDGAR requires NO API key or registration. The User-Agent header
with contact info is the only requirement for identification.
"""

import time
from datetime import date

import requests

from trading_signals.config import get_settings
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retry import retry

logger = get_logger(__name__)

# Minimum interval between requests (10 req/s → 0.1s)
MIN_REQUEST_INTERVAL = 0.11  # Slightly above 0.1s for safety margin


class SECClient:
    """SEC EDGAR API client with rate limiting and CIK mapping."""

    TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
    ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

    def __init__(self, user_agent: str | None = None) -> None:
        self._user_agent = user_agent or get_settings().SEC_USER_AGENT
        self._last_request_time: float = 0.0
        self._ticker_to_cik: dict[str, str] | None = None
        self._cik_to_ticker: dict[str, str] | None = None
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": self._user_agent,
            "Accept": "application/json",
        })

    # ── Rate Limiting ─────────────────────────────────────────────

    def _rate_limit(self) -> None:
        """Enforce max 10 requests per second to SEC servers."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.monotonic()

    # ── HTTP Helpers ──────────────────────────────────────────────

    @retry(max_attempts=3, base_delay=2.0)
    def _get_json(self, url: str) -> dict:
        """GET request returning JSON, with rate limiting and retry."""
        self._rate_limit()
        response = self._session.get(url, timeout=30)
        response.raise_for_status()
        return response.json()

    @retry(max_attempts=3, base_delay=2.0)
    def _get_text(self, url: str) -> str:
        """GET request returning text content, with rate limiting."""
        self._rate_limit()
        response = self._session.get(url, timeout=30)
        response.raise_for_status()
        return response.text

    # ── CIK Mapping ───────────────────────────────────────────────

    def load_cik_mapping(self) -> None:
        """Download and cache the SEC company_tickers.json mapping.

        Builds two dicts:
          - ticker_to_cik: {"AAPL": "0000320193", ...}
          - cik_to_ticker: {"0000320193": "AAPL", ...}

        CIK values are zero-padded to 10 digits.
        """
        logger.info("[sec_client] Loading CIK mapping from SEC...")
        data = self._get_json(self.TICKERS_URL)

        ticker_to_cik: dict[str, str] = {}
        cik_to_ticker: dict[str, str] = {}

        for entry in data.values():
            ticker = entry.get("ticker", "").upper().strip()
            cik_raw = entry.get("cik_str", entry.get("cik", ""))
            if not ticker or not cik_raw:
                continue

            cik = str(cik_raw).zfill(10)
            ticker_to_cik[ticker] = cik
            # First ticker wins for reverse mapping (avoid overwriting)
            if cik not in cik_to_ticker:
                cik_to_ticker[cik] = ticker

        self._ticker_to_cik = ticker_to_cik
        self._cik_to_ticker = cik_to_ticker
        logger.info(
            f"[sec_client] CIK mapping loaded: {len(ticker_to_cik)} tickers"
        )

    def get_cik(self, ticker: str) -> str | None:
        """Look up the 10-digit CIK for a ticker symbol."""
        if self._ticker_to_cik is None:
            self.load_cik_mapping()
        return self._ticker_to_cik.get(ticker.upper())  # type: ignore[union-attr]

    def get_ticker(self, cik: str) -> str | None:
        """Look up the ticker for a 10-digit CIK."""
        if self._cik_to_ticker is None:
            self.load_cik_mapping()
        padded = str(cik).zfill(10)
        return self._cik_to_ticker.get(padded)  # type: ignore[union-attr]

    @staticmethod
    def pad_cik(cik: str | int) -> str:
        """Pad a CIK to 10 digits with leading zeros."""
        return str(cik).zfill(10)

    # ── Submissions API ───────────────────────────────────────────

    def get_submissions(self, cik: str) -> dict:
        """Fetch filing metadata for a company via the Submissions API.

        Args:
            cik: 10-digit CIK (with leading zeros).

        Returns:
            JSON dict with company info and recent filings.
        """
        padded = self.pad_cik(cik)
        url = f"{self.SUBMISSIONS_BASE}/CIK{padded}.json"
        return self._get_json(url)

    def get_recent_form4_filings(
        self, cik: str, since_date: date | None = None
    ) -> list[dict]:
        """Extract Form 4 filings from a company's submissions.

        Args:
            cik: 10-digit CIK.
            since_date: Only return filings on or after this date.

        Returns:
            List of dicts with keys: accession_number, filing_date,
            primary_document, form_type.
        """
        submissions = self.get_submissions(cik)
        recent = submissions.get("filings", {}).get("recent", {})

        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        primary_docs = recent.get("primaryDocument", [])

        filings = []
        for i, form in enumerate(forms):
            if form not in ("4", "4/A"):
                continue

            filing_date_str = filing_dates[i] if i < len(filing_dates) else ""
            if since_date and filing_date_str:
                try:
                    fd = date.fromisoformat(filing_date_str)
                    if fd < since_date:
                        continue
                except ValueError:
                    pass

            filings.append({
                "accession_number": accessions[i] if i < len(accessions) else "",
                "filing_date": filing_date_str,
                "primary_document": primary_docs[i] if i < len(primary_docs) else "",
                "form_type": form,
            })

        return filings

    def get_recent_13f_filings(
        self, cik: str, since_date: date | None = None
    ) -> list[dict]:
        """Extract 13F-HR filings from an institutional filer's submissions.

        Args:
            cik: 10-digit CIK of the filing institution.
            since_date: Only return filings on or after this date.

        Returns:
            List of dicts with accession_number, filing_date,
            primary_document, report_period.
        """
        submissions = self.get_submissions(cik)
        recent = submissions.get("filings", {}).get("recent", {})

        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        primary_docs = recent.get("primaryDocument", [])
        report_dates = recent.get("reportDate", [])

        filings = []
        for i, form in enumerate(forms):
            if form not in ("13F-HR", "13F-HR/A"):
                continue

            filing_date_str = filing_dates[i] if i < len(filing_dates) else ""
            if since_date and filing_date_str:
                try:
                    fd = date.fromisoformat(filing_date_str)
                    if fd < since_date:
                        continue
                except ValueError:
                    pass

            filings.append({
                "accession_number": accessions[i] if i < len(accessions) else "",
                "filing_date": filing_date_str,
                "primary_document": primary_docs[i] if i < len(primary_docs) else "",
                "report_period": report_dates[i] if i < len(report_dates) else "",
                "form_type": form,
            })

        return filings

    # ── Filing Document Downloads ─────────────────────────────────

    def download_filing_document(
        self, cik: str, accession_number: str, document_name: str
    ) -> str:
        """Download a specific filing document (XML, HTML, etc.).

        Args:
            cik: 10-digit CIK.
            accession_number: Filing accession number (e.g., "0001234567-24-000001").
            document_name: Filename of the document to download.

        Returns:
            Document content as string.
        """
        padded_cik = self.pad_cik(cik)
        # Accession number in URL has no dashes
        acc_no_dashes = accession_number.replace("-", "")
        url = f"{self.ARCHIVES_BASE}/{padded_cik}/{acc_no_dashes}/{document_name}"
        return self._get_text(url)

    def find_infotable_document(
        self, cik: str, accession_number: str
    ) -> str | None:
        """Find the infotable XML document name in a 13F filing index.

        13F filings contain an 'infotable' XML with all holdings.
        This method fetches the filing index to find its filename.

        Strategy (in order of priority):
          1. Filename contains 'infotable' (e.g., infotable.xml)
          2. Filename contains 'informationtable' (e.g., informationtable.xml)
          3. Filename contains 'holding' (e.g., renaissance13Fq42025_holding.xml)
          4. Largest XML file that is NOT primary_doc.xml (fallback)
        """
        padded_cik = self.pad_cik(cik)
        acc_no_dashes = accession_number.replace("-", "")
        index_url = (
            f"{self.ARCHIVES_BASE}/{padded_cik}/{acc_no_dashes}/index.json"
        )

        try:
            index_data = self._get_json(index_url)
        except Exception:
            logger.warning(
                f"[sec_client] Could not fetch index for {accession_number}"
            )
            return None

        # Collect all XML files from the directory listing
        items = index_data.get("directory", {}).get("item", [])
        xml_files = [
            item for item in items
            if item.get("name", "").lower().endswith(".xml")
        ]

        if not xml_files:
            return None

        # Priority 1: Filename contains 'infotable'
        for item in xml_files:
            if "infotable" in item["name"].lower():
                return item["name"]

        # Priority 2: Filename contains 'informationtable'
        for item in xml_files:
            if "informationtable" in item["name"].lower():
                return item["name"]

        # Priority 3: Filename contains 'holding'
        for item in xml_files:
            if "holding" in item["name"].lower():
                return item["name"]

        # Priority 4: Largest XML that is NOT primary_doc.xml
        # In every 13F filing, the infotable is always much larger
        # than the cover page (primary_doc.xml).
        non_primary = [
            item for item in xml_files
            if item["name"].lower() != "primary_doc.xml"
        ]

        if non_primary:
            # Sort by size descending, pick the largest
            non_primary.sort(
                key=lambda x: int(x.get("size", 0) or 0), reverse=True
            )
            chosen = non_primary[0]["name"]
            logger.info(
                f"[sec_client] Infotable fallback: using '{chosen}' "
                f"(largest non-primary XML) for {accession_number}"
            )
            return chosen

        return None
