"""Settings tab tasks: add, edit, delete a keyword (each saved); watch or unwatch
a chat (saved); revert; test on the corpus. The scripts start in the first
list section (Roles & levels), whose first list is level_keywords_en."""

from __future__ import annotations

import json

from textual.widgets import OptionList, TabbedContent
from textual.widgets._footer import FooterKey

from .. import data
from . import Counter, Task, TaskFailed

WORD = "audit word"
FIRST = data.KEYWORDS["level_keywords_en"][0]          # "intern"


def _config(c: Counter) -> dict:
    return json.loads(c.sb.config.read_text(encoding="utf-8"))


def _modal(c: Counter) -> bool:
    from telenotify_tui.screens.settings import ConfirmModal
    return isinstance(c.app.screen, ConfirmModal)


def _items(c: Counter) -> OptionList:
    return c.app.query_one("#editor-roles #kw-items", OptionList)


async def _settings_kb(c: Counter) -> None:
    await c.key("4")


async def _settings_mouse(c: Counter) -> None:
    await c.click(c.app.query_one(TabbedContent).get_tab("settings"), what="the Settings tab")


async def _into_list_kb(c: Counter) -> None:
    await _settings_kb(c)
    await c.tab_to(lambda: c.app.focused is _items(c))
    if _items(c).highlighted is None:
        await c.key("down")


async def _save_kb(c: Counter) -> None:
    await c.key("ctrl+s")
    if _modal(c):                              # the offer to restart the running bot
        await c.key("n")


async def _save_mouse(c: Counter) -> None:
    await c.click("#save", what="Save")
    if _modal(c):
        await c.click("#no", what="Cancel (no restart)")


def _lists(c: Counter) -> list[str]:
    return _config(c).get("level_keywords_en", [])


# ---------------------------------------------------------------- add

async def add_kb(c: Counter) -> None:
    await _into_list_kb(c)
    await c.key("a")
    await c.type(WORD)
    await c.key("enter")
    await _save_kb(c)


async def add_mouse(c: Counter) -> None:
    await _settings_mouse(c)
    await c.click("#editor-roles #kw-add", what="Add")
    await c.type(WORD)
    await c.key("enter")                       # typing is excepted for the mouse; its enter is counted
    await _save_mouse(c)


def added(c: Counter) -> str:
    if WORD not in _lists(c):
        raise TaskFailed(f"config.json's list hasn't got {WORD!r}: {_lists(c)}")
    return f"saved with {WORD!r}"


# --------------------------------------------------------------- edit

async def edit_kb(c: Counter) -> None:
    await _into_list_kb(c)
    await c.key("enter")
    await c.key("end")                         # the box selects its text on focus: typing would replace it
    await c.type(" edited")
    await c.key("enter")
    await _save_kb(c)


async def edit_mouse(c: Counter) -> None:
    await _settings_mouse(c)
    await c.click("#editor-roles #kw-items", offset=(2, 0), what="the first keyword")
    await c.click("#editor-roles #kw-edit", what="Edit")
    await c.key("end")                         # the box selects its text on focus: typing would replace it
    await c.type(" edited")
    await c.key("enter")
    await _save_mouse(c)


def edited(c: Counter) -> str:
    words = _lists(c)
    if f"{FIRST} edited" not in words or FIRST in words:
        raise TaskFailed(f"config.json's list wasn't edited: {words}")
    return f"saved with {FIRST + ' edited'!r}"


# ------------------------------------------------------------- delete

async def delete_kb(c: Counter) -> None:
    await _into_list_kb(c)
    await c.key("x")
    await _save_kb(c)


async def delete_mouse(c: Counter) -> None:
    await _settings_mouse(c)
    await c.click("#editor-roles #kw-items", offset=(2, 0), what="the first keyword")
    await c.click("#editor-roles #kw-delete", what="Delete")
    await _save_mouse(c)


def deleted(c: Counter) -> str:
    if FIRST in _lists(c):
        raise TaskFailed(f"{FIRST!r} is still in config.json: {_lists(c)}")
    return f"saved without {FIRST!r}"


# -------------------------------------------------------------- watch

async def watch_kb(c: Counter) -> None:
    await _settings_kb(c)
    sections = c.app.query_one("#sections", TabbedContent)
    bar = sections.query_one("ContentTabs")
    await c.tab_to(lambda: c.app.focused is bar)
    while sections.active != "sec-chats":
        await c.key("right")
        if c.keys > 40:
            raise TaskFailed("never reached the Chats section")
    await c.tab_to(lambda: c.focused_id() == "chat-table")
    await c.key("space")
    await _save_kb(c)


async def watch_mouse(c: Counter) -> None:
    await _settings_mouse(c)
    await c.click(c.app.query_one("#sections", TabbedContent).get_tab("sec-chats"), what="the Chats section")
    await c.click("#chat-table", offset=(2, 1), what="the first chat row")
    keys = [k for k in c.app.screen.query(FooterKey) if k.key == "space"]
    if not keys:
        raise TaskFailed("no clickable footer key for watch on/off")
    await c.click(keys[0], what="the footer's Watch on/off")
    await _save_mouse(c)


def watched(c: Counter) -> str:
    before = set(data.config(c.sb.data_dir)["chats"])
    now = set(_config(c).get("chats", []))
    if now == before:
        raise TaskFailed("the watched chats in config.json didn't change")
    return f"watched chats changed by {sorted(now ^ before)}"


# ------------------------------------------------------------- revert

async def revert_kb(c: Counter) -> None:
    await _into_list_kb(c)
    await c.key("x")
    await c.key("ctrl+r")
    await c.key("y")


async def revert_mouse(c: Counter) -> None:
    await _settings_mouse(c)
    await c.click("#editor-roles #kw-items", offset=(2, 0), what="the first keyword")
    await c.click("#editor-roles #kw-delete", what="Delete")
    await c.click("#revert", what="Revert")
    await c.click("#yes", what="the confirm button")


def reverted(c: Counter) -> str:
    pane = c.app.query_one("SettingsPane")
    if pane.dirty or FIRST not in _lists(c):
        raise TaskFailed(f"not reverted (unsaved: {pane.dirty})")
    if FIRST not in [str(_items(c).get_option_at_index(i).prompt) for i in range(_items(c).option_count)]:
        raise TaskFailed(f"{FIRST!r} isn't back in the list on screen")
    return "the unsaved delete was undone; config.json untouched"


# --------------------------------------------------------------- test

async def test_kb(c: Counter) -> None:
    await _settings_kb(c)
    pane = c.app.query_one("SettingsPane")
    await c.tab_to(lambda: c.app.focused is not None and pane in c.app.focused.ancestors)
    await c.key("t")


async def test_mouse(c: Counter) -> None:
    await _settings_mouse(c)
    await c.click("#test", what="Test")


def tested(c: Counter) -> str:
    from telenotify_tui.screens.settings import EvalModal
    if not isinstance(c.app.screen, EvalModal):
        raise TaskFailed(f"no corpus test result shown (screen {type(c.app.screen).__name__})")
    return "the corpus test result is shown"


TASKS = [
    Task("Settings", "add a keyword and save", add_kb, add_mouse, added),
    Task("Settings", "edit a keyword and save", edit_kb, edit_mouse, edited),
    Task("Settings", "delete a keyword and save", delete_kb, delete_mouse, deleted),
    Task("Settings", "watch or unwatch a chat and save", watch_kb, watch_mouse, watched),
    Task("Settings", "revert unsaved changes", revert_kb, revert_mouse, reverted),
    Task("Settings", "test on the corpus", test_kb, test_mouse, tested),
]
