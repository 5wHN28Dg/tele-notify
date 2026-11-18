"""Right-to-left text (Arabic, Persian, Hebrew) drawn the right way round.

Textual draws every line left to right, one character per cell, in the
order the characters are stored. Arabic is stored in reading order (first
word first), so drawn as is a sentence comes out with its words in the
wrong order. Terminals differ in what they do about it, so there are three
modes (`TELENOTIFY_BIDI` or `telenotify-tui --bidi=`):

* `full` (default outside kitty), for terminals that draw cells as they
  are (Alacritty, xterm) or reorder whole lines themselves (VTE: GNOME
  Terminal, Ptyxis), following the Terminal-WG BiDi recommendation
  (https://terminal-wg.pages.freedesktop.org/bidi/):
  letters are shaped into their joined forms (Arabic presentation forms,
  `arabic_reshaper`) and each line is put in visual order with the Unicode
  bidi algorithm (`python-bidi`), brackets mirrored, so "(عاجل)" doesn't
  show as ")عاجل(". While the screen runs the terminal is in explicit
  mode (`CSI 8 l`, ECMA-48 / Terminal-WG "BDSM"), so VTE doesn't reverse
  the text a second time; implicit mode (`CSI 8 h`, the default) is
  restored on exit. VTE keeps shaping Arabic in explicit mode, and text
  that is already shaped stays as it is.
* `words` (default in kitty). kitty shapes each run of cells between
  spaces and emoji on its own with HarfBuzz and draws a run of Arabic
  right to left, brackets mirrored, but lays the runs out left to right
  as stored (its docs, the `force_ltr` option: "HELLO WORLD" in an RTL
  script shows as "WORLD HELLO"; and `render_line` / `load_hb_buffer` in
  kitty/fonts.c). So here each line is put in visual order, cut into
  kitty's runs, and each run kitty will draw right to left is sent turned
  round so kitty's reversal shows it right (see `_line`). Arabic kitty
  draws right to left isn't reshaped (kitty joins the letters) and the
  bidi mode isn't sent (kitty doesn't know it). With `force_ltr yes` in kitty.conf, kitty draws cells
  as they are: use `full` there.
* `off`: text as stored, the terminal's mode left alone.

In both reordering modes each paragraph takes its direction from its first
strong letter, lines are broken first and reordered after (reordering a
whole paragraph and then wrapping it would put its lines in the wrong
order), and right-to-left paragraphs are right-aligned.

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
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TextIO
from unicodedata import bidirectional, category

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
# lone surrogates (a "\udXXX" escape in config.json): not text, and can't be encoded
_SURROGATES = re.compile("[\ud800-\udfff]")
# line and paragraph separators: a new line, like "\n"
_SEPARATORS = re.compile("\r\n|[\r\u2028\u2029\x85]")
# letters (and Arabic digits and marks) from the right-to-left scripts
_RTL = re.compile("[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufefc\U00010800-\U00010fff\U0001e800-\U0001efff]")
_OFF = ("off", "0", "no", "false")
MODES = ("full", "words", "off")
# Arabic-block characters whose script is Common (comma, semicolon, question mark, tatweel)
_ARABIC_COMMON = frozenset("\u060c\u061b\u061f\u0640")
# BMP characters with emoji presentation (Unicode emoji-data.txt): kitty draws them from an emoji font
_EMOJI_BMP = frozenset(c for a, b in (
    (0x231A, 0x231B), (0x23E9, 0x23EC), (0x23F0, 0x23F0), (0x23F3, 0x23F3), (0x25FD, 0x25FE), (0x2614, 0x2615),
    (0x2648, 0x2653), (0x267F, 0x267F), (0x2693, 0x2693), (0x26A1, 0x26A1), (0x26AA, 0x26AB), (0x26BD, 0x26BE),
    (0x26C4, 0x26C5), (0x26CE, 0x26CE), (0x26D4, 0x26D4), (0x26EA, 0x26EA), (0x26F2, 0x26F3), (0x26F5, 0x26F5),
    (0x26FA, 0x26FA), (0x26FD, 0x26FD), (0x2705, 0x2705), (0x270A, 0x270B), (0x2728, 0x2728), (0x274C, 0x274C),
    (0x274E, 0x274E), (0x2753, 0x2755), (0x2757, 0x2757), (0x2795, 0x2797), (0x27B0, 0x27B0), (0x27BF, 0x27BF),
    (0x2B1B, 0x2B1C), (0x2B50, 0x2B50), (0x2B55, 0x2B55),
) for c in range(a, b + 1))
_LEVEL = re.compile(r"Level\(\s*(\d+),?\s*\)")
# whitespace (and invisible format characters) that rule L1 puts back at the paragraph's level
_L1_SPACE = frozenset(("WS", "FSI", "LRI", "RLI", "PDI", "BN", "LRE", "RLE", "LRO", "RLO", "PDF"))
ZWJ = "\u200d"
TATWEEL = "\u0640"
# lam-alef: the alef after a lam -> (isolated, final) ligature
_LAM_ALEF = {"\u0622": "\ufef5", "\u0623": "\ufef7", "\u0625": "\ufef9", "\u0627": "\ufefb"}
_LAM_INITIAL, _LAM_MEDIAL = "\ufedf", "\ufee0"
_RESHAPER = arabic_reshaper.ArabicReshaper(configuration={
    "delete_harakat": False,     # marks stay, on their letter
    "delete_tatweel": False,
    "support_ligatures": False,  # one shaped character per character; lam-alef is done here
})


# ------------------------------------------------------------------ modes

def terminal_mode_for(env: Mapping[str, str] | None = None) -> str:
    """'words' in kitty, 'full' anywhere else. kitty is known by
    TERM=xterm-kitty (ssh passes TERM on), or by $KITTY_WINDOW_ID inside
    tmux or screen started from kitty. $KITTY_WINDOW_ID alone isn't
    trusted: another terminal started from a kitty shell inherits it."""
    env = os.environ if env is None else env
    term = env.get("TERM", "")
    if term == "xterm-kitty":
        return "words"
    if env.get("KITTY_WINDOW_ID") and term.startswith(("tmux", "screen")):
        return "words"
    return "full"


def bad_env_value(env: Mapping[str, str] | None = None) -> str | None:
    """$TELENOTIFY_BIDI when it is set to something it can't mean, else None."""
    env = os.environ if env is None else env
    v = env.get("TELENOTIFY_BIDI", "").strip().lower()
    return None if v in (*_OFF, *MODES, "auto", "on", "1", "yes", "true", "") else env.get("TELENOTIFY_BIDI")


