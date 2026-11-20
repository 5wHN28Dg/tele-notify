"""Confirmation dialogs that a press carried over from the step before can't
confirm (policy S2): a held key, a double click landing where the opening button
was, or a press made too early and handled late on a slow machine.

A confirmation needs a deliberate input made after the dialog was first drawn
plus ARM_DELAY:

- a key carries the time it was read and reaches this screen before any binding or
  the focused button acts on it, so every confirming key is decided here;
- a mouse event is rebuilt (with a new time) before a widget sees it, so the app
  records each click on a widget with the time its press was made
  (TeleNotifyApp.press_made_at); a press on the action confirms only if a click on
  it was made after the delay, and with no click on record it's refused."""

from __future__ import annotations

import time

from textual import events
from textual.widgets import Button, Static

CONFIRM_KEYS = ("enter",)   # Textual buttons answer enter, not space


class Armed:
    """Mixin for a ModalScreen with a `#confirm-wait` Static, a binding `y` that
    answers yes and a confirming button with id YES_ID."""

    ARM_DELAY = 0.5
    YES_ID = "yes"
    _armed_at = float("inf")          # until the first paint

    def _arm(self) -> None:
        self.call_after_refresh(self._set_armed)            # type: ignore[attr-defined]

    def _set_armed(self) -> None:
        self._armed_at = time.monotonic() + self.ARM_DELAY

    def _wait_note(self) -> None:
        self.query_one("#confirm-wait", Static).update(  # type: ignore[attr-defined]
            "Too quick: press again to confirm.")

    def on_key(self, event: events.Key) -> None:
        """Every confirming key is decided here, by its own time: `y`, or enter on
        the action button (which then never reaches the button)."""
        on_action = event.key in CONFIRM_KEYS and getattr(self.app.focused, "id", None) == self.YES_ID  # type: ignore[attr-defined]
        if not (event.key == "y" or on_action):
            return
        event.prevent_default()
        event.stop()
        if event.time < self._armed_at:
            self._wait_note()
        else:
            self._answer(True)

    def _too_soon(self, button: Button) -> bool:
        """For a mouse press on the action: confirm only if the app saw a click on it made
        after the delay (the newest; the button's record is then cleared). A press with no
        click on record is refused (fail closed), except headless (see below)."""
        clicks = getattr(self.app, "press_made_at", {}).pop(button, None)  # type: ignore[attr-defined]
        if self.ARM_DELAY <= 0:
            return False
        if clicks:
            made = clicks[-1]
        elif getattr(self.app, "is_headless", False):  # type: ignore[attr-defined]
            # Textual's test pilot (tests, the audit harness) hands clicks straight to the
            # screen, past the app; headless there's no terminal and no stall, so now is when
            made = time.monotonic()
        else:
            made = float("-inf")
        if made >= self._armed_at:
            return False
        button.remove_class("-active")   # Textual ignores a press while it shows the last one
        self._wait_note()
        return True

    def _answer(self, yes: bool) -> None:
        if self is self.app.screen:                          # type: ignore[attr-defined]
            self.dismiss(yes)                                # type: ignore[attr-defined]
