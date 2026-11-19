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
from telenotify_tui.screens import dashboard_data as dd
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
            assert "can't read the state file" in note(app)
            assert app.is_running
        finally:
            db.execute("ROLLBACK")
            db.close()
        assert status_of(state, 11) == "pending"
        app.query_one(ReviewPane).reload()                       # lock gone: back to normal
        await pilot.pause()
        assert note(app) == ""
        await pilot.press("s")
        await pilot.pause()
        assert status_of(state, 11) == "to_send"
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
        assert "Can't read the state file" in str(app.query_one("#review-empty", Static).content)
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
        assert "too late" in note(app) and "already sent" in note(app)
        assert status_of(state, 11) == "sent"
    assert [l["label"] for l in read_labels(labels)] == ["TRUE"]   # it did go out: the label stays


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
        assert "can't read the state file" in note(app)


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
