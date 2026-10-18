"""Sections 7 and 8 - smart money (buys AND sells) and positioning."""

from __future__ import annotations

from datetime import date, timedelta

from trading_signals.derived.context_pack.constants import (
    HOLDER_ROWS,
    INSIDER_ROWS,
    INSIDER_WINDOW_DAYS,
    IV_RANK_MIN_OBS,
    POLITICIAN_ROWS,
    POLITICIAN_WINDOW_DAYS,
    SHORT_INTEREST_ROWS,
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
    percent,
    shares,
    signed,
    signed_int,
    signed_percent,
    to_float,
)
from trading_signals.derived.context_pack.model import TickerContext, metric_group
from trading_signals.derived.context_pack.rows import Holders13F, InsiderTradeRow
from trading_signals.derived.feature_pipeline import (
    FORM13F_MAX_AGE_DAYS,
    ark_state_at,
)

INSIDER_TYPES = {"P": "Kauf", "S": "Verkauf"}
POLITICIAN_TYPES = {"purchase": "Kauf", "sale": "Verkauf"}


# ── Insider ──────────────────────────────────────────────────────────


def placed_date(t: InsiderTradeRow) -> date | None:
    """The day a Form 4 trade is placed on: filing date, else acceptance day."""
    if t.filing_date is not None:
        return t.filing_date
    if t.acceptance is not None:
        return t.acceptance.date()
    return None


def holding_change(t: InsiderTradeRow) -> float | str | None:
    """Change of the insider's holding through this trade.

    ``fraction`` of the holding before the trade (``+0.042`` = +4.2 %),
    ``"new"`` if the holding before was zero (a first purchase), ``None`` if it
    cannot be derived (no post-trade holding or no share count, or an
    inconsistent holding before).
    """
    after, traded = to_float(t.shares_owned_after), to_float(t.shares)
    if after is None or traded is None or traded <= 0 or after < 0:
        return None
    signed = traded if t.transaction_type == "P" else -traded
    before = after - signed
    if before < 0:
        return None
    if before == 0:
        return "new"
    return signed / before


def holding_cell(t: InsiderTradeRow) -> str:
    """``holding after (change)``; a dash if the filing gave no holding."""
    after = to_float(t.shares_owned_after)
    if after is None:
        return DASH
    change = holding_change(t)
    if change is None:
        return shares(after)
    if change == "new":
        return f"{shares(after)} (neue Position)"
    return f"{shares(after)} ({signed_percent(change)})"


def insider_window_summary(
    trades: list[InsiderTradeRow], d: date, days: int
) -> tuple[int, float, int, float]:
    """``(buys, buy value, sells, sell value)`` filed in the last ``days`` days.

    The window is on the filing date, as in the feature pipeline
    (``insider_trade_features``); trades without a filing date are not counted.
    """
    since = d - timedelta(days=days)

    def inside(t: InsiderTradeRow, kind: str) -> bool:
        return (
            t.transaction_type == kind
            and t.filing_date is not None
            and since <= t.filing_date <= d
        )

    buys = [t for t in trades if inside(t, "P")]
    sells = [t for t in trades if inside(t, "S")]
    return (
        len(buys),
        sum(t.value or 0.0 for t in buys),
        len(sells),
        sum(t.value or 0.0 for t in sells),
    )


