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
