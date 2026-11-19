"""The screen as a real terminal draws it (kitty, Ptyxis / VTE), captured as PNG.

The SVG screenshots are Textual's own idea of the screen; this job shows what a
real terminal makes of it: its font, its colours, its Arabic shaping and its
cell grid. Rendering only: nothing is typed or clicked. Each shot starts the
real screen, in the harness's sandbox, in a fresh terminal window; the screen
itself (a harness child process) switches theme and tab in-process, the way
the `1`-`4` keys and the theme setting do, waits until its workers are done,
says it is ready, and the window is captured.

Where it draws: a headless GNOME compositor of its own (`mutter --headless`,
inside `dbus-run-session`), with Xwayland, a private runtime folder, home and
D-Bus session, all inside this run's session folder /tmp/tn-audit/<run>-term/.
Nothing of the caller's desktop is used: DISPLAY, WAYLAND_DISPLAY,
DBUS_SESSION_BUS_ADDRESS, XAUTHORITY and XDG_RUNTIME_DIR are never passed on.
Windows are captured with ImageMagick's `import -window` (Xwayland is rootless,
so the root window shows nothing).

Heavy (mutter and a terminal: about 1-2 GB): run it through the memory guard.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import evidence, guard, svg
from . import sandbox as sbx
from .interactive import TERMINAL_VARS
from .sandbox import Sandbox

TERMINALS = ("kitty", "ptyxis")
SCENES = ("1-dashboard", "2-review", "3-search", "4-settings", "keys-panel")
DEFAULT_BIDI = {"kitty": "words", "ptyxis": "full"}     # what the screen's auto mode picks in each
THEMES = {"dark": "textual-dark", "light": "textual-light"}
MONITOR = (2560, 1600)
FONT_SIZE = 11
READY_TIMEOUT = 40.0
TOOLS = ("mutter", "dbus-run-session", "Xwayland", "xdotool", "import", "magick")


def available(real_home: Path) -> list[str]:
    """The terminals installed, when everything else the job needs is too."""
    if any(find_program(t, real_home) is None for t in TOOLS):
        return []
    return [t for t in TERMINALS if find_program(t, real_home)]


@dataclass
class Shot:
    terminal: str
    size: str
    theme: str
    mode: str
    bidi: str
    scene: str

    @property
    def folder(self) -> str:
        return f"terminal/{self.terminal}/{self.mode}/{self.theme}/bidi-{self.bidi}/{self.size}"

    @property
    def file(self) -> str:
        return f"{self.folder}/{self.scene}.png"


# ---------------------------------------------------------------- planning

def parse_size(s: str) -> tuple[int, int]:
    """'120x40' -> (120, 40); columns 20-400, rows 10-200."""
    m = re.fullmatch(r"(\d+)x(\d+)", s)
    if not m or not (20 <= int(m[1]) <= 400 and 10 <= int(m[2]) <= 200):
        raise ValueError(f"size {s!r}: give COLUMNSxROWS, e.g. 120x40")
    return int(m[1]), int(m[2])


def plan_shots(terminals: list[str], sizes: list[str], themes: list[str], modes: list[str],
               bidis: list[str] | None, only: set[str] | None = None) -> list[Shot]:
    """Every shot, in the order they are taken (one terminal at a time). `bidis` None: each
    terminal in the mode the screen's auto setting picks for it."""
    for t in terminals:
        if t not in TERMINALS:
            raise ValueError(f"terminal {t!r}: one of {', '.join(TERMINALS)}")
    for s in sizes:
        parse_size(s)
    for th in themes:
        if th not in THEMES:
            raise ValueError(f"theme {th!r}: dark or light")
    for m in modes:
        if m not in ("dry-run", "real"):
            raise ValueError(f"mode {m!r}: dry-run or real")
    for b in bidis or []:
        if b not in ("full", "words", "off"):
            raise ValueError(f"bidi {b!r}: full, words or off")
    scenes = [s for s in SCENES if not only or any(s.startswith(o) for o in only)]
    if not scenes:
        raise ValueError(f"--only matches no scene ({', '.join(SCENES)})")
    return [Shot(t, size, theme, mode, b, scene)
            for t in terminals for mode in modes for theme in themes
            for b in (bidis or [DEFAULT_BIDI[t]]) for size in sizes for scene in scenes]


