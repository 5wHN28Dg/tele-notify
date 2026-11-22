"""The keys around PR #137's review run 7 and #140: the shown tab's keys work from the tab bar; a
double or triple click on the service menu can't fall through onto the confirm's action button;
and the single-letter shortcuts can be turned off (Settings, Screen), with the ctrl keys, the number
keys, esc, enter, the arrows and the dialogs' y / n still working. systemctl is always mocked."""

from __future__ import annotations

import asyncio
import json

import pytest
from textual.widgets import (
    Checkbox,
    Input,
    OptionList,
    Select,
    TabbedContent,
    Tabs,
    TextArea,
)

from audit.svg import parse
from telenotify_tui import bot, prefs
from telenotify_tui.app import TeleNotifyApp, is_letter_key
from telenotify_tui.screens.dashboard import ConfirmScreen, DashboardPane, ServiceMenu
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
        assert "not saved for next time" in str(app.query_one("#screen-note").render())


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
