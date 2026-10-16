"""Tests for the shared helpers of the repair scripts (no DB, no HTTP)."""

import csv
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.utils import repair_support as rs

NOW = datetime(2026, 10, 16, 12, 30, 5)


class TestWriteCsvBackup:
    def test_writes_header_rows_and_iso_dates(self, tmp_path):
        rows = [
            {
                "id": 1,
                "ticker": "TAP",
                "valid_from": date(2021, 1, 1),
                "valid_to": None,
            },
            {
                "id": 2,
                "ticker": "KSU",
                "valid_from": date(2021, 1, 1),
                "valid_to": date(2021, 12, 14),
            },
        ]
        path = rs.write_csv_backup(
            rows, ["id", "ticker", "valid_from", "valid_to"], tmp_path, "bk", now=NOW
        )
        assert path.name == "bk_20261016_123005.csv"
        with path.open(newline="", encoding="utf-8") as fh:
            data = list(csv.reader(fh))
        assert data[0] == ["id", "ticker", "valid_from", "valid_to"]
        assert data[1] == ["1", "TAP", "2021-01-01", ""]  # None -> empty cell
        assert data[2] == ["2", "KSU", "2021-01-01", "2021-12-14"]
        assert not list(tmp_path.glob("*.tmp"))

    def test_creates_missing_directory(self, tmp_path):
        target = tmp_path / "ops" / "nested"
        path = rs.write_csv_backup([], ["a"], target, "bk", now=NOW)
        assert path.exists() and path.parent == target

    def test_existing_name_is_not_overwritten(self, tmp_path):
        first = rs.write_csv_backup([{"a": 1}], ["a"], tmp_path, "bk", now=NOW)
        second = rs.write_csv_backup([{"a": 2}], ["a"], tmp_path, "bk", now=NOW)
        assert first != second
        assert first.read_text(encoding="utf-8") != second.read_text(encoding="utf-8")

    def test_roundtrip(self, tmp_path):
        rows = [{"ticker": "PSKY", "valid_from": date(2021, 1, 1), "valid_to": None}]
        path = rs.write_csv_backup(
            rows, ["ticker", "valid_from", "valid_to"], tmp_path, "bk", now=NOW
        )
        assert rs.read_csv_backup(path) == [
            {"ticker": "PSKY", "valid_from": "2021-01-01", "valid_to": ""}
        ]

    def test_unwritable_target_raises_backup_error(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        with pytest.raises(rs.BackupError, match="--backup-dir"):
            rs.write_csv_backup([{"a": 1}], ["a"], blocker / "sub", "bk", now=NOW)

    def test_verification_failure_leaves_no_final_file(self, tmp_path):
        real_reader = csv.reader

        def short_reader(fh, *a, **kw):  # drops the last line
            return iter(list(real_reader(fh, *a, **kw))[:-1])

        with patch.object(rs.csv, "reader", side_effect=short_reader):
            with pytest.raises(rs.BackupError, match="verification"):
                rs.write_csv_backup(
                    [{"a": 1}, {"a": 2}], ["a"], tmp_path, "bk", now=NOW
                )
        assert not list(tmp_path.iterdir())

    def test_missing_backup_file_raises(self, tmp_path):
        with pytest.raises(rs.BackupError):
            rs.read_csv_backup(tmp_path / "nope.csv")


class TestDbTarget:
    def test_target_names_host_port_db_but_no_password(self):
        settings = MagicMock(
            DB_HOST="127.0.0.1",
            DB_PORT=55441,
            DB_NAME="broker_data",
            DB_USER="migtest",
            DB_PASSWORD="secret-pw",
        )
        with patch("trading_signals.config.get_settings", return_value=settings):
            target = rs.db_target()
        assert target == "127.0.0.1:55441/broker_data (user migtest)"
        assert "secret-pw" not in target

    def test_announce_prints_target(self, capsys):
        with patch.object(rs, "db_target", return_value="h:1/db (user u)"):
            rs.announce_db_target("myscript")
        assert "[myscript] database target: h:1/db (user u)" in capsys.readouterr().out


class TestRepairSession:
    @staticmethod
    def _factory(session):
        return MagicMock(return_value=MagicMock(return_value=session))

    def test_read_only_session_declares_read_only_first_and_rolls_back(self):
        session = MagicMock()
        with patch(
            "trading_signals.db.session.get_session_factory",
            self._factory(session),
        ):
            with rs.repair_session(read_only=True) as s:
                assert s is session
        first = session.execute.call_args_list[0].args[0]
        assert str(first) == "SET TRANSACTION READ ONLY"
        session.commit.assert_not_called()
        session.rollback.assert_called()
        session.close.assert_called_once()

    def test_write_session_does_not_set_read_only_and_never_autocommits(self):
        session = MagicMock()
        with patch(
            "trading_signals.db.session.get_session_factory",
            self._factory(session),
        ):
            with rs.repair_session(read_only=False):
                pass
        session.execute.assert_not_called()
        session.commit.assert_not_called()  # the caller commits per logical step
        session.rollback.assert_called()

    def test_exception_rolls_back_and_closes(self):
        session = MagicMock()
        with patch(
            "trading_signals.db.session.get_session_factory",
            self._factory(session),
        ):
            with pytest.raises(ValueError):
                with rs.repair_session(read_only=False):
                    raise ValueError("boom")
        session.rollback.assert_called()
        session.close.assert_called_once()


def test_out_prints_flushed(capsys):
    rs.out("hello")
    assert capsys.readouterr().out == "hello\n"
