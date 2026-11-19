"""Writing evidence: redaction, the leak scan, machine load."""

from __future__ import annotations

import json
import os
import stat
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
    words = {w.casefold(): what for w, what in words.items()
             if len(w) > 3 or (len(w) > 2 and w.casefold() not in SHORT_COMMON)}
    hits = []
    for root, dirs, files in os.walk(out, followlinks=False):
        for d in sorted(dirs):
            if os.path.islink(os.path.join(root, d)):
                hits.append({"file": str(Path(root, d).relative_to(out)), "what": "symlink (not followed)"})
        for fname in sorted(files):
            f = Path(root, fname)
            rel = f.relative_to(out)
            for w, what in words.items():
                if w in str(rel).casefold():
                    hits.append({"file": str(rel), "what": what + " in the file name"})
            try:
                st = os.lstat(f)
            except OSError as e:
                hits.append({"file": str(rel), "what": f"can't be checked: {e.strerror}"})
                continue
            if stat.S_ISLNK(st.st_mode):
                hits.append({"file": str(rel), "what": "symlink (not followed)"})
                continue
            if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
                hits.append({"file": str(rel), "what": "not a plain file (a FIFO, socket, device or hard link): not read"})
                continue
            if fname == "leaks.json" or guard.why_forbidden(f, home):
                continue
            if st.st_size > MAX_SCAN:
                hits.append({"file": str(rel), "what": f"larger than {MAX_SCAN} bytes: not read"})
                continue
            try:
                fd = os.open(f, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as fh:
                    raw = fh.read()
            except OSError as e:
                hits.append({"file": str(rel), "what": f"can't be read: {e.strerror}"})
                continue
            text = raw.decode("utf-8", "replace").casefold()
            for n in needles:
                if n in raw:
                    hits.append({"file": str(rel), "what": "real home path", "at": raw.index(n)})
            for w, what in words.items():
                for form in _forms(w):
                    if form in text:
                        hits.append({"file": str(rel), "what": what, "at": text.index(form)})
                        break
    return hits


MAX_SCAN = 64 * 1024 * 1024
SHORT_COMMON = {"bin", "abu", "usr", "var", "tmp", "lib", "dev", "run", "opt", "etc", "src", "app", "box", "pc"}


def _forms(w: str) -> set[str]:
    """`w` as written, JSON-escaped and HTML-escaped (all casefolded)."""
    import html
    return {w, json.dumps(w)[1:-1].casefold(), html.escape(w).casefold()}


def _real_names() -> list[str]:
    import pwd
    import socket
    gecos = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0]
    return [*gecos.split(), socket.gethostname().split(".")[0]]


def scan(out: Path) -> bool:
    """Write out/leaks.json; True when nothing leaked. The file itself names neither."""
    hits = leaks(out)
    data = (json.dumps({"clean": not hits, "hits": hits}, indent=2) + "\n").encode()
    # never through a link: O_NOFOLLOW refuses a symlink, and a hard link is refused below
    fd = os.open(out / "leaks.json", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
            raise OSError(f"{out / 'leaks.json'} is a link or not a file: not written")
        os.ftruncate(fd, 0)
        os.write(fd, data)
    finally:
        os.close(fd)
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
