"""The keys around PR #137's review run 7 and #140: the shown tab's keys work from the tab bar; a
double or triple click on the service menu can't fall through onto the confirm's action button;
and the single-letter shortcuts can be turned off (Settings, Screen), with the ctrl keys, the number
keys, esc, enter, the arrows and the dialogs' y / n still working. systemctl is always mocked."""

from __future__ import annotations

import asyncio
import dataclasses
import errno
import json
import os
import stat
import time
from pathlib import Path

import pytest
from textual.screen import Screen
from textual.widgets import (
    Checkbox,
    Input,
    OptionList,
    Select,
    TabbedContent,
    Tabs,
    TextArea,
)
from textual.widgets._footer import FooterKey

from audit.svg import parse
from telenotify_tui import bot, prefs
from telenotify_tui.app import MainScreen, TeleNotifyApp, is_letter_key
from telenotify_tui.screens.dashboard import ConfirmScreen, DashboardPane, ServiceMenu
from telenotify_tui.screens.review import ReviewPane
from telenotify_tui.screens.search import SearchPane
from telenotify_tui.screens.settings import ConfirmModal, SettingsPane


@pytest.fixture(autouse=True)
def fake_systemctl(monkeypatch):
    calls: list[tuple[str, ...]] = []

    async def state(mode: str) -> str:
        return "active" if mode == "dry-run" else "inactive"

    async def action(mode: str, what: str) -> tuple[bool, str]:
        calls.append((mode, what))
        return True, ""

    monkeypatch.setattr(bot, "service_state", state)
    monkeypatch.setattr(bot, "service_action", action)
    return calls


async def settle(app, pilot) -> None:
    for _ in range(3):
        await pilot.pause()
        await app.workers.wait_for_complete()
    await pilot.pause()


def tab_bar(app) -> Tabs:
    main = app.query_one(TabbedContent)
    bar = main.query_one(Tabs)
    assert bar.parent is main
    return bar


def keys_in_effect(app) -> dict[str, str]:
    """What the footer and the keys panel show: key -> action."""
    return {key: b.binding.action for key, b in app.active_bindings.items()}


async def letters_off(app, pilot) -> None:
    """Turn the letter keys off the way a user does: Settings, Screen, the switch."""
    await pilot.press("4")
    await settle(app, pilot)
    app.query_one(SettingsPane).query_one("#sections", TabbedContent).active = "sec-screen"
    await pilot.pause()
    box = app.query_one("#screen-letter-keys", Checkbox)
    assert box.value and str(box.label) == "on"
    await pilot.click(box)
    await pilot.pause()
    assert not box.value and str(box.label) == "off"


# ------------------------------------------------------------- run 7, fail 1

async def test_the_dashboards_keys_work_with_the_focus_on_the_tab_bar(paths, fake_systemctl):
    """Run 7 fail 1: with the focus on the tab bar, `s`, `p` and `v` did nothing while the Bot box
    still said "s service"; the keys panel dropped them too."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        pane = app.query_one(DashboardPane)
        bar = tab_bar(app)
        bar.focus()
        await pilot.pause()
        keys = keys_in_effect(app)
        assert keys["s"] == "service_menu" and keys["p"] == "pick_period" and keys["v"] == "flip_top"
        await pilot.press("v")
        await pilot.pause()
        assert pane.has_class("-show-activity")
        await pilot.press("v")
        await pilot.pause()
        assert not pane.has_class("-show-activity")
        bar.focus()
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, ServiceMenu)
        await pilot.press("escape")
        await pilot.pause()
        bar.focus()
        await pilot.press("p")
        await pilot.pause()
        assert pane.query_one("#period", Select).expanded
        assert not fake_systemctl


async def test_review_keeps_its_own_s_from_the_tab_bar(paths, fake_systemctl):
    """From the tab bar, the shown tab's keys are its own: on Review `s` is Send, never the menu."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await settle(app, pilot)
        tab_bar(app).focus()
        await pilot.pause()
        assert keys_in_effect(app)["s"] == "send"
        assert "service_menu" not in keys_in_effect(app).values()
        await pilot.press("s")
        await pilot.pause()
        assert not isinstance(app.screen, ServiceMenu)


# ------------------------------------------------------------- run 7, fail 2

SIZES = [(80, 24), (100, 30), (200, 50)]