def find_program(name: str, real_home: Path, path: str | None = None) -> Path | None:
    """Where `name` is: the caller's PATH, then ~/.local/bin (kitty's installer puts it there).
    Never a protected place."""
    search = os.pathsep.join(p for p in [path if path is not None else os.environ.get("PATH", ""),
                                         str(real_home / ".local" / "bin"), "/usr/local/bin", "/usr/bin", "/bin"] if p)
    found = shutil.which(name, path=search)
    if not found or guard.why_forbidden(found, real_home):
        return None
    return Path(found)


def version(name: str, exe: Path) -> str:
    try:
        r = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=20, check=False,
                           env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    out = (r.stdout or r.stderr).strip()
    if name == "ptyxis":                       # "Ptyxis 50.1 ... VTE: 0.84.0 (Compiled against ...)"
        p = re.search(r"Ptyxis\s+(\S+)", out)
        v = re.search(r"VTE:\s+(\S+)", out)
        return f"Ptyxis {p[1] if p else '?'} (VTE {v[1] if v else '?'})"
    return out.splitlines()[0] if out else "unknown"


# ---------------------------------------------------------- the terminals

def screen_cmd(python: str, tui: Path, spec: Path) -> list[str]:
    """The command the terminal runs: the harness child that starts the screen (no PYTHONPATH in
    the terminal's own environment: kitty is a Python program too)."""
    boot = (f"import sys; sys.path.insert(0, {str(tui)!r}); from audit import child; "
            "sys.exit(child.main(sys.argv[1:]))")
    return [python, "-c", boot, "terminal", str(spec)]


def kitty_argv(exe: Path, cols: int, rows: int, title: str, cmd: list[str], config: Path | None) -> list[str]:
    """kitty as an X11 client, sized in cells; without --kitty-config, kitty's defaults (`--config NONE`)."""
    argv = [str(exe), "--config", str(config) if config else "NONE",
            "-o", "linux_display_server=x11", "-o", "remember_window_size=no",
            "-o", f"initial_window_width={cols}c", "-o", f"initial_window_height={rows}c",
            "-o", "confirm_os_window_close=0", "-o", "allow_remote_control=no", "-o", "update_check_interval=0"]
    if config is None:
        argv += ["-o", f"font_size={FONT_SIZE}"]
    return [*argv, "--title", title, *cmd]


def ptyxis_keyfile(cols: int, rows: int, theme: str) -> str:
    """Ptyxis's settings for one shot (GSETTINGS_BACKEND=keyfile in the private home): the size in
    cells, nothing restored from an earlier window, the style that goes with the screen's theme."""
    return ("[org/gnome/Ptyxis]\n"
            f"default-columns=uint32 {cols}\n"
            f"default-rows=uint32 {rows}\n"
            "restore-session=false\n"
            "restore-window-size=false\n"
            f"interface-style='{theme}'\n"
            "use-system-font=false\n"
            f"font-name='Monospace {FONT_SIZE}'\n")


def ptyxis_argv(exe: Path, title: str, cmd: list[str]) -> list[str]:
    """A separate Ptyxis instance per shot (-s), so closing it ends the process."""
    return [str(exe), "-s", "--new-window", "-T", title, "--", *cmd]


# ------------------------------------------------------- the compositor

# Ptyxis starts each tab through `systemd-run --user --scope ...`. The session has no user systemd
# (and must never reach yours), so this stand-in, first on the session's PATH, logs the call and
# runs the command itself: its options are skipped up to the command (or past a `--`).
SYSTEMD_RUN = r"""#!/bin/sh
printf '%s\n' "$*" >> "${TN_AUDIT_SCOPE_LOG:-/dev/null}"
while [ $# -gt 0 ]; do
  case "$1" in
    --) shift; break ;;
    --property|--unit|--description|--slice|--uid|--gid|--nice|--working-directory|-p|-u|-E|--setenv) shift 2 ;;
    -*) shift ;;
    *) break ;;
  esac
done
exec "$@"
"""

