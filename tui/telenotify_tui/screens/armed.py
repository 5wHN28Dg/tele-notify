"""Confirmation dialogs that a press carried over from the step before can't
confirm (policy S2): a held key, a double click landing where the opening button
was, or a press made too early and handled late on a slow machine.

Each press is judged by when *it* was made, against the moment the dialog was
first drawn plus ARM_DELAY:

- a key carries the time it was read, and reaches this screen before any binding
  or the focused button acts on it, so an early confirming key is stopped here;
- a mouse event is rebuilt (with a new time) before a widget sees it, so the app
  records when each click on a widget was made (TeleNotifyApp.press_made_at: the
  press time, once the release on the same widget made it a click), and a button
  press takes the oldest click on that button."""

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
    _key_press = False                # the next button press comes from an accepted key

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
        if not confirming:
            return
        if event.time < self._armed_at:
            event.prevent_default()
            event.stop()
            self._wait_note()
        else:
            self._key_press = True        # judged here; the press it makes reads no mouse record

    def _too_soon(self, button: Button | None = None) -> bool:
        """For a button press: when the click on it was made (the app's record, oldest
        first), else now. A press from an accepted key was judged in on_key."""
        if self._key_press:
            self._key_press = False
            return False
        made = None
        clicks = getattr(self.app, "press_made_at", {}).get(button) if button is not None else None  # type: ignore[attr-defined]
        if clicks:
            made = clicks.popleft()
        if made is None:
            made = time.monotonic()
        if made >= self._armed_at:
            return False
        self._wait_note()
        return True

    def _answer(self, yes: bool) -> None:
        if self is self.app.screen:                          # type: ignore[attr-defined]
            self.dismiss(yes)                                # type: ignore[attr-defined]
