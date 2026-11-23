"""Error and warning text is readable (4.5:1) in textual-dark, textual-light and under NO_COLOR
(Textual's monochrome filter), measured from the screen's own SVG as the audit does (#139, PR #137
review run 4). The words stay: colour is never the only signal."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import replace

import pytest
from textual.containers import Vertical
from textual.widgets import OptionList, Static, TabbedContent

from audit.svg import contrast_exact, parse
from telenotify_tui import bot
from telenotify_tui.app import TeleNotifyApp
from telenotify_tui.screens import settings_data as sd
from telenotify_tui.screens.dashboard import DashboardPane
from telenotify_tui.screens.review import ReviewPane
from telenotify_tui.screens.search import SearchPane
from telenotify_tui.screens.settings import EvalModal, SettingsPane

SIZE = (120, 40)
CONDITIONS = [("textual-dark", False), ("textual-light", False), ("textual-dark", True), ("textual-light", True)]


async def settle(app, pilot) -> None:
    for _ in range(3):
        await pilot.pause()
        await app.workers.wait_for_complete()
    await pilot.pause()


def set_colour(monkeypatch, no_color: bool) -> None:
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)


def worst(app, sample: str) -> float:
    """The lowest contrast of the cells drawing `sample` (its first occurrence on screen)."""
    grid = parse(app.export_screenshot(), *app.size)
    for y, line in enumerate(grid.text()):
        x = line.find(sample)
        if x < 0:
            continue
        cells = [grid.cells[y][i] for i in range(x, x + len(sample)) if grid.cells[y][i].ch.strip()]
        assert all(c.fg and c.bg for c in cells), sample
        return min(contrast_exact(c.fg, c.bg) for c in cells)
    raise AssertionError(f"{sample!r} isn't on screen:\n" + "\n".join(grid.text()))


@pytest.mark.parametrize("theme, no_color", CONDITIONS)
async def test_error_and_warning_text_is_readable(paths, config, monkeypatch, theme, no_color):
    set_colour(monkeypatch, no_color)
    async def fail(mode, what):
        return False, "Unit not found."
    monkeypatch.setattr(bot, "service_action", fail)
    paths = replace(paths, binary=None)   # the notes saying the program wasn't found
    cfg = json.loads(config.read_text(encoding="utf-8"))
    sd.dedupe_all(cfg)
    cfg["api_id"] = 4242                                  # api_hash missing: a problem under Bot
    cfg["alert_after_minutes"] = "soon"                   # an error under Bot
    cfg["tdlib_path"] = "/nonexistent/libtdjson.so"       # a warning under Bot
    config.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    app = TeleNotifyApp(replace(paths, config=config), refresh_seconds=0)
    seen: dict[str, float] = {}
    async with app.run_test(size=SIZE) as pilot:
        app.theme = theme
        await settle(app, pilot)
        assert app.no_color == no_color

        # Dashboard: "N chat(s) need a look" (the fixture has a banned chat) and the Bot box's read error
        seen["chats need a look"] = worst(app, "chat(s) need a look")
        dash = app.query_one(DashboardPane)
        dash.snapshot = dataclasses.replace(dash.snapshot, error="file is not a database")
        dash._show_status()
        await pilot.pause()
        seen["bot box error"] = worst(app, "Can't read the state file")
        dash.snapshot = dataclasses.replace(dash.snapshot, error="")
        dash._show_status()
        await pilot.pause()
        seen["N pending"] = worst(app, "1 pending")
        await pilot.press("a")
        await settle(app, pilot)
        seen["health status"] = worst(app, "banned")
        await pilot.press("s")   # the service menu (#83, option C): Restart is its third item
        await pilot.pause()
        await pilot.press("down", "down", "enter")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)
        seen["failed action"] = worst(app, "failed: Unit not found")

        # Review: the note, as an error and as a warning
        await pilot.press("2")
        await settle(app, pilot)
        review = app.query_one(ReviewPane)
        review.note("#11 NOT recorded (the state file can't be written); try again", "bad")
        await pilot.pause()
        seen["review note, error"] = worst(app, "NOT recorded")
        review.note("#11 was decided elsewhere; pick the next one", "warn")
        await pilot.pause()
        seen["review note, warning"] = worst(app, "decided elsewhere")

        # Search: the count saying the state can't be read
        await pilot.press("3")
        await settle(app, pilot)
        search = app.query_one(SearchPane)
        search._apply(search._generation, None, [], "couldn't read the state: locked", False)
        await pilot.pause()
        seen["search count"] = worst(app, "couldn't read")
        search._apply(search._generation, None, [search_row()], "", False)
        await pilot.pause()
        search.action_rejudge(False)
        await pilot.pause()
        seen["search judge note"] = worst(app, "program wasn't found")
        await pilot.press("p")
        await pilot.pause()
        seen["search paste note"] = worst(app, "program wasn't found")
        search.action_close_paste()
        await pilot.pause()

        # Settings: the bar's counts and the Bot section's problems
        await pilot.press("4")
        await settle(app, pilot)
        settings = app.query_one(SettingsPane)
        settings.query_one("#sections", TabbedContent).active = "sec-bot"
        await settle(app, pilot)
        status = str(settings.query_one("#settings-status", Static).render())
        assert "2 problems" in status and "1 warning" in status, status
        seen["settings bar, problems"] = worst(app, "2 problems")
        seen["settings bar, warnings"] = worst(app, "1 warning")
        seen["bot problems, missing key"] = worst(app, "problem: api_hash is missing")
        seen["bot problems, error"] = worst(app, "error: alert_after_minutes")
        seen["bot problems, warning"] = worst(app, "warning: tdlib_path")
        settings.query_one("#sections", TabbedContent).active = "sec-chats"
        await settle(app, pilot)
        seen["chats status"] = worst(app, "banned")
        app.push_screen(EvalModal(bot.EvalResult(error="no corpus"), bot.EvalResult(error="no corpus"),
                                  settings.app.paths.config))
        await settle(app, pilot)
        seen["corpus test failed"] = worst(app, "the test failed")
    low = {k: round(v, 2) for k, v in seen.items() if v < 4.5}
    assert not low, (theme, no_color, low)


def search_row():
    from telenotify_tui.store import Judged
    return Judged(chat_id=-1, msg_id=10, chat_title="Basra Jobs", verdict="publish", role="r", reasons="x",
                  action="forwarded", at=1, text="some post")


@pytest.mark.parametrize("theme, no_color", CONDITIONS)
async def test_a_failed_action_stays_readable_after_a_theme_change(paths, monkeypatch, theme, no_color):
    """Review run 5: the failed-action result kept the colour of the theme it was drawn in (2.08:1
    after a switch to light)."""
    set_colour(monkeypatch, no_color)

    async def fail(mode, what):
        return False, "Unit not found."
    monkeypatch.setattr(bot, "service_action", fail)
    other = "textual-light" if theme == "textual-dark" else "textual-dark"
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=SIZE) as pilot:
        app.theme = other
        await settle(app, pilot)
        await pilot.press("s")   # the service menu (#83, option C): Restart is its third item
        await pilot.pause()
        await pilot.press("down", "down", "enter")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)
        app.theme = theme
        await settle(app, pilot)
        assert worst(app, "failed: Unit not found") >= 4.5
        await pilot.press("a")
        await settle(app, pilot)
        assert worst(app, "banned") >= 4.5     # the health table, drawn before the switch


@pytest.mark.parametrize("theme, no_color", CONDITIONS)
@pytest.mark.parametrize("focused", [True, False])
async def test_the_pickers_mark_is_readable_on_the_highlighted_row(paths, config, monkeypatch, theme, no_color,
                                                                   focused):
    """Review run 5: the picker's " !" took the error / warning tone, made for the background, and was
    1.03:1 on the highlighted row in light. It is drawn in the row's own text colour, bold."""
    set_colour(monkeypatch, no_color)
    cfg = json.loads(config.read_text(encoding="utf-8"))
    cfg["level_keywords_en"] = ["junior", "junior"]   # a warning: listed twice
    cfg["level_keywords_ar"] = [5]                    # an error: not a word
    config.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    app = TeleNotifyApp(replace(paths, config=config), refresh_seconds=0)
    async with app.run_test(size=SIZE) as pilot:
        app.theme = theme
        await pilot.press("4")
        await settle(app, pilot)
        picker = app.query_one("#picker-roles", OptionList)
        for index, key in enumerate(("level_keywords_en", "level_keywords_ar")):
            picker.highlighted = index
            if focused:
                picker.focus()
            else:
                app.set_focus(None)
            await pilot.pause()
            assert worst(app, key) >= 4.5   # the row's own text, for comparison
            grid = parse(app.export_screenshot(), *SIZE)
            lines = grid.text()
            y = next(i for i, line in enumerate(lines) if f"{key} (" in line)
            x = lines[y].index("!", lines[y].index(key))
            cell = grid.cells[y][x]
            assert cell.bold and contrast_exact(cell.fg, cell.bg) >= 4.5, (key, cell)


