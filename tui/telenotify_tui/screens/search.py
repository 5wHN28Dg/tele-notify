"""Search & explain: find any post the bot judged, see why, and ask the
current settings what they would say now.

* The search box matches text, chat, role and reasons, taken literally
  (case is ignored for Latin letters only); results update when you stop
  typing or press Enter. Verdict and chat filters narrow it down.
* The panel on the right explains the selected post: chat, time, verdict,
  what the bot did, every reason, and the full text.
* `j` re-judges that text with the current config (the real `telenotify
  check`, links not followed); `J` follows links too and shows the text the
  filter read. Then and now are shown side by side.
* `p` opens a box to paste any post and judge it, for trying things out.
* `y` copies the selected post's text.

Searching never blocks the screen: the state file is read in a thread
("searching…" shows meanwhile), a newer search replaces one still running,
and nothing is read until the tab is first shown.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from typing import ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import Button, DataTable, Input, Select, Static, TextArea
from textual.worker import get_current_worker

from .. import bot
from ..clean import clean
from ..postview import PostView, copy_post
from ..rtl import Block, visual
from ..store import BUSY, Judged, is_busy, unreadable_note
from . import search_data

DEBOUNCE_SECONDS = 0.35
LOCK_WAIT_SECONDS = 10      # how long a search waits for the bot's lock in all,
LOCK_STEP_SECONDS = 0.25    # in steps this long, checking whether it was replaced
CHATS_LOADING = "loading chats…"
CHATS_NOT_READ = "chats not read"   # the state file couldn't be read (the details say why)
LIMIT = 200

VERDICT_STYLE = {"publish": "bold green", "review": "bold yellow", "reject": "bold red"}
VERDICT_OPTIONS = [("all verdicts", "all"), ("publish", "publish"), ("review", "review"), ("reject", "reject")]


def verdict_text(verdict: str) -> Text:
    return Text(verdict or "?", style=VERDICT_STYLE.get(verdict, "bold"))


def action_label(action: str) -> str:
    a = action.lower()
    if a.startswith("forward"):
        return "forwarded"
    if a.startswith("link"):
        return "linked"
    if "review" in a:
        return "review"
    if a.startswith("reject"):
        return "rejected"
    return action or "?"


def short(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def when(at: int, fmt: str) -> str:
    try:
        return datetime.fromtimestamp(at).strftime(fmt) if at else "?"
    except (OverflowError, ValueError, OSError, TypeError):   # a broken time in the state file
        return "?"


def _locked(e: Exception) -> bool:
    return isinstance(e, sqlite3.OperationalError) and "locked" in str(e)


def _set_options(sel: Select, options: list, value) -> None:
    """New options and value, without a Changed message. The shown label follows even when
    the value stays the same ("all"): setting an equal value doesn't redraw it."""
    with sel.prevent(Select.Changed):
        sel.set_options(options)
        sel.value = value
        sel.mutate_reactive(Select.value)


def row_key(j: Judged) -> str:
    return f"{j.chat_id}:{j.msg_id}"


def verdict_report(v: bot.Verdict, then: str | None = None, show_text: bool = False) -> Text | Block:
    """The re-judge / paste result: then vs now (when there is a then), reasons, and the text read."""
    out = Text()
    if v.error or not v.verdict:
        out.append("Couldn't judge: ", style="bold red")
        out.append(clean(v.error) or "no verdict")
        return out
    if then is not None:
        out.append("then: ")
        out.append_text(verdict_text(then))
        out.append("   now: ")
        out.append_text(verdict_text(v.verdict))
        if then != v.verdict:
            out.append("   CHANGED", style="bold reverse yellow")
        else:
            out.append("   (same)", style="dim")
    else:
        out.append("verdict: ")
        out.append_text(verdict_text(v.verdict))
    out.append("\nreasons now:\n" if then is not None else "\nreasons:\n", style="bold")
    for r in v.reasons or ["(none given)"]:
        out.append(f"  • {visual(r)}\n")
    if show_text:
        out.append("text the filter read (links followed):", style="bold")
        return Block(v.text or "(nothing)", head=out)
    return out


