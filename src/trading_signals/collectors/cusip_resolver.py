"""Resolve CUSIPs (13F infotables) to universe tickers.

Sources, in this order:
  1. ``cusip_map`` cache (positive hits forever, negative hits for
     ``NEGATIVE_TTL_DAYS``)
  2. local tables that carry both CUSIP and ticker (``ark_holdings``,
     ``universe.cusip``)
  3. OpenFIGI mapping API (``ID_CUSIP``, US composite listing)

All results – including "not found" – are written to ``cusip_map`` so every
CUSIP costs at most one OpenFIGI lookup per TTL. Network failures never
raise: unresolved CUSIPs simply stay NULL and are retried on the next run.

Tickers are normalised to the universe/Alpaca notation (share class with a
dot, ``BRK.B``; OpenFIGI writes ``BRK/B``).
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import requests
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from trading_signals.collectors._db import bulk_insert, release_transaction
from trading_signals.config import get_settings
from trading_signals.db.models.cusip_map import CusipMap
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retry import retry

logger = get_logger(__name__)

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
#: Negative results ("no identifier found") are retried after this many days.
NEGATIVE_TTL_DAYS = 90
#: Abort the OpenFIGI phase after this many consecutive failed requests.
MAX_CONSECUTIVE_FAILURES = 3

_UPDATE_COLS = ["ticker", "name", "exch_code", "security_type", "source", "resolved_at"]


def normalize_cusip(value: str | None) -> str | None:
    """Upper-case 9-character CUSIP or None for anything else."""
    if not value:
        return None
    c = value.strip().upper()
    return c if len(c) == 9 and c.isalnum() else None


def normalize_figi_ticker(ticker: str | None) -> str | None:
    """OpenFIGI ticker → universe notation (``BRK/B`` → ``BRK.B``)."""
    if not ticker:
        return None
    t = ticker.strip().upper().replace("/", ".").replace(" ", "")
    return t or None


def pick_figi_match(data: list[dict] | None) -> dict | None:
    """Choose the best OpenFIGI record: equity first, then anything."""
    if not data:
        return None
    with_ticker = [d for d in data if d.get("ticker")]
    if not with_ticker:
        return None
    for d in with_ticker:
        if (d.get("marketSector") or "").lower() == "equity":
            return d
    return with_ticker[0]


class CusipResolver:
    """CUSIP → ticker lookup with DB cache and OpenFIGI fallback."""

    def __init__(
        self,
        api_key: str | None = None,
        http: requests.Session | None = None,
        sleep=time.sleep,
    ) -> None:
        key = get_settings().OPENFIGI_API_KEY if api_key is None else api_key
        self.api_key = key or ""
        self.http = http or requests.Session()
        self._sleep = sleep
        # OpenFIGI limits: no key 10 jobs/req + 25 req/min; key 100 + 25/6s
        self.batch_size = 100 if self.api_key else 10
        self.min_interval = 6.0 / 25 if self.api_key else 60.0 / 25
        self._last_request = 0.0

    # ── public API ────────────────────────────────────────────────────
    def resolve(
        self, session: Session, cusips: Iterable[str | None], use_api: bool = True
    ) -> dict[str, str]:
        """Return ``{cusip: ticker}`` for all resolvable CUSIPs.

        Writes new results to ``cusip_map`` (flushed, committed by the
        caller's session). The read transaction is released before the
        OpenFIGI HTTP phase.
        """
        wanted = sorted({c for c in (normalize_cusip(x) for x in cusips) if c})
        if not wanted:
            return {}

        resolved, pending = self._from_cache(session, wanted)

        if pending:
            local = self._from_local_sources(session, pending)
            if local:
                self._save(session, [
                    {"cusip": c, "ticker": t, "source": src}
                    for c, (t, src) in local.items()
                ])
                resolved.update({c: t for c, (t, _) in local.items()})
                pending = [c for c in pending if c not in local]

        if pending and use_api:
            release_transaction(session)
            figi_rows = self._from_openfigi(pending)
            if figi_rows:
                self._save(session, figi_rows)
                resolved.update(
                    {r["cusip"]: r["ticker"] for r in figi_rows if r.get("ticker")}
                )

        logger.info(
            f"[cusip_resolver] {len(resolved)}/{len(wanted)} CUSIPs resolved "
            f"({len(pending)} needed a lookup)"
        )
        return resolved

    def apply_to_holdings(self, session: Session) -> int:
        """Fill ``form13f_holdings.ticker`` from ``cusip_map`` (set-based)."""
        result = session.execute(text("""
            UPDATE signals.form13f_holdings h
            SET ticker = m.ticker
            FROM signals.cusip_map m
            WHERE m.cusip = h.cusip
              AND m.ticker IS NOT NULL
              AND h.ticker IS DISTINCT FROM m.ticker
        """))
        n = int(getattr(result, "rowcount", 0) or 0)
        logger.info(f"[cusip_resolver] form13f_holdings.ticker set on {n} rows")
        return n

    # ── sources ───────────────────────────────────────────────────────
    def _from_cache(
        self, session: Session, cusips: list[str]
    ) -> tuple[dict[str, str], list[str]]:
        cutoff = datetime.now(UTC) - timedelta(days=NEGATIVE_TTL_DAYS)
        resolved: dict[str, str] = {}
        known: set[str] = set()
        for i in range(0, len(cusips), 1000):
            part = cusips[i : i + 1000]
            rows = session.execute(
                select(CusipMap.cusip, CusipMap.ticker).where(
                    CusipMap.cusip.in_(part),
                    or_(CusipMap.ticker.isnot(None), CusipMap.resolved_at >= cutoff),
                )
            ).all()
            for cusip, ticker in rows:
                known.add(cusip)
                if ticker:
                    resolved[cusip] = ticker
        return resolved, [c for c in cusips if c not in known]

    def _from_local_sources(
        self, session: Session, cusips: list[str]
    ) -> dict[str, tuple[str, str]]:
        """CUSIP → (ticker, source) from ark_holdings and universe."""
        found: dict[str, tuple[str, str]] = {}
        queries = [
            ("ark_holdings", """
                SELECT DISTINCT ON (upper(cusip)) upper(cusip), ticker
                FROM signals.ark_holdings
                WHERE upper(cusip) = ANY(:c) AND ticker IS NOT NULL
                ORDER BY upper(cusip), snapshot_date DESC
            """),
            ("universe", """
                SELECT upper(cusip), ticker FROM signals.universe
                WHERE upper(cusip) = ANY(:c)
            """),
        ]
        for source, sql in queries:
            rest = [c for c in cusips if c not in found]
            if not rest:
                break
            try:
                for cusip, ticker in session.execute(text(sql), {"c": rest}).all():
                    t = normalize_figi_ticker(ticker)
                    if t and cusip not in found:
                        found[cusip] = (t, source)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(f"[cusip_resolver] local source {source} failed: {e}")
                session.rollback()
        return found

    def _from_openfigi(self, cusips: list[str]) -> list[dict]:
        rows: list[dict] = []
        failures = 0
        total = len(cusips)
        for i in range(0, total, self.batch_size):
            batch = cusips[i : i + self.batch_size]
            try:
                results = self._post_mapping(batch)
                failures = 0
            except Exception as e:
                failures += 1
                logger.warning(
                    f"[cusip_resolver] OpenFIGI batch {i // self.batch_size + 1} "
                    f"failed: {e}"
                )
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    logger.warning(
                        "[cusip_resolver] OpenFIGI unavailable – remaining "
                        f"{total - i - len(batch)} CUSIPs stay unresolved"
                    )
                    break
                continue
            for cusip, res in zip(batch, results, strict=False):
                if not isinstance(res, dict) or "error" in res:
                    continue  # transient/invalid → no negative cache entry
                match = pick_figi_match(res.get("data"))
                rows.append({
                    "cusip": cusip,
                    "ticker": normalize_figi_ticker(match.get("ticker")) if match else None,
                    "name": ((match or {}).get("name") or "")[:200] or None,
                    "exch_code": (match or {}).get("exchCode"),
                    "security_type": ((match or {}).get("securityType") or "")[:50] or None,
                    "source": "openfigi",
                })
            done = min(i + self.batch_size, total)
            if done % (self.batch_size * 50) == 0 or done == total:
                logger.info(f"[cusip_resolver] OpenFIGI {done}/{total} CUSIPs")
        return rows

    @retry(max_attempts=4, base_delay=10.0)
    def _post_mapping(self, batch: list[str]) -> list:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            self._sleep(wait)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-OPENFIGI-APIKEY"] = self.api_key
        jobs = [{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in batch]
        resp = self.http.post(OPENFIGI_URL, json=jobs, headers=headers, timeout=30)
        self._last_request = time.monotonic()
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            raise ValueError(f"unexpected OpenFIGI response: {str(data)[:200]}")
        return data

    # ── persistence ───────────────────────────────────────────────────
    @staticmethod
    def _save(session: Session, rows: list[dict]) -> None:
        now = datetime.now(UTC)
        payload = [{**r, "resolved_at": now} for r in rows]
        bulk_insert(
            session, CusipMap, payload, conflict_cols=["cusip"],
            update_cols=_UPDATE_COLS,
        )
        session.flush()
