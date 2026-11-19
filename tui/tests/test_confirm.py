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
        assert "Too quick" in str(app.screen.query_one("#confirm-wait").render())


@pytest.mark.parametrize("theme", ["textual-dark", "textual-light"])
async def test_the_focused_destructive_button_is_readable(paths, fake_systemctl, theme):  # noqa: F811
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        app.theme = theme
        await settle(app, pilot)
        await pilot.click("#svc-stop")
        await pilot.pause()
        await pilot.press("shift+tab")                      # Cancel -> the destructive button
        await pilot.pause()
        b = app.screen.query_one("#confirm-yes", Button)
        assert app.focused is b
        assert b.styles.background_tint.a == 0               # Textual's focus tint would drop it to 4.36:1
        bg = b.styles.background
        assert _ratio(bg + b.styles.color, bg) >= 4.5, theme
        await pilot.press("escape")
        await settle(app, pilot)


async def test_a_press_made_too_early_but_handled_late_cant_confirm(paths, fake_systemctl, monkeypatch):  # noqa: F811
    # a slow machine: the press is made inside the delay, handled after it
    import time

    from textual import events
    monkeypatch.setattr(ConfirmScreen, "ARM_DELAY", 0.3)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        await pilot.click("#svc-stop")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ConfirmScreen)
        early = events.Key("y", "y")
        early.time = screen._armed_at - 0.1                   # made inside the delay
        while time.monotonic() < screen._armed_at + 0.05:   # ... handled after it
            await pilot.pause(0.05)
        screen.post_message(early)
        await pilot.pause()
        await screen.run_action("answer(True)")
        await settle(app, pilot)
        assert isinstance(app.screen, ConfirmScreen)
        assert not [c for c in fake_systemctl if c[0] == "action"]
        await pilot.press("y")                               # a press made now confirms
        await settle(app, pilot)
        assert [c for c in fake_systemctl if c[0] == "action"] == [("action", "dry-run", "stop")]


async def test_a_click_made_too_early_but_handled_late_cant_confirm(paths, fake_systemctl, monkeypatch):  # noqa: F811
    # Textual rebuilds a mouse event (new time) before the screen sees it: the app keeps the real one
    import time

    from textual import events
    monkeypatch.setattr(ConfirmScreen, "ARM_DELAY", 0.3)
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        await pilot.click("#svc-stop")
        await pilot.pause()
        screen = app.screen
        b = screen.query_one("#confirm-yes", Button)
        x, y = b.region.x + 2, b.region.y + 1
        made = screen._armed_at - 0.1                         # inside the delay
        args = (None, x, y, 0, 0, 1, False, False, False)
        clicks = [events.MouseDown(*args, screen_x=x, screen_y=y), events.MouseUp(*args, screen_x=x, screen_y=y),
                  events.Click(*args, screen_x=x, screen_y=y)]
        for e in clicks:
            e.time = made
        while time.monotonic() < screen._armed_at + 0.05:   # ... handled after it
            await pilot.pause(0.05)
        for e in clicks:
            app.post_message(e)
        await settle(app, pilot)
        assert isinstance(app.screen, ConfirmScreen)
        assert not [c for c in fake_systemctl if c[0] == "action"]
        assert "Too quick" in str(screen.query_one("#confirm-wait").render())
        await pilot.pause(0.3)                                 # the rejected press's active effect ends
        await pilot.click("#confirm-yes")                      # a click made now confirms
        await settle(app, pilot)
        assert [c for c in fake_systemctl if c[0] == "action"] == [("action", "dry-run", "stop")]


@pytest.mark.parametrize("theme", ["textual-dark", "textual-light"])
async def test_pressed_buttons_stay_readable(paths, fake_systemctl, theme):  # noqa: F811
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        app.theme = theme
        await settle(app, pilot)
        for svc in ("#svc-stop", "#svc-restart"):              # destructive and warning variants
            await pilot.click(svc)
            await pilot.pause()
            for sel in ("#confirm-yes", "#confirm-no"):
                b = app.screen.query_one(sel, Button)
                b.add_class("-active")                         # how a press looks for 0.2 s
                await pilot.pause()
                # Textual's 30 % tint drops the text under 4.5:1; without it the pressed
                # button looks like the unpressed one, whose contrast is tested above
                assert b.styles.tint.a == 0, (theme, svc, sel)
                if b.variant == "error":
                    bg = b.styles.background
                    assert _ratio(bg + b.styles.color, bg) >= 4.5, (theme, svc, sel)
                b.remove_class("-active")
            await pilot.press("escape")
            await settle(app, pilot)
