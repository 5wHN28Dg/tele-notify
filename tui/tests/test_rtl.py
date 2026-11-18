"""Right-to-left text: Arabic reads right to left on any terminal, lines
broken before they are reordered, and the terminal's own bidi switched off
while the screen runs (and back on after)."""

from __future__ import annotations

import io
import json
import os
import pty
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import unicodedata

import arabic_reshaper
import pytest
from bidi import get_display
from conftest import REPO, SCHEMA, fill
from rich.cells import cell_len
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
    for word in ("מ\u05b4ש\u05b0\u05c1פ\u05b8\u05bcט", "م\u064fط\u064eو\u0650\u0651ر", "ب\u0650س\u0652م\u0650 الر\u064e\u0651ح\u0650يم\u0650\u06d6"):
        for m in ("full", "words"):
            rtl.set_mode(m)
            shown = rtl.visual(word)
            assert not unicodedata.combining(shown[0]), (m, word)
            for i, ch in enumerate(shown):
                if unicodedata.combining(ch):        # a mark follows its letter (or another mark)
                    assert unicodedata.combining(shown[i - 1]) or unicodedata.category(shown[i - 1])[0] == "L"
    rtl.set_mode("full")
    assert "\u064f" in rtl.visual("م\u064fط\u064eو\u0650\u0651ر")              # harakat are kept, not deleted


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
    """What kitty draws for a line of cells. kitty shapes each run of cells
    on its own and a run ends at a blank (space) cell and wherever the font
    changes, as it does for an emoji, drawn from an emoji font (kitty/fonts.c:
    render_line). load_hb_buffer lets HarfBuzz guess a run's direction from
    its first character whose script isn't Common: a right-to-left run is
    drawn reversed by grapheme cluster with its brackets mirrored; others
    as sent. Checked against kitty 0.47's own renderer (`kitty +runpy`,
    kitty.fonts.render.render_string), see test_kitty_renderer_agrees."""
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        text = "".join(run)
        out.append("".join(rtl._mirror(c) for c in reversed(run)) if rtl._kitty_rtl(text) else text)
        run.clear()

    for a, b in rtl._clusters(sent):
        c = sent[a:b]
        if c == " " or rtl._is_emoji(c):
            flush()
            out.append(c)
        else:
            run.append(c)
    flush()
    return "".join(out)


KITTY_POSTS = [
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
    # a word whose parts end up apart on screen (the adversarial review)
    "مطلوب (Senior Developer) فورا\u064b",
    "مطلوب (Full Stack) للعمل",
    "مطور (React Native) في بغداد",
    "شركة (ABC Company Ltd) تعلن",
    "مطلوب للعمل في Google, Inc. فورا",
    "مطلوب \"Team Lead\" للعمل",
    "مطلوب [Senior Dev] حالا",
    "Python developer (عن بعد) مطلوب",
    "#Python_مطور مطلوب",
    # emoji: kitty ends a run there
    "🔥مطلوب محاسب",
    "✅مطلوب محاسب",
    "📍بغداد - الكرادة",
    "مطلوب🔥 فورا",
    "العنوان بغداد📍",
    "وظيفة 👨\u200d💻 في بغداد",
]


@pytest.mark.parametrize("post", KITTY_POSTS)
def test_words_mode_shows_in_kitty_what_bidi_would(post):
    """Sent in the words mode and drawn the way kitty draws, a line reads
    as the full bidi order (no shaping), with the letters left for kitty to
    join."""
    rtl.set_mode("words")
    sent = rtl.visual(post)
    want = rtl.layout(rtl.safe(post), None, "order")[0].text
    # (letters kitty draws left to right are joined here, as presentation forms: compare the letters)
    assert unicodedata.normalize("NFKC", kitty_shows(sent)) == want, (sent, want)
    if not set(post) & set("()[]") and all(ord(c) < 0x2000 for c in post):
        assert want == get_display(post)                              # and that is python-bidi's order


