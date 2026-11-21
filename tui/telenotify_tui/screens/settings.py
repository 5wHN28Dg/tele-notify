"""Settings screen: edit config.json without hand-editing JSON.

Five sections (keyword lists, places, other filters, chats, the bot itself).
Edits stay in memory until ctrl+s; saving backs the file up, replays only
the changed keys on top of the file as it is on disk (unknown keys,
secrets and key order are kept) and writes it atomically. `t` runs the
real filter over the labelled corpus with the current file and with the
edits, and shows what changes. The data side is in settings_data.py.
"""

from __future__ import annotations

import asyncio
import copy
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Label,
    OptionList,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option

from .. import bot
from ..clean import clean
from ..rtl import visual
from ..store import BUSY, is_busy, unreadable_note
from . import settings_data as sd
from .armed import Armed

FORM_HELP = {
    "max_experience_years": "More than this many years required means \"not for me\" (default 1)",
    "publish_when_unsure": "Publish a job with your role and place that says nothing about level "
                           "(off: send it to review)",
    "dry_run": "Judge and record, but forward nothing",
    "alerts": "Tell you about problems (downtime, lost access, log-out) in Saved Messages and through your notification bot",
    "alert_after_minutes": "Downtime shorter than this is only logged (default 10)",
    "notify_published": "Each published post, through your notification bot (default on)",
    "notify_review": "Each post sent to review, through your notification bot (default on)",
    "data_dir": "TDLib login and state (default ~/.local/share/tele-notify); empty = default",
    "tdlib_path": "Path to libtdjson.so if it isn't found automatically; empty = automatic",
    "target_chat": "ID of the chat matches are forwarded to",
}


# ----------------------------------------------------------------- modals

class ConfirmModal(Armed, ModalScreen[bool]):
    """A question with a button that names its action and Cancel. Dismisses with
    True only on that button or `y`. Cancel has the focus, so enter is safe; the
    action is styled destructive when it loses something; for ARM_DELAY seconds
    after opening, presses are ignored, so a key or click carried over from the
    step before can't confirm."""


    DEFAULT_CSS = """
    ConfirmModal { align: center middle; }
    ConfirmModal > Vertical { width: 64; height: auto; border: thick $warning; background: $surface;
                              padding: 1 2; }
    ConfirmModal Horizontal { height: auto; margin-top: 1; }
    ConfirmModal Button { margin-right: 2; }
    /* the stock -error button's text is 4.47:1; these pass 4.5:1 in both themes */
    ConfirmModal Button.-style-default.-error, ConfirmModal Button.-style-default.-error:hover,
    ConfirmModal Button.-style-default.-error.-active, ConfirmModal Button.-style-default.-error:focus {
        background: $error-muted; color: $text-error; background-tint: $foreground 0%;
    }
    /* pressed: Textual tints by 30 %, which drops any variant under 4.5:1 */
    ConfirmModal Button.-style-default.-active { tint: $background 0%; }
    ConfirmModal #confirm-wait { color: $text-muted; height: 1; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("y", "answer(True)", "Confirm"),
                                             Binding("n,escape", "answer(False)", "Cancel")]

    def __init__(self, message: str, yes: str, destructive: bool = True):
        super().__init__()
        self.message, self.yes = message, yes
        self.variant = "error" if destructive else "warning"

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(visual(self.message)), id="confirm-text")
            yield Static("", id="confirm-wait")
            with Horizontal():
                yield Button(f"{self.yes} (y)", variant=self.variant, id="yes")
                yield Button("Cancel (n)", id="no")

    def on_mount(self) -> None:
        self._arm()
        self.query_one("#no", Button).focus()



    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "yes" and self._too_soon(event.button):
            return
        self._answer(event.button.id == "yes")

    def action_answer(self, yes: bool) -> None:
        self._answer(yes)                        # an early `y` was stopped in Armed.on_key


class EvalModal(ModalScreen[None]):
    """The corpus test: counts per label and verdict, now -> with the edits."""

    DEFAULT_CSS = """
    EvalModal { align: center middle; }
    EvalModal > Vertical { width: 84; height: auto; max-height: 90%; border: thick $primary;
                           background: $surface; padding: 1 2; }
    EvalModal DataTable { height: auto; margin: 1 0; }
    EvalModal #eval-summary { height: auto; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape,q,enter", "close", "Close")]

    def __init__(self, before: bot.EvalResult, after: bot.EvalResult, corpus: Path):
        super().__init__()
        self.before, self.after, self.corpus = before, after, corpus

    def compose(self) -> ComposeResult:
        with Vertical():
            head = Text("Corpus test", style="bold")
            head.append(f"  {clean(self.corpus)}", style="dim")
            head.append("\nEach cell: with the saved config.json -> with your edits")
            yield Static(head)
            yield DataTable(id="eval-table", show_cursor=False, zebra_stripes=True)
            yield Static(id="eval-summary")
            yield Button("Close (esc)", id="close")

    def on_mount(self) -> None:
        t = self.query_one(DataTable)
        t.add_columns("label", *sd.VERDICTS)
        if not self.before.error and not self.after.error:
            for row in sd.compare_eval(self.before, self.after):
                t.add_row(row[0].label, *[self._cell(c) for c in row], key=row[0].label)
        self.query_one("#eval-summary", Static).update(self.summary())

    @staticmethod
    def _cell(c: sd.EvalCell) -> Text:
        s = f"{c.before} -> {c.after}" if c.before != c.after else str(c.after)
        style = {"worse": "bold red", "better": "bold green"}.get(c.mood, "")
        return Text(s + (" !" if c.mood == "worse" else ""), style=style)

    def summary(self) -> Text:
        out = Text()
        for name, r in (("saved config", self.before), ("your edits", self.after)):
            if r.error:
                out.append(f"{name}: the test failed: {clean(r.error)}\n", style="bold red")
        if self.before.error or self.after.error:
            return out

        def line(what: str, b: int, a: int) -> None:
            style = "bold red" if a > b else ("bold green" if a < b else "")
            word = " (worse)" if a > b else (" (better)" if a < b else " (same)")
            out.append(f"{what}: {b} -> {a}{word}\n", style=style)

        line("wrong publishes (FALSE published)", self.before.wrong_published, self.after.wrong_published)
        line("true matches lost (TRUE rejected)", self.before.true_lost, self.after.true_lost)
        for s in self.after.summary:
            out.append(f"with edits: {clean(s)}\n", style="dim")
        return out

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------- keyword lists

