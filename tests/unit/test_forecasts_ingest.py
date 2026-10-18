"""Unit tests for ``ingest_forecasts`` (derived/forecasts.py, in-memory store)."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import yaml

from trading_signals.derived.forecasts import (
    FORECASTS_FILE,
    ForecastRecord,
    ReviewRecord,
    ingest_forecasts,
)

#: Open of the session after the pack day 2026-10-16 (Monday 09:30 New York).
OPEN = datetime(2026, 10, 19, 13, 30, tzinfo=UTC)
#: The night run: the deadline has passed, the seals are final.
AFTER = datetime(2026, 10, 20, 2, 30, tzinfo=UTC)
#: A seal run five minutes before the open (the 09:25 trigger).
SEALED = datetime(2026, 10, 19, 13, 25, tzinfo=UTC)
DEFAULT_TICKERS = {"NVDA", "MSFT", "AAPL", "TSLA"}


class MemoryStore:
    """The part of the database the ingest touches."""

    def __init__(self, tickers: dict[date, set[str]] | None = None) -> None:
        self.reviews: list[ReviewRecord] = []
        self.forecasts: list[tuple[int, ForecastRecord]] = []
        self.tickers = tickers or {}
        self.seals: dict[date, dict[str, datetime]] = {}
        self.fail_on: dict[str, Exception] = {}  # sha prefix -> error on add_review

    def known_hashes(self) -> set[str]:
        return {r.file_sha256 for r in self.reviews}

    def session_tickers(self, session_date: date) -> set[str]:
        return self.tickers.get(session_date, DEFAULT_TICKERS)

    def seal_times(self, session_date: date) -> dict[str, datetime]:
        return dict(self.seals.get(session_date, {}))

    def add_review(self, review, forecasts) -> int:
        for prefix, error in self.fail_on.items():
            if review.file_sha256.startswith(prefix) and review.status != "rejected":
                raise error
        self.reviews.append(review)
        review_id = len(self.reviews)
        self.forecasts += [(review_id, f) for f in forecasts]
        return review_id


def _text(session: str, rows: list[dict], version: str = "skill/1+opus") -> str:
    return yaml.safe_dump(
        {
            "schema": "forecasts/v1",
            "session": session,
            "decided_at": f"{session}T20:00:00+00:00",
            "forecaster_version": version,
            "forecasts": rows,
        },
        sort_keys=False,
    )


def _row(ticker="NVDA", horizon=20, direction="up", **kw) -> dict:
    row = {
        "ticker": ticker,
        "horizon_days": horizon,
        "direction": direction,
        "confidence": 0.6,
        "conviction": 3,
    }
    return {**row, **kw}


def _pack(root: Path, day: str, content: str | None) -> Path:
    d = root / day
    d.mkdir()
    (d / "00_uebersicht.md").write_text("x", encoding="utf-8")
    if content is not None:
        (d / FORECASTS_FILE).write_text(content, encoding="utf-8")
    return d


def _seal(store: MemoryStore, folder: Path, at: datetime = SEALED) -> str:
    """Seal the CURRENT bytes of the folder's file (what the scheduler job does)."""
    sha = hashlib.sha256((folder / FORECASTS_FILE).read_bytes()).hexdigest()
    store.seals.setdefault(date.fromisoformat(folder.name), {})[sha] = at
    return sha


def _sealed_pack(
    root: Path, store: MemoryStore, day: str, content: str, at: datetime = SEALED
) -> Path:
    d = _pack(root, day, content)
    _seal(store, d, at)
    return d


def _ingest(root: Path, store: MemoryStore, entry_open=OPEN, now=AFTER):
    return ingest_forecasts(store, root, open_fn=lambda d: entry_open, now=now)


