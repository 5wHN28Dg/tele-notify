"""Text from Telegram (chat titles, posts, alert text) is written by strangers:
anything that looks like Rich markup must be shown as is, never parsed, and
no control character in it may reach the real terminal."""

from __future__ import annotations

import json
import os
import pty
import select
import signal
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from conftest import REPO, SCHEMA, fill
from rich.text import Text
from textual.widgets import DataTable, Select, TabbedContent

from telenotify_tui.app import TeleNotifyApp

ESCAPES_POST = "before\x1b]0;PWNEDTITLE\x1b\\after\x1b[?1049l\x1b[5mBLINK c1\u009b31mEND"
ESCAPES_TITLE = "T\x1b[41mRED"


def _read_until(fd: int, out: bytearray, done, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end and not done(out):
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            try:
                out += os.read(fd, 65536)
            except OSError:
                return


def test_escapes_in_the_state_never_reach_the_terminal(tmp_path):
    """The real app in a real pseudo-terminal: a post and chat title full of
    escape sequences are drawn, but none of their ESC / C1 bytes are written."""
    data = tmp_path / "data"
    data.mkdir()
    db = sqlite3.connect(data / "state.sqlite3")
    db.executescript(SCHEMA)
    fill(db, int(time.time()))
    db.execute("UPDATE reviews SET text = ?, chat_title = ?", (ESCAPES_POST, ESCAPES_TITLE))
    db.execute("UPDATE processed SET chat_title = ?", (ESCAPES_TITLE,))
    db.commit()
    db.close()
    cfg = json.loads((REPO / "tests" / "keywords.json").read_text(encoding="utf-8"))
    cfg["data_dir"] = str(data)
    config = tmp_path / "config.json"
    config.write_text(json.dumps(cfg), encoding="utf-8")

    pid, fd = pty.fork()
    if pid == 0:   # the child: the screen, as a user would start it
        os.environ.update(TERM="xterm-256color", COLUMNS="140", LINES="40",
                          TELENOTIFY_LABELS=str(tmp_path / "labels.jsonl"), TELENOTIFY_BIN="/nonexistent")
        os.execv(sys.executable, [sys.executable, "-m", "telenotify_tui", "--config", str(config)])
    out = bytearray()
    try:
        _read_until(fd, out, lambda o: b"Dashboard" in o and b"RED" in o, 20)
        time.sleep(0.5)
        os.write(fd, b"2")                              # the Review tab shows the post itself
        _read_until(fd, out, lambda o: b"BLINK" in o and b"END" in o, 20)
        time.sleep(0.5)
        _read_until(fd, out, lambda o: False, 0.5)
    finally:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        os.close(fd)
    assert b"PWNEDTITLE" in out and b"BLINK" in out and b"RED" in out, "the data wasn't drawn"
    for raw in (b"\x1b]0;PWNEDTITLE", b"PWNEDTITLE\x1b", b"\x1b[41mRED", b"after\x1b", b"\x1b[5mBLINK",
                b"\xc2\x9b", b"\x9b31m"):
        assert raw not in out, (raw, bytes(out[max(0, out.find(raw) - 30):out.find(raw) + 30]))

# a stray closing tag crashes Rich's markup parser; a style tag would restyle the row
MARKUP = ["a [/] b", "a [/x] b", "[red]x"]


@pytest.fixture(autouse=True)
def labels(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TELENOTIFY_LABELS", str(tmp_path / "corpus" / "labels.jsonl"))


def poison(state: Path, s: str) -> None:
    db = sqlite3.connect(state)
    db.execute("UPDATE processed SET chat_title = ?, role = ?, reasons = ?, raw_text = ?", (s, s, s, s))
    db.execute("UPDATE reviews SET chat_title = ?, text = ?, role = ?, reasons = ?", (s, s, s, s))
    db.execute("UPDATE chats SET title = ?, kind = ?", (s, s))
    db.execute("INSERT INTO alerts (at, text) VALUES (?, ?)", (int(time.time()), s))
    db.commit()
    db.close()


def cells(table: DataTable) -> list:
    return [c for i in range(table.row_count) for c in table.get_row_at(i)]


def assert_literal(table: DataTable, s: str) -> None:
    shown = [c for c in cells(table) if isinstance(c, Text) and c.plain == s]
    assert shown, f"{s!r} not shown literally in #{table.id}: {cells(table)!r}"
    assert all(not c.spans for c in shown)
    assert not [c for c in cells(table) if isinstance(c, str) and "[" in c]   # nothing left to parse


async def settle(app, pilot) -> None:
    for _ in range(3):
        await pilot.pause()
        await app.workers.wait_for_complete()
    await pilot.pause()


@pytest.mark.parametrize("s", MARKUP)
async def test_every_tab_shows_markup_literally(paths, state, s):
    poison(state, s)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)                                  # dashboard
        for view in "fca":
            await pilot.press(view)
            await settle(app, pilot)
        assert_literal(app.query_one("#feed", DataTable), s)      # chat title, role, reason
        assert_literal(app.query_one("#chat-stats", DataTable), s)
        assert_literal(app.query_one("#alerts", DataTable), s)
        assert_literal(app.query_one("#health", DataTable), s)

        await pilot.press("2")                                    # review queue, then history
        await settle(app, pilot)
        await pilot.press("s")
        await settle(app, pilot)
        await pilot.press("h")
        await settle(app, pilot)
        assert_literal(app.query_one("#review-history", DataTable), s)

        await pilot.press("3")                                    # search, with the chat filter
        await settle(app, pilot)
        assert_literal(app.query_one("#results", DataTable), s)
        sel = app.query_one("#chat", Select)
        prompts = [p for p, _v in sel._options]
        assert any(isinstance(p, Text) and p.plain == s for p in prompts), prompts

        await pilot.press("4")                                    # settings, chat list
        await settle(app, pilot)
        app.query_one("#sections", TabbedContent).active = "sec-chats"
        await settle(app, pilot)
        assert_literal(app.query_one("#chat-table", DataTable), s)
        assert app.is_running


async def test_notifications_title_and_notes_are_cleaned(paths):
    """Messages that carry paths, errors and config values: no control characters."""
    from dataclasses import replace

    from textual.widgets import Static

    from telenotify_tui.screens.review import ReviewPane

    app = TeleNotifyApp(replace(paths, config=paths.config.with_name("co\x1b]0;X\x07nfig.json")), refresh_seconds=0)
    async with app.run_test(size=(140, 45)) as pilot:
        await settle(app, pilot)
        assert "\x1b" not in app.sub_title and "\x07" not in app.sub_title and "co]0;Xnfig.json" in app.sub_title
        app.query_one(ReviewPane).notify("bad \x1b[2J path \x9b1m", title="t\x1bx")
        await pilot.pause()
        assert [(n.message, n.title) for n in app._notifications if n.message.startswith("bad")] == \
            [("bad [2J path 1m", "tx")]
        app.query_one(ReviewPane).note("can't write /x\x1b[?1049l/state")
        assert str(app.query_one("#review-note", Static).content) == "can't write /x[?1049l/state"


async def test_pasted_text_loses_control_characters(paths):
    from textual.widgets import TextArea

    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("3")
        await settle(app, pilot)
        area = app.query_one("#paste-text", TextArea)
        area.text = "job\x1b]0;PWNED\x07 in basra\r\nمهندس\x9b"
        await pilot.pause()
        assert area.text == "job]0;PWNED in basra\nمهندس"
