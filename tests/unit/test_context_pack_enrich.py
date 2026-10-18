"""Context pack enrichment (price targets, estimate ranges, earnings reactions,
fundamentals, 52-week range and volatility, holdings, 13F label, peers).

Everything runs through the generator on the synthetic session of
``tests/unit/pack_data.py`` (no DB); the real chart renderer is replaced by a
tiny stand-in (``test_context_pack_chart.py`` draws the real one).
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import numpy as np
import pytest

from tests.unit.pack_data import (
    SESSION,
    make_bars,
    make_loader,
    make_raw,
    stub_chart,
)
from trading_signals.derived.context_pack import ContextPackGenerator
from trading_signals.derived.context_pack.fmt import NA
from trading_signals.derived.context_pack.rows import (
    BarRow,
    EarningsRow,
    Holders13F,
    InsiderTradeRow,
    RatingRow,
)
from trading_signals.derived.context_pack.sections.estimates import (
    target_action_label,
    target_cell,
    target_change_summary,
)
from trading_signals.derived.context_pack.sections.flows import (
    holding_cell,
    holding_change,
)
from trading_signals.derived.context_pack.technicals import (
    bars_frame,
    range_52w,
    realised_vol,
    support_resistance,
)
from trading_signals.utils.market_calendar import trading_days

SESSIONS = trading_days(SESSION - timedelta(days=900), SESSION)


@pytest.fixture(autouse=True)
def fast_charts(monkeypatch):
    monkeypatch.setattr(
        "trading_signals.derived.context_pack.generator.render_chart_png", stub_chart
    )


def render(tmp_path, ticker="T003", mutate=None, raw=None, n=120):
    """One ticker file; ``mutate(loader)`` / ``raw(TickerRaw)`` tweak the data."""
    loader = make_loader(n=n)
    if mutate:
        mutate(loader)
    if raw:
        original = loader.raw_sources

        def patched(d, tickers):
            out = original(d, tickers)
            raw(out.by_ticker[ticker])
            return out

        loader.raw_sources = patched
    gen = ContextPackGenerator(MagicMock(), output_dir=str(tmp_path), loader=loader)
    gen.generate_daily(SESSION, requested=[ticker])
    return (tmp_path / SESSION.isoformat() / f"{ticker}.md").read_text(
        encoding="utf-8-sig"
    )


def section(text, number):
    """The text of ``## number.`` up to the next section."""
    head = f"## {number}. "
    rest = text.split(head, 1)[1]
    return rest.split("\n## ", 1)[0]


def bars_up_to_session(ticker="T003"):
    return [b for b in make_bars(ticker, SESSION) if b.trade_date <= SESSION]


# ── analyst price targets ─────────────────────────────────────────────


def test_rating_table_shows_old_new_target_change_and_action(tmp_path):
    text = render(tmp_path)
    block = section(text, 5)
    assert "| Kursziel (alt → neu, Änderung) |" in block
    assert (
        "| Big Bank | Upgrade | Hold → Buy | 90 → 110 (+22.2 %, angehoben) |" in block
    )
    assert (
        "| Other Bank | bestätigt | Buy → Buy | 120 → 120 (+0.0 %, bestätigt) |"
        in block
    )
    # a firm without a target: a dash, not 0
    assert re.search(r"\| No Target Bank \| bestätigt \| Hold → Hold \| – \|", block)
    assert "Kursziel-Änderungen der Häuser 30 T: 1 angehoben · 1 bestätigt" in block
    assert "LEAK" not in block  # the rating dated after the session stays hidden


def test_price_target_action_is_yahoos_text_else_derived_from_the_numbers():
    def row(old, new, action=None):
        return RatingRow("A", SESSION, "F", "main", None, None, old, new, action)

    assert target_action_label(row(100, 120, "Raises")) == "angehoben"
    assert target_action_label(row(100, 90, "Lowers")) == "gesenkt"
    assert target_action_label(row(100, 100, "Maintains")) == "bestätigt"
    assert target_action_label(row(None, 100, "Announces")) == "Erstkursziel"
    assert target_action_label(row(100, 120, "Wiggles")) == "Wiggles"  # unknown: shown
    # no action text: derived, but only if both numbers are there
    assert target_action_label(row(100, 120)) == "angehoben"
    assert target_action_label(row(100, 80)) == "gesenkt"
    assert target_action_label(row(100, 100)) == "bestätigt"
    assert target_action_label(row(None, 100)) is None
    assert target_action_label(row(None, None)) is None


