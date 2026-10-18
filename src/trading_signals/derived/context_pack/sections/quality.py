"""Sections 12 and 13 - data quality / freshness and the market regime."""

from __future__ import annotations

from datetime import date

from trading_signals.derived.context_pack.fmt import (
    NA,
    day,
    num,
    percent,
    signed,
)
from trading_signals.derived.context_pack.model import (
    TickerContext,
    vix_regime_label,
)
from trading_signals.derived.context_pack.sections.filings import recent_filings
from trading_signals.derived.context_pack.sections.flows import placed_date

#: Sources of the freshness table, in display order: key -> German label.
SOURCE_LABELS: dict[str, str] = {
    "prices": "Kurse",
    "fundamentals": "Fundamentals",
    "estimates": "Konsens-Schätzungen",
    "analyst": "Analysten-Ratings",
    "earnings": "Earnings (letzte Meldung)",
    "insider": "Insider (Form 4)",
    "form13f": "13F-Filings",
    "ark": "ARK-Bestände",
    "politicians": "Politiker-Offenlegungen",
    "short_interest": "Short Interest (FINRA)",
    "short_volume": "Short Volume (FINRA)",
    "options_iv": "Optionen (IV)",
    "news": "News / FinBERT",
    "sec_filings": "SEC-Meldungen (EDGAR)",
    "financials": "Quartalszahlen (SEC XBRL)",
}
#: Sources that have only months of history (unstable percentiles).
YOUNG_SOURCES = ("estimates", "options_iv", "short_volume", "news")


def source_freshness(ctx: TickerContext) -> dict[str, date | None]:
    """Date of the newest observation per source (None = no coverage)."""
    raw = ctx.raw
    stats = raw.stats

    def stat_last(key: str) -> date | None:
        s = stats.get(key)
        return s.last if s else None

    insider = [placed_date(t) for t in raw.insider]
    insider = [d for d in insider if d is not None]
    ark = [a.snapshot_date for a in raw.ark]
    past = [e.earnings_date for e in raw.earnings if e.earnings_date < ctx.session]
    return {
        "prices": stat_last("prices"),
        "fundamentals": ctx.fund_date,
        "estimates": stat_last("estimates"),
        "analyst": max((r.rating_date for r in raw.ratings), default=None),
        "earnings": max(past, default=None),
        "insider": max(insider, default=None),
        "form13f": raw.holders.max_filing_date if raw.holders else None,
        "ark": max(ark, default=None),
        "politicians": max(
            (p.disclosure_date for p in raw.politicians if p.disclosure_date),
            default=None,
        ),
        "short_interest": max(
            (s.settlement_date for s in raw.short_interest), default=None
        ),
        "short_volume": stat_last("short_volume"),
        "options_iv": max((i.snapshot_date for i in raw.iv), default=None),
        "news": stat_last("news"),
        # newest filing of the 90-day window / newest published quarter
        "sec_filings": max(
            (f.filing_date for f in recent_filings(raw, ctx.session)),
            default=None,
        ),
        "financials": max((q.filed for q in raw.quarters), default=None),
    }


def _history(ctx: TickerContext, key: str) -> str | None:
    s = ctx.raw.stats.get(key)
    if s is None or s.first is None:
        return None
    return f"{s.n_obs} (seit {day(s.first)})"


#: Short labels of the age line.
AGE_LABELS = {
    "prices": "Kurse",
    "fundamentals": "Fundamentals",
    "estimates": "Konsens",
    "analyst": "Ratings",
    "earnings": "letzte Earnings",
    "insider": "Insider",
    "form13f": "13F",
    "ark": "ARK",
    "politicians": "Politiker",
    "short_interest": "Short Interest",
    "short_volume": "Short Volume",
    "options_iv": "Optionen",
    "news": "News",
}


def render_quality(ctx: TickerContext) -> list[str]:
    fresh = source_freshness(ctx)
    ages = []
    for key, label in AGE_LABELS.items():
        last = fresh.get(key)
        ages.append(f"{label} {NA if last is None else (ctx.session - last).days}")
    young = []
    for key in ("prices", *YOUNG_SOURCES):
        hist = _history(ctx, key)
        young.append(f"{SOURCE_LABELS[key]} {hist if hist else NA}")
    return [
        "## 12. Datenqualität und Frische",
        "",
        "- Alter der Quellen in Tagen (letzter Stand, Daten im Kopf der Datei): "
        + " · ".join(ages),
        "- Historie (Tage mit Daten): " + " · ".join(young),
        "- Junge Quellen (Konsens, Optionen, News, Short Volume): nur Monate "
        "Historie, Perzentile instabil; IV-Rank erst ab 252 Tagen. Sektor und "
        "Branche sind der aktuelle Stand, nicht point-in-time.",
        "",
    ]


def render_regime(ctx: TickerContext) -> list[str]:
    return ["## 13. Marktregime", "", regime_line(ctx.market), ""]


def regime_line(market) -> str:
    """One-line market regime (shared with the overview)."""
    parts = [
        f"VIX {num(market.get('macro_vix'), 1)} "
        f"({vix_regime_label(market.get('macro_vix_regime'))})",
        f"Zinskurve 10J-2J {signed(market.get('macro_yield_spread'))}",
        f"HY-Spread {num(market.get('macro_hy_spread'))}",
        f"Dollar-Index {num(market.get('macro_dollar_index'), 1)}",
        f"Inflationserwartung {num(market.get('macro_inflation_expectation'))} %",
        f"Advance/Decline {num(market.get('breadth_advance_decline'))}",
        f"{percent(market.get('breadth_pct_above_sma50'), 0)} über SMA50",
        f"News-Sentiment Markt 7 T {signed(market.get('market_sentiment_7d'))}",
    ]
    return " · ".join(parts)


__all__ = [
    "SOURCE_LABELS",
    "regime_line",
    "render_quality",
    "render_regime",
    "source_freshness",
]
