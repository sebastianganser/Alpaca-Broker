"""Pack folders are made group-writable (setgid) for the forecast VM."""

from pathlib import Path

from trading_signals.derived.context_pack import generator as gen


def test_pack_dir_mode_is_group_writable_with_setgid():
    assert gen.PACK_DIR_MODE == 0o2775


def test_make_group_writable_sets_the_mode(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(Path, "chmod", lambda self, mode: calls.append((self, mode)))
    gen._make_group_writable(tmp_path)
    assert calls == [(tmp_path, 0o2775)]


def test_make_group_writable_never_raises(tmp_path, monkeypatch, caplog):
    def deny(self, mode):
        raise PermissionError("not the owner")

    monkeypatch.setattr(Path, "chmod", deny)
    gen._make_group_writable(tmp_path)  # best effort: logs, does not raise
    assert any("cannot set mode 2775" in r.getMessage() for r in caplog.records)
