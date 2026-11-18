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
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import Button, DataTable, Input, Select, Static, TextArea

from .. import bot
from ..clean import clean
from ..postview import PostView
from ..rtl import Block, visual
from ..store import Judged
from . import search_data

DEBOUNCE_SECONDS = 0.35
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
    return datetime.fromtimestamp(at).strftime(fmt) if at else "?"


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
        self._loaded = False

    # ------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        with Horizontal(id="search-bar"):
            yield Input(placeholder="search text, chat, role, reasons…  (Enter)", id="q")
            yield Select(VERDICT_OPTIONS, value="all", allow_blank=False, id="verdict")
            yield Select([("all chats", "all")], value="all", allow_blank=False, id="chat")
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
        self._refresh_chats()
        self.run_search()

    def _refresh_chats(self) -> None:
        sel = self.query_one("#chat", Select)
        current = sel.value
        try:
            with self.app.store() as s:
                chats = search_data.judged_chats(s) if s.exists else []
        except Exception:  # noqa: BLE001 - a locked or odd state file shouldn't crash the screen
            chats = []
        options = [("all chats", "all")] + [(Text(visual(short(title, 24))), cid) for cid, title in chats]
        if [o[1] for o in options] != [o[1] for o in getattr(self, "_chat_options", [])]:
            self._chat_options = options
            with sel.prevent(Select.Changed):
                sel.set_options(options)
                sel.value = current if any(o[1] == current for o in options) else "all"

    def run_search(self) -> None:
        if self._debounce is not None:
            self._debounce.stop()
            self._debounce = None
        query, verdict, chat_id = self._filters()
        count = self.query_one("#count", Static)
        rows: list[Judged] = []
        problem = ""
        try:
            with self.app.store() as s:
                if not s.exists:
                    problem = f"no state file yet ({self.app.paths.state})"
                elif not s.has_table("processed"):
                    problem = "nothing judged yet"
                else:
                    rows = search_data.search(s, query, verdict, chat_id, limit=LIMIT)
        except Exception as e:  # noqa: BLE001
            problem = f"couldn't read the state: {e}"
        self._fill(rows)
        if problem:
            count.update(Text(clean(problem), style="yellow"))
        else:
            n = len(rows)
            count.update(f"{n}{'+' if n >= LIMIT else ''} result{'s' if n != 1 else ''}")

    def _fill(self, rows: list[Judged]) -> None:
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
        self.run_search()
        if self._rows:
            self.query_one("#results", DataTable).focus()

    @on(Select.Changed, "#verdict")
    @on(Select.Changed, "#chat")
    def _filter_changed(self) -> None:
        self.run_search()

    @on(DataTable.RowHighlighted, "#results")
    def _highlighted(self, event: DataTable.RowHighlighted) -> None:
        j = self._rows.get(event.row_key.value) if event.row_key is not None else None
        if j is not self._selected:
            self._show_detail(j)

    # ------------------------------------------------------------ detail

    def _show_detail(self, j: Judged | None, keep_result: bool = False) -> None:
        self._selected = j
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

    # ---------------------------------------------------------- re-judge

    def action_rejudge(self, follow_links: bool = False) -> None:
        if self.has_class("pasting"):
            self._judge_paste(follow_links)
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
        self.add_class("pasting")
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
