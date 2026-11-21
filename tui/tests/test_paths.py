"""paths.resolve finds the same state file the bot uses."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from telenotify_tui.paths import default_data_dir, resolve


def write(tmp_path: Path, **cfg) -> Path:
    path = tmp_path / "conf" / "config.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def test_dry_run_in_config_means_the_dry_run_state(tmp_path):
    p = resolve(write(tmp_path, dry_run=True, data_dir=str(tmp_path / "d")), dry_run=False)
    assert p.dry_run and p.state == tmp_path / "d" / "state-dryrun.sqlite3"


@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_only_a_real_true_means_dry_run(tmp_path, value):
    p = resolve(write(tmp_path, dry_run=value, data_dir=str(tmp_path / "d")), dry_run=False)
    assert not p.dry_run and p.state.name == "state.sqlite3"


def test_the_flag_wins_over_the_config(tmp_path):
    p = resolve(write(tmp_path, dry_run=False, data_dir=str(tmp_path / "d")), dry_run=True)
    assert p.dry_run and p.state.name == "state-dryrun.sqlite3"


@pytest.mark.parametrize("value", ["", "   ", "\t\n", None, 7])
def test_blank_data_dir_is_the_default(tmp_path, value):
    assert resolve(write(tmp_path, data_dir=value), dry_run=False).data_dir == default_data_dir()


def test_data_dir_is_stripped_and_tilde_expanded(tmp_path):
    assert resolve(write(tmp_path, data_dir="  ~/tn-data \n"), False).data_dir == Path.home() / "tn-data"
    assert resolve(write(tmp_path, data_dir="~"), False).data_dir == Path.home()
    absolute = tmp_path / "abs"
    assert resolve(write(tmp_path, data_dir=f" {absolute} "), False).data_dir == absolute


def test_relative_data_dir_is_next_to_the_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                      # not relative to where the screen was started
    cfg = write(tmp_path, data_dir="data/bot")
    p = resolve(cfg, dry_run=False)
    assert p.data_dir == tmp_path / "conf" / "data" / "bot"
    assert p.state == tmp_path / "conf" / "data" / "bot" / "state.sqlite3"


def test_config_without_flag_env_then_xdg_then_cwd(tmp_path, monkeypatch):
    from telenotify_tui.paths import default_config
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELENOTIFY_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    std = tmp_path / "xdg" / "tele-notify" / "config.json"
    assert default_config() == std                      # nothing anywhere: the standard place
    (tmp_path / "config.json").write_text("{}")
    assert default_config() == Path("config.json")      # only ./config.json exists
    std.parent.mkdir(parents=True)
    std.write_text("{}")
    assert default_config() == std                      # the standard place wins
    monkeypatch.setenv("TELENOTIFY_CONFIG", str(tmp_path / "mine.json"))
    assert default_config() == tmp_path / "mine.json"   # the variable wins, even if missing
    monkeypatch.setenv("TELENOTIFY_CONFIG", "  ")
    assert default_config() == std


def test_tui_main_uses_the_default_config(tmp_path, monkeypatch):
    import telenotify_tui.__main__ as m
    monkeypatch.delenv("TELENOTIFY_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    std = tmp_path / "xdg" / "tele-notify" / "config.json"
    std.parent.mkdir(parents=True)
    std.write_text("{}")
    seen = {}

    class FakeApp:
        def __init__(self, paths):
            seen["config"] = paths.config

        def run(self):
            pass

    monkeypatch.setattr(m, "TeleNotifyApp", FakeApp)
    monkeypatch.setattr(m.rtl, "terminal_mode", lambda: __import__("contextlib").nullcontext())
    m.main([])
    assert seen["config"] == std.resolve()


def test_binary_next_to_config_wins_over_path(tmp_path, monkeypatch):
    from telenotify_tui.paths import find_binary
    monkeypatch.delenv("TELENOTIFY_BIN", raising=False)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for p in (bindir / "telenotify", tmp_path / "telenotify"):
        p.write_text("#!/bin/sh\n")
        p.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    assert find_binary(tmp_path / "config.json") == tmp_path / "telenotify"      # a build being tried out
    assert find_binary(tmp_path / "cfg" / "config.json") == bindir / "telenotify"  # nothing there: the installed one


@pytest.mark.parametrize(("config_dry", "flag"), [(True, False), (False, True), (True, True), (False, False)])
def test_the_flag_is_kept_apart_from_the_config(tmp_path, config_dry, flag):
    """#131 run 5: Settings needs the --dry-run flag alone (the config's dry_run can be edited there)."""
    p = resolve(write(tmp_path, dry_run=config_dry, data_dir=str(tmp_path / "d")), dry_run=flag)
    assert p.dry_run_flag is flag
    assert p.dry_run is (config_dry or flag)
