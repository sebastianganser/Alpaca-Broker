"""The chart image of a ticker file: drawing, determinism, point in time and the
rules for replacing / deleting ``TICKER.png`` files (no DB)."""

from __future__ import annotations

import hashlib
import io
import re
from datetime import date, timedelta
from unittest.mock import MagicMock

import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from tests.unit.pack_data import SESSION, make_bars, make_loader, stub_chart
from trading_signals.derived.context_pack import ContextPackGenerator
from trading_signals.derived.context_pack.chart import (
    CHART_MARKER,
    MIN_CHART_BARS,
    _month_ticks,
    chart_description,
    is_generated_chart,
    png_text_chunks,
    render_chart_png,
)
from trading_signals.derived.context_pack.generator import _is_generated_chart_file
from trading_signals.derived.context_pack.rows import BarRow
from trading_signals.derived.context_pack.technicals import analyse_prices
from trading_signals.utils.market_calendar import trading_days

SESSIONS = trading_days(SESSION - timedelta(days=900), SESSION)
MAX_CHART_BYTES = 120_000


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def draw(ticker="T003", bars=None, analysis="auto", session=SESSION):
    bars = make_bars(ticker, session, leak_after=False) if bars is None else bars
    if analysis == "auto":
        analysis = analyse_prices(bars, SESSIONS, session)
    return render_chart_png(ticker, f"Company {ticker}", session, bars, analysis)


# ── drawing ───────────────────────────────────────────────────────────


def test_the_chart_is_a_png_of_a_sane_size_with_an_ownership_marker():
    png = draw()
    assert png is not None and png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) <= MAX_CHART_BYTES
    image = Image.open(io.BytesIO(png))
    assert image.size == (1000, 640) and image.mode == "RGB"
    text = png_text_chunks(png)
    assert text == {"Description": chart_description("T003", SESSION)}
    assert text["Description"].startswith(CHART_MARKER)
    # no timestamp and no library version in the file: reproducible bytes
    assert b"Matplotlib" not in png and b"Software" not in png
    assert b"tIME" not in png


def test_the_chart_is_not_blank():
    image = Image.open(io.BytesIO(draw())).convert("L")
    pixels = np.asarray(image)
    assert (pixels < 250).mean() > 0.02  # candles, lines and text were drawn
    assert len(np.unique(pixels)) > 10


def test_same_bars_give_the_same_bytes():
    bars = make_bars("T003", SESSION, leak_after=False)
    first, second = draw(bars=bars), draw(bars=list(reversed(bars)))
    assert sha(first) == sha(second)
    other = draw("T004")
    assert sha(other) != sha(first)  # a different ticker is a different image


def test_bars_after_the_session_never_reach_the_chart():
    clean = make_bars("T003", SESSION, leak_after=False)
    leaked = [
        *clean,
        BarRow(SESSION + timedelta(days=3), 100.0, 5000.0, 1.0, 4000.0, 9e9),
    ]
    assert sha(draw(bars=leaked)) == sha(draw(bars=clean))


def test_too_few_bars_give_no_chart_and_candles_only_work_without_analysis():
    few = make_bars("T003", SESSION, days=60, leak_after=False)[: MIN_CHART_BARS - 1]
    assert draw(bars=few) is None
    assert render_chart_png("T003", None, SESSION, [], None) is None
    plain = draw(analysis=None)
    assert plain is not None and sha(plain) != sha(draw())


def test_short_history_without_sma200_still_draws():
    bars = make_bars("T003", SESSION, days=150, leak_after=False)  # about 100 bars
    assert len(bars) > MIN_CHART_BARS
    png = draw(bars=bars)
    assert png is not None and len(png) <= MAX_CHART_BYTES


def test_month_ticks_skip_a_crowded_first_month_and_label_the_year():
    dates = [d for d in SESSIONS if date(2025, 10, 27) <= d <= date(2026, 3, 6)]
    pos, labels = _month_ticks(dates)
    assert len(pos) == len(labels) and pos == sorted(pos)
    assert labels[0] == "Nov 2025"  # Oct 27-31 is too short to label
    assert "Jan 2026" in labels and "Feb" in labels


# ── ownership marker ──────────────────────────────────────────────────


