"""Sections 5 and 6 - estimates / revisions / analysts, and earnings."""

from __future__ import annotations

from datetime import timedelta

from trading_signals.derived.context_pack.constants import (
    EARNINGS_PAST_ROWS,
    RATING_ROWS,
    RATING_WINDOW_DAYS,
)
from trading_signals.derived.context_pack.fmt import (
    DASH,
    NA,
    age_text,
    clean_text,
    day,
    integer,
    md_table,
    money,
    num,
    signed,
    signed_int,
    signed_percent,
    to_float,
)
from trading_signals.derived.context_pack.model import TickerContext, metric_group
from trading_signals.derived.context_pack.rows import EstimateRow

PERIOD_LABELS = {
    "0q": "lfd. Quartal",
    "+1q": "nächstes Quartal",
    "0y": "lfd. Jahr",
    "+1y": "nächstes Jahr",
}
PERIOD_ORDER = ("0q", "+1q", "0y", "+1y")

ACTION_LABELS = {
    "up": "Upgrade",
    "down": "Downgrade",
    "main": "bestätigt",
    "init": "Neuaufnahme",
    "reit": "wiederholt",
}

#: Minimum |EPS| 30 days ago for a revision percentage (as the feature).
_MIN_BASE = 0.001


def latest_estimates(rows: list[EstimateRow]) -> list[EstimateRow]:
    """The newest snapshot of each period, in :data:`PERIOD_ORDER`."""
    newest: dict[str, EstimateRow] = {}
    for r in rows:
        if r.period not in newest or r.as_of > newest[r.period].as_of:
            newest[r.period] = r
    return [newest[p] for p in PERIOD_ORDER if p in newest]


def _revision_pct(row: EstimateRow) -> float | None:
    cur, old = to_float(row.eps_current), to_float(row.eps_30d_ago)
    if cur is None or old is None or abs(old) <= _MIN_BASE:
        return None
    return (cur - old) / abs(old)


def _updown(up: int | None, down: int | None) -> str:
    if up is None or down is None:
        return NA
    return f"{up}↑/{down}↓"


def render_estimates(ctx: TickerContext) -> list[str]:
    estimates = latest_estimates(ctx.raw.estimates)
    stamp = max((e.as_of for e in estimates), default=None)
    lines = [
        "## 5. Schätzungen, Revisionen und Analysten",
        "",
        f"Quelle Konsens: Yahoo, Stand {age_text(stamp, ctx.session)}. "
        "Netto-Revisionen = Analysten mit höherer minus mit niedrigerer Schätzung.",
        "",
        metric_group(
            [
                ctx.metric("EPS-Revision 30 T", "eps_revision_pct_30d", signed_percent),
                ctx.metric("90 T", "eps_revision_pct_90d", signed_percent),
                ctx.metric(
                    "Umsatz-Revision 30 T", "revenue_revision_pct_30d", signed_percent
                ),
            ]
        ),
        metric_group(
            [
                ctx.metric("Netto-Revisionen 7 T", "eps_revisions_net_7d", signed_int),
                ctx.metric("30 T", "eps_revisions_net_30d", signed_int),
            ]
        ),
    ]
    if estimates:
        lines += [
            "",
            *md_table(
                [
                    "Periode",
                    "EPS",
                    "Δ 30 T",
                    "Analysten",
                    "Revis. 7 T",
                    "Revis. 30 T",
                    "Umsatz",
                ],
                [
                    [
                        PERIOD_LABELS.get(e.period, e.period),
                        num(e.eps_avg),
                        signed_percent(_revision_pct(e)),
                        integer(e.eps_n_analysts),
                        _updown(e.rev_up_7d, e.rev_down_7d),
                        _updown(e.rev_up_30d, e.rev_down_30d),
                        money(e.revenue_avg),
                    ]
                    for e in estimates
                ],
            ),
        ]
    else:
        lines += ["", f"- Konsens-Schätzungen: {NA}"]

    low, mean, high = (
        ctx.fval(k)
        for k in ("target_price_low", "target_price_mean", "target_price_high")
    )
    target = (
        f"{num(mean)} USD (Spanne {num(low)} - {num(high)})" if mean is not None else NA
    )
    lines += [
        "",
        "**Analysten**",
        "",
        metric_group(
            [
                ["Kursziel-Konsens (Mittel)", target, DASH],
                ctx.metric(
                    "Aufwärtspotenzial", "analyst_price_target_upside", signed_percent
                ),
            ]
        ),
        metric_group(
            [
                ctx.metric("Upgrades 30 T", "analyst_upgrades_30d", integer),
                ctx.metric("Downgrades 30 T", "analyst_downgrades_30d", integer),
                ctx.metric("Netto 60 T", "analyst_net_sentiment_60d", signed_int),
                ctx.metric("Upgrade-Serie", "analyst_upgrade_streak", integer),
                ctx.metric(
                    "Score letzte Aktion (up 1, bestätigt 0.5, down -1)",
                    "analyst_rating_score",
                    num,
                ),
            ]
        ),
    ]
    since = ctx.session - timedelta(days=RATING_WINDOW_DAYS)
    ratings = sorted(
        (r for r in ctx.raw.ratings if r.rating_date >= since),
        key=lambda r: r.rating_date,
        reverse=True,
    )[:RATING_ROWS]
    if ratings:
        lines += [
            "",
            *md_table(
                [
                    "Datum",
                    "Haus",
                    "Aktion",
                    "Rating (alt → neu)",
                    "Kursziel (alt → neu)",
                ],
                [
                    [
                        day(r.rating_date),
                        clean_text(r.firm, 28),
                        ACTION_LABELS.get(r.action or "", clean_text(r.action, 12)),
                        f"{clean_text(r.rating_old, 16)} → "
                        f"{clean_text(r.rating_new, 16)}",
                        f"{num(r.target_old, 0, DASH)} → {num(r.target_new, 0, DASH)}",
                    ]
                    for r in ratings
                ],
            ),
        ]
    else:
        lines += ["", f"- Rating-Aktionen der letzten {RATING_WINDOW_DAYS} Tage: keine"]
    lines.append("")
    return lines


