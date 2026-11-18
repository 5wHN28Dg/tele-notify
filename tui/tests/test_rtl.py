"""Right-to-left text: Arabic reads right to left on any terminal, lines
broken before they are reordered, and the terminal's own bidi switched off
while the screen runs (and back on after)."""

from __future__ import annotations

import io
import json
import os
import pty
import re
import select
import signal
import sqlite3
import sys
import time
import unicodedata

import arabic_reshaper
import pytest
from bidi import get_display
from conftest import REPO, SCHEMA, fill
from textual.geometry import Region
from textual.widgets import Static

from telenotify_tui import __main__ as entry
from telenotify_tui import rtl
from telenotify_tui.app import TeleNotifyApp


def vw(word: str) -> str:
    """An Arabic word as it is drawn left to right: joined letters, last letter first."""
    return arabic_reshaper.reshape(word)[::-1]


def lines(text: str, width: int) -> list[tuple[str, int]]:
    return rtl.Block(text).lines(width)


def test_arabic_paragraph_reads_right_to_left_and_is_right_aligned():
    [(shown, pad)] = lines("كيف حالك", 20)
    assert shown == f"{vw('حالك')} {vw('كيف')}"           # the first word on the right
    assert pad == 20 - len(shown)                          # right-aligned
    assert rtl.visual("كيف حالك") == shown                  # a value in a line: same order, not padded


def test_letters_are_joined_for_terminals_that_dont_shape():
    shown = rtl.visual("كيف")
    assert shown == "\N{ARABIC LETTER FEH FINAL FORM}\N{ARABIC LETTER YEH MEDIAL FORM}\N{ARABIC LETTER KAF INITIAL FORM}"
    assert not set(shown) & set("كيف")


def test_mixed_arabic_english_and_numbers():
    # an Arabic paragraph: the English and the number keep their own order inside it
    [(shown, pad)] = lines("خبرة 15 سنوات in Python", 40)
    assert shown == f"in Python {vw('سنوات')} 15 {vw('خبرة')}"
    assert pad == 40 - len(shown)
    # an English paragraph with Arabic in it: left-aligned, the Arabic words in their order
    [(shown, pad)] = lines("Python developer مطلوب في البصرة", 40)
    assert shown == f"Python developer {vw('البصرة')} {vw('في')} {vw('مطلوب')}"
    assert pad == 0


def test_brackets_in_arabic_are_mirrored():
    assert rtl.visual("مطلوب (عاجل)") == f"({vw('عاجل')}) {vw('مطلوب')}"
    assert rtl.visual("Python developer مطلوب (عاجل)") == f"Python developer ({vw('عاجل')}) {vw('مطلوب')}"
    assert rtl.visual("راتب [1000]") == f"[1000] {vw('راتب')}"


def test_wrapped_paragraph_keeps_its_line_order():
    words = ["مطلوب", "مهندس", "كهرباء", "حديث", "التخرج", "للعمل", "في", "البصرة", "براتب", "جيد", "جدا"]
    out = lines(" ".join(words), 16)
    assert len(out) >= 3
    read = []
    for shown, pad in out:
        assert pad + len(shown) == 16                      # every line right-aligned
        read += [w[::-1] for w in reversed(shown.split(" "))]   # each line read from the right
    assert read == [arabic_reshaper.reshape(w) for w in words]  # top line first, nothing reordered across lines


def test_each_paragraph_has_its_own_direction():
    out = lines("مرحبا بكم\nhello there\n\nخبرة 3 سنوات", 30)
    assert [pad > 0 for _s, pad in out] == [True, False, False, True]
    assert out[1] == ("hello there", 0)
    assert out[2] == ("", 0)


def test_block_redraws_at_the_new_width():
    b = rtl.Block("مطلوب مهندس كهرباء حديث التخرج")
    wide = b.lines(60)
    narrow = b.lines(12)
    assert len(wide) == 1 and len(narrow) > 1
    assert all(pad + len(s) == 12 for s, pad in narrow)


def test_off_switch_leaves_text_as_stored():
    rtl.set_mode("off")
    assert rtl.visual("كيف حالك") == "كيف حالك"
    assert lines("كيف حالك", 20) == [("كيف حالك", 0)]


