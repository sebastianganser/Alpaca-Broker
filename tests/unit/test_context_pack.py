"""Unit tests for the context pack v2 generator (synthetic data, no DB).

Format: docs/CONTEXT_PACK.md. The generator is driven by a fake ``PackLoader``
(``tests/unit/pack_data.py``); the session is a mock, so nothing here opens a
database session.
"""

from __future__ import annotations

import codecs
import re
from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
import yaml

from tests.unit.pack_data import SESSION, FakeLoader, make_loader
from trading_signals.analysis.forecast_review import gate_flags
from trading_signals.analysis.screen import (
    DEFAULT_CONFIG,
    scored_features,
    screen_cross_section,
)
from trading_signals.db.models.forecasts import PackCandidate
from trading_signals.derived.context_pack import PACK_FORMAT, ContextPackGenerator
from trading_signals.derived.context_pack.constants import (
    FORECASTS_FILE,
    MAX_REQUESTED,
    OVERVIEW_FILE,
    REQUESTED_FILE,
    SCREEN_FILE,
)
from trading_signals.derived.context_pack.generator import (
    parse_requested,
    read_requested_tickers,
)

#: A bucket word in a ticker file would reveal why the file is in the pack.
BUCKET_WORDS = (
    "bucket",
    "bull",
    "bear",
    "random",
    "zufall",
    "requested",
    "wunsch",
    "shortlist",
    "bucket_rank",
    "rang ",
    "evidence_breadth",
    "composite",
    "screen/v1",
)
MAX_TICKER_FILE_BYTES = 12_000


def run(tmp_path, loader=None, requested=None, session=None):
    loader = loader or make_loader(n=120)
    session = session or MagicMock()
    gen = ContextPackGenerator(session, output_dir=str(tmp_path), loader=loader)
    count = gen.generate_daily(SESSION, requested=requested)
    return count, tmp_path / SESSION.isoformat(), session


def ticker_files(day_dir):
    return sorted(p for p in day_dir.glob("*.md") if p.name != OVERVIEW_FILE)


def read(path):
    return path.read_text(encoding="utf-8-sig")


def frontmatter(text):
    assert text.startswith("---\n")
    return yaml.safe_load(text.split("---\n")[1])


def expected_buckets(loader, requested=()):
    screened = screen_cross_section(
        loader.snapshots(SESSION), as_of=SESSION, requested=requested
    )
    return screened[screened["bucket"] != ""]


# ── files, order, encoding ────────────────────────────────────────────


def test_pack_files_are_written_alphabetically_with_bom(tmp_path):
    loader = make_loader(n=120)
    count, day_dir, _ = run(tmp_path, loader, requested=["T003"])
    files = ticker_files(day_dir)
    assert count == len(files) > 0
    assert (day_dir / OVERVIEW_FILE).is_file() and (day_dir / SCREEN_FILE).is_file()
    names = [p.stem for p in files]
    assert names == sorted(names)  # the file system order is not the selection order
    for path in [*files, day_dir / OVERVIEW_FILE, day_dir / SCREEN_FILE]:
        assert path.read_bytes().startswith(b"\xef\xbb\xbf"), path.name
    # no legacy rank prefixes
    assert not [
        p
        for p in day_dir.iterdir()
        if re.match(r"\d\d_", p.name) and p.name != OVERVIEW_FILE
    ]


def test_selection_is_the_screen_buckets_and_deterministic(tmp_path):
    loader = make_loader(n=120)
    _, day_dir, _ = run(tmp_path / "a", loader, requested=["T003"])
    expected = expected_buckets(loader, ["T003"])
    assert [p.stem for p in ticker_files(day_dir)] == sorted(expected["ticker"])
    counts = expected["bucket"].value_counts().to_dict()
    assert counts.get("bull", 0) <= 10 and counts.get("random") == 5
    assert "bear" not in counts  # the bearish bucket is switched off
    # a re-run (even in another folder) selects the same names
    _, day_dir2, _ = run(tmp_path / "b", make_loader(n=120), requested=["T003"])
    assert [p.name for p in ticker_files(day_dir2)] == [
        p.name for p in ticker_files(day_dir)
    ]


