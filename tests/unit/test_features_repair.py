"""Tests for scripts/repair/features_rebuild.py and migration 030 (no DB)."""

import importlib.util
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rebuild = _load("scripts/repair/features_rebuild.py", "features_rebuild")
mig030 = _load("src/alembic/versions/030_feature_store_integrity.py", "mig030")


class TestArgs:
    def test_dry_run_default(self):
        args = rebuild.parse_args([])
        assert args.apply is False
        assert args.start is None

    def test_options(self):
        args = rebuild.parse_args(
            ["--apply", "--start", "2024-03-01", "--skip-ta", "--commit-every", "3"]
        )
        assert args.apply and args.skip_ta
        assert args.start == date(2024, 3, 1)
        assert args.commit_every == 3


class TestPlan:
    def test_sessions_skip_weekend_and_holiday(self):
        days = rebuild.plan_sessions(date(2026, 7, 1), date(2026, 7, 7))
        assert date(2026, 7, 3) not in days  # Independence Day observed
        assert date(2026, 7, 4) not in days
        assert days[0] == date(2026, 7, 1)
        assert days[-1] == date(2026, 7, 7)

    def test_empty_when_reversed(self):
        assert rebuild.plan_sessions(date(2026, 7, 7), date(2026, 7, 1)) == []

    def test_window_clamped_to_retention(self):
        args = rebuild.parse_args(["--start", "2000-01-01", "--end", "2026-07-07"])
        with patch(
            "trading_signals.utils.retention.data_start_date",
            return_value=date(2021, 7, 1),
        ):
            start, end = rebuild.resolve_window(args)
        assert start == date(2021, 7, 1)
        assert end == date(2026, 7, 7)


class TestRebuildFeatures:
    def test_deletes_day_then_computes_and_commits(self):
        session = MagicMock()
        days = [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 6)]
        with patch(
            "trading_signals.derived.feature_pipeline.FeaturePipeline"
        ) as fp_cls:
            fp_cls.return_value.compute_daily.return_value = 10
            total = rebuild.rebuild_features(session, days, commit_every=2)
        assert total == 30
        assert session.commit.call_count == 2  # after day 2 and the last day
        kinds = [c[0][0].__visit_name__ for c in session.execute.call_args_list]
        assert kinds == ["delete"] * 3


class TestMigration030:
    def test_revision_chain(self):
        assert mig030.revision == "030"
        assert mig030.down_revision == "029"

    def test_upgrade_statements(self):
        executed: list[str] = []
        op = MagicMock()
        op.execute.side_effect = lambda sql: executed.append(str(sql))
        with patch.object(mig030, "op", op):
            mig030.upgrade()
        op.add_column.assert_called_once()
        assert op.add_column.call_args[0][1].name == "feature_version"
        sql = "\n".join(executed)
        assert "ISODOW" in sql
        assert "ticker = 'SPY'" in sql
        assert "return_1d = NULL" in sql
        assert "technical_indicators" in sql and "is_extrapolated IS TRUE" in sql
        assert "new_position" in sql and "closed" in sql

    def test_downgrade_drops_column(self):
        op = MagicMock()
        with patch.object(mig030, "op", op):
            mig030.downgrade()
        op.drop_column.assert_called_once_with(
            "feature_snapshots", "feature_version", schema="signals"
        )
