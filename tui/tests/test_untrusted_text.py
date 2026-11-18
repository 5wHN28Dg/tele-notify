"""Text from Telegram (chat titles, posts, alert text) is written by strangers:
anything that looks like Rich markup must be shown as is, never parsed."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from rich.text import Text
from textual.widgets import DataTable, Select, TabbedContent

from telenotify_tui.app import TeleNotifyApp

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
