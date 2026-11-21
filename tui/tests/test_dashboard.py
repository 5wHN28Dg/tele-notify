"""Dashboard screen, against the conftest state file. systemctl is always mocked."""

from __future__ import annotations

import sqlite3

import pytest
from textual.geometry import Region
from textual.widgets import DataTable, Select, Static, TabbedContent

from telenotify_tui import bot
from telenotify_tui.app import TeleNotifyApp
from telenotify_tui.paths import Paths
from telenotify_tui.postview import PostView
from telenotify_tui.screens import dashboard_data as dd
from telenotify_tui.screens.dashboard import ConfirmScreen, DashboardPane
from telenotify_tui.store import Store


@pytest.fixture(autouse=True)
def fake_systemctl(monkeypatch):
    """Record service calls instead of running systemctl."""
    calls: list[tuple[str, ...]] = []

    async def state(mode: str) -> str:
        calls.append(("state", mode))
        return "active" if mode == "dry-run" else "inactive"

    async def action(mode: str, what: str) -> tuple[bool, str]:
        calls.append(("action", mode, what))
        return True, ""

    monkeypatch.setattr(bot, "service_state", state)
    monkeypatch.setattr(bot, "service_action", action)
    return calls


async def settle(app, pilot) -> None:
    for _ in range(3):
        await pilot.pause()
        await app.workers.wait_for_complete()
    await pilot.pause()


def text(app, selector: str) -> str:
    """What the widget shows, in reading order (a post body is drawn right to
    left where it should be; test_rtl.py checks how it is drawn)."""
    w = app.query_one(selector, Static)
    return str(w.content) if isinstance(w, PostView) else str(w.render())


def rows(table: DataTable) -> list[list[str]]:
    return [[str(c) for c in table.get_row_at(i)] for i in range(table.row_count)]


