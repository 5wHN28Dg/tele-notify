"""R10 failure conditions: each tab under each condition, simulated in the
sandbox only (temporary files, the fake commands, a lock held by a helper)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sqlite3
import time
from collections.abc import Callable

from textual.widgets import Input

from . import data, drive, evidence, fakes, locks
from . import sandbox as sbx
from .sandbox import Sandbox


def _drop(table: str) -> Callable[[Sandbox], None]:
    def go(sb: Sandbox) -> None:
        db = sqlite3.connect(sb.state)
        db.execute(f"DROP TABLE {table}")
        db.commit()
        db.close()
    return go


def _services(**kw: str) -> Callable[[Sandbox], None]:
    return lambda sb: fakes.set_services(sb.root, fakes.services(**kw))


def _stale(sb: Sandbox) -> None:
    data.small_state(sb.state, dry_run=sb.dry_run, last_alive_ago=2 * 3600)


CONDITIONS: dict[str, tuple[str, Callable[[Sandbox], None]]] = {
    "bot-stopped": ("both services inactive", _services(dry_run="inactive", real="inactive")),
    "bot-failed": ("the service failed", _services(dry_run="failed")),
    "service-missing": ("no such systemd unit", _services(dry_run="missing", real="missing")),
    "never-started": ("no state file and the service inactive",
                      lambda sb: (sb.state.unlink(), _services(dry_run="inactive")(sb))),
    "state-missing": ("no state file, the service says active", lambda sb: sb.state.unlink()),
    **{f"table-missing-{t}": (f"the state file has no {t} table", _drop(t))
       for t in ("processed", "reviews", "meta", "alerts", "chats")},
    "state-locked": ("another process holds an exclusive lock on the state file", lambda sb: None),
    "config-missing": ("config.json missing", lambda sb: sb.config.unlink()),
    "config-invalid": ("config.json is not valid JSON",
                       lambda sb: sb.config.write_text('{"chats": [1, 2,', encoding="utf-8")),
    "config-not-object": ("config.json holds a list", lambda sb: sb.config.write_text("[]", encoding="utf-8")),
    "no-telenotify": ("the telenotify program isn't there", lambda sb: (sb.bin / "telenotify").unlink()),
    # PATH is only the fakes folder here: with /usr/bin on it the real systemctl would be found
    "no-systemctl": ("systemctl isn't on PATH (PATH = the fakes folder only)",
                     lambda sb: (sb.bin / "systemctl").unlink()),
    "status-stale": ("the bot's heartbeat is 2 hours old, the service active", _stale),
}

# one action per tab that touches what may be broken
ACTIONS = {
    "1-dashboard": ("start the bot", ["click:#svc-start", "y"]),
    "2-review": ("show what the filter read", ["r"]),
    "3-search": ("search", ["focus:#q", *list("مهندس"), "enter"]),
    "4-settings": ("test on the corpus", ["focus:#save", "t"]),
}


async def _do(app, pilot, keys: list[str]) -> None:
    for k in keys:
        if k.startswith("click:"):
            await pilot.click(k[6:])
        elif k.startswith("focus:"):
            w = app.query_one(k[6:])
            if isinstance(w, Input):
                w.value = ""
            w.focus()
        else:
            await pilot.press(k)
        await pilot.pause()
    await drive.settle(app, pilot, timeout=15)


async def one(sb: Sandbox, name: str, size: tuple[int, int]) -> dict:
    what, setup = CONDITIONS[name]
    sbx.prepare(sb)
    setup(sb)
    out = sb.out / "failures" / name
    rec: dict = {"condition": name, "what": what, "tabs": []}
    hold = locks.hold(sb.state, "exclusive") if name == "state-locked" else contextlib.nullcontext()
    path = os.environ["PATH"]
    if name == "no-systemctl":
        os.environ["PATH"] = str(sb.bin)
    try:
        with hold:
            app = drive.make_app(sb)
            async with drive.run_test(app, size) as pilot:
                for n, tab in enumerate(ACTIONS, 1):
                    t0 = time.monotonic()
                    try:
                        await pilot.press(str(n))
                        done = await drive.settle(app, pilot, timeout=15)
                        row = await drive.shot(app, pilot, out, tab)
                        row.update(settled=done, seconds=round(time.monotonic() - t0, 2))
                        label, keys = ACTIONS[tab]
                        await _do(app, pilot, keys)
                        after = await drive.shot(app, pilot, out, f"{tab}-after-{label.replace(' ', '-')}")
                        row["after_action"] = {"action": label, **after}
                        await pilot.press("escape")
                        await drive.settle(app, pilot)
                        app.clear_notifications()
                    except Exception as e:  # noqa: BLE001 - evidence
                        row = {"name": tab, "error": drive.error_text(e)}
                    row["app_running"] = app.is_running
                    rec["tabs"].append(row)
            rec["app_exception"] = drive.error_text(app._exception) if app._exception else None
    except Exception as e:  # noqa: BLE001 - the app crashed or the lock couldn't be taken: evidence
        rec["crash"] = drive.error_text(e)
    finally:
        os.environ["PATH"] = path
    for row in rec["tabs"]:
        txt = out / f"{row.get('name', '')}.txt"
        if txt.exists():
            row["still_loading_lines"] = [ln.strip() for ln in txt.read_text(encoding="utf-8").splitlines()
                                          if "loading" in ln.lower() or ln.rstrip().endswith("…")]
    return rec


def collect(sb: Sandbox, names: list[str] | None, size: str) -> dict:
    sbx.set_bidi("full")
    names = names or list(CONDITIONS)
    rows = [asyncio.run(one(sb, n, drive.SIZES[size])) for n in names]
    evidence.write_json(sb.out / "failures" / "index.json", rows)
    return {"conditions": len(rows), "crashes": sum(1 for r in rows if r.get("crash") or r.get("app_exception"))}