def test_missing_root_and_no_files_do_nothing(tmp_path: Path):
    store = MemoryStore()
    assert _ingest(tmp_path / "nope", store).files == 0
    _pack(tmp_path, "2026-10-16", None)  # pack without a forecasts.yaml
    (tmp_path / "forecast_auswertung.md").write_text("x", encoding="utf-8")
    (tmp_path / "notes").mkdir()  # not a date folder
    res = _ingest(tmp_path, store)
    assert (res.files, res.ingested, res.errors) == (0, 0, [])
    assert store.reviews == []


def test_ingest_stores_review_and_forecasts_with_valid_from(tmp_path: Path):
    store = MemoryStore()
    _sealed_pack(
        tmp_path,
        store,
        "2026-10-16",
        _text(
            "2026-10-16",
            [
                _row("NVDA", 60, expected_pct=4.0, drivers=["revisions"]),
                _row("MSFT", 20, "neutral", confidence=None, conviction=None),
            ],
        ),
    )
    res = _ingest(tmp_path, store)
    assert (res.files, res.ingested, res.unchanged, res.forecasts) == (1, 1, 0, 2)
    review = store.reviews[0]
    assert review.session_date == date(2026, 10, 16) and review.on_time
    assert (review.status, review.n_forecasts, review.n_rejected) == ("ok", 2, 0)
    assert (
        review.schema == "forecasts/v1" and review.forecaster_version == "skill/1+opus"
    )
    assert len(review.file_sha256) == 64 and review.errors == []
    nvda, msft = (f for _, f in store.forecasts)
    assert nvda.valid_from == OPEN and nvda.on_time and nvda.drivers == ["revisions"]
    assert nvda.forecaster_version == "skill/1+opus"
    assert nvda.decided_at == datetime(2026, 10, 16, 20, 0, tzinfo=UTC)
    assert (msft.direction, msft.confidence, msft.conviction) == ("neutral", None, None)


def test_same_file_twice_is_idempotent(tmp_path: Path):
    store = MemoryStore()
    _sealed_pack(tmp_path, store, "2026-10-16", _text("2026-10-16", [_row()]))
    _ingest(tmp_path, store)
    again = _ingest(tmp_path, store)
    assert (again.files, again.ingested, again.unchanged) == (1, 0, 1)
    assert len(store.reviews) == 1 and len(store.forecasts) == 1


def test_changed_file_is_a_new_review_and_old_rows_stay(tmp_path: Path):
    store = MemoryStore()
    d = _sealed_pack(tmp_path, store, "2026-10-16", _text("2026-10-16", [_row("NVDA")]))
    _ingest(tmp_path, store)
    (d / FORECASTS_FILE).write_text(
        _text("2026-10-16", [_row("NVDA"), _row("AAPL")]), encoding="utf-8"
    )
    _seal(store, d)  # the edit happened before the open and was sealed too
    res = _ingest(tmp_path, store)
    assert res.ingested == 1 and len(store.reviews) == 2
    assert all(r.on_time for r in store.reviews)
    # append-only: the first review keeps its row, the second adds its own two
    assert [rid for rid, _ in store.forecasts] == [1, 2, 2]


# ── the on-time rule: seals, not file times ──────────────────────────────


def test_a_file_that_was_never_sealed_is_late_but_stored(tmp_path: Path):
    d = _pack(tmp_path, "2026-10-16", _text("2026-10-16", [_row("NVDA"), _row("AAPL")]))
    os.utime(d / FORECASTS_FILE, (0, 0))  # an old mtime proves nothing
    store = MemoryStore()  # the seal job never saw the file
    res = _ingest(tmp_path, store)
    assert res.ingested == 1 and res.late == 2 and res.forecasts == 2
    assert not store.reviews[0].on_time
    assert not any(f.on_time for _, f in store.forecasts)
    assert "2026-10-16 (Datei vor der Eröffnung nicht versiegelt)" in res.notes()


