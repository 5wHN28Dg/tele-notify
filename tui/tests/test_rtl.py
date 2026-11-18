"""Right-to-left text: Arabic reads right to left on any terminal, lines
broken before they are reordered, and the terminal's own bidi switched off
while the screen runs (and back on after)."""

from __future__ import annotations

import io
import json
import os
import pty
import select
import signal
import sqlite3
import sys
import time

import arabic_reshaper
import pytest
from conftest import REPO, SCHEMA, fill
from rich.console import Console
from textual.geometry import Region
from textual.widgets import Static

from telenotify_tui import __main__ as entry
from telenotify_tui import rtl
from telenotify_tui.app import TeleNotifyApp


def vw(word: str) -> str:
    """An Arabic word as it is drawn left to right: joined letters, last letter first."""
    return arabic_reshaper.reshape(word)[::-1]


def lines(text: str, width: int) -> list[tuple[str, int]]:
    return rtl.Block(text).lines(Console(width=width), width)


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
    wide = b.lines(Console(width=60), 60)
    narrow = b.lines(Console(width=12), 12)
    assert len(wide) == 1 and len(narrow) > 1
    assert all(pad + len(s) == 12 for s, pad in narrow)


def test_off_switch_leaves_text_as_stored(monkeypatch):
    rtl.set_enabled(False)
    assert rtl.visual("كيف حالك") == "كيف حالك"
    assert lines("كيف حالك", 20) == [("كيف حالك", 0)]
    monkeypatch.setenv("TELENOTIFY_BIDI", "off")
    assert not rtl.env_enabled()
    monkeypatch.setenv("TELENOTIFY_BIDI", "on")
    assert rtl.env_enabled()
    monkeypatch.delenv("TELENOTIFY_BIDI")
    assert rtl.env_enabled()


@pytest.mark.parametrize(("argv", "env", "on"), [
    ([], None, True),
    (["--no-bidi"], None, False),
    ([], "off", False),
    ([], "OFF ", False),
])
def test_no_bidi_flag_and_env(monkeypatch, config, argv, env, on):
    seen = []

    class FakeApp:
        def __init__(self, paths):
            pass

        def run(self):
            seen.append(rtl.enabled())

    if env is None:
        monkeypatch.delenv("TELENOTIFY_BIDI", raising=False)
    else:
        monkeypatch.setenv("TELENOTIFY_BIDI", env)
    monkeypatch.setattr(entry, "TeleNotifyApp", FakeApp)
    entry.main(["--config", str(config), *argv])
    assert seen == [on]


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
    for s in (rtl.visual("كيف\x1b]0;X\x07 حالك\x9b31m"), rtl.Block("a\x1b[2Jكيف").lines(Console(width=20), 20)[0][0]):
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
    rtl.set_enabled(False)
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
        os.environ.pop("TELENOTIFY_BIDI", None)
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