def env_mode(env: Mapping[str, str] | None = None) -> str:
    """$TELENOTIFY_BIDI (full, words or off; off also as 0 / no / false),
    or the terminal's mode when it is unset, says auto (or on), or is
    something else (`bad_env_value` tells which)."""
    env = os.environ if env is None else env
    v = env.get("TELENOTIFY_BIDI", "").strip().lower()
    if v in _OFF:
        return "off"
    if v in ("full", "words"):
        return v
    return terminal_mode_for(env)


_mode = env_mode()


def mode() -> str:
    return _mode


def set_mode(m: str) -> None:
    global _mode
    if m not in MODES:
        raise ValueError(f"bidi mode must be one of {', '.join(MODES)}, not {m!r}")
    _mode = m


def enabled() -> bool:
    return _mode != "off"


def set_enabled(on: bool) -> None:
    """On means the full mode."""
    set_mode("full" if on else "off")


# ------------------------------------------------------------- characters

def safe(s: object) -> str:
    """`s` made printable (`clean`), without bidi embedding / override /
    isolate controls or lone surrogates, and with every line break a "\\n"."""
    s = _SURROGATES.sub("�", s if isinstance(s, str) else str(s))
    return _CONTROLS.sub("", clean(_SEPARATORS.sub("\n", s)))


def has_rtl(s: str) -> bool:
    return _RTL.search(s) is not None


def _is_mark(ch: str) -> bool:
    return category(ch) in ("Mn", "Me", "Mc")