def test_target_cell_formats_missing_ends_and_cents():
    def cell(old, new, action=None):
        return target_cell(
            RatingRow("A", SESSION, "F", "init", None, "Buy", old, new, action)
        )

    assert cell(None, None) == "–"
    assert cell(None, 140.0, "Announces") == "– → 140 (Erstkursziel)"
    assert cell(12.5, 14.25, "Raises") == "12.50 → 14.25 (+14.0 %, angehoben)"
    assert cell(250.0, None) == "250 → –"  # a target that was dropped


def test_target_change_summary_counts_only_the_window():
    def row(days_ago, old, new, action=None):
        return RatingRow(
            "A",
            SESSION - timedelta(days=days_ago),
            "F",
            "main",
            None,
            None,
            old,
            new,
            action,
        )

    rows = [
        row(2, 10, 12),
        row(5, 10, 8),
        row(7, 10, 10),
        row(8, 10, 11),
        row(40, 10, 15),  # outside the 30 days
        row(3, None, None),  # no target
    ]
    since = SESSION - timedelta(days=30)
    assert target_change_summary(rows, since) == "2 angehoben · 1 gesenkt · 1 bestätigt"
    assert target_change_summary([row(3, None, None)], since) is None
    assert target_change_summary([], since) is None


# ── estimates ─────────────────────────────────────────────────────────


def test_estimate_tables_show_ranges_year_ago_growth_and_revenue_detail(tmp_path):
    block = section(render(tmp_path), 5)
    assert (
        "| lfd. Quartal | 1.50 | 1.30 – 1.70 | 1.30 | +15.0 % | +7.1 % | 20 "
        "| 3↑/0↓ | 6↑/1↓ |" in block
    )
    assert "| Periode | Umsatz Ø | Spanne | Analysten | Vorjahr | Wachstum |" in block
    assert (
        "| nächstes Jahr | 25.00 Mrd. | 23.00 Mrd. – 27.00 Mrd. | 18 | 22.00 Mrd. "
        "| +12.0 % |" in block
    )
    assert "Umsatz-Konsens je Periode (USD)" in block
    assert "99.00" not in block  # the estimate snapshot dated after the session


def test_missing_estimate_details_are_no_coverage_never_zero(tmp_path):
    def blank(t):
        t.estimates = [
            replace(
                e,
                eps_low=None,
                eps_high=None,
                eps_year_ago=None,
                eps_growth=None,
                revenue_low=None,
                revenue_high=None,
                revenue_n_analysts=None,
                revenue_year_ago=None,
                revenue_growth=None,
            )
            for e in t.estimates
        ]

    block = section(render(tmp_path, raw=blank), 5)
    assert f"| lfd. Quartal | 1.50 | {NA} | {NA} | {NA} | +7.1 % | 20 |" in block
    assert f"| lfd. Quartal | 25.00 Mrd. | {NA} | {NA} | {NA} | {NA} |" in block
    assert "0.00" not in block.split("**Analysten**")[0].split("| Periode")[1]

    def half(t):
        t.estimates = [
            replace(e, eps_low=1.1, eps_high=None, revenue_low=None, revenue_high=3e10)
            for e in t.estimates
        ]

    partial = section(render(tmp_path / "x", raw=half), 5)
    assert "| 1.10 – – |" in partial and "| – – 30.00 Mrd. |" in partial


# ── earnings ──────────────────────────────────────────────────────────


def close_map(ticker="T003"):
    return {b.trade_date: b.close for b in bars_up_to_session(ticker)}


def expected_reaction(report_day, timing, closes):
    i = SESSIONS.index(report_day)
    prev, s0 = (i - 1, i) if timing in ("BMO", "DMH") else (i, i + 1)
    day0 = closes[SESSIONS[s0]] / closes[SESSIONS[prev]] - 1
    day1 = closes[SESSIONS[s0 + 1]] / closes[SESSIONS[s0]] - 1
    return day0, day1


