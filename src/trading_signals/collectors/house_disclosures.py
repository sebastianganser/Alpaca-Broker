"""House Clerk financial disclosures: index, PTR PDFs, PTR text parser.

Source: the official House Clerk site (``disclosures-clerk.house.gov``).

* Yearly index ``financial-pdfs/{YEAR}FD.zip`` (updated daily) holding
  ``{YEAR}FD.txt`` (tab separated, UTF-8 with BOM; header ``Prefix Last First
  Suffix FilingType StateDst Year FilingDate DocID``). ``FilingType == "P"``
  is a Periodic Transaction Report (PTR).
* PTR PDF ``ptr-pdfs/{YEAR}/{DocID}.pdf``. Electronic filings (8-digit DocID
  starting with ``2``) are text PDFs; paper filings (``8``/``9``-prefixed
  7-digit DocIDs) are scans without a text layer and are skipped.

Text extraction uses pdfplumber in layout mode (pypdf returns empty text for
page 1 of most of these PDFs). The parser works on that layout text: each row
starts with a *transaction line* (``[owner] asset-text  P|S|S (partial)|E
MM/DD/YYYY MM/DD/YYYY amount``), the asset text continues on the following
lines until the asset type code (``[ST]``), then label lines (``F S:``,
``S O:``, ``D:`` ...) follow. The ticker is taken from the asset text only
(the parenthesised token right before ``[ST]``), never from descriptions.

The HTTP client is deliberately plain ``requests`` with an honest User-Agent
(no browser impersonation), 2 requests/s at most, timeouts and 2 retries.
"""

from __future__ import annotations

import csv
import io
import re
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime

import pdfplumber
import requests

from trading_signals.collectors.disclosure_client import (
    RATE_LIMIT_DELAY,
    TICKER_RE,
    _normalize_ticker,
)
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retry import retry

logger = get_logger(__name__)

HOUSE_BASE_URL = "https://disclosures-clerk.house.gov/public_disc"
USER_AGENT = "alpaca-broker-research/1.0 (private, non-commercial)"
#: (connect, read) timeout in seconds.
REQUEST_TIMEOUT = (10, 60)

#: Separator between the text of two PDF pages.
PAGE_MARKER = "<<<PAGE>>>"

_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "hon"}


# ============================================================================
# Index
# ============================================================================


def ptr_url(year: int, doc_id: str) -> str:
    """URL of a PTR PDF (``year`` = the index's Year field)."""
    return f"{HOUSE_BASE_URL}/ptr-pdfs/{year}/{doc_id}.pdf"


def index_url(year: int) -> str:
    return f"{HOUSE_BASE_URL}/financial-pdfs/{year}FD.zip"


def _strip_honorifics(name: str) -> str:
    """Drop leading/trailing honorific tokens (``Mrs``, ``Dr.``) of a name part."""
    tokens = name.split()

    def is_honorific(token: str) -> bool:
        return token.rstrip(".").lower() in _HONORIFICS

    while len(tokens) > 1 and is_honorific(tokens[-1]):
        tokens.pop()
    while len(tokens) > 1 and is_honorific(tokens[0]):
        tokens.pop(0)
    return " ".join(tokens)


def house_politician_name(first: str, last: str) -> str:
    """``First Last`` without honorific tokens at the edges of either part."""
    parts = [_strip_honorifics(" ".join(p.split())) for p in (first, last)]
    return " ".join(p for p in parts if p)


@dataclass(frozen=True)
class HouseFiling:
    """One row of the House Clerk yearly index."""

    prefix: str
    last: str
    first: str
    suffix: str
    filing_type: str
    state_dst: str
    year: int
    filing_date: date
    doc_id: str

    @property
    def name(self) -> str:
        return house_politician_name(self.first, self.last)

    @property
    def state(self) -> str | None:
        code = self.state_dst[:2].upper()
        return code if code.isalpha() else None

    @property
    def is_ptr(self) -> bool:
        return self.filing_type == "P"

    @property
    def is_electronic(self) -> bool:
        """Electronic filings (readable text PDFs): 8 digits starting with 2."""
        return (
            len(self.doc_id) == 8
            and self.doc_id.isdigit()
            and self.doc_id.startswith("2")
        )

    @property
    def url(self) -> str:
        return ptr_url(self.year, self.doc_id)


