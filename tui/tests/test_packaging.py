"""An installed (not editable) screen must carry every non-Python file it loads,
such as its stylesheet: deploy/install.sh installs it that way."""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path

TUI = Path(__file__).resolve().parent.parent


def test_every_data_file_is_packaged():
    conf = tomllib.loads((TUI / "pyproject.toml").read_text())
    globs = conf["tool"]["setuptools"].get("package-data", {}).get("telenotify_tui", [])
    pkg = TUI / "telenotify_tui"
    data = [p.relative_to(pkg).as_posix() for p in pkg.rglob("*")
            if p.is_file() and p.suffix not in (".py", ".pyc") and "__pycache__" not in p.parts]
    assert data, "expected app.tcss at least"
    missing = [d for d in data if not any(fnmatch.fnmatch(d, g) for g in globs)]
    assert missing == []
