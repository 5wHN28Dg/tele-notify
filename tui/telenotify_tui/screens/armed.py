"""Confirmation dialogs that a press carried over from the step before can't
confirm (policy S2): a held key, a double click landing where the opening button
was, or a press made too early and handled late on a slow machine.

A press counts by when it was *made*, against the moment the dialog was first
drawn plus ARM_DELAY. For the mouse that time comes from the app
(TeleNotifyApp.input_at), because Textual rebuilds a mouse event, with a new
time, before it reaches the screen."""

from __future__ import annotations

import time

from textual import events
from textual.widgets import Static


class Armed:
    """Mixin for a ModalScreen with a `#confirm-wait` Static."""

    ARM_DELAY = 0.5
    _armed_at = float("inf")          # until the first paint
    _input_at: float | None = None

    def _arm(self) -> None:
        self.call_after_refresh(self._set_armed)            # type: ignore[attr-defined]

    def _set_armed(self) -> None:
        self._armed_at = time.monotonic() + self.ARM_DELAY

    def on_key(self, event: events.Key) -> None:
        self._input_at = event.time

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._input_at = event.time

    def _too_soon(self) -> bool:
        # the app's time is the press as made (mouse events reaching this screen were
        # rebuilt, with a later time); the screen's own is the fallback for other apps
        times = [t for t in (getattr(self.app, "input_at", None), self._input_at)  # type: ignore[attr-defined]
                 if t is not None]
        made = min(times) if times else time.monotonic()
        self._input_at = None
        if hasattr(self.app, "input_at"):  # type: ignore[attr-defined]
            self.app.input_at = None       # type: ignore[attr-defined]  (used once, never stale)
        if made >= self._armed_at:
            return False
        self.query_one("#confirm-wait", Static).update(  # type: ignore[attr-defined]
            "Too quick: press again to confirm.")
        return True
