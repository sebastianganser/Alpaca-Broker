"""Nightly forecast review: state of the pre-registered criterion and the report.

Plan 2026-10-17 (strategy-neutral rework), step 4. ``analysis/forecast_eval.py``
holds the pure evaluation; this module wires it to the database for the
nightly step ``forecast_review`` (``scheduler/jobs.py``):

* :func:`ingest_new_files` - read new ``forecasts.yaml`` files
  (``derived/forecasts.py``);
* :func:`evaluate_and_persist` - load forecasts, gated universe returns, SPY
  and the pack candidates, **freeze** a decisive sample as soon as a forecaster
  version has enough sessions, compute the **verdict once** from the realised
  returns and persist both (``forecast_criterion_samples``), then render the
  German report ``context_packs/forecast_auswertung.md``.

Persistence rules (``docs/FORECASTS.md`` section 6):

* A sample is frozen from the forecasts alone and written once per
  (horizon, forecaster version); an existing row is never touched again.
* The verdict is computed once, from the realised returns of the frozen
  forecasts, and stored together with the per-forecast values it rests on
  (``verdict_values``). A stored verdict is never recomputed from live
  returns - a price correction cannot change it. ``verdict_from_values`` on the
  stored values reproduces it exactly.
* Only the registered 20-session horizon is persisted (:data:`PERSISTED_HORIZONS`);
  the 60-session rule needs its own registration.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd

from trading_signals.analysis import forecast_eval as fe
from trading_signals.analysis.report_format import DASH, ci_cell
from trading_signals.utils.logging import get_logger

logger = get_logger("analysis.forecast_review")

REPORT_FILE = "forecast_auswertung.md"
#: Horizons whose decisive sample / verdict are persisted (registered ones only).
PERSISTED_HORIZONS = (fe.PRIMARY_HORIZON,)
#: Sessions screened per query when the gate of the forecast sessions is rebuilt.
UNIVERSE_CHUNK = 40
#: Reviews listed in the report.
REPORT_REVIEWS = 12
VERDICT_STATUSES = ("passed", "failed")


# ── JSON helpers ─────────────────────────────────────────────────────


def to_jsonable(obj: Any) -> Any:
    """Convert numpy / pandas / date objects to plain JSON types.

    NaN and infinity become ``None`` (PostgreSQL JSONB has no NaN).
    """
    if obj is None or isinstance(obj, bool | str):
        return obj
    if isinstance(obj, np.generic):
        return to_jsonable(obj.item())
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, int):
        return obj
    if isinstance(obj, Decimal):
        return to_jsonable(float(obj))
    if isinstance(obj, pd.Timestamp | datetime):
        return None if pd.isna(obj) else obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Mapping):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, set | frozenset):
        return [to_jsonable(v) for v in sorted(obj)]
    if isinstance(obj, Sequence | np.ndarray | pd.Series | pd.Index):
        return [to_jsonable(v) for v in obj]
    if obj is pd.NaT:
        return None
    raise TypeError(f"not JSON serialisable: {type(obj).__name__}")


_CHECK_NUMBERS = ("estimate", "ci_low", "ci_high")


def revive_verdict(stored: Mapping[str, Any]) -> dict[str, Any]:
    """A stored verdict as :func:`forecast_eval.evaluate_forecasts` expects it.

    JSON has no NaN: the ``None`` of an estimate or interval bound that was NaN
    comes back as NaN. Nothing else is changed.
    """
    out = dict(stored)
    modes: dict[str, Any] = {}
    for mode, info in (stored.get("modes") or {}).items():
        checks = {
            name: {
                k: (float("nan") if k in _CHECK_NUMBERS and v is None else v)
                for k, v in chk.items()
            }
            for name, chk in (info.get("checks") or {}).items()
        }
        modes[mode] = {**info, "checks": checks}
    out["modes"] = modes
    if stored.get("decisive_mode") in modes:
        out["checks"] = modes[stored["decisive_mode"]]["checks"]
    return out


# ── Forecast frame ───────────────────────────────────────────────────


def effective_forecasts(rows: pd.DataFrame) -> pd.DataFrame:
    """The forecasts the evaluation counts: latest usable review per session.

    ``rows`` carries every stored forecast with its ``review_id`` and
    ``review_status``. Forecasts are append-only: a changed file for the same
    session is a new review. The evaluation uses, per session and ``on_time``
    flag, the rows of the latest review whose status is ``ok`` / ``partial``
    (two on-time reviews can only exist if the step ran before the open and
    the file was edited afterwards; the later one wins, never both).
    """
    if rows.empty:
        return rows.copy()
    usable = rows[rows["review_status"].isin(("ok", "partial"))]
    if usable.empty:
        return usable.copy()
    latest = usable.groupby(["session_date", "on_time"])["review_id"].transform("max")
    return usable[usable["review_id"] == latest].reset_index(drop=True)


# ── Criterion state ──────────────────────────────────────────────────


@dataclass
class SampleRecord:
    """A persisted decisive sample (row of ``forecast_criterion_samples``)."""

    sample_key: str
    horizon: int
    forecaster_version: str
    criterion_mode: str
    min_sessions: int
    min_per_side: int
    n_up: int
    n_down: int
    sessions: list[str]
    forecast_ids: list[str]
    frozen_at: datetime | None = None
    verdict_values: list[dict] | None = None
    verdict: dict | None = None
    verdict_at: datetime | None = None

    @property
    def has_verdict(self) -> bool:
        return self.verdict is not None


class CriterionStore(Protocol):
    """Persistence of the frozen samples and verdicts (write once)."""

    def samples(self) -> list[SampleRecord]: ...

    def add_sample(self, record: SampleRecord) -> bool:
        """Insert a frozen sample; ``False`` if (horizon, version) already has one."""

    def set_verdict(
        self, sample_key: str, verdict: dict, values: list[dict], at: datetime
    ) -> bool:
        """Store the verdict of a sample that has none; ``False`` if it has one."""


class SqlCriterionStore:
    """:class:`CriterionStore` on a SQLAlchemy session (the caller commits)."""

    def __init__(self, session) -> None:
        self.session = session

    def samples(self) -> list[SampleRecord]:
        from sqlalchemy import select

        from trading_signals.db.models.forecasts import ForecastCriterionSample as M

        rows = self.session.execute(
            select(M).order_by(M.horizon, M.frozen_at)
        ).scalars()
        return [
            SampleRecord(
                sample_key=r.sample_key,
                horizon=int(r.horizon),
                forecaster_version=r.forecaster_version,
                criterion_mode=r.criterion_mode,
                min_sessions=int(r.min_sessions),
                min_per_side=int(r.min_per_side),
                n_up=int(r.n_up),
                n_down=int(r.n_down),
                sessions=list(r.sessions),
                forecast_ids=[str(i) for i in r.forecast_ids],
                frozen_at=r.frozen_at,
                verdict_values=r.verdict_values,
                verdict=r.verdict,
                verdict_at=r.verdict_at,
            )
            for r in rows
        ]

    def add_sample(self, record: SampleRecord) -> bool:
        from sqlalchemy.dialects.postgresql import insert

        from trading_signals.db.models.forecasts import ForecastCriterionSample as M

        stmt = (
            insert(M)
            .values(
                sample_key=record.sample_key,
                horizon=record.horizon,
                forecaster_version=record.forecaster_version,
                criterion_mode=record.criterion_mode,
                min_sessions=record.min_sessions,
                min_per_side=record.min_per_side,
                n_up=record.n_up,
                n_down=record.n_down,
                sessions=record.sessions,
                forecast_ids=record.forecast_ids,
            )
            .on_conflict_do_nothing()
        )  # PK or (horizon, version): never overwrite
        return bool(self.session.execute(stmt).rowcount)

    def set_verdict(
        self, sample_key: str, verdict: dict, values: list[dict], at: datetime
    ) -> bool:
        from sqlalchemy import update

        from trading_signals.db.models.forecasts import ForecastCriterionSample as M

        stmt = (
            update(M)
            .where(M.sample_key == sample_key, M.verdict.is_(None))
            .values(verdict=verdict, verdict_values=values, verdict_at=at)
        )
        return bool(self.session.execute(stmt).rowcount)


def record_from_frozen(frozen: Mapping[str, Any], mode: str) -> SampleRecord:
    """The persisted form of a frozen sample of :func:`forecast_eval.freeze_sample`."""
    return SampleRecord(
        sample_key=str(frozen["key"]),
        horizon=int(frozen["horizon"]),
        forecaster_version=str(frozen["forecaster_version"]),
        criterion_mode=mode,
        min_sessions=int(frozen["min_dates"]),
        min_per_side=int(frozen["min_per_side"]),
        n_up=int(frozen["up"]),
        n_down=int(frozen["down"]),
        sessions=[str(s) for s in frozen["sessions"]],
        forecast_ids=[str(i) for i in frozen["forecast_ids"]],
    )


def frozen_from_record(rec: SampleRecord) -> dict[str, Any]:
    """Rebuild the ``frozen`` mapping of :func:`forecast_eval.freeze_sample`."""
    sessions = list(rec.sessions)
    return {
        "status": "frozen",
        "horizon": rec.horizon,
        "forecaster_version": rec.forecaster_version,
        "min_dates": rec.min_sessions,
        "min_per_side": rec.min_per_side,
        "n_dates": len(sessions),
        "first_date": date.fromisoformat(sessions[0]),
        "last_date": date.fromisoformat(sessions[-1]),
        "sessions": sessions,
        "forecast_ids": list(rec.forecast_ids),
        "up": rec.n_up,
        "down": rec.n_down,
        "progress": {
            "dates": len(sessions),
            "dates_needed": rec.min_sessions,
            "per_side_needed": rec.min_per_side,
            "up": rec.n_up,
            "down": rec.n_down,
        },
        "key": rec.sample_key,
        "earlier_versions": [],
    }


def values_frame(values: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Stored per-forecast values back as a frame (``None`` -> NaN)."""
    df = pd.DataFrame(list(values))
    for col in df.columns:
        if col in ("forecast_id", fe.DATE_COL, "ticker"):
            continue
        if col == "bench_from_bucket":
            df[col] = df[col].fillna(False).astype(bool)
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def recompute_verdict(rec: SampleRecord) -> dict[str, Any]:
    """The verdict of a sample reproduced from its STORED values (audit, tests)."""
    if rec.verdict is None or rec.verdict_values is None:
        raise ValueError(f"sample {rec.sample_key[:12]} has no stored verdict")
    return fe.verdict_from_values(
        frozen_from_record(rec),
        values_frame(rec.verdict_values),
        rec.criterion_mode,
        n_missing=int(rec.verdict.get("n_missing") or 0),
    )


