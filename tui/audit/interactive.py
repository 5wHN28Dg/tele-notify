"""Interactive mode: the real screen in your own terminal, on the sandbox.

For the owner's sign-off (policy 3.2): try a branch for a few minutes with
every isolation of 3.3 in force: the real-home guard, a temporary HOME, the
fixed synthetic config and state, fake systemctl (Start / Stop / Restart reach
no real user unit), fake browser / xdg-open / gio / telenotify, and DISPLAY,
WAYLAND_DISPLAY, DBUS_SESSION_BUS_ADDRESS and XDG_RUNTIME_DIR unset (the
screen draws in the terminal, so it needs none of them). Only the terminal's
own description (TERM, COLORTERM, KITTY_WINDOW_ID...) is kept from your
environment, so the screen draws the way it would for you.
"""

from __future__ import annotations

import os
import shutil
import sys

from . import data
from . import sandbox as sbx
from .sandbox import Sandbox

TERMINAL_VARS = ("TERM", "COLORTERM", "KITTY_WINDOW_ID", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "VTE_VERSION")
BANNER = ("tele-notify audit sandbox: SYNTHETIC data, fake systemctl / browser / telenotify; "
          "nothing here touches the live bot. Logs: {root}/log")


def setup(sb: Sandbox, bidi: str | None, big: bool = False,
          caller_env: dict[str, str] | None = None) -> tuple[list[str], dict[str, str]]:
    """Build the sandbox; (the screen's arguments, its whole environment)."""
    caller_env = dict(os.environ if caller_env is None else caller_env)
    sbx.prepare(sb)
    if big:
        master, _man, _built = data.big_state(sb.cache)
        sbx.use_big(sb, master)
    env = sb.env(bidi or "auto")
    if bidi is None:
        del env["TELENOTIFY_BIDI"]          # the screen picks the mode for this terminal
    env.update({k: caller_env[k] for k in TERMINAL_VARS if k in caller_env})
    argv = ["--config", str(sb.config)] + (["--dry-run"] if sb.dry_run else []) + (["--bidi", bidi] if bidi else [])
    return argv, env


def _apply(env: dict[str, str]) -> None:
    import tempfile

    os.environ.clear()
    os.environ.update(env)
    tempfile.tempdir = None


def headless_check(sb: Sandbox, bidi: str | None) -> dict:
    """The same setup, with the screen run headless instead of in the terminal: proves what
    the interactive mode reaches (used by the tests; never draws on a real terminal)."""
    import asyncio

    argv, env = setup(sb, bidi, caller_env={"TERM": "xterm-kitty", "DISPLAY": ":0", "WAYLAND_DISPLAY": "w-0"})
    _apply(env)
    from . import drive, fakes

    async def go() -> list[str]:
        app = drive.make_app(sb)
        async with drive.run_test(app, (120, 40)) as pilot:
            await drive.settle(app, pilot)
            for button in ("#svc-restart", "#svc-stop", "#svc-start"):
                await pilot.click(button)
                await drive.settle(app, pilot)
                await drive.confirm_wait(app, pilot)
                await pilot.press("y")
                await drive.settle(app, pilot)
            return [str(app.sub_title)]

    shown = asyncio.run(go())
    return {"argv": argv, "env_keys": sorted(env), "home": env["HOME"], "path": env["PATH"],
            "sub_title": shown[0],
            "systemctl": [c["argv"] for c in fakes.calls(sb.root, "systemctl") if "is-active" not in c["argv"]]}


def run(sb: Sandbox, bidi: str | None, big: bool = False, size_check: bool = False) -> int:
    cols, rows = shutil.get_terminal_size((0, 0))
    if size_check:
        ok = cols >= 80 and rows >= 24
        print(f"terminal: {cols} x {rows} ({'fits' if ok else 'smaller than'} the 80 x 24 minimum)")
        if not ok:
            return 2
    argv, env = setup(sb, bidi, big)
    print(BANNER.format(root=sb.root), file=sys.stderr)
    _apply(env)
    from telenotify_tui.__main__ import main

    main(argv)
    print(BANNER.format(root=sb.root), file=sys.stderr)
    return 0