def test_earnings_table_has_eight_quarters_with_the_price_reaction(tmp_path):
    text = render(tmp_path)
    block = section(text, 6)
    rows = re.findall(r"^\| (20\d\d-\d\d-\d\d) \| (\S+) \|(.*)\|$", block, re.M)
    assert len(rows) == 8
    closes = close_map()
    by_date = {r[0]: r for r in rows}
    for report_day, timing in ((date(2026, 8, 17), "AMC"), (date(2026, 5, 19), "BMO")):
        day0, day1 = expected_reaction(report_day, timing, closes)
        cells = [c.strip() for c in by_date[report_day.isoformat()][2].split("|")]
        assert cells[-2:] == [f"{day0 * 100:+.1f} %", f"{day1 * 100:+.1f} %"]
    # the quarter without a report time has no attributable reaction day
    unknown = [r for r in rows if r[1] == "–"]
    assert len(unknown) == 1 and unknown[0][2].count("Zeit unbekannt") == 2
    assert "Tag 0** = erste Sitzung nach der Meldung" in block


def test_expected_move_proxy_is_the_mean_absolute_day_zero_reaction(tmp_path):
    block = section(render(tmp_path), 6)
    closes = close_map()
    reported = [
        e
        for e in make_raw("T003").get("T003").earnings
        if e.eps_actual is not None and e.earnings_date < SESSION
    ]
    moves = [
        abs(expected_reaction(e.earnings_date, e.time_of_day, closes)[0])
        for e in reported
        if e.time_of_day in ("BMO", "AMC", "DMH")
    ]
    assert len(reported) == 8 and len(moves) == 7  # one report has no time
    mean = sum(moves) / len(moves)
    assert (
        f"Näherung):** {mean * 100:.1f} % (Ø |Tag 0| der letzten 7 Meldungen, "
        "kein Optionsmarkt-Wert)" in block
    )


def test_without_reactions_the_proxy_says_so(tmp_path):
    def no_bars(t):
        t.bars = []

    block = section(render(tmp_path, raw=no_bars), 6)
    assert f"Typische Kursbewegung bei Zahlen (Näherung):** {NA}" in block
    rows = re.findall(r"^\| 20\d\d-\d\d-\d\d \|", block, re.M)
    assert len(rows) == 8
    assert "Zeit unbekannt" in block  # still explained for the unknown timing


def test_a_reaction_after_the_pack_day_is_open_not_guessed_or_leaked(tmp_path):
    prev = SESSION - timedelta(days=1)  # Thursday: report after the close

    def late_report(t):
        t.earnings = [
            *t.earnings,
            EarningsRow("T003", prev, "AMC", 2.0, 2.2, 10.0),
        ]
        # a bar AFTER the session with a wild close must not become "day +1"
        t.bars = [
            *t.bars,
            BarRow(SESSION + timedelta(days=3), 1.0, 9e5, 1.0, 9e5, 1.0),
        ]

    block = section(render(tmp_path, raw=late_report), 6)
    closes = close_map()
    day0 = closes[SESSION] / closes[prev] - 1
    row = [ln for ln in block.splitlines() if ln.startswith(f"| {prev.isoformat()} ")]
    assert len(row) == 1
    assert row[0].endswith(f"| {day0 * 100:+.1f} % | offen |")
    assert "900000" not in block and "9e+05" not in block


# ── fundamentals ──────────────────────────────────────────────────────


def test_fundamentals_show_revenue_eps_and_current_ratio(tmp_path):
    def known(loader):
        loader.fund.loc["T003", ["revenue_ttm", "eps_ttm", "current_ratio"]] = [
            4.2e9,
            3.14,
            1.85,
        ]

    block = section(render(tmp_path, mutate=known), 4)
    assert "Umsatz TTM: 4.20 Mrd. USD · EPS TTM: 3.14 · Current Ratio: 1.8" in block


def test_missing_fundamentals_are_no_coverage_never_zero(tmp_path):
    def gone(loader):
        loader.fund.loc["T003", ["revenue_ttm", "eps_ttm", "current_ratio"]] = np.nan

    block = section(render(tmp_path, mutate=gone), 4)
    assert f"Umsatz TTM: {NA} · EPS TTM: {NA} · Current Ratio: {NA}" in block
    assert "Umsatz TTM: 0" not in block and "EPS TTM: 0" not in block


# ── trend: 52-week range, levels, chart reference ─────────────────────


def test_trend_shows_the_52_week_range_and_the_position_in_it(tmp_path):
    block = section(render(tmp_path), 2)
    frame = bars_frame(bars_up_to_session())
    rng = range_52w(frame, SESSION)
    assert rng is not None
    assert (
        f"52-Wochen-Spanne: Tief {rng.low:.2f} ({rng.low_date.isoformat()}) – "
        f"Hoch {rng.high:.2f} ({rng.high_date.isoformat()})" in block
    )
    assert f"Abstand zum Tief {rng.dist_to_low * 100:+.1f} %" in block
    assert f"Position {rng.position * 100:.0f} % (0 % = Tief, 100 % = Hoch)" in block


