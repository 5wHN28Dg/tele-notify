"""Text from outside (the bot's state, config.json, subprocess output, file
paths) made safe to put on the terminal.

Strangers write the posts the bot stores. A post holding ESC sequences
(OSC titles, CSI colours, "leave the alternate screen") or C1 control
characters would otherwise reach the real terminal through Textual and
could rewrite it. `clean` mirrors the Nim CLI's `printable()` in
src/telenotify/commands.nim: it keeps newline and tab and drops every other
C0 control, DEL and the C1 controls (U+0080-U+009F). Everything else,
Arabic and bidi marks included, is kept as is.
"""

from __future__ import annotations

import re

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def clean(s: object) -> str:
    """`s` (made a string) without control characters other than newline and tab."""
    return _CONTROL.sub("", s if isinstance(s, str) else str(s))
