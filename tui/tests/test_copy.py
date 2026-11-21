"""y's clipboard path (#78, TN-R11): the desktop's clipboard program where the screen runs on
this machine's desktop, OSC 52 always, and a note that says "Copied" only when it knows."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from conftest import UNCLEAR
from test_review import open_review

from telenotify_tui import postview
from telenotify_tui.app import TeleNotifyApp

HOSTILE = "-o /tmp/x $(touch /tmp/pwned) ; " + UNCLEAR


def fake(tmp_path: Path, name: str, status: int = 0, sleep: float = 0) -> Path:
    """A fake clipboard program on PATH: it records its arguments and its stdin."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    prog = bin_dir / name
    prog.write_text(f"""#!/bin/sh
printf '%s\\n' "$@" > "{bin_dir}/{name}.args"
/bin/cat > "{bin_dir}/{name}.stdin"
/bin/sleep {sleep}
exit {status}
""")
    prog.chmod(0o755)
    return bin_dir


async def copy_in_review(paths, text: str = HOSTILE) -> TeleNotifyApp:
    db = sqlite3.connect(paths.state)
    db.execute("UPDATE reviews SET text = ?", (text,))
    db.commit()
    db.close()
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        await pilot.press("y")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
    return app


def toast(app) -> str:
    return [n.message for n in app._notifications][-1]


@pytest.mark.parametrize("display, name, args", [
    ("WAYLAND_DISPLAY", "wl-copy", []),
    ("DISPLAY", "xclip", ["-selection", "clipboard"]),
    ("DISPLAY", "xsel", ["--clipboard", "--input"]),
])
async def test_on_the_local_desktop_its_clipboard_program_is_used(paths, tmp_path, monkeypatch, display, name, args):
    bin_dir = fake(tmp_path, name)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv(display, ":1")
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    app = await copy_in_review(paths)
    assert app.clipboard == HOSTILE                          # OSC 52 as well
    # the untrusted text only on stdin, never as arguments
    assert (bin_dir / f"{name}.stdin").read_text(encoding="utf-8") == HOSTILE
    assert (bin_dir / f"{name}.args").read_text().split() == args
    assert toast(app).startswith("Copied the post's text (")


async def test_without_a_desktop_program_the_note_doesnt_claim_a_copy(paths, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    app = await copy_in_review(paths)
    assert app.clipboard == HOSTILE
    assert "Copied" not in toast(app) and "OSC 52" in toast(app) and "select with the mouse" in toast(app)


async def test_over_ssh_the_desktop_program_isnt_used(paths, tmp_path, monkeypatch):
    bin_dir = fake(tmp_path, "wl-copy")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 5000 10.0.0.1 22")
    app = await copy_in_review(paths)
    assert not (bin_dir / "wl-copy.stdin").exists()
    assert "Copied" not in toast(app) and "OSC 52" in toast(app)


@pytest.mark.parametrize("status, sleep", [(1, 0), (0, 5)])
async def test_a_failing_or_stuck_program_doesnt_claim_a_copy(paths, tmp_path, monkeypatch, status, sleep):
    monkeypatch.setattr(postview, "COPY_TIMEOUT", 0.5)
    monkeypatch.setenv("PATH", str(fake(tmp_path, "wl-copy", status=status, sleep=sleep)))
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    app = await copy_in_review(paths)
    assert "Copied" not in toast(app) and "OSC 52" in toast(app)