def test_the_file_mtime_no_longer_decides(tmp_path: Path):
    store = MemoryStore()
    d = _sealed_pack(tmp_path, store, "2026-10-16", _text("2026-10-16", [_row()]))
    future = (AFTER.timestamp() + 86400 * 365,) * 2
    os.utime(d / FORECASTS_FILE, future)  # touched a year later: still sealed in time
    _ingest(tmp_path, store)
    assert store.reviews[0].on_time and store.forecasts[0][1].on_time
    assert store.reviews[0].file_mtime.year == AFTER.year + 1  # recorded, not decisive


def test_a_seal_at_or_after_the_open_is_late(tmp_path: Path):
    store = MemoryStore()
    _sealed_pack(
        tmp_path, store, "2026-10-16", _text("2026-10-16", [_row()]), at=OPEN
    )  # exactly the open: not before it
    res = _ingest(tmp_path, store)
    assert not store.reviews[0].on_time
    assert "2026-10-16 (Siegel nach der Eröffnung)" in res.notes()


def test_a_content_edited_after_the_last_seal_is_late(tmp_path: Path):
    store = MemoryStore()
    d = _sealed_pack(tmp_path, store, "2026-10-16", _text("2026-10-16", [_row("NVDA")]))
    # edited at 09:28: no seal run any more -> a new hash that was never sealed
    (d / FORECASTS_FILE).write_text(
        _text("2026-10-16", [_row("NVDA"), _row("AAPL")]), encoding="utf-8"
    )
    res = _ingest(tmp_path, store)
    assert len(store.reviews) == 1 and not store.reviews[0].on_time
    assert res.late == 2


def test_a_decision_after_the_open_is_late_even_for_a_sealed_file(tmp_path: Path):
    store = MemoryStore()
    late_decision = _text("2026-10-16", [_row()]).replace(
        "2026-10-16T20:00:00", "2026-10-19T14:00:00"
    )
    _sealed_pack(tmp_path, store, "2026-10-16", late_decision)
    res = _ingest(tmp_path, store)
    assert not store.reviews[0].on_time
    assert "decided_at nach der Eröffnung" in res.notes()


def test_a_folder_is_read_only_after_its_deadline(tmp_path: Path):
    store = MemoryStore()
    d = _pack(tmp_path, "2026-10-16", _text("2026-10-16", [_row()]))
    before_open = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    res = _ingest(tmp_path, store, now=before_open)
    assert (res.files, res.pending, res.ingested) == (1, 1, 0)
    assert store.reviews == [] and "pending=1" in res.notes()
    # sealed in the meantime; the night after the open reads it as on time
    _seal(store, d)
    later = _ingest(tmp_path, store)
    assert later.pending == 0 and later.ingested == 1 and store.reviews[0].on_time


# ── rejections ───────────────────────────────────────────────────────────


def test_invalid_header_is_stored_once_as_rejected_and_reported(tmp_path: Path):
    _pack(tmp_path, "2026-10-15", _text("2026-10-16", [_row()]))  # session != folder
    _pack(tmp_path, "2026-10-16", "schema: stage2-decisions/v1\n")
    store = MemoryStore()
    res = _ingest(tmp_path, store)
    assert (res.files, res.ingested, res.rejected_files) == (2, 0, 2)
    assert len(res.errors) == 2 and res.errors[0].startswith("2026-10-15")
    assert [r.status for r in store.reviews] == ["rejected", "rejected"]
    assert store.forecasts == [] and store.reviews[0].errors
    # the same invalid files are not reported again on the next night
    again = _ingest(tmp_path, store)
    assert (again.unchanged, again.rejected_files, again.errors) == (2, 0, [])


def test_a_decided_at_without_a_time_zone_rejects_the_file(tmp_path: Path):
    store = MemoryStore()
    text = _text("2026-10-16", [_row()]).replace("T20:00:00+00:00", "T20:00:00")
    _sealed_pack(tmp_path, store, "2026-10-16", text)
    res = _ingest(tmp_path, store)
    assert res.rejected_files == 1 and store.forecasts == []
    assert store.reviews[0].status == "rejected"
    assert "Zeitzone fehlt" in store.reviews[0].errors[0]