def _parse_us_date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def parse_house_index(zip_bytes: bytes, year: int) -> list[HouseFiling]:
    """Parse ``{year}FD.txt`` of the yearly index ZIP (rows that fail are skipped).

    Raises:
        ValueError: the ZIP has no ``*FD.txt`` member.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        member = next(
            (n for n in zf.namelist() if n.lower().endswith("fd.txt")), None
        )
        if member is None:
            raise ValueError(f"House index {year}: no FD.txt in the ZIP")
        text = zf.read(member).decode("utf-8-sig", errors="replace")

    filings: list[HouseFiling] = []
    reader = csv.reader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        if len(row) < 9 or row[0].strip() == "Prefix":
            continue
        prefix, last, first, suffix, ftype, state_dst, yr, fdate, doc_id = (
            c.strip() for c in row[:9]
        )
        filing_date = _parse_us_date(fdate)
        if filing_date is None or not doc_id:
            continue
        filings.append(
            HouseFiling(
                prefix=prefix,
                last=last,
                first=first,
                suffix=suffix,
                filing_type=ftype,
                state_dst=state_dst,
                year=int(yr) if yr.isdigit() else year,
                filing_date=filing_date,
                doc_id=doc_id,
            )
        )
    return filings


# ============================================================================
# PDF text
# ============================================================================


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Layout text of all pages (joined by :data:`PAGE_MARKER`), NULs removed.

    Returns ``""`` when the PDF has no text layer (scanned paper filing).
    Raises whatever pdfplumber raises for a corrupt PDF.
    """
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = [(page.extract_text(layout=True) or "") for page in pdf.pages]
    text = f"\n{PAGE_MARKER}\n".join(pages).replace("\x00", "")
    if not text.replace(PAGE_MARKER, "").strip():
        return ""
    return text


# ============================================================================
# PTR text parser
# ============================================================================

#: ``$1,001 - $15,000``, ``Over $50,000,000`` or ``Spouse/DC Over $1,000,000``;
#: a range ending in ``-`` or ``Over`` continues with ``$...`` on the next line.
_AMOUNT = (
    r"(?:Spouse/DC\s+)?Over(?:\s+\$[\d,]+)?"
    r"|\$[\d,]+(?:\s*-(?:\s*\$[\d,]+)?)?"
)
_ROW_RE = re.compile(
    r"^(?P<lead>.*?)\s(?P<type>S \(partial\)|P|S|E)\s+"
    r"(?P<tdate>\d{2}/\d{2}/\d{4})\s+(?P<ndate>\d{2}/\d{2}/\d{4})\s+"
    rf"(?P<amount>{_AMOUNT})"
)
_OWNER_RE = re.compile(r"^\s*(SP|JT|DC)\s{2,}(?P<asset>.*)$")
_LABEL_RE = re.compile(
    r"^\s*(?P<label>F S|S O|D|C|L|FILING STATUS|SUBHOLDING OF|DESCRIPTION|"
    r"COMMENTS?|LOCATION)\s*:\s*(?P<text>.*?)\s*$",
    re.IGNORECASE,
)
#: Asset type code. Case-insensitive: the font of 2021/22 filings extracts some
#: capitals in lower case (``[gS]``, ``(AAPl)``); codes are upper-cased.
_CODE_RE = re.compile(r"\[([A-Za-z0-9]{2})\]")
#: A run of the same code (``[GS] [GS]``: some filers type the code into the
#: asset name) is one code when counting rows.
_CODE_RUN_RE = re.compile(r"\[([A-Za-z0-9]{2})\](?:\s*\[\1\])*", re.IGNORECASE)
#: Transaction date + notification date, as on every transaction line.
_TXN_DATES_RE = re.compile(r"\d{2}/\d{2}/\d{4}\s+\d{2}/\d{2}/\d{4}")
#: A (partial) date or a dollar amount: a free-text line carrying one of them
#: may be a missed transaction line, so its codes count.
_TXN_HINT_RE = re.compile(r"\d{2}/\d{2}/|\$\d")
_AMOUNT_TAIL_RE = re.compile(r"(?:^|\s{2,})(\$[\d,]+)\s*$")
_TABLE_START_RE = re.compile(r"^\s*\$200\?\s*$")
_TABLE_END_PREFIX = "* For the complete list"
_HEADER_RES = (
    re.compile(r"^\s*ID\s+Owner\s+Asset\b", re.IGNORECASE),
    re.compile(r"^\s*Type\s+Date\s+Gains\b", re.IGNORECASE),
    re.compile(r"^\s*\$200\?\s*$"),
    re.compile(r"^\s*Filing ID #"),
)
_LABEL_KEYS = {
    "f s": "status", "filing status": "status",
    "s o": "subholding", "subholding of": "subholding",
    "d": "description", "description": "description",
}
#: Label keys holding free text (``C:``/``COMMENTS`` and the rest map to other).
_FREE_TEXT_LABELS = {"description", "other"}


