"""Tests for the weekly universe validation (job ``universe_validation``).

No DB, no HTTP: session, Alpaca asset validator and corporate-actions client
are mocks; ``get_session`` is patched where the job body opens one.
"""

from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from trading_signals.scheduler import jobs
from trading_signals.scheduler.runner import JobOutcome
from trading_signals.universe import validation
from trading_signals.universe.alpaca_validator import ValidationResult
from trading_signals.universe.corporate_actions import (
    CorporateEvents,
    Merger,
    NameChange,
)
from trading_signals.universe.validation import (
    MAX_DEACTIVATION_SHARE,
    MIN_ASSET_COUNT,
    UniverseValidationError,
    UniverseValidationResult,
    validate_universe,
)
from trading_signals.utils import job_status

TODAY = date(2026, 10, 18)


def _universe(n_other=100, extra=()):
    """Active tickers: ``n_other`` healthy ones plus ``extra``."""
    return [f"OK{i:03d}" for i in range(n_other)] + list(extra)


def _setup(
    *,
    active,
    not_found=(),
    not_tradeable=(),
    n_assets=MIN_ASSET_COUNT + 500,
    open_intervals=(),
    in_universe=(),
    events=None,
    events_error=None,
):
    """Wire a manager, validator, corp-actions client and the two DB lookups."""
    manager = MagicMock()
    manager.get_active_tickers.return_value = [MagicMock(ticker=t) for t in active]

    validator = MagicMock()
    assets = {f"A{i}": object() for i in range(n_assets)}
    validator.fetch_all_assets.return_value = assets
    result = ValidationResult(
        total_checked=len(active),
        not_found=list(not_found),
        not_tradeable=list(not_tradeable),
        details={
            **{t: {"status": "not_found"} for t in not_found},
            **{t: {"status": "not_tradeable"} for t in not_tradeable},
        },
    )
    validator.validate_tickers.return_value = result

    corp = MagicMock()
    if events_error:
        corp.fetch.side_effect = events_error
    else:
        corp.fetch.return_value = events or CorporateEvents()

    patches = [
        patch.object(validation, "UniverseManager", return_value=manager),
        patch.object(
            validation,
            "_open_interval_tickers",
            side_effect=lambda s, tickers: {t for t in tickers if t in open_intervals},
        ),
        patch.object(
            validation,
            "_universe_tickers",
            side_effect=lambda s, tickers: {t for t in tickers if t in in_universe},
        ),
    ]
    return manager, validator, corp, patches


def _run(manager, validator, corp, patches, **kwargs):
    for p in patches:
        p.start()
    try:
        return validate_universe(
            MagicMock(),
            validator=validator,
            corporate_actions=corp,
            today=TODAY,
            **kwargs,
        )
    finally:
        for p in patches:
            p.stop()


class TestDeactivation:
    def test_not_found_and_not_tradable_are_deactivated(self):
        active = _universe(extra=["DELISTED", "HALTED"])
        manager, validator, corp, patches = _setup(
            active=active, not_found=["DELISTED"], not_tradeable=["HALTED"]
        )
        result = _run(manager, validator, corp, patches)
        assert result.checked == len(active)
        assert result.deactivated == ["DELISTED", "HALTED"]
        assert [c.args[0] for c in manager.deactivate_ticker.call_args_list] == [
            "DELISTED",
            "HALTED",
        ]
        assert result.kept_open_interval == [] and result.warnings == []

    def test_open_index_interval_keeps_the_ticker_active_with_a_warning(self, caplog):
        manager, validator, corp, patches = _setup(
            active=_universe(extra=["PSKY", "WBA"]),
            not_found=["PSKY", "WBA"],
            open_intervals={"PSKY"},
        )
        with caplog.at_level("WARNING"):
            result = _run(manager, validator, corp, patches)
        assert result.deactivated == ["WBA"]
        assert result.kept_open_interval == ["PSKY"]
        manager.deactivate_ticker.assert_called_once_with("WBA")
        assert any(
            "PSKY" in w and "open index_membership interval" in w
            for w in result.warnings
        )
        assert any(
            r.levelname == "WARNING" and "PSKY" in r.message for r in caplog.records
        )

    def test_nothing_to_do(self):
        manager, validator, corp, patches = _setup(active=_universe())
        result = _run(manager, validator, corp, patches)
        assert result.deactivated == [] and result.warnings == []
        manager.deactivate_ticker.assert_not_called()
        corp.fetch.assert_not_called()  # no ticker to explain
        assert result.notes() == "checked=100; deactivated=0"

    def test_report_only_mode_changes_nothing(self):
        manager, validator, corp, patches = _setup(
            active=_universe(extra=["DELISTED"]), not_found=["DELISTED"]
        )
        result = _run(manager, validator, corp, patches, deactivate=False)
        assert result.deactivated == ["DELISTED"]  # would be deactivated
        manager.deactivate_ticker.assert_not_called()


