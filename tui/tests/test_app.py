from pathlib import Path

import pytest
from textual.app import InvalidThemeError
from textual.widgets import TabbedContent, Tabs

from telenotify_tui.app import TeleNotifyApp


async def test_tabs_switch_with_number_keys(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test() as pilot:
        tabs = app.query_one(TabbedContent)
        assert tabs.active == "dashboard"
        for key, tab in [("2", "review"), ("3", "search"), ("4", "settings"), ("1", "dashboard")]:
            await pilot.press(key)
            assert tabs.active == tab


async def test_plain_q_does_not_quit_but_ctrl_q_does(paths):
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test() as pilot:
        await pilot.press("3")                     # search: focus can sit on the results table
        await pilot.press("q")
        assert app.is_running
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert not app.is_running


async def test_the_ansi_themes_are_not_offered(paths):
    """#112, the owner's check: "ansi light is not light, it is pitch black and text blends with
    highlight color"; the ansi themes take the terminal's own colours, which we can't check."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        names = set(app.available_themes)
        assert not {"ansi-dark", "ansi-light"} & names
        assert {"textual-dark", "textual-light"} <= names
        with pytest.raises(InvalidThemeError):
            app.theme = "ansi-light"


async def test_only_the_checked_themes_are_offered(paths):
    """#81: the command palette offered about 19 themes; only textual-dark and textual-light
    have their colours checked (contrast, the chart's series), so only they are offered."""
    from textual.theme import ThemeProvider

    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert set(app.available_themes) == {"textual-dark", "textual-light"}
        offered = {name for name, _ in ThemeProvider(app.screen).commands}
        assert offered == {"textual-dark", "textual-light"}
        for name in ("nord", "dracula", "solarized-light"):
            with pytest.raises(InvalidThemeError):
                app.theme = name


@pytest.mark.parametrize("key, tab, main", [("2", "review", "review-list"), ("1", "dashboard", "feed")])
async def test_the_tabs_key_again_focuses_its_main_widget(paths, key, tab, main):
    """#60: pressing 2 again on Review moved focus to the tab bar, so s / d / k did nothing.
    The key of the tab already shown focuses that tab's main widget instead."""
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press(key)
        await pilot.pause()
        await pilot.pause()
        assert app.query_one(TabbedContent).active == tab
        assert app.focused is not None and app.focused.id == main
        await pilot.press(key)
        await pilot.pause()
        await pilot.pause()
        assert app.query_one(TabbedContent).active == tab
        assert app.focused is not None and app.focused.id == main, app.focused
        app.set_focus(app.query_one(Tabs))     # from the tab bar too, the key goes back into the tab
        await pilot.press(key)
        await pilot.pause()
        await pilot.pause()
        assert app.focused is not None and app.focused.id == main, app.focused


@pytest.mark.parametrize("env_theme, expected", [("ansi-light", "textual-dark"), ("ansi-dark,textual-light", "textual-light"),
                                                 ("nord", "textual-dark")])
def test_an_ansi_theme_from_the_environment_isnt_used(paths, env_theme, expected):
    """#112, #81: TEXTUAL_THEME (read when Textual is imported) can't bring an ansi theme back,
    nor any other theme that isn't offered."""
    import os
    import subprocess
    import sys
    code = f"""
import asyncio
from pathlib import Path
from telenotify_tui.app import TeleNotifyApp
from telenotify_tui.paths import Paths
paths = Paths(config=Path({str(paths.config)!r}), data_dir=Path({str(paths.data_dir)!r}),
              state=Path({str(paths.state)!r}), binary=None, dry_run=True)
async def main():
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        print(app.theme, app.current_theme.name, app.theme_variables["background"])
asyncio.run(main())
"""
    env = {**os.environ, "TEXTUAL_THEME": env_theme, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    out = subprocess.run([sys.executable, "-c", code], check=False, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    theme, current, background = out.stdout.split()[-3:]
    assert theme == current == expected
    assert not background.startswith("ansi")


async def test_a_lost_queued_reload_doesnt_stop_tab_reloads(paths, monkeypatch):
    """Review run 3: Textual drops a call_after_refresh callback whose screen goes away first;
    the "reload queued" mark must not then stop every later tab show from reloading."""
    import time

    from telenotify_tui.screens.review import ReviewPane

    calls = []
    monkeypatch.setattr(ReviewPane, "reload", lambda self: calls.append(time.monotonic()))
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app._reload_queued = time.monotonic() - 5   # queued long ago; its callback never ran
        await pilot.press("2")
        await pilot.pause()
        await pilot.pause()
        assert calls, "showing Review didn't reload it"