async def test_status_shows_alive_pending_and_services(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        status = text(app, "#status")
        assert "alive" in status and "not seen" not in status
        assert "1 pending" in status and "press 2" in status
        assert "ready" in status and "up 1h" in status
        assert "dry run: active" in status and "real: inactive" in status


async def test_not_seen_when_last_alive_is_old(paths, now):
    db = sqlite3.connect(paths.state)
    db.execute("UPDATE meta SET value = ? WHERE key = 'last_alive'", (str(now - 3 * 3600),))
    db.commit()
    db.close()
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        assert "not seen for 3h" in text(app, "#status")


@pytest.mark.parametrize("age", [121, 3 * 3600])
async def test_stale_connection_after_a_crash_is_not_ready(paths, now, age):
    """The bot wrote connection=ready, then died: the dashboard mustn't keep saying ready."""
    db = sqlite3.connect(paths.state)
    db.execute("UPDATE meta SET value = ? WHERE key = 'last_alive'", (str(now - age),))
    db.commit()
    db.close()
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        status = text(app, "#status")
        telegram = next(line for line in status.splitlines() if line.startswith("Telegram"))
        assert "not running" in telegram and "ready" not in telegram and "up " not in telegram



# window size -> the line's form, and whether the Service row fits with it (the owner's choice B, #100:
# it gives way only where the box is too short, as at 80 x 24)
LOGIN_LAYOUTS = {
    # (size, real service, dry-run service): (the line starts, the Service row shows; None: either,
    # as long as Mode and the pending count stay in the box). Under 100 window columns the Bot box has
    # the whole width (#57); from 100 it is beside the Activity box, 64 cells at 120 columns.
    ((80, 24), "inactive", "active"): ("Not logged in to Telegram.", True),
    ((60, 24), "activating", "inactive"): ("Not logged in:", None),
    ((99, 30), "activating", "inactive"): ("Not logged in to Telegram.", True),
    ((100, 30), "activating", "inactive"): ("Not logged in:", False),   # a 2-row Service row: 7 rows, one too many
    ((100, 30), "activating", "active"): ("Not logged in:", True),      # a 1-row Service row: 6 rows, it fits
    ((120, 40), "inactive", "active"): ("Not logged in:", True),
    ((120, 40), "unknown", "unknown"): ("Not logged in:", True),     # a 2-row Service row still fits
    ((160, 40), "inactive", "active"): ("Not logged in to Telegram.", True),
}


@pytest.mark.parametrize("layout", list(LOGIN_LAYOUTS))
@pytest.mark.parametrize("age", [30, 3 * 3600])
async def test_a_lost_login_says_what_to_run(paths, now, age, layout, monkeypatch):
    """#65: Telegram logged the account out. The bot can't message you any more, and
    systemd keeps restarting it, so the Bot box says so and what to run, alive or not."""
    db = sqlite3.connect(paths.state)
    db.execute("UPDATE meta SET value = ? WHERE key = 'last_alive'", (str(now - age),))
    db.execute("UPDATE meta SET value = 'logged out' WHERE key = 'connection'")
    db.execute("INSERT INTO meta VALUES ('login_needed', '1')")
    db.commit()
    db.close()
    size, real, dry = layout
    lead, service = LOGIN_LAYOUTS[layout]

    async def state(mode: str) -> str:
        return real if mode == "real" else dry

    monkeypatch.setattr(bot, "service_state", state)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=size) as pilot:
        await settle(app, pilot)
        words = " ".join(text(app, "#status").split())
        assert "not running" not in words and "Telegram" not in words.split("Start.", 1)[1]
        # first, so a box too short for the rest (80 x 24) still shows it: read the rows drawn in the box
        box = app.query_one("#dash-bot")
        status = app.query_one("#status", Static)
        shown = status.region.intersection(box.content_region)
        drawn = [strip.text.strip() for strip in status.render_lines(Region(0, 0, shown.width, shown.height))]
        first = " ".join(" ".join(drawn).split())
        # "not logged in": right both for a log-out and for a first start before `telenotify login`
        assert first.startswith(f"{lead} Stop, run telenotify login, then Start.")
        rest = first.split("Start.", 1)[1]
        if service is not None:
            assert ("Service" in rest) == service
        assert "Mode" in rest and "1 pending" in rest   # never pushed out of the box
        if size[1] >= 24 and size[0] >= 80:   # "Stop ... Start": the buttons are in view
            restart = app.query_one("#svc-restart").region
            assert box.content_region.contains_region(restart)



async def test_the_login_line_follows_a_resize(paths):
    """Its short and long forms are chosen again when the window changes size, not at the next refresh."""
    db = sqlite3.connect(paths.state)
    db.execute("INSERT INTO meta VALUES ('login_needed', '1')")
    db.commit()
    db.close()
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        assert " ".join(text(app, "#status").split()).startswith("Not logged in to Telegram. Stop")
        await pilot.resize_terminal(120, 30)   # beside the Activity box now: 64 cells
        await settle(app, pilot)
        assert " ".join(text(app, "#status").split()).startswith("Not logged in: Stop")
        await pilot.resize_terminal(160, 30)
        await settle(app, pilot)
        assert " ".join(text(app, "#status").split()).startswith("Not logged in to Telegram. Stop")

async def test_a_new_login_clears_the_logged_out_line(paths, now):
    """The bot clears login_needed after a login: back to the usual connection line."""
    db = sqlite3.connect(paths.state)
    db.execute("INSERT INTO meta VALUES ('login_needed', '')")
    db.commit()
    db.close()
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(app, pilot)
        status = " ".join(text(app, "#status").split())
        assert "Not logged in" not in status and "telenotify login" not in status
        assert "Telegram ready" in status