def render_insider(ctx: TickerContext) -> list[str]:
    lines = ["### Insider (Form 4, offene Marktgeschäfte: Käufe und Verkäufe)", ""]
    if ctx.val("insider_net_buy_count_30d") is None and not ctx.raw.insider:
        return [*lines, f"- Insider-Meldungen: {NA}", ""]
    active = ctx.row.get("insider_cluster_active")
    lines += [
        metric_group(
            [
                ctx.metric(
                    "Netto-Käufe 30 T (Käufe minus Verkäufe)",
                    "insider_net_buy_count_30d",
                    signed_int,
                ),
                ctx.metric(
                    "Kaufanteil am Volumen 30 T", "insider_buy_ratio_30d", percent
                ),
                ctx.metric("90 T", "insider_buy_ratio_90d", percent),
            ]
        ),
        metric_group(
            [
                ctx.metric("Kaufvolumen 30 T", "insider_buy_value_30d", money),
                ctx.metric("Cluster-Score 60 T", "cluster_score_sum_60d", num),
                [
                    "Kauf-Cluster aktiv",
                    NA if active is None else ("ja" if active else "nein"),
                    DASH,
                ],
            ]
        ),
    ]
    d = ctx.session
    s30 = insider_window_summary(ctx.raw.insider, d, 30)
    s90 = insider_window_summary(ctx.raw.insider, d, 90)
    lines.append(
        f"- Gemeldet 30 T: {s30[0]} Käufe ({money(s30[1])}) / {s30[2]} Verkäufe "
        f"({money(s30[3])}); 90 T: {s90[0]} Käufe ({money(s90[1])}) / {s90[2]} "
        f"Verkäufe ({money(s90[3])}). Verkäufe sind oft vorab geplant (10b5-1) und "
        "sagen weniger als Käufe."
    )
    since = d - timedelta(days=INSIDER_WINDOW_DAYS)
    trades = sorted(
        (t for t in ctx.raw.insider if (placed_date(t) or date.min) >= since),
        key=lambda t: (placed_date(t) or date.min, t.transaction_date or date.min),
        reverse=True,
    )[:INSIDER_ROWS]
    if trades:
        rows = []
        for t in trades:
            who = clean_text(t.name, 28)
            if t.title:
                who += f" ({clean_text(t.title, 24)})"
            rows.append(
                [
                    day(placed_date(t), DASH),
                    who,
                    INSIDER_TYPES.get(t.transaction_type, t.transaction_type),
                    shares(t.shares),
                    num(t.price),
                    money(t.value),
                    holding_cell(t),
                ]
            )
        lines += [
            "",
            *md_table(
                [
                    "Gemeldet",
                    "Insider (Titel)",
                    "Art",
                    "Aktien",
                    "Preis",
                    "Wert",
                    "Bestand danach (Änderung in % des Bestands davor)",
                ],
                rows,
            ),
        ]
    else:
        lines += [
            "",
            "- Keine Käufe oder Verkäufe in den letzten "
            f"{INSIDER_WINDOW_DAYS} Tagen gemeldet",
        ]
    lines.append("")
    return lines


# ── 13F ──────────────────────────────────────────────────────────────


def _tracked_funds_line(h: Holders13F) -> str:
    """``x von N verfolgten Fonds`` and the total they hold (not all institutions)."""
    of = f" von {h.n_tracked}" if h.n_tracked else ""
    prev = ""
    if h.prev_period is not None and h.prev_n_filers is not None:
        prev_of = f" von {h.prev_n_tracked}" if h.prev_n_tracked else ""
        prev = f" (Vorperiode {h.prev_n_filers}{prev_of})"
    change = None
    if (
        h.shares_total is not None
        and h.prev_shares_total is not None
        and h.prev_shares_total > 0
    ):
        change = h.shares_total / h.prev_shares_total - 1.0
    total = (
        f"Gesamtbestand {shares(h.shares_total)} Aktien"
        + (
            f" (Vorperiode {shares(h.prev_shares_total)}, {signed_percent(change)})"
            if change is not None
            else ""
        )
        if h.shares_total is not None
        else f"Gesamtbestand {NA}"
    )
    return (
        f"- **{h.n_filers}{of} verfolgten Fonds** halten die Aktie{prev}; {total}. "
        "Nur verfolgte Fonds, nicht der gesamte institutionelle Besitz."
    )


def render_13f(ctx: TickerContext) -> list[str]:
    lines = ["### Institutionelle Investoren (13F)", ""]
    h = ctx.raw.holders
    if h is None or h.report_period < ctx.session - timedelta(
        days=FORM13F_MAX_AGE_DAYS
    ):
        last = f" (letzte Periode {day(h.report_period)})" if h else ""
        return [*lines, f"- Keine aktuelle 13F-Abdeckung{last}", ""]
    prev = f", Vorperiode {day(h.prev_period)}" if h.prev_period else ""
    lines += [
        f"Berichtsperiode {day(h.report_period)}{prev}, Filings bis "
        f"{day(h.max_filing_date)}; 13F kommen mit bis zu 45 Tagen Verzug, die "
        "jüngste Periode ist in der Einreichungssaison unvollständig.",
        "",
        metric_group(
            [
                ctx.metric("Halter", "form13f_top_holder_count", integer),
                ctx.metric(
                    "Änderung ggü. Vorperiode",
                    "form13f_holder_delta_qoq",
                    signed_percent,
                ),
                ctx.metric("neue Positionen", "form13f_new_positions_count", integer),
                ctx.metric("ausgestiegen", "form13f_exited_positions_count", integer),
            ]
        ),
        _tracked_funds_line(h),
    ]
    if h.top:
        rows = []
        for r in h.top[:HOLDER_ROWS]:
            if h.prev_period is None:
                change = DASH
            elif r.prev_shares is None:
                change = "neu"
            elif r.prev_shares > 0 and r.shares is not None:
                change = signed_percent(r.shares / r.prev_shares - 1)
            else:
                change = NA
            rows.append(
                [clean_text(r.filer_name, 40), shares(r.shares), money(r.value), change]
            )
        lines += [
            "",
            *md_table(
                ["Größte Halter", "Aktien", "Wert", "Änderung Aktien ggü. Vorperiode"],
                rows,
            ),
        ]
    lines.append("")
    return lines


