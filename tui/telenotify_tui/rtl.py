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
* `words` (default in kitty). kitty shapes each run of non-space cells on
  its own with HarfBuzz and draws a run of Arabic right to left, brackets
  mirrored, but lays the runs out left to right as stored (its docs, the
  `force_ltr` option: "HELLO WORLD" in an RTL script shows as "WORLD
  HELLO"; and `render_line` / `load_hb_buffer` in kitty/fonts.c). So
  here only the words are put in visual order and each word is written so
  that kitty's own reversal shows it right (see `_words`). Nothing is
  reshaped (kitty joins the letters) and the bidi mode isn't sent (kitty
  doesn't know it). With `force_ltr yes` in kitty.conf, kitty draws cells
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
    TERM=xterm-kitty (ssh passes TERM on) or $KITTY_WINDOW_ID (set in
    kitty's shells, and kept inside a tmux started from one)."""
    env = os.environ if env is None else env
    return "words" if env.get("TERM") == "xterm-kitty" or env.get("KITTY_WINDOW_ID") else "full"


def env_mode(env: Mapping[str, str] | None = None) -> str:
    """$TELENOTIFY_BIDI (full, words or off; off also as 0 / no / false),
    or the terminal's mode when it is unset or says auto."""
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


def _joins_previous(s: str, i: int) -> bool:
    """Whether s[i] belongs to the same grapheme cluster as s[i - 1]: a
    combining mark, a variation selector, a skin tone, a ZWJ or what an
    emoji ZWJ joins, a tag, or the second of a pair of flag letters."""
    ch, o = s[i], ord(s[i])
    if category(ch) in ("Mn", "Me", "Mc") or ch == ZWJ:
        return True
    if 0xfe00 <= o <= 0xfe0f or 0xe0100 <= o <= 0xe01ef or 0x1f3fb <= o <= 0x1f3ff or 0xe0020 <= o <= 0xe007f:
        return True
    if s[i - 1] == ZWJ and (category(ch) == "So" or o >= 0x1f000):
        return True
    if 0x1f1e6 <= o <= 0x1f1ff:   # regional indicators pair up: \U0001f1ee\U0001f1f6
        n = 0
        while i - 1 - n >= 0 and 0x1f1e6 <= ord(s[i - 1 - n]) <= 0x1f1ff:
            n += 1
        return n % 2 == 1
    return False


def _clusters(s: str, start: int, end: int) -> list[tuple[int, int]]:
    """The grapheme clusters of s[start:end], as (start, end) ranges."""
    out: list[tuple[int, int]] = []
    for i in range(start, end):
        if out and _joins_previous(s, i):
            out[-1] = (out[-1][0], i + 1)
        else:
            out.append((i, i + 1))
    return out


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
        if text[i] == "\u0644" and text[i + 1] in _LAM_ALEF and out[i] in (_LAM_INITIAL, _LAM_MEDIAL):
            lig = _LAM_ALEF[text[i + 1]]
            out[i] = lig if out[i] == _LAM_INITIAL else chr(ord(lig) + 1)
            out[i + 1] = ""
    return out


def _kitty_rtl(word: str) -> bool:
    """Whether kitty draws `word` right to left. kitty shapes each run of
    non-space cells on its own with HarfBuzz, which takes the direction
    from the script of the run's first character that isn't Common or
    Inherited (digits, punctuation and marks are skipped); with no such
    character it draws left to right."""
    for ch in word:
        if ch in _ARABIC_COMMON or _MARKS.match(ch):
            continue
        if _RTL.match(ch):
            return True
        if category(ch)[0] == "L":
            return False
    return False


def _mirror(s: str) -> str:
    return MIRRORED.get(s[0], s[0]) + s[1:] if s else s


# ----------------------------------------------------------------- layout

@dataclass
class Piece:
    """Part of a drawn line: `text` as sent to the terminal, standing for
    characters start..end of its paragraph."""
    text: str
    start: int
    end: int


@dataclass
class Line:
    """One line on the screen: `pad` spaces, then the pieces left to right."""
    para: int
    pad: int = 0
    pieces: list[Piece] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " " * self.pad + "".join(p.text for p in self.pieces)


def _wrap(text: str, width: int | None) -> list[tuple[int, int]]:
    """Where `text` (one paragraph) breaks into lines of at most `width`
    cells (None: one line): (start, end) of each, breaking at spaces, or
    between grapheme clusters inside a word too long for a line. The spaces
    at a break belong to no line."""
    clusters = _clusters(text, 0, len(text))
    if width is None or not clusters:
        return [(0, len(text))] if text else [(0, 0)]
    width = max(1, width)
    lines: list[tuple[int, int]] = []
    k = 0
    while k < len(clusters):
        while k < len(clusters) and text[clusters[k][0]] == " " and lines:
            k += 1   # the spaces where the last line broke
        if k == len(clusters):
            break
        first, used, brk, j = k, 0, None, k
        while j < len(clusters):
            a, b = clusters[j]
            w = cell_len(text[a:b])
            if text[a] == " ":
                brk = j
            elif used + w > width:
                break
            used += w
            j += 1
        if j < len(clusters):
            if brk is not None and brk > first:
                j = brk
            elif j == first:
                j = first + 1   # one cluster wider than the line
        end = clusters[j - 1][1]
        while end > clusters[first][0] and text[end - 1] == " ":
            end -= 1
        lines.append((clusters[first][0], end))
        k = j
    return lines or [(0, 0)]


def layout(text: str, width: int | None = None, the_mode: str | None = None) -> list[Line]:
    """`text` (safe already) as the lines drawn at `width` cells (None: one
    line per paragraph, not padded) in the given mode (default: the current
    one)."""
    m = _mode if the_mode is None else the_mode
    out: list[Line] = []
    for n, para in enumerate(text.split("\n")):
        out.extend(_paragraph(para, n, width, m))
    return out


def _paragraph(para: str, n: int, width: int | None, m: str) -> list[Line]:
    spans = _wrap(para, width)
    if m == "off" or not has_rtl(para):   # nothing to reorder: as stored
        return [Line(n, 0, [Piece(_MARKS.sub("", para[a:b]), a, b)]) for a, b in spans]
    base = _base(para)
    levels = _levels(para, base)
    if levels is None:   # python-bidi's levels can't be read: its own order, line by line
        return [Line(n, 0, [Piece(_MARKS.sub("", get_display("".join(_shape(para[a:b])), base_dir=base)),
                                  a, b)]) for a, b in spans]
    shaped = _shape(para) if m == "full" else None
    lines = []
    for a, b in spans:
        a2 = a
        while base == "R" and a2 < b and para[a2] == " ":
            a2 += 1   # an Arabic line's leading spaces would sit on its right edge
        pieces = _line(para, a2, b, levels, base, m, shaped)
        line = Line(n, 0, pieces)
        if base == "R" and width is not None:
            line.pad = max(0, width - cell_len(line.text))
        lines.append(line)
    return lines


def _line(para: str, a: int, b: int, levels: list[int], base: str, m: str,
          shaped: list[str] | None) -> list[Piece]:
    """para[a:b] in visual order (rules L1 and L2 on the line, with the
    paragraph's levels; grapheme clusters kept whole), as pieces."""
    clusters = _clusters(para, a, b)
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

    def drawn(k: int, forms: list[str] | None) -> str:
        s, e = clusters[k]
        t = "".join(forms[s:e]) if forms is not None else para[s:e]
        return _MARKS.sub("", _mirror(t) if lv[k] % 2 else t)

    if m != "words":
        return [Piece(drawn(k, shaped), *clusters[k]) for k in order]

    # words: each run of non-space clusters is one word for kitty
    word_of, w = [], -1
    for k, (s, _e) in enumerate(clusters):
        if para[s] == " " or k == 0 or para[clusters[k - 1][0]] == " ":
            w += 1
        word_of.append(w)
    words: dict[int, list[int]] = {}
    for k in order:
        words.setdefault(word_of[k], []).append(k)
    pieces: list[Piece] = []
    forms = None
    for ks in words.values():
        word = para[clusters[min(ks)][0]:clusters[max(ks)][1]]
        if _kitty_rtl(word):
            # kitty reverses the word and mirrors its brackets: send it so that shows the visual order
            pieces += [Piece(_mirror(drawn(k, None)), *clusters[k]) for k in reversed(ks)]
        elif has_rtl(word):
            # kitty draws it left to right and joins the letters for that direction: join them here
            forms = forms or _shape(para)
            pieces += [Piece(drawn(k, forms), *clusters[k]) for k in ks]
        else:
            pieces += [Piece(drawn(k, None), *clusters[k]) for k in ks]
    return pieces


def visual(s: object) -> str:
    """A short value set into a line of the screen (a chat title, a role, a
    keyword, a table cell): safe, and each of its lines in visual order.
    The line around it stays left to right."""
    return "\n".join(line.text for line in layout(safe(s)))


def logical(text: str, lines: list[Line], picks: list[tuple[int, int, int]]) -> str:
    """The text, in reading order, under a selection of drawn `lines`:
    `picks` holds (line index, first column, end column) for each selected
    line. Each paragraph gives the stretch from its first to its last
    selected character."""
    paras = text.split("\n")
    got: dict[int, list[int]] = {}
    for y, x0, x1 in picks:
        line = lines[y]
        x = line.pad
        for p in line.pieces:
            if p.text and x < x1 and x + len(p.text) > x0:
                got.setdefault(line.para, []).extend((p.start, p.end))
            x += len(p.text)
    return "\n".join(paras[n][min(v):max(v)] for n, v in sorted(got.items()))


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