class Session:
    """A headless mutter with Xwayland and its own D-Bus session, home and runtime folder."""

    def __init__(self, folder: Path, extra_path: list[str]):
        self.folder = folder
        self.runtime = folder / "run"
        self.home = folder / "home"
        self.log = folder / "mutter.log"
        self.extra_path = extra_path
        self.proc: subprocess.Popen | None = None
        self.display = ""
        self.bus = ""
        self.xauth = ""
        self.name = f"tn-audit-{os.getpid()}"

    def base_env(self) -> dict[str, str]:
        """Built from scratch: nothing of the caller's desktop."""
        h = str(self.home)
        path = ":".join([str(self.bin), *self.extra_path, "/usr/local/bin", "/usr/bin", "/bin"])
        return {"HOME": h, "PATH": path, "LANG": "C.UTF-8", "XDG_RUNTIME_DIR": str(self.runtime),
                "XDG_CONFIG_HOME": f"{h}/.config", "XDG_DATA_HOME": f"{h}/.local/share",
                "XDG_CACHE_HOME": f"{h}/.cache", "XDG_STATE_HOME": f"{h}/.local/state",
                "GSETTINGS_BACKEND": "keyfile", "NO_AT_BRIDGE": "1", "TMPDIR": str(self.folder / "tmp"),
                # no gvfs FUSE mount in the runtime folder, no portals: nothing outlives the session
                "GIO_USE_VFS": "local", "GVFS_DISABLE_FUSE": "1", "GTK_USE_PORTAL": "0",
                "TN_AUDIT_SCOPE_LOG": str(self.folder / "systemd-run.log")}

    @property
    def bin(self) -> Path:
        return self.folder / "bin"

    def env(self) -> dict[str, str]:
        """For the terminals and the capture tools: the session's own X display and bus."""
        return {**self.base_env(), "DISPLAY": self.display, "XAUTHORITY": self.xauth,
                "DBUS_SESSION_BUS_ADDRESS": self.bus, "GDK_BACKEND": "x11"}

    def start(self, fonts: Path | None = None) -> None:
        for d in (self.runtime, self.home, self.bin, self.folder / "tmp",
                  self.home / ".config" / "glib-2.0" / "settings"):
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.runtime, 0o700)
        stand_in = self.bin / "systemd-run"
        stand_in.write_text(SYSTEMD_RUN, encoding="utf-8")
        stand_in.chmod(0o700)
        if fonts is not None and fonts.is_dir():
            # the owner's own fonts (read only), so a kitty.conf's font_family is found
            (self.home / ".local" / "share").mkdir(parents=True, exist_ok=True)
            (self.home / ".local" / "share" / "fonts").symlink_to(fonts)
        bus_file = self.folder / "bus"
        w, h = MONITOR
        script = (f'printf %s "$DBUS_SESSION_BUS_ADDRESS" > {sh(bus_file)}; '
                  f"exec mutter --headless --wayland --wayland-display {self.name} --virtual-monitor {w}x{h}")
        with open(self.log, "wb") as log:
            self.proc = subprocess.Popen(["dbus-run-session", "--", "sh", "-c", script], stdout=log, stderr=log,
                                         stdin=subprocess.DEVNULL, env=self.base_env(), cwd=self.home,
                                         start_new_session=True)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            text = self.log.read_text(errors="replace")
            m = re.search(r"Using public X11 display (:\d+)", text)
            if m and bus_file.exists() and bus_file.read_text().strip():
                self.display, self.bus = m[1], bus_file.read_text().strip()
                break
            if self.proc.poll() is not None:
                break
            time.sleep(0.2)
        if not self.display:
            raise RuntimeError("mutter didn't start Xwayland: " + _tail(self.log))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not self.xauth:
            auths = sorted(self.runtime.glob(".mutter-Xwaylandauth.*"), key=lambda p: p.stat().st_mtime)
            self.xauth = str(auths[-1]) if auths else ""
            time.sleep(0.1)
        if not self.xauth:
            raise RuntimeError("no Xwayland authority file in the session's runtime folder")

    def stop(self) -> None:
        if self.proc is None:
            return
        _kill_group(self.proc)
        self.proc = None