@pytest.mark.parametrize("size", SIZES)
async def test_no_service_menu_row_lies_over_the_confirms_action_button(paths, size):
    """Run 7 fail 2: the menu's Restart row lay over "Restart the bot (y)" at every size. Now no
    cell of any row is on the confirm's action button once it opens, for any of the three."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=size) as pilot:
        await settle(app, pilot)
        for row in range(3):
            await pilot.press("s")
            await pilot.pause()
            items = app.screen.query_one(OptionList).region
            cells = [(x, items.y + row) for x in range(items.x, items.right)]
            for _ in range(row):
                await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            yes = app.screen.query_one("#confirm-yes")
            on_yes = [c for c in cells if app.screen.get_widget_at(*c)[0] is yes]
            assert not on_yes, (size, row, on_yes, yes.region)
            await pilot.press("escape")
            await pilot.pause()


@pytest.mark.parametrize("size", SIZES)
async def test_a_triple_click_on_a_menu_row_never_confirms(paths, fake_systemctl, monkeypatch, size):
    """Run 7 fail 2, as it was found: a click on a row opens the confirm, a second on the same cell
    was refused as too soon, and a third 0.6 s later confirmed. Clicked at the row's first, middle
    and last cells, with the real arm delay: nothing is confirmed."""
    monkeypatch.setattr(ConfirmScreen, "ARM_DELAY", 0.5)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=size) as pilot:
        await settle(app, pilot)
        for row in range(3):
            await pilot.press("s")
            await pilot.pause()
            items = app.screen.query_one(OptionList).content_region   # the rows' text, not the padding
            for x in (items.x, items.x + items.width // 2, items.right - 1):
                if not isinstance(app.screen, ServiceMenu):
                    await pilot.press("s")
                    await pilot.pause()
                await pilot.click(offset=(x, items.y + row))
                await pilot.pause()
                assert isinstance(app.screen, ConfirmScreen)
                await pilot.click(offset=(x, items.y + row))
                await asyncio.sleep(0.6)
                await pilot.click(offset=(x, items.y + row))
                await settle(app, pilot)
                assert not fake_systemctl, (size, row, x)
                assert isinstance(app.screen, ConfirmScreen)
                await pilot.press("escape")
                await pilot.pause()


async def test_the_early_press_note_doesnt_ask_for_another_press(paths, monkeypatch):
    """Run 7 fail 2: "Too quick: press again to confirm." invited the press that confirmed."""
    monkeypatch.setattr(ConfirmScreen, "ARM_DELAY", 5.0)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        note = str(app.screen.query_one("#confirm-wait").render())
        assert note == "Ignored, too soon. Read the question, then confirm."
        assert "again" not in note


# ------------------------------------------------------------------- #140

async def test_letter_keys_are_on_by_default_and_nothing_is_written(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        assert app.letter_keys
        assert keys_in_effect(app)["s"] == "service_menu"
    assert not prefs.path_for(paths.config).exists()


async def test_turning_the_letter_keys_off(paths, fake_systemctl):
    """#140's main path: Settings, Screen, the switch. Then no single-letter key works or is shown
    (footer and keys panel), the ones named on screen are gone, the number keys still switch tabs,
    each letter key's action is in the command palette, and the choice is kept for next time."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        await letters_off(app, pilot)
        assert json.loads(prefs.path_for(paths.config).read_text(encoding="utf-8")) == {"letter_keys": False}
        assert str(app.query_one("#test").label) == "Test"
        await pilot.press("1")
        await settle(app, pilot)
        pane = app.query_one(DashboardPane)
        for focus in (pane.query_one("#feed"), tab_bar(app)):
            focus.focus()
            await pilot.pause()
            assert not [k for k in keys_in_effect(app) if is_letter_key(k)], keys_in_effect(app)
        pane.query_one("#feed").focus()
        for key in "sfcapdv":
            await pilot.press(key)
            await pilot.pause()
            assert app.screen is app.screen_stack[0], key
        assert not pane.has_class("-show-activity")
        assert str(pane.query_one("#nav-feed").label) == "Live feed"
        assert pane.query_one("#dash-bot").border_subtitle == "ctrl+p: Service, Flip"
        titles = {c.title: c for c in app.get_system_commands(app.screen)}
        assert "Dashboard: Service" in titles and "Dashboard: Flip" in titles
        await titles["Dashboard: Service"].callback()
        await pilot.pause()
        assert isinstance(app.screen, ServiceMenu)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("2")   # the number keys still work
        await settle(app, pilot)
        assert app.query_one(TabbedContent).active == "review"
        assert "send" not in keys_in_effect(app).values()
        await pilot.press("s")
        await settle(app, pilot)
        assert not fake_systemctl
    again = TeleNotifyApp(paths, refresh_seconds=0)
    async with again.run_test(size=(80, 24)) as pilot:
        await settle(again, pilot)
        assert not again.letter_keys
        assert not [k for k in keys_in_effect(again) if is_letter_key(k)]
        assert not again.query_one("#screen-letter-keys", Checkbox).value


