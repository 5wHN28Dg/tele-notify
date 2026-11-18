"""python -m telenotify_tui --config:PATH [--dry-run]"""

from __future__ import annotations

import argparse
import sys

from .app import TeleNotifyApp
from .paths import resolve


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    # accept the bot's own style too: --config:FILE
    argv = [a.replace("--config:", "--config=", 1) if a.startswith("--config:") else a for a in argv]
    p = argparse.ArgumentParser(prog="telenotify-tui", description="tele-notify terminal screen")
    p.add_argument("--config", default="config.json", help="the bot's config.json")
    p.add_argument("--dry-run", action="store_true", help="show the dry run's state instead")
    a = p.parse_args(argv)
    TeleNotifyApp(resolve(a.config, a.dry_run)).run()


if __name__ == "__main__":
    main()