def sh(p: Path) -> str:
    import shlex
    return shlex.quote(str(p))


def _tail(p: Path, n: int = 15) -> str:
    try:
        return evidence.redact("\n".join(p.read_text(errors="replace").splitlines()[-n:]))
    except OSError:
        return ""


def _kill_group(proc: subprocess.Popen, grace: float = 5) -> None:
    """End a process started with start_new_session, and everything it started."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(grace)
    except subprocess.TimeoutExpired:
        pass
    # whatever is left of the group (the leader, or children that outlived it)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        pass


def _x(env: dict[str, str], *argv: str, timeout: float = 20) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), env=env, capture_output=True, text=True, timeout=timeout, check=False)


def find_window(env: dict[str, str], pid: int, klass: str) -> str | None:
    """The terminal's top-level window: by process, else the only visible one of its class."""
    for argv in (["xdotool", "search", "--onlyvisible", "--pid", str(pid)],
                 ["xdotool", "search", "--onlyvisible", "--class", klass]):
        ids = _x(env, *argv).stdout.split()
        best, area = None, 0
        for wid in ids:
            g = _x(env, "xdotool", "getwindowgeometry", wid).stdout
            m = re.search(r"Geometry:\s*(\d+)x(\d+)", g)
            if m and int(m[1]) * int(m[2]) > area:
                best, area = wid, int(m[1]) * int(m[2])
        if best and area > 100 * 100:
            return best
    return None


# ------------------------------------------------------------- the shots

def collect(sb: Sandbox, terminals: list[str], sizes: list[str], themes: list[str], modes: list[str],
            bidis: list[str] | None, only: set[str] | None, kitty_config: Path | None = None) -> dict:
    shots = plan_shots(terminals, sizes, themes, modes, bidis, only)
    exes = {t: find_program(t, sb.real_home) for t in terminals}
    versions = {t: version(t, e) if e else "not installed" for t, e in exes.items()}
    session = {"compositor": "not started", "monitor": f"{MONITOR[0]}x{MONITOR[1]}",
               "font": (f"terminal default monospace, {FONT_SIZE} pt" if kitty_config is None
                        else f"kitty: the given kitty.conf; Ptyxis: Monospace {FONT_SIZE}"),
               "kitty_config": "given" if kitty_config else "NONE (kitty's defaults)"}
    index: list[dict] = []
    problem = None
    missing = [t for t in TOOLS if not find_program(t, sb.real_home)]
    if missing:
        problem = f"missing programs: {', '.join(missing)} (needs mutter, Xwayland, xdotool and ImageMagick)"
    else:
        session["compositor"] = version("mutter", find_program("mutter", sb.real_home) or Path("mutter"))
        folder = session_folder(sb)
        guard.check([folder], sb.real_home)
        folder.mkdir(mode=0o700)
        keep = sb.dry_run
        sess = Session(folder, sorted({str(e.parent) for e in exes.values() if e}))
        try:
            fonts = sb.real_home / ".local" / "share" / "fonts"
            sess.start(fonts if fonts.is_dir() and not guard.why_forbidden(fonts, sb.real_home) else None)
            for i, s in enumerate(shots):
                exe = exes[s.terminal]
                if exe is None:
                    index.append({**_row(s, versions), "error": f"{s.terminal} isn't installed"})
                    continue
                sb.dry_run = s.mode != "real"
                index.append(take(sb, sess, s, i, exe, versions, kitty_config))
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            problem = evidence.redact(str(e))
        finally:
            sb.dry_run = keep
            sess.stop()
            try:
                _remove(folder)
            except (RuntimeError, OSError) as e:
                problem = (problem + "; " if problem else "") + evidence.redact(str(e))
    evidence.write_json(sb.out / "terminal" / "index.json", index)
    evidence.write_text(sb.out / "terminal" / "summary.md", summary(index, session, problem))
    errors = sum(1 for r in index if "error" in r) + (1 if problem else 0)
    result = {"shots": len(index), "errors": errors, "session": session, "versions": versions}
    if problem:
        result["problem"] = problem
    return result


