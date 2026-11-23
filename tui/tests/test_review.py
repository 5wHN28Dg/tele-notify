import dataclasses
import json
import multiprocessing
import os
import sqlite3
import time
from pathlib import Path

import pytest
from conftest import UNCLEAR
from textual.widgets import Button, DataTable, OptionList, Static

from telenotify_tui import bot
from telenotify_tui.app import TeleNotifyApp
from telenotify_tui.postview import PostView
from telenotify_tui.screens import dashboard_data as dd
from telenotify_tui.screens import review as review_mod
from telenotify_tui.screens.review import CheckModal, ReviewPane
from telenotify_tui.screens.review_data import labels_path, write_label
from telenotify_tui.store import BUSY_NOTE, Review, Store


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
        assert "sent to the channel in 30 s (u undoes)" in note(app) or "in 29 s" in note(app)


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
        assert str(table.get_row_at(0)[1]) == "approved"
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


# ------------------------------------------------- a locked or broken state file

def lock(state: Path) -> sqlite3.Connection:
    """Another process (the bot) holding the write lock."""
    db = sqlite3.connect(state, isolation_level=None)
    db.execute("BEGIN EXCLUSIVE")
    return db


async def test_locked_state_file_is_a_note_not_a_crash(paths, state, labels, monkeypatch):
    monkeypatch.setattr(review_mod, "UI_TIMEOUT", 0.1)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        db = lock(state)
        try:
            await pilot.press("s")                               # the decision can't be written
            await pilot.pause()
            assert "NOT recorded" in note(app) and "locked" in note(app)
            app.query_one(ReviewPane).reload()                   # nor can the queue be read
            await pilot.pause()
            assert BUSY_NOTE in note(app)   # the Dashboard's wording for a lock
            assert "#11 NOT recorded" in note(app)               # ...beside, never instead of, the failed decision
            assert app.is_running
        finally:
            db.execute("ROLLBACK")
            db.close()
        assert status_of(state, 11) == "pending"
        app.query_one(ReviewPane).reload()                       # lock gone: the failure is still said
        await pilot.pause()
        assert "#11 NOT recorded" in note(app) and BUSY_NOTE not in note(app)
        await pilot.press("s")
        await pilot.pause()
        assert status_of(state, 11) == "to_send"
        assert "NOT recorded" not in note(app)                   # until the decision is made
    assert len(read_labels(labels)) == 1


async def test_ui_reads_give_up_quickly_on_a_lock(paths, state):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        db = lock(state)
        try:
            t = time.monotonic()
            app.query_one(ReviewPane).reload()
            assert time.monotonic() - t < 3                      # not the 10 s busy timeout
        finally:
            db.execute("ROLLBACK")
            db.close()


async def test_corrupt_state_file_is_a_note_not_a_crash(paths, state):
    state.write_bytes(b"not a database" * 100)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        assert "Can't read the state file" in note(app)   # said once, in the note (#106)
        assert "Nothing to show" in str(app.query_one("#review-empty", Static).content)
        await pilot.press("h")
        await pilot.pause()
        await pilot.press("h", "s", "d")
        await pilot.pause()
        assert app.is_running


# ------------------------------------------- the list changing under the open post

async def test_open_post_decided_elsewhere_selects_nothing(paths, state, labels, now):
    add_pending(state, 7, "second post", now - 10)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        assert str(app.query_one("#detail-text", Static).content) == UNCLEAR
        with Store(state) as st:  # another window decides the open post
            rid = next(r.id for r in st.reviews("pending") if r.msg_id == 11)
            assert st.decide(rid, "discarded")
        app.query_one(ReviewPane).reload()
        await pilot.pause()
        assert "decided elsewhere; pick the next one" in note(app)
        assert app.query_one("#review-list", OptionList).highlighted is None
        assert str(app.query_one("#detail-text", Static).content) == ""
        await pilot.press("s", "d")                              # nothing open: nothing decided
        await pilot.pause()
        assert status_of(state, 7) == "pending"
        app.query_one(ReviewPane).reload()                       # a later refresh doesn't pick one either
        await pilot.pause()
        assert app.query_one("#review-list", OptionList).highlighted is None
        await pilot.press("n")                                   # the reader picks: the first post
        await pilot.pause()
        assert str(app.query_one("#detail-text", Static).content) == "second post"
        await pilot.press("s")
        await pilot.pause()
        assert status_of(state, 7) == "to_send"
    assert [l["id"] for l in read_labels(labels)] == ["-2:7"]


async def test_open_post_changed_is_redrawn(paths, state):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        db = sqlite3.connect(state)
        db.execute("UPDATE reviews SET text = 'edited text', reasons = 'new reason' WHERE msg_id = 11")
        db.commit()
        db.close()
        app.query_one(ReviewPane).reload()
        await pilot.pause()
        assert str(app.query_one("#detail-text", Static).content) == "edited text"
        assert "new reason" in str(app.query_one("#detail-head", Static).content)


# ----------------------------------------------------------- the labels file

def review(msg_id: int) -> Review:
    return Review(id=msg_id, chat_id=-1, msg_id=msg_id, chat_title="c", text=f"t{msg_id}", reasons="",
                  role="", link="", status="pending", at=0)


def test_label_file_with_bad_bytes_keeps_them(labels):
    labels.parent.mkdir(parents=True)
    labels.write_bytes(b'{"id": "-5:1", "text": "keep", "label": "TRUE"}\n\xff\xfe broken\n')
    write_label(review(11), "to_send", now=1)
    lines = labels.read_bytes().splitlines()
    assert lines[:2] == [b'{"id": "-5:1", "text": "keep", "label": "TRUE"}', b"\xff\xfe broken"]
    assert json.loads(lines[2])["id"] == "-1:11"


async def test_label_error_after_decision_is_a_note(paths, state, monkeypatch):
    def broken(*_a, **_k):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(review_mod, "write_label", broken)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        await pilot.press("s")
        await pilot.pause()
        assert "corpus label not saved" in note(app)
        assert status_of(state, 11) == "to_send"


def _write_many(path: Path, start: int) -> None:
    for i in range(start, start + 30):
        write_label(review(i), "discarded", path)


def test_two_writers_lose_no_labels(labels):
    """Two Review windows labelling at once (separate processes, as in real use)."""
    labels.parent.mkdir(parents=True)
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_write_many, args=(labels, start)) for start in (1000, 2000)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    assert len({l["id"] for l in read_labels(labels)}) == 60


# ------------------------------------------------- double taps and undo

async def test_double_tap_doesnt_decide_the_next_post_unread(paths, state, now, monkeypatch):
    monkeypatch.setattr(ReviewPane, "DECIDE_DELAY", 0.8)
    add_pending(state, 100, "second post never shown", now - 10)
    add_pending(state, 101, "third post never shown", now - 5)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        pane._shown_at -= 10                     # the first post has been read
        await pilot.press("s", "s")              # key auto-repeat / double tap
        await pilot.pause()
        assert status_of(state, 11) == "to_send"
        assert status_of(state, 100) == "pending"
        assert "approved" in note(app) and "only just appeared" in note(app)
        # a double click on Send: the same. Made 0.1 s after #100 appeared (a pilot click would
        # be judged by when it's handled, which a slow CI runner can push past the delay)
        from textual import events
        btn = app.query_one("#btn-send")
        x, y = btn.region.x + 1, btn.region.y
        for cls in (events.MouseDown, events.MouseUp):
            e = cls(None, x, y, 0, 0, 1, False, False, False, screen_x=x, screen_y=y)
            e.time = pane._shown_at + 0.1
            app.post_message(e)
        await pilot.pause()
        assert status_of(state, 100) == "pending"
        pane._shown_at -= 10                     # read now: the key works again
        await pilot.press("s")
        await pilot.pause()
        assert status_of(state, 100) == "to_send"
        assert status_of(state, 101) == "pending"


