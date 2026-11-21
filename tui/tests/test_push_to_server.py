"""deploy/push-to-server.sh: the bot, TDLib and the screen sent to a server over ssh, installed
as the service user, the previous bot kept for a rollback, the service restarted if it runs.
Here the "server" is a temporary home on this machine: a fake ssh runs the remote part locally,
and fake systemctl / python3 record what they're asked."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "push-to-server.sh"


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _setup(tmp_path: Path, active: bool) -> tuple[dict, Path, Path]:
    remote = tmp_path / "remote-home"
    (remote / ".local" / "bin").mkdir(parents=True)
    (remote / ".local" / "bin" / "telenotify").write_text("old bot")
    fakes = tmp_path / "fakes"
    fakes.mkdir()
    log = tmp_path / "calls.log"
    # ssh TARGET CMD...: run CMD locally with HOME = the remote home
    _exe(fakes / "ssh", f'#!/bin/sh\nshift\nexec env HOME="{remote}" sh -c "$*"\n')
    _exe(fakes / "systemctl", f'#!/bin/sh\necho "systemctl $*" >> "{log}"\n'
                              f'[ "$2" = is-active ] && exit {0 if active else 3}\nexit 0\n')
    # python3 -m venv DIR / DIR/bin/pip install ...: record, make the venv's entry point
    _exe(fakes / "python3", f'#!/bin/sh\necho "python3 $*" >> "{log}"\n'
                            'mkdir -p "$3/bin"\n'
                            f'printf \'#!/bin/sh\\necho "pip $*" >> "{log}"\\n\' > "$3/bin/pip"\n'
                            'chmod +x "$3/bin/pip"\n')
    built = tmp_path / "built"
    built.mkdir()
    (built / "telenotify").write_text("new bot")
    (built / "libtdjson.so").write_text("tdlib")
    env = dict(os.environ, PATH=f"{fakes}:{os.environ['PATH']}", TELENOTIFY_PREBUILT=str(built))
    return env, remote, log


def _run(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(SCRIPT), "telenotify@example.invalid"], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=120, check=False)


def test_installs_keeps_the_previous_bot_and_restarts_a_running_service(tmp_path):
    env, remote, log = _setup(tmp_path, active=True)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    bindir = remote / ".local" / "bin"
    assert (bindir / "telenotify").read_text() == "new bot"
    assert (bindir / "telenotify.prev").read_text() == "old bot"          # for a rollback
    assert (remote / ".local" / "lib" / "tele-notify" / "libtdjson.so").read_text() == "tdlib"
    unit = remote / ".config" / "systemd" / "user" / "tele-notify.service"
    assert "ExecStart=%h/.local/bin/telenotify run" in unit.read_text()
    assert (bindir / "telenotify-tui").is_symlink()
    calls = log.read_text()
    assert "pip install" in calls and "systemctl --user daemon-reload" in calls
    assert "systemctl --user restart tele-notify" in calls
    assert not list(remote.glob("tn-push-*"))                             # nothing left behind


def test_a_stopped_service_is_left_stopped(tmp_path):
    env, _remote, log = _setup(tmp_path, active=False)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert "restart" not in log.read_text()


def test_without_a_target_it_says_how_to_use_it(tmp_path):
    r = subprocess.run(["sh", str(SCRIPT)], cwd=REPO, capture_output=True, text=True, check=False)
    assert r.returncode != 0 and "usage" in r.stderr.lower()