@pytest.mark.parametrize(("env", "mode"), [
    ({}, "full"),
    ({"TERM": "xterm-256color"}, "full"),
    ({"TERM": "xterm-kitty"}, "words"),                       # kitty, or ssh from it
    ({"TERM": "tmux-256color", "KITTY_WINDOW_ID": "3"}, "words"),   # tmux started in kitty
    ({"TERM": "xterm-kitty", "TELENOTIFY_BIDI": "full"}, "full"),
    ({"TELENOTIFY_BIDI": "words"}, "words"),
    ({"TERM": "xterm-kitty", "TELENOTIFY_BIDI": "off"}, "off"),
    ({"TELENOTIFY_BIDI": "OFF "}, "off"),
    ({"TELENOTIFY_BIDI": "0"}, "off"),
    ({"TERM": "xterm-kitty", "TELENOTIFY_BIDI": "auto"}, "words"),
    ({"TELENOTIFY_BIDI": "on"}, "full"),
])
def test_mode_from_terminal_and_env(env, mode):
    assert rtl.env_mode(env) == mode


@pytest.mark.parametrize(("argv", "env", "mode"), [
    ([], {}, "full"),
    ([], {"TERM": "xterm-kitty"}, "words"),
    ([], {"TELENOTIFY_BIDI": "off"}, "off"),
    (["--no-bidi"], {}, "off"),
    (["--no-bidi"], {"TELENOTIFY_BIDI": "full"}, "off"),
    (["--bidi=words"], {}, "words"),
    (["--bidi", "full"], {"TERM": "xterm-kitty"}, "full"),
    (["--bidi=off"], {"TELENOTIFY_BIDI": "full"}, "off"),
    (["--bidi=auto"], {"TERM": "xterm-kitty", "TELENOTIFY_BIDI": "off"}, "words"),   # auto: the terminal decides
])
def test_bidi_flags_override_env(monkeypatch, config, argv, env, mode):
    seen = []

    class FakeApp:
        def __init__(self, paths):
            pass

        def run(self):
            seen.append(rtl.mode())

    for k in ("TERM", "KITTY_WINDOW_ID", "TELENOTIFY_BIDI"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(entry, "TeleNotifyApp", FakeApp)
    entry.main(["--config", str(config), *argv])
    assert seen == [mode]


def test_bad_bidi_flag_is_refused(config, capsys):
    with pytest.raises(SystemExit):
        entry.main(["--config", str(config), "--bidi=sideways"])
    assert "invalid choice" in capsys.readouterr().err


def test_bidi_controls_in_a_post_reorder_nothing():
    # an override would reverse what follows it; marks would reach the terminal
    sneaky = "\u202eSend\u202c \u2067Discard\u2069 \u200fok\u200e \u061c"
    assert rtl.visual(sneaky) == "Send Discard ok "
    assert rtl.visual("\u202eكيف") == vw("كيف")
    for shown, _pad in lines("\u202bكيف \u202eحالك\u202c\u200f\nabc\u2066def", 30):
        assert not set(shown) & set("\u200e\u200f\u061c\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
    # off, the controls are dropped all the same
    rtl.set_enabled(False)
    assert rtl.visual(sneaky) == "Send Discard ok "


def test_escapes_are_still_stripped():
    for s in (rtl.visual("كيف\x1b]0;X\x07 حالك\x9b31m"), rtl.Block("a\x1b[2Jكيف").lines(20)[0][0]):
        assert "\x1b" not in s and "\x07" not in s and "\x9b" not in s


class FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_terminal_mode_explicit_while_running_implicit_after():
    out = FakeTTY()
    with rtl.terminal_mode(out):
        assert out.getvalue() == rtl.EXPLICIT == "\x1b[8l"
    assert out.getvalue() == "\x1b[8l\x1b[8h"


def test_terminal_mode_is_restored_after_a_crash():
    out = FakeTTY()
    with pytest.raises(RuntimeError), rtl.terminal_mode(out):
        raise RuntimeError("boom")
    assert out.getvalue() == "\x1b[8l\x1b[8h"


def test_terminal_mode_left_alone_when_off_or_not_a_terminal():
    out = FakeTTY()
    for m in ("off", "words"):   # words: kitty doesn't know the mode and logs an error for it
        rtl.set_mode(m)
        with rtl.terminal_mode(out):
            pass
    assert out.getvalue() == ""
    rtl.set_enabled(True)
    piped = io.StringIO()
    with rtl.terminal_mode(piped):
        pass
    assert piped.getvalue() == ""


def _read_until(fd: int, out: bytearray, done, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end and not done(out):
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            try:
                out += os.read(fd, 65536)
            except OSError:
                return


def test_real_terminal_gets_explicit_mode_and_it_back(tmp_path):
    """The real app in a pseudo-terminal: explicit mode before the screen is
    drawn, implicit mode again after it closes."""
    data = tmp_path / "data"
    data.mkdir()
    db = sqlite3.connect(data / "state.sqlite3")
    db.executescript(SCHEMA)
    fill(db, int(time.time()))
    db.close()
    cfg = json.loads((REPO / "tests" / "keywords.json").read_text(encoding="utf-8"))
    cfg["data_dir"] = str(data)
    config = tmp_path / "config.json"
    config.write_text(json.dumps(cfg), encoding="utf-8")

    pid, fd = pty.fork()
    if pid == 0:
        for k in ("TELENOTIFY_BIDI", "KITTY_WINDOW_ID"):
            os.environ.pop(k, None)
        os.environ.update(TERM="xterm-256color", COLUMNS="120", LINES="35",
                          TELENOTIFY_LABELS=str(tmp_path / "labels.jsonl"), TELENOTIFY_BIN="/nonexistent")
        os.execv(sys.executable, [sys.executable, "-m", "telenotify_tui", "--config", str(config)])
    out = bytearray()
    status = None
    try:
        _read_until(fd, out, lambda o: b"Dashboard" in o, 20)
        time.sleep(0.5)
        os.write(fd, b"\x11")                               # ctrl+q
        _read_until(fd, out, lambda o: b"\x1b[8h" in o, 20)
        _read_until(fd, out, lambda o: False, 0.3)
        for _ in range(50):
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            time.sleep(0.1)
    finally:
        if status is None:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        os.close(fd)
    assert b"Dashboard" in out, "the screen wasn't drawn"
    assert out.startswith(b"\x1b[8l"), bytes(out[:40])               # before anything else
    assert out.index(b"\x1b[8l") < out.index(b"\x1b[?1049h")            # before the screen opens
    assert b"\x1b[8h" in out, "implicit mode wasn't restored"
    assert out.rindex(b"\x1b[?1049l") < out.rindex(b"\x1b[8h")           # after the screen closes
    assert status == 0


# ------------------------------------------------------------ on the screen

ARABIC_POST = ("مطلوب مهندس كهرباء (عاجل) خبرة 3 سنوات in Python للعمل في البصرة براتب جيد جدا ودوام كامل "
               "مع تأمين صحي\nارسال السيرة الذاتية")


def strips(widget) -> list[str]:
    region = Region(0, 0, widget.size.width, widget.size.height)
    return ["".join(seg.text for seg in strip) for strip in widget.render_lines(region)]


async def test_review_screen_shows_arabic_right_to_left(paths, state):
    db = sqlite3.connect(state)
    db.execute("UPDATE reviews SET text = ?, chat_title = ? WHERE msg_id = 11", (ARABIC_POST, "وظائف البصرة"))
    db.commit()
    db.close()
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        await pilot.pause()
        head = str(app.query_one("#detail-head", Static).content)
        assert f"chat:  {vw('البصرة')} {vw('وظائف')}  #11" in head        # the title in order, the line LTR
        assert f"role:  {vw('كهرباء')} {vw('مهندس')}" in head

        text = app.query_one("#detail-text", Static)
        width = text.size.width

        def check() -> list[str]:
            shown = [s for s in strips(text) if s.strip()]
            assert len(shown) >= 3                                     # wrapped, plus the second paragraph
            for s in shown:
                assert s.rstrip() == s and len(s) == text.size.width   # right-aligned
                assert s.startswith(" ")
            assert shown[0].endswith(vw("مطلوب"))                     # the first word top right
            assert shown[-1] == shown[-1].rjust(text.size.width)
            assert shown[-1].strip() == f"{vw('الذاتية')} {vw('السيرة')} {vw('ارسال')}"
            joined = " ".join(shown)
            assert f"({vw('عاجل')})" in joined and "in Python" in joined
            return shown

        first = check()
        await pilot.resize_terminal(90, 40)
        await pilot.pause()
        await pilot.pause()
        assert text.size.width < width
        second = check()
        assert len(second) > len(first)                                # re-wrapped at the new width


async def test_review_screen_with_bidi_off_shows_text_as_stored(paths, state):
    rtl.set_enabled(False)
    db = sqlite3.connect(state)
    db.execute("UPDATE reviews SET text = ? WHERE msg_id = 11", ("كيف حالك",))
    db.commit()
    db.close()
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        await pilot.pause()
        shown = [s for s in strips(app.query_one("#detail-text", Static)) if s.strip()]
        assert [s.rstrip() for s in shown] == ["كيف حالك"]



async def test_copying_a_post_gives_the_text_as_stored(paths, state):
    """Selecting the drawn post and copying gives the stored text (reading
    order, plain letters), not the reversed presentation forms on screen."""
    from textual.geometry import Offset
    from textual.selection import Selection

    db = sqlite3.connect(state)
    db.execute("UPDATE reviews SET text = ? WHERE msg_id = 11", (ARABIC_POST,))
    db.commit()
    db.close()
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        await pilot.pause()
        text = app.query_one("#detail-text", Static)
        assert text.get_selection(Selection(None, None)) == (ARABIC_POST, "\n")
        app.screen.selections = {text: Selection(None, None)}
        assert app.screen.get_selected_text() == ARABIC_POST
        # the rightmost word of the top line is the post's first word
        first = strips(text)[0]
        x = len(first) - len(vw("مطلوب"))
        picked, _end = text.get_selection(Selection(Offset(x, 0), Offset(len(first), 0)))
        assert picked == "مطلوب"
        # the last line is the second paragraph
        last = len([s for s in strips(text) if s.strip()]) - 1
        picked, _end = text.get_selection(Selection(Offset(0, last), Offset(text.size.width, last)))
        assert picked == "ارسال السيرة الذاتية"


# -------------------------------------------------------------- the review's findings

def test_emoji_sequences_stay_whole():
    for post, emoji in [("وظيفة 👨\u200d💻 في بغداد", "👨\u200d💻"), ("مطلوب ✔\ufe0f فورا", "✔\ufe0f"),
                        ("شكرا 👍🏽 لكم", "👍🏽"), ("علم 🇮🇶 العراق", "🇮🇶")]:
        for m in ("full", "words"):
            rtl.set_mode(m)
            assert emoji in rtl.visual(post), (m, post, rtl.visual(post))
            assert emoji in "".join(s for s, _p in lines(post, 40)), (m, post)


def test_combining_marks_stay_on_their_letter():
    for word in ("מִשְׁפָּט", "مُطَوِّر", "بِسْمِ الرَّحِيمِۖ"):
        for m in ("full", "words"):
            rtl.set_mode(m)
            shown = rtl.visual(word)
            assert not unicodedata.combining(shown[0]), (m, word)
            for i, ch in enumerate(shown):
                if unicodedata.combining(ch):        # a mark follows its letter (or another mark)
                    assert unicodedata.combining(shown[i - 1]) or unicodedata.category(shown[i - 1])[0] == "L"
    rtl.set_mode("full")
    assert "ُ" in rtl.visual("مُطَوِّر")              # harakat are kept, not deleted


def test_levels_come_from_the_whole_paragraph():
    # numbers and Latin words next to each other stay one left-to-right run
    # even when the paragraph is wrapped through them
    for post, width, keep in [("خبرة في Python 3 4 سنوات", 15, "3 4"),
                              ("مطلوب خبرة في C++ 3-5 سنوات عمل", 18, "C++"),
                              ("وظيفة: React, Vue, 2 3 روابط", 17, "React,")]:
        for m in ("full", "words"):
            rtl.set_mode(m)
            shown = " | ".join(s for s, _p in lines(post, width))
            assert keep in shown, (m, post, shown)
    rtl.set_mode("full")
    assert "4 3" not in " ".join(s for s, _p in lines("خبرة في Python 3 4 سنوات", 15))
    assert "++C" not in " ".join(s for s, _p in lines("مطلوب خبرة في C++ 3-5 سنوات عمل", 18))


def test_lone_surrogates_dont_crash():
    for m in ("full", "words", "off"):
        rtl.set_mode(m)
        assert rtl.visual("\ud800كيف") .count("�") == 1
        assert lines("كيف \udfff حالك", 20)


def test_unicode_line_and_paragraph_separators_break_lines():
    for sep in ("\u2028", "\u2029", "\r\n", "\x85"):
        out = lines(f"مرحبا بكم{sep}hello there", 30)
        assert len(out) == 2 and out[1] == ("hello there", 0) and out[0][1] > 0
        assert rtl.visual(f"كيف{sep}x") == f"{vw('كيف')}\nx"


# ------------------------------------------------------------------- kitty: words

def kitty_shows(sent: str) -> str:
    """What kitty draws for a line of cells: each run of non-space cells
    shaped on its own; a run whose first letter is from a right-to-left
    script is drawn reversed (by grapheme cluster) with its brackets
    mirrored; spaces stay where they are. This is what kitty 0.47 does
    (kitty/fonts.c: render_line splits runs at blank cells, load_hb_buffer
    lets HarfBuzz guess the direction; checked against kitty's own renderer
    with `kitty +runpy` and kitty.fonts.render.render_string)."""
    out = []
    for word in re.split("( )", sent):
        if rtl._kitty_rtl(word):
            clusters = [word[a:b] for a, b in rtl._clusters(word, 0, len(word))]
            out.append("".join(rtl._mirror(c) for c in reversed(clusters)))
        else:
            out.append(word)
    return "".join(out)


@pytest.mark.parametrize("post", [
    "كيف حالك",
    "خبرة 15 سنوات in Python",
    "Python developer مطلوب في البصرة",
    "مطلوب محاسب خبرة 5 سنوات في بغداد",
    "راتب 1,500,000 دينار شهريا",
    "١٥ سنة خبرة",
    "خبرة 15سنة",
    "email: a@b.com للتواصل",
    "مرحبا. كيف الحال؟",
    "٣ سنوات 50% خبرة",
])
def test_words_mode_shows_in_kitty_what_bidi_would(post):
    """Sent in the words mode and drawn the way kitty draws, a line reads
    as the Unicode bidi algorithm orders it (python-bidi's get_display),
    with the letters left for kitty to join."""
    rtl.set_mode("words")
    sent = rtl.visual(post)
    assert not any(0xfb50 <= ord(c) <= 0xfeff for c in sent)           # nothing reshaped: kitty joins letters
    assert kitty_shows(sent) == get_display(post)


def test_words_mode_examples():
    rtl.set_mode("words")
    assert rtl.visual("كيف حالك") == "حالك كيف"                        # words swapped, letters as stored
    assert rtl.visual("خبرة 15 سنوات in Python") == "in Python سنوات 15 خبرة"
    assert rtl.visual("١٥ سنة") == "سنة ٥١"                            # kitty reverses Arabic digits back
    assert rtl.visual("خبرة 15سنة") == "51سنة خبرة"                     # and digits glued to Arabic letters
    assert rtl.visual("مطلوب (عاجل)") == "(عاجل) مطلوب"                 # kitty mirrors brackets in Arabic words
    assert kitty_shows(rtl.visual("مطلوب (عاجل)")) == f"({'عاجل'[::-1]}) {'مطلوب'[::-1]}"
    # a bracket on its own is drawn left to right by kitty: mirrored here
    assert rtl.visual("مطلوب ( عاجل )") == "( عاجل ) مطلوب"
    assert rtl.visual("مرحبا.") == "مرحبا."                            # kitty puts the full stop on the left
    # a word kitty draws left to right keeps its Arabic part in visual order, joined here
    assert rtl.visual("Python/بايثون") == "Python/" + vw("بايثون")
    [(shown, pad)] = lines("كيف حالك", 20)
    assert shown == "حالك كيف" and pad == 20 - len(shown)               # right-aligned as in full


async def test_words_mode_on_the_review_screen(paths, state):
    rtl.set_mode("words")
    db = sqlite3.connect(state)
    db.execute("UPDATE reviews SET text = ? WHERE msg_id = 11", (ARABIC_POST,))
    db.commit()
    db.close()
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        await pilot.pause()
        text = app.query_one("#detail-text", Static)
        shown = [s for s in strips(text) if s.strip()]
        assert shown[0].endswith("مطلوب") and all(len(s) == text.size.width for s in shown)
        assert shown[-1].strip() == "الذاتية السيرة ارسال"
        assert text.get_selection(rtl_selection_all()) == (ARABIC_POST, "\n")


def rtl_selection_all():
    from textual.selection import Selection

    return Selection(None, None)