async def test_the_dialogs_keep_their_keys_with_the_letter_keys_off(paths, fake_systemctl):
    """#140: the menu's arrows, enter and esc and the confirms' y / n still work."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await letters_off(app, pilot)
        # Settings' ConfirmModal: unsaved changes, then ctrl+q asks; n says no
        settings = app.query_one(SettingsPane)
        settings.edited["max_experience_years"] = 9
        settings.changed()
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("n")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmModal) and app.is_running
        # the service menu from the palette's command, then the confirm's y
        await pilot.press("1")
        await settle(app, pilot)
        service = next(c for c in app.get_system_commands(app.screen) if c.title == "Dashboard: Service")
        await service.callback()
        await pilot.pause()
        assert isinstance(app.screen, ServiceMenu)
        await pilot.press("down", "enter")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("n")
        await settle(app, pilot)
        assert not fake_systemctl
        await service.callback()
        await pilot.pause()
        await pilot.press("down", "enter")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)
        assert fake_systemctl == [("dry-run", "stop")]


async def test_the_search_boxes_still_type_letters_with_the_letter_keys_off(paths):
    """#140: the paste box (opened from the palette: its `p` is off) and the search box take every
    letter, `p` and `j` included."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await letters_off(app, pilot)
        await pilot.press("3")
        await settle(app, pilot)
        search = app.query_one(SearchPane)
        search._show_detail(None)   # nothing selected: the line naming how to paste
        await pilot.pause()
        assert "use ctrl+p, Paste a post," in str(search.query_one("#search-detail-head").render())
        assert str(search.query_one("#rejudge").label) == "Re-judge now"
        q = search.query_one("#q", Input)
        q.focus()
        await pilot.press(*"pjys")
        await pilot.pause()
        assert q.value == "pjys"
        paste = next(c for c in app.get_system_commands(app.screen) if c.title == "Search: Paste a post")
        await paste.callback()
        await pilot.pause()
        assert search.has_class("pasting")
        box = search.query_one("#paste-text", TextArea)
        box.focus()
        await pilot.press(*"spy job")
        await pilot.pause()
        assert box.text == "spy job"
        await pilot.press("escape")
        await pilot.pause()
        assert not search.has_class("pasting")


