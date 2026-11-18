"""clean(): text from outside never carries terminal control characters."""

from __future__ import annotations

from pathlib import Path

import pytest

from telenotify_tui.clean import clean


@pytest.mark.parametrize("raw, shown", [
    ("before\x1b]0;PWNED\x1b\\after", "before]0;PWNED\\after"),        # OSC: set the window title
    ("a\x1b[?1049lb", "a[?1049lb"),                                     # leave the alternate screen
    ("T\x1b[41mRED\x1b[0m", "T[41mRED[0m"),                             # CSI colours
    ("x\x9b31my\x9d0;t\x9c", "x31my0;t"),                               # C1 CSI / OSC / ST
    ("bell\x07 bs\x08 cr\r del\x7f nul\x00", "bell bs cr del nul"),
    ("line 1\n\tline 2", "line 1\n\tline 2"),                           # newline and tab stay
])
def test_control_characters_are_dropped(raw, shown):
    assert clean(raw) == shown


def test_arabic_and_bidi_marks_are_kept():
    s = "مطلوب مهندس كهرباء \u202bالبصرة\u202c \u2067x\u2069 \u200f"
    assert clean(s) == s


def test_non_text_is_made_text():
    assert clean(Path("/tmp/a\x1bb")) == "/tmp/ab"
    assert clean(42) == "42"


def test_matches_the_nim_printable_rule():
    """Every code point below U+00A0: kept exactly when Nim's printable() keeps it."""
    for c in range(0xA0):
        keep = c in (0x0A, 0x09) or (c >= 0x20 and c != 0x7F and not 0x80 <= c <= 0x9F)
        assert clean(chr(c)) == (chr(c) if keep else ""), hex(c)
