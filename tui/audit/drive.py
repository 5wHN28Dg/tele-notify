"""Running the screen in-process (headless) inside the sandbox, and taking shots.

Imported only after `sandbox.apply()`: it imports telenotify_tui.
"""

from __future__ import annotations

import asyncio
import traceback
from pathlib import Path

from textual.widget import Widget
from textual.widgets import Input, TextArea

from telenotify_tui.app import TeleNotifyApp
from telenotify_tui.paths import resolve

from . import evidence, svg
from .sandbox import Sandbox

SIZES = {"80x24": (80, 24), "120x40": (120, 40)}
THEMES = {"dark": "textual-dark", "light": "textual-light"}


def make_app(sb: Sandbox, refresh: float = 0) -> TeleNotifyApp:
    """The real app on the sandbox's config. `refresh` 0: no timed reloads (steady shots)."""
    return TeleNotifyApp(resolve(sb.config, sb.dry_run), refresh_seconds=refresh)


def run_test(app, size: tuple[int, int]):
    """app.run_test with notifications and tooltips drawn (run_test hides both by default)."""
    return app.run_test(size=size, notifications=True, tooltips=True)


async def settle(app, pilot, timeout: float = 10) -> bool:
    """Let workers and messages finish. False when a worker was still busy after `timeout`."""
    for _ in range(3):
        await pilot.pause()
        try:
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout)
        except TimeoutError:
            return False
    await pilot.pause()
    return True


def describe(w: Widget | None) -> str:
    if w is None:
        return "(nothing)"
    return type(w).__name__ + (f"#{w.id}" if w.id else "")


def notifications(app) -> list[dict]:
    return [{"severity": n.severity, "title": n.title, "message": n.message} for n in app._notifications]


def no_blink(app) -> None:
    for screen in app.screen_stack:
        for w in screen.query("Input, TextArea"):
            if isinstance(w, Input | TextArea):
                w.cursor_blink = False


async def shot(app, pilot, out: Path, name: str) -> dict:
    """An SVG of the current screen plus its text, with what is focused and shown."""
    no_blink(app)
    await pilot.pause()
    out.mkdir(parents=True, exist_ok=True)
    w, h = app.size
    data = app.export_screenshot()
    (out / f"{name}.svg").write_text(data, encoding="utf-8")
    evidence.write_text(out / f"{name}.txt", svg.text(data, w, h))
    return {"name": name, "size": f"{w}x{h}", "theme": app.theme, "screen": type(app.screen).__name__,
            "focused": describe(app.focused), "notifications": notifications(app)}


def error_text(e: BaseException) -> str:
    return evidence.redact("".join(traceback.format_exception(e)))
