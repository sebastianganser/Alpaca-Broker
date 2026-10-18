"""Section 1 - head: price, sector, size, liquidity, index membership, coverage."""

from __future__ import annotations

from trading_signals.derived.context_pack.fmt import (
    NA,
    bullets,
    day,
    money,
    num,
)
from trading_signals.derived.context_pack.model import TickerContext

INDEX_LABELS = {"sp500": "S&P 500", "nasdaq100": "Nasdaq-100"}

#: German explanation of the quality-gate reason codes (``analysis/screen.py``).
GATE_REASON_TEXT = {
    "not_in_universe": "nicht im Universum",
    "low_liquidity": "Handelsvolumen unter dem Gate-Minimum",
    "short_history": "zu wenig Kurshistorie",
    "few_groups": "zu wenige Datengruppen befüllt",
}


def membership_text(ctx: TickerContext) -> str:
    """Index membership as a plain fact (no reading of it)."""
    parts = []
    for m in sorted(ctx.raw.memberships, key=lambda m: m.index_name):
        label = INDEX_LABELS.get(m.index_name, m.index_name)
        if m.source == "initial_seed":
            parts.append(f"{label} (Mitglied, Aufnahmedatum nicht bekannt)")
        else:
            parts.append(f"{label} (seit {day(m.valid_from)})")
    return ", ".join(parts) if parts else "kein S&P 500 / Nasdaq-100 erfasst"


def coverage_text(ctx: TickerContext) -> str:
    """``trend 3/3 · value 2/3 ...``; groups below the screen's threshold flagged."""
    from trading_signals.derived.context_pack.model import group_sizes

    sizes = group_sizes()
    parts = []
    for name, (n_present, n_total) in ctx.coverage.items():
        needed = sizes.get(name, (n_total, n_total))[1]
        flag = "" if n_present >= needed else " (zu wenig)"
        parts.append(f"{name} {n_present}/{n_total}{flag}")
    return " · ".join(parts)


def render_head(ctx: TickerContext) -> list[str]:
    lines = ["## 1. Kopf", ""]
    cap = ctx.fval("market_cap")
    dv = ctx.val("dollar_volume_20d")
    cap_pct = ctx.pct("fund_market_cap", cap, sector=True)
    cap_txt = (
        f"{money(cap)} ({cap_pct}; Stand {day(ctx.fund_date)})"
        if cap is not None
        else NA
    )
    dv_txt = (
        f"{money(dv)} ({ctx.pct('dollar_volume_20d', dv)})" if dv is not None else NA
    )
    sector = ctx.info.sector or NA
    industry = ctx.info.industry or NA
    lines += bullets(
        [
            (
                "Kurs (Schluss)",
                f"{num(ctx.close)} USD" if ctx.close is not None else NA,
            ),
            (
                "Sektor / Branche",
                f"{sector} / {industry}",
            ),
            ("Marktkapitalisierung", cap_txt),
            ("Handelsvolumen (Ø 20 T)", dv_txt),
            ("Indexzugehörigkeit", membership_text(ctx)),
            (
                "Datenabdeckung (Merkmale je Gruppe)",
                coverage_text(ctx),
            ),
        ]
    )
    if not ctx.gate_ok:
        reasons = [r for r in ctx.gate_reasons.split(";") if r]
        why = ", ".join(GATE_REASON_TEXT.get(r, r) for r in reasons) or NA
        lines += [
            "",
            f"> **Qualitäts-Gate nicht bestanden:** {why}. Der Ticker steht nicht "
            "in `screen.csv`; die Perzentile beziehen sich trotzdem auf die "
            f"{ctx.xs.n_universe} Gate-Ticker.",
        ]
    lines.append("")
    return lines
