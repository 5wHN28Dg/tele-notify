"""Where the bot keeps its things, worked out from config.json."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    config: Path        # config.json
    data_dir: Path      # the bot's data folder (TDLib login + state)
    state: Path         # the SQLite state file this screen shows
    binary: Path | None # the telenotify program, if found
    dry_run: bool       # the flag or the config's dry_run, when the screen was opened
    dry_run_flag: bool = False   # the --dry-run flag alone


def default_data_dir() -> Path:
    return Path.home() / ".local" / "share" / "tele-notify"


def config_candidates() -> list[Path]:
    """Where config.json is looked for without --config, in order (as the bot
    does): $TELENOTIFY_CONFIG, ~/.config/tele-notify/config.json (or under
    $XDG_CONFIG_HOME), then ./config.json."""
    env = os.environ.get("TELENOTIFY_CONFIG", "").strip()
    if env:
        return [Path(env).expanduser()]
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".config"
    return [base / "tele-notify" / "config.json", Path("config.json")]


def default_config() -> Path:
    """The first candidate that exists, else the standard place."""
    c = config_candidates()
    return next((p for p in c if p.is_file()), c[0])


def find_binary(config: Path) -> Path | None:
    """The telenotify program: $TELENOTIFY_BIN, next to the config (a build
    you're trying out in a checkout), or $PATH (~/.local/bin after
    deploy/install.sh)."""
    env = os.environ.get("TELENOTIFY_BIN")
    for c in ([Path(env)] if env else []) + [config.parent / "telenotify"]:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    found = shutil.which("telenotify")
    return Path(found) if found else None


def data_dir_from(cfg_path: Path, value: object) -> Path:
    """The bot's data folder for config.json's `data_dir` value, worked out the
    way the bot does: blank (or not text) means the default; surrounding
    spaces are ignored; a leading ~ is your home folder; a relative path is
    relative to the folder config.json is in."""
    if not isinstance(value, str) or not value.strip():
        return default_data_dir()
    s = value.strip()
    if s == "~" or s.startswith("~/"):
        return Path.home() / s[2:]
    p = Path(s)
    return p if p.is_absolute() else cfg_path.parent / p


def resolve(config: str | os.PathLike[str], dry_run: bool) -> Paths:
    """`dry_run`: the --dry-run flag. As in the bot, `"dry_run": true` in
    config.json means a dry run (its own state file) even without the flag."""
    cfg = Path(config).expanduser().resolve()
    raw: dict = {}
    try:
        loaded = json.loads(cfg.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded
    except (OSError, ValueError):
        pass  # the settings screen reports a broken config; the rest still works
    data = data_dir_from(cfg, raw.get("data_dir"))
    dry = dry_run or raw.get("dry_run") is True
    state = data / ("state-dryrun.sqlite3" if dry else "state.sqlite3")
    return Paths(config=cfg, data_dir=data, state=state, binary=find_binary(cfg), dry_run=dry,
                 dry_run_flag=dry_run)
