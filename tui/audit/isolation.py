"""Proof that a run only reaches fakes: the environment, where each program
resolves to, and what the fakes logged when the screen opened a link and
started / stopped the bot. Also shows the guard refusing the real places
(by name only: nothing there is opened)."""

from __future__ import annotations

import asyncio
import os
import shutil

from . import drive, evidence, fakes, guard
from . import sandbox as sbx
from .sandbox import Sandbox

UNSET = ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR")


async def _drive(sb: Sandbox) -> list[str]:
    from telenotify_tui.screens.review import ReviewPane

    done = []
    app = drive.make_app(sb)
    async with drive.run_test(app, (120, 40)) as pilot:
        await drive.settle(app, pilot)
        await pilot.press("2")
        await drive.settle(app, pilot)
        await asyncio.sleep(ReviewPane.DECIDE_DELAY + 0.1)
        await pilot.press("o")
        await drive.settle(app, pilot)
        done.append("review: o (open the link)")
        await pilot.press("1")
        await drive.settle(app, pilot)
        for button in ("#svc-stop", "#svc-start"):
            await pilot.click(button)
            await drive.settle(app, pilot)
            await pilot.press("y")
            await drive.settle(app, pilot)
            done.append(f"dashboard: {button[5:]} + y")
    return done


def collect(sb: Sandbox) -> dict:
    sbx.prepare(sb)
    sbx.set_bidi("full")
    done = asyncio.run(_drive(sb))
    home = sb.real_home
    probes = {
        "state": home / ".local" / "share" / "tele-notify" / "state.sqlite3",
        "dry-run state": home / ".local" / "share" / "tele-notify" / "state-dryrun.sqlite3",
        "corpus": home / ".local" / "share" / "tele-notify-corpus" / "eval.jsonl",
        "deployed binary": home / "tele-notify" / "telenotify",
        "production config": home / "Projects" / "bqtechjobs" / "config.json",
    }
    refused = {k: guard.why_forbidden(p, home) is not None for k, p in probes.items()}
    which = {n: shutil.which(n) for n in ("systemctl", "xdg-open", "gio", "telenotify", "firefox", "lynx")}
    root = str(sb.root)
    calls = {n: fakes.calls(sb.root, n) for n in ("systemctl", "browser", "xdg-open", "gio", "telenotify")}
    rec = {
        "actions": done,
        "env": {"HOME": os.environ["HOME"], "PATH": os.environ["PATH"], "BROWSER": os.environ.get("BROWSER"),
                "TELENOTIFY_BIN": os.environ.get("TELENOTIFY_BIN"),
                "unset": {v: v not in os.environ for v in UNSET}},
        "programs": which,
        "programs_inside_sandbox": all((which[n] or "").startswith(root + "/")
                                       for n in ("systemctl", "xdg-open", "gio", "telenotify")),
        "fake_calls": calls,
        "browser_urls": [c.get("url") for c in calls["browser"]],
        "systemctl_actions": [c["argv"] for c in calls["systemctl"] if "is-active" not in c["argv"]],
        "guard_refuses": refused,
    }
    rec["isolated"] = (rec["programs_inside_sandbox"] and all(rec["env"]["unset"].values())
                       and all(refused.values()) and bool(rec["browser_urls"])
                       and len(rec["systemctl_actions"]) == 2)
    evidence.write_json(sb.out / "isolation.json", rec)
    return {"isolated": rec["isolated"]}