class TestSafetyNets:
    def test_tiny_asset_list_aborts_before_any_change(self):
        manager, validator, corp, patches = _setup(
            active=_universe(extra=["X"]), not_found=["X"], n_assets=MIN_ASSET_COUNT - 1
        )
        with pytest.raises(UniverseValidationError, match="response looks broken"):
            _run(manager, validator, corp, patches)
        manager.deactivate_ticker.assert_not_called()

    def test_mass_deactivation_aborts(self):
        bad = [f"BAD{i}" for i in range(20)]
        manager, validator, corp, patches = _setup(
            active=_universe(100, bad), not_found=bad
        )
        assert len(bad) > MAX_DEACTIVATION_SHARE * 120
        with pytest.raises(UniverseValidationError, match="would be deactivated"):
            _run(manager, validator, corp, patches)
        manager.deactivate_ticker.assert_not_called()

    def test_share_at_the_limit_is_allowed(self):
        bad = [f"BAD{i}" for i in range(10)]  # 10 of 100 = exactly 10 %
        manager, validator, corp, patches = _setup(
            active=_universe(90, bad), not_found=bad
        )
        assert _run(manager, validator, corp, patches).deactivated == sorted(bad)


class TestCorporateActionNotes:
    def test_rename_and_merger_lines(self):
        events = CorporateEvents(
            name_changes=[NameChange("SATS", "ECHO", date(2026, 6, 24))],
            mergers=[
                Merger("AVB", "EQR", date(2026, 8, 17), "stock_merger"),
                Merger("EA", None, date(2026, 8, 4), "cash_merger"),
            ],
        )
        manager, validator, corp, patches = _setup(
            active=_universe(extra=["SATS", "AVB", "EA"]),
            not_found=["SATS", "AVB", "EA"],
            in_universe={"ECHO"},
            events=events,
        )
        result = _run(manager, validator, corp, patches)
        assert result.corporate_actions == [
            "AVB->EQR (merger)",
            "EA->CASH (merger)",
            "SATS->ECHO (rename)",
        ]
        assert result.warnings == []  # ECHO is in the universe
        notes = result.notes()
        assert "SATS->ECHO (rename)" in notes and "AVB->EQR (merger)" in notes
        args, kwargs = corp.fetch.call_args
        assert args[0] == ["AVB", "EA", "SATS"]
        assert kwargs == {"start": date(2025, 10, 18), "end": TODAY}  # 365 days

    def test_rename_target_missing_from_universe_is_a_warning(self, caplog):
        events = CorporateEvents(
            name_changes=[NameChange("OLDX", "NEWX", date(2026, 9, 1))]
        )
        manager, validator, corp, patches = _setup(
            active=_universe(extra=["OLDX"]),
            not_found=["OLDX"],
            events=events,
        )
        with caplog.at_level("WARNING"):
            result = _run(manager, validator, corp, patches)
        assert result.deactivated == ["OLDX"]
        assert result.warnings == [
            "OLDX->NEWX (rename): NEWX is not in universe -- the monthly index sync "
            "or the ARK collector normally onboards it"
        ]
        assert "NEWX is not in universe" in caplog.text
        assert "WARN: OLDX->NEWX (rename)" in result.notes()

    def test_kept_ticker_with_open_interval_is_explained_too(self):
        events = CorporateEvents(
            name_changes=[NameChange("PSKY", "SKYD", date(2026, 10, 6))]
        )
        manager, validator, corp, patches = _setup(
            active=_universe(extra=["PSKY"]),
            not_found=["PSKY"],
            open_intervals={"PSKY"},
            in_universe=set(),
            events=events,
        )
        result = _run(manager, validator, corp, patches)
        assert result.deactivated == []
        assert result.corporate_actions == ["PSKY->SKYD (rename)"]
        assert len(result.warnings) == 2  # open interval + SKYD not onboarded

    def test_lookup_failure_does_not_undo_the_deactivation(self, caplog):
        manager, validator, corp, patches = _setup(
            active=_universe(extra=["GONE"]),
            not_found=["GONE"],
            events_error=RuntimeError("alpaca down"),
        )
        with caplog.at_level("WARNING"):
            result = _run(manager, validator, corp, patches)
        assert result.deactivated == ["GONE"]
        manager.deactivate_ticker.assert_called_once_with("GONE")
        assert result.corporate_actions == []
        assert any("corporate actions lookup failed" in w for w in result.warnings)


