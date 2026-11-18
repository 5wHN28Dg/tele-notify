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
