"""Section 14 - peers (same industry, closest market cap)."""

from __future__ import annotations

from trading_signals.derived.context_pack.fmt import (
    NA,
    clean_text,
    md_table,
    money,
    signed_percent,
    to_float,
)
from trading_signals.derived.context_pack.model import TickerContext
from trading_signals.derived.context_pack.rows import PeerRow


def _forward_pe(value: float | None) -> str:
    v = to_float(value)
    if v is None:
        return NA
    return f"{v:.1f}" + (" (negativ)" if v < 0 else "")


def _row(row: PeerRow, own: bool = False) -> list[str]:
    name = clean_text(row.company, 24)
    return [
        f"**{row.ticker}** (dieser Ticker)" if own else row.ticker,
        name,
        money(row.market_cap),
        signed_percent(row.ret_60d),
        _forward_pe(row.forward_pe),
        signed_percent(row.eps_revision_30d),
    ]


def basis_text(ctx: TickerContext) -> str:
    """Where the peers come from (industry, topped up from the sector)."""
    t = ctx.peer_table
    assert t is not None
    parts = []
    if t.n_from_industry:
        parts.append(
            f"{t.n_from_industry} aus der Branche „{clean_text(t.industry, 40)}“ "
            f"(weitere Gate-Ticker der Branche: {t.n_industry_pool})"
        )
    if t.n_from_sector:
        parts.append(
            f"{t.n_from_sector} aus dem Sektor „{clean_text(t.sector, 40)}“ "
            "(zu wenige Branchen-Peers)"
        )
    return " und ".join(parts)


def render_peers(ctx: TickerContext) -> list[str]:
    lines = ["## 14. Peers (gleiche Branche)", ""]
    t = ctx.peer_table
    if t is None or (not t.industry and not t.sector):
        return [*lines, f"- Peers: {NA} (Branche und Sektor unbekannt)", ""]
    if not t.peers:
        scope = clean_text(t.industry or t.sector, 40)
        return [
            *lines,
            f"- Keine Peers: in „{scope}“ gibt es keine weiteren Ticker, die das "
            "Qualitäts-Gate bestanden haben.",
            "",
        ]
    lines += [
        f"Peers: {basis_text(ctx)}; nach ähnlichster Marktkapitalisierung.",
        "",
        *md_table(
            [
                "Ticker",
                "Unternehmen",
                "Marktkap.",
                "Rendite 60 T",
                "KGV (Forward)",
                "EPS-Revision 30 T",
            ],
            [_row(t.own, own=True), *[_row(p) for p in t.peers]],
        ),
        "",
    ]
    return lines