def sessions_elapsed(last_frozen: date, latest: date | None) -> int:
    """NYSE sessions after ``last_frozen`` up to and including ``latest``."""
    from trading_signals.utils.market_calendar import trading_days

    if latest is None or latest <= last_frozen:
        return 0
    return len(trading_days(last_frozen + timedelta(days=1), latest))


@dataclass
class CriterionUpdate:
    """What one run changed in the persisted criterion state."""

    frozen: list[SampleRecord] = field(default_factory=list)
    verdicts: list[tuple[SampleRecord, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def notes(self) -> str:
        parts = [
            f"frozen {r.horizon}d {r.forecaster_version} ({r.sample_key[:8]})"
            for r in self.frozen
        ]
        parts += [
            f"verdict {r.horizon}d {r.forecaster_version}: {status}"
            for r, status in self.verdicts
        ]
        parts += self.problems
        return "; ".join(parts)


def update_criterion(
    forecasts: pd.DataFrame,
    all_forecasts: pd.DataFrame,
    universe_returns: pd.DataFrame,
    spy_returns: pd.DataFrame | None,
    pack_candidates: pd.DataFrame | None,
    store: CriterionStore,
    latest_session: date | None,
    now: datetime,
    horizons: Sequence[int] = PERSISTED_HORIZONS,
    mode: str = fe.DEFAULT_CRITERION_MODE,
    min_dates: int | None = None,
    min_per_side: int = fe.CRITERION_MIN_PER_SIDE,
    grace_sessions: int = fe.CRITERION_GRACE_SESSIONS,
) -> CriterionUpdate:
    """Freeze new samples and compute due verdicts - each written ONCE.

    Args:
        forecasts: The effective forecasts (:func:`effective_forecasts`), the
            basis of the freeze.
        all_forecasts: Every stored forecast (incl. superseded reviews): a
            frozen sample is looked up here by forecast id, so a later
            review can never hide a forecast of an existing sample.
        latest_session: Newest session with feature snapshots (clock of the
            grace period).
        min_dates, min_per_side: Only tests pass smaller sizes than the
            registered ones.

    A (horizon, version) that already has a persisted sample is not frozen
    again; a sample with a stored verdict is never touched. Only
    ``passed`` / ``failed`` verdicts are written; ``awaiting_returns`` waits for
    the next night.
    """
    update = CriterionUpdate()
    eff = fe.prepare_forecasts(forecasts) if len(forecasts) else forecasts
    persisted = {(s.horizon, s.forecaster_version): s for s in store.samples()}

    for h in horizons:
        if eff.empty:
            break
        directional = eff[
            (eff["horizon_days"] == h) & (eff["sigma"] != 0) & eff["on_time"]
        ]
        for version in sorted(set(directional["forecaster_version"])):
            if (h, version) in persisted:
                continue
            frozen = fe.freeze_sample(eff, h, version, min_dates, min_per_side)
            if frozen["status"] != "frozen":
                continue
            rec = record_from_frozen(frozen, mode)
            if store.add_sample(rec):
                persisted[(h, version)] = rec
                update.frozen.append(rec)
                logger.info(
                    f"frozen sample {h}d {version}: {len(rec.sessions)} sessions, "
                    f"{rec.n_up} up / {rec.n_down} down, key {rec.sample_key[:12]}"
                )
            else:  # lost a race with another writer: that row stays authoritative
                update.problems.append(f"sample {h}d {version} already stored")

    open_samples = [s for s in persisted.values() if not s.has_verdict]
    if not open_samples or all_forecasts.empty:
        return update
    joined = fe.attach_outcomes(
        fe.prepare_forecasts(all_forecasts),
        universe_returns,
        spy_returns,
        pack_candidates,
    )
    for rec in open_samples:
        frozen = frozen_from_record(rec)
        if fe.sample_key(frozen) != rec.sample_key:
            update.problems.append(
                f"sample {rec.sample_key[:8]}: key does not match its sessions/ids - "
                "no verdict computed"
            )
            logger.error(f"sample key mismatch for {rec.sample_key}")
            continue
        elapsed = sessions_elapsed(frozen["last_date"], latest_session)
        verdict = fe.verdict_from_frozen(
            frozen, joined, elapsed, grace_sessions, rec.criterion_mode
        )
        if verdict["status"] not in VERDICT_STATUSES:
            continue  # awaiting_returns / not_available: try again tomorrow
        values = to_jsonable(verdict.pop("values"))
        stored = to_jsonable(verdict)
        if store.set_verdict(rec.sample_key, stored, values, now):
            rec.verdict, rec.verdict_values, rec.verdict_at = stored, values, now
            update.verdicts.append((rec, verdict["status"]))
            logger.info(
                f"verdict {rec.horizon}d {rec.forecaster_version}: {verdict['status']} "
                f"(key {rec.sample_key[:12]}, {verdict.get('n_missing', 0)} missing)"
            )
        else:
            update.problems.append(f"verdict of {rec.sample_key[:8]} already stored")
    return update


def criterion_state(
    forecasts: pd.DataFrame,
    samples: Sequence[SampleRecord],
    horizons: Sequence[int] = PERSISTED_HORIZONS,
) -> tuple[dict[int, dict], dict[int, dict]]:
    """``(frozen, verdicts)`` per horizon for :func:`forecast_eval.evaluate_forecasts`.

    The sample of the ACTIVE forecaster version (the one of the newest on-time
    directional forecast) is handed to the evaluation; with a stored verdict
    the evaluation reports it as it is.
    """
    by_key = {(s.horizon, s.forecaster_version): s for s in samples}
    frozen_map: dict[int, dict] = {}
    verdict_map: dict[int, dict] = {}
    if forecasts.empty:
        return frozen_map, verdict_map
    for h in horizons:
        live = fe.freeze_sample(forecasts, h)
        rec = by_key.get((h, live["forecaster_version"]))
        if rec is None:
            continue
        frozen = frozen_from_record(rec)
        frozen["earlier_versions"] = live.get("earlier_versions", [])
        frozen_map[h] = frozen
        if rec.verdict is not None:
            verdict = revive_verdict(rec.verdict)
            verdict["earlier_versions"] = frozen["earlier_versions"]
            verdict_map[h] = verdict
    return frozen_map, verdict_map


# ── Report ───────────────────────────────────────────────────────────


def _day(x: Any) -> str:
    return DASH if x is None else f"{pd.Timestamp(x):%Y-%m-%d}"


def render_ingest_section(
    ingest: Any | None, reviews: Sequence[Mapping[str, Any]]
) -> list[str]:
    """German section on the files read (this run and the latest reviews)."""
    lines = ["## Eingelesene Dateien", ""]
    if ingest is not None:
        lines += [
            f"Dieser Lauf: Dateien gefunden {ingest.files}, neu eingelesen "
            f"{ingest.ingested}, unverändert {ingest.unchanged} · Prognosen "
            f"{ingest.forecasts} (davon verspätet {ingest.late}) · abgelehnt: "
            f"Dateien {ingest.rejected_files}, Zeilen {ingest.rejected_rows}.",
            "",
        ]
    if not reviews:
        return lines if ingest is not None else []
    lines += [
        "| Sitzung | Version | Status | Prognosen | abgelehnt | rechtzeitig |",
        "|---|---|---|---|---|---|",
    ]
    for r in reviews:
        timely = (
            DASH
            if r["status"] == "rejected"
            else "ja"
            if r["on_time"]
            else "nein (verspätet)"
        )
        lines.append(
            f"| {_day(r['session_date'])} | `{r.get('forecaster_version') or DASH}` | "
            f"{r['status']} | {r['n_forecasts']} | {r['n_rejected']} | {timely} |"
        )
    problems = [r for r in reviews if r.get("errors")]
    if problems:
        lines += ["", "Fehler in eingelesenen Dateien (erste je Datei):", ""]
        for r in problems[:10]:
            lines.append(
                f"- {_day(r['session_date'])} ({r['status']}): "
                f"{'; '.join(map(str, r['errors'][:3]))}"
            )
    lines.append("")
    return lines


def render_samples_section(samples: Sequence[SampleRecord]) -> list[str]:
    """German section on the persisted samples and their verdicts."""
    if not samples:
        return []
    lines = [
        "## Gespeicherte Stichproben und Urteile (einmalig geschrieben)",
        "",
        "Eine eingefrorene Stichprobe und ihr Urteil werden nur einmal in die "
        "Datenbank geschrieben und nie aus aktuellen Renditen neu berechnet; das "
        "Urteil steht mit den Einzelwerten, aus denen es berechnet wurde "
        "(`forecast_criterion_samples`).",
        "",
        "| Horizont | Version | Schlüssel | Sitzungen | hoch / runter | Urteil | "
        "relativ: Hoch minus Runter | Auswahl-Trefferquote |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in samples:
        verdict = revive_verdict(s.verdict) if s.verdict else None
        checks = (verdict or {}).get("checks") or {}
        rel = checks.get("relative_up_minus_down")
        sel = checks.get("selection_hit_edge")
        state = DASH
        if verdict is not None:
            state = {"passed": "bestanden", "failed": "nicht bestanden"}.get(
                verdict["status"], verdict["status"]
            )
        else:
            state = "Urteil ausstehend (Fenster noch offen)"

        def cell(chk: Mapping[str, Any] | None) -> str:
            if not chk:
                return DASH
            return ci_cell(chk["estimate"], chk["ci_low"], chk["ci_high"], mark=False)

        lines.append(
            f"| {s.horizon} Tage | `{s.forecaster_version}` | `{s.sample_key[:12]}` | "
            f"{len(s.sessions)} ({s.sessions[0]} … {s.sessions[-1]}) | "
            f"{s.n_up} / {s.n_down} | {state} | {cell(rel)} | {cell(sel)} |"
        )
    lines.append("")
    return lines


def build_report(
    result: dict[str, Any],
    samples: Sequence[SampleRecord],
    reviews: Sequence[Mapping[str, Any]],
    ingest: Any | None,
    today: date,
) -> str:
    """The German report ``forecast_auswertung.md``."""
    extra = [render_ingest_section(ingest, reviews), render_samples_section(samples)]
    return fe.render_report(
        result, today, extra_sections=["\n".join(s) for s in extra if s]
    )


# ── Loading (DB, read-only) ──────────────────────────────────────────


def load_forecast_rows(session) -> pd.DataFrame:
    """Every stored forecast with its review (one row per forecast)."""
    from sqlalchemy import text

    df = pd.read_sql(
        text("""
        SELECT f.id::text AS forecast_id, f.review_id, r.status AS review_status,
               f.session_date, f.ticker, f.horizon_days, f.direction,
               f.confidence::float8 AS confidence, f.conviction,
               f.expected_pct::float8 AS expected_pct, f.drivers,
               f.forecaster_version, f.on_time
        FROM signals.forecasts f
        JOIN signals.forecast_reviews r ON r.id = f.review_id
        ORDER BY f.session_date, f.id
    """),
        session.connection(),
    )
    df["session_date"] = pd.to_datetime(df["session_date"])
    df["on_time"] = df["on_time"].astype(bool)
    return df


def load_pack_candidates(session) -> pd.DataFrame:
    """The screen buckets of the stored context packs."""
    from sqlalchemy import text

    df = pd.read_sql(
        text("SELECT session_date, ticker, bucket FROM signals.pack_candidates"),
        session.connection(),
    )
    df["session_date"] = pd.to_datetime(df["session_date"])
    return df


def load_review_overview(session, limit: int = REPORT_REVIEWS) -> list[dict[str, Any]]:
    """The latest reviews plus every review with errors (for the report)."""
    from sqlalchemy import text

    df = pd.read_sql(
        text("""
        SELECT session_date, forecaster_version, status, n_forecasts, n_rejected,
               on_time, errors, id
        FROM signals.forecast_reviews
        ORDER BY session_date DESC, id DESC
        LIMIT :limit
    """),
        session.connection(),
        params={"limit": limit},
    )
    rows = df.to_dict("records")
    for r in rows:
        r["errors"] = r["errors"] if isinstance(r["errors"], list) else []
    return rows


def load_latest_session(session) -> date | None:
    """Newest session with feature snapshots."""
    from sqlalchemy import text

    value = session.execute(
        text("SELECT max(snapshot_date) FROM signals.feature_snapshots")
    ).scalar()
    return value


def screen_columns() -> list[str]:
    """``feature_snapshots`` columns the quality gate reads (scored features + gate)."""
    from trading_signals.analysis.screen import (
        COL_DOLLAR_VOLUME,
        COL_HISTORY_PROXY,
        DEFAULT_CONFIG,
        scored_features,
    )
    from trading_signals.db.models.features import FEATURE_COLUMNS

    wanted = dict.fromkeys(
        [*scored_features(DEFAULT_CONFIG.groups), COL_DOLLAR_VOLUME, COL_HISTORY_PROXY]
    )
    return [c for c in wanted if c in FEATURE_COLUMNS]


def gate_flags(frame: pd.DataFrame, config: Any = None) -> pd.Series:
    """``gate_ok`` of ONE cross-section, exactly as the screen computes it.

    The same building blocks as ``screen_cross_section`` (feature values,
    groups with data, :func:`~trading_signals.analysis.screen.quality_gate`)
    without the percentile ranks and buckets, which the evaluation does not
    need: rebuilding the gate of every forecast session each night stays cheap.
    ``tests/unit/test_forecast_review.py`` pins the parity with the screen.
    """
    from trading_signals.analysis.screen import (
        DEFAULT_CONFIG,
        feature_values,
        group_min_features,
        quality_gate,
    )

    config = config or DEFAULT_CONFIG
    frame = frame.reset_index(drop=True)
    has_data = pd.DataFrame(
        {
            g.name: sum(
                feature_values(frame, s).notna().astype(int) for s in g.features
            )
            >= group_min_features(g, config.min_feature_fraction)
            for g in config.groups
        },
        index=frame.index,
    )
    return quality_gate(frame, has_data, config)[0]


def load_universe_returns(
    session, sessions: Sequence[Any], chunk: int = UNIVERSE_CHUNK
) -> pd.DataFrame:
    """Realised returns of every snapshot row of ``sessions`` with its gate flag.

    ``gate_ok`` is the quality gate of ``analysis/screen.py`` (the population of
    ``screen.csv``, :func:`gate_flags`) applied to the feature snapshots of each
    session; the exact price-history count is not stored, so the documented
    proxy (``price_vs_sma50`` not NULL) is used.
    Columns: ``snapshot_date``, ``ticker``, ``gate_ok``, ``return_5d`` /
    ``return_20d`` / ``return_60d``.
    """
    from sqlalchemy import Boolean, text

    from trading_signals.db.models.features import FeatureSnapshot

    returns = [f"return_{h}d" for h in fe.HORIZONS]
    empty = pd.DataFrame(columns=["snapshot_date", "ticker", "gate_ok", *returns])
    days = sorted({pd.Timestamp(d).date() for d in sessions})
    if not days:
        return empty
    table = FeatureSnapshot.__table__
    exprs = []
    for c in screen_columns():
        cast = "::int::float8" if isinstance(table.c[c].type, Boolean) else "::float8"
        exprs.append(f"{c}{cast} AS {c}")
    exprs += [f"{c}::float8 AS {c}" for c in returns]
    sql = text(
        "SELECT snapshot_date, ticker, "
        + ", ".join(exprs)
        + " FROM signals.feature_snapshots WHERE snapshot_date = ANY(:days)"
    )
    parts = []
    conn = session.connection()
    for i in range(0, len(days), chunk):
        feats = pd.read_sql(sql, conn, params={"days": days[i : i + chunk]})
        if feats.empty:
            continue
        feats["snapshot_date"] = pd.to_datetime(feats["snapshot_date"])
        for _, day in feats.groupby("snapshot_date", sort=True):
            part = day[["snapshot_date", "ticker", *returns]].reset_index(drop=True)
            part["gate_ok"] = gate_flags(day).to_numpy()
            parts.append(part[["snapshot_date", "ticker", "gate_ok", *returns]])
    return pd.concat(parts, ignore_index=True) if parts else empty


def load_long_run_shares(
    session, start: date, min_dollar_volume: float
) -> dict[int, tuple[float, float]]:
    """Long-run share of positive / negative ``h``-day returns per horizon.

    Over the whole retention window (``start`` on), for the liquid snapshot
    rows (``dollar_volume_20d`` at the gate minimum): the reference of the
    descriptive absolute block, free of the market path of the evaluated
    months. The full gate is not rebuilt for the whole window; the liquidity
    condition is its main part.
    """
    from sqlalchemy import text

    out: dict[int, tuple[float, float]] = {}
    for h in fe.HORIZONS:
        row = session.execute(
            text(
                f"SELECT avg((return_{h}d > 0)::int::float8), "
                f"avg((return_{h}d < 0)::int::float8) FROM signals.feature_snapshots "
                f"WHERE snapshot_date >= :start AND return_{h}d IS NOT NULL "
                "AND dollar_volume_20d >= :minvol"
            ),
            {"start": start, "minvol": min_dollar_volume},
        ).one()
        pos, neg = row
        out[h] = (
            float(pos) if pos is not None else float("nan"),
            float(neg) if neg is not None else float("nan"),
        )
    return out


# ── Nightly step ─────────────────────────────────────────────────────


def ingest_new_files(root: Path) -> Any:
    """Phase 1: read new ``forecasts.yaml`` files (own transaction, committed)."""
    from trading_signals.db.session import get_session
    from trading_signals.derived.forecasts import SqlForecastStore, ingest_forecasts

    with get_session() as session:
        return ingest_forecasts(SqlForecastStore(session), root)


@dataclass
class ReviewResult:
    """Outcome of :func:`evaluate_and_persist`."""

    report: str
    update: CriterionUpdate
    n_forecasts: int = 0


def evaluate_and_persist(
    ingest: Any | None, today: date, now: datetime
) -> ReviewResult:
    """Phase 2: freeze / verdict (written once) and the report (own transaction).

    Read-only apart from the rows of ``forecast_criterion_samples``.
    """
    from trading_signals.analysis.benchmark import load_benchmark_returns
    from trading_signals.analysis.screen import MIN_DOLLAR_VOLUME_20D
    from trading_signals.db.session import get_session
    from trading_signals.utils.retention import ml_start_date

    with get_session() as session:
        rows = load_forecast_rows(session)
        reviews = load_review_overview(session)
        store = SqlCriterionStore(session)
        if rows.empty:  # nothing ingested yet: only the file overview
            report = build_report(
                {"n_forecasts": 0}, store.samples(), reviews, ingest, today
            )
            return ReviewResult(report, CriterionUpdate(), 0)

        eff = effective_forecasts(rows)
        sessions = sorted(rows["session_date"].unique())
        universe = load_universe_returns(session, sessions)
        spy = load_benchmark_returns(session, pd.Timestamp(sessions[0]).date())
        spy = spy if len(spy) else None
        pack = load_pack_candidates(session)
        pack = pack if len(pack) else None
        long_run = load_long_run_shares(session, ml_start_date(), MIN_DOLLAR_VOLUME_20D)
        latest = load_latest_session(session)

        update = update_criterion(eff, rows, universe, spy, pack, store, latest, now)
        # the freeze / verdict are committed before anything else can fail: they
        # are written once and must not be rolled back and recomputed later
        session.commit()
        samples = store.samples()
        frozen_map, verdict_map = criterion_state(eff, samples)
        result = fe.evaluate_forecasts(
            eff,
            universe,
            spy,
            pack,
            frozen=frozen_map,
            verdicts=verdict_map,
            long_run=long_run,
        )
        report = build_report(result, samples, reviews, ingest, today)
        return ReviewResult(report, update, int(len(eff)))


__all__ = [
    "PERSISTED_HORIZONS",
    "REPORT_FILE",
    "CriterionStore",
    "CriterionUpdate",
    "ReviewResult",
    "SampleRecord",
    "SqlCriterionStore",
    "build_report",
    "criterion_state",
    "effective_forecasts",
    "evaluate_and_persist",
    "frozen_from_record",
    "gate_flags",
    "ingest_new_files",
    "record_from_frozen",
    "recompute_verdict",
    "revive_verdict",
    "to_jsonable",
    "update_criterion",
    "values_frame",
]