def _squash(text: str) -> str:
    return " ".join(text.split())


def _count_codes(text: str) -> int:
    return len(_CODE_RUN_RE.findall(text))


def ticker_from_asset(asset_text: str) -> str | None:
    """Ticker from the asset text: the parenthesised token right before ``[ST]``."""
    matches = re.findall(r"\(([^()]+)\)\s*\[ST\]", asset_text, re.IGNORECASE)
    if not matches:
        return None
    ticker = _normalize_ticker(matches[-1])
    return ticker if TICKER_RE.match(ticker) else None


@dataclass
class PtrRow:
    """One transaction row of a PTR."""

    owner_code: str | None
    asset_text: str
    asset_code: str | None
    ticker: str | None
    raw_type: str
    transaction_date: date | None
    notification_date: date | None
    #: ``$1,001 - $15,000`` (single spaces); ``None`` if the range was cut off.
    amount: str | None
    description: str = ""
    subholding: str = ""
    filing_status: str = ""


@dataclass
class ParsedPtr:
    rows: list[PtrRow] = field(default_factory=list)
    #: Number of ``[XX]`` asset codes seen in the table region.
    codes_found: int = 0
    #: Rows whose amount range could not be completed.
    incomplete_amounts: int = 0
    #: Lines with a transaction date pair that the row pattern did not match
    #: (inside label text, or before the first row): a lost row.
    stray_rows: int = 0

    @property
    def mismatch(self) -> bool:
        """The parser lost or invented a row (codes vs. rows, amounts, codes)."""
        return (
            self.codes_found != len(self.rows)
            or self.incomplete_amounts > 0
            or self.stray_rows > 0
            or any(r.asset_code is None for r in self.rows)
        )


class _RowBuilder:
    """Collects the lines of one row until the next transaction line."""

    def __init__(self, lead: str, match: re.Match[str]) -> None:
        owner = _OWNER_RE.match(lead)
        self.owner_code = owner.group(1) if owner else None
        asset = owner.group("asset") if owner else lead
        self.asset_parts = [asset.strip()] if asset.strip() else []
        self.raw_type = match.group("type")
        self.tdate = match.group("tdate")
        self.ndate = match.group("ndate")
        self.amount = _squash(match.group("amount"))
        self.labels: dict[str, list[str]] = {}
        self.label: str | None = None
        self.code = self._find_code()

    @property
    def amount_pending(self) -> bool:
        return self.amount.endswith(("-", "Over"))

    def _find_code(self) -> str | None:
        codes = _CODE_RE.findall(" ".join(self.asset_parts))
        return codes[-1].upper() if codes else None

    def add_amount_tail(self, tail: str) -> None:
        self.amount = f"{self.amount} {tail}"

    def add_asset_line(self, text: str) -> None:
        self.asset_parts.append(text)
        self.code = self._find_code()

    def add_label(self, key: str, text: str) -> None:
        self.label = key
        self.labels[key] = [text] if text else []

    def add_label_continuation(self, text: str) -> None:
        if self.label is not None:
            self.labels[self.label].append(text)

    def build(self) -> PtrRow:
        asset_text = _squash(" ".join(self.asset_parts))
        ticker = ticker_from_asset(asset_text) if self.code == "ST" else None

        def label(key: str) -> str:
            return _squash(" ".join(self.labels.get(key, [])))

        return PtrRow(
            owner_code=self.owner_code,
            asset_text=asset_text,
            asset_code=self.code,
            ticker=ticker,
            raw_type=self.raw_type,
            transaction_date=_parse_us_date(self.tdate),
            notification_date=_parse_us_date(self.ndate),
            amount=None if self.amount_pending else self.amount,
            description=label("description"),
            subholding=label("subholding"),
            filing_status=label("status"),
        )


