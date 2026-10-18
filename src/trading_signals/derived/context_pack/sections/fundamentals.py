"""Sections 3 and 4 - valuation (relative to the sector) and fundamentals."""

from __future__ import annotations

from datetime import date

from trading_signals.derived.context_pack.constants import QUARTER_ROWS
from trading_signals.derived.context_pack.financials import (
    free_cash_flow,
    margin,
    newest_first,
    revenue_growth_yoy,
    shares_change,
)
from trading_signals.derived.context_pack.fmt import (
    DASH,
    NA,
    age_text,
    day,
    md_table,
    millions,
    money,
    num,
    percent,
    shares,
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
        *_yahoo_balance_lines(ctx),
        *quarterly_lines(ctx),
        "",
    ]


def _yahoo_balance_lines(ctx: TickerContext) -> list[str]:
    """Cash flow, cash, debt, gross margin and share counts of the Yahoo snapshot."""
    return [
        metric_group(
            [
                _amount("Yahoo: Freier Cashflow TTM", ctx.fval("free_cashflow"), money),
                _amount("Cash", ctx.fval("total_cash"), money),
                _amount("Gesamtschulden", ctx.fval("total_debt"), money),
                ctx.fund_metric("Bruttomarge", "gross_margins", percent, sector=True),
            ]
        ),
        metric_group(
            [
                _amount(
                    "Yahoo: Aktien im Umlauf", ctx.fval("shares_outstanding"), shares
                ),
                _amount("Streubesitz (Float)", ctx.fval("float_shares"), shares),
            ]
        ),
    ]


def quarterly_lines(ctx: TickerContext) -> list[str]:
    """The quarterly table (SEC XBRL) and the one-year change of the share count."""
    quarters = newest_first(ctx.raw.quarters)
    lines = [
        "",
        "**Quartalszahlen (SEC XBRL, Mio. USD, wie zuerst berichtet, nur bis zum "
        "Pack-Tag eingereicht)**",
        "",
    ]
    if not quarters:
        return [
            *lines,
            f"- Quartalszahlen (SEC XBRL): {NA} (bis zum Pack-Tag kein US-GAAP-Quartal "
            "veröffentlicht oder erfasst, z. B. ausländischer Emittent)",
        ]
    rows = [
        [
            day(q.period_end),
            q.fiscal_period or DASH,
            q.form or DASH,
            day(q.filed),
            millions(q.revenue),
            signed_percent(revenue_growth_yoy(q, quarters)),
            percent(margin(q.gross_profit, q.revenue)),
            percent(margin(q.operating_income, q.revenue)),
            millions(q.net_income),
            num(q.eps_diluted),
            millions(free_cash_flow(q)),
            millions(q.cash_and_equivalents),
            millions(q.total_debt),
        ]
        for q in quarters[:QUARTER_ROWS]
    ]
    lines += [
        "FCF = operativer Cashflow minus Investitionen; J/J nur mit Vorjahresquartal.",
        "",
        *md_table(
            [
                "Quartalsende",
                "Quartal",
                "Formular",
                "Eingereicht",
                "Umsatz",
                "Umsatz J/J",
                "Bruttomarge",
                "Op.-Marge",
                "Nettogewinn",
                "EPS verw.",
                "FCF",
                "Cash",
                "Schulden",
            ],
            rows,
        ),
        "",
        _shares_line(ctx),
    ]
    return lines


def _shares_line(ctx: TickerContext) -> str:
    """Change of the XBRL share count over about a year (buyback / dilution)."""
    change = shares_change(ctx.raw.quarters)
    if change is None:
        return (
            f"- Aktienzahl-Veränderung ca. 1 Jahr (XBRL): {NA} (keine Aktienzahl im "
            "Quartal und im Vorjahresquartal)"
        )
    return (
        f"- Aktienzahl (XBRL): {shares(change.latest)} ({day(change.latest_end)}) vs. "
        f"{shares(change.prior)} ({day(change.prior_end)}): "
        f"{signed_percent(change.change)} in ca. 1 Jahr, {change.verdict}."
    )
