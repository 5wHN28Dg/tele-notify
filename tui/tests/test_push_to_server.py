"""deploy/push-to-server.sh: the bot, TDLib and the screen sent to a server over ssh, checked
there, installed as the service user with the previous set kept for a rollback, the service
restarted if it runs and rolled back if it doesn't stay up.

Here the "server" is a temporary home on this machine: a fake ssh records its arguments and
runs the remote part locally, and fake systemctl / python3 / ldd / sleep record what they're
asked. The script runs from a temporary git repository holding a copy of it, so the tests
don't depend on this checkout being clean."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "push-to-server.sh"
TARGET = "telenotify@example.invalid"

OLD_BOT = "#!/bin/sh\n# old bot\nexit 0\n"
# the new bot: `--help` fails when the test asks for it (a bot that can't start on the server)
NEW_BOT = '#!/bin/sh\n# new bot\nif [ "${FAKE_FAIL:-}" = bot ]; then exit 1; fi\nexit 0\n'


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    """A git repository with the script, the unit and a minimal tui/ package, all committed."""
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "deploy" / "push-to-server.sh")
    shutil.copy(REPO / "deploy" / "tele-notify.service", repo / "deploy" / "tele-notify.service")
    (repo / "tui" / "telenotify_tui").mkdir(parents=True)
    (repo / "tui" / "pyproject.toml").write_text('[project]\nname = "telenotify-tui"\n')
    (repo / "tui" / "telenotify_tui" / "__init__.py").write_text("")
    (repo / "src").mkdir()
    (repo / "src" / "telenotify.nim").write_text("echo 1\n")
    (repo / "telenotify.nimble").write_text('bin = @["telenotify"]\n')
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "test commit")
    return repo


class Server:
    def __init__(self, tmp_path: Path, active: bool = True, health: str = "ok",
                 tdlib: bool = True, tui_link: bool = True):
        self.tmp = tmp_path
        self.repo = _repo(tmp_path)
        self.home = home = tmp_path / "remote-home"
        self.had_tdlib = tdlib
        self.bin = home / ".local" / "bin"
        self.lib = home / ".local" / "lib" / "tele-notify"
        self.units = home / ".config" / "systemd" / "user"
        for d in (self.bin, self.lib, self.units):
            d.mkdir(parents=True)
        # what an earlier deploy/install.sh left: bot, TDLib, a real venv dir, the unit
        _exe(self.bin / "telenotify", OLD_BOT)
        if tdlib:
            (self.lib / "libtdjson.so").write_text("old tdlib")
        venv = self.lib / "tui-venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "old-screen").write_text("old screen")
        _exe(venv / "bin" / "telenotify-tui", "#!/bin/sh\n")
        if tui_link:
            (self.bin / "telenotify-tui").symlink_to(venv / "bin" / "telenotify-tui")
        (self.units / "tele-notify.service").write_text("old unit\n")

        self.log = log = tmp_path / "calls.log"
        self.ssh_args = tmp_path / "ssh-args.log"
        self.sysd = sysd = tmp_path / "systemd-state"
        sysd.mkdir()
        (sysd / "before").write_text("active" if active else "inactive")
        (sysd / "n").write_text("0")
        self.set_health(health)

        local = tmp_path / "fakes"
        remote = tmp_path / "remote-fakes"     # only on the "server"'s PATH
        local.mkdir()
        remote.mkdir()
        # ssh [options] TARGET CMD: record all but CMD, run CMD on the "server"
        _exe(local / "ssh", "#!/bin/sh\n"
                            'for a; do cmd=$a; done\n'
                            f'i=0; for a; do i=$((i+1)); [ $i -lt $# ] && printf "%s\\n" "$a" >> "{self.ssh_args}"; done\n'
                            # CLOSE_PIPE: the connection drops after two lines of output
                            'if [ -n "${CLOSE_PIPE:-}" ]; then\n'
                            f'  env HOME="{home}" PATH="{remote}:$PATH" sh -c "$cmd" 2>&1 | head -n 2\n'
                            '  exit 0\nfi\n'
                            f'exec env HOME="{home}" PATH="{remote}:$PATH" sh -c "$cmd"\n')
        _exe(remote / "systemctl", "#!/bin/sh\n"
                                   f'echo "systemctl $* XDG_RUNTIME_DIR=${{XDG_RUNTIME_DIR:-}}" >> "{log}"\n'
                                   f'S="{sysd}"\n'
                                   f'if [ -n "${{CLOSE_PIPE:-}}" ]; then "{shutil.which("sleep")}" 0.3; fi\n'
                                   'if [ "${FAKE_FAIL:-}" = "$2" ]; then exit 1; fi\n'
                                   'case "$2" in\n'
                                   '  is-active)\n'
                                   '    if [ -f "$S/restarted" ]; then st=$(cat "$S/after"); else st=$(cat "$S/before"); fi\n'
                                   '    case " $* " in *" -q "*) ;; *) echo "$st" ;; esac\n'
                                   '    [ "$st" = active ] && exit 0; exit 3 ;;\n'
                                   '  restart) touch "$S/restarted" ;;\n'
                                   '  show)\n'
                                   '    n=$(cat "$S/n"); echo "$n"\n'
                                   '    if [ -f "$S/restarted" ] && [ -f "$S/crashloop" ]; then echo $((n+1)) > "$S/n"; fi ;;\n'
                                   'esac\nexit 0\n')
        # python3 -m venv DIR: a venv whose pip records its call and whether its argument is a
        # directory with pyproject.toml, and installs the screen's entry point
        pip = ('#!/bin/sh\n'
               f'echo "pip $*" >> "{log}"\n'
               'for a; do last=$a; done\n'
               f'if [ -f "$last/pyproject.toml" ]; then echo "pyproject: yes" >> "{log}"; fi\n'
               'if [ "${FAKE_FAIL:-}" = pip ]; then exit 1; fi\n'
               'printf "#!/bin/sh\\n" > "$(dirname "$0")/telenotify-tui"\n'
               'chmod +x "$(dirname "$0")/telenotify-tui"\n')
        (tmp_path / "pip").write_text(pip)
        _exe(remote / "python3", "#!/bin/sh\n"
                                 f'echo "python3 $*" >> "{log}"\n'
                                 'if [ "${FAKE_FAIL:-}" = venv ]; then exit 1; fi\n'
                                 'mkdir -p "$3/bin"\n'
                                 f'cp "{tmp_path / "pip"}" "$3/bin/pip"\n'
                                 'chmod +x "$3/bin/pip"\n')
        _exe(remote / "ldd", "#!/bin/sh\n"
                             'if [ "${FAKE_FAIL:-}" = ldd ]; then echo "  libssl.so.3 => not found"; exit 0; fi\n'
                             'echo "  libc.so.6 => /lib/libc.so.6"\n')
        _exe(remote / "sleep", f'#!/bin/sh\necho "sleep $*" >> "{log}"\n')
        real_install = shutil.which("install")
        _exe(remote / "install", "#!/bin/sh\n"
                                 'if [ "${FAKE_FAIL:-}" = install ]; then exit 1; fi\n'
                                 f'exec "{real_install}" "$@"\n')

        self.built = tmp_path / "built"
        self.built.mkdir()
        (self.built / "telenotify").write_text(NEW_BOT)   # not executable: the script makes it so
        (self.built / "libtdjson.so").write_text("new tdlib")
        self.env = dict(os.environ, PATH=f"{local}:{os.environ['PATH']}",
                        TELENOTIFY_PREBUILT=str(self.built), XDG_RUNTIME_DIR="/run/user/test")
        self.env.pop("FAKE_FAIL", None)

    def set_health(self, health: str) -> None:
        """How the bot is after the next restart: ok, inactive or crashloop."""
        (self.sysd / "restarted").unlink(missing_ok=True)
        (self.sysd / "crashloop").unlink(missing_ok=True)
        (self.sysd / "after").write_text("failed" if health == "inactive" else "active")
        if health == "crashloop":
            (self.sysd / "crashloop").write_text("")

    def run(self, *args: str, fail: str = "", **extra: str) -> subprocess.CompletedProcess:
        env = dict(self.env, **extra)
        if fail:
            env["FAKE_FAIL"] = fail
        argv = list(args) if args else [TARGET]
        return subprocess.run(["sh", str(self.repo / "deploy" / "push-to-server.sh"), *argv],
                              cwd=self.repo, env=env, capture_output=True, text=True, timeout=120,
                              check=False)

    def calls(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def assert_old_set_in_place(self) -> None:
        assert (self.bin / "telenotify").read_text() == OLD_BOT
        if self.had_tdlib:
            assert (self.lib / "libtdjson.so").read_text() == "old tdlib"
        else:
            assert not (self.lib / "libtdjson.so").exists()
        assert (self.units / "tele-notify.service").read_text() == "old unit\n"
        assert (self.lib / "tui-venv" / "old-screen").read_text() == "old screen"
        tui = self.bin / "telenotify-tui"
        assert tui.resolve() == (self.lib / "tui-venv" / "bin" / "telenotify-tui").resolve()

    def leftovers(self) -> list[str]:
        found = [*self.home.glob("tn-push-*"), *self.bin.glob(".telenotify*"),
                 *self.units.glob(".tele-notify*"), *self.lib.glob(".libtdjson*"),
                 *self.lib.glob(".tui-venv*")]
        # screen builds: only the current one and the previous one stay
        kept = {p.resolve() for p in (self.lib / "tui-venv", self.lib / "tui-venv.prev")}
        found += [p for p in self.lib.glob("tui-venv.build-*") if p.resolve() not in kept]
        return [p.name for p in found]


def test_installs_keeps_the_previous_set_and_restarts_a_running_service(tmp_path):
    s = Server(tmp_path, active=True)
    mine = s.lib / "tui-venv.mine12"          # not the script's: the cleanup leaves it alone
    mine.mkdir()
    r = s.run()
    assert r.returncode == 0, r.stderr
    head = subprocess.run(["git", "-C", str(s.repo), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert head in r.stdout                                               # the commit deployed
    assert (s.bin / "telenotify").read_text() == NEW_BOT
    assert (s.lib / "libtdjson.so").read_text() == "new tdlib"
    unit = s.units / "tele-notify.service"
    assert "ExecStart=%h/.local/bin/telenotify run" in unit.read_text()
    # the previous set, for a rollback
    assert (s.bin / "telenotify.prev").read_text() == OLD_BOT
    assert (s.lib / "libtdjson.so.prev").read_text() == "old tdlib"
    assert (s.units / "tele-notify.service.prev").read_text() == "old unit\n"
    assert (s.lib / "tui-venv.prev" / "old-screen").read_text() == "old screen"
    # the new screen is installed and reachable
    assert not (s.lib / "tui-venv" / "old-screen").exists()
    assert (s.bin / "telenotify-tui").is_symlink()
    assert os.access(s.bin / "telenotify-tui", os.X_OK)
    calls = s.calls()
    assert "pip install" in calls and "systemctl --user daemon-reload" in calls
    assert "systemctl --user restart tele-notify" in calls
    assert "sleep" in calls                                               # the health check waited
    assert s.leftovers() == []                                            # nothing left behind
    assert mine.is_dir()


def test_the_installed_bot_is_executable(tmp_path):
    s = Server(tmp_path)
    assert not os.access(s.built / "telenotify", os.X_OK)
    r = s.run()
    assert r.returncode == 0, r.stderr
    assert os.access(s.bin / "telenotify", os.X_OK)


def test_pip_gets_a_directory_with_pyproject(tmp_path):
    s = Server(tmp_path)
    r = s.run()
    assert r.returncode == 0, r.stderr
    assert "pyproject: yes" in s.calls()


def test_config_and_drop_ins_are_left_byte_identical(tmp_path):
    s = Server(tmp_path)
    cfg = s.home / ".config" / "tele-notify" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_bytes(b'{"api_id": 1, "secret": "x"}\n')
    dropin = s.units / "tele-notify.service.d" / "override.conf"
    dropin.parent.mkdir()
    dropin.write_bytes(b"[Service]\nEnvironment=X=1\n")
    r = s.run()
    assert r.returncode == 0, r.stderr
    assert cfg.read_bytes() == b'{"api_id": 1, "secret": "x"}\n'
    assert dropin.read_bytes() == b"[Service]\nEnvironment=X=1\n"


def test_a_second_identical_run_keeps_the_real_previous_set(tmp_path):
    s = Server(tmp_path)
    assert s.run().returncode == 0
    r = s.run()
    assert r.returncode == 0, r.stderr
    assert (s.bin / "telenotify").read_text() == NEW_BOT
    assert (s.bin / "telenotify.prev").read_text() == OLD_BOT
    assert (s.lib / "libtdjson.so.prev").read_text() == "old tdlib"
    assert (s.lib / "tui-venv.prev" / "old-screen").read_text() == "old screen"
    assert s.leftovers() == []


def test_ssh_options_go_before_the_target(tmp_path):
    s = Server(tmp_path)
    r = s.run(TARGET, "-i", "/k/key", "-o", "IdentitiesOnly=yes")
    assert r.returncode == 0, r.stderr
    assert s.ssh_args.read_text().splitlines() == ["-i", "/k/key", "-o", "IdentitiesOnly=yes", TARGET]


def test_xdg_runtime_dir_is_set_for_systemctl_when_missing(tmp_path):
    s = Server(tmp_path)
    del s.env["XDG_RUNTIME_DIR"]
    r = s.run()
    assert r.returncode == 0, r.stderr
    lines = [ln for ln in s.calls().splitlines() if ln.startswith("systemctl")]
    assert lines
    assert all(ln.endswith(f"XDG_RUNTIME_DIR=/run/user/{os.getuid()}") for ln in lines)


@pytest.mark.parametrize("fail", ["bot", "ldd", "install", "venv", "pip"])
def test_a_failure_on_the_server_changes_nothing(tmp_path, fail):
    s = Server(tmp_path)
    r = s.run(fail=fail)
    assert r.returncode != 0, r.stdout
    s.assert_old_set_in_place()
    assert not (s.bin / "telenotify.prev").exists()
    assert "restart" not in s.calls()
    assert s.leftovers() == []


@pytest.mark.parametrize("health", ["inactive", "crashloop"])
def test_a_bot_that_does_not_stay_up_is_rolled_back(tmp_path, health):
    s = Server(tmp_path, active=True, health=health)
    r = s.run()
    assert r.returncode != 0, r.stdout
    assert "rolled back" in r.stderr
    s.assert_old_set_in_place()
    assert s.calls().count("systemctl --user restart tele-notify") == 2   # the new, then the old
    assert "systemctl --user reset-failed tele-notify" in s.calls()        # past the start limit


@pytest.mark.parametrize("fail", ["restart", "daemon-reload"])
def test_a_failing_systemctl_after_the_swap_rolls_back(tmp_path, fail):
    s = Server(tmp_path, active=True)
    r = s.run(fail=fail)
    assert r.returncode != 0, r.stdout
    assert "rolled back" in r.stderr
    s.assert_old_set_in_place()


def test_a_dropped_connection_after_the_swap_still_rolls_back(tmp_path):
    s = Server(tmp_path, active=True, health="inactive")
    s.run(CLOSE_PIPE="1")
    s.assert_old_set_in_place()
    assert s.calls().count("systemctl --user restart tele-notify") == 2


def test_a_rollback_with_no_tdlib_before_removes_the_new_one(tmp_path):
    s = Server(tmp_path, active=True, health="inactive", tdlib=False)
    r = s.run()
    assert r.returncode != 0, r.stdout
    s.assert_old_set_in_place()


def test_a_rollback_after_two_changing_pushes_goes_back_to_the_first(tmp_path):
    s = Server(tmp_path, active=True)
    assert s.run().returncode == 0
    first = (s.lib / "tui-venv").resolve()
    # a second, different push: new bot, new screen; the bot then doesn't stay up
    (s.built / "telenotify").write_text(NEW_BOT + "# v2\n")
    (s.repo / "tui" / "pyproject.toml").write_text('[project]\nname = "telenotify-tui"\nversion = "2"\n')
    _git(s.repo, "commit", "-q", "-am", "v2")
    s.set_health("inactive")
    r = s.run()
    assert r.returncode != 0, r.stdout
    assert (s.bin / "telenotify").read_text() == NEW_BOT
    assert (s.lib / "tui-venv").resolve() == first
    assert (first / "bin" / "telenotify-tui").exists()
    assert os.access(s.bin / "telenotify-tui", os.X_OK)


def test_the_screen_command_is_linked_when_missing(tmp_path):
    s = Server(tmp_path, tui_link=False)
    r = s.run()
    assert r.returncode == 0, r.stderr
    assert (s.bin / "telenotify-tui").is_symlink()
    assert os.access(s.bin / "telenotify-tui", os.X_OK)


def test_a_stopped_service_is_left_stopped(tmp_path):
    s = Server(tmp_path, active=False)
    r = s.run()
    assert r.returncode == 0, r.stderr
    assert "restart" not in s.calls()


@pytest.mark.parametrize("path", ["tui/pyproject.toml", "src/telenotify.nim", "telenotify.nimble"])
def test_a_dirty_tree_is_refused_unless_allowed(tmp_path, path):
    s = Server(tmp_path)
    with (s.repo / path).open("a") as f:
        f.write("# changed\n")
    r = s.run()
    assert r.returncode != 0
    assert "uncommitted" in r.stderr and path in r.stderr
    assert not s.ssh_args.exists()                                        # nothing was sent
    r = s.run("--allow-dirty", TARGET)
    assert r.returncode == 0, r.stderr
    assert (s.bin / "telenotify").read_text() == NEW_BOT


def test_without_a_target_it_says_how_to_use_it(tmp_path):
    r = subprocess.run(["sh", str(SCRIPT)], cwd=REPO, capture_output=True, text=True, check=False)
    assert r.returncode != 0 and "usage" in r.stderr.lower()