async def test_undo_send_puts_it_back_and_drops_the_label(paths, state, labels, now):
    add_pending(state, 7, "second post", now - 10)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        assert pane.check_action("undo", ()) is None          # nothing to undo yet
        await pilot.press("d")                                # discard post 11 (the older one)
        await pilot.pause()
        await pilot.press("s")                                # approve post 7
        await pilot.pause()
        assert [l["id"] for l in read_labels(labels)] == ["-1:11", "-2:7"]
        await pilot.press("u")
        await pilot.pause()
        assert status_of(state, 7) == "pending" and status_of(state, 11) == "discarded"
        assert "back in the queue" in note(app)
        assert [l["id"] for l in read_labels(labels)] == ["-1:11"]
        assert str(app.query_one("#detail-text", Static).content) == "second post"   # open again
        assert pane.check_action("undo", ()) is None          # one step only
        await pilot.press("u")
        await pilot.pause()
        assert status_of(state, 11) == "discarded"


async def test_undo_discard(paths, state, labels):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        await pilot.press("d")
        await pilot.pause()
        assert app.query_one("#btn-undo", Button).display
        await pilot.click("#btn-undo")
        await pilot.pause()
        assert status_of(state, 11) == "pending"
        assert app.query_one("#review-list", OptionList).option_count == 1
        assert not app.query_one("#btn-undo", Button).display
    assert read_labels(labels) == []


async def test_undo_after_the_bot_sent_it_is_too_late(paths, state, labels):
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        await pilot.press("s")
        await pilot.pause()
        db = sqlite3.connect(state)                  # the bot sends it and marks it
        db.execute("UPDATE reviews SET status = 'sent' WHERE msg_id = 11")
        db.commit()
        db.close()
        await pilot.press("u")
        await pilot.pause()
        assert "too late" in note(app) and "already handed #11 to Telegram" in note(app)
        assert status_of(state, 11) == "sent"
    assert [l["label"] for l in read_labels(labels)] == ["TRUE"]   # it did go out: the label stays


# ------------------------------------------------- the undo window (#62) and History undo (#76)

def set_row(state: Path, msg_id: int, **cols) -> None:
    db = sqlite3.connect(state)
    sets = ", ".join(f"{k} = ?" for k in cols)
    db.execute(f"UPDATE reviews SET {sets} WHERE msg_id = ?", (*cols.values(), msg_id))
    db.commit()
    db.close()


def send_after_of(state: Path, msg_id: int) -> int | None:
    db = sqlite3.connect(state)
    try:
        return db.execute("SELECT send_after FROM reviews WHERE msg_id = ?", (msg_id,)).fetchone()[0]
    finally:
        db.close()


async def test_approving_counts_down_then_says_it_was_sent(paths, state, labels):
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        before = int(time.time())
        await pilot.press("s")
        await pilot.pause()
        assert before + 30 <= send_after_of(state, 11) <= int(time.time()) + 30   # the bot waits 30 s
        # a loaded machine may take a second or two between the decision and the note
        assert any(f"sent to the channel in {n} s" in note(app) for n in (30, 29, 28)) and "u undoes" in note(app)
        set_row(state, 11, send_after=int(time.time()) + 5)
        pane._countdown()                                    # a second later, and so on
        await pilot.pause()
        assert any(f"in {n} s" in note(app) for n in (5, 4, 3))
        set_row(state, 11, send_after=int(time.time()) - 1)
        pane._countdown()
        await pilot.pause()
        assert "sends it within seconds" in note(app)
        assert pane.check_action("undo", ()) is True          # not claimed yet: still undoable
        set_row(state, 11, status="sent")                     # the bot sent it
        pane._countdown()
        await pilot.pause()
        assert note(app) == "#11 handed to Telegram for the channel"
        assert pane.check_action("undo", ()) is None          # no undo offered once sent
        assert not app.query_one("#btn-undo", Button).display
        await pilot.press("u")
        await pilot.pause()
        assert status_of(state, 11) == "sent" and note(app) == "#11 handed to Telegram for the channel"
    assert [l["label"] for l in read_labels(labels)] == ["TRUE"]


async def test_the_countdown_runs_by_itself(paths, state):
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        await pilot.press("s")
        await pilot.pause()
        set_row(state, 11, send_after=int(time.time()) + 9)
        await pilot.pause(1.5)                                # the 1-second timer redraws it
        assert any(f"in {n} s" in note(app) for n in (9, 8, 7))


async def test_u_within_the_window_puts_it_back(paths, state, labels):
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        await pilot.press("s")
        await pilot.pause()
        assert status_of(state, 11) == "to_send" and send_after_of(state, 11) > time.time()
        await pilot.press("u")
        await pilot.pause()
        assert status_of(state, 11) == "pending" and send_after_of(state, 11) is None
        assert "back in the queue" in note(app)
    assert read_labels(labels) == []


async def test_u_once_the_bot_has_started_sending_is_too_late(paths, state, labels):
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        await pilot.press("s")
        await pilot.pause()
        # the window is over and the bot claimed it (state.nim claimReview) before the screen looked again
        set_row(state, 11, send_after=int(time.time()) - 1, status="sending")
        await pilot.press("u")
        await pilot.pause()
        assert "too late to undo" in note(app) and "handing #11 to Telegram" in note(app)
        assert status_of(state, 11) == "sending"
    assert [l["label"] for l in read_labels(labels)] == ["TRUE"]


async def history_row(pilot, app, msg_id: int) -> None:
    pane = app.query_one(ReviewPane)
    table = app.query_one("#review-history", DataTable)
    table.move_cursor(row=[r.msg_id for r in pane._past].index(msg_id))
    await pilot.pause()


async def test_history_undoes_any_decision_not_yet_sent(paths, state, labels, now):
    add_pending(state, 7, "discarded elsewhere", now - 30)
    add_pending(state, 8, "kept earlier", now - 20)
    with Store(state) as st:
        ids = {r.msg_id: r.id for r in st.reviews("pending")}
        assert st.decide(ids[7], "discarded") and st.decide(ids[8], "kept") and st.decide(ids[11], "to_send")
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("h")
        await pilot.pause()
        table = app.query_one("#review-history", DataTable)
        labels_shown = {r.msg_id: str(table.get_row_at(i)[1]) for i, r in enumerate(pane._past)}
        assert labels_shown[11].startswith("sends in ") and labels_shown[9] == "sent"
        await history_row(pilot, app, 9)                      # sent: no undo
        assert pane.check_action("undo", ()) is None and not app.query_one("#btn-undo", Button).display
        await pilot.press("u")
        await pilot.pause()
        assert status_of(state, 9) == "sent"
        for msg_id in (7, 8, 11):                             # discarded, kept, approved in its window
            await history_row(pilot, app, msg_id)
            assert app.query_one("#btn-undo", Button).display
            await pilot.press("u")
            await pilot.pause()
            assert status_of(state, msg_id) == "pending", msg_id
            assert f"#{msg_id} is back in the queue" in note(app)
        assert [r.msg_id for r in pane._past] == [9]
        await pilot.press("h")
        await pilot.pause()
        assert app.query_one("#review-list", OptionList).option_count == 3