async def test_turning_them_back_on(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await letters_off(app, pilot)
        await pilot.click("#screen-letter-keys")
        await pilot.pause()
        assert app.letter_keys
        assert json.loads(prefs.path_for(paths.config).read_text(encoding="utf-8")) == {"letter_keys": True}
        assert str(app.query_one("#test").label) == "Test t"
        await pilot.press("1")
        await settle(app, pilot)
        assert keys_in_effect(app)["s"] == "service_menu"


@pytest.mark.parametrize("text", ["not json", "[]", '{"letter_keys": "no"}'])
def test_an_unusable_screen_file_means_the_defaults(paths, text):
    prefs.path_for(paths.config).write_text(text, encoding="utf-8")
    assert prefs.load(paths.config).letter_keys is True


async def test_a_screen_file_that_cant_be_written_is_said(paths):
    """The switch still holds for this run, and the Screen tab says it wasn't kept."""
    prefs.path_for(paths.config).mkdir()   # a folder where the file goes: it can't be written
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await letters_off(app, pilot)
        assert not app.letter_keys
        assert "couldn't save that to" in str(app.query_one("#screen-note").render())


async def test_the_dashboards_view_buttons_name_their_keys_while_they_work(paths):
    """The view buttons' "[f]", "[c]" and "[a]" were read as markup and never drawn; with the
    letter keys off they're left out, since the keys do nothing then."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        labels = {v: str(app.query_one(f"#nav-{v}").label) for v in ("feed", "chats", "alerts")}
        assert labels == {"feed": "Live feed [f]", "chats": "Chats [c]", "alerts": "Alerts & health [a]"}
        drawn = "\n".join(parse(app.export_screenshot(), 80, 24).text())
        assert "Live feed [f]" in drawn and "Alerts & health [a]" in drawn and "need a look" in drawn
        await letters_off(app, pilot)
        await pilot.press("1")
        await settle(app, pilot)
        assert str(app.query_one("#nav-feed").label) == "Live feed"
        assert "press a" not in str(app.query_one("#status").render())


# ------------------------------------------------- review run 8 and the merge of #141

def keys_off_from_the_start(paths) -> None:
    prefs.save(paths.config, prefs.Prefs(letter_keys=False))


async def test_review_says_nothing_about_a_letter_press_with_the_letter_keys_off(paths, state, now, monkeypatch):
    """#141's ReviewPane.on_key notes s / k / d pressed while a new post settles ("has only just
    appeared") and u while a History row settles ("…then press u"). With the letter keys off those
    keys do nothing, so they mustn't raise the notes."""
    from test_review import add_decided, add_pending, note, open_review, status_of

    monkeypatch.setattr(ReviewPane, "DECIDE_DELAY", 30.0)
    add_pending(state, 7, "second post", now - 10)
    for i, status in enumerate(["discarded", "kept"]):
        add_decided(state, 50 + i, status, now - 10 - i)
    keys_off_from_the_start(paths)
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("down")          # the next post appears and settles
        await pilot.pause()
        assert pane._settling
        for key in "skd":
            await pilot.press(key)
            await pilot.pause()
            assert "only just appeared" not in note(app), key
        assert status_of(state, 7) == "pending"
        await pilot.click("#btn-history")
        await pilot.pause()
        pane._hist_shown_at = time.monotonic()   # a row has only just come under the cursor
        assert pane._undo_settling
        await pilot.press("u")
        await pilot.pause()
        assert "then press" not in note(app)
        assert status_of(state, 50) == "discarded"


async def test_reviews_notes_name_the_undo_button_with_the_letter_keys_off(paths, state, now, monkeypatch):
    """The notes' key names follow the switch: "(u undoes)" and "then press u" name the Undo button
    while the letter keys are off."""
    from test_review import add_decided, note, open_review

    for i, status in enumerate(["discarded", "kept"]):
        add_decided(state, 50 + i, status, now - 10 - i)
    keys_off_from_the_start(paths)
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        pane._shown_at -= 60   # the open post has been read
        await pilot.click("#btn-discard")
        await pilot.pause()
        assert "(Undo undoes)" in note(app) and "(u undoes)" not in note(app)
        await pilot.click("#btn-history")
        await pilot.pause()
        pane._hist_shown_at = time.monotonic()   # the row under the cursor has only just come
        monkeypatch.setattr(ReviewPane, "DECIDE_DELAY", 30.0)
        await pilot.click("#btn-undo")
        await pilot.pause()
        assert "then press Undo" in note(app) and "press u" not in note(app)


def footer_keys(app) -> set[str]:
    return {k.key for k in app.query(FooterKey)}


@pytest.mark.parametrize("how", ["4", "right"])
async def test_the_footer_follows_the_tab_shown_from_the_tab_bar(paths, how):
    """Run 8 fail 1: with the focus on the tab bar, 3 then 4 (or the right arrow) left Search's keys
    in the footer and the keys panel over Settings."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("3")
        await settle(app, pilot)
        tab_bar(app).focus()
        await pilot.pause()
        assert "j" in footer_keys(app)
        await pilot.press(how)
        await settle(app, pilot)
        assert app.query_one(TabbedContent).active == "settings"
        assert isinstance(app.focused, Tabs)
        shown = footer_keys(app)
        assert "j" not in shown and "p" not in shown, shown
        assert "t" in keys_in_effect(app) and "j" not in keys_in_effect(app)
        assert "ctrl+s" in shown


async def test_the_chats_views_hint_follows_the_switch(paths):
    """Run 8 fail 2: the Chats view's "(p: change the period, d: pick dates)" stayed with the keys off."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        line = app.query_one("#chats-period")
        assert "(p: change the period, d: pick dates)" in str(line.render())
        app.set_letter_keys(False)
        await pilot.pause()
        assert "p: change" not in str(line.render())
        assert "(ctrl+p: Period, Dates)" in str(line.render())


async def test_the_palette_turns_the_letter_keys_off_and_on(paths):
    """Run 8 fail 3: ctrl+p "letter" found nothing; the way back was Settings, Screen."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        titles = {c.title: c for c in app.get_system_commands(app.screen)}
        assert "Letter keys: turn off" in titles and "Letter keys: turn on" not in titles
        titles["Letter keys: turn off"].callback()
        await pilot.pause()
        assert not app.letter_keys
        assert not app.query_one("#screen-letter-keys", Checkbox).value   # the switch shows it too
        titles = {c.title: c for c in app.get_system_commands(app.screen)}
        assert "Letter keys: turn on" in titles
        titles["Letter keys: turn on"].callback()
        await pilot.pause()
        assert app.letter_keys and app.query_one("#screen-letter-keys", Checkbox).value
        assert json.loads(prefs.path_for(paths.config).read_text(encoding="utf-8")) == {"letter_keys": True}


async def test_the_palette_command_is_found_by_typing_letter(paths):
    """Through the palette itself: ctrl+p, "letter keys", enter."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        await pilot.press("ctrl+p")
        await pilot.pause()
        await pilot.press(*"letter keys")
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        assert not app.letter_keys


async def test_the_main_screen_says_while_the_letter_keys_are_off(paths):
    """Run 8 fail 3: nothing on the main screen said they were off. The header says so first, so a
    narrow window doesn't cut it, and it is drawn at 80 x 24."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        assert "letter keys off" not in app.sub_title
        app.set_letter_keys(False)
        await pilot.pause()
        assert app.sub_title.startswith("letter keys off (ctrl+p) · ")
        assert "letter keys off (ctrl+p)" in "\n".join(parse(app.export_screenshot(), 80, 24).text())
        app.set_letter_keys(True)
        await pilot.pause()
        assert "letter keys off" not in app.sub_title


def letters_off_notes(app) -> list[str]:
    return [n.message for n in app._notifications if "Letter keys are off" in n.message]


async def test_a_letter_press_with_the_keys_off_says_why_once(paths):
    """Run 8 fail 3: a letter press did nothing and said nothing. The first one that would have been a
    shortcut says how to turn them on; later ones don't nag, and a box that types never says it."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        app.set_letter_keys(False)
        await pilot.pause()
        await pilot.press("3")
        await settle(app, pilot)
        q = app.query_one("#q", Input)
        q.focus()
        await pilot.press("p", "j")             # typed into the search box: not a shortcut
        await pilot.pause()
        assert q.value == "pj" and not letters_off_notes(app)
        app.query_one("#results").focus()
        await pilot.press("j")
        await pilot.pause()
        assert letters_off_notes(app) == ["Letter keys are off: ctrl+p → Letter keys: turn on"]
        await pilot.press("j", "p", "y")
        await pilot.pause()
        assert len(letters_off_notes(app)) == 1   # once, not on every press


def test_a_deeply_nested_screen_file_means_the_defaults(paths):
    """Run 8 fail 4: 200,000 "[" made load raise RecursionError, so the screen wouldn't start."""
    prefs.path_for(paths.config).write_text("[" * 200_000, encoding="utf-8")
    assert prefs.load(paths.config).letter_keys is True


async def test_the_screen_starts_with_a_deeply_nested_screen_file(paths):
    prefs.path_for(paths.config).write_text("[" * 200_000, encoding="utf-8")
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert app.letter_keys


def test_a_failed_save_leaves_no_temporary_file(paths):
    """Run 8 fail 4: with a folder where screen.json goes, save left its temporary file behind."""
    target = prefs.path_for(paths.config)
    target.mkdir()
    before = set(target.parent.iterdir())
    with pytest.raises(OSError):
        prefs.save(paths.config, prefs.Prefs(letter_keys=False))
    assert set(target.parent.iterdir()) == before


async def test_a_textual_that_ignores_the_override_turns_the_switch_off_visibly(paths, monkeypatch):
    """Run 8 advice: the switch rests on MainScreen extending Textual's private Screen._binding_chain.
    If Textual stopped reading it, the switch would silently do nothing. The start-up check finds it:
    the letter keys stay on (as they would anyway), the switch is greyed and says why."""
    monkeypatch.setattr(MainScreen, "_binding_chain", Screen._binding_chain)   # Textual no longer calls ours
    keys_off_from_the_start(paths)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        assert not app.letter_keys_work and app.letter_keys
        assert "letter keys off" not in app.sub_title
        assert not [c for c in app.get_system_commands(app.screen) if c.title.startswith("Letter keys")]
        await pilot.press("4")
        await settle(app, pilot)
        box = app.query_one("#screen-letter-keys", Checkbox)
        assert box.disabled and box.value
        assert "doesn't let the screen turn the letter keys off" in str(app.query_one("#screen-note").render())


async def test_the_start_up_check_passes_on_this_textual(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert app.letter_keys_work


# ------------------------------------------------------------------- run 9

async def test_a_late_handled_tap_from_the_tab_bar_doesnt_decide(paths, state, now, monkeypatch):
    """Run 9 fail 1: from Review's tab bar the bindings ran but ReviewPane.on_key never saw the key,
    so #141's guard took the time the key was *handled*. A second s made 0.1 s after the next post
    appeared but handled late approved it unread. Mirrors test_review.py's clock-proof test."""
    from test_review import add_pending, open_review, status_of
    from textual import events

    monkeypatch.setattr(ReviewPane, "DECIDE_DELAY", 0.4)
    add_pending(state, 100, "second post never shown", now - 10)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        pane._shown_at -= 10                     # the first post has been read
        tab_bar(app).focus()
        await pilot.pause()
        for key in ("s", "k", "d"):
            assert keys_in_effect(app)[key] in ("send", "keep", "discard")
        await pilot.press("s")
        await pilot.pause()
        assert status_of(state, 11) == "to_send"
        assert isinstance(app.focused, Tabs)
        shown = pane._shown_at                   # the second post appeared now
        for key in ("s", "k", "d"):
            early = events.Key(key, key)
            early.time = shown + 0.1             # made inside the delay...
            await pilot.pause(0.6)               # ...handled after it
            app.post_message(early)
            await pilot.pause()
            assert status_of(state, 100) == "pending", key


async def test_a_late_handled_undo_from_the_tab_bar_undoes_one_row(paths, state, now, monkeypatch):
    """Run 9 fail 1, History: a late second u from the tab bar undid a second row."""
    from test_review import add_decided, open_review, status_of
    from textual import events

    monkeypatch.setattr(ReviewPane, "DECIDE_DELAY", 0.4)
    for i, status in enumerate(["discarded", "kept", "discarded"]):
        add_decided(state, 50 + i, status, now - 10 - i)
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("h")
        await pilot.pause()
        pane._hist_shown_at -= 10                # the first row has been read
        tab_bar(app).focus()
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()
        assert [m for m in (50, 51, 52) if status_of(state, m) == "pending"] == [50]
        came = pane._hist_shown_at               # the next row came under the cursor now
        early = events.Key("u", "u")
        early.time = came + 0.1
        await pilot.pause(0.6)
        app.post_message(early)
        await pilot.pause()
        assert [m for m in (50, 51, 52) if status_of(state, m) == "pending"] == [50]


async def test_an_early_tap_from_the_tab_bar_says_why(paths, state, now, monkeypatch):
    """Run 9 fail 2: from the tab bar, s while a post settles was dropped silently."""
    from test_review import add_pending, note, open_review, status_of

    monkeypatch.setattr(ReviewPane, "DECIDE_DELAY", 30.0)
    add_pending(state, 7, "second post", now - 10)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        await pilot.press("down")                # the next post appears and settles
        await pilot.pause()
        assert pane._settling
        tab_bar(app).focus()
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert "only just appeared" in note(app) and status_of(state, 7) == "pending"


@pytest.mark.parametrize("start_on", [True, False])
async def test_reviews_note_rewords_when_the_switch_changes(paths, state, now, start_on):
    """Run 9 fail 3: "(u undoes)" stayed after the letter keys went off, and "(Undo undoes)" after
    they came back on."""
    from test_review import note, open_review

    if not start_on:
        keys_off_from_the_start(paths)
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_review(pilot)
        pane = app.query_one(ReviewPane)
        pane._shown_at -= 60
        await pilot.click("#btn-discard")
        await pilot.pause()
        before, after = ("(u undoes)", "(Undo undoes)") if start_on else ("(Undo undoes)", "(u undoes)")
        assert before in note(app)
        app.set_letter_keys(not start_on)
        await pilot.pause()
        assert after in note(app) and before not in note(app)
        app.set_letter_keys(start_on)
        await pilot.pause()
        assert before in note(app) and after not in note(app)


async def test_the_palettes_switch_says_when_it_isnt_kept(paths):
    """Run 9 fail 4: the palette's command threw away "not saved for next time"; nothing said it,
    and the Settings Screen note stayed empty."""
    prefs.path_for(paths.config).mkdir()     # a folder where screen.json goes: it can't be written
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        command = next(c for c in app.get_system_commands(app.screen) if c.title == "Letter keys: turn off")
        command.callback()
        await pilot.pause()
        assert not app.letter_keys
        said = [n.message for n in app._notifications if "couldn't save that to" in n.message]
        assert said and said[0].startswith("Letter keys: off for now, but couldn't save that to")
        assert "couldn't save that to" in str(app.query_one("#screen-note").render())


async def test_a_fifo_named_screen_json_doesnt_stop_the_screen(paths):
    """Run 9 fail 5: a FIFO there made prefs.load wait forever, so the screen never started."""
    fifo = prefs.path_for(paths.config)
    os.mkfifo(fifo)
    try:
        loaded = await asyncio.wait_for(asyncio.to_thread(prefs.load, paths.config), timeout=5)
        assert loaded.letter_keys is True
    finally:   # a reader still waiting (the bug) gets a writer and then end of file, so nothing hangs
        try:
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        except OSError:
            pass   # no reader waiting: nothing to release


def test_an_oversized_screen_file_means_the_defaults(paths):
    """Run 9 fail 5: only a regular file of at most 64 KiB is read (a link to an endless device
    would read until memory runs out)."""
    big = '{"letter_keys": false' + " " * (prefs.MAX_BYTES + 10) + "}"
    prefs.path_for(paths.config).write_text(big, encoding="utf-8")
    assert prefs.load(paths.config).letter_keys is True
    small = '{"letter_keys": false' + " " * 100 + "}"
    prefs.path_for(paths.config).write_text(small, encoding="utf-8")
    assert prefs.load(paths.config).letter_keys is False


def test_a_link_to_a_device_means_the_defaults(paths):
    prefs.path_for(paths.config).symlink_to("/dev/zero")
    assert prefs.load(paths.config).letter_keys is True


# ------------------------------------------------------------------ run 10

@pytest.mark.parametrize("code, why, todo", [
    (errno.EISDIR, "it's a folder", "Remove or rename that folder"),
    (errno.EACCES, "no permission to write there", "Give yourself write permission to that folder"),
    (errno.ENOTDIR, "a file is in the way where its folder should be", "Remove or rename that file"),
    (errno.ENAMETOOLONG, "the path is too long", "Move config.json to a shorter path"),
    (errno.ELOOP, "its path loops through links", "Fix the links in that path"),
    (errno.EROFS, "the disk is read-only", "Make that disk writable"),
    (errno.ENOENT, "its folder is missing", "Create that folder"),
    (errno.EIO, "the system refused", "Check that file"),
])
def test_the_not_saved_message_is_in_plain_words(paths, code, why, todo):
    """Run 10: "not saved for next time: Is a directory" didn't say what it meant or what to do. The
    message says both, names the file, and leaves the system's own words out (they're logged)."""
    error = OSError(code, os.strerror(code))
    for on in (False, True):
        said = prefs.not_saved(paths.config, on, error)
        assert said == (f"Letter keys: {'on' if on else 'off'} for now, but couldn't save that to "
                        f"{prefs.shown_path(prefs.path_for(paths.config))} ({why}). {todo} to keep this setting.")
        assert os.strerror(code) not in said


def test_the_file_is_named_from_the_home_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = tmp_path / ".config" / "tele-notify" / "config.json"
    assert prefs.shown_path(prefs.path_for(config)) == "~/.config/tele-notify/screen.json"
    assert prefs.shown_path(Path("/etc/screen.json")) == "/etc/screen.json"


async def test_the_not_saved_message_is_whole_at_80x24(paths, monkeypatch):
    """Where it's shown, the Settings Screen note and the palette's toast, the whole message is drawn
    at 80 x 24, with a folder where screen.json goes."""
    monkeypatch.setenv("HOME", str(paths.config.parent))   # the file shows as ~/screen.json, as at home
    prefs.path_for(paths.config).mkdir()
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24), notifications=True) as pilot:   # toasts drawn
        await settle(app, pilot)
        message = ("Letter keys: off for now, but couldn't save that to ~/screen.json (it's a folder). "
                   "Remove or rename that folder to keep this setting.")
        command = next(c for c in app.get_system_commands(app.screen) if c.title == "Letter keys: turn off")
        command.callback()
        await settle(app, pilot)
        toast = app.screen.query_one("Toast")
        lines = parse(app.export_screenshot(), 80, 24).text()
        inside = [lines[y][toast.region.x:toast.region.right] for y in range(toast.region.y, toast.region.bottom)]
        flat = " ".join(" ".join(line.strip(" ▌▐▊▎") for line in inside).split())
        assert message in flat, inside
        app.clear_notifications()
        await pilot.press("4")
        await settle(app, pilot)
        app.query_one(SettingsPane).query_one("#sections", TabbedContent).active = "sec-screen"
        await pilot.pause()
        assert str(app.query_one("#screen-note").render()) == f"warning: {message}"
        flat = " ".join(" ".join(parse(app.export_screenshot(), 80, 24).text()).split())
        assert f"warning: {message}" in flat, flat


