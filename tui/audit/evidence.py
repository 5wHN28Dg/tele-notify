"""Writing evidence: redaction, the leak scan, machine load."""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import guard


def redact(s: str, home: Path | None = None) -> str:
    """Text with the real home path written as ~ (tracebacks, handle reprs)."""
    home = guard.real_home() if home is None else home
    for h in {str(home), os.path.realpath(home)}:
        s = s.replace(h, "~")
    return s


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact(json.dumps(obj, ensure_ascii=False, indent=2)) + "\n", encoding="utf-8")


def write_text(path: Path, s: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact(s), encoding="utf-8")


def leaks(out: Path, home: Path | None = None, login: str | None = None,
          names: list[str] | None = None) -> list[dict]:
    """Every file under `out` (names and contents) holding the login name, the real name
    (from the password database), the host name or the real home path. Symlinks are
    reported, never followed, and nothing in a protected place is read."""
    home = guard.real_home() if home is None else home
    login = guard.login_name() if login is None else login
    needles = {str(home).encode(), os.path.realpath(home).encode()}
    words = {login.lower(): "login name"}
    if names is None:
        names = _real_names()
    for n in names:
        words.setdefault(n.lower(), "real name or host name")
    words = {w.encode(): what for w, what in words.items() if len(w) > 2}
    hits = []
    for root, dirs, files in os.walk(out, followlinks=False):
        for d in sorted(dirs):
            if os.path.islink(os.path.join(root, d)):
                hits.append({"file": str(Path(root, d).relative_to(out)), "what": "symlink (not followed)"})
        for fname in sorted(files):
            f = Path(root, fname)
            rel = f.relative_to(out)
            for w, what in words.items():
                if w in str(rel).encode().lower():
                    hits.append({"file": str(rel), "what": what + " in the file name"})
            if f.is_symlink():
                hits.append({"file": str(rel), "what": "symlink (not followed)"})
                continue
            if fname == "leaks.json" or guard.why_forbidden(f, home):
                continue
            raw = f.read_bytes()
            low = raw.lower()
            for n in needles:
                if n in raw:
                    hits.append({"file": str(rel), "what": "real home path", "at": raw.index(n)})
            for w, what in words.items():
                if w in low:
                    hits.append({"file": str(rel), "what": what, "at": low.index(w)})
    return hits


def _real_names() -> list[str]:
    import pwd
    import socket
    gecos = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0]
    return [*gecos.split(), socket.gethostname().split(".")[0]]


def scan(out: Path) -> bool:
    """Write out/leaks.json; True when nothing leaked. The file itself names neither."""
    hits = leaks(out)
    (out / "leaks.json").write_text(json.dumps({"clean": not hits, "hits": hits}, indent=2) + "\n",
                                    encoding="utf-8")
    return not hits


def machine() -> dict:
    """The machine's load while evidence is taken (R7: recorded, never reduced)."""
    load1, load5, load15 = os.getloadavg()
    mem = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            if k in ("MemTotal", "MemAvailable"):
                mem[k] = int(v.split()[0]) // 1024
    except OSError:
        pass
    return {"load": [round(load1, 2), round(load5, 2), round(load15, 2)], "cpus": os.cpu_count(),
            "mem_total_mib": mem.get("MemTotal"), "mem_available_mib": mem.get("MemAvailable")}
