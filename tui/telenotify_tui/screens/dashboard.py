"""Dashboard: is the bot alive, what is it doing, and does anything need a look (issue #10).

Top: the bot panel (service state, alive, connection, pending reviews, and
start / stop / restart for the service of the mode this screen shows) and
the activity panel: a period picker (today, last 7 / 30 days, this month,
this year, all time, or a custom range of days), the totals for that period
and a chart of it by day, week, month or year. Below, three views: the live
feed of judged messages, per-chat numbers for the same period, and alerts
with chat health.

`reload()` never blocks: the state file is read in a thread and systemctl
runs as a subprocess, both in workers; the widgets are updated when they
finish. A missing state file, table, or systemctl shows a muted note.
"""

from __future__ import annotations

import asyncio
import math
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime
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
    Input,
    Label,
    Select,
    Static,
    TabbedContent,
    Tabs,
)
from textual.widgets.data_table import CellDoesNotExist

from .. import bot
from ..clean import clean
from ..store import Alert, ChatStats, Judged, Store
from . import dashboard_data as dd
from . import search_data

ALIVE_SECONDS = 120
FEED_ROWS = 50
CHART_WIDTH = 46    # the activity panel's width (50) less its border and padding
CHART_HEIGHT = 3
# A period whose numbers took longer than this to count (all time on a big
# state file) is counted again at most every SLOW_REUSE_SECONDS, not on every
# refresh tick; choosing a period always counts it afresh.
SLOW_SECONDS = 0.25
SLOW_REUSE_SECONDS = 60
CUSTOM_SHOWN = "custom-shown"   # the picker entry naming the custom range shown now
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
    try:
        t = datetime.fromtimestamp(at)
    except (OverflowError, ValueError, OSError, TypeError):   # a broken time in the state file
        return "?"
    if t.date() == datetime.fromtimestamp(now).date():
        return t.strftime("%H:%M")
    return t.strftime("%d %b %H:%M")


def first_reason(reasons: str) -> str:
    return search_data.main_reason(reasons)


def bar_label(day: str, bucket: str) -> str:
    """How a chart bar is named under the chart: "18 Nov", "Nov 2026", "2026"."""
    d = date.fromisoformat(day)
    if bucket == "year":
        return f"{d.year}"
    if bucket == "month":
        return f"{d:%b %Y}"
    return dd.fmt_day(d, short=True)


def _merge(bars: list[dd.DayCount], width: int) -> list[dd.DayCount]:
    """Join neighbouring bars until they fit in `width` columns (only a very long custom range needs it)."""
    if len(bars) <= width:
        return bars
    k = math.ceil(len(bars) / width)
    return [dd.DayCount(group[0].day, sum(b.publish for b in group), sum(b.review for b in group),
                        sum(b.reject for b in group))
            for group in (bars[i:i + k] for i in range(0, len(bars), k))]


def render_chart(bars: list[dd.DayCount], bucket: str = "day", width: int = CHART_WIDTH,
                 height: int = CHART_HEIGHT) -> Text:
    """A small stacked bar chart: publish (green) at the bottom, then review (yellow), then reject (dim).

    Bars are two columns wide with a gap while they fit, then one column,
    then without gaps. Up to 15 days each bar is labelled with its day of the
    month; otherwise the first and last bars are named at the two ends."""
    bars = _merge(bars, width)
    n = len(bars)
    bar_w, gap = (2, 1) if n * 3 - 1 <= width else (1, 1) if n * 2 - 1 <= width else (1, 0)
    top = max((d.total for d in bars), default=0)
    out = Text()
    units = height * 8
    eighths = " ▁▂▃▄▅▆▇█"
    scaled = []
    for d in bars:
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
            out.append(eighths[fill] * bar_w, style=style)
            if gap and i < n - 1:
                out.append(" ")
        out.append("\n")
    if not bars:
        return out
    if bucket == "day" and bar_w == 2:
        for i, d in enumerate(bars):
            out.append(d.day[-2:], style="bold" if i == n - 1 else "dim")
            if i < n - 1:
                out.append(" ")
        return out
    span = n * (bar_w + gap) - gap
    first, last = bar_label(bars[0].day, bucket), bar_label(bars[-1].day, bucket)
    out.append(first, style="dim")
    if n > 1:
        out.append(" " * max(1, span - len(first) - len(last)))
        out.append(last, style="dim")
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
    period: dd.PeriodStats | None = None
    period_cost: float = 0.0   # seconds it took to count `period`
    stats: list[ChatStats] = field(default_factory=list)
    feed: list[Judged] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    problems: list[dd.ChatProblem] = field(default_factory=list)
    error: str = ""


def _int(v: str) -> int | None:
    v = v.strip()
    return int(v) if v.isascii() and v.isdigit() else None


