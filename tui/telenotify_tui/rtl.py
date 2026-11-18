"""Right-to-left text (Arabic, Persian, Hebrew) drawn the right way round.

Textual draws every line left to right, one character per cell, in the
order the characters are stored. Arabic is stored in reading order (first
word first), so drawn as is a sentence comes out with its words reversed,
and the letters don't join. Most terminals don't fix that up: Alacritty
(and Zed's terminal, built on it), kitty and xterm do no bidi at all, and
VTE (GNOME Terminal, Ptyxis) only does when it isn't told otherwise.

So the screen does it itself, the way a terminal app has to (see the
Terminal-WG BiDi recommendation, https://terminal-wg.pages.freedesktop.org/bidi/):

* Letters are shaped into their joined forms (Arabic presentation forms,
  `arabic_reshaper`), so terminals that don't shape still join them.
* Each line is put in visual order with the Unicode bidi algorithm
  (`python-bidi`), the paragraph's direction taken from its first strong
  letter. Lines are broken first and reordered after: reordering a whole
  paragraph and then wrapping it would put its lines in the wrong order.
  Paragraphs that read right to left are right-aligned, and brackets in
  right-to-left text are mirrored, so "(عاجل)" doesn't show as ")عاجل(".
* At start the terminal is switched to explicit mode (`CSI 8 l`, the
  ECMA-48 / Terminal-WG "BDSM" mode), so terminals with their own bidi
  (VTE) don't reverse the text a second time; implicit mode (`CSI 8 h`,
  the Terminal-WG default) is restored on exit. Terminals that don't know
  the mode ignore both. VTE keeps shaping Arabic in explicit mode, and
  text that is already shaped stays as it is.

`TELENOTIFY_BIDI=off` (or `telenotify-tui --no-bidi`) turns all of this
off: text is drawn as stored and the terminal's mode is left alone, for a
terminal that does its own bidi well.

Text from outside is made safe here as well: `clean` drops control
characters, and the bidi embedding, override and isolate controls
(U+202A-U+202E, U+2066-U+2069) are dropped too, so a post can't reorder
anything but its own letters; the direction marks (U+200E, U+200F,
U+061C) steer the algorithm and are then dropped, so they never reach the
terminal either.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TextIO
from unicodedata import bidirectional

import arabic_reshaper
from bidi import get_base_level, get_display
from bidi.mirror import MIRRORED
from rich.cells import cell_len
from rich.console import Console, ConsoleOptions, RenderResult
from rich.measure import Measurement
from rich.style import StyleType
from rich.text import Text

from .clean import clean

EXPLICIT = "\x1b[8l"   # BDSM reset: the terminal leaves the order alone
IMPLICIT = "\x1b[8h"   # BDSM set: the terminal reorders (the Terminal-WG default)

# embeddings, overrides and isolates: they would reorder text around them
_CONTROLS = re.compile("[\u202a-\u202e\u2066-\u2069]")
# direction marks: invisible hints for the algorithm
_MARKS = re.compile("[\u200e\u200f\u061c]")
# letters (and Arabic digits and marks) from the right-to-left scripts
_RTL = re.compile("[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufefc\U00010800-\U00010fff\U0001e800-\U0001efff]")
_OFF = ("off", "0", "no", "false")
_LEVEL = re.compile(r"Level\(\s*(\d+),?\s*\)")
# whitespace (and invisible format characters) that rule L1 puts back at the paragraph's level
_L1_SPACE = frozenset(("WS", "FSI", "LRI", "RLI", "PDI", "BN", "LRE", "RLE", "LRO", "RLO", "PDF"))


def env_enabled() -> bool:
    """False when $TELENOTIFY_BIDI says off."""
    return os.environ.get("TELENOTIFY_BIDI", "").strip().lower() not in _OFF


_enabled = env_enabled()


def enabled() -> bool:
    return _enabled


def set_enabled(on: bool) -> None:
    global _enabled
    _enabled = bool(on)


def safe(s: object) -> str:
    """`s` without control characters or bidi embedding / override / isolate controls."""
    return _CONTROLS.sub("", clean(s))


def has_rtl(s: str) -> bool:
    return _RTL.search(s) is not None


def _base(paragraph: str) -> str:
    """'R' or 'L': the paragraph's direction, from its first strong letter (LTR if none)."""
    try:
        return "R" if get_base_level(paragraph) == 1 else "L"
    except ValueError:   # nothing in it
        return "L"


def _levels(line: str, base: str) -> list[int] | None:
    """The embedding level of each character of `line`, as python-bidi
    resolved it, or None if they can't be read. python-bidi (its Rust
    unicode-bidi core) reports them only in its debug output, one per UTF-8
    byte; `_reorder` checks the result against `get_display`."""
    dbg = get_display(line, base_dir=base, debug=True)
    start = dbg.find("levels: [")
    end = dbg.find("paragraphs:", start)
    if start < 0 or end < 0:
        return None
    per_byte = [int(x) for x in _LEVEL.findall(dbg, start, end)]
    if len(per_byte) != len(line.encode("utf-8")):
        return None
    out, at = [], 0
    for ch in line:
        out.append(per_byte[at])
        at += len(ch.encode("utf-8"))
    return out


