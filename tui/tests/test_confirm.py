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
        assert "forwards nothing" in question       # the conftest paths are the dry run
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


async def test_the_real_service_dialog_says_forwarding_stops(paths, fake_systemctl):  # noqa: F811
    app = TeleNotifyApp(replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.click("#svc-stop")
        await pilot.pause()
        question = str(app.screen.query_one("#confirm-question").render())
        assert "forwarding matches" in question and "forwards nothing" not in question
        await pilot.press("escape")
        await settle(app, pilot)


def _ratio(fg, bg) -> float:
    def lum(c):
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(c.r) + 0.7152 * ch(c.g) + 0.0722 * ch(c.b)
    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


@pytest.mark.parametrize("theme", ["textual-dark", "textual-light"])
async def test_the_destructive_button_text_is_readable(paths, cfg_path, fake_systemctl, theme):  # noqa: F811
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        app.theme = theme
        await settle(app, pilot)
        await pilot.click("#svc-stop")
        await pilot.pause()
        for sel in ("#confirm-yes",):
            b = app.screen.query_one(sel, Button)
            bg = b.styles.background
            fg = bg + b.styles.color                 # text colour over the button, alpha applied
            assert _ratio(fg, bg) >= 4.5, (theme, str(fg), str(bg))
        await pilot.press("escape")
        await settle(app, pilot)
        pane = await open_settings(pilot)
        await add_keyword(pilot, pane, "filters", "extra_seniority", "lead")
        await pilot.press("ctrl+q")
        await pilot.pause()
        b = app.screen.query_one("#yes", Button)
        bg = b.styles.background
        assert _ratio(bg + b.styles.color, bg) >= 4.5, theme


async def test_a_too_early_press_says_why_nothing_happened(paths, cfg_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ConfirmModal, "ARM_DELAY", 5.0)
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        await add_keyword(pilot, pane, "filters", "extra_seniority", "lead")
        await pilot.press("ctrl+q")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert "press again" in str(app.screen.query_one("#confirm-wait").render())
