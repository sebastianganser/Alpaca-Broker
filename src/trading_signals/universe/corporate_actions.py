"""Alpaca corporate actions (renames and mergers) -- read-only.

Endpoint: ``GET https://data.alpaca.markets/v1/corporate-actions`` with the
same key headers as the other Alpaca clients. Only the action types needed to
explain why a ticker disappeared are requested:

* ``name_change``  -> :class:`NameChange` (``old_symbol`` -> ``new_symbol``)
* ``cash_merger`` / ``stock_merger`` / ``stock_and_cash_merger``
  -> :class:`Merger` (``acquiree_symbol`` -> ``acquirer_symbol`` or cash)

Used by the weekly universe validation (notes of deactivated tickers) and by
:class:`~trading_signals.universe.index_sync.IndexSyncer` (rename detection).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date

import requests

from trading_signals.config import get_settings
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retry import retry

logger = get_logger(__name__)

DATA_BASE_URL = "https://data.alpaca.markets"
CORPORATE_ACTIONS_URL = f"{DATA_BASE_URL}/v1/corporate-actions"

NAME_CHANGE_TYPE = "name_change"
MERGER_TYPES = ("cash_merger", "stock_merger", "stock_and_cash_merger")

#: Symbols per request (keeps the URL short; pagination covers the rest).
SYMBOL_BATCH_SIZE = 25
PAGE_LIMIT = 1000


@dataclass(frozen=True)
class NameChange:
    """Alpaca ``name_change``: ``old_symbol`` became ``new_symbol``."""

    old_symbol: str
    new_symbol: str
    process_date: date | None


@dataclass(frozen=True)
class Merger:
    """Alpaca merger: ``acquiree_symbol`` was absorbed.

    ``acquirer_symbol`` is None for an all-cash deal.
    """

    acquiree_symbol: str
    acquirer_symbol: str | None
    effective_date: date | None
    kind: str


@dataclass
class CorporateEvents:
    """Name changes and mergers returned for a set of symbols."""

    name_changes: list[NameChange] = field(default_factory=list)
    mergers: list[Merger] = field(default_factory=list)

    def renames_of(self, ticker: str) -> list[NameChange]:
        t = ticker.upper()
        return [c for c in self.name_changes if c.old_symbol.upper() == t]

    def mergers_of(self, ticker: str) -> list[Merger]:
        t = ticker.upper()
        return [m for m in self.mergers if m.acquiree_symbol.upper() == t]


def _parse_date(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _batches(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class AlpacaCorporateActions:
    """Thin client for the Alpaca corporate-actions endpoint."""

    def __init__(self) -> None:
        settings = get_settings()
        self._headers = {
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
        }

    @retry(max_attempts=3, base_delay=2.0)
    def _get(self, params: dict) -> dict:
        response = requests.get(
            CORPORATE_ACTIONS_URL, headers=self._headers, params=params, timeout=30
        )
        response.raise_for_status()
        return response.json()

    def fetch(
        self,
        symbols: Iterable[str],
        start: date,
        end: date,
        include_mergers: bool = True,
    ) -> CorporateEvents:
        """Name changes (and mergers) involving ``symbols`` in ``[start, end]``.

        Alpaca matches a symbol against the old *and* the new symbol of an
        action; callers filter with :meth:`CorporateEvents.renames_of` /
        :meth:`CorporateEvents.mergers_of`.
        """
        types = [NAME_CHANGE_TYPE]
        if include_mergers:
            types += MERGER_TYPES
        events = CorporateEvents()
        unique = sorted({s.strip().upper() for s in symbols if s and s.strip()})
        for batch in _batches(unique, SYMBOL_BATCH_SIZE):
            token: str | None = None
            while True:
                params = {
                    "symbols": ",".join(batch),
                    "types": ",".join(types),
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "limit": PAGE_LIMIT,
                }
                if token:
                    params["page_token"] = token
                payload = self._get(params)
                self._collect(payload.get("corporate_actions") or {}, events)
                token = payload.get("next_page_token")
                if not token:
                    break
        return events

    @staticmethod
    def _collect(actions: dict, events: CorporateEvents) -> None:
        for item in actions.get("name_changes") or []:
            old, new = item.get("old_symbol"), item.get("new_symbol")
            if old and new:
                events.name_changes.append(
                    NameChange(old, new, _parse_date(item.get("process_date")))
                )
        for kind in MERGER_TYPES:
            for item in actions.get(f"{kind}s") or []:
                acquiree = item.get("acquiree_symbol")
                if acquiree:
                    events.mergers.append(
                        Merger(
                            acquiree,
                            item.get("acquirer_symbol") or None,
                            _parse_date(
                                item.get("effective_date") or item.get("process_date")
                            ),
                            kind,
                        )
                    )
