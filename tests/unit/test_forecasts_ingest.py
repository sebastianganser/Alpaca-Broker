"""Unit tests for ``ingest_forecasts`` (derived/forecasts.py, in-memory store)."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

from trading_signals.derived.forecasts import (
    FORECASTS_FILE,
    ForecastRecord,
    ReviewRecord,
    ingest_forecasts,
)

FAR_FUTURE = datetime(2100, 1, 1, tzinfo=UTC)  # a file written now is on time
LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)  # a file written now is late


class MemoryStore:
    """The part of the database the ingest touches."""

    def __init__(self, tickers: dict[date, set[str]] | None = None) -> None:
        self.reviews: list[ReviewRecord] = []
        self.forecasts: list[tuple[int, ForecastRecord]] = []
        self.tickers = tickers or {}

    def known_hashes(self) -> set[str]:
        return {r.file_sha256 for r in self.reviews}

    def session_tickers(self, session_date: date) -> set[str]:
        return self.tickers.get(session_date, set())

    def add_review(self, review, forecasts) -> int:
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


def _ingest(root: Path, store: MemoryStore, entry_open=FAR_FUTURE):
    return ingest_forecasts(store, root, open_fn=lambda d: entry_open)


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
    _pack(
        tmp_path,
        "2026-10-16",
        _text(
            "2026-10-16",
            [
                _row("NVDA", 60, expected_pct=4.0, drivers=["revisions"]),
                _row("MSFT", 20, "neutral", confidence=None, conviction=None),
            ],
        ),
    )
    store = MemoryStore()
    open_ = datetime(2026, 10, 19, 13, 30, tzinfo=UTC)
    res = _ingest(tmp_path, store, entry_open=open_)
    assert (res.files, res.ingested, res.unchanged, res.forecasts) == (1, 1, 0, 2)
    review = store.reviews[0]
    assert review.session_date == date(2026, 10, 16) and review.on_time
    assert (review.status, review.n_forecasts, review.n_rejected) == ("ok", 2, 0)
    assert (
        review.schema == "forecasts/v1" and review.forecaster_version == "skill/1+opus"
    )
    assert len(review.file_sha256) == 64 and review.errors == []
    nvda, msft = (f for _, f in store.forecasts)
    assert nvda.valid_from == open_ and nvda.on_time and nvda.drivers == ["revisions"]
    assert nvda.forecaster_version == "skill/1+opus"
    assert nvda.decided_at == datetime(2026, 10, 16, 20, 0, tzinfo=UTC)
    assert (msft.direction, msft.confidence, msft.conviction) == ("neutral", None, None)


def test_same_file_twice_is_idempotent(tmp_path: Path):
    _pack(tmp_path, "2026-10-16", _text("2026-10-16", [_row()]))
    store = MemoryStore()
    _ingest(tmp_path, store)
    again = _ingest(tmp_path, store)
    assert (again.files, again.ingested, again.unchanged) == (1, 0, 1)
    assert len(store.reviews) == 1 and len(store.forecasts) == 1


def test_changed_file_is_a_new_review_and_old_rows_stay(tmp_path: Path):
    d = _pack(tmp_path, "2026-10-16", _text("2026-10-16", [_row("NVDA")]))
    store = MemoryStore()
    _ingest(tmp_path, store)
    (d / FORECASTS_FILE).write_text(
        _text("2026-10-16", [_row("NVDA"), _row("AAPL")]), encoding="utf-8"
    )
    res = _ingest(tmp_path, store)
    assert res.ingested == 1 and len(store.reviews) == 2
    # append-only: the first review keeps its row, the second adds its own two
    assert [rid for rid, _ in store.forecasts] == [1, 2, 2]


def test_a_file_written_after_the_open_is_late_but_stored(tmp_path: Path):
    _pack(tmp_path, "2026-10-16", _text("2026-10-16", [_row("NVDA"), _row("AAPL")]))
    store = MemoryStore()
    res = _ingest(tmp_path, store, entry_open=LONG_AGO)
    assert res.ingested == 1 and res.late == 2 and res.forecasts == 2
    assert not store.reviews[0].on_time
    assert not any(f.on_time for _, f in store.forecasts)


def test_a_decision_after_the_open_is_late_even_for_an_early_file(tmp_path: Path):
    d = _pack(tmp_path, "2026-10-16", _text("2026-10-16", [_row()]))
    os.utime(d / FORECASTS_FILE, (0, 0))  # last write long before the open
    store = MemoryStore()
    open_ = datetime(2026, 10, 16, 18, 0, tzinfo=UTC)  # decided_at is 20:00
    _ingest(tmp_path, store, entry_open=open_)
    assert not store.reviews[0].on_time
    # an early decision and an early write: on time
    (d / FORECASTS_FILE).write_text(
        _text("2026-10-16", [_row()]).replace("T20:00:00", "T10:00:00"),
        encoding="utf-8",
    )
    os.utime(d / FORECASTS_FILE, (0, 0))
    _ingest(tmp_path, store, entry_open=open_)
    assert store.reviews[-1].on_time


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


def test_bad_rows_are_dropped_and_reported_the_good_ones_are_kept(tmp_path: Path):
    _pack(
        tmp_path,
        "2026-10-16",
        _text(
            "2026-10-16", [_row("NVDA"), _row("MSFT", stop=5), _row("AAPL", horizon=7)]
        ),
    )
    store = MemoryStore()
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


def test_without_snapshots_of_the_day_no_ticker_judgement_is_made(tmp_path: Path):
    _pack(tmp_path, "2026-10-16", _text("2026-10-16", [_row("NVDA")]))
    store = MemoryStore()  # no snapshots known for the session
    _ingest(tmp_path, store)
    assert [f.ticker for _, f in store.forecasts] == ["NVDA"]


def test_utf8_bom_files_are_read(tmp_path: Path):
    d = _pack(tmp_path, "2026-10-16", None)
    (d / FORECASTS_FILE).write_bytes(
        b"\xef\xbb\xbf" + _text("2026-10-16", [_row()]).encode("utf-8")
    )
    store = MemoryStore()
    assert _ingest(tmp_path, store).ingested == 1