def render_earnings(ctx: TickerContext) -> list[str]:
    d = ctx.session
    upcoming = sorted(
        (e for e in ctx.raw.earnings if e.earnings_date >= d),
        key=lambda e: e.earnings_date,
    )
    past = sorted(
        (e for e in ctx.raw.earnings if e.earnings_date < d),
        key=lambda e: e.earnings_date,
        reverse=True,
    )[:EARNINGS_PAST_ROWS]
    lines = ["## 6. Earnings", ""]
    if upcoming:
        nxt = upcoming[0]
        days = (nxt.earnings_date - d).days
        when = f" ({nxt.time_of_day})" if nxt.time_of_day else ""
        est = (
            f", EPS-Schätzung {num(nxt.eps_estimate)}"
            if nxt.eps_estimate is not None
            else ""
        )
        lines.append(
            "- **Nächster Termin (Information, kein Ausschlusskriterium):** "
            f"{day(nxt.earnings_date)}{when}, in {days} Tagen{est}"
        )
    else:
        lines.append(
            "- **Nächster Termin:** kein Termin in den nächsten 90 Tagen bekannt"
        )
    lines.append(
        metric_group(
            [
                ctx.metric("SUE letzte Meldung", "sue_last", signed),
                ctx.metric("Beat-Serie", "consecutive_beats", integer),
                ctx.metric(
                    "Ø Überraschung der letzten 3 Meldungen (%)",
                    "surprise_trend_3q",
                    lambda v: f"{v:+.1f}",
                ),
                ctx.metric(
                    "Kursreaktion auf die letzte Meldung",
                    "st_earnings_reaction",
                    signed_percent,
                ),
            ]
        )
    )
    if past:
        lines += [
            "",
            *md_table(
                ["Datum", "EPS Schätzung", "EPS tatsächlich", "Überraschung"],
                [
                    [
                        day(e.earnings_date),
                        num(e.eps_estimate),
                        num(e.eps_actual),
                        f"{e.surprise_pct:+.1f} %"
                        if e.surprise_pct is not None
                        else NA,
                    ]
                    for e in past
                ],
            ),
        ]
    else:
        lines += ["", f"- Berichtete Quartale: {NA}"]
    lines.append("")
    return lines
