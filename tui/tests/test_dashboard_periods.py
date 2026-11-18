"""Dashboard periods: the day math, the counting queries, and the picker on screen."""

from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime, timedelta

import pytest
from conftest import SCHEMA
from textual.widgets import DataTable, Input, Select, Static

from telenotify_tui import bot
from telenotify_tui.app import TeleNotifyApp
from telenotify_tui.screens import dashboard as dash
from telenotify_tui.screens import dashboard_data as dd
from telenotify_tui.screens.dashboard import DashboardPane, RangeScreen, render_chart
from telenotify_tui.store import Store


@pytest.fixture
def tz(monkeypatch):
    """Run in a given time zone (one with daylight saving), then go back."""
    def use(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        time.tzset()
    yield use
    monkeypatch.undo()
    time.tzset()


def at(d: date, hour: int = 12, minute: int = 0, second: int = 0) -> int:
    return int(datetime(d.year, d.month, d.day, hour, minute, second).timestamp())


def new_db(path, rows=(), reviews=(), schema: str = SCHEMA) -> None:
    """rows: (chat_id, msg_id, title, verdict, action, at, album_id); reviews: (chat_id, msg_id, status)."""
    db = sqlite3.connect(path)
    db.executescript(schema)
    db.executemany("""INSERT INTO processed (chat_id, msg_id, chat_title, verdict, action, at, album_id)
                      VALUES (?, ?, ?, ?, ?, ?, ?)""", rows)
    db.executemany("INSERT INTO reviews (chat_id, msg_id, status, at) VALUES (?, ?, ?, 0)", reviews)
    db.commit()
    db.close()


# ----------------------------------------------------------- period math

def test_resolve_presets_cross_month_and_year_boundaries():
    def days(kind: str, today: date) -> tuple[date, date]:
        p = dd.resolve(dd.PeriodChoice(kind), today)
        return p.first, p.last

    assert days("today", date(2026, 3, 1)) == (date(2026, 3, 1), date(2026, 3, 1))
    assert days("7d", date(2026, 3, 1)) == (date(2026, 2, 23), date(2026, 3, 1))
    assert days("7d", date(2024, 3, 1)) == (date(2024, 2, 24), date(2024, 3, 1))   # leap year
    assert days("30d", date(2026, 1, 10)) == (date(2025, 12, 12), date(2026, 1, 10))
    assert days("month", date(2026, 11, 30)) == (date(2026, 11, 1), date(2026, 11, 30))
    assert days("month", date(2026, 12, 1)) == (date(2026, 12, 1), date(2026, 12, 1))
    assert days("year", date(2027, 1, 1)) == (date(2027, 1, 1), date(2027, 1, 1))
    assert days("year", date(2026, 11, 18)) == (date(2026, 1, 1), date(2026, 11, 18))
    assert dd.resolve(dd.PeriodChoice("7d"), date(2026, 3, 1)).days == 7
    assert dd.resolve(dd.PeriodChoice("30d"), date(2026, 3, 1)).days == 30


def test_all_time_starts_at_the_first_judged_post_or_today_when_empty():
    p = dd.resolve(dd.PeriodChoice("all"), date(2026, 11, 18), date(2026, 3, 4))
    assert (p.first, p.last, p.since) == (date(2026, 3, 4), date(2026, 11, 18), date(2026, 3, 4))
    assert p.describe() == "All time: since 4 Mar 2026"
    empty = dd.resolve(dd.PeriodChoice("all"), date(2026, 11, 18), None)
    assert (empty.first, empty.last, empty.since) == (date(2026, 11, 18), date(2026, 11, 18), None)
    assert "nothing judged yet" in empty.describe()


def test_custom_range_includes_the_whole_last_day(tmp_path):
    p = dd.resolve(dd.PeriodChoice("custom", date(2026, 11, 1), date(2026, 11, 3)), date(2026, 11, 18))
    assert p.start == dd.midnight(date(2026, 11, 1))
    assert p.end == dd.midnight(date(2026, 11, 4))
    path = tmp_path / "s.sqlite3"
    rows = [(-1, 1, "A", "reject", "rejected", at(date(2026, 10, 31), 23, 59, 59), 0),   # the day before
            (-1, 2, "A", "reject", "rejected", at(date(2026, 11, 1), 0, 0, 0), 0),       # first second
            (-1, 3, "A", "reject", "rejected", at(date(2026, 11, 3), 23, 59, 59), 0),    # last second
            (-1, 4, "A", "reject", "rejected", at(date(2026, 11, 4), 0, 0, 0), 0)]       # the day after
    new_db(path, rows)
    with Store(path) as s:
        ps = dd.period_stats(s, p, 46)
    assert ps.totals.reject == 2
    assert [b.day for b in ps.bars] == ["2026-11-01", "2026-11-02", "2026-11-03"]
    assert [b.reject for b in ps.bars] == [1, 0, 1]
    assert p.describe() == "Custom: 1 Nov – 3 Nov 2026"


def test_days_are_local_days_across_daylight_saving(tz, tmp_path):
    tz("America/New_York")   # 2026-03-08 has 23 hours, 2026-11-01 has 25
    spring = dd.resolve(dd.PeriodChoice("custom", date(2026, 3, 8), date(2026, 3, 8)), date(2026, 11, 18))
    assert spring.end - spring.start == 23 * 3600
    autumn = dd.resolve(dd.PeriodChoice("custom", date(2026, 11, 1), date(2026, 11, 1)), date(2026, 11, 18))
    assert autumn.end - autumn.start == 25 * 3600
    path = tmp_path / "s.sqlite3"
    rows = [(-1, 1, "A", "publish", "forwarded", at(date(2026, 11, 1), 23, 30), 0),
            (-1, 2, "A", "publish", "forwarded", at(date(2026, 11, 2), 0, 30), 0)]
    new_db(path, rows)
    p = dd.resolve(dd.PeriodChoice("custom", date(2026, 10, 31), date(2026, 11, 2)), date(2026, 11, 18))
    with Store(path) as s:
        ps = dd.period_stats(s, p, 46)
        only = dd.period_stats(s, autumn, 46)
    assert [b.publish for b in ps.bars] == [0, 1, 1]   # SQLite and Python agree on the local day
    assert only.totals.publish == 1


@pytest.mark.parametrize("first,last,message", [
    ("", "2026-11-03", "start date as YYYY-MM-DD"),
    ("2026/11/01", "2026-11-03", "isn't YYYY-MM-DD"),
    ("2026-11-1", "2026-11-03", "isn't YYYY-MM-DD"),
    ("２０２６-11-01", "2026-11-03", "isn't YYYY-MM-DD"),
    ("2026-02-30", "2026-11-03", "isn't a real day"),
    ("1999-12-31", "2026-11-03", "years 2000 to 2100"),
    ("2026-11-03", "2026-11-01", "is before the start date"),
    ("2026-11-01", "", "end date as YYYY-MM-DD"),
])
def test_parse_range_explains_mistakes(first, last, message):
    with pytest.raises(ValueError, match=message):
        dd.parse_range(first, last)


def test_parse_range_ok():
    assert dd.parse_range(" 2026-11-01 ", "2026-11-01") == (date(2026, 11, 1), date(2026, 11, 1))


# --------------------------------------------------------------- buckets

def test_bucket_picks_the_finest_that_fits():
    def period(a: date, b: date) -> dd.Period:
        return dd.Period("custom", a, b)

    assert dd.pick_bucket(period(date(2026, 10, 20), date(2026, 11, 18)), 46) == "day"      # 30 days
    assert dd.pick_bucket(period(date(2026, 10, 1), date(2026, 11, 18)), 46) == "week"      # 49 days
    year = period(date(2026, 1, 1), date(2026, 11, 18))
    assert dd.pick_bucket(year, 46) == "month"   # 47 weeks: Thu 1 Jan, then 46 Mondays
    assert dd.pick_bucket(year, 60) == "week"
    assert dd.pick_bucket(period(date(2020, 1, 1), date(2026, 11, 18)), 46) == "year"


def test_bucket_starts_clip_the_first_bar_and_cross_the_year():
    p = dd.Period("custom", date(2026, 1, 1), date(2026, 1, 20))   # a Thursday
    assert dd.bucket_starts(p, "week") == [date(2026, 1, 1), date(2026, 1, 5), date(2026, 1, 12),
                                           date(2026, 1, 19)]
    p = dd.Period("custom", date(2025, 11, 15), date(2026, 2, 3))
    assert dd.bucket_starts(p, "month") == [date(2025, 11, 15), date(2025, 12, 1), date(2026, 1, 1),
                                            date(2026, 2, 1)]
    assert dd.bucket_starts(p, "year") == [date(2025, 11, 15), date(2026, 1, 1)]


def test_render_chart_fits_any_number_of_bars():
    many = [dd.DayCount((date(2000, 1, 1) + timedelta(days=i)).isoformat(), i % 3, 1, 2) for i in range(101)]
    for n in (1, 7, 15, 16, 30, 46, 101):
        out = render_chart(many[:n], "day").plain.splitlines()
        assert len(out) == 4 and all(len(line) <= dash.CHART_WIDTH for line in out), n
    assert render_chart(many[:7], "day").plain.splitlines()[-1] == "01 02 03 04 05 06 07"
    last = render_chart(many[:30], "day").plain.splitlines()[-1]
    assert last.startswith("1 Jan") and last.endswith("30 Jan")
    months = [dd.DayCount("2026-01-01", 1), dd.DayCount("2026-02-01", 2), dd.DayCount("2026-03-01", 3)]
    assert render_chart(months, "month").plain.splitlines()[-1].split() == ["Jan", "2026", "Mar", "2026"]


# --------------------------------------------------------------- queries

def test_empty_and_missing_tables_give_zeros(tmp_path):
    path = tmp_path / "s.sqlite3"
    new_db(path)
    p = dd.resolve(dd.PeriodChoice("all"), date(2026, 11, 18), None)
    with Store(path) as s:
        assert dd.first_judged(s) == (None, True)
        ps = dd.period_stats(s, p, 46)
    assert ps.totals == dd.Totals() and ps.chats == [] and [b.total for b in ps.bars] == [0]
    bare = tmp_path / "bare.sqlite3"
    sqlite3.connect(bare).close()
    with Store(bare) as s:
        assert dd.first_judged(s) == (None, True)
        assert dd.period_stats(s, p, 46).totals == dd.Totals()


def test_counts_albums_once_and_forwarded_reviews_only_when_sent(tmp_path):
    day = date(2026, 11, 18)
    t = at(day)
    rows = [
        (-1, 1, "A old name", "publish", "forwarded", t - 3600, 0),
        (-1, 2, "A", "publish", "linked", t, 0),
        (-1, 3, "A", "review", "queued for review", t, 0),       # sent from the review queue
        (-1, 4, "A", "review", "queued for review", t, 0),       # still pending
        (-1, 5, "A", "review", "kept", t, 0),                    # matched, recorded, not sent
        (-1, 6, "A", "publish", "would publish", t, 0),          # a dry run doesn't forward
        # an album of three pictures: one post, counted on its first picture
        (-2, 20, "B", "reject", "rejected", t, 77),
        (-2, 21, "B", "", "part of a post already handled", t, 77),
        (-2, 22, "B", "", "part of a post already handled", t, 77),
        (-2, 23, "B", "reject", "rejected", t, None),
    ]
    reviews = [(-1, 3, "sent"), (-1, 4, "pending"), (-1, 5, "kept"), (-2, 23, "sent")]
    path = tmp_path / "s.sqlite3"
    new_db(path, rows, reviews)
    with Store(path) as s:
        ps = dd.period_stats(s, dd.resolve(dd.PeriodChoice("today"), day), 46)
    assert ps.totals == dd.Totals(publish=3, review=3, reject=2, forwarded=3)
    chats = {c.chat: c for c in ps.chats}
    assert set(chats) == {"A", "B"}   # the newest title
    assert (chats["A"].publish, chats["A"].review, chats["A"].forwarded) == (3, 3, 3)
    assert (chats["B"].reject, chats["B"].total) == (2, 2)


def test_album_counts_where_its_first_picture_is(tmp_path):
    """Like `telenotify stats`: an album whose first picture was judged before the period isn't counted again."""
    day = date(2026, 11, 18)
    rows = [(-1, 1, "A", "publish", "forwarded", at(day - timedelta(days=1), 23, 59), 5),
            (-1, 2, "A", "", "part of a post already handled", at(day, 0, 1), 5)]
    path = tmp_path / "s.sqlite3"
    new_db(path, rows)
    with Store(path) as s:
        assert dd.period_stats(s, dd.resolve(dd.PeriodChoice("today"), day), 46).totals.judged == 0
        assert dd.period_stats(s, dd.resolve(dd.PeriodChoice("7d"), day), 46).totals.publish == 1


def test_old_state_file_without_album_column(tmp_path):
    old = SCHEMA.replace(", album_id INTEGER,\n  PRIMARY KEY", ",\n  PRIMARY KEY")
    assert "album_id INTEGER,\n  PRIMARY" not in old
    path = tmp_path / "s.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(old)
    db.execute("""INSERT INTO processed (chat_id, msg_id, chat_title, verdict, action, at)
                  VALUES (-1, 1, 'A', 'publish', 'forwarded', ?)""", (at(date(2026, 11, 18)),))
    db.commit()
    db.close()
    with Store(path) as s:
        ps = dd.period_stats(s, dd.resolve(dd.PeriodChoice("month"), date(2026, 11, 18)), 46)
    assert ps.totals == dd.Totals(publish=1, forwarded=1)


def test_weeks_and_months_add_up_to_the_totals(tmp_path):
    first = date(2025, 1, 1)
    rows = [(-1, i, "A", ("publish", "review", "reject")[i % 3], "rejected", at(first + timedelta(days=i * 3)), 0)
            for i in range(200)]
    path = tmp_path / "s.sqlite3"
    new_db(path, rows)
    with Store(path) as s:
        start, known = dd.first_judged(s)
        assert start == first and known
        for kind, bucket in (("all", "month"), ("year", "week")):
            p = dd.resolve(dd.PeriodChoice(kind), date(2026, 8, 31), start)
            ps = dd.period_stats(s, p, 46 if kind == "all" else 60)
            assert ps.bucket == bucket
            assert sum(b.total for b in ps.bars) == ps.totals.judged > 0
            assert ps.bars[0].day == p.first.isoformat()


# ---------------------------------------------------------------- screen

@pytest.fixture(autouse=True)
def quiet_systemctl(monkeypatch):
    async def state(mode: str) -> str:
        return "inactive"
    monkeypatch.setattr(bot, "service_state", state)


async def settle(app, pilot) -> None:
    for _ in range(3):
        await pilot.pause()
        await app.workers.wait_for_complete()
    await pilot.pause()


def text(app, selector: str) -> str:
    return str(app.query_one(selector, Static).render())


def numbers(app) -> list[str]:
    """judged, publish, review, reject, forwarded."""
    return text(app, "#totals").splitlines()[1].split()


async def test_picking_periods_with_keys_and_mouse(paths, now):
    # one more post, judged 20 days ago: in the last 30 days, not in the last 7
    db = sqlite3.connect(paths.state)
    db.execute("""INSERT INTO processed (chat_id, msg_id, chat_title, verdict, action, at)
                  VALUES (-2, 1, 'Iraq Jobs', 'publish', 'forwarded', ?)""", (now - 20 * 86400,))
    db.commit()
    db.close()
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(110, 32)) as pilot:
        await settle(app, pilot)
        assert numbers(app) == ["4", "1", "1", "2", "1"]   # last 7 days

        await pilot.press("p")          # opens the picker; Last 7 days is highlighted
        await pilot.pause()
        await pilot.press("down", "enter")
        await settle(app, pilot)
        assert app.query_one(DashboardPane).choice.kind == "30d"
        assert numbers(app) == ["5", "2", "1", "2", "2"]

        await pilot.press("p")
        await pilot.pause()
        await pilot.press("up", "up", "enter")   # from Last 30 days to Today
        await settle(app, pilot)
        assert app.query_one(DashboardPane).choice.kind == "today"
        assert numbers(app) == ["3", "1", "1", "1", "1"]

        app.query_one("#period", Select).value = "all"
        await settle(app, pilot)
        first = dd.today(now - 20 * 86400)
        assert numbers(app) == ["5", "2", "1", "2", "2"]
        assert f"since {dd.fmt_day(first)}" in str(app.query_one("#dash-activity").border_subtitle)

        # mouse: Dates… opens the custom range form; Escape leaves the period alone
        await pilot.click("#period-range")
        await pilot.pause()
        assert isinstance(app.screen, RangeScreen)
        await pilot.press("escape")
        await settle(app, pilot)
        assert app.query_one(DashboardPane).choice.kind == "all"
        assert app.query_one("#period", Select).value == "all"