class SearchPane(Vertical):
    live = False  # the search re-runs on ctrl+r or when the tab is shown

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("j", "rejudge(False)", "Re-judge"),
        Binding("J", "rejudge(True)", "Re-judge + links"),
        Binding("p", "paste", "Paste a post"),
        Binding("y", "copy_post", "Copy post", show=False),   # in the keys panel; the footer keeps the tab keys at 80 columns
        Binding("escape", "close_paste", "Back", show=False),
    ]

    DEFAULT_CSS = """
    SearchPane #search-bar { height: 3; }
    SearchPane #q { width: 1fr; }
    SearchPane #verdict { width: 20; }
    SearchPane #chat { width: 24; }
    SearchPane #count { width: auto; min-width: 12; padding: 1 1 0 1; color: $text-muted; }
    SearchPane #search-body { height: 1fr; }
    SearchPane #results { width: 3fr; height: 1fr; }
    SearchPane #detail { width: 2fr; height: 1fr; border-left: solid $panel; padding: 0 1; }
    SearchPane #judge-buttons { height: auto; margin: 1 0 0 0; }
    SearchPane #judge-buttons Button { margin-right: 1; min-width: 10; }
    SearchPane #judge-result { margin: 1 0 0 0; }
    SearchPane #search-detail-text { margin: 1 0 0 0; }
    SearchPane #paste { display: none; height: 1fr; }
    SearchPane.pasting #paste { display: block; }
    SearchPane.pasting #search-body, SearchPane.pasting #search-bar { display: none; }
    SearchPane #paste-text { height: 1fr; min-height: 5; }
    SearchPane #paste-buttons { height: auto; }
    SearchPane #paste-buttons Button { margin-right: 1; }
    SearchPane #paste-result-box { height: 1fr; max-height: 50%; }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rows: dict[str, Judged] = {}
        self._selected: Judged | None = None
        self._debounce: Timer | None = None
        self._loaded = False     # shown at least once: until then nothing is searched
        self._generation = 0     # the newest search started; older results are dropped
        self._pending: tuple | None = None   # (filters, with chats, after Enter) of the search still reading
        self._deferred_judge: bool | None = None   # j / J pressed while Enter's search ran
        self._chats_waiting: list[tuple[int, str]] | None = None   # read while the chat drop-down was open
        self._chats_shown = False   # the drop-down still says "loading chats…"

    # ------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        with Horizontal(id="search-bar"):
            yield Input(placeholder="search text, chat, role, reasons…  (Enter)", id="q")
            yield Select(VERDICT_OPTIONS, value="all", allow_blank=False, id="verdict")
            yield Select([(CHATS_LOADING, "all")], value="all", allow_blank=False, id="chat")
            yield Static("", id="count")
        with Horizontal(id="search-body"):
            table = DataTable(id="results", cursor_type="row", zebra_stripes=True)
            yield table
            with VerticalScroll(id="detail"):
                yield Static("Select a post to see why it got its verdict.", id="search-detail-head", classes="muted")
                yield Static("", id="detail-reasons")
                with Horizontal(id="judge-buttons"):
                    yield Button("Re-judge now (j)", id="rejudge", variant="primary")
                    yield Button("+ links (J)", id="rejudge-links")
                yield Static("", id="judge-note", classes="muted")
                yield PostView("", id="judge-result")
                yield PostView("", id="search-detail-text")
        with Vertical(id="paste"):
            yield Static("Paste any post below and judge it with the current settings. "
                         "Esc goes back to search.", classes="muted")
            yield TextArea(id="paste-text")
            with Horizontal(id="paste-buttons"):
                yield Button("Judge", id="paste-judge", variant="primary")
                yield Button("Judge following links", id="paste-judge-links")
                yield Button("Back (esc)", id="paste-close")
            with VerticalScroll(id="paste-result-box"):
                yield PostView("", id="paste-result")

    def on_mount(self) -> None:
        table = self.query_one("#results", DataTable)
        table.add_column("time", key="time")
        table.add_column("chat", key="chat")
        table.add_column("verdict", key="verdict")
        table.add_column("role", key="role")
        table.add_column("first reason", key="reason")
        self._show_detail(None)
        self.watch(self.query_one("#chat", Select), "expanded", self._chat_dropdown_toggled, init=False)
        note = self._binary_note()
        if note:
            self.query_one("#paste-result", Static).update(Text(note, style="yellow"))
            self.query_one("#paste-judge", Button).disabled = True
            self.query_one("#paste-judge-links", Button).disabled = True

    # ----------------------------------------------------------- helpers

    def _binary_ok(self) -> bool:
        b = getattr(self.app, "paths", None) and self.app.paths.binary
        return bool(b) and b.exists()

    def _binary_note(self) -> str:
        if self._binary_ok():
            return ""
        return ("The telenotify program wasn't found, so judging is off. "
                "Put it next to config.json or set TELENOTIFY_BIN.")

    def _filters(self) -> tuple[str, str | None, int | None]:
        query = self.query_one("#q", Input).value.strip()
        v = self.query_one("#verdict", Select).value
        c = self.query_one("#chat", Select).value
        return query, (v if v in ("publish", "review", "reject") else None), (c if isinstance(c, int) else None)

    # ------------------------------------------------------------ search

    def reload(self) -> None:
        """Refresh the chat list and re-run the current search."""
        self._loaded = True
        if self._pending is not None and self._pending[:2] == (self._filters(), True):
            return   # the same read just started (showing a tab asks twice)
        self.run_search(chats=True)

    def _apply_chats(self, chats: list[tuple[int, str]]) -> None:
        sel = self.query_one("#chat", Select)
        if sel.expanded:
            # never swap the list under the pointer: it comes when the drop-down closes
            self._chats_waiting = chats
            return
        self._chats_waiting = None
        current = sel.value
        options = [("all chats", "all")] + [(Text(visual(short(title, 24))), cid) for cid, title in chats]
        if [o[1] for o in options] != [o[1] for o in getattr(self, "_chat_options", [])] or not self._chats_shown:
            self._chats_shown = True
            self._chat_options = options
            _set_options(sel, options, current if any(o[1] == current for o in options) else "all")

    def _chat_dropdown_toggled(self, expanded: bool) -> None:
        if not expanded and self._chats_waiting is not None:
            self._apply_chats(self._chats_waiting)

    def run_search(self, chats: bool = False, focus_results: bool = False) -> None:
        """Search with the current filters (and with `chats`, refresh the chat list
        first). The state file is read in a thread: on a big file a query takes
        seconds, and the screen must keep answering meanwhile (issue #56). A
        newer search replaces one still running; its results are dropped, but
        what it still had to do (the chat list, Enter's move to the results)
        carries over to the newer one."""
        if self._debounce is not None:
            self._debounce.stop()
            self._debounce = None
        if self._pending is not None:
            chats = chats or self._pending[1]
            focus_results = focus_results or self._pending[2]
        self._generation += 1
        filters = self._filters()
        self._pending = (filters, chats, focus_results)
        self.query_one("#count", Static).update("searching…")   # #count's own muted colour (contrast)
        self._search_worker(self._generation, *filters, chats, focus_results)

    @work(thread=True, exclusive=True, group="search-state")
    def _search_worker(self, generation: int, query: str, verdict: str | None, chat_id: int | None,
                       chats: bool, focus_results: bool) -> None:
        worker = get_current_worker()
        # While the bot holds a lock, wait for it in short steps, so a replaced
        # search or quitting the screen doesn't sit out a 10 s busy timeout.
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        note = ""   # a failed read, in words, where the selected post's details go
        while True:
            try:
                found, rows, problem = self._read(worker, query, verdict, chat_id, chats)
                break
            except Exception as e:  # noqa: BLE001 - shown, not raised
                if worker.is_cancelled:
                    return
                if _locked(e) and time.monotonic() < deadline:
                    continue
                # the Dashboard's words, short in the count (it shares its row at 80 columns), whole beside it
                found, rows, problem = None, [], "file busy" if is_busy(e) else "can't read"
                note = f"{BUSY}. ctrl+r searches again." if is_busy(e) else unreadable_note(self._service())
                break
        if worker.is_cancelled:
            return
        try:
            self.app.call_from_thread(self._apply, generation, found, rows, problem, focus_results, note)
        except RuntimeError:   # the app closed while we read
            pass

    def _read(self, worker, query: str, verdict: str | None, chat_id: int | None,
              chats: bool) -> tuple[list[tuple[int, str]] | None, list[Judged], str]:
        """(chat list or None, rows, problem). Raises when the file is locked."""
        found: list[tuple[int, str]] | None = None
        with self.app.store(timeout=LOCK_STEP_SECONDS) as s:
            if s.db is not None:
                # a replaced search stops at once instead of finishing a long scan
                s.db.set_progress_handler(lambda: worker.is_cancelled, 10_000)
            if chats:
                try:
                    found = search_data.judged_chats(s) if s.exists else []
                except Exception as e:   # an odd state file shouldn't crash the screen
                    if worker.is_cancelled or _locked(e):
                        raise
                    found = []
                if chat_id is not None and chat_id not in {cid for cid, _ in found}:
                    chat_id = None   # the chosen chat is gone: the filter goes back to all chats
            if not s.exists:
                return found, [], f"no state file yet ({self.app.paths.state})"
            if not s.has_table("processed"):
                return found, [], "nothing judged yet"
            return found, search_data.search(s, query, verdict, chat_id, limit=LIMIT), ""

    def _service(self) -> str:
        return bot.service(self.app.paths.dry_run)

    def _apply(self, generation: int, chats: list[tuple[int, str]] | None, rows: list[Judged], problem: str,
               focus_results: bool, note: str = "") -> None:
        if generation != self._generation or not self.is_mounted:
            return   # a newer search is on its way
        self._pending = None
        if chats is not None:
            self._apply_chats(chats)
        table = self.query_one("#results", DataTable)
        # After Enter the results table may have focus already; a key or click there
        # while the search ran selected a post: it stays selected if it is among
        # the new rows, otherwise the cursor keeps its position.
        moved = focus_results and self.app.focused is table
        self._fill(rows, cursor=table.cursor_row if moved else None)
        count = self.query_one("#count", Static)
        if note:
            self.query_one("#search-detail-head", Static).update(Text(note, style="bold"))
            sel = self.query_one("#chat", Select)
            if not self._chats_shown and not sel.expanded:   # not "loading" any more
                _set_options(sel, [(CHATS_NOT_READ, "all")], "all")
        if problem:
            count.update(Text(clean(problem), style="yellow"))
        else:
            n = len(rows)
            count.update(f"{n}{'+' if n >= LIMIT else ''} result{'s' if n != 1 else ''}")
        if focus_results:
            box = self.query_one("#q", Input)
            if not self._rows and self.app.focused is table:
                box.focus()     # nothing found: back to the box, as before
            elif self._rows and self.app.focused is box:
                table.focus()   # Enter on an empty table: move once the results are in
        judge, self._deferred_judge = self._deferred_judge, None
        if judge is not None and self._rows and not self.has_class("pasting"):
            self.action_rejudge(judge)

    def _fill(self, rows: list[Judged], cursor: int | None = None) -> None:
        """Show `rows`. The selected post stays selected if it is among them;
        otherwise row `cursor` (the last one if there are fewer), or the first."""
        table = self.query_one("#results", DataTable)
        keep = row_key(self._selected) if self._selected else None
        table.clear()
        self._rows = {}
        for j in rows:
            k = row_key(j)
            if k in self._rows:
                continue
            self._rows[k] = j
            table.add_row(when(j.at, "%m-%d %H:%M"), Text(visual(short(j.chat_title, 14))), verdict_text(j.verdict),
                          Text(visual(short(j.role, 14))), Text(visual(short(search_data.main_reason(j.reasons), 32))),
                          key=k)
        if keep in self._rows:
            table.move_cursor(row=table.get_row_index(keep))
            self._show_detail(self._rows[keep], keep_result=True)
        elif cursor is not None and self._rows:
            row = min(max(cursor, 0), table.row_count - 1)
            table.move_cursor(row=row)
            self._show_detail(self._rows[str(table.coordinate_to_cell_key((row, 0)).row_key.value)])
        elif rows:
            table.move_cursor(row=0)
            self._show_detail(self._rows[row_key(rows[0])])
        else:
            self._show_detail(None)

    @on(Input.Changed, "#q")
    def _typed(self) -> None:
        if self._debounce is not None:
            self._debounce.stop()
        self._debounce = self.set_timer(DEBOUNCE_SECONDS, self.run_search)

    @on(Input.Submitted, "#q")
    def _submitted(self) -> None:
        # Enter moves to the results: at once when there are rows, so keys typed
        # while the search runs (Down, j) reach the table and act on the new
        # results; with an empty table only when results arrive, so letters typed
        # meanwhile still go into the box.
        table = self.query_one("#results", DataTable)
        if table.row_count:
            table.focus()
        self.run_search(focus_results=True)

    @on(Select.Changed, "#verdict")
    @on(Select.Changed, "#chat")
    def _filter_changed(self) -> None:
        # The filters announce their first value when they are mounted: that must
        # not search a tab nobody has opened yet (it held up the first tab, #56).
        if self._loaded:
            self.run_search()

    @on(DataTable.RowHighlighted, "#results")
    def _highlighted(self, event: DataTable.RowHighlighted) -> None:
        table = event.data_table
        if event.row_key is None or not table.row_count:
            return
        if event.row_key.value != table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value:
            return   # stale: the rows were replaced (or the cursor moved on) since it was sent
        j = self._rows.get(event.row_key.value)
        if j is not None and (self._selected is None or row_key(j) != row_key(self._selected)):
            self._show_detail(j)

    # ------------------------------------------------------------ detail

    def _show_detail(self, j: Judged | None, keep_result: bool = False) -> None:
        self._selected = j
        self.refresh_bindings()   # y (copy) is on only with a post open
        head = self.query_one("#search-detail-head", Static)
        reasons = self.query_one("#detail-reasons", Static)
        text = self.query_one("#search-detail-text", Static)
        note = self.query_one("#judge-note", Static)
        if not keep_result:
            self.query_one("#judge-result", Static).update("")
        buttons = [self.query_one("#rejudge", Button), self.query_one("#rejudge-links", Button)]
        if j is None:
            head.update(Text("Nothing selected. Search above, or press p to paste a post.", style="dim"))
            reasons.update("")
            text.update("")
            note.update("")
            for b in buttons:
                b.disabled = True
            return
        h = Text()
        h.append(visual(j.chat_title or str(j.chat_id)), style="bold")
        h.append(f"  ·  {when(j.at, '%Y-%m-%d %H:%M')}  ·  msg {j.msg_id}\n", style="dim")
        h.append("verdict: ")
        h.append_text(verdict_text(j.verdict))
        h.append(f"   action: {action_label(j.action)}")
        if j.role:
            h.append(f"   role: {visual(j.role)}")
        head.update(h)
        r = Text("why:\n", style="bold")
        for reason in search_data.split_reasons(j.reasons) or ["(no reasons stored)"]:
            r.append(f"  • {visual(reason)}\n", style="")
        reasons.update(r)
        if j.text.strip():
            text.update(Block(j.text, head=Text("text:", style="bold")))
        else:
            text.update(Text("The text of this post wasn't stored (older bot versions didn't keep it), "
                             "so it can't be shown or re-judged.", style="yellow"))
        binary_note = self._binary_note()
        can = not binary_note and bool(j.text.strip())
        for b in buttons:
            b.disabled = not can
        note.update(Text(binary_note))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "copy_post" and self.is_mounted:
            ok = not self.has_class("pasting") and self._selected is not None and bool(self._selected.text.strip())
            return True if ok else None   # shown, but off with no post open
        return True

    def action_copy_post(self) -> None:
        """Copy the selected post's text, as stored (reading order, not the order it is drawn in)."""
        j = self._selected
        if self.has_class("pasting") or j is None or not j.text.strip():
            return
        copy_post(self.app, j.text)

    # ---------------------------------------------------------- re-judge

    def action_rejudge(self, follow_links: bool = False) -> None:
        if self.has_class("pasting"):
            self._judge_paste(follow_links)
            return
        if self._pending is not None and self._pending[2]:
            self._deferred_judge = follow_links   # pressed after Enter: judge the new first result
            return
        j = self._selected
        out = self.query_one("#judge-result", Static)
        if j is None:
            return
        if not self._binary_ok():
            out.update(Text(self._binary_note(), style="yellow"))
            return
        if not j.text.strip():
            out.update(Text("No stored text to re-judge.", style="yellow"))
            return
        out.update(Text("judging with the current settings" + (", following links" if follow_links else "") + "…",
                        style="dim"))
        self.run_worker(self._rejudge(j, follow_links), exclusive=True, group="search-judge")

    async def _rejudge(self, j: Judged, follow_links: bool) -> None:
        paths = self.app.paths
        try:
            v = await bot.check(paths.binary, paths.config, j.text, scrape=follow_links, show_text=follow_links)
        except Exception as e:  # noqa: BLE001 - shown, not raised
            v = bot.Verdict("", error=str(e) or type(e).__name__)
        if self._selected is None or row_key(self._selected) != row_key(j):
            return  # the selection moved on while we waited
        self.query_one("#judge-result", Static).update(verdict_report(v, then=j.verdict, show_text=follow_links))

    @on(Button.Pressed, "#rejudge")
    def _rejudge_pressed(self) -> None:
        self.action_rejudge(False)

    @on(Button.Pressed, "#rejudge-links")
    def _rejudge_links_pressed(self) -> None:
        self.action_rejudge(True)

    # ------------------------------------------------------------- paste

    def action_paste(self) -> None:
        self._deferred_judge = None   # a j waiting for results never judges the paste box
        self.add_class("pasting")
        self.refresh_bindings()
        self.query_one("#paste-text", TextArea).focus()

    @on(TextArea.Changed, "#paste-text")
    def _paste_changed(self, event: TextArea.Changed) -> None:
        """Pasted text is shown as typed: drop control characters before they reach the terminal."""
        area = event.text_area
        text = clean(area.text)
        if text != area.text:
            area.text = text
            area.move_cursor(area.document.end)

    def action_close_paste(self) -> None:
        if self.has_class("pasting"):
            self.remove_class("pasting")
            self.refresh_bindings()
            self.query_one("#results", DataTable).focus()

    @on(Button.Pressed, "#paste-close")
    def _paste_close(self) -> None:
        self.action_close_paste()

    @on(Button.Pressed, "#paste-judge")
    def _paste_judge(self) -> None:
        self._judge_paste(False)

    @on(Button.Pressed, "#paste-judge-links")
    def _paste_judge_links(self) -> None:
        self._judge_paste(True)

    def _judge_paste(self, follow_links: bool) -> None:
        out = self.query_one("#paste-result", Static)
        text = self.query_one("#paste-text", TextArea).text
        if not self._binary_ok():
            out.update(Text(self._binary_note(), style="yellow"))
            return
        if not text.strip():
            out.update(Text("Paste some text first.", style="yellow"))
            return
        out.update(Text("judging…", style="dim"))
        self.run_worker(self._paste_worker(text, follow_links), exclusive=True, group="search-paste")

    async def _paste_worker(self, text: str, follow_links: bool) -> None:
        paths = self.app.paths
        try:
            v = await bot.check(paths.binary, paths.config, text, scrape=follow_links, show_text=follow_links)
        except Exception as e:  # noqa: BLE001
            v = bot.Verdict("", error=str(e) or type(e).__name__)
        self.query_one("#paste-result", Static).update(verdict_report(v, show_text=follow_links))
