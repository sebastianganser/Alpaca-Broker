"""Peer selection for the peer section (pure, deterministic).

Peers of a ticker are other **gate** tickers (the same population as the
percentiles and ``screen.csv``) of the same ``universe.industry``; if the
industry has fewer than :data:`PEER_ROWS` other gate tickers, the list is
topped up from the same ``universe.sector``. Inside each group the peers
closest in market cap come first: smallest ``|ln(cap / own cap)|``, ties by
ticker. Without an own market cap the largest peers come first. A peer
without a market cap ranks after every peer with one. The same inputs always
give the same peers - no randomness, no dependence on dictionary order.

``universe.sector`` / ``industry`` are the current values, not point-in-time
(documented limit of the pack).
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from trading_signals.derived.context_pack.constants import PEER_ROWS
from trading_signals.derived.context_pack.model import UniverseInfo


@dataclass(frozen=True)
class PeerSelection:
    """The chosen peers, industry peers first, then the sector top-up."""

    industry: str | None
    sector: str | None
    n_industry_pool: int
    industry_peers: tuple[str, ...]
    sector_peers: tuple[str, ...]

    @property
    def tickers(self) -> tuple[str, ...]:
        return (*self.industry_peers, *self.sector_peers)


def _cap(caps: Mapping[str, float | None], ticker: str) -> float | None:
    value = caps.get(ticker)
    if value is None or not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def _ranked(
    pool: list[str], own_cap: float | None, caps: Mapping[str, float | None]
) -> list[str]:
    def key(t: str) -> tuple[int, float, str]:
        cap = _cap(caps, t)
        if cap is None:
            return (1, 0.0, t)
        if own_cap is None:
            return (0, -cap, t)
        return (0, abs(math.log(cap / own_cap)), t)

    return sorted(pool, key=key)


def select_peers(
    ticker: str,
    universe: Mapping[str, UniverseInfo],
    gate: Collection[str],
    caps: Mapping[str, float | None],
    limit: int = PEER_ROWS,
) -> PeerSelection:
    """Up to ``limit`` peers of ``ticker`` among the gate tickers.

    Args:
        universe: company / sector / industry per ticker.
        gate: tickers that passed the quality gate (the population).
        caps: market cap per ticker (None / missing = unknown).
    """
    info = universe.get(ticker)
    industry = info.industry if info and info.industry else None
    sector = info.sector if info and info.sector else None
    own_cap = _cap(caps, ticker)
    gate_set = set(gate)

    def same(attr: str, value: str | None) -> list[str]:
        if value is None:
            return []
        return [
            t
            for t, i in universe.items()
            if t != ticker and t in gate_set and getattr(i, attr, None) == value
        ]

    industry_pool = same("industry", industry)
    industry_peers = _ranked(industry_pool, own_cap, caps)[:limit]
    sector_peers: list[str] = []
    if len(industry_peers) < limit:
        taken = set(industry_peers)
        rest = [t for t in same("sector", sector) if t not in taken]
        sector_peers = _ranked(rest, own_cap, caps)[: limit - len(industry_peers)]
    return PeerSelection(
        industry=industry,
        sector=sector,
        n_industry_pool=len(industry_pool),
        industry_peers=tuple(industry_peers),
        sector_peers=tuple(sector_peers),
    )


__all__ = ["PeerSelection", "select_peers"]
