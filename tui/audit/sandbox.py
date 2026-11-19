"""The sandbox every evidence run uses (docs/UI-UX-POLICY.md, section 3.3).

`plan()` only works out paths; `check()` runs the real-home guard on all of
them before anything is changed; `create()` makes the run's own folder
/tmp/tn-audit/<run>/ (never shared with another run); `prepare()` fills it;
`apply()` replaces this process's environment with the sandbox's; `remove()`
copies the fakes' logs into the evidence and deletes the run folder.

The large master state file (~/.cache/agent-tmp/tn-audit/) is never opened for
writing: `use_big()` copies it into the run's own scratch folder next to it
(on disk; /tmp is memory) and links the run's state file to the copy.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import data, fakes, guard

BASE = Path("/tmp/tn-audit")
SYSTEM_PATH = "/usr/bin:/bin"
TUI = Path(__file__).resolve().parents[1]
REPO = TUI.parent


@dataclass
class Sandbox:
    real_home: Path
    base: Path
    run: str
    out: Path
    dry_run: bool = True
    use_repo_bin: bool = False

    # ------------------------------------------------------------ paths

    @property
    def root(self) -> Path:
        """This run's own folder."""
        return self.base / self.run

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def config(self) -> Path:
        return self.root / "config.json"

    @property
    def data_dir(self) -> Path:
        return self.home / "data"

    @property
    def state(self) -> Path:
        return self.data_dir / ("state-dryrun.sqlite3" if self.dry_run else "state.sqlite3")

    @property
    def labels(self) -> Path:
        return self.home / "corpus" / "review-labels.jsonl"

    @property
    def corpus(self) -> Path:
        return self.home / "corpus" / "eval.jsonl"

    @property
    def bin(self) -> Path:
        return self.root / "bin"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def cache(self) -> Path:
        """Where the large state file is kept between runs (on disk: /tmp is memory)."""
        return self.real_home / ".cache" / "agent-tmp" / "tn-audit"

    @property
    def scratch(self) -> Path:
        """This run's copy of the large state file (on disk), deleted with the run."""
        return self.cache / "runs" / self.run

    @property
    def telenotify(self) -> Path:
        return REPO / "telenotify" if self.use_repo_bin else self.bin / "telenotify"

    def all_paths(self) -> list[Path]:
        return [self.root, self.home, self.config, self.data_dir, self.state, self.labels, self.corpus,
                self.bin, self.tmp, self.cache, self.scratch, self.telenotify, self.out]

    # ------------------------------------------------------- environment

    def env(self, bidi: str = "full") -> dict[str, str]:
        """The whole environment for the screen and its children: nothing from the caller's
        (no DISPLAY, WAYLAND_DISPLAY, DBUS_SESSION_BUS_ADDRESS, XDG_RUNTIME_DIR, USER...)."""
        h = str(self.home)
        return {
            "HOME": h, "PATH": f"{self.bin}:{SYSTEM_PATH}", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "TERM": "xterm-256color", "COLORTERM": "truecolor", "TMPDIR": str(self.tmp),
            "XDG_CONFIG_HOME": f"{h}/.config", "XDG_DATA_HOME": f"{h}/.local/share",
            "XDG_CACHE_HOME": f"{h}/.cache", "XDG_STATE_HOME": f"{h}/.local/state",
            "BROWSER": str(self.bin / "browser"),
            "TELENOTIFY_LABELS": str(self.labels), "TELENOTIFY_CORPUS": str(self.corpus),
            "TELENOTIFY_BIN": str(self.telenotify), "TELENOTIFY_BIDI": bidi,
            "PYTHONPATH": str(TUI), "PYTHONDONTWRITEBYTECODE": "1", "TN_AUDIT_ROOT": str(self.root),
        }


def plan(out: Path, *, base: Path = BASE, job: str = "run", dry_run: bool = True, use_repo_bin: bool = False,
         real_home: Path | None = None) -> Sandbox:
    """Work out every path; nothing is changed yet. The real home comes from the password database."""
    home = guard.real_home() if real_home is None else real_home
    run = f"{job}-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    return Sandbox(real_home=home, base=Path(os.path.abspath(base)), run=run, out=Path(os.path.abspath(out)),
                   dry_run=dry_run, use_repo_bin=use_repo_bin)