def test_bad_rows_are_dropped_and_reported_the_good_ones_are_kept(tmp_path: Path):
    store = MemoryStore()
    _sealed_pack(
        tmp_path,
        store,
        "2026-10-16",
        _text(
            "2026-10-16", [_row("NVDA"), _row("MSFT", stop=5), _row("AAPL", horizon=7)]
        ),
    )
    res = _ingest(tmp_path, store)
    review = store.reviews[0]
    assert (review.status, review.n_forecasts, review.n_rejected) == ("partial", 1, 2)
    assert [f.ticker for _, f in store.forecasts] == ["NVDA"]
    assert len(review.errors) == 2 and "forecasts[2] (MSFT 20d)" in review.errors[0]
    assert (res.forecasts, res.rejected_rows) == (1, 2)
    assert "rejected_rows=2" in res.notes() and "2026-10-16" in res.notes()


def test_all_rows_bad_makes_a_rejected_review_without_forecasts(tmp_path: Path):
    _pack(tmp_path, "2026-10-16", _text("2026-10-16", [_row(direction="buy")]))
    store = MemoryStore()
    _ingest(tmp_path, store)
    assert store.reviews[0].status == "rejected" and store.forecasts == []


def test_tickers_without_a_snapshot_on_the_session_day_are_rejected(tmp_path: Path):
    _pack(tmp_path, "2026-10-16", _text("2026-10-16", [_row("NVDA"), _row("SPY")]))
    store = MemoryStore({date(2026, 10, 16): {"NVDA", "MSFT"}})
    res = _ingest(tmp_path, store)
    assert [f.ticker for _, f in store.forecasts] == ["NVDA"]
    assert store.reviews[0].status == "partial" and res.rejected_rows == 1
    assert "SPY" in store.reviews[0].errors[0] and "Feature-Snapshot" in res.errors[0]


def test_a_folder_that_is_no_session_with_snapshots_rejects_the_whole_file(
    tmp_path: Path,
):
    # a Saturday folder (or a typo): no feature snapshot exists on that date
    _pack(tmp_path, "2026-10-17", _text("2026-10-17", [_row("NVDA"), _row("MSFT")]))
    store = MemoryStore({date(2026, 10, 17): set()})
    res = _ingest(tmp_path, store)
    assert store.forecasts == [] and res.forecasts == 0
    assert (res.rejected_files, res.ingested) == (1, 0)
    review = store.reviews[0]
    assert review.status == "rejected" and not review.on_time
    assert "kein Feature-Snapshot am 2026-10-17" in review.errors[0]
    # recorded once: the next night does not report it again
    assert _ingest(tmp_path, store).errors == []


def test_utf8_bom_files_are_read(tmp_path: Path):
    store = MemoryStore()
    d = _pack(tmp_path, "2026-10-16", None)
    (d / FORECASTS_FILE).write_bytes(
        b"\xef\xbb\xbf" + _text("2026-10-16", [_row()]).encode("utf-8")
    )
    _seal(store, d)
    assert _ingest(tmp_path, store).ingested == 1
    assert store.reviews[0].on_time


# ── one bad file never blocks the others ─────────────────────────────────


def test_an_unreadable_file_is_reported_and_the_others_are_read(tmp_path: Path):
    store = MemoryStore()
    bad = _sealed_pack(tmp_path, store, "2026-10-15", _text("2026-10-15", [_row()]))
    _sealed_pack(tmp_path, store, "2026-10-16", _text("2026-10-16", [_row()]))
    real = Path.read_bytes

    def flaky(self):
        if self.parent == bad:
            raise PermissionError("denied")
        return real(self)

    with patch.object(Path, "read_bytes", flaky):
        res = _ingest(tmp_path, store)
    assert res.ingested == 1 and len(store.reviews) == 1
    assert any("nicht lesbar" in e and "PermissionError" in e for e in res.errors)
    assert store.reviews[0].session_date == date(2026, 10, 16)
    # nothing was recorded for the unreadable file: it is tried again tomorrow
    assert _ingest(tmp_path, store).ingested == 1