class KeywordFilter(Input):
    """The filter box. Down or enter moves into the list below; every other
    key (delete and backspace too) edits the filter text."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("down", "into_list", "Into the list", show=False)]

    def action_into_list(self) -> None:
        if isinstance(self.parent, KeywordEditor):
            self.parent.focus_list()


class KeywordList(OptionList):
    """The items. A click only picks an item (so del / x and the buttons act
    on it); enter or a double click edits it."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("delete,x", "delete_item", "Delete item"),
                Binding("a", "add", "Add item")]

    @property
    def editor(self) -> KeywordEditor | None:
        return self.parent if isinstance(self.parent, KeywordEditor) else None

    async def _on_click(self, event: events.Click) -> None:
        event.prevent_default()   # OptionList's own click handler would edit on every click
        clicked = event.style.meta.get("option")
        if clicked is None or self.get_option_at_index(clicked).disabled:
            return
        self.highlighted = clicked
        if event.chain >= 2:
            self.action_select()

    def action_delete_item(self) -> None:
        if self.editor is not None:
            self.editor.action_delete_item()

    def action_add(self) -> None:
        if self.editor is not None:
            self.editor.action_add()


class EditorButton(Button, can_focus=False):
    """A mouse button that leaves the focus (and the picked item) alone."""


KEYS_HINT = "click picks · enter / double click: edit · del / x: delete · a: add"