def _extends(ch: str) -> bool:
    """A character that belongs with the one before it: a combining mark,
    ZWJ, a variation selector, a skin tone, a tag."""
    o = ord(ch)
    return (_is_mark(ch) or ch == ZWJ or 0xfe00 <= o <= 0xfe0f or 0xe0100 <= o <= 0xe01ef
            or 0x1f3fb <= o <= 0x1f3ff or 0xe0020 <= o <= 0xe007f)


def _clusters(s: str) -> list[tuple[int, int]]:
    """The grapheme clusters of `s` (one paragraph), as (start, end) ranges,
    in one pass: a letter with its marks, an emoji with its ZWJ sequence,
    variation selector and skin tone, a pair of flag letters. A space stays
    a cluster of its own: a mark after it starts a new (bare) cluster."""
    out: list[tuple[int, int]] = []
    flags = 0   # regional indicators in a row so far
    for i, ch in enumerate(s):
        o = ord(ch)
        prev = s[i - 1] if i else ""
        is_flag = 0x1f1e6 <= o <= 0x1f1ff
        if not out or prev == " ":
            join = False
        elif _extends(ch):
            join = True
        elif prev == ZWJ:
            join = category(ch) == "So" or o >= 0x1f000
        else:
            join = is_flag and flags % 2 == 1
        flags = flags + 1 if is_flag else 0
        if join:
            out[-1] = (out[-1][0], i + 1)
        else:
            out.append((i, i + 1))
    return out


def _cluster_text(s: str) -> str:
    """A cluster as drawn: a mark with no letter to sit on is shown on a dotted circle."""
    return "\u25cc" + s if s and _is_mark(s[0]) else s


def _is_emoji(s: str) -> bool:
    """Whether kitty draws the cluster from an emoji font rather than the main
    one (which ends its run there): anything with VS16, a flag, or a
    character from the emoji planes or the BMP emoji with emoji presentation."""
    for ch in s:
        o = ord(ch)
        if ch == "\ufe0f" or 0x1f000 <= o <= 0x1faff or o in _EMOJI_BMP:
            return True
    return False


def _base(paragraph: str) -> str:
    """'R' or 'L': the paragraph's direction, from its first strong letter (LTR if none)."""
    try:
        return "R" if get_base_level(paragraph) == 1 else "L"
    except ValueError:   # nothing in it
        return "L"


def _levels(text: str, base: str) -> list[int] | None:
    """The embedding level of each character of `text` (one paragraph), as
    python-bidi resolved it, or None if they can't be read. python-bidi (its
    Rust unicode-bidi core) reports them only in its debug output, one per
    UTF-8 byte; the tests check the order they give against `get_display`."""
    dbg = get_display(text, base_dir=base, debug=True)
    start = dbg.find("levels: [")
    end = dbg.find("paragraphs:", start)
    if start < 0 or end < 0:
        return None
    per_byte = [int(x) for x in _LEVEL.findall(dbg, start, end)]
    if len(per_byte) != len(text.encode("utf-8")):
        return None
    out, at = [], 0
    for ch in text:
        out.append(per_byte[at])
        at += len(ch.encode("utf-8"))
    return out


def _shape(text: str) -> list[str]:
    """Each character of `text` in its joined (presentation) form, one entry
    per character; the alef of a lam-alef becomes "" and the lam the
    ligature. For terminals that don't join Arabic letters themselves."""
    if not has_rtl(text):
        return list(text)
    stand_in = text.replace(ZWJ, TATWEEL)   # the reshaper drops ZWJ; a tatweel joins the same way
    shaped = _RESHAPER.reshape(stand_in)
    if len(shaped) != len(text):
        return list(text)
    out = [ZWJ if c == ZWJ else s for c, s in zip(text, shaped, strict=True)]
    for i in range(len(text) - 1):
        if text[i] == "ل" and text[i + 1] in _LAM_ALEF and out[i] in (_LAM_INITIAL, _LAM_MEDIAL):
            lig = _LAM_ALEF[text[i + 1]]
            out[i] = lig if out[i] == _LAM_INITIAL else chr(ord(lig) + 1)
            out[i + 1] = ""
    return out


def _script(ch: str) -> str | None:
    """'R' or 'L' for a character HarfBuzz takes a run's direction from (its
    script isn't Common or Inherited), None for digits, punctuation, marks."""
    if ch in _ARABIC_COMMON or _MARKS.match(ch):
        return None
    if _RTL.match(ch):
        return "R"
    if category(ch)[0] == "L":
        return "L"
    return None