# ── ARK ──────────────────────────────────────────────────────────────


def render_ark(ctx: TickerContext) -> list[str]:
    lines = ["### ARK-ETFs", ""]
    state = ark_state_at(
        [
            (a.snapshot_date, a.etf, a.ticker, a.weight_pct, a.shares)
            for a in ctx.raw.ark
        ],
        ctx.session,
    ).get(ctx.ticker, {})
    if not state:
        last = max((a.snapshot_date for a in ctx.raw.ark), default=None)
        note = f" (letzter Bestand {day(last)})" if last else ""
        return [*lines, f"- In keinem ARK-ETF gehalten{note}", ""]
    held = " · ".join(f"{etf} {w:.2f} %" for etf, (w, _) in sorted(state.items()))
    lines += [
        f"- Gehalten in: {held}",
        metric_group(
            [
                ctx.metric(
                    "Gewichtsänderung 5 T (%-Pkt)", "ark_weight_delta_5d", signed
                ),
                ctx.metric("20 T", "ark_weight_delta_20d", signed),
                ctx.metric("Aufstock-Tage in 20 T", "ark_increase_days_20d", integer),
            ]
        ),
        "",
    ]
    return lines


# ── Politicians ──────────────────────────────────────────────────────


def render_politicians(ctx: TickerContext) -> list[str]:
    lines = ["### Politiker (STOCK Act)", ""]
    since = ctx.session - timedelta(days=POLITICIAN_WINDOW_DAYS)
    trades = sorted(
        (
            p
            for p in ctx.raw.politicians
            if p.disclosure_date and p.disclosure_date >= since
        ),
        key=lambda p: (p.disclosure_date, p.transaction_date or date.min),
        reverse=True,
    )[:POLITICIAN_ROWS]
    if ctx.val("politician_buy_count_60d_disclosure") is None and not trades:
        return [*lines, f"- Offenlegungen: {NA} (kein Treffer in der Quelle)", ""]
    lines.append(
        metric_group(
            [
                ctx.metric(
                    "Käufe 60 T (nach Offenlegung)",
                    "politician_buy_count_60d_disclosure",
                    integer,
                ),
                ctx.metric(
                    "verschiedene Politiker 90 T",
                    "politician_distinct_90d_disclosure",
                    integer,
                ),
            ]
        )
    )
    if trades:
        rows = []
        for p in trades:
            lag = (
                (p.disclosure_date - p.transaction_date).days
                if p.transaction_date
                else None
            )
            who = clean_text(p.name, 26)
            tags = "/".join(x for x in (p.chamber, p.party, p.state) if x)
            if tags:
                who += f" ({clean_text(tags, 18)})"
            kind = POLITICIAN_TYPES.get(
                (p.transaction_type or "").split(" ")[0].lower(),
                clean_text(p.transaction_type, 14),
            )
            lag_text = (
                DASH if lag is None else f"{lag} T" + (" (>45)" if lag > 45 else "")
            )
            rows.append(
                [
                    day(p.disclosure_date),
                    who,
                    kind,
                    clean_text(p.amount_range, 22),
                    day(p.transaction_date, DASH),
                    lag_text,
                    clean_text(p.owner, 10),
                ]
            )
        lines += [
            "",
            *md_table(
                [
                    "Offenlegung",
                    "Politiker",
                    "Art",
                    "Betrag",
                    "Handel",
                    "Verzug",
                    "Eigentümer",
                ],
                rows,
            ),
        ]
    else:
        lines += [
            "",
            f"- Keine Offenlegung in den letzten {POLITICIAN_WINDOW_DAYS} Tagen",
        ]
    lines.append("")
    return lines


def render_smart_money(ctx: TickerContext) -> list[str]:
    lines = ["## 7. Smart Money (Käufe und Verkäufe)", ""]
    for part in (render_insider, render_13f, render_ark, render_politicians):
        lines += part(ctx)
    return lines


# ── Positioning ──────────────────────────────────────────────────────