async def test_custom_range_form_checks_dates(paths, now):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    today = dd.today(now)
    async with app.run_test(size=(110, 32)) as pilot:
        await settle(app, pilot)
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, RangeScreen)
        form = app.screen
        assert form.query_one("#range-from", Input).value == (today - timedelta(days=6)).isoformat()
        form.query_one("#range-from", Input).value = "2026-02-30"
        await pilot.click("#range-ok")
        await pilot.pause()
        assert isinstance(app.screen, RangeScreen)
        assert "isn't a real day" in str(form.query_one("#range-error", Static).render())

        # from today back to the day of the 3-day-old reject, typed with the keyboard
        three_ago = today - timedelta(days=3)
        form.query_one("#range-from", Input).value = ""
        form.query_one("#range-to", Input).value = ""
        form.query_one("#range-from", Input).focus()
        await pilot.press(*three_ago.isoformat(), "enter", *three_ago.isoformat(), "enter")
        await settle(app, pilot)
        assert not isinstance(app.screen, RangeScreen)
        pane = app.query_one(DashboardPane)
        assert pane.choice == dd.PeriodChoice("custom", three_ago, three_ago)
        assert app.query_one("#period", Select).value == dash.CUSTOM_SHOWN
        assert numbers(app) == ["1", "0", "0", "1", "0"]
        assert text(app, "#chats-period").startswith("Custom:")
        rows = app.query_one("#chat-stats", DataTable)
        assert rows.row_count == 1


