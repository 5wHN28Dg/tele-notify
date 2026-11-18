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
    dry_run: bool


def default_data_dir() -> Path:
    return Path.home() / ".local" / "share" / "tele-notify"


def find_binary(config: Path) -> Path | None:
    """The telenotify program: $TELENOTIFY_BIN, next to the config, ~/tele-notify, or $PATH."""
    env = os.environ.get("TELENOTIFY_BIN")
    candidates = [Path(env)] if env else []
    candidates += [config.parent / "telenotify", Path.home() / "tele-notify" / "telenotify"]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    found = shutil.which("telenotify")
    return Path(found) if found else None


def resolve(config: str | os.PathLike[str], dry_run: bool) -> Paths:
    cfg = Path(config).expanduser().resolve()
    data = default_data_dir()
    try:
        raw = json.loads(cfg.read_text(encoding="utf-8"))
        if isinstance(raw.get("data_dir"), str) and raw["data_dir"]:
            data = Path(raw["data_dir"]).expanduser()
    except (OSError, ValueError):
        pass  # the settings screen reports a broken config; the rest still works
    state = data / ("state-dryrun.sqlite3" if dry_run else "state.sqlite3")
    return Paths(config=cfg, data_dir=data, state=state, binary=find_binary(cfg), dry_run=dry_run)