def test_no_bucket_or_rank_leaks_into_the_files(tmp_path):
    _, day_dir, _ = run(tmp_path, requested=["T003"])
    for path in ticker_files(day_dir):
        text = read(path)
        low = text.lower()
        for word in BUCKET_WORDS:
            assert word not in low, f"{word!r} in {path.name}"
        fm = frontmatter(text)
        assert set(fm) == {
            "pack_format",
            "ticker",
            "session",
            "sector",
            "data_freshness",
        }
        assert fm["pack_format"] == PACK_FORMAT == 2
        assert fm["ticker"] == path.stem and str(fm["session"]) == SESSION.isoformat()
    header = read(day_dir / SCREEN_FILE).splitlines()[0].split(",")
    assert not {"bucket", "bucket_rank", "is_requested", "rank"} & set(header)
    # the overview lists the files alphabetically with ticker / company / sector only
    overview = read(day_dir / OVERVIEW_FILE)
    table = [ln for ln in overview.splitlines() if re.match(r"\| T\d{3} \|", ln)]
    assert [ln.split("|")[1].strip() for ln in table] == [
        p.stem for p in ticker_files(day_dir)
    ]
    assert all(ln.count("|") == 4 for ln in table)


# ── the quality gate and screen.csv ───────────────────────────────────


def degraded_loader():
    """120 tickers; some fail each gate check."""
    loader = make_loader(n=120)
    snap = loader.snap
    snap.loc[snap["ticker"] == "T010", "dollar_volume_20d"] = 1e5  # liquidity
    snap.loc[snap["ticker"] == "T011", "price_vs_sma50"] = np.nan  # < 60 price days
    few = snap["ticker"] == "T012"
    for col in scored_features():  # almost no group has data
        if col not in ("price_vs_sma50", "dollar_volume_20d"):
            snap.loc[few, col] = np.nan
    return loader


def test_screen_csv_holds_exactly_the_gate_tickers_and_matches_the_evaluation_gate(
    tmp_path,
):
    loader = degraded_loader()
    _, day_dir, _ = run(tmp_path, loader)
    csv = pd.read_csv(day_dir / SCREEN_FILE, encoding="utf-8-sig")
    # the evaluation rebuilds the gate with forecast_review.gate_flags on the
    # snapshot rows (price-history proxy: price_vs_sma50 not NULL)
    snap = loader.snap
    flags = gate_flags(snap.drop(columns=["snapshot_date"]).reset_index(drop=True))
    gate_tickers = sorted(snap.loc[flags.to_numpy(), "ticker"])
    assert {"T010", "T011", "T012"}.isdisjoint(gate_tickers)
    assert len(gate_tickers) == 117
    assert list(csv["ticker"]) == gate_tickers  # exactly the gate, alphabetical
    # group percentiles are whole numbers 0 ... 100, empty = no data (not 0)
    p_cols = [c for c in csv.columns if c.startswith("p_")]
    assert len(p_cols) == 12
    values = csv[p_cols].stack()
    assert values.between(0, 100).all() and (values == values.round()).all()
    assert {
        "evidence_breadth",
        "composite",
        "composite_pct",
        "earnings_days_until",
        "sector",
        "dollar_volume_musd",
        "events_bull",
        "events_bear",
    } <= set(csv.columns)


def test_gate_failures_are_counted_in_the_overview(tmp_path):
    _, day_dir, _ = run(tmp_path, degraded_loader())
    overview = read(day_dir / OVERVIEW_FILE)
    assert "Gate bestanden: **117**" in overview
    assert "Feature-Snapshots der Sitzung: **120**" in overview
    assert "| Handelsvolumen unter dem Gate-Minimum | 1 |" in overview
    assert "| zu wenig Kurshistorie | 1 |" in overview
    assert "| zu wenige Datengruppen befüllt | 1 |" in overview


# ── requested tickers ─────────────────────────────────────────────────


def test_requested_tickers_file_failing_gate_and_missing_snapshot(tmp_path):
    (tmp_path / REQUESTED_FILE).write_text(
        "﻿# wishes\nt010  # fails the liquidity gate\nZZZZ\nT003\nt003\nbad ticker!\n",
        encoding="utf-8",
    )
    count, day_dir, _ = run(tmp_path, degraded_loader())
    stems = [p.stem for p in ticker_files(day_dir)]
    assert {"T010", "T003"} <= set(stems) and "ZZZZ" not in stems
    gated_out = read(day_dir / "T010.md")
    assert "Qualitäts-Gate nicht bestanden" in gated_out
    assert "Handelsvolumen unter dem Gate-Minimum" in gated_out
    assert "T010" not in read(day_dir / SCREEN_FILE)
    assert "Qualitäts-Gate nicht bestanden" not in read(day_dir / "T003.md")
    overview = read(day_dir / OVERVIEW_FILE)
    assert "Wunsch-Ticker ohne Daten" in overview and "ZZZZ" in overview
    assert count == len(stems)