def test_unreadable_first_time_gives_unknown_start(tmp_path):
    """A corrupt `at` (too big for a date, negative, or text) mustn't crash all time."""
    good = at(date(2026, 3, 4))
    for bad in (10**15, -(10**15), "garbage"):
        path = tmp_path / f"s{abs(hash(str(bad)))}.sqlite3"
        new_db(path, [(-1, 1, "A", "reject", "rejected", bad, 0),
                      (-1, 2, "A", "publish", "forwarded", good, 0)])
        with Store(path) as s:
            first, known = dd.first_judged(s)
            ps = dd.period_stats(s, dd.resolve(dd.PeriodChoice("all"), date(2026, 11, 18), first, known), 46)
        if bad != -(10**15):   # bigger than the good time (text sorts after numbers): not the first
            assert (first, known) == (date(2026, 3, 4), True)
            assert ps.totals == dd.Totals(publish=1, forwarded=1)
            continue
        assert (first, known) == (date(2026, 3, 4), False), bad
        assert ps.period.describe() == "All time: start unknown"
        assert ps.totals == dd.Totals(publish=1, forwarded=1)
    path = tmp_path / "only-bad.sqlite3"
    new_db(path, [(-1, 1, "A", "reject", "rejected", 10**15, 0)])
    with Store(path) as s:
        assert dd.first_judged(s) == (None, False)


