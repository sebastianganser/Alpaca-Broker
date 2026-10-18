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

import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from trading_signals.config import get_settings
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retry import retry

logger = get_logger(__name__)

# Minimum interval between requests (10 req/s → 0.1s)
MIN_REQUEST_INTERVAL = 0.11  # Slightly above 0.1s for safety margin

FORM4_TYPES = ("4", "4/A")
FORM13F_TYPES = ("13F-HR", "13F-HR/A")


def normalize_ticker(ticker: str) -> str:
    """Normalise a ticker to SEC's notation (upper case, share class with '-').

    SEC's company_tickers.json writes share classes with a dash
    (``BRK-B``), while our universe / Alpaca use a dot (``BRK.B``).
    """
    return ticker.strip().upper().replace(".", "-").replace("/", "-")


def parse_acceptance_datetime(value: str | None) -> datetime | None:
    """Parse SEC ``acceptanceDateTime`` (e.g. ``2024-04-12T18:30:05.000Z``).

    The submissions JSON labels values with ``Z``, but the clock time is
    widely reported to be EDGAR (US/Eastern) time. We therefore interpret
    the wall-clock value as ``America/New_York``. This is the conservative
    choice for point-in-time use: if the value were truly UTC, reading it
    as Eastern makes the filing appear 4–5 h *later*, never earlier
    (no look-ahead). Returns a tz-aware datetime or ``None``.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "").split("+")[0])
    except ValueError:
        return None
    return dt.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)


_NY = ZoneInfo("America/New_York")


def submission_acceptance_utc(value: str | None) -> datetime | None:
    """True acceptance instant of a filing from the submissions JSON (UTC).

    The ``acceptanceDateTime`` strings of the submissions API look like UTC
    (``...Z``) but are not: compared with the "Accepted" time that EDGAR shows
    on every filing index page (Eastern time, e.g. ``2026-07-30 16:30:28``) and
    with the ``<ACCEPTANCE-DATETIME>`` of the full-submission text, the JSON
    value is the Eastern wall clock PLUS twice the UTC offset - ``+8 h`` during
    daylight saving time and ``+10 h`` in winter (verified 2026-10-18 on ten
    filings 2016-2026 of six filers, e.g. ``2026-07-31T00:30:28.000Z`` for
    ``16:30:28`` ET). This function undoes that shift and returns the real
    instant (tz-aware, UTC). The raw string is stored next to the result
    (``sec_filings.acceptance_raw``) so the reading can be revised without a
    new download.

    NOTE: :func:`parse_acceptance_datetime` (Form 4 / 13F) reads the same
    string as Eastern wall clock, i.e. 8-10 hours too late. That is
    conservative for point-in-time use (never earlier than the truth) but
    delays almost every intraday filing by a session in cut-off rules such as
    "public before 16:00 ET". Returns ``None`` for missing/unparseable input.
    """
    if not value:
        return None
    try:
        naive = datetime.fromisoformat(value.strip().replace("Z", "").split("+")[0])
    except ValueError:
        return None
    for shift_hours, utc_offset_hours in ((8, -4), (10, -5)):
        eastern = naive - timedelta(hours=shift_hours)
        local = eastern.replace(tzinfo=_NY)
        if local.utcoffset() == timedelta(hours=utc_offset_hours):
            return local.astimezone(UTC)
    # Inside the one-hour clock-change gap: use the winter reading
    return (naive - timedelta(hours=10)).replace(tzinfo=_NY).astimezone(UTC)


@dataclass(frozen=True)
class SgmlDocument:
    """One ``<DOCUMENT>`` of an EDGAR full-submission text file."""

    type: str  # e.g. "8-K", "EX-99.1", "EX-101.SCH"
    sequence: int | None
    filename: str | None
    raw: bytes  # content between <TEXT> and </TEXT> (undecoded)

    def text(self) -> str:
        """Decode the content (UTF-8, falling back to Windows-1252)."""
        try:
            return self.raw.decode("utf-8")
        except UnicodeDecodeError:
            return self.raw.decode("cp1252", errors="replace")


_SGML_DOC_END = b"</DOCUMENT>"
_SGML_TYPE = re.compile(rb"<TYPE>[ \t]*([^\r\n<]+)")
_SGML_SEQUENCE = re.compile(rb"<SEQUENCE>[ \t]*(\d+)")
_SGML_FILENAME = re.compile(rb"<FILENAME>[ \t]*([^\r\n<]+)")
_SGML_TEXT_START = re.compile(rb"<TEXT>[ \t]*\r?\n?")

#: Upper bound of bytes read from one full-submission text file.
MAX_SUBMISSION_BYTES = 8_000_000


def _parse_sgml_block(block: bytes) -> SgmlDocument | None:
    """Parse the part of the stream that ends with ``</DOCUMENT>``."""
    start = block.rfind(b"<DOCUMENT>")
    if start < 0:
        return None
    block = block[start:]
    m_type = _SGML_TYPE.search(block)
    m_text = _SGML_TEXT_START.search(block)
    if not m_type or not m_text:
        return None
    body = block[m_text.end():]
    end = body.rfind(b"</TEXT>")
    if end >= 0:
        body = body[:end]
    m_seq = _SGML_SEQUENCE.search(block, 0, m_text.start())
    m_name = _SGML_FILENAME.search(block, 0, m_text.start())
    filename = None
    if m_name:
        filename = m_name.group(1).decode("ascii", errors="replace").strip()
    return SgmlDocument(
        type=m_type.group(1).decode("ascii", errors="replace").strip(),
        sequence=int(m_seq.group(1)) if m_seq else None,
        filename=filename,
        raw=body,
    )


def iter_sgml_documents(
    chunks: Iterable[bytes], max_bytes: int = MAX_SUBMISSION_BYTES
) -> Iterator[SgmlDocument]:
    """Yield the documents of an EDGAR ``<accession>.txt`` file as they arrive.

    ``chunks`` is the (streamed) byte content. Documents are yielded as soon
    as their ``</DOCUMENT>`` has been read, so the caller can stop early (e.g.
    after the press-release exhibit) and close the connection. Reading stops
    after ``max_bytes``; a document that is cut off by the limit is dropped.
    """
    buffer = bytearray()
    total = 0
    for chunk in chunks:
        if not chunk:
            continue
        total += len(chunk)
        buffer.extend(chunk)
        while True:
            end = buffer.find(_SGML_DOC_END)
            if end < 0:
                break
            block = bytes(buffer[:end])
            del buffer[: end + len(_SGML_DOC_END)]
            doc = _parse_sgml_block(block)
            if doc is not None:
                yield doc
        if total > max_bytes:
            logger.warning(
                f"[sec_client] submission text exceeds {max_bytes} bytes - "
                f"stopped reading"
            )
            return


# ── SEC header: the parties of a filing ───────────────────────────────────

#: Header sections that name the company a filing is ABOUT.
_SUBJECT_SECTIONS = frozenset({"SUBJECT COMPANY", "ISSUER"})

#: Largest header read from a full-submission text (fallback of the header fetch).
MAX_HEADER_BYTES = 262_144

# Tagged header (``<accession>.hdr.sgml``): <SUBJECT-COMPANY> ... </SUBJECT-COMPANY>
# with ``<CIK>0000320193`` inside. Only OUTERMOST containers are matched (the
# scan is left to right and non-overlapping); leaf tags have no closing tag.
_HDR_SECTION = re.compile(
    r"<(?!SEC-HEADER>)(?P<name>[A-Z][A-Z0-9-]*)>[ \t]*\r?\n(?P<body>.*?)</(?P=name)>",
    re.DOTALL,
)
_HDR_CIK = re.compile(r"<CIK>[ \t]*(\d+)")
# Text header (top of ``<accession>.txt``): a value-less line at column 0
# (``SUBJECT COMPANY:``, ``FILED BY:``) opens a section; the CIK is on an
# indented ``CENTRAL INDEX KEY:`` line inside it.
_TXT_SECTION = re.compile(r"^([A-Z][A-Z -]*[A-Z]):[ \t]*\r?$", re.MULTILINE)
_TXT_CIK = re.compile(r"CENTRAL INDEX KEY:[ \t]*(\d+)")


def _pad_cik(raw: str) -> str:
    return str(int(raw)).zfill(10)


@dataclass(frozen=True)
class HeaderParties:
    """The parties named in the SEC header of a filing (10-digit CIKs).

    ``subjects`` - the ``SUBJECT COMPANY`` block(s): the company the filing is
    ABOUT (the issuer of a Schedule 13D/13G or of a Form 144).
    ``others`` - everyone else (``FILED BY``, ``FILER``, ``REPORTING-OWNER`` ...):
    the holders / sellers / filers.
    """

    subjects: tuple[str, ...] = ()
    others: frozenset[str] = frozenset()

    @property
    def empty(self) -> bool:
        """The header named no party at all (not a SEC header / unparseable)."""
        return not self.subjects and not self.others

    def is_subject(self, cik: str) -> bool:
        """Is the company with ``cik`` the one the filing is about?

        With a ``SUBJECT COMPANY`` block this is a plain membership test.
        Without one the company counts as the subject only by elimination: it
        is not named as a filer / owner (and the header names someone at all).
        """
        padded = _pad_cik(cik)
        if self.subjects:
            return padded in self.subjects
        return not self.empty and padded not in self.others


def parse_header_parties(header_text: str) -> HeaderParties:
    """Parse the parties out of a SEC header.

    Understands both renderings of the header: the tagged one of
    ``<accession>.hdr.sgml`` (``<SUBJECT-COMPANY>`` ... ``<CIK>0000320193``) and the
    one at the top of ``<accession>.txt`` (``SUBJECT COMPANY:`` ...
    ``CENTRAL INDEX KEY: 0000320193``). Returns empty parties for anything else.
    """
    sections: list[tuple[str, list[str]]] = []
    for m in _HDR_SECTION.finditer(header_text):
        ciks = [_pad_cik(c) for c in _HDR_CIK.findall(m.group("body"))]
        if ciks:
            sections.append((m.group("name").replace("-", " "), ciks))
    marks = list(_TXT_SECTION.finditer(header_text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(header_text)
        ciks = [_pad_cik(c) for c in _TXT_CIK.findall(header_text[m.end() : end])]
        if ciks:
            sections.append((m.group(1).replace("-", " "), ciks))
    subjects: list[str] = []
    others: list[str] = []
    for name, ciks in sections:
        (subjects if name in _SUBJECT_SECTIONS else others).extend(ciks)
    return HeaderParties(tuple(dict.fromkeys(subjects)), frozenset(others))


def parse_subject_ciks(header_text: str) -> list[str]:
    """CIKs of the ``SUBJECT COMPANY`` block(s) of a SEC header (10 digits)."""
    return list(parse_header_parties(header_text).subjects)


def parse_subject_cik(header_text: str) -> str | None:
    """CIK of the subject company of a filing (10 digits), ``None`` if there is none.

    The subject is the company a Schedule 13D/13G or a Form 144 is ABOUT - EDGAR
    lists such a filing in the submissions of the subject AND of the filer (the
    holder / seller). With several subject blocks the first one is returned
    (:func:`parse_subject_ciks` returns all).
    """
    subjects = parse_header_parties(header_text).subjects
    return subjects[0] if subjects else None


class SECClient:
    """SEC EDGAR API client with rate limiting and CIK mapping."""

    TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
    FACTS_BASE = "https://data.sec.gov/api/xbrl/companyfacts"
    ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

    def __init__(self, user_agent: str | None = None) -> None:
        self._user_agent = user_agent or get_settings().SEC_USER_AGENT
        self._last_request_time: float = 0.0
        self._ticker_to_cik: dict[str, str] | None = None
        self._cik_to_ticker: dict[str, str] | None = None
        # Submissions JSON per padded CIK – valid for the lifetime of this
        # client (one collector run). Call clear_cache() to start fresh.
        self._submissions_cache: dict[str, dict] = {}
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": self._user_agent,
            "Accept": "application/json",
        })

    def clear_cache(self) -> None:
        """Drop cached submissions (call at the start of each run)."""
        self._submissions_cache.clear()

    # ── Rate Limiting ─────────────────────────────────────────────

    def _rate_limit(self) -> None:
        """Enforce max 10 requests per second to SEC servers."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.monotonic()

    # ── HTTP Helpers ──────────────────────────────────────────────

    @retry(max_attempts=5, base_delay=3.0)
    def _get_json(self, url: str) -> dict:
        """GET request returning JSON, with rate limiting and retry."""
        self._rate_limit()
        response = self._session.get(url, timeout=30)
        response.raise_for_status()
        return response.json()

    @retry(max_attempts=5, base_delay=3.0)
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
        """Look up the 10-digit CIK for a ticker symbol.

        Share-class tickers are normalised to SEC notation first
        (``BRK.B`` → ``BRK-B``).
        """
        if self._ticker_to_cik is None:
            self.load_cik_mapping()
        mapping = self._ticker_to_cik or {}
        return mapping.get(normalize_ticker(ticker)) or mapping.get(
            ticker.strip().upper()
        )

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

    def get_submissions(self, cik: str, use_cache: bool = True) -> dict:
        """Fetch filing metadata for a company via the Submissions API.

        Responses are cached per CIK for the lifetime of the client
        (one collector run), so repeated lookups cost no extra request.

        Args:
            cik: 10-digit CIK (with leading zeros).
            use_cache: Return a cached response if available.

        Returns:
            JSON dict with company info and recent filings.
        """
        padded = self.pad_cik(cik)
        if use_cache and padded in self._submissions_cache:
            return self._submissions_cache[padded]
        url = f"{self.SUBMISSIONS_BASE}/CIK{padded}.json"
        data = self._get_json(url)
        self._submissions_cache[padded] = data
        return data

    def fetch_submissions(self, cik: str) -> dict:
        """Fetch a company's submissions JSON WITHOUT keeping it in the cache.

        For collectors that walk the whole universe and only keep the extracted
        rows: ``get_submissions`` caches every response for the lifetime of the
        client (~1 MB of Python objects per large filer).
        """
        return self._get_json(f"{self.SUBMISSIONS_BASE}/CIK{self.pad_cik(cik)}.json")

    def get_submissions_page(self, name: str) -> dict:
        """Fetch an older submissions page (``CIK##########-submissions-001.json``).

        Companies with many filings list only the newest ~1000 (at least one
        year) in ``filings.recent``; ``filings.files`` names the older pages.
        """
        return self._get_json(f"{self.SUBMISSIONS_BASE}/{name}")

    def get_company_facts(self, cik: str) -> dict | None:
        """XBRL ``companyfacts`` of a company (all us-gaap/dei facts).

        Returns ``None`` if the company has no XBRL facts (HTTP 404).
        """
        url = f"{self.FACTS_BASE}/CIK{self.pad_cik(cik)}.json"
        try:
            return self._get_json(url)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise

    @retry(max_attempts=5, base_delay=3.0)
    def _open_stream(self, url: str) -> requests.Response:
        """GET request with ``stream=True`` (rate limited, retried on open)."""
        self._rate_limit()
        response = self._session.get(
            url, timeout=30, stream=True, headers={"Accept": "text/plain, */*"}
        )
        try:
            response.raise_for_status()
        except Exception:
            response.close()
            raise
        return response

    def iter_filing_documents(
        self,
        cik: str,
        accession_number: str,
        max_bytes: int = MAX_SUBMISSION_BYTES,
    ) -> Iterator[SgmlDocument]:
        """Stream the documents of a filing from its full-submission text file.

        One request per filing (``<accession>.txt`` holds the primary document
        and all exhibits). The caller stops iterating once it has what it needs;
        the connection is closed then.
        """
        acc_no_dashes = accession_number.replace("-", "")
        url = (
            f"{self.ARCHIVES_BASE}/{self.pad_cik(cik)}/{acc_no_dashes}/"
            f"{accession_number}.txt"
        )
        response = self._open_stream(url)
        try:
            yield from iter_sgml_documents(
                response.iter_content(chunk_size=65536), max_bytes=max_bytes
            )
        finally:
            response.close()

    def fetch_filing_header(self, cik: str, accession_number: str) -> str:
        """SGML header of a filing: who its parties are (subject company, filer ...).

        One small request (``<accession>.hdr.sgml``, 1-5 KB). If EDGAR has no such
        file (HTTP 404) the head of the full-submission text is streamed instead
        (closed after ``</SEC-HEADER>``, at most ``MAX_HEADER_BYTES``). ``cik`` may
        be any party of the filing - EDGAR serves a filing under all of them.
        Feed the result to :func:`parse_header_parties`.
        """
        folder = (
            f"{self.ARCHIVES_BASE}/{int(cik)}/{accession_number.replace('-', '')}"
        )
        try:
            return self._get_text(f"{folder}/{accession_number}.hdr.sgml")
        except requests.exceptions.HTTPError as e:
            if e.response is None or e.response.status_code != 404:
                raise
        response = self._open_stream(f"{folder}/{accession_number}.txt")
        try:
            buffer = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                buffer.extend(chunk)
                end = buffer.find(b"</SEC-HEADER>")
                if end >= 0:
                    return bytes(buffer[: end + len(b"</SEC-HEADER>")]).decode(
                        "ascii", errors="replace"
                    )
                if len(buffer) > MAX_HEADER_BYTES:
                    break
        finally:
            response.close()
        raise ValueError(f"no SEC header found at the start of {accession_number}.txt")

    @staticmethod
    def _extract_filings(
        submissions: dict,
        form_types: Iterable[str],
        since_date: date | None = None,
    ) -> list[dict]:
        """Extract filings of ``form_types`` from a submissions JSON.

        Every filing dict contains: accession_number, filing_date (str),
        primary_document, report_period (str), form_type and
        acceptance_datetime (tz-aware datetime or None).
        """
        wanted = set(form_types)
        recent = submissions.get("filings", {}).get("recent", {})

        def col(name: str) -> list:
            return recent.get(name, []) or []

        forms = col("form")
        accessions = col("accessionNumber")
        filing_dates = col("filingDate")
        primary_docs = col("primaryDocument")
        report_dates = col("reportDate")
        acceptances = col("acceptanceDateTime")

        def at(values: list, i: int) -> str:
            return values[i] if i < len(values) and values[i] else ""

        filings = []
        for i, form in enumerate(forms):
            if form not in wanted:
                continue

            filing_date_str = at(filing_dates, i)
            if since_date and filing_date_str:
                try:
                    fd = date.fromisoformat(filing_date_str)
                    if fd < since_date:
                        continue
                except ValueError:
                    pass

            filings.append({
                "accession_number": at(accessions, i),
                "filing_date": filing_date_str,
                "primary_document": at(primary_docs, i),
                "report_period": at(report_dates, i),
                "form_type": form,
                "acceptance_datetime": parse_acceptance_datetime(
                    at(acceptances, i)
                ),
            })

        return filings

    def get_recent_form4_filings(
        self, cik: str, since_date: date | None = None
    ) -> list[dict]:
        """Extract Form 4 filings from a company's submissions.

        Args:
            cik: 10-digit CIK.
            since_date: Only return filings on or after this date.

        Returns:
            List of dicts with keys: accession_number, filing_date,
            primary_document, form_type, report_period,
            acceptance_datetime.
        """
        submissions = self.get_submissions(cik)
        return self._extract_filings(submissions, FORM4_TYPES, since_date)

    def get_recent_13f_filings(
        self, cik: str, since_date: date | None = None
    ) -> list[dict]:
        """Extract 13F-HR filings from an institutional filer's submissions.

        Args:
            cik: 10-digit CIK of the filing institution.
            since_date: Only return filings on or after this date.

        Returns:
            List of dicts with accession_number, filing_date,
            primary_document, report_period, form_type,
            acceptance_datetime.
        """
        submissions = self.get_submissions(cik)
        return self._extract_filings(submissions, FORM13F_TYPES, since_date)

    def get_13f_amendment_type(
        self, cik: str, accession_number: str
    ) -> str | None:
        """Return the amendment type of a 13F-HR/A filing.

        Reads ``coverPage/amendmentInfo/amendmentType`` from the filing's
        ``primary_doc.xml``: ``"RESTATEMENT"`` (complete replacement of the
        period's holdings) or ``"NEW HOLDINGS"`` (adds holdings). Returns
        ``None`` if the document has no amendment type.
        """
        xml_content = self.download_filing_document(
            cik, accession_number, "primary_doc.xml"
        )
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError:
            return None
        node = root.find(".//{*}amendmentType")
        if node is None:
            node = root.find(".//amendmentType")
        if node is not None and node.text and node.text.strip():
            return " ".join(node.text.split()).upper()
        return None

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
