"""R13 task cost: one keyboard script and one mouse script per main task of
the Search and Settings tabs (tab table in tui/README.md).

A script drives the screen from a fresh start (120 x 40, synthetic data)
through a `Counter`, which counts every key and every click: typed text
counts as one key, waits (settling, DECIDE_DELAY) count as nothing. Each
script ends with its task's end-state check; a script that doesn't reach it
is a fail. The table (tasks/costs.md) is meant to be diffed between main and
a branch: `python -m audit tasks --app <checkout>/tui --out DIR`.

A PR that changes a task's path updates its script here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .. import drive, evidence
from .. import sandbox as sbx
from ..sandbox import Sandbox


class TaskFailed(Exception):
    """The script didn't reach its task's end state."""


class Counter:
    def __init__(self, app, pilot, sb: Sandbox):
        self.app, self.pilot, self.sb = app, pilot, sb
        self.keys = 0
        self.clicks = 0
        self.log: list[str] = []

    async def settle(self) -> None:
        await drive.settle(self.app, self.pilot)

    async def key(self, *keys: str) -> None:
        for k in keys:
            self.keys += 1
            self.log.append(f"key {k}")
            await self.pilot.press(k)
            await self.pilot.pause()
        await self.settle()

    async def type(self, text: str) -> None:
        """Typed text: one key, whatever its length."""
        self.keys += 1
        self.log.append(f"type {text!r}")
        await self.pilot.press(*list(text))
        await self.settle()

    async def click(self, target, offset: tuple[int, int] = (0, 0), what: str = "") -> None:
        self.clicks += 1
        self.log.append(f"click {what or target}{'' if offset == (0, 0) else f' at {offset}'}")
        await self.pilot.click(target, offset=offset)
        await self.settle()

    async def wait(self, seconds: float) -> None:
        """A pause (not counted): debounce timers, DECIDE_DELAY."""
        await asyncio.sleep(seconds)
        await self.settle()

    async def tab_to(self, done: Callable[[], bool], limit: int = 25) -> None:
        """tab until `done()` (each tab is a key)."""
        while not done():
            if limit == 0:
                raise TaskFailed("tab never reached the control")
            limit -= 1
            await self.key("tab")

    def focused_id(self) -> str | None:
        f = self.app.focused
        return f.id if f is not None else None


Script = Callable[[Counter], Awaitable[None]]


@dataclass
class Task:
    tab: str
    name: str
    keyboard: Script
    mouse: Script
    check: Callable[[Counter], str]     # raises TaskFailed; returns what it found


async def _run(sb: Sandbox, task: Task, how: str) -> dict:
    sbx.prepare(sb)
    app = drive.make_app(sb)
    async with drive.run_test(app, (120, 40)) as pilot:
        await drive.settle(app, pilot)
        c = Counter(app, pilot, sb)
        try:
            await getattr(task, how)(c)
            await c.settle()
            found = task.check(c)
            ok, detail = True, found
        except Exception as e:  # noqa: BLE001 - a script that fails is the evidence
            ok, detail = False, (str(e) if isinstance(e, TaskFailed) else drive.error_text(e))
        return {"keys": c.keys, "clicks": c.clicks, "reached_end_state": ok, "detail": detail, "log": c.log}


def all_tasks() -> list[Task]:
    from . import search, settings
    return search.TASKS + settings.TASKS


def collect(sb: Sandbox, only: list[str] | None) -> dict:
    sbx.set_bidi("full")
    rows = []
    for t in all_tasks():
        if only and not any(o in t.name for o in only):
            continue
        r = {"tab": t.tab, "task": t.name}
        for how in ("keyboard", "mouse"):
            r[how] = asyncio.run(_run(sb, t, how))
        rows.append(r)
    evidence.write_json(sb.out / "tasks" / "costs.json", rows)
    lines = ["| tab | task | keyboard: keys | keyboard: clicks | mouse: keys | mouse: clicks | end state reached |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        k, m = r["keyboard"], r["mouse"]
        reached = "both" if k["reached_end_state"] and m["reached_end_state"] else \
            "keyboard only" if k["reached_end_state"] else "mouse only" if m["reached_end_state"] else "neither"
        lines.append(f"| {r['tab']} | {r['task']} | {k['keys']} | {k['clicks']} | {m['keys']} | {m['clicks']} | "
                     f"{reached} |")
    evidence.write_text(sb.out / "tasks" / "costs.md", "\n".join(lines) + "\n")
    return {"tasks": len(rows), "failed_scripts": sum(1 for r in rows for h in ("keyboard", "mouse")
                                                      if not r[h]["reached_end_state"])}
