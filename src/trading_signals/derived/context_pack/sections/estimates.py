"""Sections 5 and 6 - estimates / revisions / analysts, and earnings."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

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
from trading_signals.derived.context_pack.rows import EstimateRow, RatingRow
from trading_signals.derived.context_pack.technicals import (
    REASON_OPEN,
    REASON_TIMING,
    EarningsReaction,
    earnings_reactions,
    mean_abs_move,
)

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

#: Price-target action of a rating row (Yahoo ``priceTargetAction``, lower case).
TARGET_ACTION_LABELS = {
    "raises": "angehoben",
    "lowers": "gesenkt",
    "maintains": "bestätigt",
    "reiterates": "bestätigt",
    "announces": "Erstkursziel",
    "initiates": "Erstkursziel",
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


def _span(low: Any, high: Any, fmt: Callable[[Any], str]) -> str:
    """``low – high``; a missing end is a dash, both missing is no coverage."""
    lo, hi = to_float(low), to_float(high)
    if lo is None and hi is None:
        return NA
    return (
        f"{fmt(lo) if lo is not None else DASH} – "
        f"{fmt(hi) if hi is not None else DASH}"
    )


def _usd(value: Any) -> str:
    """An amount in the abbreviated unit, without the currency (header: USD)."""
    return money(value, unit="")


def _target_price(value: float | None) -> str:
    """A price target (whole USD if it is one, else with cents)."""
    v = to_float(value)
    if v is None:
        return DASH
    return f"{v:.0f}" if abs(v - round(v)) < 0.005 else f"{v:.2f}"


def target_action_label(r: RatingRow) -> str | None:
    """Raise / lower / maintain of the price target (Yahoo's text, else derived)."""
    if r.target_action:
        return TARGET_ACTION_LABELS.get(
            r.target_action.lower(), clean_text(r.target_action, 14)
        )
    old, new = to_float(r.target_old), to_float(r.target_new)
    if old is None or new is None:
        return None
    if new > old:
        return TARGET_ACTION_LABELS["raises"]
    if new < old:
        return TARGET_ACTION_LABELS["lowers"]
    return TARGET_ACTION_LABELS["maintains"]


def target_cell(r: RatingRow) -> str:
    """``old → new (change %, action)``; a dash if the row has no price target."""
    old, new = to_float(r.target_old), to_float(r.target_new)
    if old is None and new is None:
        return DASH
    text = f"{_target_price(old)} → {_target_price(new)}"
    extra = []
    if old is not None and new is not None:
        extra.append(signed_percent(new / old - 1.0))
    action = target_action_label(r)
    if action:
        extra.append(action)
    return text + (f" ({', '.join(extra)})" if extra else "")


def target_change_summary(ratings: list[RatingRow], since: date) -> str | None:
    """Raised / lowered / maintained counts of the price targets since ``since``."""
    counts: dict[str, int] = {}
    for r in ratings:
        if r.rating_date < since or (r.target_old is None and r.target_new is None):
            continue  # outside the window, or no price target at all
        label = target_action_label(r)
        if label is not None:
            counts[label] = counts.get(label, 0) + 1
    if not counts:
        return None
    order = ("angehoben", "gesenkt", "bestätigt")
    parts = [f"{counts[k]} {k}" for k in order if k in counts]
    parts += [f"{v} {k}" for k, v in sorted(counts.items()) if k not in order]
    return " · ".join(parts)


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
                    "EPS Ø",
                    "Spanne (tief – hoch)",
                    "Vorjahr",
                    "Wachstum",
                    "Δ 30 T",
                    "Analysten",
                    "Revis. 7 T",
                    "Revis. 30 T",
                ],
                [
                    [
                        PERIOD_LABELS.get(e.period, e.period),
                        num(e.eps_avg),
                        _span(e.eps_low, e.eps_high, num),
                        num(e.eps_year_ago),
                        signed_percent(e.eps_growth),
                        signed_percent(_revision_pct(e)),
                        integer(e.eps_n_analysts),
                        _updown(e.rev_up_7d, e.rev_down_7d),
                        _updown(e.rev_up_30d, e.rev_down_30d),
                    ]
                    for e in estimates
                ],
            ),
            "",
            "Umsatz-Konsens je Periode (USD):",
            "",
            *md_table(
                [
                    "Periode",
                    "Umsatz Ø",
                    "Spanne",
                    "Analysten",
                    "Vorjahr",
                    "Wachstum",
                ],
                [
                    [
                        PERIOD_LABELS.get(e.period, e.period),
                        _usd(e.revenue_avg),
                        _span(e.revenue_low, e.revenue_high, _usd),
                        integer(e.revenue_n_analysts),
                        _usd(e.revenue_year_ago),
                        signed_percent(e.revenue_growth),
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
    summary = target_change_summary(ctx.raw.ratings, ctx.session - timedelta(days=30))
    if summary:
        lines.append(
            metric_group([["Kursziel-Änderungen der Häuser 30 T", summary, DASH]])
        )
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
                    "Kursziel (alt → neu, Änderung)",
                ],
                [
                    [
                        day(r.rating_date),
                        clean_text(r.firm, 28),
                        ACTION_LABELS.get(r.action or "", clean_text(r.action, 12)),
                        f"{clean_text(r.rating_old, 16)} → "
                        f"{clean_text(r.rating_new, 16)}",
                        target_cell(r),
                    ]
                    for r in ratings
                ],
            ),
        ]
    else:
        lines += ["", f"- Rating-Aktionen der letzten {RATING_WINDOW_DAYS} Tage: keine"]
    lines += ["", yahoo_consensus_line(ctx), ""]
    return lines


