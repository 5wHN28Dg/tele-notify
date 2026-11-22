"""The screen's own settings (not the bot's): `screen.json` beside config.json.

The bot never reads this file, so its settings stay in config.json and the screen's stay here.
Today it holds one switch, `letter_keys` (#140): false turns the single-letter shortcuts off. A
missing or unreadable file means the defaults; it is written only when a setting is changed."""

from __future__ import annotations

import errno
import json
import os
import stat
import tempfile
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
    try:   # checked on the raw descriptor: fdopen refuses a folder, and the descriptor must not leak (run 10)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"{path} isn't a regular file")
        f = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    with f:
        data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"{path} is over {MAX_BYTES} bytes")
    return data.decode("utf-8")


# why screen.json couldn't be written, in plain words, and what to do about it (run 10)
_WHY = {
    errno.EISDIR: ("it's a folder", "Remove or rename that folder"),
    errno.EACCES: ("no permission to write there", "Give yourself write permission to that folder"),
    errno.EPERM: ("no permission to write there", "Give yourself write permission to that folder"),
    errno.EROFS: ("the disk is read-only", "Make that disk writable"),
    errno.ENOENT: ("its folder is missing", "Create that folder"),
    errno.ENOTDIR: ("a file is in the way where its folder should be", "Remove or rename that file"),
    errno.ENOSPC: ("the disk is full", "Free some space"),
    errno.EDQUOT: ("the disk is full", "Free some space"),
    errno.ENAMETOOLONG: ("the path is too long", "Move config.json to a shorter path"),
    errno.ELOOP: ("its path loops through links", "Fix the links in that path"),
}


def shown_path(path: Path) -> str:
    """`path` as the screen names it: under the home folder, starting with ~."""
    home = Path.home()
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


def not_saved(config: Path, on: bool, error: OSError) -> str:
    """What to say when the letter-keys switch couldn't be kept: what happened, what it means and
    what to do, naming what failed (save() puts it in error.filename: the file, or the folder it
    goes in when no file could be made there); the raw error is logged, not shown."""
    why, todo = _WHY.get(error.errno or 0, ("the system refused", "Check that file"))
    failed = Path(error.filename) if error.filename else path_for(config)
    where = "to" if failed.name == FILE_NAME else "in"
    return (f"Letter keys: {'on' if on else 'off'} for now, but couldn't save that {where} "
            f"{shown_path(failed)} ({why}). {todo} to keep this setting.")


def save(config: Path, prefs: Prefs) -> None:
    """Write the settings beside `config`: a new temporary file of its own (made with O_EXCL, under
    a name no one else uses, so nothing that was already there is ever written or removed), then
    renamed over screen.json, so a crash never leaves half a file. Raises OSError naming what
    failed: the folder (no file could be made in it) or screen.json (it couldn't be replaced)."""
    path = path_for(config)
    try:
        fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    except OSError as e:
        raise OSError(e.errno, e.strerror, str(path.parent)) from e
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(asdict(prefs), indent=2) + "\n")
        os.replace(tmp, path)
    except OSError as e:
        tmp.unlink(missing_ok=True)   # ours, made above: never leave it behind
        raise OSError(e.errno, e.strerror, str(path)) from e