async def test_history_counts_down_and_shows_the_send(paths, state, now):
    with Store(state) as st:
        assert st.decide(st.reviews("pending")[0].id, "to_send")
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("h")
        await pilot.pause()
        table = app.query_one("#review-history", DataTable)
        set_row(state, 11, send_after=int(time.time()) + 12)
        pane._countdown()
        await pilot.pause()
        assert str(table.get_row_at(0)[1]) in ("sends in 12 s", "sends in 11 s", "sends in 10 s")
        set_row(state, 11, status="sending")
        pane._countdown()
        await pilot.pause()
        assert str(table.get_row_at(0)[1]) == "sending now" and pane.check_action("undo", ()) is None
        set_row(state, 11, status="sent")
        pane._countdown()
        await pilot.pause()
        assert str(table.get_row_at(0)[1]) == "sent"


async def test_a_decision_from_the_command_line_is_undone_in_history(paths, state, now):
    """`telenotify review` writes it as the bot does (state.nim decideReview): the screen can take it back."""
    with Store(state) as st:
        rid = st.reviews("pending")[0].id
    db = sqlite3.connect(state)                               # the command line, another process
    db.execute("UPDATE reviews SET status = 'to_send', decided_at = ?, send_after = ? WHERE id = ? AND status = 'pending'",
               (now, now + 30, rid))
    db.commit()
    db.close()
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        await pilot.press("h")
        await pilot.pause()
        await history_row(pilot, app, 11)
        await pilot.press("u")
        await pilot.pause()
        assert status_of(state, 11) == "pending"


def run_review_cli(binary: Path, config: Path, typed: str) -> str:
    """`telenotify review --dry-run` on a terminal (a pty: it asks y/N only there), with `typed` as the keys."""
    import pty
    import select
    import subprocess
    master, slave = pty.openpty()
    p = subprocess.Popen([str(binary), "review", f"--config:{config}", "--dry-run"],
                         stdin=slave, stdout=slave, stderr=slave, close_fds=True)
    os.close(slave)
    os.write(master, typed.encode())
    out = b""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        r, _, _ = select.select([master], [], [], 0.5)
        if r:
            try:
                chunk = os.read(master, 4096)
            except OSError:   # the program ended
                break
            if not chunk:
                break
            out += chunk
        elif p.poll() is not None:
            break
    p.wait(timeout=10)
    os.close(master)
    return out.decode("utf-8", "replace")