def session_folder(sb: Sandbox) -> Path:
    """Beside the run folder (prepare() empties the run folder before every shot)."""
    return sb.base / f"{sb.run}-term"


def mounts_under(folder: Path, mounts: str | None = None) -> list[str]:
    """Mount points inside `folder` (a desktop service may mount gvfs in a runtime folder)."""
    if mounts is None:
        try:
            mounts = Path("/proc/self/mounts").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
    root = str(folder).rstrip("/") + "/"
    points = []
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) > 1:
            point = parts[1].replace("\\040", " ")
            if point.startswith(root):
                points.append(point)
    return sorted(points, key=len, reverse=True)


def _remove(folder: Path) -> None:
    """Unmount anything mounted inside the session folder (lazily, never following it), then delete it."""
    if not folder.is_dir() or folder.is_symlink():
        return
    for point in mounts_under(folder):
        for tool in ("fusermount3", "fusermount"):
            if shutil.which(tool, path="/usr/bin:/bin"):
                subprocess.run([tool, "-uz", point], capture_output=True, timeout=20, check=False)
                break
    if mounts_under(folder):
        raise RuntimeError(f"{folder} still has something mounted in it: not deleted")
    shutil.rmtree(folder)


def _row(s: Shot, versions: dict[str, str]) -> dict:
    return {"terminal": s.terminal, "version": versions.get(s.terminal, "unknown"), "size": s.size,
            "theme": s.theme, "mode": s.mode, "bidi": s.bidi, "scene": s.scene, "file": s.file}


def take(sb: Sandbox, sess: Session, s: Shot, n: int, exe: Path, versions: dict[str, str],
         kitty_config: Path | None) -> dict:
    """One shot: a fresh sandbox, a fresh terminal window running the screen, a capture."""
    import sys

    row = _row(s, versions)
    cols, rows = parse_size(s.size)
    sbx.prepare(sb)
    shot_dir = sess.folder / "shots" / str(n)
    shot_dir.mkdir(parents=True, mode=0o700)
    spec = shot_dir / "spec.json"
    ready, quit_ = shot_dir / "ready.json", shot_dir / "quit"
    env = sb.env(s.bidi)
    if os.environ.get("TN_AUDIT_APP"):
        env["TN_AUDIT_APP"] = os.environ["TN_AUDIT_APP"]
    spec.write_text(json.dumps({"env": env, "config": str(sb.config), "dry_run": sb.dry_run, "bidi": s.bidi,
                                "theme": s.theme, "scene": s.scene, "ready": str(ready), "quit": str(quit_)}),
                    encoding="utf-8")
    cmd = screen_cmd(sys.executable, sbx.TUI, spec)
    title = f"tn-audit-shot-{n}"
    tenv = sess.env()
    if s.terminal == "kitty":
        argv, klass = kitty_argv(exe, cols, rows, title, cmd, kitty_config), "kitty"
    else:
        keyfile = sess.home / ".config" / "glib-2.0" / "settings" / "keyfile"
        keyfile.write_text(ptyxis_keyfile(cols, rows, s.theme), encoding="utf-8")
        argv, klass = ptyxis_argv(exe, title, cmd), "ptyxis"
    log = shot_dir / "term.log"
    with open(log, "wb") as lf:
        proc = subprocess.Popen(argv, env=tenv, cwd=sess.home, stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
                                start_new_session=True)
    try:
        deadline = time.monotonic() + READY_TIMEOUT
        while time.monotonic() < deadline and not ready.exists() and proc.poll() is None:
            time.sleep(0.1)
        if not ready.exists():
            row["error"] = "the screen didn't get ready" + (" (the terminal exited)" if proc.poll() is not None
                                                             else f" in {READY_TIMEOUT:.0f} s")
            row["log"] = _tail(log)
            return row
        time.sleep(0.2)
        seen = json.loads(ready.read_text(encoding="utf-8"))
        if "error" in seen:
            row["error"] = "the screen failed: " + seen["error"]
            return row
        row["cells_seen"] = f"{seen['cols']}x{seen['rows']}"
        row["term_env"] = seen.get("term_env", {})
        row["screen"] = seen.get("screen")
        wid = find_window(tenv, proc.pid, klass)
        if wid is None:
            row["error"] = "no window found to capture"
            return row
        time.sleep(0.8)            # the terminal draws what the screen wrote
        dest = sb.out / s.file
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = shot_dir / "shot.png"
        r = _x(tenv, "import", "-window", wid, "-strip", f"png:{tmp}", timeout=30)
        if r.returncode != 0 or not tmp.exists():
            row["error"] = "import failed: " + evidence.redact(r.stderr.strip())
            return row
        shutil.copyfile(tmp, dest)
        row["image_px"] = image_size(dest)
        row["rows_read"] = read_rows(tenv, dest, seen["cols"], seen["rows"], seen.get("regions") or {},
                                     s.terminal, kitty_config is not None)
        return row
    finally:
        quit_.touch()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            pass
        _kill_group(proc, grace=2)


