"""R9 hostile text: the real screen in a pseudo-terminal (the method of
tests/test_untrusted_text.py), with markup and escape-sequence payloads in
posts, chat titles, reasons, alerts, links, keywords, config values, the
config path (header) and the fake programs' output, driven through the tabs,
dialogs, drop-down lists, the keys panel and notifications.

Every payload carries a marker "Zq<place><kind>". The output stream is
searched for the payloads' own escape sequences and C1 bytes (they must
never appear) and, with the screen's own escape sequences taken out, for
each marker and whether its brackets are shown as text. Mouse clicks land on
widget centres found by a headless run of the same screen at the same size.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import re
import select
import signal
import sqlite3
import struct
import sys
import termios
import time
from pathlib import Path

from . import data, drive, evidence, fakes
from .sandbox import Sandbox

SIZE = (140, 40)
KINDS = {
    "b": "[b]{m}[/b]",
    "l": "[link=https://example.com/{m}]{m}[/link]",
    "c": "[@click=app.quit]{m}[/]",
    "u": "{m}[",
    "a": "\x1b[5m\x1b[41m{m}",
    "q": "\u009b31m{m}",
    "o": "\x1b]8;;https://example.com/{m}\x1b\\{m}\x1b]8;;\x1b\\",
    "p": "\x1b]52;c;WnFQV05FRA==\x07{m}",
    "t": "\x1b]0;{m}\x07",
}
PLACES = {"PO": "post text", "TI": "chat titles", "RS": "roles and reasons", "AL": "alerts", "LK": "review link",
          "KW": "a keyword", "CF": "a config value (tdlib_path)", "SP": "telenotify output",
          "SY": "systemctl is-active output", "SF": "systemctl error output", "HD": "the config path (header)"}
# the payloads' own bytes: none may reach the terminal
RAW = {"CSI blink": b"\x1b[5m", "CSI red background": b"\x1b[41m", "C1 CSI (UTF-8)": b"\xc2\x9b31m",
       "C1 CSI (byte)": b"\x9b31m", "OSC 8 link": b"\x1b]8;;https://example.com/Zq",
       "OSC 52 clipboard": b"\x1b]52;c;WnFQV05FRA", "OSC 0 title": b"\x1b]0;Zq"}
_SCREEN_ESC = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]")


def payload(place: str) -> str:
    return " ".join(t.format(m=f"Zq{place}{k}") for k, t in KINDS.items())


def poison(sb: Sandbox) -> Path:
    """Hostile data in the sandbox; returns the config path to start the screen with."""
    db = sqlite3.connect(sb.state)
    db.execute("UPDATE processed SET chat_title = ?, role = ?, reasons = ?, raw_text = ?",
               (payload("TI"), payload("RS"), payload("RS"), payload("PO")))
    db.execute("UPDATE reviews SET chat_title = ?, text = ?, role = ?, reasons = ?, link = ?",
               (payload("TI"), payload("PO"), payload("RS"), payload("RS"), "https://example.com/" + payload("LK")))
    db.execute("UPDATE chats SET title = ?", (payload("TI"),))
    db.execute("UPDATE alerts SET text = ?", (payload("AL"),))
    db.commit()
    db.close()
    folder = sb.root / ("cfg " + payload("HD").replace("/", "|"))
    folder.mkdir()
    cfg = data.config(sb.data_dir, dry_run=sb.dry_run, tdlib_path=payload("CF"))
    cfg["level_keywords_en"] = [payload("KW"), *cfg["level_keywords_en"]]
    config = folder / "config.json"
    data.write_config(config, cfg)
    svc = fakes.services(dry_run="active")
    svc["tele-notify-dryrun"].update(say=payload("SY"), fail=payload("SF"))
    fakes.set_services(sb.root, svc)
    fakes.set_telenotify(sb.root, say=payload("SP"))
    return config


async def _locate(sb: Sandbox, config: Path) -> dict[str, tuple[int, int]]:
    """Widget centres (1-based terminal cells) from a headless run at the same size."""
    from textual.widgets import TabbedContent

    from telenotify_tui.app import TeleNotifyApp
    from telenotify_tui.paths import resolve

    app = TeleNotifyApp(resolve(config, sb.dry_run), refresh_seconds=0)
    where: dict[str, tuple[int, int]] = {}

    def centre(name: str, w, first_row: bool = False) -> None:
        r = w.region
        where[name] = (r.x + max(r.width // 2, 0) + 1, (r.y if first_row else r.y + r.height // 2) + 1)

    async with drive.run_test(app, SIZE) as pilot:
        await drive.settle(app, pilot)
        for k in "caf":                     # the same views as the pty run before its click
            await pilot.press(k)
            await drive.settle(app, pilot)
        # the bot box may be pushed out of view by the long hostile status text, so the
        # restart button is reached with shift+tab from the feed (focused), not clicked
        chain = app.screen.focus_chain
        back = chain.index(app.query_one("#feed")) - chain.index(app.query_one("#svc-restart"))
        where["restart-back"] = (back, 0)
        centre("restart", app.query_one("#svc-restart"))
        await pilot.press("3")
        await drive.settle(app, pilot)
        centre("chat-select", app.query_one("#chat"))
        await pilot.press("4")
        await drive.settle(app, pilot)
        centre("keyword", app.query_one("#editor-roles #kw-items"), first_row=True)
        tabs = app.query_one("#sections", TabbedContent)
        centre("section-chats", tabs.get_tab("sec-chats"))
        centre("section-bot", tabs.get_tab("sec-bot"))
    return where


def _click(x: int, y: int) -> bytes:
    return f"\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m".encode()


def steps(at: dict[str, tuple[int, int]]) -> list[tuple[str, bytes, float]]:
    """(what is shown, bytes to send, seconds to read)."""
    return [
        ("dashboard: feed, header, bot status", b"", 4.0),
        ("dashboard: chats", b"c", 1.5),
        ("dashboard: alerts and health", b"a", 1.5),
        ("dashboard: feed again", b"f", 1.0),
        # restart comes before the drop-down: the first click after closing one is ignored
        ("dashboard: restart dialog", b"\x1b[Z" * at["restart-back"][0] + b"\r", 1.5),
        ("dashboard: restart fails with systemctl's output", b"y", 3.0),
        ("dashboard: period drop-down", b"p", 1.5),
        ("close the drop-down", b"\x1b", 1.0),
        ("review: the post", b"2", 2.0),
        ("review: what the filter read (dialog)", b"r", 3.0),
        ("close the dialog", b"\x1b", 1.0),
        ("review: open the link (note)", b"o", 1.5),
        ("review: history", b"h", 1.5),
        ("review: back", b"h", 1.0),
        ("search: results", b"3", 2.0),
        ("search: chat drop-down", _click(*at["chat-select"]), 1.5),
        ("close the drop-down", b"\x1b", 1.0),
        ("settings: keyword lists", b"4", 2.0),
        ("settings: pick the hostile keyword", _click(*at["keyword"]), 1.0),
        ("settings: delete it (notification)", b"x", 1.5),
        ("settings: chat list", _click(*at["section-chats"]), 1.5),
        ("settings: bot options (config values)", _click(*at["section-bot"]), 1.5),
        ("settings: corpus test result (dialog)", b"t", 3.0),
        ("close the dialog", b"\x1b", 1.0),
        ("command palette", b"\x10", 1.5),
        ("keys panel", b"keys\r", 2.0),
    ]


def _read(fd: int, out: bytearray, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ready, _, _ = select.select([fd], [], [], 0.05)
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            out += chunk


def check(chunk: bytes) -> dict:
    raw = {}
    for name, seq in RAW.items():
        i = chunk.find(seq)
        if i >= 0:
            raw[name] = repr(chunk[max(0, i - 40):i + 60])
    plain = _SCREEN_ESC.sub(b"", chunk).decode("utf-8", "replace")
    markers = {}
    for place in PLACES:
        for k in KINDS:
            m = f"Zq{place}{k}"
            if m in plain:
                around = [plain[max(0, j - 30):j + len(m) + 10] for j in _finds(plain, m)][:3]
                markers[m] = around
    return {"payload_sequences_found": raw, "markers_seen": markers}


def _finds(s: str, sub: str) -> list[int]:
    out, i = [], s.find(sub)
    while i >= 0:
        out.append(i)
        i = s.find(sub, i + 1)
    return out


def one(sb: Sandbox, bidi: str) -> dict:
    from . import sandbox as sbx

    sbx.prepare(sb)
    sbx.set_bidi(bidi)
    config = poison(sb)
    at = asyncio.run(_locate(sb, config))
    plan = steps(at)
    env = dict(os.environ, COLUMNS=str(SIZE[0]), LINES=str(SIZE[1]), TELENOTIFY_BIDI=bidi)
    pid, fd = pty.fork()
    if pid == 0:   # the child: the screen as a user starts it, in this pseudo-terminal
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", SIZE[1], SIZE[0], 0, 0))
        os.execve(sys.executable, [sys.executable, "-m", "audit.child", "app", "--config", str(config),
                                   "--dry-run", "--bidi", bidi], env)
    stream = bytearray()
    rows = []
    try:
        for what, send, secs in plan:
            start = len(stream)
            if send:
                os.write(fd, send)
            _read(fd, stream, secs)
            rows.append({"step": what, "sent": repr(send), **check(bytes(stream[start:]))})
        alive = os.waitpid(pid, os.WNOHANG) == (0, 0)
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        os.close(fd)
    whole = check(bytes(stream))
    seen = set(whole["markers_seen"])
    out = sb.out / "hostile" / f"bidi-{bidi}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "stream.bin").write_bytes(evidence.redact(bytes(stream).decode("utf-8", "surrogateescape"))
                                     .encode("utf-8", "surrogateescape"))
    rec = {"bidi": bidi, "size": f"{SIZE[0]}x{SIZE[1]}", "screen_alive_at_end": alive,
           "payload_sequences_found": whole["payload_sequences_found"],
           "markers_never_seen": sorted(f"Zq{p}{k}" for p in PLACES for k in KINDS if f"Zq{p}{k}" not in seen),
           "places": PLACES, "payloads": {k: repr(v) for k, v in KINDS.items()}, "steps": rows}
    evidence.write_json(out / "steps.json", rec)
    return {"bidi": bidi, "payload_sequences_found": len(rec["payload_sequences_found"]),
            "markers_never_seen": len(rec["markers_never_seen"]), "alive": alive}


def collect(sb: Sandbox, bidis: list[str]) -> dict:
    return {"runs": [one(sb, b) for b in bidis], "note": json.dumps(
        "a marker never seen may be cut off by a column; see steps.json")}