def test_a_file_the_database_refuses_is_recorded_as_rejected_and_not_retried(
    tmp_path: Path,
):
    store = MemoryStore()
    poison = _sealed_pack(tmp_path, store, "2026-10-15", _text("2026-10-15", [_row()]))
    _sealed_pack(tmp_path, store, "2026-10-16", _text("2026-10-16", [_row("MSFT")]))
    sha = hashlib.sha256((poison / FORECASTS_FILE).read_bytes()).hexdigest()
    store.fail_on[sha[:12]] = ValueError("A string literal cannot contain NUL (0x00)")
    res = _ingest(tmp_path, store)
    # the second file was stored normally, the first one as a rejected review
    assert res.ingested == 1 and res.rejected_files == 1
    by_day = {r.session_date: r for r in store.reviews}
    assert by_day[date(2026, 10, 15)].status == "rejected"
    assert "Speichern fehlgeschlagen" in by_day[date(2026, 10, 15)].errors[0]
    assert by_day[date(2026, 10, 15)].file_sha256 == sha
    assert by_day[date(2026, 10, 16)].status == "ok"
    assert any("Speichern fehlgeschlagen" in e for e in res.errors)
    # the hash is known now: the file does not fail every night
    again = _ingest(tmp_path, store)
    assert (again.unchanged, again.errors, again.rejected_files) == (2, [], 0)


def test_when_even_the_rejection_cannot_be_stored_the_run_goes_on(tmp_path: Path):
    class Broken(MemoryStore):
        def add_review(self, review, forecasts):
            if review.session_date == date(2026, 10, 15):
                raise RuntimeError("connection reset")
            return super().add_review(review, forecasts)

    store = Broken()
    _sealed_pack(tmp_path, store, "2026-10-15", _text("2026-10-15", [_row()]))
    _sealed_pack(tmp_path, store, "2026-10-16", _text("2026-10-16", [_row()]))
    res = _ingest(tmp_path, store)
    assert res.ingested == 1 and [r.session_date for r in store.reviews] == [
        date(2026, 10, 16)
    ]
    assert any("Ablehnung nicht speicherbar" in e for e in res.errors)


def test_control_characters_in_text_fields_are_removed_before_storing(tmp_path: Path):
    store = MemoryStore()
    rows = [_row("NVDA", reason="Gut\x00e Gründe\x07", risks="\x1b[31mrot\x7f")]
    _sealed_pack(tmp_path, store, "2026-10-16", _text("2026-10-16", rows))
    res = _ingest(tmp_path, store)
    assert res.ingested == 1 and res.errors == []
    row = store.forecasts[0][1]
    assert row.reason == "Gute Gründe" and row.risks == "[31mrot"


def test_a_version_with_control_characters_rejects_the_file_with_a_clean_message(
    tmp_path: Path,
):
    store = MemoryStore()
    _sealed_pack(
        tmp_path,
        store,
        "2026-10-16",
        _text("2026-10-16", [_row()], version="skill\x00/1"),
    )
    res = _ingest(tmp_path, store)
    assert res.rejected_files == 1 and store.forecasts == []
    assert "Steuerzeichen" in store.reviews[0].errors[0]
    assert all("\x00" not in m for m in store.reviews[0].errors)


def test_error_messages_never_carry_control_characters(tmp_path: Path):
    store = MemoryStore()
    rows = [_row("NV\x00DA"), _row("MSFT", stop=1)]
    _sealed_pack(tmp_path, store, "2026-10-16", _text("2026-10-16", rows))
    res = _ingest(tmp_path, store)
    assert res.rejected_rows == 2
    for msg in [*store.reviews[0].errors, *res.errors]:
        assert "\x00" not in msg