def _reorder(line: str, base: str) -> str:
    """`line` in visual order, with brackets and other paired signs mirrored
    where they read right to left (rule L4, which python-bidi's
    `get_display` leaves out: "(urgent)" in Arabic would show as ")urgent(").
    Rules L1 and L2 are done here, from python-bidi's levels; if the order
    doesn't come out the same as `get_display`'s, that is used as is."""
    plain = get_display(line, base_dir=base)
    levels = _levels(line, base)
    if not levels:
        return plain
    para = 1 if base == "R" else 0
    # L1: separators, and whitespace before them or at the end, at the paragraph's level
    trailing = True
    for i in range(len(line) - 1, -1, -1):
        cls = bidirectional(line[i])
        if cls in ("S", "B"):
            levels[i], trailing = para, True
        elif cls in _L1_SPACE:
            if trailing:
                levels[i] = para
        else:
            trailing = False
    # L2: from the highest level to the lowest odd one, reverse every run at that level or higher
    order = list(range(len(line)))
    odd = [lv for lv in levels if lv % 2]
    for level in range(max(levels), (min(odd) if odd else max(levels) + 1) - 1, -1):
        i = 0
        while i < len(order):
            if levels[order[i]] < level:
                i += 1
                continue
            j = i
            while j < len(order) and levels[order[j]] >= level:
                j += 1
            order[i:j] = order[i:j][::-1]
            i = j
    if "".join(line[i] for i in order) != plain:
        return plain
    return "".join(MIRRORED.get(line[i], line[i]) if levels[i] % 2 else line[i] for i in order)


def _visual(line: str, base: str | None = None) -> str:
    """One line (no newline in it) in the order it is drawn. `base` is 'L',
    'R' or None (from the first strong letter)."""
    if not _enabled or not has_rtl(line):
        return _MARKS.sub("", line)
    shaped = arabic_reshaper.reshape(line)
    return _MARKS.sub("", _reorder(shaped, base or _base(shaped)))


def visual(s: object) -> str:
    """A short value set into a line of the screen (a chat title, a role, a
    keyword, a table cell): safe, and each of its lines in visual order.
    The line around it stays left to right."""
    return "\n".join(_visual(line) for line in safe(s).split("\n"))


class Block:
    """A block of outside text (a post, the text the filter read), wrapped to
    the width it is drawn at: each paragraph in its own direction, broken
    into lines first and then reordered line by line, right-to-left
    paragraphs right-aligned. Redrawn at the new width on resize.

    `head`, if given, is drawn above it as is (labels, with any outside
    values in it already passed through `visual`).
    `str(block)` is the whole thing in reading order."""

    def __init__(self, text: object, style: StyleType = "", head: Text | None = None) -> None:
        self.text = safe(text).expandtabs(8)
        self.style = style
        self.head = head
        self._lines: dict[tuple[int, bool], list[tuple[str, int]]] = {}   # Textual asks more than once per size

    def __str__(self) -> str:
        return (self.head.plain if self.head is not None else "") + self.text

    def __repr__(self) -> str:
        return f"Block({str(self)!r})"

    def lines(self, console: Console, width: int) -> list[tuple[str, int]]:
        """(visual text, cells of padding on its left) for each line of the text at `width`."""
        width = max(1, width)
        key = (width, _enabled)
        if key not in self._lines:
            self._lines = {key: self._layout(console, width)}
        return self._lines[key]

    def _layout(self, console: Console, width: int) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for para in self.text.split("\n"):
            if not para.strip():
                out.append(("", 0))
                continue
            rtl = _enabled and has_rtl(para)
            if not rtl:
                out.extend((_MARKS.sub("", line.plain.rstrip()), 0)
                           for line in Text(para).wrap(console, width, overflow="fold"))
                continue
            shaped = arabic_reshaper.reshape(para)
            base = _base(shaped)
            for line in Text(shaped).wrap(console, width, overflow="fold"):
                vis = _MARKS.sub("", _reorder(line.plain.strip(), base))
                out.append((vis, max(0, width - cell_len(vis)) if base == "R" else 0))
        return out

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        if self.head is not None:
            yield self.head
        rendered = Text(no_wrap=True, overflow="crop")
        for i, (vis, pad) in enumerate(self.lines(console, options.max_width)):
            if i:
                rendered.append("\n")
            rendered.append(" " * pad)
            rendered.append(vis, style=self.style)
        yield rendered

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        parts = [Text(self.text)] + ([self.head] if self.head is not None else [])
        sizes = [Measurement.get(console, options, p) for p in parts]
        return Measurement(max(m.minimum for m in sizes), max(m.maximum for m in sizes))


@contextmanager
def terminal_mode(stream: TextIO | None = None) -> Iterator[None]:
    """While the screen runs, the terminal in explicit bidi mode (it doesn't
    reorder what the screen already put in order); implicit mode again
    afterwards, whatever way the screen ends. Does nothing when bidi is off
    or `stream` (default: stderr, which Textual draws on) isn't a terminal."""
    out = sys.__stderr__ if stream is None else stream
    try:
        active = _enabled and out is not None and out.isatty()
    except (OSError, ValueError):
        active = False
    if not active:
        yield
        return
    out.write(EXPLICIT)
    out.flush()
    try:
        yield
    finally:
        try:
            out.write(IMPLICIT)
            out.flush()
        except (OSError, ValueError):
            pass