def yahoo_consensus_line(ctx: TickerContext) -> str:
    """Yahoo's analyst consensus as raw values: mean, label and number of analysts.

    ``recommendation_mean`` runs from 1 (strong buy) to 5 (sell); the label is
    Yahoo's own text (``buy``, ``hold`` ...) and shown as it is. Each missing
    part is no coverage, never a number.
    """
    key = ctx.fund.get("recommendation_key")
    key_text = clean_text(key, 20) if isinstance(key, str) and key.strip() else NA
    return metric_group(
        [
            [
                "Yahoo-Analystenkonsens, Empfehlung Ø (1 = Strong Buy … 5 = Sell)",
                num(ctx.fval("recommendation_mean")),
                DASH,
            ],
            ["Schlüssel", key_text, DASH],
            ["Analysten", integer(ctx.fval("number_of_analyst_opinions")), DASH],
        ]
    )


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
        reactions = {
            r.earnings_date: r
            for r in earnings_reactions(past, ctx.raw.bars, ctx.sessions, d)
        }
        lines += [
            "",
            f"Die letzten {len(past)} berichteten Quartale; Reaktion Schluss zu "
            "Schluss: **Tag 0** = erste Sitzung nach der Meldung (BMO / während "
            "der Börse: Meldetag, AMC: Folgetag), **Tag +1** = die Sitzung danach.",
            "",
            *md_table(
                [
                    "Datum",
                    "Zeit",
                    "EPS Schätzung",
                    "EPS tatsächlich",
                    "Überraschung",
                    "Kurs Tag 0",
                    "Kurs Tag +1",
                ],
                [
                    [
                        day(e.earnings_date),
                        e.time_of_day or DASH,
                        num(e.eps_estimate),
                        num(e.eps_actual),
                        f"{e.surprise_pct:+.1f} %"
                        if e.surprise_pct is not None
                        else NA,
                        *_reaction_cells(reactions.get(e.earnings_date)),
                    ]
                    for e in past
                ],
            ),
        ]
        mean, n = mean_abs_move(list(reactions.values()))
        if mean is None:
            move = f"{NA} (keine Reaktion mit bekannter Meldezeit und Kursen)"
        else:
            move = (
                f"{mean * 100:.1f} % (Ø |Tag 0| der letzten {n} Meldungen, "
                "kein Optionsmarkt-Wert)"
            )
        lines += ["", f"- **Typische Kursbewegung bei Zahlen (Näherung):** {move}"]
    else:
        lines += ["", f"- Berichtete Quartale: {NA}"]
    lines.append("")
    return lines


def _reaction_cells(reaction: EarningsReaction | None) -> list[str]:
    """Tag 0 / Tag +1 cells; a missing number says why."""
    if reaction is None:
        return [NA, NA]
    cells = []
    for value, reason in (
        (reaction.day0, reaction.reason0),
        (reaction.day1, reaction.reason1),
    ):
        if value is not None:
            cells.append(signed_percent(value))
        elif reason == REASON_TIMING:
            cells.append("Zeit unbekannt")
        elif reason == REASON_OPEN:
            cells.append("offen")
        else:
            cells.append(NA)
    return cells