def test_trend_prints_the_support_and_resistance_levels(tmp_path):
    block = section(render(tmp_path), 2)
    levels = support_resistance(bars_frame(bars_up_to_session()), SESSION)
    assert levels  # the synthetic random walk has swing levels
    sup = [lv for lv in levels if lv.kind == "support"]
    res = [lv for lv in levels if lv.kind == "resistance"]
    for lv in levels:
        assert (
            f"{lv.price:.2f} ({lv.distance * 100:+.1f} %, {lv.touches}×, "
            f"zuletzt {lv.last_touch.isoformat()})" in block
        )
    unsupported = "keine bestätigte Ebene unter dem Kurs"
    assert (unsupported in block) == (not sup)
    assert ("keine bestätigte Ebene über dem Kurs" in block) == (not res)
    assert "Swing-Punkte im selben Preisband" in block


def test_the_chart_is_referenced_when_it_exists_and_not_otherwise(
    tmp_path, monkeypatch
):
    block = section(render(tmp_path), 2)
    assert "![Chart T003](T003.png)" in block and "`T003.png`" in block
    monkeypatch.setattr(
        "trading_signals.derived.context_pack.generator.render_chart_png",
        lambda *a: None,
    )
    assert "T003.png" not in render(tmp_path / "x")


def test_a_future_bar_changes_nothing_in_the_price_facts(tmp_path):
    def spike(t):
        t.bars = [
            *t.bars,
            BarRow(SESSION + timedelta(days=3), 1.0, 1e6, 0.5, 5e5, 1.0),
        ]

    plain = render(tmp_path / "a")
    leaked = render(tmp_path / "b", raw=spike)
    assert leaked == plain
    assert "1000000" not in leaked


def test_a_stale_last_bar_gives_no_52_week_range(tmp_path):
    def stale(t):
        t.bars = [b for b in t.bars if b.trade_date < SESSION]

    block = section(render(tmp_path, raw=stale), 2)
    assert f"52-Wochen-Spanne: {NA}" in block
    assert "Unterstützung" in block  # levels still use the older bars


# ── positioning: realised volatility next to the implied one ──────────


def test_realised_vol_is_shown_next_to_the_iv_with_the_premium(tmp_path):
    def iv(loader):
        loader.snap.loc[loader.snap["ticker"] == "T003", "options_iv_atm_30d"] = 0.35

    block = section(render(tmp_path, mutate=iv), 8)
    frame = bars_frame(bars_up_to_session())
    rv20 = realised_vol(frame, SESSIONS, 20, SESSION)
    rv60 = realised_vol(frame, SESSIONS, 60, SESSION)
    assert rv20 is not None and rv60 is not None
    assert (
        f"20 T {rv20 * 100:.1f} % (IV-Prämie {(0.35 - rv20) * 100:+.1f} Vol.-Pkt)"
        in block
    )
    assert (
        f"60 T {rv60 * 100:.1f} % (IV-Prämie {(0.35 - rv60) * 100:+.1f} Vol.-Pkt)"
        in block
    )
    assert "IV-Prämie = ATM-IV 30 T minus realisiert" in block


def test_no_iv_or_no_prices_means_no_premium(tmp_path):
    def no_iv(loader):
        loader.snap.loc[loader.snap["ticker"] == "T003", "options_iv_atm_30d"] = np.nan

    block = section(render(tmp_path, mutate=no_iv), 8)
    line = block.split("Realisierte Volatilität")[1].split("\n")[0]
    assert line.count(f"(IV-Prämie {NA})") == 2 and "Vol.-Pkt)" not in line
    assert re.search(r"20 T \d+\.\d % \(IV-Prämie", line)  # the realised part stays

    def no_bars(t):
        t.bars = []

    gone = section(render(tmp_path / "x", raw=no_bars), 8)
    assert f"20 T {NA} (IV-Prämie {NA}) · 60 T {NA} (IV-Prämie {NA})" in gone


# ── smart money ───────────────────────────────────────────────────────


def trade(kind, shares, after, **kw):
    return InsiderTradeRow(
        "A", "n", None, kind, SESSION, SESSION, None, shares, 1.0, 1.0, after, **kw
    )


