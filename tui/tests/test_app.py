import pytest
from textual.app import InvalidThemeError
from textual.widgets import TabbedContent

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