def test_only_a_png_with_our_marker_and_the_right_ticker_is_ours(tmp_path):
    ours = tmp_path / "T003.png"
    ours.write_bytes(stub_chart("T003", None, SESSION, [], None))
    assert is_generated_chart(ours, "T003")
    assert not is_generated_chart(ours, "T004")  # the marker names another ticker
    assert not is_generated_chart(ours, "T00")  # a prefix is not the ticker
    plain = tmp_path / "T005.png"
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buffer, format="PNG")
    plain.write_bytes(buffer.getvalue())
    assert not is_generated_chart(plain, "T005")
    foreign = tmp_path / "T006.png"
    info = PngInfo()
    info.add_text("Description", "my own chart ticker=T006 ")
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buffer, format="PNG", pnginfo=info)
    foreign.write_bytes(buffer.getvalue())
    assert not is_generated_chart(foreign, "T006")
    junk = tmp_path / "T007.png"
    junk.write_bytes(b"not a png at all")
    empty = tmp_path / "T008.png"
    empty.write_bytes(b"")
    for path, ticker in ((junk, "T007"), (empty, "T008"), (tmp_path / "x.png", "X")):
        assert not is_generated_chart(path, ticker)


def test_png_text_chunks_stop_at_the_image_data_and_survive_truncation():
    png = draw()
    head = png_text_chunks(png[:4096])  # the marker sits before the image data
    assert head == {"Description": chart_description("T003", SESSION)}
    assert png_text_chunks(png[:20]) == {}  # cut inside the first chunk
    assert png_text_chunks(png[:70]) == {}  # cut inside the text chunk
    assert png_text_chunks(b"") == {}