def _kitty_rtl(run: str) -> bool:
    """Whether kitty draws `run` (cells between spaces or emoji) right to
    left: HarfBuzz takes the direction from the first character whose
    script isn't Common or Inherited; with none it draws left to right."""
    for ch in run:
        d = _script(ch)
        if d is not None:
            return d == "R"
    return False


def _mirror(s: str) -> str:
    return MIRRORED.get(s[0], s[0]) + s[1:] if s else s


# ----------------------------------------------------------------- layout

@dataclass
class Piece:
    """Part of a drawn line: `text` as sent to the terminal, standing for
    characters start..end of the whole text."""
    text: str
    start: int
    end: int


@dataclass
class Line:
    """One line on the screen: `pad` spaces, then the pieces as sent, left
    to right. `cells[i]` is the (start, end) in the whole text of what the
    terminal shows at character i after the pad (kitty reverses some runs,
    so that isn't always pieces' order). lo..hi is the stretch of the text
    the line stands for, the spaces it broke at included."""
    para: int
    pad: int = 0
    pieces: list[Piece] = field(default_factory=list)
    cells: list[tuple[int, int]] = field(default_factory=list)
    lo: int = 0
    hi: int = 0
    rtl: bool = False   # right to left: its right edge is where it starts

    @property
    def text(self) -> str:
        return " " * self.pad + "".join(p.text for p in self.pieces)


def _width(s: str) -> int:
    return cell_len(_cluster_text(s))


def _wrap(text: str, clusters: list[tuple[int, int]], width: int | None) -> list[tuple[int, int]]:
    """Where a paragraph breaks into lines of at most `width` cells (None:
    one line), as (first, end) cluster indexes: at spaces, or between
    clusters inside a word too long for a line. The spaces at a break belong
    to no line."""
    if width is None or not clusters:
        return [(0, len(clusters))]
    width = max(1, width)
    lines: list[tuple[int, int]] = []
    k = 0
    while k < len(clusters):
        while lines and k < len(clusters) and text[clusters[k][0]] == " ":
            k += 1   # the spaces where the last line broke
        if k == len(clusters):
            break
        first, used, brk, j = k, 0, None, k
        while j < len(clusters):
            a, b = clusters[j]
            if text[a] == " ":
                brk = j
            else:
                w = _width(text[a:b])
                if used + w > width:
                    break
            used += 1 if text[a] == " " else _width(text[a:b])
            j += 1
        if j < len(clusters):
            if brk is not None and brk > first:
                j = brk
            elif j == first:
                j = first + 1   # one cluster wider than the line
        end = j
        while end > first and text[clusters[end - 1][0]] == " ":
            end -= 1
        lines.append((first, end))
        k = j
    return lines or [(0, 0)]


def layout(text: str, width: int | None = None, the_mode: str | None = None) -> list[Line]:
    """`text` (safe already) as the lines drawn at `width` cells (None: one
    line per paragraph, not padded) in the given mode (default: the current
    one; "order" is the full mode without shaping, for tests)."""
    m = _mode if the_mode is None else the_mode
    out: list[Line] = []
    at = 0
    for n, para in enumerate(text.split("\n")):
        out.extend(_paragraph(para, n, at, width, m))
        at += len(para) + 1
    return out


def _plain(n: int, at: int, pieces: list[Piece], lo: int, hi: int) -> Line:
    cells = [(p.start, p.end) for p in pieces for _ in p.text]
    return Line(n, 0, pieces, cells, lo, hi)


