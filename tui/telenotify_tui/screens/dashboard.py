"""Dashboard: is the bot alive, what is it doing, and does anything need a look (issue #10).

Top: the bot panel (service state, alive, connection, pending reviews, and
start / stop / restart for the service of the mode this screen shows) and
the activity panel (today / 7-day totals and a 14-day chart). Below, three
views: the live feed of judged messages, per-chat numbers, and alerts with
chat health.

`reload()` never blocks: the state file is read in a thread and systemctl
runs as a subprocess, both in workers; the widgets are updated when they
finish. A missing state file, table, or systemctl shows a muted note.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    ContentSwitcher,
    DataTable,
    Label,
    RadioButton,
    RadioSet,
    Static,
    TabbedContent,
    Tabs,
)
from textual.widgets.data_table import CellDoesNotExist

from .. import bot
from ..clean import clean
from ..rtl import Block, visual
from ..store import Alert, ChatStats, Judged, Store
from . import dashboard_data as dd
from . import search_data

ALIVE_SECONDS = 120
FEED_ROWS = 50
CHART_DAYS = 14
CHART_HEIGHT = 3
VERDICT_STYLE = {"publish": "green", "review": "yellow", "reject": "dim"}
STAT_COLUMNS = ("chat", "total", "publish", "review", "reject", "forwarded")


# ------------------------------------------------------------- helpers

def fmt_age(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m}m" if m else f"{h}h"
    d, h = divmod(h, 24)
    return f"{d}d {h}h" if h else f"{d}d"


def fmt_time(at: int, now: float) -> str:
    if not at:
        return "?"
    t = datetime.fromtimestamp(at)
    if t.date() == datetime.fromtimestamp(now).date():
        return t.strftime("%H:%M")
    return t.strftime("%d %b %H:%M")


def first_reason(reasons: str) -> str:
    return search_data.main_reason(reasons)


def render_chart(days: list[dd.DayCount], height: int = CHART_HEIGHT) -> Text:
    """A small stacked bar chart: publish (green) at the bottom, then review (yellow), then reject (dim)."""
    top = max((d.total for d in days), default=0)
    out = Text()
    units = height * 8
    eighths = " ▁▂▃▄▅▆▇█"
    scaled = []
    for d in days:
        if top == 0:
            scaled.append((0, 0, 0))
            continue
        p = round(d.publish * units / top)
        r = round((d.publish + d.review) * units / top)
        t = round(d.total * units / top)
        if d.total and t == 0:
            t = 1  # any activity shows at least a sliver
        scaled.append((p, max(r, p), max(t, r, p)))
    for row in range(height - 1, -1, -1):
        lo = row * 8
        for i, (p, r, t) in enumerate(scaled):
            fill = min(8, max(0, t - lo))
            mid = lo + max(fill, 1) / 2
            style = "green" if mid < p else "yellow" if mid < r else "grey50"
            out.append(eighths[fill] * 2, style=style)
            if i < len(scaled) - 1:
                out.append(" ")
        out.append("\n")
    for i, d in enumerate(days):
        label = d.day[-2:]
        out.append(label, style="bold" if i == len(days) - 1 else "dim")
        if i < len(days) - 1:
            out.append(" ")
    return out


# ------------------------------------------------------------ snapshot

@dataclass
class Snapshot:
    """Everything the dashboard shows from the state file, read in one go."""
    exists: bool = False
    now: float = 0.0
    last_alive: int | None = None
    started_at: int | None = None
    mode: str = ""
    connection: str = ""
    pending: int = 0
    today: dd.Totals = field(default_factory=dd.Totals)
    week: dd.Totals = field(default_factory=dd.Totals)
    days: list[dd.DayCount] = field(default_factory=list)
    stats: list[ChatStats] = field(default_factory=list)
    feed: list[Judged] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    problems: list[dd.ChatProblem] = field(default_factory=list)
    error: str = ""


def _int(v: str) -> int | None:
    v = v.strip()
    return int(v) if v.isascii() and v.isdigit() else None


def load_snapshot(path: Path, stat_days: int) -> Snapshot:
    now = time.time()
    snap = Snapshot(now=now)
    try:
        with Store(path) as s:
            snap.exists = s.exists
            if not s.exists:
                return snap
            snap.last_alive = s.last_alive()
            snap.started_at = _int(s.meta("started_at"))
            snap.mode = s.meta("mode")
            snap.connection = s.meta("connection")
            snap.pending = s.review_counts().get("pending", 0)
            snap.today = dd.totals(s, dd.local_midnight(0, now))
            snap.week = dd.totals(s, int(now) - 7 * 86400)
            snap.days = dd.per_day(s, CHART_DAYS, now)
            snap.stats = s.stats(stat_days)
            snap.feed = s.recent(FEED_ROWS)
            snap.alerts = s.alerts(100)
            snap.problems = dd.chat_problems(s, now)
    except (sqlite3.Error, OSError, ValueError) as e:  # a locked or half-written file: say so, try again next tick
        snap.error = clean(f"{type(e).__name__}: {e}")
    return snap


# --------------------------------------------------------------- modal

class ConfirmScreen(ModalScreen[bool]):
    """Yes / no question; Escape or "No" answers no."""

    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    ConfirmScreen > Vertical {
        width: 60; height: auto; padding: 1 2;
        border: thick $warning; background: $surface;
    }
    ConfirmScreen Label { width: 100%; margin-bottom: 1; }
    ConfirmScreen Horizontal { height: auto; align-horizontal: right; }
    ConfirmScreen Button { margin-left: 2; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "answer(False)", "Cancel"), Binding("y", "answer(True)", "Yes", show=False),
                Binding("n", "answer(False)", "No", show=False)]

    def __init__(self, question: str):
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.question, id="confirm-question")
            with Horizontal():
                yield Button("Yes", id="confirm-yes", variant="warning")
                yield Button("No", id="confirm-no", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#confirm-no", Button).focus()

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes)

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "confirm-yes")


# ---------------------------------------------------------------- pane

class DashboardPane(Vertical):
    live = True  # reload every few seconds while visible

    DEFAULT_CSS = """
    DashboardPane #dash-top { height: 9; }
    DashboardPane #dash-bot { width: 1fr; border: round $primary; padding: 0 1; }
    DashboardPane #dash-activity { width: 50; border: round $primary; padding: 0 1; }
    DashboardPane #svc-buttons { height: 1; width: auto; }
    DashboardPane #svc-buttons Button { margin-right: 1; min-width: 0; }
    DashboardPane #svc-row { height: 1; }
    DashboardPane #svc-name { width: 1fr; padding-left: 1; }
    DashboardPane #dash-nav { height: 1; margin-top: 1; }
    DashboardPane #dash-nav Button { margin-right: 1; }
    DashboardPane #dash-nav Button.-active { text-style: bold reverse; }
    DashboardPane #dash-nav #dash-note { width: 1fr; padding-left: 1; }
    DashboardPane #dash-views { height: 1fr; }
    DashboardPane #view-feed, DashboardPane #view-chats { height: 1fr; }
    DashboardPane #feed { height: 1fr; }
    DashboardPane #feed-detail-box { height: 7; border-top: solid $primary-darken-2; }
    DashboardPane #days { layout: horizontal; height: auto; border: none; padding: 0; background: transparent; }
    DashboardPane #days RadioButton { width: auto; margin-right: 2; }
    DashboardPane #chat-stats { height: 1fr; }
    DashboardPane #view-alerts { height: 1fr; }
    DashboardPane #view-alerts > Vertical { width: 3fr; height: 1fr; }
    DashboardPane #view-alerts > #health-box { width: 2fr; margin-left: 1; }
    DashboardPane #alerts, DashboardPane #health { height: 1fr; }
    DashboardPane .section { text-style: bold; height: 1; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("f", "view('feed')", "Feed", show=False),
        Binding("c", "view('chats')", "Chats", show=False),
        Binding("a", "view('alerts')", "Alerts", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.snapshot: Snapshot | None = None
        self.services: dict[str, str] = {}
        self.stat_days = 7
        self._sort: tuple[str, bool] = ("total", True)
        self._chat_names: dict[str, str] = {}   # chat name as drawn -> as read, for sorting
        self._busy = False
        self._reading = False
        self._read_again = False   # asked for a fresh read while one was running
        self._feed: dict[str, Judged] = {}

    # ------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        with Horizontal(id="dash-top"):
            with Vertical(id="dash-bot"):
                yield Static("loading…", id="status", classes="muted")
                with Horizontal(id="svc-row"):
                    with Horizontal(id="svc-buttons"):
                        yield Button("Start", id="svc-start", compact=True, variant="success")
                        yield Button("Stop", id="svc-stop", compact=True, variant="error")
                        yield Button("Restart", id="svc-restart", compact=True, variant="warning")
                    yield Static("", id="svc-name", classes="muted")
                yield Static("", id="svc-result", classes="muted")
            with Vertical(id="dash-activity"):
                yield Static("", id="totals")
                yield Static("", id="chart")
        with Horizontal(id="dash-nav"):
            yield Button("Live feed [f]", id="nav-feed", compact=True, classes="-active")
            yield Button("Chats [c]", id="nav-chats", compact=True)
            yield Button("Alerts & health [a]", id="nav-alerts", compact=True)
            yield Static("", id="dash-note", classes="muted")
        with ContentSwitcher(initial="view-feed", id="dash-views"):
            with Vertical(id="view-feed"):
                yield DataTable(id="feed", cursor_type="row", zebra_stripes=True)
                with VerticalScroll(id="feed-detail-box"):
                    yield Static("Select a message to see why it got its verdict.", id="feed-detail",
                                 classes="muted")
            with Vertical(id="view-chats"):
                with RadioSet(id="days"):
                    yield RadioButton("1 day", id="days-1")
                    yield RadioButton("7 days", id="days-7", value=True)
                    yield RadioButton("30 days", id="days-30")
                yield DataTable(id="chat-stats", cursor_type="row", zebra_stripes=True)
            with Horizontal(id="view-alerts"):
                with Vertical():
                    yield Label("Alerts", classes="section")
                    yield DataTable(id="alerts", cursor_type="row")
                with Vertical(id="health-box"):
                    yield Label("Chat health", classes="section")
                    yield DataTable(id="health", cursor_type="row")

    def on_mount(self) -> None:
        self.query_one("#feed", DataTable).add_columns("time", "chat", "verdict", "role", "reason")
        stats = self.query_one("#chat-stats", DataTable)
        for col in STAT_COLUMNS:
            stats.add_column(col, key=col)
        self.query_one("#alerts", DataTable).add_columns("when", "alert")
        self.query_one("#health", DataTable).add_columns("chat", "status", "detail")
        self.query_one("#dash-activity").border_title = "Activity"
        self.query_one("#dash-activity").border_subtitle = f"judged per day, last {CHART_DAYS} days"
        bot_box = self.query_one("#dash-bot")
        bot_box.border_title = "Bot"
        self.query_one("#svc-name", Static).update(bot.SERVICES[self.mode])

    def on_show(self) -> None:
        # Focus the feed when the tab is shown, so f / c / a and the arrow keys work at once.
        focused = self.screen.focused
        if focused is None or self not in focused.ancestors:
            self.query_one("#feed", DataTable).focus()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        # When another tab is shown while a widget here has focus, Textual hands focus to a
        # sibling of that widget, which is still in this (now hidden) tab, and TabbedContent
        # then switches back to this tab: the number keys would never leave the dashboard.
        # Don't let a focus change in a hidden tab switch tabs; give focus to the tab bar.
        tabs = next((a for a in self.ancestors if isinstance(a, TabbedContent)), None)
        pane = self.parent
        if tabs is not None and pane is not None and pane.id and tabs.active != pane.id:
            event.stop()
            bar = self.screen.query(Tabs)
            self.screen.set_focus(bar.first() if bar else None)

    # ------------------------------------------------------------ state

    @property
    def paths(self):
        return self.app.paths  # type: ignore[attr-defined]

    @property
    def mode(self) -> str:
        """The service this screen controls: the one matching the state file it shows."""
        return "dry-run" if self.paths.dry_run else "real"

    def reload(self) -> None:
        self._request_state(again=False)
        self._load_services()

    def _request_state(self, again: bool = True) -> None:
        """Read the state file, unless a read is already running: a read stuck on
        a locked file must not pile up threads. With `again` (the days or the
        service changed), one more read follows the running one."""
        if not self._reading:
            self._load_state()
        elif again:
            self._read_again = True

    @work(exclusive=True, group="dash-state")
    async def _load_state(self) -> None:
        self._reading = True
        try:
            snap = await asyncio.to_thread(load_snapshot, self.paths.state, self.stat_days)
        finally:
            self._reading = False
        if self.is_mounted:
            self._apply(snap)
            if self._read_again:
                self._read_again = False
                self._load_state()

    @work(exclusive=True, group="dash-services")
    async def _load_services(self) -> None:
        async def one(mode: str) -> str:
            try:
                return await bot.service_state(mode)
            except OSError:
                return "unknown"
        states = await asyncio.gather(*(one(m) for m in bot.SERVICES))
        self.services = dict(zip(bot.SERVICES, states))
        if self.is_mounted:
            self._show_status()

    # ---------------------------------------------------------- render

    def _apply(self, snap: Snapshot) -> None:
        old = self.snapshot
        self.snapshot = snap
        self._show_status()
        self._show_activity(snap)
        if old is None or old.feed != snap.feed:
            self._show_feed(snap)
        if old is None or old.stats != snap.stats:
            self._show_stats(snap)
        if old is None or old.alerts != snap.alerts or old.problems != snap.problems:
            self._show_alerts(snap)

    def _show_status(self) -> None:
        snap = self.snapshot
        out = Text()

        def row(label: str, *parts: tuple[str, str]) -> None:
            out.append(f"{label:<9}", style="bold")
            for text, style in parts:
                out.append(text, style=style)
            out.append("\n")

        def svc(mode: str) -> tuple[str, str]:
            st = self.services.get(mode, "…")
            style = "green" if st == "active" else "red" if st == "failed" else "dim"
            if st == "unknown":
                st = "unknown (no systemctl?)" if all(v == "unknown" for v in self.services.values()) else st
            return st, style

        label = {"real": "real", "dry-run": "dry run"}
        parts: list[tuple[str, str]] = []
        for i, m in enumerate(bot.SERVICES):
            if i:
                parts.append((" · ", "dim"))
            parts.append((f"{label[m]}: ", "bold" if m == self.mode else ""))
            parts.append(svc(m))
        row("Service", *parts)

        if snap is None:
            out.append("reading the state file…", style="dim")
        elif snap.error:
            out.append(f"can't read the state file: {snap.error}", style="red")
        elif not snap.exists:
            out.append(f"No state file yet ({clean(self.paths.state.name)}).\n", style="dim")
            out.append("Start the bot in this mode to see it here.", style="dim")
        else:
            now = snap.now
            mode = snap.mode or self.mode
            if snap.last_alive is None:
                alive = ("never seen", "dim")
            elif now - snap.last_alive < ALIVE_SECONDS:
                alive = (f"alive (seen {fmt_age(now - snap.last_alive)} ago)", "green")
            else:
                alive = (f"not seen for {fmt_age(now - snap.last_alive)}", "red")
            row("Mode", (mode, "bold"), ("  ·  ", "dim"), alive)
            stale = snap.last_alive is None or now - snap.last_alive >= ALIVE_SECONDS
            if stale:
                # the stored connection is whatever the bot last wrote: after a crash it still says "ready"
                conn, conn_style = "not running", "dim"
            else:
                conn = snap.connection or "unknown"
                conn_style = "green" if conn == "ready" else "dim" if conn == "unknown" else "yellow"
            up = []
            if snap.started_at and not stale:
                up = [("  ·  ", "dim"), (f"up {fmt_age(now - snap.started_at)}", "")]
            row("Telegram", (conn, conn_style), *up)
            if snap.pending:
                row("Review", (f"{snap.pending} pending", "bold yellow"), ("  press 2 to review", "dim"))
            else:
                row("Review", ("nothing pending", "dim"))
            if snap.alerts:
                a = snap.alerts[0]
                row("Alerts", (f"{len(snap.alerts)}", ""),
                    (f"  last {fmt_age(now - a.at)} ago, press a", "dim"))
        status = self.query_one("#status", Static)
        status.remove_class("muted")
        status.update(out.rstrip() or out)

    def _show_activity(self, snap: Snapshot) -> None:
        totals = self.query_one("#totals", Static)
        chart = self.query_one("#chart", Static)
        if not snap.exists or snap.error:
            totals.update(Text("No activity to show.", style="dim"))
            chart.update("")
            return
        t = Text()
        t.append(f"{'':<8}{'publish':>9}{'review':>8}{'reject':>8}{'forwarded':>11}\n", style="bold")
        for label, tot in (("today", snap.today), ("7 days", snap.week)):
            t.append(f"{label:<8}")
            t.append(f"{tot.publish:>9}", style="green")
            t.append(f"{tot.review:>8}", style="yellow")
            t.append(f"{tot.reject:>8}", style="dim")
            t.append(f"{tot.forwarded:>11}\n", style="bold")
        totals.update(t.rstrip() or t)
        if any(d.total for d in snap.days):
            chart.update(render_chart(snap.days))
        else:
            chart.update(Text(f"nothing judged in the last {CHART_DAYS} days", style="dim"))

    def _show_feed(self, snap: Snapshot) -> None:
        table = self.query_one("#feed", DataTable)
        keep = None
        if table.row_count and table.cursor_row is not None:
            try:
                keep = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
            except CellDoesNotExist:
                keep = None
        table.clear()
        self._feed = {}
        for j in snap.feed:
            key = f"{j.chat_id}:{j.msg_id}"
            if key in self._feed:
                continue
            self._feed[key] = j
            style = VERDICT_STYLE.get(j.verdict, "")
            table.add_row(fmt_time(j.at, snap.now), Text(visual(j.chat_title), overflow="ellipsis"),
                          Text(j.verdict, style=style), Text(visual(j.role)),
                          Text(visual(first_reason(j.reasons)), style=style), key=key)
        if not snap.feed:
            self._detail(None, "Nothing judged yet." if snap.exists else "No state file yet.")
            return
        if keep in self._feed:
            table.move_cursor(row=table.get_row_index(keep))
        else:
            self._detail(snap.feed[0])

    def _detail(self, j: Judged | None, note: str = "") -> None:
        box = self.query_one("#feed-detail", Static)
        if j is None:
            box.update(Text(note, style="dim"))
            return
        t = Text()
        t.append(f"{j.verdict}", style="bold " + VERDICT_STYLE.get(j.verdict, ""))
        t.append(f"  {visual(j.chat_title)}  ·  {visual(j.action)}", style="dim")
        if j.role:
            t.append(f"  ·  role: {visual(j.role)}")
        for r in [r.strip() for r in j.reasons.split(";") if r.strip()] or ["(no reasons recorded)"]:
            t.append(f"\n  - {visual(r)}")
        if not j.text.strip():
            box.update(t)
            return
        t.append("\n")   # a blank line, then the post
        box.update(Block(j.text.strip(), style="italic", head=t))

    @on(DataTable.RowHighlighted, "#feed")
    @on(DataTable.RowSelected, "#feed")
    def _feed_row(self, event: DataTable.RowHighlighted | DataTable.RowSelected) -> None:
        j = self._feed.get(event.row_key.value)
        if j is not None:
            self._detail(j)

    def _show_stats(self, snap: Snapshot) -> None:
        table = self.query_one("#chat-stats", DataTable)
        table.clear()
        self._chat_names = {}
        for s in snap.stats:
            shown = visual(s.chat or "?")
            self._chat_names[shown] = (s.chat or "?").casefold()   # sorted by the name as read, not as drawn
            table.add_row(Text(shown), s.total, s.publish, s.review, s.reject, s.forwarded)
        self._sort_stats()

    def _sort_stats(self) -> None:
        table = self.query_one("#chat-stats", DataTable)
        if not table.row_count:
            return
        col, reverse = self._sort
        key = (lambda v: self._chat_names.get(str(v), str(v).casefold())) if col == "chat" else None
        table.sort(col, key=key, reverse=reverse)

    @on(DataTable.HeaderSelected, "#chat-stats")
    def _sort_by(self, event: DataTable.HeaderSelected) -> None:
        col = str(event.column_key.value)
        cur, rev = self._sort
        self._sort = (col, not rev) if col == cur else (col, col != "chat")
        self._sort_stats()

    @on(RadioSet.Changed, "#days")
    def _days_changed(self, event: RadioSet.Changed) -> None:
        self.stat_days = int(str(event.pressed.id).split("-")[1])
        self._request_state()

    def _show_alerts(self, snap: Snapshot) -> None:
        alerts = self.query_one("#alerts", DataTable)
        alerts.clear()
        for a in snap.alerts:
            alerts.add_row(fmt_time(a.at, snap.now), Text(visual(a.text)))
        health = self.query_one("#health", DataTable)
        health.clear()
        for p in snap.problems:
            style = "dim" if p.status == "silent" else "red"
            health.add_row(Text(visual(p.title)), Text(visual(p.status), style=style), Text(visual(p.detail)))
        note = self.query_one("#dash-note", Static)
        bits = []
        if snap.problems:
            bits.append(f"{len(snap.problems)} chat(s) need a look")
        note.update(Text("  ".join(bits), style="yellow") if bits else "")

    # ---------------------------------------------------------- views

    def action_view(self, view: str) -> None:
        self.query_one("#dash-views", ContentSwitcher).current = f"view-{view}"
        for b in self.query("#dash-nav Button"):
            b.set_class(b.id == f"nav-{view}", "-active")
        target = {"feed": "#feed", "chats": "#chat-stats", "alerts": "#alerts"}[view]
        self.query_one(target).focus()

    @on(Button.Pressed, "#dash-nav Button")
    def _nav(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_view(str(event.button.id).removeprefix("nav-"))

    # -------------------------------------------------------- service

    @on(Button.Pressed, "#svc-buttons Button")
    def _service_button(self, event: Button.Pressed) -> None:
        event.stop()
        if self._busy:
            return
        action = str(event.button.id).removeprefix("svc-")
        name = bot.SERVICES[self.mode]

        def answered(yes: bool | None) -> None:
            if yes:
                self._run_service(action)
            else:
                self.query_one("#svc-result", Static).update(Text(f"{action} cancelled", style="dim"))

        self.app.push_screen(ConfirmScreen(f"{action.capitalize()} {name} ({self.mode})?"), answered)

    @work(exclusive=True, group="dash-action")
    async def _run_service(self, action: str) -> None:
        result = self.query_one("#svc-result", Static)
        name = bot.SERVICES[self.mode]
        self._busy = True
        for b in self.query("#svc-buttons Button"):
            b.disabled = True
        result.update(Text(f"{action} {name}…", style="dim"))
        try:
            ok, msg = await bot.service_action(self.mode, action)
        except (OSError, ValueError) as e:
            ok, msg = False, str(e)
        finally:
            self._busy = False
            for b in self.query("#svc-buttons Button"):
                b.disabled = False
        if ok:
            result.update(Text(f"{action} {name}: done", style="green"))
        else:
            result.update(Text(f"{action} {name} failed: {msg or 'no reason given'}", style="red"))
        self._load_services()
        self._request_state()
