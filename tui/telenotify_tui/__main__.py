"""python -m telenotify_tui --config:PATH [--dry-run] [--no-bidi]"""

from __future__ import annotations

import argparse
import sys

from . import rtl
from .app import TeleNotifyApp
from .paths import resolve


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    # accept the bot's own style too: --config:FILE
    argv = [a.replace("--config:", "--config=", 1) if a.startswith("--config:") else a for a in argv]
    p = argparse.ArgumentParser(prog="telenotify-tui", description="tele-notify terminal screen")
    p.add_argument("--config", default="config.json", help="the bot's config.json")
    p.add_argument("--dry-run", action="store_true", help="show the dry run's state instead")
    p.add_argument("--no-bidi", action="store_true",
                   help="draw right-to-left text as stored and leave the terminal's own bidi alone "
                        "(the same as TELENOTIFY_BIDI=off)")
    a = p.parse_args(argv)
    rtl.set_enabled(rtl.env_enabled() and not a.no_bidi)
    app = TeleNotifyApp(resolve(a.config, a.dry_run))
    with rtl.terminal_mode():
        app.run()


if __name__ == "__main__":
    main()
