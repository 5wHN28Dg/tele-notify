"""The screen's own settings (not the bot's): `screen.json` beside config.json.

The bot never reads this file, so its settings stay in config.json and the screen's stay here.
Today it holds one switch, `letter_keys` (#140): false turns the single-letter shortcuts off. A
missing or unreadable file means the defaults; it is written only when a setting is changed."""

from __future__ import annotations

import json
import os
import stat
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
        raw = json.loads(_read_small(path_for(config)))
    # every way reading and parsing can fail, whatever the file holds: unreadable, not UTF-8 or not
    # JSON (ValueError), nested too deep (RecursionError), too big (MemoryError)
    except (OSError, ValueError, RecursionError, MemoryError):
        return Prefs()
    prefs = Prefs()
    if isinstance(raw, dict) and isinstance(raw.get("letter_keys"), bool):
        prefs.letter_keys = raw["letter_keys"]
    return prefs


MAX_BYTES = 64 * 1024   # the file holds a line or two; anything bigger isn't one of ours


def _read_small(path: Path) -> str:
    """The text of `path` if it is a regular file of at most MAX_BYTES, else ValueError. Opened
    without blocking (a FIFO named screen.json would wait forever) and checked before reading (a
    link to an endless device would read until memory runs out) (run 9)."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"{path} isn't a regular file")
        data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"{path} is over {MAX_BYTES} bytes")
    return data.decode("utf-8")


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
