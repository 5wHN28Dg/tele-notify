"""Dashboard screen (placeholder: being built)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class DashboardPane(Vertical):
    live = False  # reload every few seconds while visible

    def compose(self) -> ComposeResult:
        yield Static("Dashboard: coming soon", classes="muted")

    def reload(self) -> None:
        pass