async def test_all_time_with_a_corrupt_time_on_screen(paths):
    db = sqlite3.connect(paths.state)
    db.execute("""INSERT INTO processed (chat_id, msg_id, chat_title, verdict, action, at)
                  VALUES (-2, 99, 'Iraq Jobs', 'reject', 'rejected', ?)""", (-(10**15),))
    db.execute("""INSERT INTO processed (chat_id, msg_id, chat_title, verdict, action, at)
                  VALUES (-2, 98, 'Iraq Jobs', 'reject', 'rejected', ?)""", (10**15,))   # newest in the feed
    db.commit()
    db.close()
    assert dash.load_snapshot(paths.state, dd.PeriodChoice("all")).error == ""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(110, 32)) as pilot:
        await settle(app, pilot)
        app.query_one("#period", Select).value = "all"
        await settle(app, pilot)
        assert "start unknown" in str(app.query_one("#dash-activity").border_subtitle)
        assert numbers(app) == ["4", "1", "1", "2", "1"]
        assert app.query_one("#feed", DataTable).get_row_at(0)[0] == "?"   # the broken row's time


async def test_picking_custom_again_reopens_the_form(paths, now):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    today = dd.today(now)
    async with app.run_test(size=(110, 32)) as pilot:
        await settle(app, pilot)
        pane = app.query_one(DashboardPane)
        pane._set_choice(dd.PeriodChoice("custom", today, today))
        await settle(app, pilot)
        sel = app.query_one("#period", Select)
        assert sel.value == dash.CUSTOM_SHOWN
        await pilot.press("p")   # the picker, then "Custom range…" (the last entry) again
        await pilot.pause()
        await pilot.press("down", "enter")
        await pilot.pause()
        assert isinstance(app.screen, RangeScreen)
        assert app.screen.query_one("#range-from", Input).value == today.isoformat()
        await pilot.press("escape")
        await settle(app, pilot)
        assert sel.value == dash.CUSTOM_SHOWN and pane.choice == dd.PeriodChoice("custom", today, today)


