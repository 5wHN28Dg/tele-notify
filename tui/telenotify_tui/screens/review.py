"""Review screen: go through the review queue quickly, by keyboard or mouse.

Left, the posts waiting for a decision; right, the one under the cursor.
`s` approves it (the running bot sends it within seconds), `d` discards it,
`n` / down moves on, `o` opens the post in the browser, `r` shows what the
filter read (it runs `telenotify check` with link scraping), `h` shows what
was decided recently.

Decisions are written exactly as `telenotify review` writes them
(Store.decide), and each one is also a label for the test corpus
(review_data.write_label).
"""

from __future__ import annotations

import subprocess
import time
import webbrowser
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, OptionList, Static
from textual.widgets.option_list import Option

from .. import bot
from ..store import Review
from .review_data import history, write_label

DECISION_KEYS = ("send", "discard", "next", "open_link", "check")


def when(at: int | None) -> str:
    """'14:05' for today, '11-16 14:05' for earlier days."""
    if not at:
        return "-"
    t = time.localtime(at)
    if time.strftime("%Y-%m-%d", t) == time.strftime("%Y-%m-%d"):
        return time.strftime("%H:%M", t)
    return time.strftime("%m-%d %H:%M", t)


def cut(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def entry(r: Review) -> Content:
    """A queue entry: time and chat, with the role below it."""
    first = f"{when(r.at):>11}  {cut(r.chat_title, 21)}\n"
    second = f"{'':>11}  {cut(r.role or '(no role)', 21)}"
    return Content(first) + Content.styled(second, "$text-muted")


class KeyButton(Button, can_focus=False):
    """A button for the mouse; the keyboard uses the keys shown on it.

    Not focusable, and neither is the post view: when a tab is hidden,
    Textual moves focus from the hidden widget to a focusable sibling, which
    would switch the tabs straight back to this one.
    """


class PostScroll(VerticalScroll, can_focus=False):
    """The open post; scrolled with the mouse or space / b."""


class CheckModal(ModalScreen[None]):
    """What the filter made of a post, including the text it read from links."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "dismiss", "Close")]
    DEFAULT_CSS = """
    CheckModal { align: center middle; }
    CheckModal > Vertical { width: 90%; height: 85%; border: thick $accent; background: $surface;
                            padding: 0 1; }
    CheckModal #check-body { height: 1fr; }
    CheckModal #check-close { margin-top: 1; }
    """

    def __init__(self, review: Review, verdict: bot.Verdict):
        super().__init__()
        self.review = review
        self.verdict = verdict

    def compose(self) -> ComposeResult:
        v = self.verdict
        with Vertical():
            yield Static(Content(f"What the filter read: {self.review.chat_title} #{self.review.msg_id}"),
                         classes="title")
            with VerticalScroll(id="check-body"):
                if v.error and not v.verdict:
                    yield Static(Content(f"The filter couldn't judge it: {v.error}"), id="check-verdict",
                                 classes="bad")
                else:
                    yield Static(Content(f"verdict: {v.verdict}"), id="check-verdict")
                    reasons = "\n".join(f"  - {r}" for r in v.reasons) or "  (no reasons given)"
                    yield Static(Content(reasons), id="check-reasons")
                    yield Static("─ text the filter read (post + links) ─", classes="muted")
                    yield Static(Content(v.text or "(no text shown)"), id="check-text")
            yield Button("Close (Esc)", id="check-close", compact=True)

    @on(Button.Pressed, "#check-close")
    def _close(self) -> None:
        self.dismiss()


class ReviewPane(Vertical, can_focus=True):  # focusable so the keys work even with nothing listed
    live = True  # the list refreshes every few seconds; the open post is left alone

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("s", "send", "Send"),
        Binding("d", "discard", "Discard"),
        Binding("n", "next", "Next"),
        Binding("o", "open_link", "Open link"),
        Binding("r", "check", "Filter read"),
        Binding("h", "toggle_history", "History"),
        Binding("space", "scroll_post(1)", "Scroll post"),
        Binding("b", "scroll_post(-1)", "Scroll back", show=False),
    ]

    DEFAULT_CSS = """
    ReviewPane { height: 1fr; }
    ReviewPane #review-top { height: 1; }
    ReviewPane #review-summary { width: auto; text-style: bold; }
    ReviewPane #review-note { width: 1fr; margin-left: 2; }
    ReviewPane #review-empty { height: 1fr; content-align: center middle; text-align: center; }
    ReviewPane #review-split { height: 1fr; }
    ReviewPane #review-list { width: 40%; max-width: 56; min-width: 28; height: 1fr; }
    ReviewPane #review-detail { width: 1fr; height: 1fr; padding: 0 1; border-left: tall $panel; }
    ReviewPane #detail-head { margin-bottom: 1; }
    ReviewPane #review-history { height: 1fr; }
    ReviewPane #review-buttons { height: 1; margin-top: 1; }
    ReviewPane #review-buttons Button { margin-right: 1; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._items: list[Review] = []
        self._shown_id: int | None = None
        self._history_mode = False

    # ------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        with Horizontal(id="review-top"):
            yield Static("", id="review-summary")
            yield Static("", id="review-note", classes="muted")
        yield Static("", id="review-empty")
        with Horizontal(id="review-split"):
            yield OptionList(id="review-list")
            with PostScroll(id="review-detail"):
                yield Static("", id="detail-head")
                yield Static("", id="detail-text")
        table = DataTable(id="review-history", cursor_type="row", zebra_stripes=True)
        yield table
        with Horizontal(id="review-buttons"):
            yield KeyButton("Send", id="btn-send", variant="success", compact=True)
            yield KeyButton("Discard", id="btn-discard", variant="error", compact=True)
            yield KeyButton("Next", id="btn-next", compact=True)
            yield KeyButton("Open link", id="btn-open", compact=True)
            yield KeyButton("Filter read", id="btn-check", compact=True)
            yield KeyButton("History", id="btn-history", compact=True)

    def on_mount(self) -> None:
        table = self.query_one("#review-history", DataTable)
        table.add_columns("decided", "status", "chat", "role", "post")
        table.display = False
        self.query_one("#review-split").display = False
        if self.app.paths.binary is None:
            self.query_one("#btn-check", Button).tooltip = "the telenotify program wasn't found"
            self.note("no telenotify program: Filter read is off", "warn")

    def on_show(self) -> None:
        self.call_after_refresh(self._focus_main)

    def _focus_main(self) -> None:
        # Only while this tab is still the one shown: focusing a widget in a
        # hidden tab would make the tabs jump back here.
        if not self._shown(self):
            return
        target = self.query_one("#review-history" if self._history_mode else "#review-list")
        (target if self._shown(target) else self).focus()

    @staticmethod
    def _shown(widget) -> bool:
        return all(node.display for node in widget.ancestors_with_self if hasattr(node, "display"))

    # ------------------------------------------------------------- state

    @property
    def current(self) -> Review | None:
        if self._history_mode:
            return None
        idx = self.query_one("#review-list", OptionList).highlighted
        if idx is None or not 0 <= idx < len(self._items):
            return None
        return self._items[idx]

    def note(self, text: str, style: str = "muted") -> None:
        n = self.query_one("#review-note", Static)
        n.set_classes(style)
        n.update(Content(text))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in DECISION_KEYS and self.is_mounted:
            if self.current is None:
                return None  # shown, but disabled
            if action == "check" and self.app.paths.binary is None:
                return None
        return True

    def _sync_controls(self) -> None:
        none = self.current is None
        for bid in ("#btn-send", "#btn-discard", "#btn-next", "#btn-open", "#btn-check"):
            button = self.query_one(bid, Button)
            button.display = not none  # with nothing open, only History is offered
            button.disabled = none or (bid == "#btn-check" and self.app.paths.binary is None)
        self.query_one("#btn-history", Button).label = "Queue" if self._history_mode else "History"
        self.refresh_bindings()

    # ------------------------------------------------------------ reload

    def reload(self) -> None:
        paths = self.app.paths
        with self.app.store() as st:
            exists, has_table = st.exists, st.has_table("reviews")
            items = st.reviews("pending")
            past = history(st) if self._history_mode else []
        mode = " · dry run" if paths.dry_run else ""
        self.query_one("#review-summary", Static).update(
            Content(f"Recently decided{mode}" if self._history_mode
                    else f"{len(items)} waiting for review{mode}"))
        if self._history_mode:
            self._fill_history(past, exists, has_table)
        else:
            self._fill_queue(items, exists, has_table)
        self._sync_controls()

    def _show_only(self, which: str) -> None:
        for wid in ("#review-empty", "#review-split", "#review-history"):
            self.query_one(wid).display = wid == which
        focused = self.screen.focused
        if focused is None or focused is self or self in focused.ancestors:
            self.call_after_refresh(self._focus_main)

    def _fill_queue(self, items: list[Review], exists: bool, has_table: bool) -> None:
        if not items:
            self._items = []
            self._shown_id = None
            self.query_one("#review-list", OptionList).clear_options()
            empty = self.query_one("#review-empty", Static)
            if not exists:
                empty.update(Content(f"No state file yet ({self.app.paths.state}).\n"
                                     "Start the bot and unclear posts will show up here."))
            elif not has_table:
                empty.update(Content("This bot's state has no review queue yet."))
            else:
                empty.update(Content("Nothing to review.\n"
                                     "Unclear posts show up here by themselves as the bot finds them."))
            self._show_only("#review-empty")
            return
        self._show_only("#review-split")
        ol = self.query_one("#review-list", OptionList)
        new_keys = [(r.id, r.chat_title, r.role, r.at) for r in items]
        old_keys = [(r.id, r.chat_title, r.role, r.at) for r in self._items]
        prev = self.current
        prev_idx = ol.highlighted or 0
        self._items = items
        if new_keys != old_keys:
            ol.set_options(Option(entry(r), id=str(r.id)) for r in items)
            ids = [r.id for r in items]
            ol.highlighted = ids.index(prev.id) if prev is not None and prev.id in ids else min(prev_idx, len(items) - 1)
        elif ol.highlighted is None:
            ol.highlighted = 0
        self._sync_detail()

    def _fill_history(self, past: list[Review], exists: bool, has_table: bool) -> None:
        table = self.query_one("#review-history", DataTable)
        if not past:
            self.query_one("#review-empty", Static).update(
                Content("Nothing decided yet." if exists and has_table else "No review queue yet."))
            self._show_only("#review-empty")
            return
        self._show_only("#review-history")
        row = table.cursor_row
        table.clear()
        labels = {"to_send": "approved", "sent": "sent", "discarded": "discarded"}
        for r in past:
            table.add_row(when(r.decided_at or r.at), labels.get(r.status, r.status), cut(r.chat_title, 20),
                          cut(r.role, 22), cut(r.text, 40), key=str(r.id))
        table.move_cursor(row=min(row, len(past) - 1))

    # ------------------------------------------------------------ detail

    @on(OptionList.OptionHighlighted, "#review-list")
    def _highlighted(self, _event: OptionList.OptionHighlighted) -> None:
        self._sync_detail()

    def _sync_detail(self) -> None:
        r = self.current
        if r is None:
            self._shown_id = None
            self.query_one("#detail-head", Static).update("")
            self.query_one("#detail-text", Static).update("")
        elif r.id != self._shown_id:   # same post still open: leave the reader alone
            self._shown_id = r.id
            when_full = time.strftime("%Y-%m-%d %H:%M", time.localtime(r.at)) if r.at else "-"
            album = f"  ({len(r.album_ids)} messages)" if len(r.album_ids) > 1 else ""
            head = (f"chat:  {r.chat_title}  #{r.msg_id}{album}\n"
                    f"date:  {when_full}\n"
                    f"link:  {r.link or '(none)'}\n"
                    f"role:  {r.role or '(none)'}\n"
                    f"why review:  {r.reasons or '(no reason given)'}")
            self.query_one("#detail-head", Static).update(Content(head))
            self.query_one("#detail-text", Static).update(Content(r.text or "(no text)"))
            self.query_one("#review-detail", VerticalScroll).scroll_home(animate=False)
        self._sync_controls()

    # ----------------------------------------------------------- actions

    def _decide(self, status: str) -> None:
        r = self.current
        if r is None:
            return
        with self.app.store() as st:
            ok = st.decide(r.id, status)
        if not ok:
            self.note(f"#{r.msg_id} was already decided elsewhere", "warn")
        else:
            label_note = ""
            try:
                write_label(r, status)
            except OSError as e:
                label_note = f" (corpus label not saved: {e})"
            if status == "to_send":
                msg = ("approved: recorded only (dry run, nothing is sent)" if self.app.paths.dry_run
                       else "approved, the bot will send it in a few seconds")
            else:
                msg = "discarded"
            self.note(f"#{r.msg_id} {msg}{label_note}", "warn" if label_note else "good")
        self._shown_id = None if self._shown_id == r.id else self._shown_id
        self.reload()

    def action_send(self) -> None:
        self._decide("to_send")

    def action_discard(self) -> None:
        self._decide("discarded")

    def action_next(self) -> None:
        if not self._items or self._history_mode:
            return
        ol = self.query_one("#review-list", OptionList)
        ol.highlighted = ((ol.highlighted or 0) + 1) % len(self._items)

    def action_open_link(self) -> None:
        r = self.current
        if r is None:
            return
        if not r.link:
            self.note("this post has no link", "warn")
            return
        try:
            opened = webbrowser.open(r.link)
        except Exception:  # noqa: BLE001 - a broken browser setting shouldn't take the screen down
            opened = False
        if not opened:
            try:
                subprocess.Popen(["xdg-open", r.link], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                opened = True
            except OSError:
                pass
        self.note(f"opened {r.link}" if opened else f"couldn't open a browser: {r.link}",
                  "muted" if opened else "warn")

    def action_check(self) -> None:
        r = self.current
        if r is None:
            return
        if self.app.paths.binary is None:
            self.note("telenotify program not found: can't ask the filter", "warn")
            return
        self.note(f"asking the filter about #{r.msg_id} (it follows links, may take a while)…")
        self.run_worker(self._check(r), group="review-check", exclusive=True)

    async def _check(self, r: Review) -> None:
        paths = self.app.paths
        try:
            v = await bot.check(paths.binary, paths.config, r.text, scrape=True, show_text=True)
        except Exception as e:  # noqa: BLE001 - shown to the user instead
            v = bot.Verdict("", error=str(e))
        self.note("")
        self.app.push_screen(CheckModal(r, v))

    def action_scroll_post(self, pages: int) -> None:
        post = self.query_one("#review-detail", PostScroll)
        if pages > 0:
            post.scroll_page_down(animate=False)
        else:
            post.scroll_page_up(animate=False)

    def action_toggle_history(self) -> None:
        self._history_mode = not self._history_mode
        self.note("")
        self.reload()
        self.call_after_refresh(self._focus_main)

    @on(Button.Pressed)
    def _button(self, event: Button.Pressed) -> None:
        actions = {"btn-send": self.action_send, "btn-discard": self.action_discard,
                   "btn-next": self.action_next, "btn-open": self.action_open_link,
                   "btn-check": self.action_check, "btn-history": self.action_toggle_history}
        if event.button.id in actions:
            event.stop()
            actions[event.button.id]()
