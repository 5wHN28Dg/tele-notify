"""python -m telenotify_tui --config:PATH [--dry-run] [--bidi=full|words|off|auto] [--no-bidi]"""

from __future__ import annotations

import argparse
import sys

from . import rtl
from .app import TeleNotifyApp
from .paths import default_config, resolve


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    # accept the bot's own style too: --config:FILE
    argv = [a.replace("--config:", "--config=", 1) if a.startswith("--config:") else a for a in argv]
    p = argparse.ArgumentParser(prog="telenotify-tui", description="tele-notify terminal screen")
    p.add_argument("--config", default=None,
                   help="the bot's config.json (default: $TELENOTIFY_CONFIG, else "
                        "~/.config/tele-notify/config.json, else ./config.json)")
    p.add_argument("--dry-run", action="store_true", help="show the dry run's state instead")
    p.add_argument("--bidi", choices=(*rtl.MODES, "auto"),
                   help="right-to-left text: full (reorder and join letters; most terminals), words (word "
                        "order only; kitty), off (as stored), auto (words in kitty, else full). "
                        "Default: $TELENOTIFY_BIDI, else auto")
    p.add_argument("--no-bidi", action="store_true", help="the same as --bidi=off")
    a = p.parse_args(argv)
    if a.no_bidi:
        rtl.set_mode("off")
    elif a.bidi == "auto":
        rtl.set_mode(rtl.terminal_mode_for())
    elif a.bidi:
        rtl.set_mode(a.bidi)
    else:
        bad = rtl.bad_env_value()
        rtl.set_mode(rtl.env_mode())
        if bad is not None:
            print(f"telenotify-tui: TELENOTIFY_BIDI={bad!r} isn't full, words, off or auto; "
                  f"using {rtl.mode()} for this terminal", file=sys.stderr)
    app = TeleNotifyApp(resolve(a.config if a.config else default_config(), a.dry_run))
    with rtl.terminal_mode():
        app.run()


if __name__ == "__main__":
    main()