def _paragraph(para: str, n: int, at: int, width: int | None, m: str) -> list[Line]:
    clusters = _clusters(para)
    spans = _wrap(para, clusters, width)
    # the stretch each line stands for: up to where the next one starts
    starts = [clusters[f][0] if f < len(clusters) else len(para) for f, _e in spans]
    bounds = [(at + (0 if i == 0 else s), at + (starts[i + 1] if i + 1 < len(spans) else len(para)))
              for i, s in enumerate(starts)]
    if m == "off" or not has_rtl(para):   # nothing to reorder: as stored
        return [_plain(n, at, [Piece(_MARKS.sub("", _cluster_text(para[a:b])), at + a, at + b)
                               for a, b in clusters[f:e]], *bounds[i])
                for i, (f, e) in enumerate(spans)]
    base = _base(para)
    levels = _levels(para, base)
    if levels is None:   # python-bidi's levels can't be read: its own order, line by line
        lines = []
        for i, (f, e) in enumerate(spans):
            a = clusters[f][0] if f < e else 0
            b = clusters[e - 1][1] if f < e else 0
            text = _MARKS.sub("", get_display("".join(_shape(para[a:b])), base_dir=base))
            lines.append(_plain(n, at, [Piece(text, at + a, at + b)], *bounds[i]))
        return lines
    shaped: list[list[str]] = []

    def forms() -> list[str]:   # shaped once per paragraph, when first needed
        if not shaped:
            shaped.append(_shape(para))
        return shaped[0]

    lines = []
    for i, (f, e) in enumerate(spans):
        line_clusters = clusters[f:e]
        while base == "R" and line_clusters and para[line_clusters[0][0]] == " ":
            line_clusters = line_clusters[1:]   # an Arabic line's leading spaces would sit on its right edge
        line = _line(para, at, line_clusters, levels, base, m, forms)
        line.para, line.lo, line.hi, line.rtl = n, *bounds[i], base == "R"
        if base == "R" and width is not None:
            line.pad = max(0, width - cell_len(line.text))
        lines.append(line)
    return lines


def _line(para: str, at: int, clusters: list[tuple[int, int]], levels: list[int], base: str, m: str,
          forms) -> Line:
    """A line of a paragraph (its clusters) in visual order: rules L1 and L2
    on the line with the paragraph's levels, grapheme clusters kept whole."""
    para_level = 1 if base == "R" else 0
    lv = [levels[c[0]] for c in clusters]
    trailing = True
    for k in range(len(clusters) - 1, -1, -1):   # L1
        cls = bidirectional(para[clusters[k][0]])
        if cls in ("S", "B"):
            lv[k], trailing = para_level, True
        elif cls in _L1_SPACE:
            if trailing:
                lv[k] = para_level
        else:
            trailing = False
    order = list(range(len(clusters)))
    odd = [x for x in lv if x % 2]
    if lv:
        for level in range(max(lv), (min(odd) if odd else max(lv) + 1) - 1, -1):   # L2
            i = 0
            while i < len(order):
                if lv[order[i]] < level:
                    i += 1
                    continue
                j = i
                while j < len(order) and lv[order[j]] >= level:
                    j += 1
                order[i:j] = order[i:j][::-1]
                i = j

    def drawn(k: int, shaped: list[str] | None) -> str:
        s, e = clusters[k]
        t = "".join(shaped[s:e]) if shaped is not None else para[s:e]
        return _MARKS.sub("", _cluster_text(_mirror(t) if lv[k] % 2 else t))

    def piece(k: int, text: str) -> Piece:
        return Piece(text, at + clusters[k][0], at + clusters[k][1])

    if m != "words":
        pieces = [piece(k, drawn(k, forms() if m == "full" else None)) for k in order]
        return _plain(0, at, pieces, 0, 0)

    # words (kitty): kitty shapes each run of cells between spaces and emoji
    # on its own, so split the visual order there and send each run so that
    # what kitty does with it shows the visual order
    runs: list[list[int]] = []
    for k in order:
        t = para[clusters[k][0]:clusters[k][1]]
        if t == " " or _is_emoji(t):
            runs += [[k], []]
        elif runs and runs[-1]:
            runs[-1].append(k)
        else:
            runs.append([k])
    pieces: list[Piece] = []
    cells: list[tuple[int, int]] = []
    for run in runs:
        if not run:
            continue
        seen = "".join(drawn(k, None) for k in run)   # what should show, left to right
        dirs = [d for d in map(_script, seen) if d]
        if dirs and dirs[0] == "R" and dirs[-1] == "R":
            # kitty draws this run right to left (reversed, brackets mirrored): send it turned round
            pieces += [piece(k, _mirror(drawn(k, None))) for k in reversed(run)]
            cells += [(at + clusters[k][0], at + clusters[k][1]) for k in run for _ in drawn(k, None)]
        else:
            # kitty draws it left to right as sent; join its Arabic letters here
            shaped = forms() if "R" in dirs else None
            out = [piece(k, drawn(k, shaped)) for k in run]
            pieces += out
            cells += [(p.start, p.end) for p in out for _ in p.text]
    return Line(0, 0, pieces, cells)