async def test_totals(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        lines = text(app, "#totals").splitlines()
        # judged, publish, review, reject, forwarded over the last 7 days (the default)
        # the owner's pick in Kitty (#95): ▓ and ▒ were too alike on a high-DPI screen
        assert lines[0].split() == ["judged", "█publish", "▚review", "▌reject", "forwarded"]   # marks name the chart's fills
        assert lines[1].split() == ["4", "1", "1", "2", "1"]
        # the numbers in the text colour: the marks name the series, and a fixed green or yellow
        # was below 4.5:1 in the light theme and clashed with the chart's theme colours (#118)
        totals = app.query_one("#totals", Static).content
        numbers = len(totals.plain.splitlines()[0]) + 1
        assert not [sp for sp in totals.spans if sp.start >= numbers and str(sp.style) not in ("", "bold")]
        assert "Last 7 days" in str(app.query_one("#dash-activity").border_subtitle)


async def test_per_chat_table_follows_the_period(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("c")
        await settle(app, pilot)
        table = app.query_one("#chat-stats", DataTable)
        got = {r[0]: r[1:] for r in rows(table)}
        # chat: total, publish, review, reject, forwarded (7 days by default)
        assert got == {"Basra Jobs": ["2", "1", "1", "0", "1"], "Iraq Jobs": ["2", "0", "0", "2", "0"]}
        assert "Last 7 days" in text(app, "#chats-period")

        app.query_one("#period", Select).value = "today"
        await settle(app, pilot)
        got = {r[0]: r[1:] for r in rows(table)}
        assert got["Iraq Jobs"] == ["1", "0", "0", "1", "0"]  # the 3-day-old reject drops out
        assert "Today" in text(app, "#chats-period")


async def test_slow_reads_never_pile_up(paths, monkeypatch, fake_systemctl):
    """A state file the bot keeps locked: day changes, refreshes and service
    actions while a read is stuck wait for it instead of starting more threads."""
    import threading

    from telenotify_tui.screens import dashboard as dash

    real = dash.load_snapshot
    gate = threading.Event()
    guard = threading.Lock()
    running: list[int] = []
    peak = [0]
    days_read: list[str] = []

    def slow(path, choice, reuse=None):
        with guard:
            running.append(choice.kind)
            peak[0] = max(peak[0], len(running))
        gate.wait(10)
        with guard:
            running.remove(choice.kind)
        days_read.append(choice.kind)
        return real(path, choice, reuse)

    monkeypatch.setattr(dash, "load_snapshot", slow)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            for _ in range(50):
                await pilot.pause(0.02)
                if running:
                    break
            assert running, "the first read never started"
            pane = app.query_one(DashboardPane)
            await pilot.press("c")
            app.query_one("#period", Select).value = "today"
            await pilot.pause()
            app.query_one("#period", Select).value = "30d"
            await pilot.pause()
            pane.reload()
            pane._run_service("restart")
            await pilot.pause(0.2)
            assert peak[0] == 1
            gate.set()
            await settle(app, pilot)
            await settle(app, pilot)
            assert peak[0] == 1
            assert days_read[-1] == "30d" and pane.snapshot is not None and pane.choice.kind == "30d"
            assert len(days_read) <= 3
    finally:
        gate.set()


async def test_per_chat_sort_by_header(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("c")
        await settle(app, pilot)
        table = app.query_one("#chat-stats", DataTable)
        pane = app.query_one(DashboardPane)
        reject = table.columns["reject"]
        pane.post_message(DataTable.HeaderSelected(table, reject.key, 3, reject.label))
        await settle(app, pilot)
        assert [r[0] for r in rows(table)] == ["Iraq Jobs", "Basra Jobs"]  # most rejects first
        pane.post_message(DataTable.HeaderSelected(table, reject.key, 3, reject.label))
        await settle(app, pilot)
        assert [r[0] for r in rows(table)] == ["Basra Jobs", "Iraq Jobs"]  # clicked again: reversed


async def test_feed_newest_first_and_selection_shows_reasons(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        feed = app.query_one("#feed", DataTable)
        assert [r[2] for r in rows(feed)] == ["reject", "review", "publish", "reject"]
        feed.focus()
        await pilot.press("down", "down")  # the publish row
        await settle(app, pilot)
        detail = text(app, "#feed-detail")
        assert "publish" in detail
        assert "location: basra" in detail and "entry level" in detail
        assert "مطلوب مهندس كهرباء" in detail


async def test_locked_state_file_arrow_keys_dont_crash_and_it_recovers(paths, state, monkeypatch):
    """Issue #54: another program holds an exclusive lock on the state file. The
    read fails, the feed empties, and the arrow keys on it used to crash the
    screen (a highlight with no row). Now the feed says the file is busy, the
    keys do nothing, and the next refresh after the lock is gone shows it all again."""
    from telenotify_tui.screens import dashboard as dash

    monkeypatch.setattr(dash, "Store", lambda path: Store(path, timeout=0.1))
    app = TeleNotifyApp(paths, refresh_seconds=0.3)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        feed = app.query_one("#feed", DataTable)
        assert feed.row_count == 4
        feed.focus()
        db = sqlite3.connect(state, isolation_level=None)
        db.execute("BEGIN EXCLUSIVE")
        try:
            for _ in range(40):
                await pilot.pause(0.05)
                if app.query_one(DashboardPane).snapshot.error:
                    break
            await settle(app, pilot)
            assert app.query_one(DashboardPane).snapshot.error
            await pilot.press("down", "up", "down", "enter")
            await settle(app, pilot)
            assert app.is_running and app._exception is None
            busy = "The state file is busy (another program has it locked); retrying"
            assert busy in text(app, "#status")                  # said once, in the Bot box
            assert busy not in text(app, "#feed-detail")         # the other boxes point to it
            assert "see Bot" in text(app, "#feed-detail")
            status = app.query_one("#status", Static).render()
            spans = [str(sp.style) for sp in status.spans
                     if status.plain[sp.start:sp.end].startswith("The state file is busy")]
            assert spans == ["bold"]                             # no literal colour: theme text, both themes
            assert "Nothing judged yet" not in text(app, "#feed-detail")
        finally:
            db.execute("ROLLBACK")
            db.close()
        for _ in range(40):                      # lock gone: the next refresh brings it back
            await pilot.pause(0.05)
            if not app.query_one(DashboardPane).snapshot.error:
                break
        await settle(app, pilot)
        assert feed.row_count == 4
        assert "busy" not in text(app, "#status") and "alive" in text(app, "#status")
        await pilot.press("down")
        await settle(app, pilot)
        assert "review" in text(app, "#feed-detail")


async def test_banned_chat_in_health_and_alerts(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("a")
        await settle(app, pilot)
        health = rows(app.query_one("#health", DataTable))
        assert health[0][:2] == ["Blocked Channel", "banned"]
        assert not any(r[0] == "Family" for r in health)  # not watched
        alerts = rows(app.query_one("#alerts", DataTable))
        assert "no connection to Telegram" in alerts[0][1]


async def test_confirm_cancel_does_nothing(paths, fake_systemctl):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.click("#svc-restart")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        assert "tele-notify-dryrun" in str(app.screen.query_one("#confirm-question").render())
        await pilot.click("#confirm-no")
        await settle(app, pilot)
        assert not isinstance(app.screen, ConfirmScreen)
        assert not [c for c in fake_systemctl if c[0] == "action"]
        assert "cancelled" in text(app, "#svc-result")

        await pilot.click("#svc-stop")
        await pilot.pause()
        await pilot.press("escape")
        await settle(app, pilot)
        assert not [c for c in fake_systemctl if c[0] == "action"]


async def test_confirm_yes_acts_on_this_mode_only(paths, fake_systemctl):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.click("#svc-restart")
        await pilot.pause()
        await pilot.click("#confirm-yes")
        await settle(app, pilot)
        assert [c for c in fake_systemctl if c[0] == "action"] == [("action", "dry-run", "restart")]
        assert "done" in text(app, "#svc-result")


async def test_failed_action_is_shown(paths, monkeypatch):
    async def fail(mode, what):
        return False, "Unit tele-notify-dryrun.service not found."
    monkeypatch.setattr(bot, "service_action", fail)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.click("#svc-start")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)
        assert "failed" in text(app, "#svc-result") and "not found" in text(app, "#svc-result")


async def test_missing_state_and_systemctl(tmp_path, monkeypatch):
    async def unknown(mode):
        return "unknown"
    monkeypatch.setattr(bot, "service_state", unknown)
    paths = Paths(config=tmp_path / "config.json", data_dir=tmp_path, state=tmp_path / "nope.sqlite3",
                  binary=None, dry_run=False)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        status = text(app, "#status")
        assert "No state file yet" in status and "no systemctl" in status
        assert "No activity" in text(app, "#totals")
        assert app.query_one("#feed", DataTable).row_count == 0
        for key in ("c", "a", "f"):
            await pilot.press(key)
            await settle(app, pilot)


async def test_empty_tables(paths):
    db = sqlite3.connect(paths.state)
    for t in ("processed", "reviews", "meta", "alerts", "chats"):
        db.execute(f"DROP TABLE {t}")
    db.commit()
    db.close()
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        assert "never seen" in text(app, "#status")
        assert "nothing judged" in text(app, "#chart")


def test_data_helpers(paths, now):
    with Store(paths.state) as s:
        ps = dd.period_stats(s, dd.resolve(dd.PeriodChoice("7d"), dd.today(now)), 46)
        assert ps.bucket == "day" and len(ps.bars) == 7 and ps.bars[-1].total == 3
        assert sum(d.total for d in ps.bars) == 4
        week = ps.totals
        assert (week.publish, week.review, week.reject, week.forwarded) == (1, 1, 2, 1)
        # Iraq Jobs goes silent once its newest post is older than the window
        problems = dd.chat_problems(s, now + 8 * 86400)
        assert [(p.title, p.status) for p in problems] == [
            ("Blocked Channel", "banned"), ("Basra Jobs", "silent"), ("Iraq Jobs", "silent")]
        assert [(p.title, p.status) for p in dd.chat_problems(s, now)] == [("Blocked Channel", "banned")]


async def test_number_keys_leave_dashboard_with_focus_inside(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        tabs = app.query_one(TabbedContent)
        app.query_one("#svc-stop").focus()
        await pilot.pause()
        await pilot.press("2")
        await settle(app, pilot)
        assert tabs.active == "review"
        await pilot.press("1")
        await settle(app, pilot)
        assert tabs.active == "dashboard"
        assert app.focused is app.query_one("#feed")  # ready for f / c / a and the arrows
        await pilot.press("4")
        await settle(app, pilot)
        assert tabs.active == "settings"


def test_an_unreadable_file_doesnt_promise_a_retry():
    from telenotify_tui.screens import dashboard as dash
    snap = dash.Snapshot(exists=True)
    snap.error, snap.busy = "DatabaseError: file is not a database", False
    note = dash.problem_note(snap)
    assert "retry" not in note and "see Bot" in note


def _contrast(a, b) -> float:
    def lum(c):
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(c.r) + 0.7152 * ch(c.g) + 0.0722 * ch(c.b)
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


async def test_chart_marks_reach_3_to_1_in_both_themes(paths):
    """#102, #118: the chart's marks are drawn in the theme's colours, at least 3:1 on its background
    in both themes and redrawn on a theme switch. VTE draws a shade (░ ▒ ▓) as one flat tone, so every
    mark has a shape of its own; and where one-column bars touch (24+ bars) the series are told apart
    by colour too, 3:1 between each pair (reject a darker grey in a dark theme)."""
    from textual.color import Color

    from telenotify_tui.screens.dashboard import SERIES_MARK

    assert not set(SERIES_MARK.values()) & set("░▒▓")
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        for theme in ("textual-dark", "textual-light"):
            app.theme = theme
            await settle(app, pilot)
            bg = Color.parse(app.theme_variables["background"])
            chart = app.query_one("#chart", Static).content
            seen = {}
            for span in chart.spans:
                glyph = chart.plain[span.start]
                if glyph in SERIES_MARK.values():
                    seen[glyph] = Color.parse(str(span.style))
            assert set(seen) == set(SERIES_MARK.values()), (theme, seen)   # today has all three
            for glyph, colour in seen.items():
                assert _contrast(colour, bg) >= 3, (theme, glyph, colour.hex, round(_contrast(colour, bg), 2))
            publish, review, reject = (seen[SERIES_MARK[k]] for k in ("publish", "review", "reject"))
            for a, b in ((publish, reject), (review, reject)):
                assert _contrast(a, b) >= 3, (theme, a.hex, b.hex, round(_contrast(a, b), 2))

def test_chart_colours_never_carry_alpha():
    """A theme colour with alpha would blend into an 8-digit hex Rich can't draw (#118 run 3)."""
    from telenotify_tui.screens.dashboard import series_colours
    c = series_colours({"background": "#12121280", "foreground": "#E0E0E0EA",
                        "text-success": "#8AD4A1", "text-warning": "#FFC473"})
    assert len(c["reject"]) == 7, c


async def test_a_narrow_window_shows_one_top_box_at_a_time(paths):
    """#57, the owner's choice: under 100 columns the Bot and Activity boxes don't fit side by side,
    so one has the whole width (Bot first, v flips) and every Bot row and button is in view; under 30
    rows the post's details shrink, so the live feed keeps rows. From 100 columns up, both, as before."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        bot_box, activity = app.query_one("#dash-bot"), app.query_one("#dash-activity")
        assert bot_box.display and not activity.display
        for button in ("#svc-start", "#svc-stop", "#svc-restart"):
            assert bot_box.content_region.contains_region(app.query_one(button).region), button
        status = " ".join(text(app, "#status").split())
        assert "Alerts" in status
        status_widget = app.query_one("#status", Static)
        assert status_widget.region.height <= bot_box.content_region.height - 1   # nothing cut
        assert app.query_one("#feed", DataTable).region.height >= 4               # header and 3 rows
        await pilot.press("v")
        await settle(app, pilot)
        assert activity.display and not bot_box.display
        await pilot.press("v")
        await settle(app, pilot)
        assert bot_box.display and not activity.display
        await pilot.resize_terminal(120, 40)
        await settle(app, pilot)
        assert bot_box.display and activity.display
        await pilot.press("v")                    # nothing to flip: both are shown
        await settle(app, pilot)
        assert bot_box.display and activity.display


def shown(app, widget) -> bool:
    """On screen: displayed, inside every displayed ancestor, with some area."""
    return all(w.display for w in widget.ancestors_with_self
               if hasattr(w, "display")) and widget.region.area > 0


async def test_p_in_a_narrow_window_shows_the_activity_box_first(paths):
    """Review run 1, finding 1: with the Bot box shown, p opened a picker in the hidden Activity box."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        activity, sel = app.query_one("#dash-activity"), app.query_one("#period", Select)
        assert not activity.display
        await pilot.press("p")
        await settle(app, pilot)
        assert activity.display and sel.expanded and app.focused is not None and shown(app, app.focused)
        await pilot.press("down", "enter")
        await settle(app, pilot)
        assert app.query_one(DashboardPane).choice.kind != "7d"
        assert activity.display and not app.query_one("#dash-bot").display   # the result stays in view
        assert app.focused is not None and shown(app, app.focused)


@pytest.mark.parametrize("answer", ["cancel", "submit"])
async def test_d_in_a_narrow_window_shows_the_activity_box(paths, now, answer):
    """Review run 1, findings 1 and 6: the dates form's result is in the Activity box, so it is shown
    before the form opens and stays shown after it closes."""
    from telenotify_tui.screens.dashboard import RangeScreen
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        activity = app.query_one("#dash-activity")
        await pilot.press("d")
        await settle(app, pilot)
        assert isinstance(app.screen, RangeScreen)
        assert activity.display and not app.query_one("#dash-bot").display
        if answer == "cancel":
            await pilot.press("escape")
        else:
            day = dd.today(now).isoformat()
            app.screen.query_one("#range-from").value = day
            app.screen.query_one("#range-to").value = day
            await pilot.click("#range-ok")
        await settle(app, pilot)
        assert not isinstance(app.screen, RangeScreen)
        assert activity.display and not app.query_one("#dash-bot").display
        if answer == "submit":
            assert app.query_one(DashboardPane).choice.kind == "custom"
        assert app.focused is not None and shown(app, app.focused)


@pytest.mark.parametrize("start", ["#svc-stop", "#period-range"])
async def test_a_flip_never_leaves_focus_in_the_hidden_box(paths, fake_systemctl, start):
    """Review run 1, finding 2: v hid the focused widget and Enter still pressed it."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        if start == "#period-range":
            await pilot.press("v")
            await settle(app, pilot)
        app.query_one(start).focus()
        await settle(app, pilot)
        assert app.focused is app.query_one(start)
        await pilot.press("v")
        await settle(app, pilot)
        focused = app.focused
        assert focused is not None and focused is not app.query_one(start) and shown(app, focused)
        box = "#dash-activity" if start == "#svc-stop" else "#dash-bot"
        assert app.query_one(box) in focused.ancestors   # the box just shown
        if start == "#svc-stop":
            await pilot.press("enter")
            await settle(app, pilot)
            assert not isinstance(app.screen, ConfirmScreen)          # the hidden Stop wasn't pressed
        assert not [c for c in fake_systemctl if c[0] == "action"]


async def test_a_resize_never_leaves_focus_in_the_hidden_box(paths):
    """Review run 1, finding 2: Dates… focused in a wide window, then the window narrows."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        app.query_one("#period-range").focus()
        await settle(app, pilot)
        await pilot.resize_terminal(80, 24)
        await settle(app, pilot)
        assert not app.query_one("#dash-activity").display
        assert app.focused is not None and shown(app, app.focused)


@pytest.mark.parametrize(("size", "narrow", "short"), [
    ((100, 30), False, False), ((99, 30), True, False), ((120, 29), False, True)])
async def test_the_thresholds_are_the_window_size(paths, size, narrow, short):
    """Review run 1, finding 3: 100 columns and 30 rows of the window, as documented, not of the pane."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=size) as pilot:
        await settle(app, pilot)
        pane = app.query_one(DashboardPane)
        assert pane.has_class("-narrow") == narrow and pane.has_class("-short") == short
        assert app.query_one("#dash-activity").display != narrow


async def test_v_does_nothing_in_a_wide_window(paths):
    """Review run 1: both boxes fit, so v isn't offered and pressing it changes nothing."""
    from textual.widgets._footer import FooterKey
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        pane = app.query_one(DashboardPane)
        assert not [k for k in app.query(FooterKey) if k.key == "v"]
        assert pane.check_action("flip_top", ()) is False
        await pilot.press("v")
        await settle(app, pilot)
        assert not pane.has_class("-show-activity")
        assert app.query_one("#dash-bot").display and app.query_one("#dash-activity").display


async def test_the_flipped_activity_box_is_full_width(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        await pilot.press("v")
        await settle(app, pilot)
        top = app.query_one("#dash-top")
        assert app.query_one("#dash-activity").outer_size.width == top.content_region.width


async def test_the_footer_at_80_columns(paths):
    """Review run 1, finding 5: a short label for v, so the tab keys still fit at 80 columns."""
    from textual.widgets._footer import FooterKey
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        keys = {k.key: k for k in app.query(FooterKey)}
        assert keys["v"].description == "Flip"
        palette = keys["ctrl+p"].region.x   # "^p palette" is drawn over the footer's right end
        for key in ("p", "d", "v", "1", "2", "3"):
            k = keys[key]
            pad = k.get_component_styles("footer-key--description").padding.right   # a blank cell
            assert k.region.right - pad <= palette, key   # the label itself is clear of it