def iv_rank(iv_values: list[float], current: float) -> float | None:
    """IV rank (0-100): position of ``current`` in the min-max range.

    None below :data:`IV_RANK_MIN_OBS` observations (a year of snapshots).
    """
    if len(iv_values) < IV_RANK_MIN_OBS:
        return None
    lo, hi = min(iv_values), max(iv_values)
    if hi <= lo:
        return None
    return 100.0 * (current - lo) / (hi - lo)


def vol_points(v: float) -> str:
    """A difference of two implied volatilities in volatility points."""
    return f"{v * 100:+.1f}"


def render_positioning(ctx: TickerContext) -> list[str]:
    d = ctx.session
    lines = [
        "## 8. Positionierung",
        "",
        "### Short Interest (FINRA, zweimal monatlich)",
        "",
    ]
    si = sorted(ctx.raw.short_interest, key=lambda r: r.settlement_date, reverse=True)
    newest = si[0].settlement_date if si else None
    lines += [
        f"Stichtag {age_text(newest, d)}; FINRA veröffentlicht rund 7 Handelstage "
        "danach, hier gilt ein Stichtag erst nach 9 Handelstagen als öffentlich.",
        "",
        metric_group(
            [
                ctx.metric("Days-to-Cover", "short_interest_days_to_cover", num),
                ctx.metric(
                    "Änderung Short Interest (≈ 1 Monat)",
                    "short_interest_change_1m",
                    signed_percent,
                ),
            ]
        ),
    ]
    if si:
        lines += [
            "",
            *md_table(
                ["Stichtag", "Short Interest (Aktien)", "Days-to-Cover"],
                [
                    [
                        day(r.settlement_date),
                        shares(r.short_interest),
                        num(r.days_to_cover),
                    ]
                    for r in si[:SHORT_INTEREST_ROWS]
                ],
            ),
        ]

    lines += [
        "",
        "### Short Volume (FINRA, täglich; enthält auch Market-Maker-Absicherung)",
        "",
        metric_group(
            [
                ctx.metric("Anteil Ø 5 T", "short_volume_ratio_5d", percent),
                ctx.metric("Ø 20 T", "short_volume_ratio_20d", percent),
                ctx.metric(
                    "Änderung ggü. den 20 T davor (%-Pkt)",
                    "short_volume_change_20d",
                    lambda v: f"{v * 100:+.1f}",
                ),
            ]
        ),
        "",
        "### Optionen (implizite Volatilität)",
        "",
    ]
    iv_rows = sorted(ctx.raw.iv, key=lambda r: r.snapshot_date)
    current = ctx.val("options_iv_atm_30d")
    if current is not None:
        values = [r.atm_iv_30d for r in iv_rows if r.atm_iv_30d is not None]
        values = values[-IV_RANK_MIN_OBS:]  # the trailing 252 snapshots
        rank = iv_rank(values, current)
        rank_txt = (
            f"{rank:.0f} %"
            if rank is not None
            else f"n/a (Historie {len(values)} von {IV_RANK_MIN_OBS} Tagen)"
        )
    else:
        rank_txt = NA
    lines.append(
        metric_group(
            [
                ctx.metric("ATM-IV 30 T", "options_iv_atm_30d", percent),
                ctx.metric(
                    "Skew OTM-Put minus OTM-Call (Vol.-Pkt)",
                    "options_iv_skew_25d",
                    vol_points,
                ),
                ctx.metric(
                    "Laufzeit-Steigung 60 T minus 30 T (Vol.-Pkt)",
                    "options_iv_term_slope",
                    vol_points,
                ),
                ctx.metric("Put/Call-OI", "options_iv_put_call_oi", num),
                ["IV-Rank (252 Tage)", rank_txt, DASH],
            ]
        )
    )
    lines.append(_realised_vs_implied(ctx, current))
    latest = iv_rows[-1] if iv_rows else None
    if latest is not None:
        lines.append(f"- Stand Optionen: {age_text(latest.snapshot_date, d)}")
    lines.append("")
    return lines


def _realised_vs_implied(ctx: TickerContext, iv: float | None) -> str:
    """Realised volatility (20 / 60 sessions) next to the ATM IV and the premium."""
    price = ctx.price
    rows = []
    for label, rv in (
        ("20 T", price.rv20 if price else None),
        ("60 T", price.rv60 if price else None),
    ):
        premium = None if rv is None or iv is None else iv - rv
        premium_text = NA if premium is None else f"{vol_points(premium)} Vol.-Pkt"
        rows.append(f"{label} {percent(rv)} (IV-Prämie {premium_text})")
    return (
        "- Realisierte Volatilität (annualisiert, echte Kurse): "
        + " · ".join(rows)
        + "; IV-Prämie = ATM-IV 30 T minus realisiert."
    )
