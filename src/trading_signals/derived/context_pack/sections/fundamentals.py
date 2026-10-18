"""Sections 3 and 4 - valuation (relative to the sector) and fundamentals."""

from __future__ import annotations

from datetime import date

from trading_signals.derived.context_pack.fmt import (
    DASH,
    NA,
    age_text,
    money,
    num,
    percent,
    signed_percent,
)
from trading_signals.derived.context_pack.model import (
    TickerContext,
    metric_group,
    metric_lines,
)


def _multiple(v: float) -> str:
    """A valuation multiple; a negative one is shown as such with a hint."""
    return f"{v:.1f}" + (" (negativ)" if v < 0 else "")


def render_valuation(ctx: TickerContext) -> list[str]:
    lines = [
        "## 3. Bewertung (relativ zum Sektor)",
        "",
        "Quelle: Fundamentals-Snapshot (Yahoo), Stand "
        f"{age_text(ctx.fund_date, ctx.session)}. Hohes Perzentil = teurer.",
        "",
    ]
    loss = (ctx.val("profit_margin") or 0.0) < 0
    pe = ctx.metric("KGV (trailing)", "pe_ratio", _multiple, sector=True)
    if ctx.val("pe_ratio") is None and loss:
        pe = ["KGV (trailing)", "n/a (Verlust)", DASH]
    lines += metric_lines(
        [
            pe,
            ctx.metric("KGV (Forward)", "forward_pe", _multiple, sector=True),
            ctx.metric("KUV", "ps_ratio", _multiple, sector=True),
            ctx.fund_metric("KBV", "pb_ratio", _multiple, sector=True),
            ctx.fund_metric("EV/EBITDA", "ev_ebitda", _multiple, sector=True),
            ctx.fund_metric(
                "Dividendenrendite", "dividend_yield", percent, sector=True
            ),
        ]
    )
    lines.append("")
    return lines


def _amount(label: str, value: float | None, fmt) -> list[str]:
    """A metric row without percentile (an absolute amount, not a ratio)."""
    return [label, NA if value is None else fmt(value), DASH]


def render_fundamentals(ctx: TickerContext) -> list[str]:
    quarter = ctx.fund.get("most_recent_quarter")
    quarter = quarter.isoformat() if isinstance(quarter, date) else NA
    return [
        "## 4. Fundamentals",
        "",
        f"Berichtsquartal der TTM-Werte: {quarter}.",
        "",
        metric_group(
            [
                _amount("Umsatz TTM", ctx.fval("revenue_ttm"), money),
                _amount("EPS TTM", ctx.fval("eps_ttm"), num),
                ctx.fund_metric(
                    "Current Ratio",
                    "current_ratio",
                    num,
                    sector=True,
                ),
            ]
        ),
        metric_group(
            [
                ctx.metric(
                    "Umsatzwachstum J/J",
                    "revenue_growth_yoy",
                    signed_percent,
                    sector=True,
                ),
                ctx.fund_metric(
                    "EPS-Wachstum J/J", "eps_growth_yoy", signed_percent, sector=True
                ),
            ]
        ),
        metric_group(
            [
                ctx.metric("Gewinnmarge", "profit_margin", percent, sector=True),
                ctx.fund_metric(
                    "Operative Marge", "operating_margin", percent, sector=True
                ),
                ctx.metric("Margen-Trend 4 W (Steigung)", "margin_trend_4w", num),
            ]
        ),
        metric_group(
            [
                ctx.fund_metric(
                    "Eigenkapitalrendite", "return_on_equity", percent, sector=True
                ),
                ctx.metric(
                    "Verschuldung D/E (Yahoo, in %)",
                    "debt_to_equity",
                    lambda v: num(v, 1),
                    sector=True,
                ),
                ctx.fund_metric("Beta", "beta", num, sector=True),
            ]
        ),
        "",
    ]