def test_the_name_pattern_alone_never_makes_a_png_ours(tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(buffer, format="PNG")
    # README looks like a ticker to the name pattern - the missing marker decides
    (tmp_path / "README.png").write_bytes(buffer.getvalue())
    assert not _is_generated_chart_file(tmp_path / "README.png")
    # a marker for another ticker than the file name says
    (tmp_path / "AAPL.png").write_bytes(stub_chart("MSFT", None, SESSION, [], None))
    assert not _is_generated_chart_file(tmp_path / "AAPL.png")
    # a lower-case name is no ticker file at all
    (tmp_path / "t003.png").write_bytes(stub_chart("T003", None, SESSION, [], None))
    assert not _is_generated_chart_file(tmp_path / "t003.png")
    (tmp_path / "T003.png").write_bytes(stub_chart("T003", None, SESSION, [], None))
    assert _is_generated_chart_file(tmp_path / "T003.png")


# ── in the generator ──────────────────────────────────────────────────


def real_only_for(ticker):
    """The real renderer for one ticker (a pack has dozens: 0.3 s each)."""

    def renderer(t, company, session, bars, analysis):
        if t == ticker:
            return render_chart_png(t, company, session, bars, analysis)
        return stub_chart(t, company, session, bars, analysis)

    return renderer


def generate(tmp_path, monkeypatch, renderer=None, loader=None, requested=("T003",)):
    monkeypatch.setattr(
        "trading_signals.derived.context_pack.generator.render_chart_png",
        renderer or real_only_for("T003"),
    )
    gen = ContextPackGenerator(
        MagicMock(), output_dir=str(tmp_path), loader=loader or make_loader(n=120)
    )
    gen.generate_daily(SESSION, requested=list(requested))
    return tmp_path / SESSION.isoformat()


def test_the_generator_writes_the_chart_next_to_the_ticker_file(tmp_path, monkeypatch):
    day = generate(tmp_path, monkeypatch)
    png = (day / "T003.png").read_bytes()
    assert png.startswith(b"\x89PNG") and len(png) <= MAX_CHART_BYTES
    text = (day / "T003.md").read_text(encoding="utf-8-sig")
    assert "![Chart T003](T003.png)" in text and "`T003.png`" in text
    # every ticker file has its chart, and only ticker files
    stems = {p.stem for p in day.glob("*.md") if p.name != "00_uebersicht.md"}
    assert {p.stem for p in day.glob("*.png")} == stems
    for path in day.glob("*.png"):
        assert is_generated_chart(path, path.stem)


def test_a_rerun_gives_identical_charts_and_files(tmp_path, monkeypatch):
    first = generate(tmp_path / "a", monkeypatch)
    second = generate(tmp_path / "b", monkeypatch)
    assert sha((first / "T003.png").read_bytes()) == sha(
        (second / "T003.png").read_bytes()
    )
    assert (first / "T003.md").read_bytes() == (second / "T003.md").read_bytes()


def test_a_chart_failure_costs_only_the_image(tmp_path, monkeypatch, caplog):
    def broken(ticker, *args):
        if ticker == "T003":
            raise RuntimeError("font cache exploded")
        return stub_chart(ticker, *args)

    with caplog.at_level("WARNING"):
        day = generate(tmp_path, monkeypatch, renderer=broken)
    assert not (day / "T003.png").exists()
    text = (day / "T003.md").read_text(encoding="utf-8-sig")
    assert "T003.png" not in text and "![Chart" not in text
    assert "## 2. Trend und Momentum" in text  # the rest is intact
    assert (day / "00_uebersicht.md").is_file()  # and the pack is complete
    assert "no chart for T003" in caplog.text and "font cache exploded" in caplog.text
    # the other tickers kept their charts
    assert any(p.stem != "T003" for p in day.glob("*.png"))


def test_no_chart_without_bars_and_the_text_still_says_what_is_known(
    tmp_path, monkeypatch
):
    loader = make_loader(n=120)
    original = loader.raw_sources

    def bare(d, tickers):
        out = original(d, tickers)
        out.by_ticker["T003"].bars = []
        return out

    loader.raw_sources = bare
    day = generate(tmp_path, monkeypatch, loader=loader)
    assert not (day / "T003.png").exists()
    text = (day / "T003.md").read_text(encoding="utf-8-sig")
    assert "52-Wochen-Spanne: keine Abdeckung" in text
    assert "Unterstützung / Widerstand: keine Abdeckung" in text
    assert "![Chart" not in text


def test_a_rerun_replaces_only_charts_the_generator_wrote(tmp_path, monkeypatch):
    day_dir = tmp_path / SESSION.isoformat()
    day_dir.mkdir()
    # a chart of an earlier run of ours for a ticker that is no longer selected
    (day_dir / "OLDX.png").write_bytes(stub_chart("OLDX", None, SESSION, [], None))
    # images that are not ours, whatever they are called
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    mine = buffer.getvalue()
    (day_dir / "README.png").write_bytes(mine)
    (day_dir / "ABCD.png").write_bytes(mine)  # a ticker-like name, no marker
    (day_dir / "OTHR.png").write_bytes(stub_chart("WRONG", None, SESSION, [], None))
    (day_dir / "screenshot.png").write_bytes(mine)
    (day_dir / "notes.txt").write_text("mine", encoding="utf-8")
    day = generate(tmp_path, monkeypatch)
    assert not (day / "OLDX.png").exists()
    for kept in ("README.png", "ABCD.png", "OTHR.png", "screenshot.png", "notes.txt"):
        assert (day / kept).exists(), kept
    assert (day / "ABCD.png").read_bytes() == mine
    assert (day / "T003.png").is_file()


def test_a_chart_is_removed_when_the_new_run_has_no_chart_for_the_ticker(
    tmp_path, monkeypatch
):
    day = generate(tmp_path, monkeypatch)
    assert (day / "T003.png").is_file()

    def broken(ticker, *args):
        if ticker == "T003":
            raise RuntimeError("no")
        return stub_chart(ticker, *args)

    day = generate(tmp_path, monkeypatch, renderer=broken)
    # a stale picture next to a file that no longer mentions it would mislead
    assert not (day / "T003.png").exists()


def test_a_pack_that_was_assessed_keeps_its_charts(tmp_path, monkeypatch):
    day = generate(tmp_path, monkeypatch)
    png = (day / "T003.png").read_bytes()
    (day / "forecasts.yaml").write_text("schema: forecasts/v1\n", encoding="utf-8")
    monkeypatch.setattr(
        "trading_signals.derived.context_pack.generator.render_chart_png",
        lambda *a: b"changed",
    )
    gen = ContextPackGenerator(
        MagicMock(), output_dir=str(tmp_path), loader=make_loader(n=120)
    )
    assert gen.generate_daily(SESSION, requested=["T003"]) == 0
    assert (day / "T003.png").read_bytes() == png


def test_every_png_name_in_a_pack_is_a_ticker_file_name(tmp_path, monkeypatch):
    day = generate(tmp_path, monkeypatch)
    for path in day.glob("*.png"):
        assert re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}\.png", path.name)
