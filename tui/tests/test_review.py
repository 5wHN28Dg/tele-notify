import dataclasses
import json
import os
import sqlite3
from pathlib import Path

import pytest
from conftest import UNCLEAR
from textual.widgets import Button, DataTable, OptionList, Static

from telenotify_tui import bot
from telenotify_tui.app import TeleNotifyApp
from telenotify_tui.screens import review as review_mod
from telenotify_tui.screens.review import CheckModal, ReviewPane
from telenotify_tui.screens.review_data import labels_path, write_label
from telenotify_tui.store import Review, Store


@pytest.fixture(autouse=True)
def labels(tmp_path: Path, monkeypatch) -> Path:
    """Labels go to a temp file, never the real corpus folder."""
    path = tmp_path / "corpus" / "review-labels.jsonl"
    monkeypatch.setenv("TELENOTIFY_LABELS", str(path))
    return path


def add_pending(state: Path, msg_id: int, text: str, at: int, link: str = "") -> None:
    db = sqlite3.connect(state)
    db.execute("""INSERT INTO reviews (chat_id, msg_id, chat_title, text, reasons, role, link, status, at)
                  VALUES (-2, ?, 'Iraq Jobs', ?, 'unclear place', 'it support', ?, 'pending', ?)""",
               (msg_id, text, link, at))
    db.commit()
    db.close()


def status_of(state: Path, msg_id: int) -> str:
    db = sqlite3.connect(state)
    try:
        return db.execute("SELECT status FROM reviews WHERE msg_id = ?", (msg_id,)).fetchone()[0]
    finally:
        db.close()


def note(app) -> str:
    return str(app.query_one("#review-note", Static).content)