def test_explicit_requested_argument_overrides_the_file(tmp_path):
    (tmp_path / REQUESTED_FILE).write_text("T020\n", encoding="utf-8")
    loader = make_loader(n=120)
    _, day_dir, _ = run(tmp_path, loader, requested=["T021"])
    expected = expected_buckets(loader, ["T021"])
    assert [p.stem for p in ticker_files(day_dir)] == sorted(expected["ticker"])
    assert "T021" in {p.stem for p in ticker_files(day_dir)}


def test_requested_tickers_are_capped(tmp_path):
    wanted = [f"T{i:03d}" for i in range(MAX_REQUESTED + 3)]
    _, day_dir, _ = run(tmp_path, requested=wanted)
    overview = read(day_dir / OVERVIEW_FILE)
    assert "über dem Limit" in overview
    for extra in wanted[MAX_REQUESTED:]:
        assert extra in overview.split("über dem Limit")[1]


def test_parse_requested_and_read_file(tmp_path):
    text = "﻿# comment\nnvda\n  BRK.B  # class B\n\nNVDA\n123\nfoo bar\n"
    assert parse_requested(text.lstrip("﻿")) == ["NVDA", "BRK.B"]
    assert read_requested_tickers(tmp_path) == []  # no file
    (tmp_path / REQUESTED_FILE).write_text(text, encoding="utf-8-sig")
    assert read_requested_tickers(tmp_path) == ["NVDA", "BRK.B"]


def test_requested_tickers_file_in_other_encodings(tmp_path):
    body = "# Wünsche\nnvda\n  BRK.B  # class B\n"
    path = tmp_path / REQUESTED_FILE
    # Notepad "ANSI" (Windows-1252): the umlaut is not valid UTF-8
    path.write_bytes(body.encode("cp1252"))
    assert read_requested_tickers(tmp_path) == ["NVDA", "BRK.B"]
    # PowerShell / Notepad "Unicode": UTF-16 LE and BE with a BOM
    path.write_bytes(codecs.BOM_UTF16_LE + body.encode("utf-16-le"))
    assert read_requested_tickers(tmp_path) == ["NVDA", "BRK.B"]
    path.write_bytes(codecs.BOM_UTF16_BE + body.encode("utf-16-be"))
    assert read_requested_tickers(tmp_path) == ["NVDA", "BRK.B"]
    # UTF-8 without and with a BOM, CRLF line ends
    path.write_bytes(body.replace("\n", "\r\n").encode("utf-8"))
    assert read_requested_tickers(tmp_path) == ["NVDA", "BRK.B"]
    path.write_bytes(codecs.BOM_UTF8 + body.encode("utf-8"))
    assert read_requested_tickers(tmp_path) == ["NVDA", "BRK.B"]


def test_an_undecodable_requested_file_is_ignored_with_a_warning(tmp_path, caplog):
    path = tmp_path / REQUESTED_FILE
    path.write_bytes(b"NVDA\n\x81\x8d\x8f\x90\x9d\n")  # invalid in cp1252 too
    with caplog.at_level("WARNING"):
        assert read_requested_tickers(tmp_path) == []
    assert any("cannot decode" in r.getMessage() for r in caplog.records)
    # and the pack is generated without requested tickers instead of failing
    _, day_dir, _ = run(tmp_path)
    assert (day_dir / OVERVIEW_FILE).is_file()


def test_a_requested_file_read_error_is_ignored(tmp_path):
    (tmp_path / REQUESTED_FILE).write_text("NVDA\n", encoding="utf-8")
    with patch("pathlib.Path.read_bytes", side_effect=PermissionError("denied")):
        assert read_requested_tickers(tmp_path) == []


# ── NULL is not zero ──────────────────────────────────────────────────


