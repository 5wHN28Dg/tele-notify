"""An installed (not editable) screen must carry every non-Python file it loads,
such as its stylesheet: deploy/install.sh installs it that way. (CI also builds
a real wheel and looks for app.tcss in it.)"""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path

TUI = Path(__file__).resolve().parent.parent


def missing_from_package(pkg: Path, package_data: dict[str, list[str]]) -> list[str]:
    """Data files under `pkg` that a wheel would leave out. setuptools applies a
    package's patterns (or the "*" ones) to files in that package's own folder,
    and a folder without __init__.py isn't a package."""
    missing = []
    for p in sorted(pkg.rglob("*")):
        if not p.is_file() or p.suffix in (".py", ".pyc") or "__pycache__" in p.parts:
            continue
        rel = p.relative_to(pkg)
        name = ".".join((pkg.name, *rel.parent.parts))
        globs = package_data.get("*", []) + package_data.get(name, [])
        in_package = (p.parent / "__init__.py").is_file()
        if not in_package or not any(fnmatch.fnmatchcase(p.name, g) for g in globs):
            missing.append(rel.as_posix())
    return missing


def package_data() -> dict[str, list[str]]:
    conf = tomllib.loads((TUI / "pyproject.toml").read_text())
    return conf["tool"]["setuptools"].get("package-data", {})


def test_every_data_file_is_packaged():
    assert (TUI / "telenotify_tui" / "app.tcss").is_file()
    assert missing_from_package(TUI / "telenotify_tui", package_data()) == []


def test_the_check_sees_subfolders(tmp_path):
    pkg = tmp_path / "telenotify_tui"
    (pkg / "screens").mkdir(parents=True)
    (pkg / "assets").mkdir()
    for f in ("__init__.py", "screens/__init__.py", "app.tcss", "screens/x.tcss", "assets/y.tcss"):
        (pkg / f).write_text("")
    assert missing_from_package(pkg, {"telenotify_tui": ["*.tcss"]}) == ["assets/y.tcss", "screens/x.tcss"]
    assert missing_from_package(pkg, {"*": ["*.tcss"]}) == ["assets/y.tcss"]   # not a package folder
