"""Known ticker renames (same company, new symbol) and helpers.

Why this exists
---------------
Index change tables (Wikipedia, GitHub snapshots) are keyed by the ticker that
was valid on the day of the event. A ticker rename is **not** an index exit:
Facebook did not leave the S&P 500 when ``FB`` became ``META``. Convention for
``index_membership``:

* a rename keeps **one continuous interval keyed by the CURRENT symbol**
  (e.g. ``META`` from 2021-01-01, ``BNY`` from 2021-01-01);
* the old symbol gets **no interval of its own** -- several old symbols are
  reused by unrelated securities today (``FB`` = ProShares ETF, ``LB`` =
  LandBridge, ``PARA`` = Banzai, ``INFO`` = Harbor ETF);
* real exits (mergers, takeovers, demotions) end an interval at the effective
  removal date.

Prices of the old symbol are still needed for backfills (Alpaca serves e.g.
``CDAY`` bars only under the old symbol): use :func:`symbol_history` /
:func:`current_symbol` with ``as_of`` for that.

Source of the list
------------------
The 21 ``rename`` rows of ``docs/reports/2026-10-16_sp500_removals.csv``
(Alpaca ``name_change`` corporate actions, Wikipedia reason "changed its ticker
symbol"), plus FBHS->FBIN, PSTG->P and PSKY->SKYD which the inventory
documents separately. Dates are the Alpaca ``process_date`` (first day under
the new symbol).

Deliberately NOT listed
-----------------------
* ``PARA -> PSKY`` (Paramount/Skydance 2025-08-08), ``DISCA``/``DISCK -> WBD``:
  corporate mergers with their own price history, counted as real exits.
* ``GPS -> GAP`` (2024-08-22): the rename happened *after* the 2022 exit; the
  exit stays keyed by the symbol that traded during the membership.
* Collisions (a symbol re-used by another company) are never aliases, e.g.
  ``BNZI -> PARA`` (Banzai, 2026-08-07) or ``FI -> XPRO`` (2021).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from typing import NamedTuple

from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)


class TickerAlias(NamedTuple):
    """One rename: ``old`` became ``new`` on ``effective_date``."""

    old: str
    new: str
    effective_date: date
    note: str = ""


#: Chronological list of known renames (see module docstring for the source).
KNOWN_RENAMES: tuple[TickerAlias, ...] = (
    TickerAlias(
        "LB", "BBWI", date(2021, 8, 3),
        "L Brands renamed Bath & Body Works (before the data window)",
    ),
    TickerAlias(
        "COG", "CTRA", date(2021, 10, 4),
        "Cabot Oil & Gas merged with Cimarex and became Coterra Energy",
    ),
    TickerAlias(
        "WLTW", "WTW", date(2022, 1, 10),
        "Willis Towers Watson changed its ticker symbol (Wikipedia reason text)",
    ),
    TickerAlias(
        "VIAC", "PARA", date(2022, 2, 17),
        "ViacomCBS became Paramount Global; PARA today is Banzai, so Paramount's "
        "own series needs an explicit alias key in repairs",
    ),
    TickerAlias(
        "BLL", "BALL", date(2022, 5, 10),
        "Ball Corporation ticker change",
    ),
    TickerAlias(
        "FB", "META", date(2022, 6, 9),
        "Facebook became Meta Platforms; FB is a ProShares ETF today",
    ),
    TickerAlias(
        "ANTM", "ELV", date(2022, 6, 28),
        "Anthem became Elevance Health",
    ),
    TickerAlias(
        "NLOK", "GEN", date(2022, 11, 8),
        "NortonLifeLock became Gen Digital",
    ),
    TickerAlias(
        "FBHS", "FBIN", date(2022, 12, 15),
        "Fortune Brands Home & Security became Fortune Brands Innovations; the "
        "company left the S&P 500 on 2022-12-19 (real exit, keyed FBIN)",
    ),
    TickerAlias(
        "PKI", "RVTY", date(2023, 5, 16),
        "PerkinElmer became Revvity",
    ),
    TickerAlias(
        "FISV", "FI", date(2023, 6, 7),
        "Fiserv switched its ticker to FI (back to FISV on 2025-11-11)",
    ),
    TickerAlias(
        "RE", "EG", date(2023, 7, 10),
        "Everest Re Group became Everest Group",
    ),
    TickerAlias(
        "ABC", "COR", date(2023, 8, 30),
        "AmerisourceBergen became Cencora",
    ),
    TickerAlias(
        "CDAY", "DAY", date(2024, 2, 1),
        "Ceridian changed its ticker symbol (Wikipedia reason text); the company "
        "(Dayforce) left the S&P 500 on 2026-02-09 -- Alpaca serves pre-2024-02-01 "
        "bars only under CDAY",
    ),
    TickerAlias(
        "PEAK", "DOC", date(2024, 3, 4),
        "Healthpeak Properties ticker change",
    ),
    TickerAlias(
        "FLT", "CPAY", date(2024, 3, 25),
        "FleetCor became Corpay (CUSIP changed as well)",
    ),
    TickerAlias(
        "WRK", "SW", date(2024, 7, 8),
        "WestRock became Smurfit Westrock; Alpaca lists a name_change with the "
        "same CUSIP, so it is treated as a rename (open question: new entity?)",
    ),
    TickerAlias(
        "FI", "FISV", date(2025, 11, 11),
        "Fiserv switched back to FISV",
    ),
    TickerAlias(
        "MMC", "MRSH", date(2026, 1, 14),
        "Marsh & McLennan became Marsh",
    ),
    TickerAlias(
        "PSTG", "P", date(2026, 4, 17),
        "Pure Storage became Everpure; P was added to the S&P 500 on 2026-09-21",
    ),
    TickerAlias(
        "BK", "BNY", date(2026, 5, 21),
        "Bank of New York Mellon became BNY",
    ),
    TickerAlias(
        "SATS", "ECHO", date(2026, 6, 24),
        "EchoStar ticker change",
    ),
    TickerAlias(
        "EQR", "VMRK", date(2026, 8, 18),
        "Equity Residential became Vivmark Residential",
    ),
    TickerAlias(
        "PSKY", "SKYD", date(2026, 10, 6),
        "Paramount Skydance became Skydance Corporation (Alpaca name_change; in "
        "neither the Wikipedia nor the GitHub change history)",
    ),
)

#: Symbols that were re-used by an unrelated security (never aliases).
SYMBOL_REUSE: dict[str, str] = {
    "FB": "ProShares ETF (Facebook until 2022-06-09)",
    "LB": "LandBridge (L Brands until 2021-08-03)",
    "PARA": "Banzai International since 2026-08-07 (Paramount Global before)",
    "INFO": "Harbor PanAgora ETF (IHS Markit until 2022-03-02)",
    "FI": "Frank's International until 2021-10-04 (Fiserv 2023-06-07..2025-11-11)",
}

#: Slack when matching an event date against a rename date (see ``canonical_symbol``).
EVENT_TOLERANCE_DAYS = 7

# (symbol, first day under this symbol or None, first day under the next symbol or None)
_Period = tuple[str, date | None, date | None]


def _norm(ticker: str) -> str:
    return str(ticker).strip().upper()


class AliasMap:
    """Rename graph with per-company symbol histories.

    The renames of one company must form a chain (``A->B``, ``B->C``, even
    ``A->B->A``); anything else raises :class:`ValueError` -- a non-linear list
    is a data error, not something to guess around.
    """

    def __init__(self, aliases: Iterable[TickerAlias] = KNOWN_RENAMES) -> None:
        self._aliases: tuple[TickerAlias, ...] = tuple(
            sorted(
                (
                    TickerAlias(_norm(a.old), _norm(a.new), a.effective_date, a.note)
                    for a in aliases
                ),
                key=lambda a: (a.effective_date, a.old, a.new),
            )
        )
        self._history: dict[str, list[_Period]] = {}
        self._build()

    @property
    def aliases(self) -> tuple[TickerAlias, ...]:
        return self._aliases

    def _build(self) -> None:
        # connected components over symbols
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a in self._aliases:
            if a.old == a.new:
                raise ValueError(f"alias maps {a.old} to itself")
            parent[find(a.old)] = find(a.new)

        components: dict[str, list[TickerAlias]] = {}
        for a in self._aliases:  # already chronological
            components.setdefault(find(a.old), []).append(a)

        for edges in components.values():
            first = edges[0]
            periods: list[_Period] = []
            symbol, start = first.old, None
            for edge in edges:
                if edge.old != symbol:
                    raise ValueError(
                        f"renames of one company are not a chain: expected "
                        f"{symbol}->?, got {edge.old}->{edge.new} "
                        f"({edge.effective_date})"
                    )
                periods.append((symbol, start, edge.effective_date))
                symbol, start = edge.new, edge.effective_date
            periods.append((symbol, start, None))
            for sym, _, _ in periods:
                self._history[sym] = periods

    def knows(self, ticker: str) -> bool:
        return _norm(ticker) in self._history

    def symbol_history(self, ticker: str) -> list[tuple[str, date | None, date | None]]:
        """Chronological ``(symbol, valid_from, valid_to)`` of the company.

        ``valid_from``/``valid_to`` are the first day under the symbol and the
        first day under the next one (``None`` = open). Unknown tickers return
        a single open period.
        """
        t = _norm(ticker)
        return list(self._history.get(t, [(t, None, None)]))

    def current_symbol(self, ticker: str, as_of: date | None = None) -> str:
        """Symbol the company behind ``ticker`` uses on ``as_of``.

        ``as_of=None`` is the latest known symbol (``FB`` -> ``META``,
        ``FI`` -> ``FISV``). With a date it works in both directions:
        ``current_symbol("DAY", date(2022, 1, 1))`` is ``CDAY``. Unknown
        tickers are returned unchanged (upper-cased).
        """
        t = _norm(ticker)
        periods = self._history.get(t)
        if not periods:
            return t
        if as_of is None:
            return periods[-1][0]
        for symbol, start, end in periods:
            if (start is None or start <= as_of) and (end is None or as_of < end):
                return symbol
        return periods[-1][0]  # unreachable: periods tile the time line

    def canonical_symbol(
        self,
        ticker: str,
        on_date: date,
        tolerance_days: int = EVENT_TOLERANCE_DAYS,
    ) -> str:
        """Key for an index event of ``ticker`` dated ``on_date``.

        Returns the company's latest symbol if the company really traded as
        ``ticker`` around ``on_date`` (within ``tolerance_days`` of the
        symbol's period -- index sources date a rename event a few days off
        the Alpaca process date), otherwise ``ticker`` unchanged: the symbol
        was re-used by another company (``Q`` was Quintiles until 2017, then
        Qnity from 2025).
        """
        t = _norm(ticker)
        periods = self._history.get(t)
        if not periods:
            return t
        tol = timedelta(days=tolerance_days)
        for symbol, start, end in periods:
            if symbol != t:
                continue
            if (start is None or on_date >= start - tol) and (
                end is None or on_date <= end + tol
            ):
                return periods[-1][0]
        return t

    def aliases_of(self, ticker: str) -> list[str]:
        """Other symbols of the same company, in chronological order of use."""
        t = _norm(ticker)
        seen: list[str] = []
        for symbol, _, _ in self._history.get(t, []):
            if symbol != t and symbol not in seen:
                seen.append(symbol)
        return seen

    def with_extra(self, extra: Iterable[TickerAlias]) -> AliasMap:
        """Copy with additional renames (e.g. detected in a change table).

        Extras duplicating a known ``old -> new`` pair are ignored (the
        curated entry wins); extras that would break the chain property are
        skipped with a warning.
        """
        known = {(a.old, a.new) for a in self._aliases}
        merged = list(self._aliases)
        for alias in extra:
            norm = TickerAlias(
                _norm(alias.old), _norm(alias.new), alias.effective_date, alias.note
            )
            if (norm.old, norm.new) in known:
                continue
            try:
                AliasMap([*merged, norm])
            except ValueError as e:
                logger.warning(
                    f"[ticker_aliases] ignoring rename {norm.old}->{norm.new} "
                    f"({norm.effective_date}): {e}"
                )
                continue
            merged.append(norm)
            known.add((norm.old, norm.new))
        return AliasMap(merged)


#: Alias map of the curated list.
DEFAULT_ALIASES = AliasMap(KNOWN_RENAMES)


def current_symbol(ticker: str, as_of: date | None = None) -> str:
    """See :meth:`AliasMap.current_symbol` (curated list)."""
    return DEFAULT_ALIASES.current_symbol(ticker, as_of)


def aliases_of(ticker: str) -> list[str]:
    """See :meth:`AliasMap.aliases_of` (curated list)."""
    return DEFAULT_ALIASES.aliases_of(ticker)


def symbol_history(ticker: str) -> list[tuple[str, date | None, date | None]]:
    """See :meth:`AliasMap.symbol_history` (curated list)."""
    return DEFAULT_ALIASES.symbol_history(ticker)