def test_missing_sources_render_no_coverage_never_zero(tmp_path):
    loader = make_loader(n=120)
    target = "T003"
    snap = loader.snap
    for col in snap.columns:
        if col.startswith(
            ("insider_", "politician_", "options_iv_", "ark_", "short_interest_")
        ):
            snap.loc[snap["ticker"] == target, col] = np.nan
    snap.loc[snap["ticker"] == target, "cluster_score_sum_60d"] = np.nan
    original = loader.raw_sources

    def bare(d, tickers):
        out = original(d, tickers)
        raw = out.by_ticker[target]
        raw.insider, raw.politicians, raw.ark, raw.iv = [], [], [], []
        raw.short_interest, raw.headlines, raw.holders = [], [], None
        return out

    loader.raw_sources = bare
    _, day_dir, _ = run(tmp_path, loader, requested=[target])
    text = read(day_dir / f"{target}.md")
    assert "Insider-Meldungen: keine Abdeckung" in text
    assert "Offenlegungen: keine Abdeckung" in text
    assert "Keine aktuelle 13F-Abdeckung" in text
    assert "In keinem ARK-ETF gehalten" in text
    assert "ATM-IV 30 T: keine Abdeckung" in text
    assert "IV-Rank (252 Tage): keine Abdeckung" in text
    assert "Days-to-Cover: keine Abdeckung" in text
    assert "Schlagzeilen der letzten 30 Tage: keine Abdeckung" in text
    assert "ATM-IV 30 T: 0" not in text and "Days-to-Cover: 0" not in text
    fm = frontmatter(text)["data_freshness"]
    assert (
        fm["options_iv"] is None and fm["politicians"] is None and fm["form13f"] is None
    )


def test_a_covered_zero_is_shown_as_zero(tmp_path):
    loader = make_loader(n=120)
    loader.snap.loc[loader.snap["ticker"] == "T003", "insider_net_buy_count_30d"] = 0.0
    _, day_dir, _ = run(tmp_path, loader, requested=["T003"])
    assert "Netto-Käufe 30 T (Käufe minus Verkäufe): 0 (" in read(day_dir / "T003.md")


# ── point in time ─────────────────────────────────────────────────────


def test_rows_that_were_not_public_at_the_close_are_never_shown(tmp_path):
    _, day_dir, _ = run(tmp_path)
    for path in ticker_files(day_dir):
        text = read(path)
        # Form 4 accepted 16:05 ET, news published 16:30 ET, a rating and a
        # politician disclosure dated after the session: all hidden
        assert "LEAK" not in text, path.name
        assert "99.00" not in text  # estimate snapshot dated after the session
        assert "9.90" not in text  # earnings date first seen after the session
        si = text.split("Short Interest (Aktien)")[1].split("###")[0]
        assert "2026-09-30" in si and "2026-10-15" not in si and "99.00 Mio" not in si
        # the visible neighbours of the hidden rows ARE shown
        assert "Early Bird" in text  # Form 4 accepted 15:55 ET
        assert "beats expectations" in text  # published 10:00 ET
        nxt = "Nächster Termin (Information, kein Ausschlusskriterium):** 2026-11-15"
        assert nxt in text


# ── pack candidates ───────────────────────────────────────────────────


def test_pack_candidates_are_written_for_exactly_the_files(tmp_path):
    session = MagicMock()
    _, day_dir, _ = run(tmp_path, requested=["T003"], session=session)
    added = [c.args[0] for c in session.add.call_args_list]
    assert added and all(isinstance(a, PackCandidate) for a in added)
    assert {a.ticker for a in added} == {p.stem for p in ticker_files(day_dir)}
    assert {a.session_date for a in added} == {SESSION}
    assert {a.bucket for a in added} <= {"bull", "random", "requested"}
    assert "delete" in str(session.execute.call_args_list[0].args[0]).lower()
    session.flush.assert_called()
    session.commit.assert_not_called()  # the caller owns the transaction


def test_file_write_failure_leaves_the_transaction_to_the_caller(tmp_path):
    session = MagicMock()
    blocker = tmp_path / SESSION.isoformat()
    blocker.write_text("a file where the folder should be")
    gen = ContextPackGenerator(session, output_dir=str(tmp_path), loader=make_loader())
    with pytest.raises(OSError):
        gen.generate_daily(SESSION)
    session.commit.assert_not_called()  # get_session() rolls back on the exception


