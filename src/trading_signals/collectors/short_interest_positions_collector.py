"""Short Interest Positions Collector – FINRA short interest via Massive API.

FINRA publishes the *outstanding* short positions twice a month per
settlement date (bi-monthly). This is a different dataset than the daily
short *volume* collected by ``short_interest_collector``; it fills the
``signals.short_interest`` table.

Strategy (``GET {api}/stocks/v1/short-interest``, free tier, 5 calls/min):

1. One request for the reference ticker (AAPL) lists every settlement date
   the API knows (all dates since 2017-12-29, one page). Dates older than
   ``utils.retention.data_start_date()`` are dropped client-side – the
   retention rule forbids collecting them.
2. Dates to fetch = dates not stored yet + stored dates that look
   incomplete (far fewer rows than usual) + the two most recent stored
   dates (FINRA revises recent settlement dates).
3. One bulk request per date returns *all* tickers for that date (~22.6k
   rows incl. OTC, one page). Rows are filtered to the universe (all
   tickers incl. inactive ones – survivorship) and mapped.
4. ``store`` upserts on (ticker, settlement_date, source) so revisions
   overwrite older values.

A normal weekly run needs ~3 requests (reference + 2 revision dates + any
new date); the initial backfill needs ~120 requests (~25 min at 12 s).

``days_to_cover`` is ``999.99`` when the average daily volume is zero – that
sentinel is stored as NULL (``NULL`` = no value, not "999 days").

The payload carries no publication date. Consumers (feature pipeline) must
apply a conservative availability lag themselves.

Auth: API key as ``Authorization: Bearer`` header, never as a query
parameter (URLs end up in logs/exception messages).
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_signals.collectors._db import release_transaction
from trading_signals.collectors._parsing import parse_date, safe_float, safe_int
from trading_signals.collectors.base import BaseCollector
from trading_signals.config import get_settings
from trading_signals.db.models.short_interest import ShortInterest
from trading_signals.db.models.universe import Universe
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retention import data_start_date

logger = get_logger(__name__)

#: Value of the ``source`` column (part of the unique key).
SOURCE = "massive"

#: Ticker whose listing defines the available settlement dates.
REFERENCE_TICKER = "AAPL"

#: Seconds between requests (free tier: 5 calls/min).
REQUEST_PAUSE_SECS = 12

#: Attempts per request on 429 / 5xx / network errors (each paced by the pause).
MAX_ATTEMPTS = 3

#: HTTP timeout; the bulk response (~22k rows) is a few MB.
REQUEST_TIMEOUT_SECS = 60

#: Safety bound for ``next_url`` pagination (a date normally is one page).
MAX_PAGES = 20

#: The N most recent stored settlement dates are re-fetched (FINRA revisions).
REVISION_DATES = 2

#: A stored date with fewer rows than this share of the median row count of
#: all stored dates is considered incomplete and fetched again.
INCOMPLETE_SHARE = 0.5

#: ``days_to_cover`` at/above this is the "no volume" sentinel (999.99).
DAYS_TO_COVER_SENTINEL = 999.0

#: Universe tickers without data are listed in the log only up to this many.
MISSING_SAMPLE = 15

_UPSERT_COLUMNS = ("short_interest", "avg_daily_volume", "days_to_cover", "fetched_at")


class _ApiError(Exception):
    """Protocol problem detected by the collector (message is URL-free)."""


class ShortInterestPositionsCollector(BaseCollector):
    """Collect bi-monthly FINRA short interest (settlement-date data)."""

    name = "short_interest_positions"

    def __init__(self, *, refetch_all: bool = False) -> None:
        """Initialize the HTTP session.

        Args:
            refetch_all: Fetch every settlement date inside the retention
                window, not only missing/recent ones (e.g. to give tickers
                that joined the universe later their history). Upsert, so
                safe to use at any time.
        """
        settings = get_settings()
        self._api_key = settings.POLYGON_API_KEY
        if not self._api_key:
            raise ValueError(
                "POLYGON_API_KEY not configured. Register free at "
                "https://massive.com (formerly polygon.io)"
            )
        self.refetch_all = refetch_all
        self._session = requests.Session()
        # Key in header, not in the URL (H4)
        self._session.headers.update({"Authorization": f"Bearer {self._api_key}"})
        self._api_base = "https://api.massive.com"
        self._requests_made = 0

    # ── Fetch ────────────────────────────────────────────────────────
    def fetch(self, session: Session) -> list[dict]:
        """Fetch short interest rows for all dates that need (re-)fetching."""
        self._requests_made = 0
        start = data_start_date()

        universe = self._read_universe(session)
        stored = self._read_stored_dates(session, start)
        release_transaction(session)  # no open transaction during HTTP work

        if not universe:
            logger.warning(f"[{self.name}] Universe is empty — nothing to fetch")
            return []

        try:
            available = self._reference_dates(start)
            self.record_success()
        except Exception as e:
            self.record_error()
            logger.error(
                f"[{self.name}] Could not list settlement dates: {_describe(e)}"
            )
            return []

        dates = self.select_dates(available, stored, refetch_all=self.refetch_all)
        logger.info(
            f"[{self.name}] {len(available)} settlement dates available since "
            f"{start}, {len(stored)} stored — fetching {len(dates)}"
        )

        results: list[dict] = []
        fetched_dates: list[date] = []
        errors = 0
        latest_rows: list[dict] = []

        for i, settlement_date in enumerate(dates):
            try:
                raw_rows = self._fetch_date_rows(settlement_date)
            except Exception as e:
                errors += 1
                self.record_error()
                logger.warning(
                    f"[{self.name}] {settlement_date} failed: {_describe(e)}"
                )
                continue
            self.record_success()
            rows = self._map_rows(raw_rows, settlement_date, universe)
            results.extend(rows)
            fetched_dates.append(settlement_date)
            latest_rows = rows
            if i > 0 and i % 25 == 0:
                logger.info(
                    f"[{self.name}] Progress: {i + 1}/{len(dates)} dates processed"
                )

        if fetched_dates:
            self._log_missing_universe(universe, latest_rows, fetched_dates[-1])

        span = f"{fetched_dates[0]}..{fetched_dates[-1]}" if fetched_dates else "-"
        logger.info(
            f"[{self.name}] Fetched {len(fetched_dates)}/{len(dates)} settlement "
            f"dates ({span}): {len(results)} rows for {len(universe)} universe "
            f"tickers (errors: {errors})"
        )
        return results

    @staticmethod
    def select_dates(
        available: Iterable[date],
        stored_counts: Mapping[date, int],
        *,
        refetch_all: bool = False,
    ) -> list[date]:
        """Settlement dates to request (ascending).

        Missing dates + incomplete stored dates + the ``REVISION_DATES`` most
        recent stored dates. Stored dates the API no longer lists are ignored.
        """
        avail = sorted(set(available))
        if refetch_all:
            return avail

        avail_set = set(avail)
        missing = {d for d in avail if d not in stored_counts}
        stored_here = {d: n for d, n in stored_counts.items() if d in avail_set}

        incomplete: set[date] = set()
        if stored_here:
            typical = statistics.median(stored_here.values())
            incomplete = {
                d for d, n in stored_here.items() if n < INCOMPLETE_SHARE * typical
            }
        recent = set(sorted(stored_here)[-REVISION_DATES:])

        return sorted(missing | incomplete | recent)

    def _read_universe(self, session: Session) -> set[str]:
        """All universe tickers incl. inactive ones (survivorship)."""
        rows = session.execute(select(Universe.ticker)).all()
        return {r[0].strip().upper() for r in rows if r and r[0]}

    def _read_stored_dates(self, session: Session, start: date) -> dict[date, int]:
        """Stored settlement dates inside the retention window → row count."""
        rows = session.execute(
            select(ShortInterest.settlement_date, func.count())
            .where(
                ShortInterest.source == SOURCE,
                ShortInterest.settlement_date >= start,
            )
            .group_by(ShortInterest.settlement_date)
        ).all()
        return {r[0]: int(r[1]) for r in rows}

    def _reference_dates(self, start: date) -> list[date]:
        """All settlement dates >= ``start`` known for the reference ticker.

        The date filter is applied client-side (one page of ~210 rows, so a
        server-side ``settlement_date.gte`` buys nothing and its support is
        not verified).
        """
        rows = self._fetch_rows(
            f"{self._api_base}/stocks/v1/short-interest",
            {
                "ticker": REFERENCE_TICKER,
                "limit": 1000,
                "sort": "settlement_date.asc",
            },
        )
        dates = {parse_date(r.get("settlement_date")) for r in rows}
        dates.discard(None)
        return sorted(d for d in dates if d >= start)

    def _fetch_date_rows(self, settlement_date: date) -> list[dict]:
        """Raw rows of ALL tickers for one settlement date."""
        return self._fetch_rows(
            f"{self._api_base}/stocks/v1/short-interest",
            {"settlement_date": settlement_date.isoformat(), "limit": 50000},
        )

    def _fetch_rows(self, url: str, params: dict[str, Any]) -> list[dict]:
        """GET all pages of a listing (follows ``next_url`` defensively)."""
        rows: list[dict] = []
        next_url: str | None = None
        for _ in range(MAX_PAGES):
            if next_url is None:
                payload = self._get_json(url, params)
            else:
                payload = self._get_json(next_url, None)
            rows.extend(_extract_rows(payload))
            next_url = payload.get("next_url") if isinstance(payload, dict) else None
            if not next_url:
                return rows
            # The Bearer header must never be sent to a foreign host.
            if not str(next_url).startswith(self._api_base + "/"):
                raise _ApiError("next_url points outside the Massive API")
        raise _ApiError(f"pagination exceeded {MAX_PAGES} pages")

    def _get_json(self, url: str, params: dict[str, Any] | None) -> Any:
        """GET + parse JSON; paced, retries 429 / 5xx / network errors."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self._requests_made > 0:
                time.sleep(REQUEST_PAUSE_SECS)  # 5 calls/min
            self._requests_made += 1
            try:
                resp = self._session.get(
                    url, params=params, timeout=REQUEST_TIMEOUT_SECS
                )
            except requests.RequestException as e:
                if attempt == MAX_ATTEMPTS:
                    raise
                logger.warning(
                    f"[{self.name}] Request failed ({_describe(e)}), "
                    f"retry {attempt}/{MAX_ATTEMPTS - 1}"
                )
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == MAX_ATTEMPTS:
                    resp.raise_for_status()
                logger.warning(
                    f"[{self.name}] Massive API HTTP {resp.status_code}, "
                    f"retry {attempt}/{MAX_ATTEMPTS - 1}"
                )
                continue
            if resp.status_code in (401, 403):
                logger.warning(
                    f"[{self.name}] Massive API error {resp.status_code}: "
                    f"check API tier/key"
                )
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError("unreachable")  # pragma: no cover

    # ── Mapping ──────────────────────────────────────────────────────
    def _map_rows(
        self, raw_rows: Iterable[dict], settlement_date: date, universe: set[str]
    ) -> list[dict]:
        """Filter raw API rows to the universe and map them to table rows."""
        fetched_at = datetime.now()
        out: list[dict] = []
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            ticker = raw.get("ticker")
            if not isinstance(ticker, str):
                continue
            ticker = ticker.strip().upper()
            if ticker not in universe:
                continue
            row_date = parse_date(raw.get("settlement_date")) or settlement_date
            if row_date != settlement_date:
                continue  # API ignored the date filter for this row
            short_interest = safe_int(raw.get("short_interest"))
            if short_interest is None:
                continue
            avg_volume = safe_int(raw.get("avg_daily_volume"))
            out.append(
                {
                    "ticker": ticker,
                    "settlement_date": settlement_date,
                    "short_interest": short_interest,
                    "avg_daily_volume": avg_volume,
                    "days_to_cover": clean_days_to_cover(
                        raw.get("days_to_cover"), avg_volume
                    ),
                    "source": SOURCE,
                    "fetched_at": fetched_at,
                }
            )
        return out

    def _log_missing_universe(
        self, universe: set[str], rows: list[dict], settlement_date: date
    ) -> None:
        """Info line: universe tickers without a row on the newest fetched date."""
        have = {r["ticker"] for r in rows}
        missing = sorted(universe - have)
        if missing:
            sample = ", ".join(missing[:MISSING_SAMPLE])
            more = "…" if len(missing) > MISSING_SAMPLE else ""
            logger.info(
                f"[{self.name}] {len(missing)}/{len(universe)} universe tickers "
                f"have no row for {settlement_date}: {sample}{more}"
            )

    # ── Store ────────────────────────────────────────────────────────
    def store(self, session: Session, data: list[dict]) -> tuple[int, int]:
        """Upsert into ``short_interest`` (revisions overwrite older values)."""
        if not data:
            return 0, 0

        written = self._bulk_insert(
            session,
            ShortInterest,
            data,
            conflict_cols=["ticker", "settlement_date", "source"],
            update_cols=_UPSERT_COLUMNS,
        )
        session.flush()
        return len(data), written


def clean_days_to_cover(value: Any, avg_daily_volume: int | None) -> float | None:
    """``days_to_cover`` or ``None`` for the "no volume" sentinel / bad values.

    The API reports ``999.99`` when ``avg_daily_volume`` is 0; that is "not
    defined", not 999 days.
    """
    if avg_daily_volume == 0:
        return None
    days = safe_float(value)
    if days is None or days < 0 or days >= DAYS_TO_COVER_SENTINEL:
        return None
    return round(days, 4)


def _extract_rows(payload: Any) -> list[dict]:
    """Row list of a listing response (``results`` array; bare list tolerated)."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return [r for r in payload["results"] if isinstance(r, dict)]
    return []


def _describe(e: Exception) -> str:
    """Exception summary without URLs (H4: never log request URLs)."""
    if isinstance(e, _ApiError):
        return f"{type(e).__name__}: {e}"
    resp = getattr(e, "response", None)
    status = getattr(resp, "status_code", None)
    if status is not None:
        return f"{type(e).__name__} (HTTP {status})"
    return type(e).__name__