def check(sb: Sandbox) -> None:
    """The guard, on every path the run will use. Raises guard.Refused."""
    guard.check(sb.all_paths(), sb.real_home)
    held = guard.holds_protected(sb.out, sb.real_home)
    if held:
        raise guard.Refused(f"refusing to run: --out {held} (the leak scan reads everything under --out)")
    real_out, real_base = Path(os.path.realpath(sb.out)), Path(os.path.realpath(sb.base))
    if (sb.out == sb.base or sb.base in sb.out.parents
            or real_out == real_base or real_base in real_out.parents):
        raise guard.Refused(f"--out must be outside {sb.base}: run folders are deleted")
    if sb.use_repo_bin and not sb.telenotify.is_file():
        raise guard.Refused(f"--bin repo: {sb.telenotify} isn't built (nimble build -d:release)")


def _own_dir(p: Path) -> None:
    """`p` exists as a real folder (not a symlink) owned by this user, mode 0700."""
    try:
        p.mkdir(mode=0o700)
    except FileExistsError:
        pass
    st = os.lstat(p)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise guard.Refused(f"{p} isn't a folder of this user's (a symlink, a file or someone else's)")
    os.chmod(p, 0o700)


def create(sb: Sandbox) -> None:
    """The run's own folder, new (a run never reuses or shares one)."""
    _own_dir(sb.base)
    sb.root.mkdir(mode=0o700)


def remove(sb: Sandbox) -> None:
    """Keep the fakes' call logs as evidence, then delete the run folder and its scratch copy."""
    logs = sb.root / "log"
    if logs.is_dir() and sb.out.is_dir():
        shutil.copytree(logs, sb.out / "fake-calls", dirs_exist_ok=True)
    for d in (sb.root, sb.scratch):
        if d.is_dir() and not d.is_symlink():
            shutil.rmtree(d)


def use_big(sb: Sandbox, master: Path) -> Path:
    """Link the run's state file to a fresh copy of the large master file (the master is never
    opened for writing: the screen opens its state file read-write)."""
    sb.scratch.mkdir(parents=True, exist_ok=True)
    copy = sb.scratch / "state.sqlite3"
    for extra in ("", "-wal", "-shm"):
        Path(str(copy) + extra).unlink(missing_ok=True)
        Path(str(sb.state) + extra).unlink(missing_ok=True)
    shutil.copyfile(master, copy)
    sb.state.symlink_to(copy)
    return copy


def wipe(sb: Sandbox) -> None:
    """Empty the run folder (ours, checked by `_own_dir`)."""
    _own_dir(sb.root)
    for child in sb.root.iterdir():
        if child.is_symlink() or not child.is_dir():
            child.unlink()
        else:
            shutil.rmtree(child)


def prepare(sb: Sandbox, *, services: dict | None = None, state: bool = True) -> None:
    """A fresh sandbox: fakes, config, corpus, small synthetic state."""
    wipe(sb)
    for d in (sb.home, sb.data_dir, sb.labels.parent, sb.tmp, sb.root / "log"):
        d.mkdir(parents=True, exist_ok=True)
    fakes.install(sb.root, sb.bin)
    mine, other = "active", "inactive"   # the service of the shown mode runs
    fakes.set_services(sb.root, services or (fakes.services(dry_run=mine, real=other) if sb.dry_run
                                             else fakes.services(dry_run=other, real=mine)))
    fakes.set_telenotify(sb.root)
    data.write_config(sb.config, data.config(sb.data_dir, dry_run=sb.dry_run))
    sb.corpus.write_text("".join(json.dumps({"label": "TRUE" if i % 3 else "FALSE", "text": t},
                                            ensure_ascii=False) + "\n" for i, t in enumerate(data.POSTS)),
                         encoding="utf-8")
    if state:
        data.small_state(sb.state, dry_run=sb.dry_run)


def apply(sb: Sandbox, bidi: str = "full") -> None:
    """Make this process's environment the sandbox's (before telenotify_tui is imported)."""
    app = os.environ.get("TN_AUDIT_APP")     # which checkout's screen (appsrc), for child processes
    os.environ.clear()
    os.environ.update(sb.env(bidi))
    if app:
        os.environ["TN_AUDIT_APP"] = app
    tempfile.tempdir = None


def set_bidi(mode: str) -> None:
    """Pin the bidi mode for the next app run in this process."""
    from telenotify_tui import rtl

    os.environ["TELENOTIFY_BIDI"] = mode
    rtl.set_mode(mode)
