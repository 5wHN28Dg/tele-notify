"""Review screen: go through the review queue quickly, by keyboard or mouse.

Left, the posts waiting for a decision; right, the one under the cursor.
`s` approves it (the running bot sends it after a 30-second undo window,
counted down at the top), `d` discards it,
`k` keeps it as a match without sending it (for a post that already reached
the channel another way, e.g. the old bot forwarded it), `n` / down moves on, `o` opens the post in the browser, `r` shows what the
filter read (it runs `telenotify check` with link scraping), `h` shows what
was decided recently.

`u` undoes the last decision made here; in History (`h`), it undoes the
decision under the cursor, wherever it was made (`telenotify review` too).
Anything the bot hasn't started sending can be undone: a discard, a keep, or
an approval still in its window (#62, #76). Once the bot sends a post, undo
isn't offered and the screen says it was sent. For a moment after a new
post appears, Send / Match / Discard are greyed and `s` / `d` are ignored, so
a double tap or a held key can't decide a post nobody has read. In a dry
run nothing is ever sent: `s` only records the choice (the screen says so).

Decisions are written exactly as `telenotify review` writes them
(Store.decide), and each one is also a label for the test corpus
(review_data.write_label).
"""

from __future__ import annotations

import sqlite3
import subprocess
import time
import webbrowser
from typing import ClassVar

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, OptionList, Static
from textual.widgets.option_list import Option

from .. import bot
from ..clean import clean
from ..postview import PostView
from ..rtl import Block, visual
from ..store import BUSY_NOTE, Review, is_busy, unreadable_note
from . import settings_data as sd
from .review_data import UNDOABLE, history, remove_label, write_label

DECISION_KEYS = ("send", "keep", "discard", "next", "open_link", "check")
UI_TIMEOUT = 1.0   # seconds to wait for the bot's lock: these reads and writes run on the UI thread
TICK_TIMEOUT = 0.25   # the countdown's once-a-second read waits less, and skips that second when busy

# a decision's status in plain words, for notes
PLAIN = {"pending": "back in the queue", "to_send": "approved", "sending": "being sent", "sent": "sent",
         "discarded": "discarded", "kept": "kept as a match, not sent"}


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


def status_label(r: Review, dry_run: bool, bot_up: bool = True, now: float | None = None) -> str:
    """A decided post's status in a few words (History's column: 15 characters at most, so the
    post column still starts on an 80-column screen), with an approved post's countdown (#62).
    `bot_up`: the bot is running; without it nothing is sent, and nothing is promised."""
    if r.status == "to_send":
        if dry_run:
            return "approved"
        if not bot_up:
            return "waits for bot"
        left = (r.send_after or 0) - int(time.time() if now is None else now)
        return f"sends in {left} s" if left > 0 else "sending soon"
    if r.status == "sending":
        return "sending now" if bot_up else "interrupted"
    return {"sent": "sent", "discarded": "discarded", "kept": "match, not sent"}.get(r.status, r.status)


def entry(r: Review) -> Content:
    """A queue entry: time and chat, with the role below it."""
    first = f"{when(r.at):>11}  {visual(cut(r.chat_title, 21))}\n"
    second = f"{'':>11}  {visual(cut(r.role or '(no role)', 21))}"
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
            yield Static(Content(f"What the filter read: {visual(self.review.chat_title)} #{self.review.msg_id}"),
                         classes="title")
            with VerticalScroll(id="check-body"):
                if v.error and not v.verdict:
                    yield Static(Content(clean(f"The filter couldn't judge it: {v.error}")), id="check-verdict",
                                 classes="bad")
                else:
                    yield Static(Content(f"verdict: {v.verdict}"), id="check-verdict")
                    reasons = "\n".join(f"  - {visual(r)}" for r in v.reasons) or "  (no reasons given)"
                    yield Static(Content(reasons), id="check-reasons")
                    yield Static("─ text the filter read (post + links) ─", classes="muted")
                    yield PostView(Block(v.text or "(no text shown)"), id="check-text")
            yield Button("Close (Esc)", id="check-close", compact=True)

    @on(Button.Pressed, "#check-close")
    def _close(self) -> None:
        self.dismiss()


