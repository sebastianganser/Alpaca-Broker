"""Shell helpers of the forecast VM (infra/forecast-vm/common.sh), run through bash."""

import os
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

COMMON = Path(__file__).resolve().parents[2] / "infra" / "forecast-vm" / "common.sh"


def _bash(script: str, **env: str) -> subprocess.CompletedProcess:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    return subprocess.run(
        [bash, "-c", f'. "{COMMON.as_posix()}"; {script}'],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        timeout=30,
    )


@pytest.fixture
def pack_root(tmp_path):
    """A pack root that this bash can see (WSL's bash cannot see Windows paths)."""
    root = tmp_path.as_posix()
    if _bash(f'[ -d "{root}" ]').returncode != 0:
        pytest.skip("bash cannot see the temporary directory")
    return tmp_path


def _pack(root: Path, days_ago: int, complete: bool = True) -> str:
    day = (date.today() - timedelta(days=days_ago)).isoformat()
    folder = root / day
    folder.mkdir()
    if complete:
        (folder / "00_uebersicht.md").write_text("x", encoding="utf-8")
    return f"{root.as_posix()}/{day}"


def _latest(root: Path, **env: str) -> subprocess.CompletedProcess:
    return _bash("latest_complete_pack", PACK_ROOT=root.as_posix(), **env)


def test_the_newest_complete_pack_wins(pack_root):
    _pack(pack_root, 5)
    newest = _pack(pack_root, 2)
    result = _latest(pack_root)
    assert result.returncode == 0
    assert result.stdout.strip() == newest


def test_a_folder_without_overview_is_not_complete_yet(pack_root):
    complete = _pack(pack_root, 3)
    _pack(pack_root, 1, complete=False)
    assert _latest(pack_root).stdout.strip() == complete


def test_todays_folder_counts(pack_root):
    _pack(pack_root, 1)
    today = _pack(pack_root, 0)
    assert _latest(pack_root).stdout.strip() == today


def test_a_pack_older_than_the_probe_window_is_still_found_by_listing(pack_root):
    old = _pack(pack_root, 40)
    assert _latest(pack_root).stdout.strip() == old


def test_a_recent_pack_is_found_by_name_when_the_listing_is_stale(pack_root):
    # `set -f` switches globbing off, so the listing finds nothing - like a stale
    # virtiofs listing that misses last night's folder. The lookup by name finds it.
    newest = _pack(pack_root, 1)
    result = _bash("set -f; latest_complete_pack", PACK_ROOT=pack_root.as_posix())
    assert result.returncode == 0
    assert result.stdout.strip() == newest


def test_the_probe_window_ends_where_the_listing_takes_over(pack_root):
    _pack(pack_root, 40)
    result = _bash("set -f; latest_complete_pack", PACK_ROOT=pack_root.as_posix())
    assert result.returncode != 0


def test_no_pack_at_all_returns_non_zero(pack_root):
    result = _latest(pack_root)
    assert result.returncode != 0
    assert result.stdout.strip() == ""


class TestOauthRefreshFailed:
    def _check(self, pack_root, content: str | None) -> int:
        json_file = pack_root / "run.json"
        if content is not None:
            json_file.write_text(content, encoding="utf-8")
        return _bash(f'oauth_refresh_failed "{json_file.as_posix()}"').returncode

    def test_the_refresh_error_is_recognised(self, pack_root):
        body = (
            '{"is_error":true,"num_turns":1,"total_cost_usd":0,"result":"Failed to '
            'refresh OAuth token: another Claude Code process is refreshing it"}'
        )
        assert self._check(pack_root, body) == 0

    def test_another_error_is_not_retried(self, pack_root):
        body = '{"is_error":true,"result":"Budget exceeded"}'
        assert self._check(pack_root, body) != 0

    def test_an_empty_or_missing_result_file_is_not_retried(self, pack_root):
        assert self._check(pack_root, None) != 0
        assert self._check(pack_root, "") != 0
