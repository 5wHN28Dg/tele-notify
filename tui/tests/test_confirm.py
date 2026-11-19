"""Confirmation dialogs are safe (policy S2): the safe choice has the focus, the
confirming button names its action and is styled destructive when it loses
something, the question says what follows, and a press carried over from the
step before can't confirm."""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_dashboard import fake_systemctl, settle  # noqa: F401  (fixture)
from test_settings import add_keyword, cfg_path, open_settings  # noqa: F401  (fixture)
from textual.widgets import Button

from telenotify_tui.app import TeleNotifyApp
from telenotify_tui.screens.dashboard import ConfirmScreen
from telenotify_tui.screens.settings import ConfirmModal


async def dirty_app(paths, cfg_path, pilot_size=(120, 40)):  # noqa: F811
    return TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)


async def test_enter_on_the_quit_question_keeps_the_edits(paths, cfg_path):  # noqa: F811
    app = await dirty_app(paths, cfg_path)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        await add_keyword(pilot, pane, "filters", "extra_seniority", "lead")
        assert pane.dirty
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        assert app.focused is not None and app.focused.id == "no"
        await pilot.press("enter")                  # one enter must not quit
        await pilot.pause()
        assert app.is_running and not isinstance(app.screen, ConfirmModal)
        assert pane.dirty


async def test_settings_confirm_names_and_styles_the_action(paths, cfg_path):  # noqa: F811
    app = await dirty_app(paths, cfg_path)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        await add_keyword(pilot, pane, "filters", "extra_seniority", "lead")
        await pilot.press("ctrl+q")
        await pilot.pause()
        yes = app.screen.query_one("#yes", Button)
        assert "Quit" in str(yes.label) and "Yes" not in str(yes.label)
        assert yes.variant == "error"               # it loses the edits
        assert "Cancel" in str(app.screen.query_one("#no", Button).label)


async def test_restart_question_isnt_styled_destructive():
    modal = ConfirmModal("Restart?", "Restart", destructive=False)
    assert modal.variant == "warning"


async def test_service_dialog_names_the_action_and_its_effect(paths, fake_systemctl):  # noqa: F811
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.click("#svc-stop")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        assert app.focused is not None and app.focused.id == "confirm-no"
        yes = app.screen.query_one("#confirm-yes", Button)
        assert str(yes.label).startswith("Stop") and yes.variant == "error"
        assert "Cancel" in str(app.screen.query_one("#confirm-no", Button).label)
        question = str(app.screen.query_one("#confirm-question").render())
        assert "forward" in question                # says what stopping means
        await pilot.press("escape")
        await settle(app, pilot)


@pytest.mark.parametrize("dialog", ["settings", "service"])
async def test_a_carried_over_press_cant_confirm(paths, cfg_path, fake_systemctl, monkeypatch, dialog):  # noqa: F811
    monkeypatch.setattr(ConfirmModal, "ARM_DELAY", 5.0)
    monkeypatch.setattr(ConfirmScreen, "ARM_DELAY", 5.0)
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        if dialog == "settings":
            pane = await open_settings(pilot)
            await add_keyword(pilot, pane, "filters", "extra_seniority", "lead")
            await pilot.press("ctrl+q")
            await pilot.pause()
            await pilot.press("y")                  # too soon: ignored
            await pilot.pause()
            assert app.is_running and isinstance(app.screen, ConfirmModal)
        else:
            await settle(app, pilot)
            await pilot.click("#svc-stop")
            await pilot.pause()
            await pilot.click("#confirm-yes")       # too soon: ignored
            await pilot.press("y")
            await settle(app, pilot)
            assert isinstance(app.screen, ConfirmScreen)
            assert not [c for c in fake_systemctl if c[0] == "action"]
