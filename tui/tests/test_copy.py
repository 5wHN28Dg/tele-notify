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


def fake_tmux(bin_dir: Path, out: str, status: int = 0) -> None:
    """A fake tmux: `show-environment SSH_CONNECTION` prints `out` and exits with `status`."""
    prog = bin_dir / "tmux"
    prog.write_text(f"""#!/bin/sh
printf '%s\\n' "$@" > "{bin_dir}/tmux.args"
printf '%s\\n' '{out}'
exit {status}
""")
    prog.chmod(0o755)


@pytest.mark.parametrize("tmux_says, status, used, note", [
    ("-SSH_CONNECTION", 0, True, "Copied the post's text ("),                     # attached from here
    ("SSH_CONNECTION=10.0.0.2 5000 10.0.0.1 22", 0, False, "Sent "),              # attached over ssh
    ("unknown variable: SSH_CONNECTION", 1, True, "Copied to this computer's desktop clipboard ("),
])
async def test_inside_tmux_the_attached_client_decides(paths, tmp_path, monkeypatch, tmux_says, status, used, note):
    """Review run 2 (TN-R11): a tmux pane started on the desktop keeps WAYLAND_DISPLAY and no
    SSH_CONNECTION when the session is later attached over ssh, and the screen said "Copied" while the
    user's clipboard got nothing. tmux is asked who attached; when it can't say, the note names where
    the text went."""
    bin_dir = fake(tmp_path, "wl-copy")
    fake_tmux(bin_dir, tmux_says, status)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    app = await copy_in_review(paths)
    assert (bin_dir / "tmux.args").read_text().split() == ["show-environment", "SSH_CONNECTION"]
    assert (bin_dir / "wl-copy.stdin").exists() == used
    assert toast(app).startswith(note)
    assert app.clipboard == HOSTILE


async def test_inside_tmux_without_tmux_on_path_names_where_it_went(paths, tmp_path, monkeypatch):
    bin_dir = fake(tmp_path, "wl-copy")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    app = await copy_in_review(paths)
    assert toast(app).startswith("Copied to this computer's desktop clipboard (")


def _alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"   # a zombie has already ended


async def test_a_cancelled_copy_leaves_no_program_behind(paths, tmp_path, monkeypatch):
    """Review run 2 (note 5): a second y cancels the first copy; its clipboard program (here one that
    hangs) is killed, as is the second one when the screen quits."""
    import asyncio

    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    prog = bin_dir / "wl-copy"
    prog.write_text(f"""#!/bin/sh
echo $$ >> "{bin_dir}/pids"
/bin/cat > /dev/null
exec /bin/sleep 30
""")
    prog.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("TMUX", raising=False)
    pids_file = bin_dir / "pids"
    app = TeleNotifyApp(paths, refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_review(pilot)
        await pilot.press("y")
        for _ in range(50):
            await asyncio.sleep(0.05)
            if pids_file.exists():
                break
        await pilot.press("y")
        for _ in range(50):
            await asyncio.sleep(0.05)
            if len(pids_file.read_text().split()) == 2:
                break
        first, second = map(int, pids_file.read_text().split())
        for _ in range(40):
            if not _alive(first):
                break
            await asyncio.sleep(0.05)
        assert not _alive(first)
        assert _alive(second)
    for _ in range(40):
        if not _alive(second):
            break
        await asyncio.sleep(0.05)
    assert not _alive(second)