class KeywordEditor(Vertical):
    """One keyword list: filter, add (enter in the bottom box, `a` or the Add
    button), edit (enter or double click on an item), delete (del / x on an
    item, or the Delete button)."""

    DEFAULT_CSS = """
    KeywordEditor { width: 1fr; height: 1fr; padding-left: 1; }
    KeywordEditor #kw-title { text-style: bold; }
    KeywordEditor #kw-items { height: 1fr; }
    KeywordEditor Input { margin: 0; }
    KeywordEditor #kw-buttons { height: 1; }
    KeywordEditor #kw-buttons Button { margin-right: 1; width: auto; min-width: 6; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel_edit", "Cancel edit", show=False)]

    def __init__(self, get: Callable[[str], list], put: Callable[[str, list], None],
                 parse: Callable[[str], Any] = str, **kw: Any):
        super().__init__(**kw)
        self.get, self.put, self.parse = get, put, parse
        self.key: str | None = None
        self.editing: int | None = None      # the index of the item open in the bottom box
        self.editing_value: Any = None       # and its value, to find it again if the list changed

    def compose(self) -> ComposeResult:
        yield Static(id="kw-title")
        yield Static(id="kw-help", classes="muted")
        yield Static(KEYS_HINT, id="kw-keys", classes="muted")
        yield KeywordFilter(placeholder="filter... (↓ or enter: into the list)", id="kw-filter", compact=True)
        yield KeywordList(id="kw-items", compact=True)
        with Horizontal(id="kw-buttons"):
            yield EditorButton("Edit (enter)", id="kw-edit", compact=True)
            yield EditorButton("Delete (del)", id="kw-delete", compact=True, variant="error")
            yield EditorButton("Add (a)", id="kw-add", compact=True, variant="success")
        yield Input(placeholder="add a keyword: type it and press enter", id="kw-entry", compact=True)

    def focus_list(self) -> None:
        ol = self.query_one("#kw-items", OptionList)
        ol.focus()
        if ol.highlighted is None and ol.option_count:
            ol.highlighted = 0

    def show(self, key: str) -> None:
        if key != self.key:
            self.key = key
            self.editing = None
            self.query_one("#kw-entry", Input).value = ""
        else:
            self.recheck_edit()   # the same list, re-read (revert, save): the edit follows its item
        self.refresh_items()

    def items(self) -> list:
        v = self.get(self.key) if self.key else []
        return list(v) if isinstance(v, list) else []

    def refresh_items(self) -> None:
        if self.key is None:
            return
        items = self.items()
        flt = sd.norm_keyword(self.query_one("#kw-filter", Input).value)
        shown = [(i, x) for i, x in enumerate(items) if not flt or flt in sd.norm_keyword(str(x))]
        self.query_one("#kw-title", Static).update(
            f"{self.key}  [dim]({len(items)} item{'s' if len(items) != 1 else ''}"
            f"{f', {len(shown)} shown' if flt else ''})[/dim]")
        self.query_one("#kw-help", Static).update(sd.LIST_KEYS.get(self.key, ""))
        ol = self.query_one("#kw-items", OptionList)
        old = ol.highlighted
        ol.clear_options()
        if shown:
            ol.add_options([Option(Text(visual(x)), id=str(i)) for i, x in shown])
            ol.highlighted = min(old or 0, len(shown) - 1)
        else:
            ol.add_option(Option(Text("(empty)" if not items else "(nothing matches)", style="dim"),
                                 disabled=True))
        entry = self.query_one("#kw-entry", Input)
        entry.placeholder = ("editing: enter saves, esc cancels" if self.editing is not None
                             else "add a keyword: type it and press enter")

    def _highlighted_index(self) -> int | None:
        ol = self.query_one("#kw-items", OptionList)
        if ol.highlighted is None:
            return None
        opt = ol.get_option_at_index(ol.highlighted)
        return int(opt.id) if opt.id is not None else None

    @on(Input.Changed, "#kw-filter")
    def _filter(self) -> None:
        self.refresh_items()

    @on(Input.Submitted, "#kw-filter")
    def _filter_done(self, event: Input.Submitted) -> None:
        event.stop()
        self.focus_list()

    @on(Button.Pressed, "#kw-buttons Button")
    def _button(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "kw-add":
            self.action_add()
            return
        # Edit / Delete act on the item picked in the list (clicked or reached with the
        # arrows), never on whatever happens to be highlighted while you type elsewhere.
        if not self.query_one("#kw-items", OptionList).has_focus:
            what = "edit" if event.button.id == "kw-edit" else "delete"
            self.notify(f"Pick an item in the list first (click it), then {what}.", severity="warning")
            return
        if event.button.id == "kw-edit":
            self.action_edit_item()
        else:
            self.action_delete_item()

    @on(Input.Submitted, "#kw-entry")
    def _submit(self, event: Input.Submitted) -> None:
        event.stop()
        text = event.value.strip()
        if not text or self.key is None:
            self.action_cancel_edit()
            return
        items = self.items()
        if self.editing is not None:
            i = self.edit_index()
            if i is None:   # the list changed under the edit and the item is gone
                self.editing = None
                self.refresh_items()
                self.notify("The item you were editing is no longer in the list, so nothing was "
                            "changed; press enter again to add your text as a new item.",
                            severity="warning", markup=False)
                return
            items[i] = self.parse(text)
        else:
            items.append(self.parse(text))
        self.editing = None
        event.input.value = ""
        self.put(self.key, items)
        self.refresh_items()

    def edit_index(self) -> int | None:
        """Where the item being edited is now: the list can change under an open
        edit (another item deleted, duplicates removed, config re-read)."""
        if self.editing is None:
            return None
        items = self.items()

        def same(x: Any) -> bool:
            return type(x) is type(self.editing_value) and x == self.editing_value

        if self.editing < len(items) and same(items[self.editing]):
            return self.editing
        return next((j for j, x in enumerate(items) if same(x)), None)

    def recheck_edit(self) -> None:
        """After the list changed: follow the edited item to its new place, or
        end the edit (with a note) if it is gone."""
        if self.editing is None:
            return
        i = self.edit_index()
        if i is None:
            self.action_cancel_edit()
            self.notify("The item you were editing was removed, so the edit was cancelled.",
                        severity="warning", markup=False)
        else:
            self.editing = i

    @on(OptionList.OptionSelected, "#kw-items")
    def _edit(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id is not None:
            self.edit(int(event.option.id))

    def edit(self, i: int) -> None:
        items = self.items()
        if not 0 <= i < len(items):
            return
        self.editing, self.editing_value = i, items[i]
        entry = self.query_one("#kw-entry", Input)
        entry.value = clean(items[i])
        self.refresh_items()
        entry.focus()

    def action_edit_item(self) -> None:
        i = self._highlighted_index()
        if i is None:
            self.notify("Pick an item in the list first (click it, or ↑↓).", severity="warning")
            return
        self.edit(i)

    def action_delete_item(self) -> None:
        i = self._highlighted_index()
        if i is None or self.key is None:
            self.notify("Pick an item in the list first (click it, or ↑↓), then delete.", severity="warning")
            return
        items = self.items()
        if not 0 <= i < len(items):
            return
        was_open = self.edit_index() == i
        gone = items.pop(i)
        self.put(self.key, items)
        if was_open:   # deleted the very item open in the bottom box: that edit ends too
            self.action_cancel_edit()
        else:          # another item: an open edit stays open, on its item
            self.recheck_edit()
        self.refresh_items()
        note = " (the edit you had open on it was cancelled)" if was_open else ""
        self.notify(f"Deleted {clean(gone)!r} from {self.key}{note} (not saved yet: ctrl+s saves).",
                    markup=False)

    def action_add(self) -> None:
        entry = self.query_one("#kw-entry", Input)
        if self.editing is not None:   # leave the edit: its text mustn't be added as a copy
            self.editing = None
            entry.value = ""
        entry.focus()
        self.refresh_items()

    def action_cancel_edit(self) -> None:
        if self.editing is not None:
            self.editing = None
            self.query_one("#kw-entry", Input).value = ""
            self.refresh_items()


class ChatTable(DataTable):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("space", "toggle_watch", "Watch on/off"),
                Binding("m", "make_target", "Send matches here")]

    def _row_id(self) -> int | None:
        if not self.row_count:
            return None
        key = self.coordinate_to_cell_key(self.cursor_coordinate).row_key
        return int(key.value) if key.value is not None else None

    def action_toggle_watch(self) -> None:
        cid = self._row_id()
        if cid is not None:
            self.post_message(SettingsPane.ChatAction(cid, "watch"))

    def action_make_target(self) -> None:
        cid = self._row_id()
        if cid is not None:
            self.post_message(SettingsPane.ChatAction(cid, "target"))


# ------------------------------------------------------------------ pane

LIST_SECTIONS = ("roles", "places", "filters")
CHATS_TIMEOUT = 0.25   # seconds the chat-list read waits for the bot's lock (on the UI thread)


class SettingsPane(Vertical):
    live = False  # reload every few seconds while visible

    DEFAULT_CSS = """
    SettingsPane { height: 1fr; }
    SettingsPane #settings-bar { height: 1; }
    SettingsPane #settings-status { width: 1fr; height: 1; }
    SettingsPane #settings-bar Button { margin-left: 1; width: auto; min-width: 8; }
    SettingsPane #sections { height: 1fr; }
    SettingsPane #sections TabPane { padding: 0; }
    SettingsPane .lists { height: 1fr; }
    SettingsPane .picker { width: 34; height: 1fr; }
    SettingsPane .problems { height: auto; max-height: 5; }
    SettingsPane .form-row { height: auto; min-height: 1; margin-top: 1; }
    SettingsPane .form-row Label { width: 22; }
    SettingsPane .form-row Input { width: 1fr; }
    SettingsPane .form-row Input.short { width: 10; }
    SettingsPane .form-row .help { width: 1fr; padding-left: 2; }
    SettingsPane Checkbox { border: none; padding: 0; height: 1; width: 10; background: transparent; }
    /* focused too: Textual's focus border would fill the one-row box and hide the switch; the
       highlighted label still shows which one has focus */
    SettingsPane Checkbox:focus { border: none; }
    /* and the focused label in the theme's own text pair, reversed: Textual's focus pair is 3.78:1
       in textual-dark (#131 run 3) */
    SettingsPane Checkbox:focus > .toggle--label { background: $foreground; color: $background; }
    SettingsPane #chat-table { height: 1fr; }
    SettingsPane #chat-ids { height: 1fr; }
    SettingsPane .hidden { display: none; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("ctrl+s", "save", "Save settings"),
                Binding("t", "test_corpus", "Test on corpus"),
                Binding("D", "remove_duplicates", "Remove duplicates")]

    class ChatAction(Message):
        def __init__(self, chat_id: int, what: str):
            super().__init__()
            self.chat_id, self.what = chat_id, what

    def __init__(self, **kw: Any):
        super().__init__(**kw)
        self.original: dict | None = None
        self.edited: dict = {}
        self.load_error = ""
        self.loaded_text: str | None = None
        self.problems: list[sd.Problem] = []
        self.has_chat_table = False
        self.store_chats: list = []
        self.chats_error = ""   # why the last chat-list read failed: "busy", "unreadable" or "" (it worked)
        self.chat_filter = ""
        self._tab_label: str | None = None
        self.has_dups = False   # some list has an entry twice: "Remove duplicates" is offered

    # -------------------------------------------------------- composing

    def compose(self) -> ComposeResult:
        with Horizontal(id="settings-bar"):
            yield Static(id="settings-status")
            yield Button("Save ^s", id="save", compact=True, variant="primary")
            yield Button("Test t", id="test", compact=True)
            yield Button("Revert", id="revert", compact=True)
            yield Button("Remove duplicates D", id="dedupe", compact=True, variant="warning",
                         classes="hidden")
        with TabbedContent(id="sections"):
            for sec, title, keys in sd.SECTIONS:
                with TabPane(title, id=f"sec-{sec}"):
                    if sec in LIST_SECTIONS:
                        lists = [k for k in keys if k in sd.LIST_KEYS]
                        with Horizontal(classes="lists"):
                            yield OptionList(*[Option(k, id=k) for k in lists], id=f"picker-{sec}",
                                             classes="picker", compact=True)
                            yield KeywordEditor(self._get, self._put, id=f"editor-{sec}")
                        if sec == "filters":
                            yield from self._form_row("max_experience_years")
                            yield from self._form_row("publish_when_unsure")
                    elif sec == "chats":
                        yield from self._compose_chats()
                    else:
                        with VerticalScroll():
                            for k in keys:
                                yield from self._form_row(k)
                            yield Static(id="secrets", classes="form-row")
                    yield Static(id=f"problems-{sec}", classes="problems")

    def _form_row(self, key: str) -> ComposeResult:
        with Horizontal(classes="form-row"):
            yield Label(key)
            if key in sd.BOOL_KEYS:
                yield Checkbox("on", id=f"f-{key}")      # "on" / "off": the state in words, not colour alone
            else:
                short = key in sd.INT_KEYS or key == "target_chat"
                yield Input(id=f"f-{key}", compact=True, classes="short" if short else "",
                            placeholder=str(sd.DEFAULTS[key]) if key in sd.INT_KEYS else "default")
            yield Static(FORM_HELP.get(key, ""), id=f"help-{key}", classes="help muted")

    def _compose_chats(self) -> ComposeResult:
        yield Static(id="chat-note", classes="muted")
        with Vertical(id="chat-named"):
            yield Input(placeholder="filter chats by name...", id="chat-filter", compact=True)
            yield ChatTable(id="chat-table", cursor_type="row", zebra_stripes=True)
            yield Input(placeholder="watch a chat by ID (for a chat the bot hasn't seen yet): enter adds",
                        id="chat-add", compact=True)
        with Vertical(id="chat-ids", classes="hidden"):
            yield KeywordEditor(self._get, self._put, parse=sd.parse_int, id="editor-chats")
            yield from self._form_row("target_chat")

    def on_mount(self) -> None:
        t = self.query_one("#chat-table", DataTable)
        t.add_column("watch", key="watch")
        t.add_column("target", key="target")
        t.add_column("chat", key="chat")
        t.add_column("kind", key="kind")
        t.add_column("status", key="status")
        t.add_column("id", key="id")
        # config.json only: the chat list is read when the tab is shown (it may be locked)
        self.call_after_refresh(self.load, chats=False)

    # ------------------------------------------------------- data in/out

    @property
    def paths(self):
        return self.app.paths  # type: ignore[attr-defined]

    def _get(self, key: str) -> Any:
        return self.edited.get(key, copy.deepcopy(sd.DEFAULTS[key]))

    def _put(self, key: str, v: Any) -> None:
        if v is sd.MISSING:
            self.edited.pop(key, None)
        else:
            self.edited[key] = v
        self.show_switches()
        self.changed()

    def show_switches(self) -> None:
        """Each switch says "on" or "off" (not by colour alone, and still while focused), and the
        notification switches say when they can't do anything (no bot token, a dry run)."""
        for key in sd.BOOL_KEYS:
            for box in self.query(f"#f-{key}").results(Checkbox):
                box.label = "on" if box.value else "off"
        # the --dry-run flag alone; the config's dry_run comes from the edits
        note = sd.notify_note(self.edited, dry_run_flag=self.paths.dry_run_flag)
        for key in sd.NOTIFY_KEYS:
            for help_ in self.query(f"#help-{key}").results(Static):
                help_.update(FORM_HELP[key] + (f". {note}" if note else ""))

    @property
    def dirty(self) -> bool:
        return self.original is not None and sd.is_dirty(self.original, self.edited)

    def load(self, chats: bool = True) -> None:
        """(Re-)read config.json and (with `chats`) the chat list; replaces any edits."""
        path = self.paths.config
        try:
            text = path.read_text(encoding="utf-8")
            self.original = sd.load(path)
            self.load_error = ""
        except (OSError, sd.ConfigError) as e:
            text = None
            self.original = None
            self.load_error = str(e) if isinstance(e, sd.ConfigError) else f"cannot read {path}: {e}"
        self.loaded_text = text
        self.edited = copy.deepcopy(self.original) if self.original is not None else {}
        if chats:
            self.load_chats()
        self.fill_widgets()
        self.changed()

    def load_chats(self, say: bool = True) -> str:
        """Read the bot's chat list. On the UI thread, so it waits for a lock only briefly:
        the screen must keep answering. A failure is said in #chat-note while it lasts
        and, with `say`, in a toast; it is returned ("" when the read worked)."""
        try:
            with self.app.store(CHATS_TIMEOUT) as s:  # type: ignore[attr-defined]
                self.has_chat_table = s.has_table("chats")
                self.store_chats = s.chats()
            self.chats_error = ""
        except (sqlite3.Error, OSError, ValueError) as e:  # a locked or broken state file mustn't break settings
            self.has_chat_table, self.store_chats = False, []
            self.chats_error = "busy" if is_busy(e) else "unreadable"
            if say:
                self.notify(self.chats_problem, severity="warning", markup=False)
        return self.chats_problem

    @property
    def chats_problem(self) -> str:
        """The failed chat-list read in the Dashboard's words (#105), "" when it worked. Built
        when said: with unsaved edits, ctrl+r reverts instead of retrying."""
        if self.chats_error == "busy":
            retry = "opening this tab again retries" if self.dirty else "ctrl+r retries"
            return f"{BUSY}, so the chat list wasn't read; {retry}."
        if self.chats_error:
            return f"The chat list wasn't read. {unreadable_note(bot.service(self.paths.dry_run))}"
        return ""

    def reload(self) -> None:
        """Called by the app when the tab is shown and on ctrl+r."""
        if self.original is None and not self.load_error:
            self.load()
            return
        if not self.dirty:
            try:
                same = self.paths.config.read_text(encoding="utf-8") == self.loaded_text
            except OSError:
                same = False
            if same:
                self.load_chats()
                self.refresh_chats()
            else:
                self.load()
            return
        unread = self.load_chats(say=False)   # one toast for this reload
        self.refresh_chats()
        self.notify("Settings has unsaved changes, so config.json wasn't re-read. "
                    "ctrl+s saves them; Revert discards them."
                    + (" The chat list wasn't read either (see Chats)." if unread else ""), severity="warning")

    def fill_widgets(self) -> None:
        with self.prevent(Input.Changed, Checkbox.Changed):
            for key in [*sd.INT_KEYS, *sd.STR_KEYS, "target_chat"]:
                v = self.edited.get(key, sd.MISSING)
                self.query_one(f"#f-{key}", Input).value = "" if v is sd.MISSING else clean(v)
            for key in sd.BOOL_KEYS:
                v = self.edited.get(key, sd.BOOL_KEYS[key])
                self.query_one(f"#f-{key}", Checkbox).value = bool(v)
        self.show_switches()
        for sec in LIST_SECTIONS:
            picker = self.query_one(f"#picker-{sec}", OptionList)
            editor = self.query_one(f"#editor-{sec}", KeywordEditor)
            if picker.highlighted is None:
                picker.highlighted = 0
            opt = picker.get_option_at_index(picker.highlighted)
            editor.show(opt.id)
        self.query_one("#editor-chats", KeywordEditor).show("chats")
        status = sd.secret_status(self.original or {})
        self.query_one("#secrets", Static).update(
            "api_id: " + status["api_id"] + "   api_hash: " + status["api_hash"]
            + "   [dim](never shown or changed here; always kept as they are)[/dim]")
        self.refresh_chats()

    # ----------------------------------------------------- after a change

    def changed(self) -> None:
        self.problems = sd.validate(self.edited) if self.original is not None else []
        self.has_dups = self.original is not None and bool(sd.dedupe_all(copy.deepcopy(self.edited)))
        self.query_one("#dedupe", Button).set_class(not self.has_dups, "hidden")
        self.refresh_bindings()
        self.update_status()
        self.update_problems()
        self.update_pickers()
        self.update_tab_label()
        self.refresh_chats()

    def update_status(self) -> None:
        if self.original is None:
            self.query_one("#settings-status", Static).update(
                Text(f"config.json can't be used: {clean(self.load_error)}", style="bold red"))
            return
        n = len(sd.changed_keys(self.original, self.edited))
        errs = len(sd.errors(self.problems))
        warns = len(self.problems) - errs
        sec = sd.secret_status(self.original)
        t = Text()
        t.append(f"{n} unsaved change{'s' if n != 1 else ''}" if n else "saved",
                 style="bold yellow" if n else "green")
        if errs:
            t.append(f" · {errs} problem{'s' if errs != 1 else ''} to fix", style="bold red")
        if warns:
            t.append(f" · {warns} warning{'s' if warns != 1 else ''}", style="yellow")
        t.append(f" · api_id {sec['api_id']} · api_hash {sec['api_hash']}", style="dim")
        self.query_one("#settings-status", Static).update(t)

    def update_problems(self) -> None:
        for sec, _title, keys in sd.SECTIONS:
            grouped: dict[tuple[bool, str], list[str]] = {}   # the same message for several lists: one line
            for p in self.problems:
                if p.key in keys:
                    grouped.setdefault((p.error, p.message), []).append(p.key)
            t = Text()
            for (error, message), ks in sorted(grouped.items(), key=lambda x: not x[0][0]):
                t.append(visual(("error: " if error else "warning: ") + f"{', '.join(ks)}: {message}") + "\n",
                         style="red" if error else "yellow")
            t.rstrip()
            self.query_one(f"#problems-{sec}", Static).update(t)

    def update_pickers(self) -> None:
        bad: dict[str, bool] = {}
        for p in self.problems:
            bad[p.key] = bad.get(p.key, False) or p.error
        changed = set(sd.changed_keys(self.original, self.edited)) if self.original is not None else set()
        for sec in LIST_SECTIONS:
            picker = self.query_one(f"#picker-{sec}", OptionList)
            for i in range(picker.option_count):
                key = picker.get_option_at_index(i).id
                v = self._get(key)
                n = len(v) if isinstance(v, list) else "?"
                label = Text(f"{'*' if key in changed else ' '}{key} ")
                label.append(f"({n})", style="dim")
                if key in bad:
                    label.append(" !", style="bold red" if bad[key] else "yellow")
                picker.replace_option_prompt_at_index(i, label)

    def update_tab_label(self) -> None:
        mine = next((a for a in self.ancestors if isinstance(a, TabPane)), None)
        outer = next((a for a in self.ancestors if isinstance(a, TabbedContent)), None)
        if mine is None or outer is None or mine.id is None:
            return
        tab = outer.get_tab(mine.id)
        if self._tab_label is None:
            self._tab_label = str(tab.label)
        tab.label = self._tab_label + (" *" if self.dirty else "")

    # -------------------------------------------------------------- chats

    def refresh_chats(self) -> None:
        named = self.has_chat_table and bool(self.store_chats)
        self.query_one("#chat-named").set_class(not named, "hidden")
        self.query_one("#chat-ids").set_class(named, "hidden")
        note = self.query_one("#chat-note", Static)
        if not named and self.chats_problem:   # not "not yet": the list couldn't be read
            # the advice first: the note ends with the command, so it copies cleanly
            note.update(Text(f"Edit chat IDs directly for now. {self.chats_problem}", style="bold"))
            self.query_one("#editor-chats", KeywordEditor).refresh_items()
            return
        if not named:
            note.update("No chat list from the bot yet: edit chat IDs directly. Names appear here "
                        "once the bot has run (it saves your chat list when it starts).")
            self.query_one("#editor-chats", KeywordEditor).refresh_items()
            return
        note.update("space: watch on/off · m: send matches to this chat · the bot reads the new "
                    "list after a restart")
        table = self.query_one("#chat-table", DataTable)
        rows = sd.chat_rows(self.store_chats, self.edited)
        flt = sd.norm_keyword(self.chat_filter)
        rows = [r for r in rows if not flt or flt in sd.norm_keyword(r.title) or flt in str(r.id)]
        cursor = table.cursor_row
        keep = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value if table.row_count else None
        table.clear()
        for r in rows:
            status = Text(visual(r.status), style="red" if r.status not in ("ok", "") else "")
            title = Text(visual(r.title), style="italic dim" if not r.known else "")
            table.add_row(Text("[x]" if r.watched else "[ ]", style="bold green" if r.watched else "dim"), Text("->", style="bold green") if r.target else "",
                          title, Text(visual(r.kind)), status, Text(str(r.id), style="dim"), key=str(r.id))
        if table.row_count:
            ids = [str(r.id) for r in rows]
            table.move_cursor(row=ids.index(keep) if keep in ids else min(cursor, table.row_count - 1))

    @on(Input.Changed, "#chat-filter")
    def _chat_filter(self, event: Input.Changed) -> None:
        event.stop()
        self.chat_filter = event.value
        self.refresh_chats()

    @on(Input.Submitted, "#chat-add")
    def _chat_add(self, event: Input.Submitted) -> None:
        event.stop()
        v = sd.parse_int(event.value)
        if not isinstance(v, int):
            self.notify(f"Not a chat ID: {event.value!r}", severity="error", markup=False)
            return
        if v not in (self._get("chats") or []):
            chats = list(self._get("chats")) + [v]
            self._put("chats", chats)
        event.input.value = ""

    def on_settings_pane_chat_action(self, event: SettingsPane.ChatAction) -> None:
        event.stop()
        if self.original is None:
            return
        if event.what == "watch":
            sd.toggle_watched(self.edited, event.chat_id)
        else:
            sd.set_target(self.edited, event.chat_id)
            with self.prevent(Input.Changed):
                self.query_one("#f-target_chat", Input).value = str(event.chat_id)
        self.changed()

    # -------------------------------------------------------------- forms

    @on(OptionList.OptionHighlighted, ".picker")
    def _pick(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        sec = event.option_list.id.removeprefix("picker-")
        self.query_one(f"#editor-{sec}", KeywordEditor).show(event.option.id)

    @on(Input.Changed)
    def _field(self, event: Input.Changed) -> None:
        iid = event.input.id or ""
        if not iid.startswith("f-") or self.original is None:
            return
        event.stop()
        key = iid[2:]
        text = event.value
        if not text.strip():
            v = sd.MISSING
        elif key in sd.INT_KEYS or key == "target_chat":
            v = sd.parse_int(text)
        else:
            v = text
        self._put(key, v)

    @on(Checkbox.Changed)
    def _switch(self, event: Checkbox.Changed) -> None:
        iid = event.checkbox.id or ""
        if not iid.startswith("f-") or self.original is None:
            return
        event.stop()
        self._put(iid[2:], event.value)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        # the inner section tabs: don't let the app take this for "Settings was shown"
        if event.tabbed_content.id == "sections":
            event.stop()

    @on(Button.Pressed, "#settings-bar Button")
    def _bar(self, event: Button.Pressed) -> None:
        event.stop()
        {"save": self.action_save, "test": self.action_test_corpus, "revert": self.action_revert,
         "dedupe": self.action_remove_duplicates}[event.button.id]()

    # ------------------------------------------------------------ actions

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "remove_duplicates":
            return self.has_dups
        return True

    def action_remove_duplicates(self) -> None:
        """Every list: keep the first spelling of each entry, drop the repeats."""
        if self.original is None:
            return
        removed = sd.dedupe_all(self.edited)
        if not removed:
            self.notify("No list has an entry twice.")
            return
        self.changed()
        for editor in self.query(KeywordEditor):
            editor.recheck_edit()   # an open edit follows its item, or ends if it was a repeat
            editor.refresh_items()
        lines = [f"{k}: {', '.join(repr(clean(x)) for x in xs)}" for k, xs in removed.items()]
        self.notify("Removed the repeats (the first spelling stays; not saved yet: ctrl+s saves):\n"
                    + "\n".join(lines), timeout=10, markup=False)

    def action_revert(self) -> None:
        if not self.dirty:
            self.load()
            return

        def done(yes: bool | None) -> None:
            if yes:
                self.load()
                self.notify("Edits discarded; config.json re-read.")

        self.app.push_screen(ConfirmModal("Discard your unsaved settings changes and re-read config.json?",
                                          "Discard"), done)

    def _merged(self) -> dict:
        """The edits on top of the file as it is now (falls back to the loaded copy)."""
        try:
            disk = sd.load(self.paths.config)
        except sd.ConfigError:
            disk = self.original or {}
        return sd.apply_edits(disk, self.original or {}, self.edited)[0]

    def action_save(self) -> None:
        if self.original is None:
            self.notify(f"Nothing to save: {self.load_error}", severity="error", markup=False)
            return
        errs = sd.errors(sd.validate(self.edited))
        if errs:
            self.changed()
            self.notify("Not saved: fix these first:\n" + "\n".join(f"{p.key}: {p.message}" for p in errs[:5]),
                        severity="error", timeout=8)
            return
        if not self.dirty:
            self.notify("Nothing to save.")
            return
        path = self.paths.config
        try:
            disk = sd.load(path)
        except sd.ConfigError as e:
            self.notify(f"Not saved: {e}", severity="error", markup=False)
            return
        new, conflicts = sd.apply_edits(disk, self.original, self.edited)
        if conflicts:
            self._offer_take_disk(disk, conflicts)
            return
        bad = sd.non_finite_keys(new)
        if bad:
            self.notify(f"Not saved: config.json holds NaN or Infinity (a number too big, like 1e999?) in "
                        f"{', '.join(bad)}, which can't be written as JSON. Fix it in the file by hand.",
                        severity="error", timeout=10, markup=False)
            return
        moved = [k for k in sd.changed_keys(self.original, self.edited) if k in ("data_dir", "dry_run")]
        try:
            bak = sd.save(path, new)
        except (OSError, ValueError) as e:
            self.notify(f"Not saved: {e}", severity="error", markup=False)
            return
        self.load()
        self.notify(f"Saved. Backup: {bak.name if bak else '(no old file)'}", markup=False)
        if moved:
            # Paths are worked out once at start (paths.resolve): the screen keeps showing the old state file.
            self.notify(f"You changed {' and '.join(moved)}: close and reopen this screen so it shows "
                        "the right state file.", severity="warning", timeout=12, markup=False)
        self.run_worker(self._offer_restart("dry-run" if self.paths.dry_run else "real"), group="restart")

    def _offer_take_disk(self, disk: dict, conflicts: list[str]) -> None:
        """config.json changed elsewhere for keys the screen changed too: offer
        to take the file's values for just those keys and keep the other edits."""
        keys = ", ".join(conflicts)
        original, edited = self.original or {}, self.edited

        def done(yes: bool | None) -> None:
            if not yes:
                self.notify(f"Not saved: config.json was changed elsewhere for {keys}.",
                            severity="error", timeout=10, markup=False)
                return
            try:
                text = self.paths.config.read_text(encoding="utf-8")
                now = sd.load(self.paths.config)
            except (OSError, sd.ConfigError) as e:
                self.notify(f"Couldn't re-read config.json: {e}", severity="error", markup=False)
                return
            self.edited = sd.take_disk(now, original, edited, conflicts)
            self.original, self.loaded_text = now, text
            self.fill_widgets()
            self.changed()
            self.notify(f"Took the file's value for {keys}; your other edits are kept, not saved yet. "
                        "Check them and save again.", timeout=10, markup=False)

        self.app.push_screen(ConfirmModal(
            f"Not saved: config.json was changed elsewhere for {keys}, which you changed too.\n"
            "Drop your edits to these and use the file's values? Your other edits are kept.",
            "Use the file's values"), done)

    async def _offer_restart(self, mode: str) -> None:
        """After a save: offer to restart the bot, but only when it is running
        (a restart would start a stopped service)."""
        name = bot.SERVICES[mode]
        try:
            state = await bot.service_state(mode)
        except OSError:
            state = "unknown"
        if state != "active":
            self.notify(f"The {name} service isn't running ({state}), so it wasn't restarted; "
                        "it reads the new settings when it starts.", timeout=8, markup=False)
            return

        def done(yes: bool | None) -> None:
            if yes:
                self.run_worker(self._restart(mode), group="restart")

        self.app.push_screen(ConfirmModal(
            f"Saved. Restart the bot ({name} service) so it uses the new settings?", "Restart", destructive=False),
            done)

    async def _restart(self, mode: str) -> None:
        # try-restart: if the service stopped meanwhile, it stays stopped
        try:
            ok, msg = await bot.service_action(mode, "try-restart")
        except (OSError, ValueError) as e:
            ok, msg = False, str(e)
        self.notify(f"{bot.SERVICES[mode]} restarted." if ok else f"Restart failed: {msg}",
                    severity="information" if ok else "error", markup=False)

    def action_test_corpus(self) -> None:
        if self.original is None:
            self.notify(f"Can't test: {self.load_error}", severity="error", markup=False)
            return
        errs = sd.errors(sd.validate(self.edited))
        if errs:
            self.notify(f"Fix {len(errs)} problem(s) before testing.", severity="error")
            return
        binary = self.paths.binary
        if binary is None:
            self.notify("The telenotify program wasn't found, so the corpus can't be tested.", severity="error")
            return
        corpus = sd.corpus_path()
        if not corpus.is_file():
            self.notify(f"No corpus at {corpus} (set TELENOTIFY_CORPUS to use another file).",
                        severity="warning", timeout=8, markup=False)
            return
        self.notify("Testing on the corpus...")
        self.run_worker(self._run_eval(binary, corpus, self._merged()), exclusive=True, group="eval")

    async def _run_eval(self, binary: Path, corpus: Path, cfg: dict) -> None:
        try:
            tmp = sd.write_temp(cfg)
        except ValueError as e:   # NaN / Infinity in the config
            self.notify(f"Can't test: {e}", severity="error", markup=False)
            return
        try:
            before, after = await asyncio.gather(bot.evaluate(binary, self.paths.config, corpus),
                                                 bot.evaluate(binary, tmp, corpus))
        finally:
            tmp.unlink(missing_ok=True)
        self.app.push_screen(EvalModal(before, after, corpus))