@pytest.mark.skipif(shutil.which("kitty") is None, reason="kitty isn't installed")
def test_kitty_renderer_agrees(tmp_path):
    """kitty's own renderer (no window: kitty.fonts.render.render_string, in
    kitty's Python) draws a line the way kitty_shows says: each run on its
    own, split at spaces and emoji, so the cells of a whole line are the
    cells of its runs drawn one by one."""
    rtl.set_mode("words")
    lines = [rtl.visual(p) for p in KITTY_POSTS]
    script = tmp_path / "probe.py"
    script.write_text(
        "import json, sys\n"
        "from kitty.fonts.render import render_string\n"
        "def cells(s): return [bytes(c).hex() for c in render_string(s)[2]]\n"
        "blank = cells('x x')[1]\n"
        "out = []\n"
        "for line, runs in json.load(open(sys.argv[1])):\n"
        "    out.append(cells(line) == [c for r in runs for c in ([blank] if r == ' ' else cells(r))])\n"
        "print(json.dumps(out))\n", encoding="utf-8")
    runs = []
    for line in lines:
        cur, parts = "", []
        for a, b in rtl._clusters(line):
            c = line[a:b]
            if c == " " or rtl._is_emoji(c):
                parts += [cur, c]
                cur = ""
            else:
                cur += c
        runs.append([r for r in [*parts, cur] if r])
    data = tmp_path / "lines.json"
    data.write_text(json.dumps(list(zip(lines, runs, strict=True)), ensure_ascii=False), encoding="utf-8")
    code = (f"import runpy, sys; sys.argv = ['p', {str(data)!r}]; "
            f"runpy.run_path({str(script)!r}, run_name='__main__')")
    done = subprocess.run(["kitty", "+runpy", code], check=False, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    agree = json.loads(done.stdout.strip().splitlines()[-1])
    assert all(agree), [p for p, ok in zip(KITTY_POSTS, agree, strict=True) if not ok]


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


# ----------------------------------------------- the adversarial review's findings

def test_words_mode_copy_maps_columns_through_kittys_reversal():
    """kitty shows a right-to-left run reversed, so the two leftmost cells of
    'بغداد' show its last two letters: copying them gives those."""
    rtl.set_mode("words")
    block = rtl.Block("بغداد")
    [line] = block.layout(20)
    left = line.pad
    assert rtl.logical(block.text, [line], [(0, left, left + 2)]) == "اد"
    assert rtl.logical(block.text, [line], [(0, len(line.text) - 2, len(line.text))]) == "بغ"
    rtl.set_mode("full")
    [line] = block.layout(20)
    assert rtl.logical(block.text, [line], [(0, line.pad, line.pad + 2)]) == "اد"


@pytest.mark.parametrize("m", ["full", "words", "off"])
def test_copy_keeps_blank_lines_and_spaces(m):
    rtl.set_mode(m)
    for post in ("مطلوب محاسب\n\nللتواصل 0770", "first line\n\n\nlast  line  ", "a  b"):
        block = rtl.Block(post)
        drawn = block.layout(40)
        picks = [(y, 0, len(line.text)) for y, line in enumerate(drawn)]
        assert rtl.logical(block.text, drawn, picks) == post, (m, post)


def test_long_runs_stay_fast():
    flags = "🇮🇶" * 8000
    started = time.perf_counter()
    assert len(rtl._clusters(flags)) == 8000
    assert time.perf_counter() - started < 1.0                    # was quadratic: 22 s for 16k letters
    rtl.set_mode("words")
    post = " ".join(["Python/بايثون"] * 2000)
    started = time.perf_counter()
    rtl.Block(post).layout(80)
    assert time.perf_counter() - started < 2.0                    # the paragraph is shaped once, not per line


def test_a_mark_after_a_space_is_kept_and_lines_dont_overflow():
    for m in ("full", "words", "off"):
        rtl.set_mode(m)
        for post, width in [("ع  \u200d\u064b", 2), ("a \u0301b c", 2), ("مرحبا \u064b بكم", 6)]:
            drawn = rtl.Block(post).layout(width)
            assert all(cell_len(line.text) <= width for line in drawn), (m, post, [ln.text for ln in drawn])
            shown = "".join(line.text for line in drawn)
            assert "\u064b" in shown or "\u0301" in shown, (m, post)   # the mark isn't lost at the break
    rtl.set_mode("off")
    assert rtl.visual("a \u0301b") == "a \u25cc\u0301b"            # a bare mark sits on a dotted circle


@pytest.mark.parametrize(("env", "mode"), [
    ({"KITTY_WINDOW_ID": "3"}, "full"),                                # no TERM to go by: not trusted
    ({"TERM": "alacritty", "KITTY_WINDOW_ID": "3"}, "full"),           # another terminal started from kitty
    ({"TERM": "xterm-256color", "KITTY_WINDOW_ID": "3"}, "full"),
    ({"TERM": "screen-256color", "KITTY_WINDOW_ID": "3"}, "words"),    # screen / tmux started in kitty
])
def test_kitty_window_id_alone_isnt_kitty(env, mode):
    assert rtl.env_mode(env) == mode


def test_unknown_env_value_is_reported(monkeypatch, config, capsys):
    class FakeApp:
        def __init__(self, paths):
            pass

        def run(self):
            pass

    for k in ("TERM", "KITTY_WINDOW_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TELENOTIFY_BIDI", "wrods")
    monkeypatch.setattr(entry, "TeleNotifyApp", FakeApp)
    entry.main(["--config", str(config)])
    err = capsys.readouterr().err
    assert "TELENOTIFY_BIDI='wrods'" in err and "using full" in err
    for ok in ("full", "words", "off", "auto", "", "0"):
        monkeypatch.setenv("TELENOTIFY_BIDI", ok)
        entry.main(["--config", str(config)])
        assert capsys.readouterr().err == ""