class ReviewPane(Vertical, can_focus=True):  # focusable so the keys work even with nothing listed
    live = True  # the list refreshes every few seconds; the open post is left alone

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("s", "send", "Send"),
        Binding("k", "keep", "Match, don't send"),
        Binding("d", "discard", "Discard"),
        Binding("n", "next", "Next"),
        Binding("o", "open_link", "Open link"),
        Binding("r", "check", "Filter read"),
        Binding("h", "toggle_history", "History"),
        Binding("u", "undo", "Undo"),
        Binding("space", "scroll_post(1)", "Scroll post"),
        Binding("b", "scroll_post(-1)", "Scroll back", show=False),
    ]

    DEFAULT_CSS = """
    ReviewPane { height: 1fr; }
    ReviewPane #review-top { height: auto; }   /* a long note wraps instead of being cut */
    ReviewPane #review-summary { width: auto; text-style: bold; }
    ReviewPane #review-note { width: 1fr; margin-left: 2; height: auto; }   /* wraps: never cut */
    /* while an approval counts down, its note changes by itself: two lines are kept for it, so
       the post below never moves without a key press (P4) */
    ReviewPane #review-note.-reserve { min-height: 2; }
    ReviewPane #review-dryrun { height: auto; }
    ReviewPane #review-empty { height: 1fr; content-align: center middle; text-align: center; }
    ReviewPane #review-split { height: 1fr; }
    ReviewPane #review-list { width: 40%; max-width: 56; min-width: 28; height: 1fr; }
    ReviewPane #review-detail { width: 1fr; height: 1fr; padding: 0 1; border-left: tall $panel; }
    ReviewPane #detail-head { margin-bottom: 1; }
    ReviewPane #review-history { height: 1fr; }
    ReviewPane #review-buttons { height: 1; margin-top: 1; }
    ReviewPane #review-buttons Button { margin-right: 1; width: auto; min-width: 4; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._items: list[Review] = []
        self._shown_key: tuple | None = None       # the post drawn on the right, and its content
        self._history_mode = False
        self._await_pick = False                   # the open post vanished: nothing selected until the reader moves
        self._just_decided: int | None = None      # decided here: moving on to the next post is expected
        self._read_error = False
        self._shown_at = 0.0                       # time.monotonic() when the open post was drawn
        self._last: tuple[Review, str] | None = None   # the last decision made here, while undoable
        self._decided_note = ""                    # what that decision's note said
        self._label_note = ""                      # that decision's corpus-label problem, if any
        self._unrecorded = ""                      # a decision the state file refused, said until the next one
        self._press_at: float | None = None        # when the key or click deciding now was *made*
        self._past: list[Review] = []              # the History rows, in table order
        self._bot_up = True                        # the bot was running when the state was last read
        self._last_now: Review | None = None       # the last approval made here, as last read
        self._ticker = None                        # the countdown's 1 s timer (paused when nothing counts)

    # seconds during which s / d (keys and buttons) are ignored after a new post
    # is shown: a double tap or a held key mustn't decide a post nobody has read
    DECIDE_DELAY: ClassVar[float] = 0.8
    # how often an approved post's countdown (top note, History) is redrawn
    COUNTDOWN_EVERY: ClassVar[float] = 1.0

    @property
    def _settling(self) -> bool:
        """A new post was shown less than DECIDE_DELAY ago: Send / Match / Discard wait."""
        return (self.DECIDE_DELAY > 0 and self._shown_key is not None
                and time.monotonic() - self._shown_at < self.DECIDE_DELAY)

    # ------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        with Horizontal(id="review-top"):
            yield Static("", id="review-summary")
            yield Static("", id="review-note", classes="muted")
        yield Static(Content("DRY RUN: Send only records your choice (and the corpus label); "
                             "nothing is forwarded to the channel."), id="review-dryrun", classes="warn")
        yield Static("", id="review-empty")
        with Horizontal(id="review-split"):
            yield OptionList(id="review-list")
            with PostScroll(id="review-detail"):
                yield Static("", id="detail-head")
                yield PostView("", id="detail-text")
        table = DataTable(id="review-history", cursor_type="row", zebra_stripes=True)
        yield table
        with Horizontal(id="review-buttons"):
            yield KeyButton("Send", id="btn-send", variant="success", compact=True)
            yield KeyButton("Match, no send", id="btn-keep", variant="primary", compact=True)
            yield KeyButton("Discard", id="btn-discard", variant="error", compact=True)
            yield KeyButton("Next", id="btn-next", compact=True)
            yield KeyButton("Link", id="btn-open", compact=True)
            yield KeyButton("Filter read", id="btn-check", compact=True)
            yield KeyButton("History", id="btn-history", compact=True)
            yield KeyButton("Undo", id="btn-undo", compact=True)

    def on_mount(self) -> None:
        table = self.query_one("#review-history", DataTable)
        table.add_columns("decided", "status", "chat", "role", "post")
        table.display = False
        self.query_one("#review-split").display = False
        dry = self.app.paths.dry_run
        self.query_one("#review-dryrun").display = dry
        if dry:
            self.query_one("#btn-send", Button).tooltip = "Dry run: records your choice only, forwards nothing"
        self.query_one("#btn-keep", Button).tooltip = (
            "A match, but don't send it (it already reached the channel): "
            "labelled a match in the corpus, never forwarded")
        if self.app.paths.binary is None:
            self.query_one("#btn-check", Button).tooltip = "the telenotify program wasn't found"
            self.note("no telenotify program: Filter read is off", "warn")
        if getattr(self.app, "refresh_seconds", 0) > 0:   # tests call _countdown themselves
            self._ticker = self.set_interval(self.COUNTDOWN_EVERY, self._countdown, pause=True)

    def on_show(self) -> None:
        self.call_after_refresh(self._focus_main)
        self._arm()

    def on_hide(self) -> None:
        if self._ticker is not None:
            self._ticker.pause()   # only while Review is shown (P1)

    def _counting(self) -> bool:
        """Is something on screen counting down, or about to be sent by a running bot?
        Only then does the screen look again every second."""
        if self.app.paths.dry_run:   # a dry run never sends: nothing counts down
            return False
        now = time.time()

        def live(r: Review | None) -> bool:
            if r is None:
                return False
            if r.status == "to_send":
                return self._bot_up   # a stopped bot sends nothing: no second-by-second reading
            return r.status == "sending" and self._bot_up
        if self._history_mode:
            return any(live(r) or (r.status == "to_send" and (r.send_after or 0) > now and self._bot_up)
                       for r in self._past)
        return self._last is not None and self._last[1] == "to_send" and live(self._last_now)

    def _arm(self) -> None:
        """Run the 1 s timer while Review is shown and something counts down; else pause it."""
        if self._ticker is None:
            return
        if self._shown(self) and self._counting():
            self._ticker.resume()
        else:
            self._ticker.pause()

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
        reserve = n.has_class("-reserve")
        n.set_classes(style)
        n.set_class(reserve, "-reserve")   # the height kept for a countdown stays (P4)
        n.update(Content(visual(text)))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in DECISION_KEYS and self.is_mounted:
            if action == "next":   # also picks the first post when nothing is selected
                return True if self._items and not self._history_mode else None
            if self.current is None:
                return None  # shown, but disabled
            if self._settling and action in ("send", "keep", "discard"):
                return None  # greyed in the footer too while a new post's wait runs (on_key says why)
            if action == "check" and self.app.paths.binary is None:
                return None
        if action == "undo" and self.is_mounted:
            return True if self._undo_target() is not None else None
        return True

    def _history_pick(self) -> Review | None:
        """The History row under the cursor, if History is shown."""
        if not self._history_mode:
            return None
        table = self.query_one("#review-history", DataTable)
        if not table.display or not 0 <= table.cursor_row < len(self._past):
            return None
        return self._past[table.cursor_row]

    def _undo_target(self) -> tuple[Review, str] | None:
        """What u would undo: in History, the row under the cursor if it can still be
        undone; in the queue, the last decision made here."""
        if self._history_mode:
            r = self._history_pick()
            return (r, r.status) if r is not None and r.status in UNDOABLE else None
        if self._last is not None and (self._last_now is None or self._last_now.status != "sending"):
            return self._last
        return None

    def _sync_controls(self) -> None:
        none = self.current is None
        for bid in ("#btn-send", "#btn-keep", "#btn-discard", "#btn-next", "#btn-open", "#btn-check"):
            button = self.query_one(bid, Button)
            off = none and not (bid == "#btn-next" and self._items and not self._history_mode)
            button.display = not off  # with nothing open, only History (and Next, to pick one) is offered
            button.disabled = (off or (bid == "#btn-check" and self.app.paths.binary is None)
                               or (self._settling and bid in ("#btn-send", "#btn-keep", "#btn-discard")))
        self.query_one("#btn-history", Button).label = "Queue" if self._history_mode else "History"
        self.query_one("#btn-undo", Button).display = self._undo_target() is not None
        self.refresh_bindings()

    # ------------------------------------------------------------ reload

    def reload(self) -> None:
        paths = self.app.paths
        try:
            with self.app.store(UI_TIMEOUT) as st:
                exists, has_table = st.exists, st.has_table("reviews")
                items = st.reviews("pending")
                past = history(st) if self._history_mode else []
                bot_up = st.bot_running()
                last_now = st.review(self._last[0].id) if self._watching_last() else None
        except (sqlite3.Error, OSError, ValueError) as e:   # a lock, a broken file, a value that isn't a number
            self._read_error = True
            busy = is_busy(e)
            problem = BUSY_NOTE if busy else unreadable_note(bot.service(paths.dry_run))   # the Dashboard's words
            if self._unrecorded:   # beside, never instead of, a decision that wasn't recorded
                self.note(f"{self._unrecorded}  ·  {problem}", "bad")
            else:
                self.note(problem, "warn")
            if self._history_mode or not self._items:   # the note above says why, once (#106)
                self.query_one("#review-empty", Static).update(Content("Nothing to show until the state file can be read."))
                self._show_only("#review-empty")
            self._sync_controls()
            return
        if self._read_error:
            self._read_error = False
            self.note(self._unrecorded, "bad") if self._unrecorded else self.note("")
        self._bot_up = bot_up
        if self._watching_last() and self._last is not None:
            self._show_last(self._last[0], last_now)
        mode = " · dry run" if paths.dry_run else ""
        self.query_one("#review-summary", Static).update(
            Content(f"Recently decided{mode}" if self._history_mode
                    else f"{len(items)} waiting for review{mode}"))
        if self._history_mode:
            self._fill_history(past, exists, has_table)
        else:
            self._fill_queue(items, exists, has_table)
        self._sync_controls()
        self._arm()

    def _watching_last(self) -> bool:
        """An approval made here may still change by itself (sent, or the bot stopped)."""
        return self._last is not None and self._last[1] == "to_send" and not self.app.paths.dry_run

    def _show_only(self, which: str) -> None:
        for wid in ("#review-empty", "#review-split", "#review-history"):
            self.query_one(wid).display = wid == which
        focused = self.screen.focused
        if focused is None or focused is self or self in focused.ancestors:
            self.call_after_refresh(self._focus_main)

    def _fill_queue(self, items: list[Review], exists: bool, has_table: bool) -> None:
        if not items:
            self._items = []
            self._shown_key = None
            self._await_pick = False
            self._just_decided = None
            self.query_one("#review-list", OptionList).clear_options()
            empty = self.query_one("#review-empty", Static)
            if not exists:
                empty.update(Content(f"No state file yet ({clean(self.app.paths.state)}).\n"
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
        just_decided, self._just_decided = self._just_decided, None
        self._items = items
        if new_keys != old_keys:
            ids = [r.id for r in items]
            if prev is not None and prev.id not in ids and prev.id != just_decided:
                # The open post was decided elsewhere (another window, `telenotify review`).
                # Don't slide another post under the cursor: a quick s / d would act on
                # a post nobody has read yet.
                self._await_pick = True
                self.note(f"#{prev.msg_id} was decided elsewhere; pick the next one", "warn")
            ol.set_options(Option(entry(r), id=str(r.id)) for r in items)
            if self._await_pick:
                ol.highlighted = None
            elif prev is not None and prev.id in ids:
                ol.highlighted = ids.index(prev.id)
            else:
                ol.highlighted = min(prev_idx, len(items) - 1)
        elif ol.highlighted is None and not self._await_pick:
            ol.highlighted = 0
        self._sync_detail()

    def _fill_history(self, past: list[Review], exists: bool, has_table: bool) -> None:
        table = self.query_one("#review-history", DataTable)
        old, self._past = self._past, past
        row = table.cursor_row
        # the review under the cursor, so a reload (a decision elsewhere, a send) never slides
        # another one under it: u must act on the row you chose (P4)
        keep = old[row].id if table.row_count and 0 <= row < len(old) else None
        if not past:
            self.query_one("#review-empty", Static).update(
                Content("Nothing decided yet." if exists and has_table else "No review queue yet."))
            self._show_only("#review-empty")
            return
        self._show_only("#review-history")
        table.clear()
        dry, now = self.app.paths.dry_run, time.time()
        for r in past:
            table.add_row(when(r.decided_at or r.at), Text(status_label(r, dry, self._bot_up, now)),
                          Text(visual(cut(r.chat_title, 20))), Text(visual(cut(r.role, 22))),
                          Text(visual(cut(r.text, 40))), key=str(r.id))
        ids = [r.id for r in past]
        table.move_cursor(row=ids.index(keep) if keep in ids else min(row, len(past) - 1))

    # ------------------------------------------------------------ detail

    @on(DataTable.RowHighlighted, "#review-history")
    def _history_row(self, _event: DataTable.RowHighlighted) -> None:
        self._sync_controls()   # u / Undo follow the row under the cursor

    @on(OptionList.OptionHighlighted, "#review-list")
    def _highlighted(self, _event: OptionList.OptionHighlighted) -> None:
        self._await_pick = False   # the reader picked a post (only they move the cursor while we wait)
        self._sync_detail()

    def _sync_detail(self) -> None:
        r = self.current
        key = (r.id, r.chat_title, r.msg_id, r.at, r.link, r.role, r.reasons, r.text,
               tuple(r.album_ids)) if r is not None else None
        if r is None:
            self._shown_key = None
            self.query_one("#detail-head", Static).update("")
            self.query_one("#detail-text", Static).update("")
        elif key != self._shown_key:   # same post, unchanged: leave the reader alone
            self._shown_key = key
            self._shown_at = time.monotonic()
            if self.DECIDE_DELAY > 0:   # greyed while s / d are ignored, so the wait shows (#76)
                self.set_timer(self.DECIDE_DELAY, self._settled)
            when_full = time.strftime("%Y-%m-%d %H:%M", time.localtime(r.at)) if r.at else "-"
            album = f"  ({len(r.album_ids)} messages)" if len(r.album_ids) > 1 else ""
            head = (f"chat:  {visual(r.chat_title)}  #{r.msg_id}{album}\n"
                    f"date:  {when_full}\n"
                    f"link:  {visual(r.link or '(none)')}\n"
                    f"role:  {visual(r.role or '(none)')}\n"
                    f"why review:  {visual(r.reasons or '(no reason given)')}")
            self.query_one("#detail-head", Static).update(Content(head))
            self.query_one("#detail-text", Static).update(Block(r.text or "(no text)"))
            self.query_one("#review-detail", VerticalScroll).scroll_home(animate=False)
        self._sync_controls()

    def _settled(self) -> None:
        if self._settling:   # a later post's wait: look again when it ends
            self.set_timer(max(0.01, self.DECIDE_DELAY - (time.monotonic() - self._shown_at)), self._settled)
        else:
            self._sync_controls()

    # --------------------------------------------------------- countdown

    def _countdown(self) -> None:
        """Every second while something counts down: redraw it, and notice when the bot
        has started sending it, sent it, or stopped. Reads wait at most TICK_TIMEOUT for
        the bot's lock, and skip that second when it's busy (P1)."""
        if not self.is_mounted:
            return
        if not self._counting():
            self._arm()
            return
        if self._history_mode:
            try:
                with self.app.store(TICK_TIMEOUT) as st:
                    past = history(st)
                    bot_up = st.bot_running()
                    exists, has_table = st.exists, st.has_table("reviews")
            except (sqlite3.Error, OSError, ValueError):
                return   # busy or broken: the next second tries again
            self._bot_up = bot_up
            self._fill_history(past, exists, has_table)
            self._sync_controls()
        elif self._last is not None:
            try:
                with self.app.store(TICK_TIMEOUT) as st:
                    now = st.review(self._last[0].id)
                    bot_up = st.bot_running()
            except (sqlite3.Error, OSError, ValueError):
                return
            self._bot_up = bot_up
            self._show_last(self._last[0], now)
        self._arm()

    def _undo_seconds(self) -> int:
        """config.json's `undo_seconds`, as the bot reads it (default 30)."""
        try:
            return sd.undo_seconds(sd.load(self.app.paths.config))
        except (sd.ConfigError, OSError, ValueError):
            return sd.INT_KEYS["undo_seconds"]

    def _approved_note(self, r: Review, send_after: int | None) -> str:
        """An approval's note, at most two lines at 80 columns (the note keeps two for it)."""
        if self.app.paths.dry_run:
            return f"#{r.msg_id} approved: recorded only (dry run, nothing is sent) (u undoes)"
        if not self._bot_up:
            return f"#{r.msg_id} approved. The bot isn't running: it will be sent when the bot starts (u undoes)"
        left = (send_after or 0) - int(time.time())
        if left > 0:
            return f"#{r.msg_id} approved: sent to the channel in {left} s (u undoes)"
        return f"#{r.msg_id} approved: the bot sends it within seconds (u undoes until it starts)"

    def _show_last(self, decided: Review, now: Review | None) -> None:
        """The top note for an approval made here, as it is now."""
        self._last_now = now
        if now is not None and now.status == "to_send":
            self._decided_note = self._approved_note(decided, now.send_after) + self._label_note
            self.note(self._decided_note, "warn" if self._label_note else "good")
            return
        if now is not None and now.status == "sending" and self._bot_up:
            # still watched, so "sent" follows; no undo offered (_undo_target)
            self._decided_note = f"#{decided.msg_id} is being sent to the channel now: too late to undo"
            self.note(self._decided_note, "warn")
            self._sync_controls()
            return
        self._last = None
        if now is not None and now.status == "sent":
            self._decided_note = f"#{decided.msg_id} sent to the channel"
            self.note(self._decided_note, "good")
        elif now is not None and now.status == "sending":
            self._decided_note = (f"#{decided.msg_id}: sending was interrupted when the bot stopped; "
                                  "it goes back to the queue when the bot starts")
            self.note(self._decided_note, "warn")
        else:
            self._decided_note = ""
            self.note(f"#{decided.msg_id} was changed elsewhere: it is now "
                      f"{PLAIN.get(now.status, now.status) if now is not None else 'gone from the queue'}", "warn")
        self._sync_controls()

    def _reserve_note(self, on: bool) -> None:
        self.query_one("#review-note", Static).set_class(on, "-reserve")

    # ----------------------------------------------------------- actions

    def on_key(self, event: events.Key) -> None:
        # a key carries the time it was pressed; a slow machine may handle it much later
        if event.key in ("s", "k", "d") and self.current is not None:
            self._press_at = event.time   # a footer click's key carries the click's time (app.simulate_key)
            if self._settling:   # the binding is greyed: say why here
                self._too_early(self.current)
        else:
            self._press_at = None

    def _too_early(self, r: Review) -> None:
        before = f"{self._decided_note}  ·  " if self._decided_note else ""
        self.note(f"{before}#{r.msg_id} has only just appeared: read it, then decide", "warn")

    def _made(self) -> float:
        """When the press deciding now was made (else now), used once."""
        made, self._press_at = self._press_at, None
        return made if made is not None else time.monotonic()

    def _decide(self, status: str) -> None:
        r = self.current
        if r is None:
            return
        if self._made() - self._shown_at < self.DECIDE_DELAY:
            # a double tap / held key, or a click that landed as the next post appeared
            self._too_early(r)
            return
        try:
            with self.app.store(UI_TIMEOUT) as st:
                ok = st.decide(r.id, status, undo_seconds=self._undo_seconds())
                now = st.review(r.id) if ok else None
                self._bot_up = st.bot_running()
        except (sqlite3.Error, OSError) as e:   # the bot holds a lock, or the file is broken
            self._unrecorded = f"#{r.msg_id} NOT recorded (the state file can't be written: {e}); try again"
            self.note(self._unrecorded, "bad")
            return
        self._unrecorded = ""
        self._just_decided = r.id
        if not ok:
            self._decided_note = ""
            self.note(f"#{r.msg_id} was already decided elsewhere", "warn")
        else:
            self._last = (r, status)
            label_note = ""
            try:
                write_label(r, status)
            except (OSError, ValueError) as e:
                label_note = f" (corpus label not saved: {e})"
            self._label_note = label_note
            self._last_now = now
            self._reserve_note(status == "to_send" and not self.app.paths.dry_run)
            if status == "to_send":
                self._decided_note = self._approved_note(r, now.send_after if now else None) + label_note
            else:
                msg = "kept as a match, not sent" if status == "kept" else "discarded"
                self._decided_note = f"#{r.msg_id} {msg}{label_note} (u undoes)"
            self.note(self._decided_note, "warn" if label_note else "good")
        self.reload()

    def action_undo(self) -> None:
        """Put a decided post back in the queue, if the bot hasn't started sending it: in
        History the row under the cursor, else the last post decided here."""
        target = self._undo_target()
        if target is None:
            return
        r, status = target
        try:
            with self.app.store(UI_TIMEOUT) as st:
                ok, now = st.undo(r.id, status)
        except (sqlite3.Error, OSError) as e:
            self.note(f"#{r.msg_id} NOT undone (the state file can't be written: {e}); try again", "bad")
            return
        if self._last is not None and self._last[0].id == r.id:
            self._last = None
        self._reserve_note(self._watching_last())   # a key press: the note may change height now
        self._decided_note = ""
        if not ok:
            if now == "sent":
                self.note(f"too late to undo: the bot has already sent #{r.msg_id} to the channel", "bad")
            elif now == "sending":
                self.note(f"too late to undo: the bot is sending #{r.msg_id} to the channel now", "bad")
            else:
                self.note(f"#{r.msg_id} can't be undone: it is now "
                          f"{PLAIN.get(now, now) if now else 'gone from the queue'}", "warn")
            if self._history_mode:
                self.reload()
            self._sync_controls()
            return
        label_note = ""
        try:
            remove_label(r)
        except (OSError, ValueError) as e:
            label_note = f" (its corpus label couldn't be removed: {e})"
        self.note(f"#{r.msg_id} is back in the queue{label_note}", "warn" if label_note else "good")
        self.reload()
        ids = [x.id for x in self._items]
        if not self._history_mode and r.id in ids:   # open it again
            ol = self.query_one("#review-list", OptionList)
            self._await_pick = False
            ol.highlighted = ids.index(r.id)
            self._sync_detail()

    def action_send(self) -> None:
        self._decide("to_send")

    def action_keep(self) -> None:
        """A match, but don't send it: it already reached the channel another way."""
        self._decide("kept")

    def action_discard(self) -> None:
        self._decide("discarded")

    def action_next(self) -> None:
        if not self._items or self._history_mode:
            return
        ol = self.query_one("#review-list", OptionList)
        ol.highlighted = 0 if ol.highlighted is None else (ol.highlighted + 1) % len(self._items)

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
        actions = {"btn-send": self.action_send, "btn-keep": self.action_keep,
                   "btn-discard": self.action_discard,
                   "btn-next": self.action_next, "btn-open": self.action_open_link,
                   "btn-check": self.action_check, "btn-history": self.action_toggle_history,
                   "btn-undo": self.action_undo}
        if event.button.id in actions:
            event.stop()
            # a click: when it was made, from the app's record (Textual rebuilds mouse events,
            # with a later time, before a widget sees them); none recorded means now
            clicks = getattr(self.app, "press_made_at", {}).pop(event.button, None)
            if clicks:
                self._press_at = clicks[-1]
            elif getattr(self.app, "is_headless", False):
                self._press_at = None   # Textual's test pilot clicks past the app: no stall there
            else:
                self._press_at = float("-inf")   # a click we can't vouch for counts as too early
            actions[event.button.id]()
            self._press_at = None
