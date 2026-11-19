"""The real-home guard: never touch the owner's bot data, corpus or deployment.

`real_home()` reads the password database, so a changed $HOME can't hide the
real one. `check()` refuses any path that is, or (symlinks followed) points
into, one of the protected places. Nothing here reads a protected file: it
only resolves names, and lists the entries of ~/.local/share whose names
start with "tele-notify" so that a symlinked data folder is protected at its
target too.
"""

from __future__ import annotations

import os
import pwd
from pathlib import Path


class Refused(Exception):
    """A path the harness would use is in a protected place."""


def real_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def login_name() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def _real(p: Path) -> Path:
    return Path(os.path.realpath(p))


def _under(p: Path, root: Path) -> bool:
    return p == root or root in p.parents


def protected(home: Path) -> tuple[Path, list[Path]]:
    """(the share folder whose tele-notify* entries are protected, other protected roots).
    Roots are given both as written and resolved."""
    share = home / ".local" / "share"
    roots = [share / "tele-notify-corpus", home / "tele-notify", home / "Projects" / "bqtechjobs",
             home / ".config" / "tele-notify",                    # the installed config (deploy/install.sh)
             home / ".local" / "lib" / "tele-notify",             # TDLib and the installed screen
             home / ".local" / "bin" / "telenotify", home / ".local" / "bin" / "telenotify-tui"]
    try:
        roots += [share / e.name for e in os.scandir(share) if e.name.startswith("tele-notify")]
    except OSError:
        pass
    out: list[Path] = []
    for r in roots:
        out += [r, _real(r)]
    return share, out


def why_forbidden(path: str | os.PathLike[str], home: Path | None = None) -> str | None:
    """A reason when `path` (as written, or with symlinks followed) is protected, else None."""
    home = real_home() if home is None else home
    share, roots = protected(home)
    shares = {share, _real(share)}
    written = Path(os.path.abspath(path))
    for p in (written, _real(written)):
        for root in roots:
            if _under(p, root):
                return f"{p} is in {root}"
        for s in shares:
            if p != s and s in p.parents and p.relative_to(s).parts[0].startswith("tele-notify"):
                return f"{p} is in {s}/tele-notify*"
    return None


def check(paths: list[str | os.PathLike[str]], home: Path | None = None) -> None:
    """Raise Refused when any of `paths` is protected."""
    home = real_home() if home is None else home
    bad = [r for r in (why_forbidden(p, home) for p in paths) if r]
    if bad:
        raise Refused("refusing to run: " + "; ".join(bad))