async def test_the_real_command_line_asks_and_the_screen_undoes_it(paths, state, config, binary):
    out = run_review_cli(binary, config, "s\ny\n")
    assert out.lstrip("sy\r\n").startswith("DRY RUN:"), out          # what was typed is echoed first
    assert "Record as approved (dry run, nothing is sent)? [y/N]" in out, out
    assert status_of(state, 11) == "to_send" and send_after_of(state, 11) > time.time()
    app = TeleNotifyApp(dataclasses.replace(paths, binary=binary), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        await pilot.press("h")
        await pilot.pause()
        await history_row(pilot, app, 11)
        await pilot.press("u")
        await pilot.pause()
        assert status_of(state, 11) == "pending"


def set_meta(state: Path, key: str, value: str) -> None:
    db = sqlite3.connect(state)
    db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
    db.commit()
    db.close()


async def test_history_keeps_the_cursor_on_the_same_review_across_reloads(paths, state, labels, now):
    """Review run 1, fail 1: cursor on #22, the bot sends #24 and another decision lands; u acts on #22."""
    for k in range(20, 26):
        add_pending(state, k, f"post {k}", now - 100 + k)
    with Store(state) as st:
        ids = {r.msg_id: r.id for r in st.reviews("pending")}
    db = sqlite3.connect(state)
    for k in range(20, 24):   # discarded, oldest first
        db.execute("UPDATE reviews SET status = 'discarded', decided_at = ? WHERE id = ?", (now - 60 + k, ids[k]))
    db.execute("UPDATE reviews SET status = 'to_send', decided_at = ?, send_after = ? WHERE id = ?",
               (now - 30, now + 30, ids[24]))
    db.commit()
    db.close()
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("h")
        await pilot.pause()
        await history_row(pilot, app, 22)
        # meanwhile: the bot sends #24, and `telenotify review` discards #25 (a new row at the top)
        set_row(state, 24, status="sent")
        db = sqlite3.connect(state)
        db.execute("UPDATE reviews SET status = 'discarded', decided_at = ? WHERE id = ?", (now + 5, ids[25]))
        db.commit()
        db.close()
        pane.reload()
        await pilot.pause()
        table = app.query_one("#review-history", DataTable)
        assert pane._past[table.cursor_row].msg_id == 22
        await pilot.press("u")
        await pilot.pause()
        assert status_of(state, 22) == "pending" and status_of(state, 21) == "discarded"
        assert status_of(state, 23) == "discarded"


async def test_with_the_bot_stopped_nothing_is_promised(paths, state):
    """Review run 1, fail 6 (F3/F4): no "sends in", and an interrupted send says so; no 1 s reloads."""
    set_meta(state, "connection", "stopped")
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("s")
        await pilot.pause()
        assert "The bot isn't running: it will be sent when the bot starts (u undoes)" in note(app)
        assert "sent to the channel in" not in note(app)
        assert not pane._counting()                          # nothing counts down with the bot stopped
        await pilot.press("h")
        await pilot.pause()
        table = app.query_one("#review-history", DataTable)
        labels_shown = {r.msg_id: str(table.get_row_at(i)[1]) for i, r in enumerate(pane._past)}
        assert labels_shown[11] == "waits for bot"
        set_row(state, 11, status="sending")                 # the bot crashed while sending
        pane.reload()
        await pilot.pause()
        assert str(table.get_row_at(0)[1]) == "interrupted" and not pane._counting()
        assert pane.check_action("undo", ()) is None
        set_meta(state, "connection", "ready")               # it runs again: the screen follows
        pane.reload()
        await pilot.pause()
        assert str(table.get_row_at(0)[1]) == "sending now" and pane._counting()


async def test_an_approval_note_counting_down_never_moves_the_post(paths, state, now):
    """Review run 1, fail 7 (P4): at 80 x 24 the note keeps its height while it changes by itself."""
    add_pending(state, 1234567890123, "a long id", now - 100)   # TDLib message ids are long
    add_pending(state, 7, "the next post", now - 5)
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("s")                               # the oldest: the long id
        await pilot.pause()
        assert "#1234567890123 approved" in note(app)
        split = app.query_one("#review-split")
        y, h = split.region.y, app.query_one("#review-note").region.height
        for change in ({"send_after": int(time.time()) - 1}, {"status": "sending"}, {"status": "sent"}):
            set_row(state, 1234567890123, **change)
            pane._countdown() if pane._last is not None else pane.reload()
            await pilot.pause()
            assert split.region.y == y and app.query_one("#review-note").region.height == h, note(app)
        assert note(app) == "#1234567890123 handed to Telegram for the channel"


async def test_the_timer_runs_only_while_review_is_shown_and_counting(paths, state):
    """Review run 1, fail 8 (P1): paused on other tabs and when nothing counts; short lock waits."""
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        ticker = pane._ticker
        assert ticker is not None and not ticker._active.is_set()      # nothing counts yet
        await pilot.press("s")
        await pilot.pause()
        assert ticker._active.is_set()
        await pilot.press("3")                                          # Search
        await pilot.pause()
        assert not ticker._active.is_set()
        await pilot.press("2")
        await pilot.pause()
        assert ticker._active.is_set()
        waits: list[float] = []
        real = app.store

        def store(timeout: float = 10):
            waits.append(timeout)
            return real(timeout)
        app.store = store
        set_row(state, 11, status="sent")
        pane._countdown()
        await pilot.pause()
        assert waits and max(waits) <= review_mod.TICK_TIMEOUT == 0.25
        assert not ticker._active.is_set()                              # sent: nothing counts


async def test_a_busy_state_file_skips_the_tick(paths, state, monkeypatch):
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("s")
        await pilot.pause()
        before = note(app)

        def busy(timeout: float = 10):
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(app, "store", busy)
        pane._countdown()                                     # no exception, nothing changes
        assert note(app) == before


async def test_undo_seconds_from_the_config_sets_the_window(paths, state, config):
    """Review run 1, fail 9 (A21): 0 turns the window off."""
    cfg = json.loads(config.read_text(encoding="utf-8"))
    cfg["undo_seconds"] = 0
    config.write_text(json.dumps(cfg), encoding="utf-8")
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        before = int(time.time())
        await pilot.press("s")
        await pilot.pause()
        assert before <= send_after_of(state, 11) <= int(time.time())
        assert "the bot sends it within seconds" in note(app)


async def test_a_post_changed_elsewhere_is_said_in_plain_words(paths, state):
    """Review run 1, fail 11 (W1)."""
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("s")
        await pilot.pause()
        set_row(state, 11, status="kept")
        pane._countdown()
        await pilot.pause()
        assert note(app) == "#11 was changed elsewhere: it is now kept as a match, not sent"


def test_history_status_words_stay_short():
    """Review run 1, fail 12 (TUI1): the status column never pushes the post off an 80-column screen."""
    now = time.time()
    shown = set()
    for status in ("to_send", "sending", "sent", "discarded", "kept"):
        for send_after in (None, int(now) + 600, int(now) - 5):
            for dry in (False, True):
                for up in (False, True):
                    r = Review(1, -1, 1, "", "", "", "", "", status, 0, send_after=send_after)
                    shown.add(review_mod.status_label(r, dry, up, now))
    assert max(len(x) for x in shown) <= len("match, not sent")


async def test_the_history_post_column_starts_on_an_80_column_screen(paths, state, now):
    with Store(state) as st:
        assert st.decide(st.reviews("pending")[0].id, "to_send", undo_seconds=0)
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await open_review(pilot)
        await pilot.press("h")
        await pilot.pause()
        table = app.query_one("#review-history", DataTable)
        assert str(table.get_row_at(0)[1]) == "sending soon"
        cols = table.ordered_columns
        start = sum(c.get_render_width(table) for c in cols[:4])
        assert start < table.region.width - 5, start


# ------------------------------------------------- review run 2 (adversarial)

def add_decided(state: Path, msg_id: int, status: str, decided_at: int, send_after: int | None = None) -> None:
    db = sqlite3.connect(state)
    db.execute("""INSERT INTO reviews (chat_id, msg_id, chat_title, text, reasons, role, link, status, at,
                  decided_at, send_after) VALUES (-2, ?, 'Iraq Jobs', ?, 'unclear', 'it support', '', ?, ?, ?, ?)""",
               (msg_id, f"post {msg_id}", status, decided_at - 100, decided_at, send_after))
    db.commit()
    db.close()


async def test_history_shows_every_state_of_the_window(paths, decided_states):
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("h")
        await pilot.pause()
        table = app.query_one("#review-history", DataTable)
        shown = {r.msg_id: str(table.get_row_at(i)[1]) for i, r in enumerate(pane._past)}
        assert shown[41].startswith("sends in ") and shown[42] == "sending now" and shown[43] == "sending soon"


async def test_history_undo_acts_once_per_deliberate_press(paths, state, now, labels, monkeypatch):
    """N1: u u u (a double tap, a held key) undoes one decision; the next row waits DECIDE_DELAY."""
    monkeypatch.setattr(ReviewPane, "DECIDE_DELAY", 0.8)
    for i, status in enumerate(["discarded", "kept", "discarded", "discarded"]):
        add_decided(state, 50 + i, status, now - 10 - i)
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("h")
        await pilot.pause()
        pane._hist_shown_at -= 10                      # the first row has been read
        await pilot.press("u", "u", "u")
        await pilot.pause()
        assert [m for m in (50, 51, 52, 53) if status_of(state, m) == "pending"] == [50]
        assert "only just come under the cursor" in note(app)
        assert pane.check_action("undo", ()) is None   # greyed while the new row's wait runs
        pane._hist_shown_at -= 10                      # read now: a deliberate press works again
        await pilot.press("u")
        await pilot.pause()
        assert [m for m in (50, 51, 52, 53) if status_of(state, m) == "pending"] == [50, 51]


async def test_a_double_click_on_undo_in_history_undoes_one(paths, state, now, monkeypatch):
    """The two clicks are made 0.1 s apart, whenever a loaded machine handles them: each carries
    the time it was made (as a real mouse's does), so the second is judged by that time."""
    from textual import events
    monkeypatch.setattr(ReviewPane, "DECIDE_DELAY", 0.8)
    for i, status in enumerate(["discarded", "kept", "discarded"]):
        add_decided(state, 60 + i, status, now - 10 - i)
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("h")
        await pilot.pause()
        pane._hist_shown_at -= 10                      # the first row has been read
        pane._sync_controls()
        await pilot.pause()
        btn = app.query_one("#btn-undo")
        x, y = btn.region.x + 1, btn.region.y
        first = time.monotonic()
        for made in (first, first + 0.1):              # a double click
            for cls in (events.MouseDown, events.MouseUp):
                e = cls(None, x, y, 0, 0, 1, False, False, False, screen_x=x, screen_y=y)
                e.time = made
                app.post_message(e)
            await pilot.pause()
        await pilot.pause()
        assert [m for m in (60, 61, 62) if status_of(state, m) == "pending"] == [60]


async def test_a_decision_not_recorded_stays_beside_the_countdown(paths, state, now):
    """N2: the next countdown second must not hide "NOT recorded"."""
    add_pending(state, 71, "another post", now - 20)
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("s")
        await pilot.pause()
        lock = sqlite3.connect(state, isolation_level=None)
        lock.execute("BEGIN EXCLUSIVE")
        await pilot.press("d")
        await pilot.pause()
        lock.execute("ROLLBACK")
        lock.close()
        for _ in range(2):
            pane._countdown()
            pane.reload()
            await pilot.pause()
            assert "#71 NOT recorded" in note(app) and "#11 approved" in note(app)
        assert status_of(state, 71) == "pending"


async def test_already_decided_elsewhere_is_said_beside_the_countdown(paths, state, now):
    """N3: d on a post another window approved says so, and what it is now."""
    add_pending(state, 70, "another post", now - 20)
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("s")
        await pilot.pause()
        set_row(state, 70, status="to_send", decided_at=now)
        await pilot.press("d")
        await pilot.pause()
        assert "#70 was already decided elsewhere: it is now approved" in note(app)
        pane._countdown()
        await pilot.pause()
        assert "#70 was already decided elsewhere" in note(app) and "#11 approved" in note(app)
        await pilot.press("space")                      # the next key press: it may go now
        await pilot.pause()
        assert "#70" not in note(app)


async def test_history_keeps_its_scroll_position_on_a_tick(paths, state, now):
    """N4: a countdown second doesn't scroll History back to the cursor."""
    add_decided(state, 80, "to_send", now - 1, now + 25)
    for i in range(45):
        add_decided(state, 100 + i, "discarded", now - 100 - i)
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await open_review(pilot)
        await pilot.press("h")
        await pilot.pause()
        table = app.query_one("#review-history", DataTable)
        table.scroll_to(y=20, animate=False)
        await pilot.pause()
        y0, row = table.scroll_y, table.cursor_row
        pane = app.query_one(ReviewPane)
        pane._countdown()
        await pilot.pause()
        assert table.scroll_y == y0 and table.cursor_row == row
        add_decided(state, 81, "discarded", now)        # a new row: refilled, still where it was
        pane.reload()
        await pilot.pause()
        assert table.scroll_y == y0


@pytest.mark.parametrize("why,expected", [
    ("; could not send: CHAT_WRITE_FORBIDDEN",
     "#11: sending failed: this account may not post in the channel; it's back in the queue"),
    ("; the bot stopped while sending it: check the channel before sending it again",
     "#11: the bot stopped while sending; check the channel before sending it again"),
])
async def test_a_failed_or_interrupted_send_is_said_as_such(paths, state, why, expected):
    """N5: not "changed elsewhere"."""
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("s")
        await pilot.pause()
        set_row(state, 11, status="sending")
        pane._countdown()
        db = sqlite3.connect(state)
        db.execute("UPDATE reviews SET status = 'pending', send_after = NULL, reasons = reasons || ? "
                   "WHERE msg_id = 11", (why,))
        db.commit()
        db.close()
        pane._countdown()
        await pilot.pause()
        assert note(app) == expected


async def test_a_bot_still_reading_its_backlog_isnt_sending_yet(paths, state):
    """N6: a fresh heartbeat but no sending_ready: nothing is promised "within seconds"."""
    set_meta(state, "sending_ready", "0")
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("s")
        await pilot.pause()
        assert "The bot is starting: it will be sent once it has caught up (u undoes)" in note(app)
        assert not pane._counting()
        await pilot.press("h")
        await pilot.pause()
        assert str(app.query_one("#review-history", DataTable).get_row_at(0)[1]) == "waits for bot"
        set_meta(state, "sending_ready", "1")
        pane.reload()
        await pilot.pause()
        assert str(app.query_one("#review-history", DataTable).get_row_at(0)[1]).startswith("sends in ")


async def test_a_label_error_stays_and_the_post_doesnt_move(paths, state, now, monkeypatch, tmp_path):
    """N8: at 80 x 24 the note keeps its height with "corpus label not saved" in it, until a key press."""
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    monkeypatch.setenv("TELENOTIFY_LABELS", str(blocker / "deep" / "review-labels.jsonl"))
    add_pending(state, 90, "the next post", now - 5)
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("s")
        await pilot.pause()
        split = app.query_one("#review-split")
        y = split.region.y
        assert "corpus label not saved" in note(app)
        for change in ({"send_after": int(time.time()) - 1}, {"status": "sending"}, {"status": "sent"}):
            set_row(state, 11, **change)
            pane._countdown()
            await pilot.pause()
            assert split.region.y == y, (change, note(app))
            assert "corpus label not saved" in note(app)
        await pilot.press("space")
        await pilot.pause()
        assert "corpus label not saved" not in note(app)


# ------------------------------------------------- review run 3

async def test_a_second_bot_run_doesnt_switch_off_the_countdown(paths, state, config, binary, tmp_path):
    """F1: a second `telenotify run` that can't start (here: no api_id) leaves the running bot's
    state alone, so the screen still counts down."""
    import asyncio
    import shutil
    real_state = state.parent / "state.sqlite3"           # the real run's state, beside the dry run's
    shutil.copy(state, real_state)
    app = TeleNotifyApp(dataclasses.replace(paths, state=real_state, dry_run=False, dry_run_flag=False),
                        refresh_seconds=0)
    async with app.run_test(size=(120, 30)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("s")
        await pilot.pause()
        assert "sent to the channel in" in note(app)
        proc = await asyncio.create_subprocess_exec(str(binary), "run", f"--config:{config}",
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), 60)
        assert proc.returncode != 0, out                              # it didn't start
        pane.reload()
        await pilot.pause()
        assert "sent to the channel in" in note(app) and pane._counting(), note(app)
    with Store(real_state) as st:
        assert st.meta("sending_ready") == "1" and st.meta("connection") == "ready"


async def test_history_widens_a_column_when_a_cell_grows(paths, state, now, monkeypatch):
    """F3: "sends in 5 s" becomes "waits for bot", and a date gains its day after midnight."""
    db = sqlite3.connect(state)
    db.execute("DELETE FROM reviews")
    db.commit()
    db.close()
    add_decided(state, 1, "sent", now - 100)
    add_decided(state, 3, "to_send", now - 25, now + 5)
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(120, 30)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("h")
        await pilot.pause()
        table = app.query_one("#review-history", DataTable)
        decided_col, status_col = list(table.columns.values())[:2]
        set_meta(state, "connection", "stopped")
        pane.reload()
        await pilot.pause()
        assert str(table.get_row_at(0)[1]) == "waits for bot"
        assert status_col.content_width >= len("waits for bot")
        monkeypatch.setattr(review_mod, "when", lambda at: "11-21 23:59")   # the day has turned
        pane.reload()
        await pilot.pause()
        assert decided_col.content_width >= len("11-21 23:59")


@pytest.mark.parametrize("why,words", [
    ("Have no write access to the chat (400)", "this account may not post in the channel"),
    ("Need administrator rights in the channel chat (400)", "this account isn't an admin of the channel"),
    ("Too Many Requests: retry after 30 (429)", "Telegram asked to wait 30 s before sending more"),
    ("Message has protected content and can't be forwarded (400)",
     "the source doesn't allow forwarding, and the post has no link to share"),
    ("CHAT_WRITE_FORBIDDEN (403)", "this account may not post in the channel"),
    ("no answer, after 60 s", "no answer, after 60 s"),     # whole (#142 writes a reason's "; " as ", ")
])
async def test_tdlibs_own_errors_are_put_in_plain_words(paths, state, why, words):
    """F4."""
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("s")
        await pilot.pause()
        db = sqlite3.connect(state)
        db.execute("UPDATE reviews SET status = 'pending', send_after = NULL, reasons = reasons || ? "
                   "WHERE msg_id = 11", ("; could not send: " + why,))
        db.commit()
        db.close()
        pane._countdown()
        await pilot.pause()
        assert note(app) == f"#11: sending failed: {words}; it's back in the queue"


async def test_a_resize_measures_the_kept_note_height_again(paths, state, now, monkeypatch, tmp_path):
    """F6: label error at 200 x 50, then 80 x 24: the post doesn't move when the bot stops."""
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    monkeypatch.setenv("TELENOTIFY_LABELS", str(blocker / "deep" / "review-labels.jsonl"))
    add_pending(state, 90, "the next post", now - 5)
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(200, 50)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("s")
        await pilot.pause()
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        await pilot.pause()
        split = app.query_one("#review-split")
        y = split.region.y
        set_meta(state, "connection", "stopped")
        pane._countdown()
        pane.reload()
        await pilot.pause()
        assert split.region.y == y, note(app)
        set_row(state, 11, status="sent")
        set_meta(state, "connection", "ready")
        pane._countdown()
        await pilot.pause()
        assert split.region.y == y, note(app)


async def test_the_undo_wait_warning_survives_a_reload(paths, state, now, monkeypatch):
    """F7: "only just come under the cursor" stays while an approval made here is watched."""
    monkeypatch.setattr(ReviewPane, "DECIDE_DELAY", 0.8)
    add_decided(state, 50, "discarded", now - 10)
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        pane._shown_at -= 10
        await pilot.press("s")                              # #11 approved and watched
        await pilot.pause()
        await pilot.press("h")
        await pilot.pause()
        await pilot.press("u")                              # too soon after #50 came under the cursor
        await pilot.pause()
        assert "only just come under the cursor" in note(app)
        pane.reload()
        pane._countdown()
        await pilot.pause()
        assert "only just come under the cursor" in note(app) and "#11 approved" in note(app)
        assert status_of(state, 50) == "discarded"


# ------------------------------------------------- review run 4

async def test_a_log_out_says_a_new_login_is_needed(paths, state):
    """R4-2: with a fresh heartbeat but connection "logged out", not "the bot is starting"."""
    set_meta(state, "sending_ready", "0")
    set_meta(state, "connection", "logged out")
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("s")
        await pilot.pause()
        assert ("#11 approved. Not logged in to Telegram: it will be sent once you run telenotify login "
                "and start the bot (u undoes)") in note(app)
        assert "starting" not in note(app) and not pane._counting()
        await pilot.press("h")
        await pilot.pause()
        assert str(app.query_one("#review-history", DataTable).get_row_at(0)[1]) == "needs login"


async def test_a_failed_send_beside_a_label_error_never_moves_the_post(paths, state, now, monkeypatch):
    """R4-1: the protected-content failure (the longest plain words) beside a label error fits in the
    height kept for the note. With this label error, at a note 87 or 88 columns wide, the failure is
    the tallest thing the note can say, a line taller than with 60 unbroken characters (what was
    measured before): every screen width around those."""
    def no_label(review, decision):
        raise OSError("No space left on device")
    monkeypatch.setattr(review_mod, "write_label", no_label)
    why = "; could not send: Message has protected content and can't be forwarded (400)"
    add_pending(state, 90, "the next post", now - 5)
    moved, note_widths = [], set()
    for width in range(95, 131):
        set_row(state, 11, status="pending", send_after=None, decided_at=None, reasons="x")
        set_meta(state, "last_alive", str(int(time.time())))      # the bot runs all along
        app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
        async with app.run_test(size=(width, 30)) as pilot:
            await open_review(pilot)
            pane = app.query_one(ReviewPane)
            await pilot.press("s")
            await pilot.pause()
            note_widths.add(app.query_one("#review-note").content_region.width)
            y = app.query_one("#review-split").region.y
            db = sqlite3.connect(state)
            db.execute("UPDATE reviews SET status = 'pending', send_after = NULL, reasons = reasons || ? "
                       "WHERE msg_id = 11", (why,))
            db.commit()
            db.close()
            pane._countdown()
            await pilot.pause()
            assert "source doesn't allow forwarding" in note(app) and "No space left on device" in note(app)
            if app.query_one("#review-split").region.y != y:
                moved.append(width)
    assert {87, 88} <= note_widths                    # the widths where it matters were reached
    assert moved == []


# ------------------------------------------------- review run 5, and #142's reasons

async def test_a_restart_after_a_log_out_says_starting_everywhere(paths, state):
    """R5-1: connection "starting" with login_needed still set: the note and History agree."""
    set_meta(state, "sending_ready", "0")
    set_meta(state, "connection", "starting")
    set_meta(state, "login_needed", "1")
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        await pilot.press("s")
        await pilot.pause()
        assert "The bot is starting" in note(app)
        await pilot.press("h")
        await pilot.pause()
        assert str(app.query_one("#review-history", DataTable).get_row_at(0)[1]) == "waits for bot"


@pytest.mark.parametrize("before,after,expected", [
    # #142's withProblem: a second failure of the same kind is counted, not added again
    ("x; could not send: Have no write access to the chat (400)",
     "x; could not send (2 tries): Have no write access to the chat (400)",
     "#11: sending failed: this account may not post in the channel; it's back in the queue"),
    # folded into the live post's own error
    ("x; error: Too Many Requests: retry after 30 (429)", "x; error (2 tries): Too Many Requests: retry after 30 (429)",
     "#11: sending failed: Telegram asked to wait 30 s before sending more; it's back in the queue"),
    # held back on purpose (#80), as the bot words it
    ("x", ("x; not posted: it isn't from a public channel (it's a private or members-only chat, or a group), "
           "and only public channels' posts are republished in your public channel (#80)."),
     ("#11: not sent: it isn't from a public channel, so it can't be republished; post it yourself or discard it; "
      "it's back in the queue")),
])
async def test_142s_counted_and_held_back_reasons_in_plain_words(paths, state, before, after, expected):
    set_row(state, 11, reasons=before)
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("s")
        await pilot.pause()
        set_row(state, 11, status="pending", send_after=None, reasons=after)
        pane._countdown()
        await pilot.pause()
        assert note(app) == expected


async def test_the_double_tap_wait_shows_on_the_buttons(paths, state, now, monkeypatch):
    """Not timed by the wall clock (a loaded CI runner took longer than the wait itself): the
    wait is long, and its end is made by moving when the post appeared back past it."""
    monkeypatch.setattr(ReviewPane, "DECIDE_DELAY", 30.0)
    add_pending(state, 7, "second post", now - 10)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        pane._shown_at -= 60                                        # the first post has been read
        pane._settled()
        await pilot.pause()
        assert not app.query_one("#btn-send", Button).disabled
        await pilot.press("n")                                      # a new post appears
        assert app.query_one("#btn-send", Button).disabled          # greyed while s / d wait
        for action in ("send", "keep", "discard"):                  # the footer's keys too
            assert pane.check_action(action, ()) is None
        await pilot.press("s")
        assert "only just appeared" in note(app) and status_of(state, 7) == "pending"
        assert app.query_one("#btn-discard", Button).disabled
        assert not app.query_one("#btn-next", Button).disabled
        pane._shown_at -= 60                                        # the wait is over
        pane._settled()                                             # what its timer does then
        await pilot.pause()
        assert not app.query_one("#btn-send", Button).disabled
        assert not app.query_one("#btn-discard", Button).disabled
        assert pane.check_action("send", ()) is True


async def test_a_post_interrupted_twice_is_said_both_times(paths, state):
    """Review run 6: the second interruption is counted ("(2 tries)"), still said as one."""
    stopped = "the bot stopped while sending it: check the channel before sending it again"
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        for reasons in (f"x; {stopped}", f"x; {stopped} (2 tries)"):
            await pilot.press("s")
            await pilot.pause()
            set_row(state, 11, status="sending")
            pane._countdown()
            set_row(state, 11, status="pending", send_after=None, reasons=reasons)
            pane._countdown()
            await pilot.pause()
            assert note(app) == "#11: the bot stopped while sending; check the channel before sending it again"
            pane.reload()
            await pilot.pause()
            app.query_one("#review-list", OptionList).highlighted = 0
            await pilot.pause()


# ------------------------------------------------- odd data in the state file

async def test_invalid_utf8_doesnt_block_the_queue(paths, state, now):
    db = sqlite3.connect(state)
    db.execute("""INSERT INTO reviews (chat_id, msg_id, chat_title, text, reasons, role, link, status, at)
                  VALUES (-2, 200, 'x', CAST(X'61FF62' AS TEXT), 'r', 'it', '', 'pending', ?)""", (now - 5,))
    db.commit()
    db.close()
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        assert len(app.query_one(ReviewPane)._items) == 2
        assert "can't read" not in note(app)


async def test_superscript_album_id_doesnt_crash(paths, state, now):
    db = sqlite3.connect(state)
    db.execute("""INSERT INTO reviews (chat_id, msg_id, chat_title, text, reasons, role, link, status, at, album_ids)
                  VALUES (-2, 300, 'x', 'post', 'r', 'it', '', 'pending', ?, '²')""", (now - 5,))
    db.commit()
    db.close()
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        assert app.is_running
        assert len(app.query_one(ReviewPane)._items) == 2


async def test_a_value_error_from_the_data_is_a_note_not_a_crash(paths, monkeypatch):
    def broken(self, status="pending"):
        raise ValueError("invalid literal for int() with base 10: '²'")

    monkeypatch.setattr(Store, "reviews", broken)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        assert app.is_running
        assert "Can't read the state file" in note(app)


# ------------------------------------- "match, don't send" (already sent elsewhere)

async def test_keep_labels_a_match_but_never_sends(paths, state, labels, now):
    """k: the old bot already forwarded this post. It is a true match for the
    corpus, but the bot must not send it again, show it as pending, or count
    it as forwarded."""
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        await pilot.press("k")
        await pilot.pause()
        assert status_of(state, 11) == "kept"
        assert "kept as a match, not sent" in note(app)
        assert app.query_one("#review-empty").display          # gone from the queue
    lines = read_labels(labels)
    assert [(l["id"], l["label"]) for l in lines] == [("-1:11", "TRUE")]   # same label as send
    with Store(state) as s:
        assert s.review_counts().get("pending", 0) == 0 and s.review_counts()["kept"] == 1
        assert s.reviews("to_send") == []                       # the bot sends only to_send
        basra = next(c for c in s.stats(7) if c.chat == "Basra Jobs")
        assert basra.forwarded == 1                             # only #10, which was forwarded
        week = dd.resolve(dd.PeriodChoice("7d"), dd.today(now))
        assert dd.period_stats(s, week, 46).totals.forwarded == 1


async def test_keep_button_and_history(paths, state, labels):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        assert app.query_one("#btn-keep", Button).display
        await pilot.click("#btn-keep")
        await pilot.pause()
        assert status_of(state, 11) == "kept"
        await pilot.press("h")
        await pilot.pause()
        table = app.query_one("#review-history", DataTable)
        assert [str(table.get_row_at(i)[1]) for i in range(table.row_count)] == ["match, not sent", "sent"]


async def test_undo_keep(paths, state, labels):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        await pilot.press("k")
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()
        assert status_of(state, 11) == "pending"
        assert app.query_one("#review-list", OptionList).option_count == 1
        assert "back in the queue" in note(app)
    assert read_labels(labels) == []


def test_store_keep_and_undo(state):
    with Store(state) as s:
        r = s.reviews("pending")[0]
        assert s.decide(r.id, "kept")
        assert not s.decide(r.id, "to_send")                    # decided: can't be approved after
        assert s.undo(r.id, "to_send") == (False, "kept")
        assert s.undo(r.id, "kept") == (True, "pending")


async def test_dry_run_says_send_only_records(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)   # the fixture paths are a dry run
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        line = app.query_one("#review-dryrun", Static)
        assert line.display
        assert "only records your choice" in str(line.content) and "nothing is forwarded" in str(line.content)
        assert line.region.height >= 1                     # on screen, not just in the tree
        assert "forwards nothing" in str(app.query_one("#btn-send", Button).tooltip)


async def test_real_run_has_no_dry_run_line(paths):
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        assert not app.query_one("#review-dryrun", Static).display
        assert app.query_one("#btn-send", Button).tooltip is None


async def test_buttons_fit_an_80_column_terminal(paths, state, now):
    add_pending(state, 7, "second post", now - 10)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await open_review(pilot)
        await pilot.press("k")                      # so Undo shows too
        await pilot.pause()
        assert app.query_one("#btn-undo", Button).display
        assert app.query_one("#btn-keep", Button).region.width > 0
        assert app.query_one("#btn-undo", Button).region.right <= 80


async def test_a_locked_state_file_is_worded_as_on_the_dashboard(paths, state, monkeypatch):
    # one wording for one state on every tab (policy C2, W1)
    from telenotify_tui.store import BUSY_NOTE
    monkeypatch.setattr(review_mod, "UI_TIMEOUT", 0.1)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        db = sqlite3.connect(state, isolation_level=None)
        db.execute("BEGIN EXCLUSIVE")
        try:
            await pilot.press("2")
            for _ in range(40):
                await pilot.pause(0.05)
                if BUSY_NOTE[:20] in note(app) or BUSY_NOTE[:20] in str(app.query_one("#review-empty", Static).render()):
                    break
            shown = note(app) + str(app.query_one("#review-empty", Static).render())
            assert BUSY_NOTE in shown
            assert "database is locked" not in shown
        finally:
            db.execute("ROLLBACK")
            db.close()


async def test_the_busy_note_is_whole_at_80x24(paths, state, monkeypatch):
    # never cut what matters (W7): the note wraps rather than losing its end
    monkeypatch.setattr(review_mod, "UI_TIMEOUT", 0.1)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await open_review(pilot)
        db = lock(state)
        try:
            app.query_one(ReviewPane).reload()
            await pilot.pause()
            box = app.query_one("#review-note", Static)
            assert box.size.width * box.size.height >= len(BUSY_NOTE), box.size   # room for all of it
            assert "retrying" in app.export_screenshot()                        # and it is drawn
        finally:
            db.execute("ROLLBACK")
            db.close()
async def test_a_tap_made_too_early_but_handled_late_doesnt_decide(paths, state, now, monkeypatch):
    # a slow machine: the second tap is made inside DECIDE_DELAY but handled after it (CI saw this)
    from textual import events
    monkeypatch.setattr(ReviewPane, "DECIDE_DELAY", 0.4)
    add_pending(state, 100, "second post never shown", now - 10)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        pane._shown_at -= 10                     # the first post has been read
        await pilot.press("s")
        await pilot.pause()
        assert status_of(state, 11) == "to_send"
        shown = pane._shown_at                   # the second post appeared now
        early = events.Key("s", "s")
        early.time = shown + 0.1                 # made inside the delay...
        await pilot.pause(0.6)                   # ...handled after it
        app.post_message(early)
        await pilot.pause()
        assert status_of(state, 100) == "pending"
        btn = app.query_one("#btn-send")         # the same for a click on Send
        x, y = btn.region.x + 1, btn.region.y
        for cls in (events.MouseDown, events.MouseUp):
            e = cls(None, x, y, 0, 0, 1, False, False, False, screen_x=x, screen_y=y)
            e.time = shown + 0.1
            app.post_message(e)
        await pilot.pause()
        assert status_of(state, 100) == "pending"


async def test_a_footer_click_made_too_early_but_handled_late_doesnt_decide(paths, state, now, monkeypatch):
    # the footer's "s Send" makes a key when the click is *handled*; the click's own time counts
    from textual import events
    from textual.widgets._footer import FooterKey
    monkeypatch.setattr(ReviewPane, "DECIDE_DELAY", 0.4)
    add_pending(state, 100, "second post never shown", now - 10)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(160, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        pane._shown_at -= 10
        await pilot.press("s")
        await pilot.pause()
        assert status_of(state, 11) == "to_send"
        [send] = [k for k in app.query(FooterKey) if k.key == "s"]
        x, y = send.region.x + 1, send.region.y
        e = events.MouseDown(None, x, y, 0, 0, 1, False, False, False, screen_x=x, screen_y=y)
        e.time = pane._shown_at + 0.1            # made inside the delay...
        await pilot.pause(0.6)                   # ...handled after it
        app.post_message(e)
        await pilot.pause()
        assert status_of(state, 100) == "pending"


async def test_a_key_that_decided_nothing_leaves_no_time_behind(paths, state, now, monkeypatch):
    monkeypatch.setattr(ReviewPane, "DECIDE_DELAY", 0.4)
    add_pending(state, 100, "second post", now - 10)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        pane._shown_at -= 10
        await pilot.press("enter", "down")       # no decision: nothing to remember
        await pilot.pause(1.0)
        assert pane._press_at is None
        await pilot.click("#btn-send")           # read for a second: the click decides
        await pilot.pause()
        assert status_of(state, 100) == "to_send" or status_of(state, 11) == "to_send"


async def test_a_later_press_elsewhere_doesnt_lend_a_footer_click_its_time(paths, state, now, monkeypatch):
    from textual import events
    from textual.widgets._footer import FooterKey
    monkeypatch.setattr(ReviewPane, "DECIDE_DELAY", 0.4)
    add_pending(state, 100, "second post never shown", now - 10)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(160, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        pane._shown_at -= 10
        await pilot.press("s")
        await pilot.pause()
        [send] = [k for k in app.query(FooterKey) if k.key == "s"]
        text = app.query_one("#detail-text")

        def press(widget, made):
            x, y = widget.region.x + 1, widget.region.y
            for cls in (events.MouseDown, events.MouseUp):
                e = cls(None, x, y, 0, 0, 1, False, False, False, screen_x=x, screen_y=y)
                e.time = made
                app.post_message(e)
        shown = pane._shown_at
        await pilot.pause(0.6)                   # a stall: all of these are handled late
        press(send, shown + 0.1)                 # the footer "s": made too early
        press(text, shown + 0.9)                 # a later click on the post text
        await pilot.pause()
        assert status_of(state, 100) == "pending"


async def test_pilot_footer_clicks_never_dead_end(paths, state, now, monkeypatch):
    # Textual's pilot clicks past the app: an earlier real press must not make them look early
    from textual import events
    monkeypatch.setattr(ReviewPane, "DECIDE_DELAY", 0.2)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(160, 40)) as pilot:
        await open_review(pilot)
        text = app.query_one("#detail-text")
        x, y = text.region.x + 1, text.region.y
        e = events.MouseDown(None, x, y, 0, 0, 1, False, False, False, screen_x=x, screen_y=y)
        app.post_message(e)
        await pilot.pause(1.0)
        await pilot.click("FooterKey")           # the first footer key; then "s" itself
        from textual.widgets._footer import FooterKey
        [send] = [k for k in app.query(FooterKey) if k.key == "s"]
        await pilot.click(send)
        await pilot.pause()
        assert status_of(state, 11) == "to_send"


async def test_a_double_click_on_the_last_posts_footer_key_leaves_nothing_behind(paths, state, now, monkeypatch):
    # the second press lands on the now-disabled key: no key is made, so no press may be kept
    import time

    from textual import events
    from textual.widgets._footer import FooterKey
    monkeypatch.setattr(ReviewPane, "DECIDE_DELAY", 0.3)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(160, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        pane._shown_at -= 10

        # one place on the screen, as a user would click (the footer is rebuilt after each
        # decision, and a key read mid-rebuild has no position yet)
        [k] = [w for w in app.query(FooterKey) if w.key == "s"]
        spot = (k.region.x + 1, k.region.y)

        def click_footer(key):
            x, y = spot
            for cls in (events.MouseDown, events.MouseUp):
                app.post_message(cls(None, x, y, 0, 0, 1, False, False, False, screen_x=x, screen_y=y))
        click_footer("s")                        # the last post: sent
        await pilot.pause(0.1)
        click_footer("s")                        # the double click's second press: key disabled now
        await pilot.pause(0.3)
        assert status_of(state, 11) == "to_send"
        add_pending(state, 100, "a new post", now)
        pane.reload()
        await pilot.pause()
        await pilot.pause(0.8)                   # read it
        assert time.monotonic() - pane._shown_at > 0.3
        click_footer("s")                        # one click after reading decides
        await pilot.pause(0.3)
        assert status_of(state, 100) == "to_send"


async def test_a_footer_press_during_a_drag_leaves_nothing_behind(paths, state, now, monkeypatch):
    # while another widget holds the mouse the footer never sees the press: nothing to keep
    from textual import events
    from textual.widgets._footer import FooterKey
    monkeypatch.setattr(ReviewPane, "DECIDE_DELAY", 0.3)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(160, 40)) as pilot:
        await open_review(pilot)
        [k] = [w for w in app.query(FooterKey) if w.key == "s"]
        x, y = k.region.x + 1, k.region.y
        app.capture_mouse(app.query_one("#detail-text"))      # a drag in progress elsewhere
        app.post_message(events.MouseDown(None, x, y, 0, 0, 3, False, False, False, screen_x=x, screen_y=y))
        await pilot.pause()
        app.capture_mouse(None)
        assert not any(app.footer_downs.values())


@pytest.mark.parametrize("history_mode", [False, True])
async def test_a_read_error_is_said_once(paths, state, monkeypatch, history_mode):
    """#106: with nothing in the list (an empty queue, or History), the note and the
    empty-state body both said the same error. The note says it; the body says what isn't shown."""
    monkeypatch.setattr(review_mod, "UI_TIMEOUT", 0.1)
    if not history_mode:   # an empty queue
        db = sqlite3.connect(state)
        db.execute("UPDATE reviews SET status = 'sent' WHERE status = 'pending'")
        db.commit()
        db.close()
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        assert bool(pane._items) == history_mode
        if history_mode:
            await pilot.press("h")
            await pilot.pause()
        db = lock(state)
        try:
            pane.reload()
            await pilot.pause()
        finally:
            db.execute("ROLLBACK")
            db.close()
        body = str(app.query_one("#review-empty", Static).render())
        assert app.query_one("#review-empty", Static).display
        assert BUSY_NOTE in note(app)
        assert "busy" not in body and "locked" not in body
        assert "Nothing to show" in body


async def test_an_unreadable_state_file_is_worded_as_on_the_dashboard(paths, state):
    """Review run 1 (C2, W3): Review said "can't read the state file: file is not a database"."""
    from telenotify_tui.store import unreadable_note
    state.write_bytes(b"not a database" * 100)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        assert note(app) == unreadable_note("tele-notify-dryrun")
        assert "not a database" not in note(app)




async def test_y_copies_the_open_post_in_reading_order(paths):
    """#78: y copies the open post's text as stored (reading order; it is drawn right to left),
    through the terminal's clipboard (OSC 52), and a toast says so."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        await pilot.press("y")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert app.clipboard == UNCLEAR
        assert any("OSC 52" in n.message for n in app._notifications)   # no desktop here: not "Copied"


async def test_y_is_off_with_no_post_open(paths, state):
    db = sqlite3.connect(state)
    db.execute("UPDATE reviews SET status = 'discarded'")
    db.commit()
    db.close()
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        await pilot.press("y")
        await pilot.pause()
        assert app.clipboard == ""
        assert not app.query_one(ReviewPane).check_action("copy_post", ())


async def test_y_copies_what_a_selection_gives_without_bidi_controls(paths, state, now):
    """I5 (review of #137): y copied the stored text with its bidi override / embedding controls,
    while selecting the post with the mouse drops them (rtl.safe); y copies what the selection gives."""
    from textual.selection import Selection

    db = sqlite3.connect(state)
    db.execute("UPDATE reviews SET status = 'discarded'")
    db.commit()
    db.close()
    add_pending(state, 77, "before \u202eevil\u202c and \u202bembedded\u202c after", now)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        await pilot.press("y")
        await pilot.pause()
        await app.workers.wait_for_complete()
        view = app.query_one("#detail-text", PostView)
        selected, _ = view.get_selection(Selection(None, None))
        assert app.clipboard == selected == "before evil and embedded after"


async def test_y_and_the_selection_drop_direction_marks(paths, state, now):
    """Review run 2 (I5): direction marks and invisible characters (LRM, RLM, ALM, ZWSP, BOM; run 3:
    the soft hyphen and the word joiner) came along: a link with an RLM glued on pasted with it. y and the mouse selection both drop them, and
    Arabic still copies in reading order."""
    from textual.selection import Selection

    db = sqlite3.connect(state)
    db.execute("UPDATE reviews SET status = 'discarded'")
    db.commit()
    db.close()
    marked = ("\u200f" + UNCLEAR.replace(" ", " \u200b", 1) + "\u061c\nhttps://example.com/ap\u00adply\u2060\u200f\ufeff"
              " \u200emore")
    add_pending(state, 78, marked, now)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        await pilot.press("y")
        await pilot.pause()
        await app.workers.wait_for_complete()
        selected, _ = app.query_one("#detail-text", PostView).get_selection(Selection(None, None))
        assert app.clipboard == selected == UNCLEAR + "\nhttps://example.com/apply more"