def _table_lines(text: str) -> list[str]:
    """Lines of the transaction table (page headers and markers removed)."""
    lines = [ln.rstrip() for ln in text.replace("\x00", "").splitlines()]
    start = next((i for i, ln in enumerate(lines) if _TABLE_START_RE.match(ln)), None)
    if start is None:
        return []
    result: list[str] = []
    for ln in lines[start + 1 :]:
        stripped = ln.strip()
        if stripped.startswith(_TABLE_END_PREFIX):
            break
        if not stripped or stripped == PAGE_MARKER:
            continue
        if any(rx.match(ln) for rx in _HEADER_RES):
            continue
        result.append(ln)
    return result


def parse_ptr_text(text: str) -> ParsedPtr:
    """Parse the layout text of a PTR (see the module docstring for the format)."""
    parsed = ParsedPtr()
    current: _RowBuilder | None = None

    def finish() -> None:
        nonlocal current
        if current is None:
            return
        if current.amount_pending:
            parsed.incomplete_amounts += 1
        parsed.rows.append(current.build())
        current = None

    for line in _table_lines(text):
        label = _LABEL_RE.match(line)
        if label:
            if current is not None:
                key = _LABEL_KEYS.get(label.group("label").lower(), "other")
                current.add_label(key, label.group("text"))
            continue

        row = _ROW_RE.match(line)
        if row:
            finish()
            current = _RowBuilder(row.group("lead"), row)
            parsed.codes_found += _count_codes(row.group("lead"))
            continue
        if current is None:
            # e.g. a first transaction line the row pattern missed: it still
            # counts, so the lost row shows up as a mismatch
            parsed.codes_found += _count_codes(line)
            parsed.stray_rows += bool(_TXN_DATES_RE.search(line))
            continue

        body = line
        if current.amount_pending and current.label is None:
            tail = _AMOUNT_TAIL_RE.search(body)
            if tail:
                current.add_amount_tail(tail.group(1))
                body = body[: tail.start()]
        # Free text (description, comments) may quote asset codes ("... B/E
        # CPN [CS] [CS]"): its codes count only on a line with a date or a
        # dollar amount, which may be a missed transaction line (fail-safe:
        # a false mismatch stores nothing, a missed row would store a partial
        # filing for good). A transaction date pair in label text means the
        # row pattern missed the next row.
        if current.label not in _FREE_TEXT_LABELS or _TXN_HINT_RE.search(body):
            parsed.codes_found += _count_codes(body)
        if current.label is not None:
            parsed.stray_rows += bool(_TXN_DATES_RE.search(body))
        body = body.strip()
        if not body:
            continue
        if current.label is not None:
            current.add_label_continuation(body)
        elif current.code is None:
            current.add_asset_line(body)

    finish()
    return parsed


# ============================================================================
# HTTP client
# ============================================================================


class HouseDisclosureClient:
    """Downloads the yearly index and PTR PDFs from the House Clerk site."""

    def __init__(self, user_agent: str = USER_AGENT) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._last_request_time = 0.0

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < RATE_LIMIT_DELAY:
            time.sleep(RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    @retry(max_attempts=3, base_delay=2.0)
    def _get(self, url: str) -> bytes:
        self._rate_limit()
        resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.content

    def fetch_index(self, year: int) -> bytes:
        """Bytes of ``{year}FD.zip``."""
        return self._get(index_url(year))

    def fetch_ptr_pdf(self, year: int, doc_id: str) -> bytes:
        """Bytes of one PTR PDF."""
        return self._get(ptr_url(year, doc_id))

    @staticmethod
    def ptr_url(year: int, doc_id: str) -> str:
        return ptr_url(year, doc_id)