def read_labels(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


async def open_review(pilot):
    await pilot.press("2")
    await pilot.pause()
    await pilot.pause()


async def test_list_shows_pending_and_detail(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        ol = app.query_one("#review-list", OptionList)
        assert ol.option_count == 1
        assert "Basra Jobs" in str(ol.get_option_at_index(0).prompt)
        head = str(app.query_one("#detail-head", Static).content)
        assert "https://t.me/c/1/11" in head and "no sign of level either way" in head
        assert str(app.query_one("#detail-text", Static).content) == UNCLEAR


async def test_send_marks_to_send_and_writes_true_label(paths, state, labels):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        await pilot.press("s")
        await pilot.pause()
        assert status_of(state, 11) == "to_send"
        assert "dry run" in note(app)
        assert app.query_one("#review-empty").display
        assert "Nothing to review" in str(app.query_one("#review-empty", Static).content)
    lines = read_labels(labels)
    assert lines == [{"id": "-1:11", "text": UNCLEAR, "label": "TRUE", "source": "review", "at": lines[0]["at"]}]
    assert os.stat(labels.parent).st_mode & 0o777 == 0o700


async def test_send_outside_dry_run_says_bot_sends(paths, state):
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        await pilot.press("s")
        await pilot.pause()
        assert "the bot will send it in a few seconds" in note(app)


async def test_discard_second_item_writes_false(paths, state, labels, now):
    add_pending(state, 7, "second post", now - 10)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        ol = app.query_one("#review-list", OptionList)
        assert ol.option_count == 2
        await pilot.press("n")
        await pilot.pause()
        assert str(app.query_one("#detail-text", Static).content) == "second post"
        await pilot.press("d")
        await pilot.pause()
        assert status_of(state, 7) == "discarded"
        assert status_of(state, 11) == "pending"
        assert ol.option_count == 1
    assert [(l["id"], l["label"]) for l in read_labels(labels)] == [("-2:7", "FALSE")]


async def test_decided_elsewhere_shows_note_and_drops_it(paths, state, labels):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        with Store(state) as st:  # another window decides first
            rid = st.reviews("pending")[0].id
            assert st.decide(rid, "discarded")
        await pilot.press("s")
        await pilot.pause()
        assert "already decided elsewhere" in note(app)
        assert app.query_one("#review-list", OptionList).option_count == 0
        assert status_of(state, 11) == "discarded"
    assert not labels.exists()


async def test_live_reload_keeps_selection_and_open_post(paths, state, now):
    add_pending(state, 7, "second post", now - 10)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        await pilot.press("n")
        await pilot.pause()
        add_pending(state, 3, "older newcomer", now - 500)  # sorts first
        app.query_one(ReviewPane).reload()
        await pilot.pause()
        ol = app.query_one("#review-list", OptionList)
        assert ol.option_count == 3
        assert ol.highlighted_option.id == str(
            next(r.id for r in Store(state).reviews("pending") if r.msg_id == 7))
        assert str(app.query_one("#detail-text", Static).content) == "second post"


def test_label_file_dedupes_by_id(labels):
    r = Review(id=1, chat_id=-1, msg_id=11, chat_title="c", text="t", reasons="", role="", link="",
               status="pending", at=0)
    labels.parent.mkdir(parents=True)
    labels.write_text('{"id": "-5:1", "text": "keep", "label": "TRUE"}\nnot json\n', encoding="utf-8")
    assert labels_path() == labels
    write_label(r, "to_send", now=1)
    write_label(r, "discarded", now=2)
    lines = labels.read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ['{"id": "-5:1", "text": "keep", "label": "TRUE"}', "not json"]
    assert [json.loads(l) for l in lines[2:]] == [
        {"id": "-1:11", "text": "t", "label": "FALSE", "source": "review", "at": 2}]


async def test_open_calls_browser(paths, monkeypatch):
    opened = []
    monkeypatch.setattr(review_mod.webbrowser, "open", lambda url: opened.append(url) or True)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        await pilot.press("o")
        await pilot.pause()
    assert opened == ["https://t.me/c/1/11"]


async def test_open_without_link_notes_it(paths, state, now, monkeypatch):
    monkeypatch.setattr(review_mod.webbrowser, "open", lambda url: pytest.fail("no link to open"))
    db = sqlite3.connect(state)
    db.execute("UPDATE reviews SET link = '' WHERE msg_id = 11")
    db.commit()
    db.close()
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        await pilot.press("o")
        await pilot.pause()
        assert "no link" in note(app)


async def test_filter_read_shows_modal(paths, monkeypatch):
    calls = []

    async def fake_check(binary, config, text, scrape=False, show_text=False):
        calls.append((text, scrape, show_text))
        return bot.Verdict("review", ["role", "no sign of level"], text="post text\nfrom the link")

    monkeypatch.setattr(bot, "check", fake_check)
    app = TeleNotifyApp(dataclasses.replace(paths, binary=Path("/bin/true")), refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, CheckModal)
        assert "verdict: review" in str(app.screen.query_one("#check-verdict", Static).content)
        assert "from the link" in str(app.screen.query_one("#check-text", Static).content)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, CheckModal)
    assert calls == [(UNCLEAR, True, True)]


async def test_filter_read_disabled_without_binary(paths):
    app = TeleNotifyApp(dataclasses.replace(paths, binary=None), refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        assert app.query_one("#btn-check", Button).disabled
        assert "no telenotify program" in note(app)
        await pilot.press("r")
        await pilot.pause()
        assert not isinstance(app.screen, CheckModal)


async def test_history_toggle(paths, state):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("h")
        await pilot.pause()
        table = app.query_one("#review-history", DataTable)
        assert table.display and table.row_count == 2  # the one just approved and the older sent one
        assert table.get_row_at(0)[1] == "approved"
        await pilot.press("h")
        await pilot.pause()
        assert not table.display


async def test_missing_state_file(paths, tmp_path):
    app = TeleNotifyApp(dataclasses.replace(paths, state=tmp_path / "nope.sqlite3"), refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        assert "No state file yet" in str(app.query_one("#review-empty", Static).content)
        await pilot.press("s", "d", "o", "h", "h")


async def test_missing_reviews_table(paths, tmp_path):
    db_path = tmp_path / "old.sqlite3"
    sqlite3.connect(db_path).execute("CREATE TABLE meta (key TEXT, value TEXT)").connection.close()
    app = TeleNotifyApp(dataclasses.replace(paths, state=db_path), refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        assert "no review queue" in str(app.query_one("#review-empty", Static).content)
