"""Sections 2, 10, 11 - trend / momentum, sector / relative, liquidity."""

from __future__ import annotations

from trading_signals.derived.context_pack.constants import RETURN_HORIZONS
from trading_signals.derived.context_pack.fmt import (
    NA,
    age_text,
    day,
    md_table,
    money,
    num,
    percent,
    signed,
    signed_percent,
)
from trading_signals.derived.context_pack.model import TickerContext, metric_group

RETURN_COLUMN = "ret_{h}d"


def render_trend(ctx: TickerContext) -> list[str]:
    stat = ctx.raw.stats.get("prices")
    lines = [
        "## 2. Trend und Momentum",
        "",
        "Quelle: Schlusskurse, Stand "
        f"{age_text(stat.last if stat else None, ctx.session)}.",
        "",
    ]
    rows = []
    for h in RETURN_HORIZONS:
        r = ctx.returns.get(h)
        rows.append(
            [
                f"{h} T",
                signed_percent(r),
                signed_percent(ctx.spy_returns.get(h)),
                ctx.pct(RETURN_COLUMN.format(h=h), r, sector=True),
            ]
        )
    lines += md_table(["Zeitraum", "Rendite", "SPY", "P · S"], rows)
    lines.append("")

    tech = ctx.raw.technical
    if tech is None or tech.macd is None:
        macd = NA
    else:
        macd = (
            f"{num(tech.macd)} / Signal {num(tech.macd_signal)} / "
            f"Histogramm {signed(tech.macd_histogram)}"
        )
    lines += [
        metric_group(
            [
                ctx.metric("Abstand SMA50", "price_vs_sma50", signed_percent),
                ctx.metric("SMA200", "price_vs_sma200", signed_percent),
                ctx.metric("52-Wochen-Hoch", "st_dist_52w_high", signed_percent),
            ]
        ),
        metric_group(
            [
                ctx.metric("RSI(14)", "rsi_14", lambda v: num(v, 1)),
                ctx.metric(
                    "ATR(14) in % des Kurses (nur Risikoinfo)", "atr_14_pct", percent
                ),
            ]
        ),
        f"- MACD: {macd}",
        *_range_and_levels(ctx),
        "",
    ]
    return lines


def _range_and_levels(ctx: TickerContext) -> list[str]:
    """52-week range, support / resistance levels and the chart reference."""
    price = ctx.price
    rng = price.range52 if price else None
    if rng is None:
        lines = [
            f"- 52-Wochen-Spanne: {NA} "
            "(zu wenig Kurshistorie oder kein Kurs am Pack-Tag)"
        ]
    else:
        pos = rng.position
        lines = [
            f"- 52-Wochen-Spanne: Tief {num(rng.low)} "
            f"({day(rng.low_date)}) – Hoch {num(rng.high)} ({day(rng.high_date)}); "
            f"Abstand zum Tief {signed_percent(rng.dist_to_low)}, zum Hoch "
            f"{signed_percent(rng.dist_to_high)}; Position "
            f"{'n/a (flache Spanne)' if pos is None else percent(pos, 0)} "
            "(0 % = Tief, 100 % = Hoch)"
        ]
    if price is None:
        lines.append(f"- Unterstützung / Widerstand: {NA}")
    else:
        lines.append(
            "- Unterstützung (252 Sitzungen; Preis, Abstand, Berührungen = "
            "Swing-Punkte im selben Preisband, letzte Berührung): "
            + _levels_text(price.supports, "keine bestätigte Ebene unter dem Kurs")
        )
        lines.append(
            "- Widerstand (über dem Kurs): "
            + _levels_text(price.resistances, "keine bestätigte Ebene über dem Kurs")
        )
    if ctx.chart_file:
        lines += [
            "",
            f"![Chart {ctx.ticker}]({ctx.chart_file})",
            "",
            f"Chart `{ctx.chart_file}` im selben Ordner: Tageskerzen eines Jahres, "
            "SMA50 / SMA200, 52-Wochen-Spanne, S / R, Volumen.",
        ]
    return lines


def _levels_text(levels, empty: str) -> str:
    if not levels:
        return empty
    return " · ".join(
        f"{lv.price:.2f} ({signed_percent(lv.distance)}, {lv.touches}×, "
        f"zuletzt {day(lv.last_touch)})"
        for lv in levels
    )


def render_sector(ctx: TickerContext) -> list[str]:
    lines = ["## 10. Sektor und relative Stärke", ""]
    lines.append(
        metric_group(
            [
                ctx.metric(
                    "Rendite 20 T minus Sektor-ETF",
                    "sector_relative_return_20d",
                    signed_percent,
                ),
                ctx.metric("minus SPY", "relative_strength_spy", signed_percent),
                ctx.metric(
                    "Abstand SMA50 minus Sektor-ETF",
                    "sector_relative_momentum",
                    signed_percent,
                ),
            ]
        )
    )
    parts = []
    for h in (20, 60):
        median, n = ctx.xs.sector_median(RETURN_COLUMN.format(h=h), ctx.info.sector)
        mine = ctx.returns.get(h)
        if median is None:
            parts.append(f"{h} T {NA} (Sektor mit weniger als 10 Gate-Tickern)")
        else:
            diff = None if mine is None else mine - median
            parts.append(
                f"{h} T {signed_percent(median)} (n={n}; Ticker minus Median "
                f"{signed_percent(diff)})"
            )
    lines += ["- Sektor-Median der Rendite: " + " · ".join(parts), ""]
    return lines


def render_liquidity(ctx: TickerContext) -> list[str]:
    return [
        "## 11. Liquidität",
        "",
        metric_group(
            [
                ctx.metric("Handelsvolumen Ø 20 T", "dollar_volume_20d", money),
                ctx.metric("Volumen heute / Ø 20 T", "volume_ratio_20d", num),
                ctx.metric(
                    "Amihud-Illiquidität (höher = weniger liquide)",
                    "amihud_illiquidity_20d",
                    lambda v: f"{v:.3g}",
                ),
            ]
        ),
        "",
    ]