def test_holding_change_is_relative_to_the_holding_before_the_trade():
    assert holding_change(trade("P", 1000, 11000)) == pytest.approx(0.10)
    assert holding_change(trade("S", 5000, 45000)) == pytest.approx(-0.10)
    assert holding_change(trade("S", 5000, 0)) == pytest.approx(-1.0)  # sold it all
    assert holding_change(trade("P", 1000, 1000)) == "new"  # a first purchase
    assert holding_change(trade("P", 1000, None)) is None  # no holding reported
    assert holding_change(trade("P", None, 100)) is None
    assert holding_change(trade("P", 0, 100)) is None
    assert holding_change(trade("S", 5000, 1000)) == pytest.approx(-5000 / 6000)
    assert holding_change(trade("P", 5000, 1000)) is None  # before would be negative
    assert holding_change(trade("P", 100, -5)) is None


def test_holding_cell_never_invents_a_holding():
    assert holding_cell(trade("P", 1000, 11000)) == "11.0 Tsd. (+10.0 %)"
    assert holding_cell(trade("P", 1000, 1000)) == "1.0 Tsd. (neue Position)"
    assert holding_cell(trade("P", 5000, 1000)) == "1.0 Tsd."  # change not derivable
    assert holding_cell(trade("P", 1000, None)) == "–"


def test_insider_table_shows_holding_after_and_its_change(tmp_path):
    insider = section(render(tmp_path), 7).split("### Institutionelle")[0]
    assert "| Bestand danach (Änderung in % des Bestands davor) |" in insider
    assert (
        "| Jane Doe (CEO) | Kauf | 1.0 Tsd. | 10.00 | 10.0 Tsd. USD "
        "| 11.0 Tsd. (+10.0 %) |" in insider
    )
    assert (
        "| John Roe (CFO) | Verkauf | 5.0 Tsd. | 11.00 | 55.0 Tsd. USD "
        "| 45.0 Tsd. (-10.0 %) |" in insider
    )
    # the trade accepted at 15:55 ET has no reported holding: a dash
    assert re.search(r"\| Early Bird \(Director\) \| Kauf \|.*\| – \|", insider)
    assert "LEAK" not in insider


def test_13f_names_the_tracked_funds_and_the_total_they_hold(tmp_path):
    block = section(render(tmp_path), 7).split("### ARK")[0]
    assert (
        "**15 von 19 verfolgten Fonds** halten die Aktie (Vorperiode 14 von 18); "
        "Gesamtbestand 500.00 Mio. Aktien (Vorperiode 490.00 Mio., +2.0 %)." in block
    )
    assert "nicht der gesamte institutionelle Besitz" in block

    def unknown_n(t):
        t.holders = replace(t.holders, n_tracked=None, prev_n_tracked=None)

    plain = section(render(tmp_path / "x", raw=unknown_n), 7).split("### ARK")[0]
    assert "**15 verfolgten Fonds** halten die Aktie (Vorperiode 14);" in plain

    def first_period(t):
        t.holders = Holders13F(
            t.holders.report_period, t.holders.max_filing_date, 15, 5e8, n_tracked=19
        )

    first = section(render(tmp_path / "y", raw=first_period), 7).split("### ARK")[0]
    assert (
        "**15 von 19 verfolgten Fonds** halten die Aktie; "
        "Gesamtbestand 500.00 Mio. Aktien." in first
    )

    def no_total(t):
        t.holders = Holders13F(
            t.holders.report_period, t.holders.max_filing_date, 15, None, n_tracked=19
        )

    nothing = section(render(tmp_path / "z", raw=no_total), 7).split("### ARK")[0]
    assert f"Gesamtbestand {NA}" in nothing


# ── peers ─────────────────────────────────────────────────────────────


def peer_rows(text):
    block = section(text, 14)
    return [
        line.split("|")[1].strip()
        for line in block.splitlines()
        if line.startswith("| ")
    ][1:]  # without the header


