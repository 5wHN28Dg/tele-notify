"""Error and warning text is readable (4.5:1) in textual-dark, textual-light and under NO_COLOR
(Textual's monochrome filter), measured from the screen's own SVG as the audit does (#139, PR #137
review run 4). The words stay: colour is never the only signal."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import replace

import pytest
from textual.widgets import Static, TabbedContent

from audit.svg import contrast_exact, parse
from telenotify_tui.app import TeleNotifyApp
from telenotify_tui.screens import settings_data as sd
from telenotify_tui.screens.dashboard import DashboardPane
from telenotify_tui.screens.review import ReviewPane
from telenotify_tui.screens.search import SearchPane
from telenotify_tui.screens.settings import SettingsPane

SIZE = (120, 40)
CONDITIONS = [("textual-dark", False), ("textual-light", False), ("textual-dark", True), ("textual-light", True)]


async def settle(app, pilot) -> None:
    for _ in range(3):
        await pilot.pause()
        await app.workers.wait_for_complete()
    await pilot.pause()


def worst(app, sample: str) -> float:
    """The lowest contrast of the cells drawing `sample` (its first occurrence on screen)."""
    grid = parse(app.export_screenshot(), *SIZE)
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
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
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
    low = {k: round(v, 2) for k, v in seen.items() if v < 4.5}
    assert not low, (theme, no_color, low)