# ── re-runs and stale files ───────────────────────────────────────────


GENERATED = "---\npack_format: 2\nticker: {t}\nsession: 2026-10-16\n---\n\n# {t}\n"
LEGACY = "---\nticker: {t}\nrank: 3\n---\n\n# {t}\n"


def test_rerun_of_an_unpublished_pack_replaces_only_files_the_generator_wrote(
    tmp_path,
):
    day_dir = tmp_path / SESSION.isoformat()
    day_dir.mkdir()
    # files of an earlier run (ours): a ticker file and a legacy v1 file
    (day_dir / "OLDX.md").write_text(GENERATED.format(t="OLDX"), encoding="utf-8")
    (day_dir / "01_LEGACY.md").write_text(LEGACY.format(t="LEGACY"), encoding="utf-8")
    # files that are not ours, whatever their name looks like
    (day_dir / "README.md").write_text("# my notes\n", encoding="utf-8")
    (day_dir / "ABCD.md").write_text("---\ntitle: mine\n---\n", encoding="utf-8")
    (day_dir / "02_THING.md").write_text("no frontmatter", encoding="utf-8")
    (day_dir / "OTHR.md").write_text(GENERATED.format(t="WRONG"), encoding="utf-8")
    (day_dir / "notes.txt").write_text("mine", encoding="utf-8")
    _, day_dir, _ = run(tmp_path)
    assert (
        not (day_dir / "OLDX.md").exists() and not (day_dir / "01_LEGACY.md").exists()
    )
    for kept in ("README.md", "ABCD.md", "02_THING.md", "OTHR.md", "notes.txt"):
        assert (day_dir / kept).exists(), kept
    assert (day_dir / "README.md").read_text(encoding="utf-8") == "# my notes\n"
    assert (day_dir / OVERVIEW_FILE).is_file() and (day_dir / SCREEN_FILE).is_file()


def test_a_pack_with_forecasts_yaml_is_never_regenerated(tmp_path):
    day_dir = tmp_path / SESSION.isoformat()
    day_dir.mkdir()
    (day_dir / FORECASTS_FILE).write_text("schema: forecasts/v1\n", encoding="utf-8")
    (day_dir / "OLDX.md").write_text(GENERATED.format(t="OLDX"), encoding="utf-8")
    (day_dir / OVERVIEW_FILE).write_text("old overview", encoding="utf-8")
    session = MagicMock()
    loader = make_loader(n=120)
    gen = ContextPackGenerator(session, output_dir=str(tmp_path), loader=loader)
    assert gen.generate_daily(SESSION) == 0
    assert gen.skip_reason == f"{FORECASTS_FILE} exists"
    # nothing was touched: files, candidates, not even the data was loaded
    assert (day_dir / "OLDX.md").exists()
    assert (day_dir / OVERVIEW_FILE).read_text(encoding="utf-8") == "old overview"
    assert (day_dir / FORECASTS_FILE).read_text(
        encoding="utf-8"
    ) == "schema: forecasts/v1\n"
    assert not (day_dir / SCREEN_FILE).exists()
    session.add.assert_not_called()
    session.execute.assert_not_called()  # the candidates are not replaced
    assert "snapshots" not in loader.calls


def test_a_pack_whose_candidates_are_stored_is_never_regenerated(tmp_path):
    loader = make_loader(n=120)
    loader.stored_candidates = True
    day_dir = tmp_path / SESSION.isoformat()
    day_dir.mkdir()
    (day_dir / "OLDX.md").write_text(GENERATED.format(t="OLDX"), encoding="utf-8")
    session = MagicMock()
    gen = ContextPackGenerator(session, output_dir=str(tmp_path), loader=loader)
    assert gen.generate_daily(SESSION) == 0
    assert gen.skip_reason == "pack_candidates exist"
    assert (day_dir / "OLDX.md").exists() and not (day_dir / SCREEN_FILE).exists()
    session.add.assert_not_called()
    session.execute.assert_not_called()
    # an operator can still force a regeneration (repair), the chain never does
    assert gen.generate_daily(SESSION, force=True) > 0
    assert gen.skip_reason is None and not (day_dir / "OLDX.md").exists()
    assert session.execute.called  # the candidates were replaced this time