@pytest.mark.parametrize("theme, no_color", CONDITIONS)
async def test_a_focused_scroll_area_shows_a_mark(paths, config, monkeypatch, theme, no_color):
    """Review run 5: the problems list took the focus (to scroll) with nothing on screen changing. A
    focused scroll area under Settings shows a bar at its left edge, 3:1 or more against the background
    it replaces; the Bot form's own scroll area too."""
    set_colour(monkeypatch, no_color)
    cfg = json.loads(config.read_text(encoding="utf-8"))
    sd.dedupe_all(cfg)
    cfg.update(dry_run="x", notify_review="x", alert_after_minutes="x", data_dir=5, alerts="x",
               tdlib_path="/nonexistent/" + "d" * 150 + "/libtdjson.so")
    config.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    app = TeleNotifyApp(replace(paths, config=config), refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        app.theme = theme
        await pilot.press("4")
        await settle(app, pilot)
        settings = app.query_one(SettingsPane)
        settings.query_one("#sections", TabbedContent).active = "sec-bot"
        await settle(app, pilot)
        problems = settings.query_one("#problems-bot").parent
        form = settings.query_one("#sec-bot VerticalScroll")
        for box in (problems, form):
            assert box.allow_focus()
            app.set_focus(None)
            await pilot.pause()
            before = parse(app.export_screenshot(), 80, 24)
            box.focus()
            await pilot.pause()
            after = parse(app.export_screenshot(), 80, 24)
            r = box.region
            for y in range(r.y, r.y + r.height):
                old, new = before.cells[y][r.x], after.cells[y][r.x]
                assert not old.ch.strip(), (box, y, old)              # unfocused: nothing there
                assert new.ch.strip() and contrast_exact(new.fg, old.bg) >= 3, (box, y, new, old)


@pytest.mark.parametrize("theme, no_color", CONDITIONS)
async def test_the_service_menu_is_readable_and_whole_at_80x24(paths, monkeypatch, theme, no_color):
    """#83, option C: the menu's title, items, highlighted row and "why it's off" line in the
    theme's text pair (4.5:1), whole at 80 x 24; the highlighted row is told apart without colour
    (its text colour becomes its background, and it is bold), and the words say why the items are off."""
    import asyncio

    set_colour(monkeypatch, no_color)
    gate = asyncio.Event()

    async def slow(mode, what):
        await gate.wait()
        return True, ""
    monkeypatch.setattr(bot, "service_action", slow)
    name = bot.SERVICES["dry-run"]
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        app.theme = theme
        await settle(app, pilot)
        await pilot.press("s")
        await pilot.pause()
        box = app.screen.query_one(Vertical).region

        def in_menu(sample: str) -> list:
            """The cells drawing `sample` inside the menu, which must show it whole."""
            grid = parse(app.export_screenshot(), 80, 24)
            for y in range(box.y, box.bottom):
                line = "".join(c.ch for c in grid.cells[y][box.x:box.right])
                if sample in line:
                    x = box.x + line.index(sample)
                    return [grid.cells[y][j] for j in range(x, x + len(sample))]
            raise AssertionError(f"{sample!r} isn't whole in the menu")

        for sample in (f"Service: {name}", "↑↓ move · enter pick · esc close", "Start", "Stop", "Restart"):
            cells = [c for c in in_menu(sample) if c.ch.strip()]
            assert min(contrast_exact(c.fg, c.bg) for c in cells) >= 4.5, sample
        start, stop = in_menu("Start")[0], in_menu("Stop")[0]
        assert start.bold and not stop.bold                            # not by colour alone
        assert start.bg == stop.fg and start.fg != stop.fg              # the text pair, reversed
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("down", "down", "enter")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        box = app.screen.query_one(Vertical).region
        grid = parse(app.export_screenshot(), 80, 24)
        inside = [[c for c in grid.cells[y][box.x + 1:box.right - 1]] for y in range(box.y + 1, box.bottom - 1)]
        drawn = " ".join("".join(c.ch for c in row) for row in inside).split()
        why = f"Restarting {name}: these are off until it's done.".split()
        assert drawn[:len(why)] == why, drawn                          # whole, before the items
        words = [c for row in inside[:2] for c in row if c.ch.strip()]
        assert min(contrast_exact(c.fg, c.bg) for c in words) >= 4.5
        gate.set()
        await settle(app, pilot)
