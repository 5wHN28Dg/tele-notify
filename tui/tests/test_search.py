import dataclasses
import sqlite3

from textual.widgets import Button, DataTable, Input, Select, Static, TextArea

from conftest import BAD, GOOD
from telenotify_tui import bot
from telenotify_tui.app import TeleNotifyApp
from telenotify_tui.screens import search_data
from telenotify_tui.screens.search import SearchPane
from telenotify_tui.store import Store


def text_of(app, selector: str) -> str:
    return str(app.query_one(selector, Static).render())


async def open_search(app, pilot):
    await pilot.press("3")
    await pilot.pause()
    return app.query_one(SearchPane)


def fake_binary(paths, tmp_path):
    b = tmp_path / "telenotify"
    b.write_text("#!/bin/sh\nexit 1\n")
    b.chmod(0o755)
    return dataclasses.replace(paths, binary=b)


async def test_lists_everything_then_typing_filters(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_search(app, pilot)
        table = app.query_one("#results", DataTable)
        assert table.row_count == 4
        assert "4 results" in text_of(app, "#count")
        app.query_one("#q", Input).focus()
        await pilot.press(*"محاسب")
        await pilot.pause(0.6)  # the typing pause triggers the search
        assert table.row_count == 1
        assert app.query_one(SearchPane)._selected.text == BAD
        app.query_one("#q", Input).value = ""
        await pilot.press(*"location", "enter")  # Enter searches right away
        assert table.row_count == 1 and "1 result" in text_of(app, "#count")


async def test_verdict_and_chat_filters(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_search(app, pilot)
        table = app.query_one("#results", DataTable)
        app.query_one("#verdict", Select).value = "reject"
        await pilot.pause()
        assert table.row_count == 2
        chat = app.query_one("#chat", Select)
        values = [v for _, v in chat._options]
        assert -1 in values and -2 in values and -3 in values  # judged chats plus known chats
        chat.value = -1
        await pilot.pause()
        assert table.row_count == 0 and "0 results" in text_of(app, "#count")
        app.query_one("#verdict", Select).value = "all"
        await pilot.pause()
        assert table.row_count == 2


async def test_selecting_a_row_shows_reasons_and_text(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_search(app, pilot)
        table = app.query_one("#results", DataTable)
        table.focus()
        await pilot.press("down")  # second newest: the review post
        await pilot.pause()
        reasons = text_of(app, "#detail-reasons")
        assert "• role" in reasons and "• no sign of level either way" in reasons
        assert "Basra Jobs" in text_of(app, "#search-detail-head") and "action: review" in text_of(app, "#search-detail-head")
        assert "ارسال السيرة الذاتية" in text_of(app, "#search-detail-text")


async def test_empty_text_says_so(paths, tmp_path):
    db = sqlite3.connect(paths.state)
    db.execute("UPDATE processed SET raw_text = NULL WHERE msg_id = 5")
    db.commit()
    db.close()
    paths = fake_binary(paths, tmp_path)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_search(app, pilot)
        app.query_one("#q", Input).value = "no matching role"
        await pilot.pause()
        app.query_one(SearchPane).run_search()
        await pilot.pause()
        assert app.query_one("#results", DataTable).row_count == 1
        assert "wasn't stored" in text_of(app, "#search-detail-text")
        assert app.query_one("#rejudge", Button).disabled


async def test_rejudge_shows_then_and_now_and_flags_a_change(paths, tmp_path, monkeypatch):
    calls = []

    async def fake_check(binary, config, text, scrape=False, show_text=False):
        calls.append((text, scrape, show_text))
        return bot.Verdict("publish", ["role: محاسب", "location: baghdad"], text="read: " + text)

    monkeypatch.setattr(bot, "check", fake_check)
    app = TeleNotifyApp(fake_binary(paths, tmp_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_search(app, pilot)
        app.query_one("#verdict", Select).value = "reject"
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("j")
        await app.workers.wait_for_complete()
        await pilot.pause()
        out = text_of(app, "#judge-result")
        assert "then: reject" in out and "now: publish" in out and "CHANGED" in out
        assert "• location: baghdad" in out
        assert calls == [(BAD, False, False)]
        await pilot.press("J")  # following links shows what was read
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert calls[-1] == (BAD, True, True)
        assert "read: " + BAD in text_of(app, "#judge-result")
        assert pane._selected.text == BAD


async def test_rejudge_error_is_shown_not_raised(paths, tmp_path, monkeypatch):
    async def broken(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot, "check", broken)
    app = TeleNotifyApp(fake_binary(paths, tmp_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_search(app, pilot)
        await pilot.click("#rejudge")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "Couldn't judge: boom" in text_of(app, "#judge-result")


async def test_missing_binary_disables_rejudge(paths):
    app = TeleNotifyApp(dataclasses.replace(paths, binary=None), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_search(app, pilot)
        assert app.query_one("#rejudge", Button).disabled
        assert "wasn't found" in text_of(app, "#judge-note")


async def test_missing_state_file(paths, tmp_path):
    app = TeleNotifyApp(dataclasses.replace(paths, state=tmp_path / "nope.sqlite3"), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_search(app, pilot)
        assert app.query_one("#results", DataTable).row_count == 0
        assert "no state file" in text_of(app, "#count")


async def test_paste_mode_returns_a_verdict(paths, tmp_path, monkeypatch):
    async def fake_check(binary, config, text, scrape=False, show_text=False):
        return bot.Verdict("review", ["no sign of level"]) if "test" in text else bot.Verdict("reject")

    monkeypatch.setattr(bot, "check", fake_check)
    app = TeleNotifyApp(fake_binary(paths, tmp_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_search(app, pilot)
        app.query_one("#results", DataTable).focus()
        await pilot.press("p")
        assert pane.has_class("pasting")
        area = app.query_one("#paste-text", TextArea)
        assert area.has_focus
        await pilot.press(*"a test post")
        await pilot.click("#paste-judge")
        await app.workers.wait_for_complete()
        await pilot.pause()
        out = text_of(app, "#paste-result")
        assert "verdict: review" in out and "• no sign of level" in out
        await pilot.press("escape")
        assert not pane.has_class("pasting")


async def test_rejudge_good_post_with_the_real_binary(paths, binary):
    app = TeleNotifyApp(dataclasses.replace(paths, binary=binary), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_search(app, pilot)
        app.query_one("#q", Input).focus()
        await pilot.press(*"entry level", "enter")  # Enter also moves to the results
        assert pane._selected.text == GOOD
        await pilot.press("j")
        await app.workers.wait_for_complete()
        await pilot.pause()
        out = text_of(app, "#judge-result")
        assert "now: publish" in out and "(same)" in out, out


def test_search_data_chat_filter_and_chats(state):
    with Store(state) as s:
        assert [j.msg_id for j in search_data.search(s, "", chat_id=-2)] == [5, 6]
        assert [j.msg_id for j in search_data.search(s, "", verdict="publish", chat_id=-1)] == [10]
        titles = [t for _, t in search_data.judged_chats(s)]
        assert titles == ["Basra Jobs", "Blocked Channel", "Family", "Iraq Jobs"]
    assert search_data.split_reasons("a; b ;; c") == ["a", "b", "c"]
