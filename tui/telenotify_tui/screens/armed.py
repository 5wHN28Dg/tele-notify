"""Confirmation dialogs that a press carried over from the step before can't
confirm (policy S2): a held key, a double click landing where the opening button
was, or a press made too early and handled late on a slow machine.

Each press is judged by when *it* was made, against the moment the dialog was
first drawn plus ARM_DELAY:

- a key carries the time it was read, and reaches this screen before any binding
  or the focused button acts on it, so an early confirming key is stopped here;
- a mouse event is rebuilt (with a new time) before a widget sees it, so the app
  records when each press on a widget was made (TeleNotifyApp.press_made_at), and
  a button press reads the time of the press on that button."""

from __future__ import annotations

import time

from textual import events
from textual.widgets import Button, Static

CONFIRM_KEYS = ("y", "enter", "space")


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
        confirming = event.key == "y" or (
            event.key in CONFIRM_KEYS and getattr(self.app.focused, "id", None) == self.YES_ID)  # type: ignore[attr-defined]
        if confirming and event.time < self._armed_at:
            event.prevent_default()
            event.stop()
            self._wait_note()

    def _too_soon(self, button: Button | None = None) -> bool:
        """For a button press: when the mouse press on it was made (the app's record),
        else now (a key, already judged in on_key)."""
        made = None
        if button is not None:
            made = getattr(self.app, "press_made_at", {}).pop(button, None)  # type: ignore[attr-defined]
        if made is None:
            made = time.monotonic()
        if made >= self._armed_at:
            return False
        self._wait_note()
        return True

    def _answer(self, yes: bool) -> None:
        if self is self.app.screen:                          # type: ignore[attr-defined]
            self.dismiss(yes)                                # type: ignore[attr-defined]