def open_fds() -> int:
    return len(os.listdir("/proc/self/fd"))


def test_reading_a_folder_named_screen_json_leaks_no_descriptor(paths):
    """Run 10: os.fdopen refused the folder before the `with` owned the descriptor, so each read
    leaked one."""
    prefs.path_for(paths.config).mkdir()
    before = open_fds()
    for _ in range(20):
        assert prefs.load(paths.config).letter_keys is True
    assert open_fds() == before


# ------------------------------------------------------------------ run 11

MARKUP_FOLDERS = ["x[/]y", "notes[", "[@click=app.bell]RING[/]"]


def config_in(paths, folder: str):
    """The conftest config, moved into a folder with this name."""
    where = paths.config.parent / folder
    where.mkdir(parents=True)   # "x[/]y" is "x[" holding "]y"
    moved = where / "config.json"
    moved.write_text(paths.config.read_text(encoding="utf-8"), encoding="utf-8")
    return dataclasses.replace(paths, config=moved)


@pytest.mark.parametrize("folder", MARKUP_FOLDERS)
async def test_a_path_with_markup_in_the_not_saved_toast_stays_text(paths, monkeypatch, folder):
    """Run 11 fail 1 (sev 2): the palette's not-saved toast read the config path as markup. A folder
    named "x[/]y" crashed the screen (unsaved Settings edits lost); "[@click=app.bell]…" made a
    clickable action. Now the path is shown as it is and nothing runs."""
    paths = config_in(paths, folder)
    prefs.path_for(paths.config).mkdir()          # can't be written: the toast names the path
    rung: list[bool] = []
    app = TeleNotifyApp(paths, refresh_seconds=0)
    monkeypatch.setattr(app, "bell", lambda: rung.append(True))
    async with app.run_test(size=(120, 40), notifications=True) as pilot:
        await settle(app, pilot)
        command = next(c for c in app.get_system_commands(app.screen) if c.title == "Letter keys: turn off")
        command.callback()
        await settle(app, pilot)
        toast = app.screen.query_one("Toast")
        said = [n.message for n in app._notifications if "couldn't save that" in n.message]
        assert said and folder in said[0]
        lines = parse(app.export_screenshot(), 120, 40).text()
        inside = [lines[y][toast.region.x:toast.region.right] for y in range(toast.region.y, toast.region.bottom)]
        drawn = "".join(line.strip(" ▌▐▊▎") for line in inside)   # the toast wraps the long path
        assert folder in drawn, inside
        ring = [(x, y) for y in range(toast.region.y, toast.region.bottom)
                for x in [lines[y].find("RING", toast.region.x)] if x >= 0]
        if ring:   # a click on the word a [@click] link would make
            await pilot.click(offset=ring[0])
            await settle(app, pilot)
        assert app.is_running and not rung