async def test_custom_from_the_picker_and_cancel_goes_back(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(110, 32)) as pilot:
        await settle(app, pilot)
        app.query_one("#period", Select).value = "custom"
        await pilot.pause()
        assert isinstance(app.screen, RangeScreen)
        await pilot.click("#range-cancel")
        await settle(app, pilot)
        assert app.query_one("#period", Select).value == "7d"
        assert app.query_one(DashboardPane).choice.kind == "7d"


async def test_slow_counts_are_reused_between_ticks_but_not_after_a_change(paths, monkeypatch):
    counted: list[str] = []
    real = dd.period_stats

    def counting(store, period, max_bars):
        counted.append(period.kind)
        return real(store, period, max_bars)

    monkeypatch.setattr(dd, "period_stats", counting)
    monkeypatch.setattr(dash, "SLOW_SECONDS", -1.0)   # every count looks slow
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(110, 32)) as pilot:
        await settle(app, pilot)
        pane = app.query_one(DashboardPane)
        assert counted == ["7d"]
        pane.reload()
        await settle(app, pilot)
        assert counted == ["7d"]   # the tick reused it
        app.query_one("#period", Select).value = "today"
        await settle(app, pilot)
        assert counted == ["7d", "today"]
        monkeypatch.setattr(dash, "SLOW_SECONDS", 1e9)   # fast again: every tick counts
        pane._counted = (pane.choice, 0.0, pane.snapshot.period, 0.0)
        pane.reload()
        await settle(app, pilot)
        assert counted == ["7d", "today", "today"]