def image_size(png: Path) -> list[int]:
    """(width, height) from the PNG's IHDR."""
    with open(png, "rb") as f:
        head = f.read(24)
    return [int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")]


# ------------------------------------------------------- reading colours

def top_colours(env: dict[str, str], png: Path, box: tuple[int, int, int, int], n: int = 3) -> list[dict]:
    """The `n` most common colours in a box (x, y, w, h) of the PNG, with their share of pixels."""
    x, y, w, h = box
    r = _x(env, "magick", str(png), "-crop", f"{w}x{h}+{x}+{y}", "+repage", "-format", "%c", "histogram:info:-")
    found = []
    for line in r.stdout.splitlines():
        m = re.match(r"\s*(\d+):.*?(#[0-9A-Fa-f]{6})", line)
        if m:
            found.append((int(m[1]), m[2].lower()))
    total = sum(c for c, _ in found) or 1
    found.sort(reverse=True)
    return [{"colour": c, "share": round(k / total, 3)} for k, c in found[:n]]


def read_rows(env: dict[str, str], png: Path, cols: int, rows: int, regions: dict, terminal: str,
              own_config: bool) -> dict | None:
    """The header and the footer as drawn, in the cells the screen says they cover (`regions`:
    name -> [x, y, width, height] in cells; a docked panel can narrow the footer): the most
    common colour (the background), the next most common (mostly the text; letters' edges blend
    the two) and their contrast. Only where the grid's place in the image is known: kitty with
    its defaults (no padding, no title bar; the grid starts at the top left, cells are whole
    pixels, a leftover pixel or two may follow at the right and bottom). Elsewhere None (Ptyxis
    draws a title bar and padding)."""
    if terminal != "kitty" or own_config or not regions:
        return None
    w, h = image_size(png)
    cw, ch = w // cols, h // rows
    if ch < 8 or cw < 4:
        return None
    out = {}
    for name, (x, y, rw, rh) in sorted(regions.items()):
        if rw <= 0 or rh <= 0:
            continue
        top = top_colours(env, png, (x * cw, y * ch, rw * cw, rh * ch), n=6)
        out[name] = {"cells": [x, y, rw, rh], **row_reading(top)}
    return out or None


def row_reading(top: list[dict]) -> dict:
    """(background, the most common other colour, their contrast) from a row's colour counts."""
    if len(top) < 2:
        return {"colours": top, "background": top[0]["colour"] if top else None, "text": None, "contrast": None}
    bg, text = top[0]["colour"], top[1]["colour"]
    return {"colours": top, "background": bg, "text": text, "contrast": round(svg.contrast(text, bg), 2)}


def summary(index: list[dict], session: dict, problem: str | None = None) -> str:
    lines = ["# The screen in real terminals", "",
             ("Each PNG is a real terminal window (headless GNOME compositor, Xwayland) running the screen on the "
              "harness's synthetic data. Rendering only: no keys or clicks; tabs and the keys panel are opened "
              "in-process, the way the `1`-`4` keys and `?` do."), "",
             (f"Compositor: {session['compositor']}; monitor {session['monitor']}; font: {session['font']}; "
              f"kitty config: {session['kitty_config']}."), "",
             *([f"**The job stopped early:** {problem}", ""] if problem else []),
             "| terminal | version | mode | theme | bidi | size (cells seen) | scene | file | footer contrast |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in index:
        footer = ((r.get("rows_read") or {}).get("footer") or {}).get("contrast") or ""
        shown = r.get("file") if "error" not in r else f"ERROR: {r['error']}"
        lines.append(f"| {r['terminal']} | {r['version']} | {r['mode']} | {r['theme']} | {r['bidi']} | "
                     f"{r['size']} ({r.get('cells_seen', '?')}) | {r['scene']} | {shown} | {footer} |")
    wrong = [r for r in index if r.get("cells_seen") and r["cells_seen"] != r["size"]]
    if wrong:
        lines += ["", (f"{len(wrong)} shot(s) where the terminal gave the screen another size than asked "
                       "(window manager or font metrics): see `cells_seen` in index.json.")]
    lines += ["", ("Footer contrast: the footer's cells as drawn, their most common colour (the background) against "
                   "their next most common (mostly the text); kitty with its defaults only, where the cell grid's "
                   "place in the image is known. It is a reading, not the R6 measure: R6 uses the `colours` job's "
                   "per-state pairs."), ""]
    return "\n".join(lines)


# ------------------------------------------------------- the child side

def regions(app) -> dict[str, list[int]]:
    """Where the header and the footer are drawn, in cells (what is visible of them)."""
    from textual.css.query import NoMatches
    from textual.errors import NoWidget
    from textual.widgets import Footer, Header

    out = {}
    for name, kind in (("header", Header), ("footer", Footer)):
        try:
            r = app.screen.find_widget(app.screen.query_one(kind)).region
        except (NoMatches, NoWidget):       # not on this screen, or not drawn: nothing to read
            continue
        out[name] = [r.x, r.y, r.width, r.height]
    return out

def screen(spec_path: str) -> int:
    """Inside the terminal: the real screen on the sandbox, switched to the shot's theme and
    scene, then 'ready' (its size and the terminal's variables) until told to quit."""
    import asyncio

    from . import appsrc

    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    env = dict(spec["env"])
    term_env = {k: os.environ[k] for k in TERMINAL_VARS if k in os.environ}
    env.update(term_env)
    os.environ.clear()
    os.environ.update(env)
    tempfile.tempdir = None
    ready, quit_ = Path(spec["ready"]), Path(spec["quit"])
    appsrc.use(env.get(appsrc.ENV))
    from telenotify_tui import rtl
    from telenotify_tui.app import TeleNotifyApp
    from telenotify_tui.paths import resolve

    appsrc.verify_loaded()
    rtl.set_mode(spec["bidi"])
    app = TeleNotifyApp(resolve(Path(spec["config"]), spec["dry_run"]))

    async def settle(pilot) -> None:
        for _ in range(3):
            await pilot.pause()
            try:
                await asyncio.wait_for(app.workers.wait_for_complete(), 10)
            except TimeoutError:
                pass
        await pilot.pause()

    async def auto(pilot) -> None:
        try:
            from . import drive

            await settle(pilot)
            app.theme = THEMES[spec["theme"]]
            scene = spec["scene"]
            tabs = {"1-dashboard": "dashboard", "2-review": "review", "3-search": "search", "4-settings": "settings"}
            if scene in tabs:
                app.action_show(tabs[scene])
            elif scene == "keys-panel":
                app.action_show_help_panel()
            await settle(pilot)
            if scene == "2-review":
                from telenotify_tui.screens.review import ReviewPane
                await asyncio.sleep(ReviewPane.DECIDE_DELAY)     # as a person would see it
            drive.no_blink(app)
            await pilot.pause(0.5)
            info = {"cols": app.size.width, "rows": app.size.height, "theme": app.theme,
                    "screen": type(app.screen).__name__, "term_env": term_env, "regions": regions(app)}
        except Exception as e:  # noqa: BLE001  (reported to the harness, which records it)
            info = {"error": evidence.redact(f"{type(e).__name__}: {e}")}
        ready.write_text(json.dumps(info), encoding="utf-8")
        while not quit_.exists():
            await asyncio.sleep(0.1)
        app.exit()

    with rtl.terminal_mode():
        app.run(auto_pilot=auto)
    return 0
