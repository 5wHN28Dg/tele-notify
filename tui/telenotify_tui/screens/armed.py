"""Confirmation dialogs that a press carried over from the step before can't
confirm (policy S2): a held key, a double click landing where the opening button
was, or a press made too early and handled late on a slow machine.

A press counts by when it was *made* (the event's own time), against the moment
the dialog was first drawn plus ARM_DELAY."""

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
        made = self._input_at if self._input_at is not None else time.monotonic()
        self._input_at = None
        if made >= self._armed_at:
            return False
        self.query_one("#confirm-wait", Static).update(  # type: ignore[attr-defined]
            "Too quick: press again to confirm.")
        return True