def test_peers_are_gate_tickers_of_the_same_industry_closest_in_market_cap(tmp_path):
    loader = make_loader(n=650)
    gen = ContextPackGenerator(MagicMock(), output_dir=str(tmp_path), loader=loader)
    gen.generate_daily(SESSION, requested=["T003"])
    day = tmp_path / SESSION.isoformat()
    text = (day / "T003.md").read_text(encoding="utf-8-sig")
    rows = peer_rows(text)
    assert rows[0] == "**T003** (dieser Ticker)" and len(rows) == 5

    gate = {
        line.split(",")[0]
        for line in (day / "screen.csv")
        .read_text(encoding="utf-8-sig")
        .splitlines()[1:]
    }
    industry = loader.info["T003"].industry
    caps = loader.fund["market_cap"]
    pool = sorted(
        (t for t in gate if t != "T003" and loader.info[t].industry == industry),
        key=lambda t: (abs(np.log(caps[t] / caps["T003"])), t),
    )
    assert rows[1:] == pool[:4]
    assert "4 aus der Branche" in section(text, 14)
    assert f"weitere Gate-Ticker der Branche: {len(pool)}" in section(text, 14)
    assert "T003" not in rows[1:]


def test_peer_section_is_deterministic(tmp_path):
    first = render(tmp_path / "a", n=650)
    second = render(tmp_path / "b", n=650)
    assert section(first, 14) == section(second, 14)


def test_a_one_company_industry_is_topped_up_from_the_sector(tmp_path):
    def alone(loader):
        loader.info["T003"].industry = "Only One"

    block = section(render(tmp_path, mutate=alone, n=650), 14)
    assert "aus der Branche" not in block
    assert "4 aus dem Sektor" in block and "zu wenige Branchen-Peers" in block


def test_a_ticker_without_any_gate_peer_says_so(tmp_path):
    def alone(loader):
        loader.info["T003"].industry = "Only One"
        loader.info["T003"].sector = "Only Sector"

    block = section(render(tmp_path, mutate=alone), 14)
    assert "Keine Peers: in „Only One“ gibt es keine weiteren Ticker" in block
    assert "| Ticker |" not in block


def test_peer_values_come_from_the_same_session_data(tmp_path):
    loader = make_loader(n=650)
    gen = ContextPackGenerator(MagicMock(), output_dir=str(tmp_path), loader=loader)
    gen.generate_daily(SESSION, requested=["T003"])
    text = (tmp_path / SESSION.isoformat() / "T003.md").read_text(encoding="utf-8-sig")
    block = section(text, 14)
    own = [ln for ln in block.splitlines() if ln.startswith("| **T003**")][0]
    snap = loader.snap.set_index("ticker")
    forward = snap.loc["T003", "forward_pe"]
    revision = snap.loc["T003", "eps_revision_pct_30d"]
    assert f"{forward:.1f}" in own and f"{revision * 100:+.1f} %" in own
    assert re.search(r"\| [+-]\d+\.\d % \|", own)  # the 60-session return


def test_missing_peer_values_are_no_coverage(tmp_path):
    def blank(loader):
        peers = loader.snap["ticker"] != "T003"
        loader.snap.loc[peers, "forward_pe"] = np.nan

    block = section(render(tmp_path, mutate=blank, n=650), 14)
    assert block.count(NA) >= 4  # every peer's forward P/E


def test_the_overview_mentions_the_charts_only_when_they_exist(tmp_path, monkeypatch):
    def overview(folder):
        loader = make_loader(n=120)
        gen = ContextPackGenerator(MagicMock(), output_dir=str(folder), loader=loader)
        gen.generate_daily(SESSION, requested=["T003"])
        return (folder / SESSION.isoformat() / "00_uebersicht.md").read_text(
            encoding="utf-8-sig"
        )

    with_charts = overview(tmp_path / "a")
    assert "je ein Chart `TICKER.png` im selben Ordner" in with_charts
    monkeypatch.setattr(
        "trading_signals.derived.context_pack.generator.render_chart_png",
        lambda *a: None,
    )
    assert "TICKER.png" not in overview(tmp_path / "b")


# ── fixtures ──────────────────────────────────────────────────────────


def test_the_fixture_has_a_leaky_bar_and_old_enough_history_for_8_quarters():
    raw = make_raw("T003").get("T003")
    assert any(b.trade_date > SESSION for b in raw.bars)  # point-in-time must cut it
    assert min(b.trade_date for b in raw.bars) <= SESSION - timedelta(days=800)
    assert len([e for e in raw.earnings if e.eps_actual is not None]) >= 8
    assert isinstance(raw.holders, Holders13F) and raw.holders.n_tracked == 19
    assert datetime(2026, 1, 1) < datetime.combine(SESSION, datetime.min.time())
