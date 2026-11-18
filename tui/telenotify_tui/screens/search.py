"""Search screen (placeholder: being built)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class SearchPane(Vertical):
    live = False  # reload every few seconds while visible

    def compose(self) -> ComposeResult:
        yield Static("Search: coming soon", classes="muted")

    def reload(self) -> None:
        pass