def load_snapshot(path: Path, choice: dd.PeriodChoice, reuse: dd.PeriodStats | None = None) -> Snapshot:
    """Read the state file. `reuse`: numbers counted a moment ago for the same
    choice, used again unless the day changed since."""
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
            day = dd.today(now)
            if reuse is not None and (choice.kind == "custom" or reuse.period.last == day):
                snap.period = reuse
            else:
                started = time.perf_counter()
                first, known = dd.first_judged(s) if choice.kind == "all" else (None, True)
                snap.period = dd.period_stats(s, dd.resolve(choice, day, first, known), CHART_WIDTH)
                snap.period_cost = time.perf_counter() - started
            snap.stats = snap.period.chats
            snap.feed = s.recent(FEED_ROWS)
            snap.alerts = s.alerts(100)
            snap.problems = dd.chat_problems(s, now)
    except (sqlite3.Error, OSError, ValueError, OverflowError) as e:  # a locked or half-written file: say so, try again next tick
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


class RangeScreen(ModalScreen["tuple[date, date] | None"]):
    """Ask for a custom period: from / to as YYYY-MM-DD, both days included.
    Enter in "from" moves to "to"; Enter in "to" (or Show) checks and closes;
    Escape or Cancel closes without a change."""

    DEFAULT_CSS = """
    RangeScreen { align: center middle; }
    RangeScreen > Vertical {
        width: 64; height: auto; padding: 1 2;
        border: thick $primary; background: $surface;
    }
    RangeScreen Label { width: 100%; }
    RangeScreen #range-fields { height: auto; margin-top: 1; }
    RangeScreen #range-fields Label { width: auto; padding: 1 1 0 0; }
    RangeScreen Input { width: 18; margin-right: 1; }
    RangeScreen #range-error { color: $error; height: auto; margin-top: 1; }
    RangeScreen #range-buttons { height: auto; align-horizontal: right; margin-top: 1; }
    RangeScreen Button { margin-left: 2; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, first: date, last: date):
        super().__init__()
        self.first, self.last = first, last

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Show the numbers for these days (both included, local time):")
            with Horizontal(id="range-fields"):
                yield Label("From")
                yield Input(self.first.isoformat(), placeholder="YYYY-MM-DD", id="range-from", max_length=10)
                yield Label("to")
                yield Input(self.last.isoformat(), placeholder="YYYY-MM-DD", id="range-to", max_length=10)
            yield Static("", id="range-error")
            with Horizontal(id="range-buttons"):
                yield Button("Show", id="range-ok", variant="primary")
                yield Button("Cancel", id="range-cancel")

    def on_mount(self) -> None:
        self.query_one("#range-from", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _apply(self) -> None:
        try:
            got = dd.parse_range(self.query_one("#range-from", Input).value,
                                 self.query_one("#range-to", Input).value)
        except ValueError as e:
            self.query_one("#range-error", Static).update(Text(str(e)))
            return
        self.dismiss(got)

    @on(Input.Submitted)
    def _submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id == "range-from":
            self.query_one("#range-to", Input).focus()
        else:
            self._apply()

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "range-ok":
            self._apply()
        else:
            self.dismiss(None)


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
    DashboardPane #period-row { height: 1; }
    DashboardPane #period { width: 22; }
    DashboardPane #period-range { margin-left: 1; min-width: 0; }
    DashboardPane #period-name { width: 1fr; padding-left: 1; }
    DashboardPane #chats-period { height: 1; }
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
        Binding("p", "pick_period", "Period"),
        Binding("d", "custom_range", "Dates"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.snapshot: Snapshot | None = None
        self.services: dict[str, str] = {}
        self.choice = dd.PeriodChoice("7d")
        # the last numbers counted: (choice, when (monotonic), numbers, seconds it took)
        self._counted: tuple[dd.PeriodChoice, float, dd.PeriodStats, float] | None = None
        self._sort: tuple[str, bool] = ("total", True)
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
                with Horizontal(id="period-row"):
                    yield Select([(name, kind) for kind, name in dd.PERIODS], value=self.choice.kind,
                                 allow_blank=False, compact=True, id="period")
                    yield Button("Dates…", id="period-range", compact=True)
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
                yield Static("", id="chats-period", classes="muted")
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
        a locked file must not pile up threads. With `again` (the period or the
        service changed), one more read follows the running one."""
        if not self._reading:
            self._load_state()
        elif again:
            self._read_again = True

    @work(exclusive=True, group="dash-state")
    async def _load_state(self) -> None:
        self._reading = True
        try:
            choice = self.choice
            reuse = None
            if self._counted is not None:
                was, at, numbers, cost = self._counted
                if was == choice and cost > SLOW_SECONDS and time.monotonic() - at < SLOW_REUSE_SECONDS:
                    reuse = numbers
            snap = await asyncio.to_thread(load_snapshot, self.paths.state, choice, reuse)
            if snap.period is not None and snap.period is not reuse:
                self._counted = (choice, time.monotonic(), snap.period, snap.period_cost)
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
        box = self.query_one("#dash-activity")
        note = self.query_one("#chats-period", Static)
        ps = snap.period
        if not snap.exists or snap.error or ps is None:
            totals.update(Text("No activity to show.", style="dim"))
            chart.update("")
            box.border_subtitle = ""
            note.update("")
            return
        by = {"day": "by day", "week": "by week", "month": "by month", "year": "by year"}[ps.bucket]
        box.border_subtitle = f"{ps.period.describe()} · {by}"
        whole = "   whole days, today included" if ps.period.kind in ("7d", "30d") else ""
        note.update(Text(f"{ps.period.describe()}{whole}   (p: change the period, d: pick dates)", style="dim"))
        tot = ps.totals
        t = Text()
        t.append(f"{'judged':>8}{'publish':>9}{'review':>8}{'reject':>8}{'forwarded':>11}\n", style="bold")
        t.append(f"{tot.judged:>8}")
        t.append(f"{tot.publish:>9}", style="green")
        t.append(f"{tot.review:>8}", style="yellow")
        t.append(f"{tot.reject:>8}", style="dim")
        t.append(f"{tot.forwarded:>11}", style="bold")
        totals.update(t)
        if tot.judged:
            chart.update(render_chart(ps.bars, ps.bucket))
        else:
            chart.update(Text("nothing judged in this period", style="dim"))

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
            table.add_row(fmt_time(j.at, snap.now), Text(j.chat_title, overflow="ellipsis"),
                          Text(j.verdict, style=style), Text(j.role), Text(first_reason(j.reasons), style=style),
                          key=key)
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
        t.append(f"  {j.chat_title}  ·  {j.action}", style="dim")
        if j.role:
            t.append(f"  ·  role: {j.role}")
        t.append("\n")
        for r in [r.strip() for r in j.reasons.split(";") if r.strip()] or ["(no reasons recorded)"]:
            t.append(f"  - {r}\n")
        if j.text:
            t.append("\n" + j.text.strip(), style="italic")
        box.update(t)

    @on(DataTable.RowHighlighted, "#feed")
    @on(DataTable.RowSelected, "#feed")
    def _feed_row(self, event: DataTable.RowHighlighted | DataTable.RowSelected) -> None:
        j = self._feed.get(event.row_key.value)
        if j is not None:
            self._detail(j)

    def _show_stats(self, snap: Snapshot) -> None:
        table = self.query_one("#chat-stats", DataTable)
        table.clear()
        for s in snap.stats:
            table.add_row(Text(s.chat or "?"), s.total, s.publish, s.review, s.reject, s.forwarded)
        self._sort_stats()

    def _sort_stats(self) -> None:
        table = self.query_one("#chat-stats", DataTable)
        if not table.row_count:
            return
        col, reverse = self._sort
        key = (lambda v: str(v).casefold()) if col == "chat" else None
        table.sort(col, key=key, reverse=reverse)

    @on(DataTable.HeaderSelected, "#chat-stats")
    def _sort_by(self, event: DataTable.HeaderSelected) -> None:
        col = str(event.column_key.value)
        cur, rev = self._sort
        self._sort = (col, not rev) if col == cur else (col, col != "chat")
        self._sort_stats()

    # --------------------------------------------------------- period

    def _set_choice(self, choice: dd.PeriodChoice) -> None:
        """Show `choice` and count it. A custom range gets its own entry in the
        picker (CUSTOM_SHOWN), so picking "Custom range…" again is a change
        that opens the dates form."""
        self.choice = choice
        sel = self.query_one("#period", Select)
        options = [(name, kind) for kind, name in dd.PERIODS]
        value = choice.kind
        if choice.kind == "custom" and choice.first and choice.last:
            p = dd.Period("custom", choice.first, choice.last)
            options.insert(-1, (p.describe(), CUSTOM_SHOWN))
            value = CUSTOM_SHOWN
        with sel.prevent(Select.Changed):
            sel.set_options(options)
            sel.value = value
        self._request_state()

    @on(Select.Changed, "#period")
    def _period_changed(self, event: Select.Changed) -> None:
        event.stop()
        kind = str(event.value)
        if kind == CUSTOM_SHOWN or (kind == self.choice.kind and kind != "custom"):
            return
        if kind == "custom":
            self.action_custom_range()
        else:
            self._set_choice(dd.PeriodChoice(kind))

    @on(Button.Pressed, "#period-range")
    def _range_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_custom_range()

    def action_pick_period(self) -> None:
        sel = self.query_one("#period", Select)
        sel.focus()
        sel.expanded = True

    def action_custom_range(self) -> None:
        """Ask for the days; the form starts with the period shown now."""
        ps = self.snapshot.period if self.snapshot is not None else None
        if ps is not None:
            first, last = ps.period.first, ps.period.last
        else:
            first = last = dd.today()

        def answered(days: tuple[date, date] | None) -> None:
            if days is None:   # cancelled: the picker goes back to what is shown
                self._set_choice(self.choice)
            else:
                self._set_choice(dd.PeriodChoice("custom", *days))

        self.app.push_screen(RangeScreen(first, last), answered)

    def _show_alerts(self, snap: Snapshot) -> None:
        alerts = self.query_one("#alerts", DataTable)
        alerts.clear()
        for a in snap.alerts:
            alerts.add_row(fmt_time(a.at, snap.now), Text(a.text))
        health = self.query_one("#health", DataTable)
        health.clear()
        for p in snap.problems:
            style = "dim" if p.status == "silent" else "red"
            health.add_row(Text(p.title), Text(p.status, style=style), Text(p.detail))
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