class TestNotes:
    def test_full_note_line(self):
        r = UniverseValidationResult(
            checked=757,
            deactivated=["A", "B"],
            kept_open_interval=["C"],
            corporate_actions=["A->AA (rename)"],
            warnings=["w1", "w2"],
        )
        assert r.notes() == (
            "checked=757; deactivated=2 (A, B); corporate actions: A->AA (rename); "
            "kept active (open index interval): C; WARN: w1 | w2"
        )


class TestLookups:
    def test_open_interval_query_is_distinct_and_open_only(self):
        session = MagicMock()
        session.execute.return_value.all.return_value = [("PSKY",)]
        assert validation._open_interval_tickers(session, ["PSKY", "X"]) == {"PSKY"}
        sql = str(
            session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
        )
        assert "index_membership.valid_to IS NULL" in sql
        assert "DISTINCT" in sql

    def test_lookups_skip_the_query_for_no_tickers(self):
        session = MagicMock()
        assert validation._open_interval_tickers(session, []) == set()
        assert validation._universe_tickers(session, []) == set()
        session.execute.assert_not_called()

    def test_universe_lookup(self):
        session = MagicMock()
        session.execute.return_value.all.return_value = [("ECHO",)]
        assert validation._universe_tickers(session, ["ECHO", "NEWX"]) == {"ECHO"}


# ── Scheduler job ────────────────────────────────────────────────────────


@contextmanager
def _fake_session(session):
    yield session


class TestJobBody:
    def _body(self, result):
        with (
            patch("trading_signals.db.session.get_session") as get_session,
            patch(
                "trading_signals.universe.validation.validate_universe",
                return_value=result,
            ) as validate,
        ):
            get_session.side_effect = lambda: _fake_session(MagicMock())
            outcome = jobs._universe_validation_body()
        validate.assert_called_once()
        return outcome

    def test_clean_run_is_success(self):
        outcome = self._body(UniverseValidationResult(checked=757, deactivated=["A"]))
        assert isinstance(outcome, JobOutcome)
        assert outcome.status == job_status.SUCCESS
        assert (outcome.records_written, outcome.records_fetched) == (1, 757)
        assert outcome.notes == "checked=757; deactivated=1 (A)"

    def test_warnings_make_it_partial(self):
        outcome = self._body(
            UniverseValidationResult(
                checked=757, kept_open_interval=["PSKY"], warnings=["PSKY ..."]
            )
        )
        assert outcome.status == job_status.PARTIAL
        assert "PSKY" in outcome.notes

    def test_runner_uses_run_logged_job(self):
        with patch.object(jobs, "run_logged_job") as run:
            jobs.run_universe_validation()
        job_id, collector, body = run.call_args.args
        assert (job_id, collector) == ("universe_validation", "universe_validation")
        assert body is jobs._universe_validation_body
