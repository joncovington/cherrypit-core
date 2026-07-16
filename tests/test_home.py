"""Tests for cherrypick.core.home — the single per-user home resolver.

Pins the override precedence (CHERRYPICK_HOME master → per-scope env → default) and the on-disk layout,
so the four packages that delegate here can't drift back to package-local paths.
"""

from pathlib import Path

import pytest

from cherrypick.core import home


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start each test from a known env: no cherrypick overrides set, home() pinned to a fake home."""
    for var in ("CHERRYPICK_HOME", "CHERRYPICK_MODULES_HOME", "MEIC_DATA_DIR", "MEIC_LOGS_DIR"):
        monkeypatch.delenv(var, raising=False)
    fake = Path("/fake/user")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake))
    # os.path.expanduser("~") reads $HOME/$USERPROFILE, not Path.home(); pin them so `~` in an
    # override expands to the same fake home on both POSIX and Windows.
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))
    return fake


def test_default_home_is_dotcherrypick_under_user_home(_clean_env):
    assert home.home() == _clean_env / ".cherrypick"


def test_cherrypick_home_is_master_override(monkeypatch, _clean_env):
    monkeypatch.setenv("CHERRYPICK_HOME", "/opt/cp")
    assert home.home() == Path("/opt/cp")
    # It relocates the whole tree uniformly, not just one scope.
    assert home.data_dir("meic") == Path("/opt/cp/data/meic")
    assert home.logs_dir("earnings") == Path("/opt/cp/logs/earnings")
    assert home.state_dir() == Path("/opt/cp/state")
    assert home.config_path() == Path("/opt/cp/config.json")


def test_data_and_logs_default_under_home(_clean_env):
    base = _clean_env / ".cherrypick"
    assert home.data_dir() == base / "data"
    assert home.data_dir("meic") == base / "data" / "meic"
    assert home.logs_dir() == base / "logs"
    assert home.logs_dir("gex") == base / "logs" / "gex"


def test_per_scope_env_wins_over_home_default(monkeypatch, _clean_env):
    monkeypatch.setenv("MEIC_DATA_DIR", "/tmp/meic-data")
    monkeypatch.setenv("MEIC_LOGS_DIR", "/tmp/meic-logs")
    assert home.data_dir("meic", env="MEIC_DATA_DIR") == Path("/tmp/meic-data")
    assert home.logs_dir("meic", env="MEIC_LOGS_DIR") == Path("/tmp/meic-logs")
    # A different package with no override still falls through to the home default.
    earnings_default = _clean_env / ".cherrypick" / "data" / "earnings"
    assert home.data_dir("earnings", env="EARNINGS_DATA_DIR") == earnings_default


def test_per_scope_env_beats_cherrypick_home(monkeypatch, _clean_env):
    monkeypatch.setenv("CHERRYPICK_HOME", "/opt/cp")
    monkeypatch.setenv("MEIC_DATA_DIR", "/tmp/override")
    assert home.data_dir("meic", env="MEIC_DATA_DIR") == Path("/tmp/override")


def test_env_values_expand_user_and_vars(monkeypatch, _clean_env):
    monkeypatch.setenv("CP_DEST", "/mnt/vol")
    monkeypatch.setenv("MEIC_DATA_DIR", "$CP_DEST/meic")
    assert home.data_dir("meic", env="MEIC_DATA_DIR") == Path("/mnt/vol/meic")
    monkeypatch.setenv("MEIC_LOGS_DIR", "~/custom-logs")
    assert home.logs_dir("meic", env="MEIC_LOGS_DIR") == _clean_env / "custom-logs"


def test_modules_dir_default_and_override(monkeypatch, _clean_env):
    assert home.modules_dir() == _clean_env / ".cherrypick" / "modules"
    monkeypatch.setenv("CHERRYPICK_MODULES_HOME", "/srv/mods")
    assert home.modules_dir() == Path("/srv/mods")


def test_config_and_dashboard_paths(_clean_env):
    base = _clean_env / ".cherrypick"
    assert home.config_path() == base / "config.json"
    assert home.dashboard_path() == base / "dashboard.html"


def test_module_config_paths(_clean_env):
    base = _clean_env / ".cherrypick"
    assert home.config_dir() == base / "config"
    assert home.config_path("meic") == base / "config" / "meic.json"
    assert home.config_path("earnings") == base / "config" / "earnings.json"
    assert home.config_path("gex") == base / "config" / "gex.json"


def test_resolvers_are_pure_no_dirs_created(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "cp"))
    _ = (home.home(), home.data_dir("meic"), home.logs_dir("meic"), home.state_dir())
    assert not (tmp_path / "cp").exists()  # nothing touched the filesystem


def test_ensure_creates_directory_idempotently(tmp_path):
    target = tmp_path / "a" / "b" / "c"
    assert home.ensure(target) == target
    assert target.is_dir()
    assert home.ensure(target) == target  # second call is a no-op, not an error