def test_skip_reason_is_cleared_by_a_normal_run(tmp_path):
    loader = make_loader(n=120)
    gen = ContextPackGenerator(MagicMock(), output_dir=str(tmp_path), loader=loader)
    gen.skip_reason = "left over"
    assert gen.generate_daily(SESSION) > 0 and gen.skip_reason is None


def test_no_snapshots_writes_nothing(tmp_path):
    loader = make_loader(n=120)
    session = MagicMock()
    gen = ContextPackGenerator(session, output_dir=str(tmp_path), loader=loader)
    assert gen.generate_daily(date(2026, 10, 15)) == 0
    assert list(tmp_path.iterdir()) == []
    session.add.assert_not_called()


def test_a_session_where_nobody_passes_the_gate_still_writes_overview_and_csv(
    tmp_path,
):
    loader = make_loader(n=40)
    loader.snap["dollar_volume_20d"] = 1e3
    session = MagicMock()
    count, day_dir, _ = run(tmp_path, loader, session=session)
    assert count == 0 and ticker_files(day_dir) == []
    assert len(read(day_dir / SCREEN_FILE).strip().splitlines()) == 1  # header only
    overview = read(day_dir / OVERVIEW_FILE)
    assert (
        "Heute keine Ticker-Dateien" in overview and "Gate bestanden: **0**" in overview
    )
    session.add.assert_not_called()  # no candidates ...
    assert session.execute.called  # ... and the old rows of the session are replaced


# ── size, speed, batch queries ────────────────────────────────────────


def test_production_sized_session_stays_within_the_budgets(tmp_path):
    import time

    loader = make_loader(n=650)
    started = time.monotonic()
    count, day_dir, _ = run(
        tmp_path, loader, requested=[f"T{i:03d}" for i in range(10)]
    )
    elapsed = time.monotonic() - started
    assert elapsed < 20  # measured about 0.5 s; generous for slow CI machines
    sizes = [p.stat().st_size for p in ticker_files(day_dir)]
    assert count == len(sizes) <= 10 + 10 + 5 + 10
    assert max(sizes) < MAX_TICKER_FILE_BYTES  # densest possible synthetic file
    assert (day_dir / SCREEN_FILE).stat().st_size < 150_000
    # one batch query per source: the loader is called once per kind, not per ticker
    assert loader.calls.count("raw_sources") == 1
    assert loader.calls.count("snapshots") == 1 and loader.calls.count("closes") == 1
    assert len(loader.raw_requests[0]) == count


def test_screen_runs_with_the_default_config():
    # the pack must use the default (registered) screen config, nothing else
    assert (
        ContextPackGenerator(MagicMock(), output_dir="x", loader=FakeLoader()).config
        is DEFAULT_CONFIG
    )


# ── entry point of the nightly chain ──────────────────────────────────


def test_nightly_chain_entry_point_still_works():
    from trading_signals.scheduler import jobs

    fake_session = MagicMock()
    ctx = MagicMock()
    ctx.__enter__.return_value = fake_session
    gen = MagicMock()
    gen.return_value.generate_daily.return_value = 7
    gen.return_value.skip_reason = None
    with (
        patch("trading_signals.db.session.get_session", return_value=ctx),
        patch("trading_signals.derived.context_pack.ContextPackGenerator", gen),
    ):
        outcome = jobs._context_pack_body(SESSION)
    gen.assert_called_once_with(fake_session)
    gen.return_value.generate_daily.assert_called_once_with(SESSION)
    assert outcome.records_written == 7 and outcome.notes == "session=2026-10-16"


def test_a_kept_pack_is_a_success_with_a_note():
    from trading_signals.scheduler import jobs

    ctx = MagicMock()
    gen = MagicMock()
    gen.return_value.generate_daily.return_value = 0
    gen.return_value.skip_reason = "forecasts.yaml exists"
    with (
        patch("trading_signals.db.session.get_session", return_value=ctx),
        patch("trading_signals.derived.context_pack.ContextPackGenerator", gen),
    ):
        outcome = jobs._context_pack_body(SESSION)
    assert outcome.status == "success" and outcome.records_written == 0
    assert outcome.notes == (
        "session=2026-10-16; pack kept (forecasts.yaml exists) - not regenerated"
    )


def test_old_generator_module_is_gone():
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("trading_signals.derived.context_pack_generator")
