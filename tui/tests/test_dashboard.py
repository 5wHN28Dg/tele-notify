"""Dashboard screen, against the conftest state file. systemctl is always mocked."""

from __future__ import annotations

import sqlite3

import pytest
from textual.widgets import DataTable, Static, TabbedContent

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


async def test_totals(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        lines = text(app, "#totals").splitlines()
        assert lines[1].split() == ["today", "1", "1", "1", "1"]
        assert lines[2].split() == ["7", "days", "1", "1", "2", "1"]


async def test_per_chat_table_and_day_selector(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("c")
        await settle(app, pilot)
        table = app.query_one("#chat-stats", DataTable)
        got = {r[0]: r[1:] for r in rows(table)}
        # chat: total, publish, review, reject, forwarded (7 days by default)
        assert got == {"Basra Jobs": ["2", "1", "1", "0", "1"], "Iraq Jobs": ["2", "0", "0", "2", "0"]}

        await pilot.click("#days-1")
        await settle(app, pilot)
        got = {r[0]: r[1:] for r in rows(table)}
        assert got["Iraq Jobs"] == ["1", "0", "0", "1", "0"]  # the 3-day-old reject drops out


async def test_slow_reads_never_pile_up(paths, monkeypatch, fake_systemctl):
    """A state file the bot keeps locked: day changes, refreshes and service
    actions while a read is stuck wait for it instead of starting more threads."""
    import threading

    from textual.widgets import RadioButton

    from telenotify_tui.screens import dashboard as dash

    real = dash.load_snapshot
    gate = threading.Event()
    guard = threading.Lock()
    running: list[int] = []
    peak = [0]
    days_read: list[int] = []

    def slow(path, days):
        with guard:
            running.append(days)
            peak[0] = max(peak[0], len(running))
        gate.wait(10)
        with guard:
            running.remove(days)
        days_read.append(days)
        return real(path, days)

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
            app.query_one("#days-1", RadioButton).value = True
            await pilot.pause()
            app.query_one("#days-30", RadioButton).value = True
            await pilot.pause()
            pane.reload()
            pane._run_service("restart")
            await pilot.pause(0.2)
            assert peak[0] == 1
            gate.set()
            await settle(app, pilot)
            await settle(app, pilot)
            assert peak[0] == 1
            assert days_read[-1] == 30 and pane.snapshot is not None and pane.stat_days == 30
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
        days = dd.per_day(s, 14, now)
        assert len(days) == 14 and days[-1].total == 3
        assert sum(d.total for d in days) == 4
        week = dd.totals(s, now - 7 * 86400)
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