async def test_the_no_corpus_note_shows_a_markup_path_as_text(paths, monkeypatch, tmp_path):
    """Run 11: "No corpus at {corpus}" carries a path from TELENOTIFY_CORPUS."""
    corpus = tmp_path / "x[/]y" / "[@click=app.bell]RING[/].jsonl"
    monkeypatch.setenv("TELENOTIFY_CORPUS", str(corpus))
    fake = tmp_path / "telenotify"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    rung: list[bool] = []
    app = TeleNotifyApp(dataclasses.replace(paths, binary=fake), refresh_seconds=0)
    monkeypatch.setattr(app, "bell", lambda: rung.append(True))
    async with app.run_test(size=(140, 40), notifications=True) as pilot:
        await pilot.press("4")
        await settle(app, pilot)
        settings = app.query_one(SettingsPane)
        settings.action_test_corpus()
        await settle(app, pilot)
        said = [n.message for n in app._notifications if n.message.startswith("No corpus at")]
        assert said and str(corpus) in said[0]
        assert app.is_running and not rung
        assert "x[/]y" in "\n".join(parse(app.export_screenshot(), 140, 40).text())


async def test_the_not_saved_problems_toast_shows_config_values_as_text(paths):
    """Run 11, the same class: "Not saved: fix these first" quotes config.json's values (a chat ID
    that isn't one) with markup on, so "[/]" there crashed the screen."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(140, 40), notifications=True) as pilot:
        await pilot.press("4")
        await settle(app, pilot)
        settings = app.query_one(SettingsPane)
        settings.edited["chats"] = ["x[/]y"]
        settings.changed()
        await pilot.press("ctrl+s")
        await settle(app, pilot)
        assert app.is_running
        said = [n.message for n in app._notifications if n.message.startswith("Not saved: fix these first")]
        assert said and "x[/]y" in said[0]
        assert "x[/]y" in "\n".join(parse(app.export_screenshot(), 140, 40).text())


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a read-only folder")
def test_a_folder_that_cant_be_written_is_the_one_named(paths):
    """Run 11, 2a: name what actually failed. A read-only folder: no file can be made in it, so the
    message names the folder, says it needs write permission, and leaves nothing behind."""
    folder = paths.config.parent
    folder.chmod(0o555)
    try:
        with pytest.raises(OSError) as caught:
            prefs.save(paths.config, prefs.Prefs(letter_keys=False))
        said = prefs.not_saved(paths.config, False, caught.value)
    finally:
        folder.chmod(0o755)
    assert said == (f"Letter keys: off for now, but couldn't save that in {prefs.shown_path(folder)} "
                    "(no permission to write there). Give yourself write permission to that folder "
                    "to keep this setting.")
    assert not [p for p in folder.iterdir() if p.name.endswith(".tmp")]


def test_screen_json_that_cant_be_replaced_is_the_one_named(paths):
    target = prefs.path_for(paths.config)
    target.mkdir()
    with pytest.raises(OSError) as caught:
        prefs.save(paths.config, prefs.Prefs(letter_keys=False))
    assert f"to {prefs.shown_path(target)} (it's a folder)" in prefs.not_saved(paths.config, False, caught.value)


@pytest.mark.parametrize("kind", ["fifo", "folder", "file"])
async def test_save_never_touches_a_tmp_it_didnt_make(paths, kind):
    """Run 11, 2f: save unlinked screen.json.tmp whatever it was. It now writes a new file of its own
    (O_EXCL, its own name), so what's there already is left as it is, and the save still works."""
    other = prefs.path_for(paths.config).with_name("screen.json.tmp")
    if kind == "fifo":
        os.mkfifo(other)
    elif kind == "folder":
        other.mkdir()
    else:
        other.write_text("someone else's", encoding="utf-8")
    try:   # bounded: writing into someone else's FIFO would wait forever
        await asyncio.wait_for(asyncio.to_thread(prefs.save, paths.config, prefs.Prefs(letter_keys=False)), 5)
    finally:   # a writer still waiting on the FIFO (the bug) gets a reader, then end of file
        if kind == "fifo":
            try:
                os.close(os.open(other, os.O_RDONLY | os.O_NONBLOCK))
            except OSError:
                pass   # nothing waiting
    assert prefs.load(paths.config).letter_keys is False
    assert other.exists()
    if kind == "fifo":
        assert stat.S_ISFIFO(other.lstat().st_mode)
    elif kind == "folder":
        assert other.is_dir()
    else:
        assert other.read_text(encoding="utf-8") == "someone else's"
