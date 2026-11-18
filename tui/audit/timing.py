"""R7 timing: the scripted interaction on the fixed large state file, with
asyncio's debug mode logging every callback that holds the event loop.

Each run is a fresh child process (`python -m audit.child timing`), so no
run reuses another's counted numbers. The result per lock kind is the median,
over the runs, of each run's longest stall. The machine's load is recorded
before and after each run; nothing is stopped to make it quieter.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

from . import appsrc, data, evidence, fakes, locks
from .sandbox import Sandbox

SLOW = 0.05          # log every callback over 50 ms
SEARCHES = ("مهندس", "python", "example.com")
PERIODS = ("today", "7d", "30d", "month", "year", "all")
_TOOK = re.compile(r"took (\d+(?:\.\d+)?) seconds")


# ------------------------------------------------------------------ child

class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.stalls: list[dict] = []
        self.step = "start"

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        m = _TOOK.search(msg)
        if m:
            self.stalls.append({"seconds": float(m.group(1)), "step": self.step,
                                "callback": evidence.redact(msg[:300])})


async def _interaction(cfg: dict, log: _Collect) -> dict:
    from textual.widgets import DataTable, Select, Static

    from telenotify_tui.app import TeleNotifyApp
    from telenotify_tui.paths import resolve

    from . import drive

    loop = asyncio.get_running_loop()
    loop.slow_callback_duration = SLOW
    started = time.monotonic()
    app = TeleNotifyApp(resolve(cfg["config"], cfg["dry_run"]))
    steps: list[dict] = []
    async with drive.run_test(app, (120, 40)) as pilot:
        first = None
        while time.monotonic() - started < 60:
            await pilot.pause(0.05)
            if str(app.query_one("#totals", Static).content).strip():
                first = round(time.monotonic() - started, 3)
                break

        async def step(name: str, *keys: str) -> None:
            log.step = name
            t = time.monotonic()
            for k in keys:
                await pilot.press(k)
            await drive.settle(app, pilot, timeout=60)
            steps.append({"step": name, "seconds": round(time.monotonic() - t, 3)})

        for tab in "2341":
            await step(f"switch to tab {tab}", tab)
        await step("review queue: focus", "2")
        app.query_one("#review-list").focus()
        await step("review queue: scroll", *["down"] * 30)
        await step("dashboard", "1")
        app.query_one("#feed", DataTable).focus()
        await step("feed: scroll", *["down"] * 40, *["pagedown"] * 5)
        await step("search tab", "3")
        for q in SEARCHES:
            box = app.query_one("#q")
            box.value = ""
            box.focus()
            await step(f"search: type {q!r} + enter", *list(q), "enter")
        await step("dashboard", "1")
        for p in PERIODS:
            log.step = f"period {p}"
            t = time.monotonic()
            app.query_one("#period", Select).value = p
            await drive.settle(app, pilot, timeout=60)
            steps.append({"step": f"period {p}", "seconds": round(time.monotonic() - t, 3)})
        await step("refresh", "ctrl+r")
    return {"first_tab_seconds": first, "steps": steps}


def child(cfg: dict) -> dict:
    log = _Collect()
    logging.getLogger("asyncio").addHandler(log)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    before = evidence.machine()
    result = asyncio.run(_interaction(cfg, log), debug=True)
    stalls = sorted(log.stalls, key=lambda s: -s["seconds"])
    return {**result, "longest_stall": stalls[0]["seconds"] if stalls else 0.0,
            "stalls_over_100ms": sum(1 for s in stalls if s["seconds"] > 0.1),
            "stalls_over_1s": sum(1 for s in stalls if s["seconds"] > 1.0),
            "worst": stalls[:15], "machine_before": before, "machine_after": evidence.machine()}


# ----------------------------------------------------------------- parent

def collect(sb: Sandbox, runs: int, kinds: list[str], posts: int, compare: Path | None = None) -> dict:
    """`runs` runs per lock kind; with `compare` (another checkout's tui/), its runs are
    interleaved with this one's (A B A B ...) so both see the same machine load."""
    from . import sandbox as sbx

    master, man, built = data.big_state(sb.cache, posts=posts)
    copy = sbx.use_big(sb, master)
    fakes.set_services(sb.root, fakes.services(dry_run="active", real="active"))
    cfg = {"config": str(sb.config), "dry_run": sb.dry_run}
    out = sb.out / "timing"
    apps = {"A": os.environ.get(appsrc.ENV, str(appsrc.HERE))}
    if compare is not None:
        apps["B"] = str(Path(compare).resolve())
    info = {k: appsrc.describe(Path(v)) for k, v in apps.items()}
    results: dict[tuple[str, str], list[dict]] = {}
    for kind in kinds:
        for n in range(runs):
            for label, app_dir in apps.items():
                hold = locks.hold(copy, kind) if kind != "none" else _null()
                env = dict(os.environ, **{appsrc.ENV: app_dir})
                with hold:
                    proc = subprocess.run([sys.executable, "-m", "audit.child", "timing", json.dumps(cfg)],
                                          capture_output=True, text=True, timeout=900, env=env, check=False)
                if proc.returncode:
                    r = {"error": evidence.redact(proc.stderr[-4000:])}
                else:
                    r = json.loads(proc.stdout.strip().splitlines()[-1])
                r.update(lock=kind, run=n + 1, app=label)
                evidence.write_json(out / f"{label}-{kind}-run{n + 1}.json", r)
                results.setdefault((label, kind), []).append(r)
    summary = {"state": {k: man[k] for k in ("posts", "reviews", "chats", "built_at")}, "built_now": built,
               "apps": info, "slow_callback_threshold": SLOW, "runs": runs, "by_app_and_lock": {}}
    for (label, kind), rs in results.items():
        longest = [r["longest_stall"] for r in rs if "longest_stall" in r]
        firsts = [r["first_tab_seconds"] for r in rs if r.get("first_tab_seconds") is not None]
        med = statistics.median(longest) if longest else None
        summary["by_app_and_lock"][f"{label}/{kind}"] = {
            "longest_stall_per_run": longest, "median_longest_stall": med,
            "first_tab_seconds_per_run": firsts, "errors": sum(1 for r in rs if "error" in r),
            "over_1s (R7.1)": med is not None and med > 1.0, "over_100ms (R7.2)": med is not None and med > 0.1,
            "first_tab_over_2s (R7.3)": any(f > 2.0 for f in firsts) or len(firsts) < len(rs),
            "machine_load_per_run": [r.get("machine_before", {}).get("load") for r in rs],
        }
    if compare is not None:
        for kind in kinds:
            a = summary["by_app_and_lock"].get(f"A/{kind}", {}).get("median_longest_stall")
            b = summary["by_app_and_lock"].get(f"B/{kind}", {}).get("median_longest_stall")
            if a is not None and b:
                summary.setdefault("A_vs_B", {})[kind] = {"A": a, "B": b, "change_percent": round((a - b) / b * 100, 1)}
    evidence.write_json(out / "summary.json", summary)
    lines = ["| app | lock | longest stall per run (s) | median | first tab (s) |", "|---|---|---|---|---|"]
    lines += [f"| {k.split('/')[0]} ({info[k.split('/')[0]]['commit']}) | {k.split('/')[1]} | "
              f"{v['longest_stall_per_run']} | {v['median_longest_stall']} | {v['first_tab_seconds_per_run']} |"
              for k, v in summary["by_app_and_lock"].items()]
    evidence.write_text(out / "summary.md", "\n".join(lines) + "\n")
    return {k: v["median_longest_stall"] for k, v in summary["by_app_and_lock"].items()}


class _null:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *a: object) -> None:
        return None
