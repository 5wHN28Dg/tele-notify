"""Which checkout's screen to audit.

By default the screen is this checkout's `tui/telenotify_tui`. `--app DIR`
(a `tui/` folder of any other checkout, e.g. a worktree of main) makes every
collector import the screen from there instead, so counts and evidence can be
taken on main and on a branch with the same harness. The venv's editable
install hooks the import system, so its finder is taken out first.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ENV = "TN_AUDIT_APP"
HERE = Path(__file__).resolve().parents[1]


def use(app_dir: str | os.PathLike[str] | None) -> Path:
    """Import telenotify_tui from `app_dir` (a tui/ folder). Call before importing it."""
    d = Path(app_dir or HERE).resolve()
    if not (d / "telenotify_tui" / "__init__.py").is_file():
        raise SystemExit(f"{d} has no telenotify_tui package (give a checkout's tui/ folder)")
    if "telenotify_tui" in sys.modules and Path(sys.modules["telenotify_tui"].__file__ or "").parent.parent != d:
        raise RuntimeError("telenotify_tui was imported before the app source was chosen")
    sys.meta_path[:] = [f for f in sys.meta_path if "__editable__" not in getattr(f, "__module__", "")
                        and "__editable__" not in type(f).__module__]
    sys.path.insert(0, str(d))
    os.environ[ENV] = str(d)
    return d


def describe(d: Path) -> dict:
    """The commit of the checkout the screen came from (no paths: evidence may be published)."""
    try:
        rev = subprocess.run(["git", "-C", str(d), "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, timeout=10, check=False).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(d), "status", "--porcelain", "--", "."], capture_output=True,
                               text=True, timeout=10, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        rev, dirty = "", ""
    return {"commit": rev or "unknown", "uncommitted_changes": bool(dirty), "is_this_checkout": d == HERE}
