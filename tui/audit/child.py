"""Entry points for child processes the collectors start inside the sandbox
(they inherit its environment, $TN_AUDIT_APP names the screen's checkout).

  python -m audit.child app [telenotify-tui arguments]   the screen, as a user starts it
  python -m audit.child timing JSON                       one R7 timing run
  python -m audit.child terminal SPEC                     the screen in a real terminal (audit/terminal.py)
"""

from __future__ import annotations

import json
import os
import sys

from . import appsrc


def main(argv: list[str]) -> int:
    if argv[:1] == ["terminal"] and len(argv) == 2:
        from . import terminal  # builds its own environment from the spec first

        return terminal.screen(argv[1])
    appsrc.use(os.environ.get(appsrc.ENV))
    if argv[:1] == ["app"]:
        from telenotify_tui.__main__ import main as app_main

        appsrc.verify_loaded()

        app_main(argv[1:])
        return 0
    if argv[:1] == ["timing"]:
        from . import timing

        print(json.dumps(timing.child(json.loads(argv[1]))))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