def visual(s: object) -> str:
    """A short value set into a line of the screen (a chat title, a role, a
    keyword, a table cell): safe, and each of its lines in visual order.
    The line around it stays left to right."""
    return "\n".join(line.text for line in layout(safe(s)))


def logical(text: str, lines: list[Line], picks: list[tuple[int, int, int]]) -> str:
    """The text, in reading order, under a selection of drawn `lines`:
    `picks` holds (line index, first column, end column) for each selected
    line. A line selected to where it ends (the right edge for left-to-right
    text, the left edge for right-to-left) brings the spaces it broke at,
    one selected from where it starts the spaces before it, so blank lines
    and spaces come along."""
    lo, hi = None, None
    for y, x0, x1 in picks:
        line = lines[y]
        width = len(line.text)
        got = [line.cells[x - line.pad] for x in range(max(x0, line.pad), min(x1, width))]
        left, right = (line.hi, line.lo) if line.rtl else (line.lo, line.hi)
        if x0 <= line.pad:
            got.append((left, left))
        if x1 >= width:
            got.append((right, right))
        for a, b in got:
            lo = a if lo is None else min(lo, a)
            hi = b if hi is None else max(hi, b)
    return "" if lo is None else text[lo:hi]


class Block:
    """A block of outside text (a post, the text the filter read), laid out
    for the width it is drawn at: each paragraph in its own direction,
    broken into lines first and then reordered line by line, right-to-left
    paragraphs right-aligned. `PostView` draws it (and copies it back in
    reading order); it can also be drawn as a Rich renderable.

    `head`, if given, is drawn above it as is (labels, with any outside
    values in it already passed through `visual`).
    `str(block)` is the whole thing in reading order."""

    def __init__(self, text: object, style: StyleType = "", head: Text | None = None) -> None:
        self.text = safe(text).expandtabs(8)
        self.style = style
        self.head = head
        self._cache: dict[tuple[int, str], list[Line]] = {}   # Textual asks more than once per size

    def __str__(self) -> str:
        return (self.head.plain if self.head is not None else "") + self.text

    def __repr__(self) -> str:
        return f"Block({str(self)!r})"

    def layout(self, width: int) -> list[Line]:
        key = (max(1, width), _mode)
        if key not in self._cache:
            self._cache = {key: layout(self.text, key[0])}
        return self._cache[key]

    def lines(self, width: int) -> list[tuple[str, int]]:
        """(drawn text, cells of padding on its left) for each line at `width`."""
        return [("".join(p.text for p in line.pieces), line.pad) for line in self.layout(width)]

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        if self.head is not None:
            yield self.head
        rendered = Text(no_wrap=True, overflow="crop")
        for i, line in enumerate(self.layout(options.max_width)):
            if i:
                rendered.append("\n")
            rendered.append(" " * line.pad)
            rendered.append("".join(p.text for p in line.pieces), style=self.style)
        yield rendered

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        parts = [Text(self.text)] + ([self.head] if self.head is not None else [])
        sizes = [Measurement.get(console, options, p) for p in parts]
        return Measurement(max(m.minimum for m in sizes), max(m.maximum for m in sizes))


@contextmanager
def terminal_mode(stream: TextIO | None = None) -> Iterator[None]:
    """While the screen runs, the terminal in explicit bidi mode (it doesn't
    reorder what the screen already put in order); implicit mode again
    afterwards, whatever way the screen ends. Only in the full mode, and
    only when `stream` (default: stderr, which Textual draws on) is a
    terminal: kitty (the words mode) doesn't know the mode and logs an
    error for it."""
    out = sys.__stderr__ if stream is None else stream
    try:
        active = _mode == "full" and out is not None and out.isatty()
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
