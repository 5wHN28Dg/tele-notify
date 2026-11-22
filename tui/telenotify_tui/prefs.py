"""The screen's own settings (not the bot's): `screen.json` beside config.json.

The bot never reads this file, so its settings stay in config.json and the screen's stay here.
Today it holds one switch, `letter_keys` (#140): false turns the single-letter shortcuts off. A
missing or unreadable file means the defaults; it is written only when a setting is changed."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

FILE_NAME = "screen.json"
LETTER_KEYS_BROKEN = ("this Textual version doesn't let the screen turn the letter keys off, so they stay "
                      "on (the screen expects Textual 8.2)")


@dataclass
class Prefs:
    letter_keys: bool = True   # single-letter shortcuts (s, p, y, ...) on


def path_for(config: Path) -> Path:
    return config.parent / FILE_NAME


def load(config: Path) -> Prefs:
    """The settings saved beside `config`; the defaults where the file is missing, unreadable
    or holds something else (a value that isn't true or false keeps its default)."""
    try:
        raw = json.loads(path_for(config).read_text(encoding="utf-8"))
    # every way reading and parsing can fail, whatever the file holds: unreadable, not UTF-8 or not
    # JSON (ValueError), nested too deep (RecursionError), too big (MemoryError)
    except (OSError, ValueError, RecursionError, MemoryError):
        return Prefs()
    prefs = Prefs()
    if isinstance(raw, dict) and isinstance(raw.get("letter_keys"), bool):
        prefs.letter_keys = raw["letter_keys"]
    return prefs


def save(config: Path, prefs: Prefs) -> None:
    """Write the settings beside `config` (a temporary file, then renamed, so a crash never
    leaves half a file). Raises OSError when it can't be written."""
    path = path_for(config)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(asdict(prefs), indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)   # never leave the half-made file behind
        raise
