"""deploy/install-kittygram.sh (#114): the optional kittygram install for reading Instagram posts.

The system is faked: apt-get, systemctl, useradd, id and runuser record what they're asked; git
"clones" a small hand-written kittygram tree (its example settings in the real files' format); curl
answers the health check, and downloads only junk, so a build is always refused by its checksum.
OpenResty and LuaRocks count as built when their (fake) programs print the expected versions."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "install-kittygram.sh"
COMMIT = re.search(r"kittygram_commit=([0-9a-f]{40})", SCRIPT.read_text()).group(1)

NGINX_EXAMPLE = """worker_processes ${{NUM_WORKERS}};
error_log stderr notice;
daemon off;
env PORT;

http {
  server {
    listen ${{PORT}};
    lua_code_cache ${{CODE_CACHE}};
  }
}
"""

CONFIG_EXAMPLE = """local config = require("lapis.config")

config({ "development", "production", "docker" }, {
    server = "nginx",
    secret = "CHANGE_THIS!", -- The secret used to sign tokens. Change this.
})

config("development", {
    port = "8080",
    num_workers = "4",
    allow_json = false,
})

config("production", {
    port = "80",
    code_cache = "on",
    num_workers = "4",
    allow_json = false,
})

config("docker", {
    port = "80",
    secret = os.getenv("SECRET") or "CHANGE_THIS!!",
    allow_json = false,
})
"""


def _exe(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class Host:
    def __init__(self, tmp: Path, built: bool = True, root: bool = True):
        self.tmp = tmp
        self.home = tmp / "opt-kittygram"
        self.units = tmp / "units"
        self.units.mkdir()
        self.log = tmp / "calls.log"
        self.users = tmp / "users"
        self.users.mkdir()
        self.upstream = tmp / "upstream"
        (self.upstream / "lua_modules").mkdir(parents=True)
        (self.upstream / "nginx.example.conf").write_text(NGINX_EXAMPLE)
        (self.upstream / "config.example.lua").write_text(CONFIG_EXAMPLE)
        self.fakes = tmp / "fakes"
        log = self.log
        rec = f'echo "$(basename "$0") $*" >> "{log}"\n'
        _exe(self.fakes / "apt-get", "#!/bin/sh\n" + rec)
        _exe(self.fakes / "apt-cache", '#!/bin/sh\n[ "${FAKE_NO_VALKEY:-}" != 1 ]\n')
        # the service's state: a file; curl gets kittygram's front page only while it runs
        up = tmp / "kittygram-up"
        _exe(self.fakes / "systemctl", "#!/bin/sh\n" + rec +
             f'case "$1 $2" in "start kittygram"|"restart kittygram") [ "${{FAKE_DOWN:-}}" = 1 ] || touch "{up}";;\n'
             f'  "stop kittygram") rm -f "{up}";;\n'
             f'  "is-active --quiet") [ -f "{up}" ]; exit $?;;\n'
             'esac\n')
        _exe(self.fakes / "dpkg-query", '#!/bin/sh\nfor p; do :; done\n'
             'case " ${FAKE_INSTALLED:-} " in *" $p "*) printf "install ok installed";; *) exit 1;; esac\n')
        _exe(self.fakes / "getent", f'#!/bin/sh\necho "kittygram:x:999:988::${{FAKE_KG_HOME:-{self.home}}}:/usr/sbin/nologin"\n')
        _exe(self.fakes / "stat", '#!/bin/sh\necho "${FAKE_OWNER:-kittygram}"\n')
        _exe(self.fakes / "id", "#!/bin/sh\n"
             f'if [ "$1" = -u ]; then echo {0 if root else 1000}; exit 0; fi\n'
             f'[ -f "{self.users}/$1" ]\n')
        _exe(self.fakes / "useradd", "#!/bin/sh\n" + rec +
             'for last; do :; done\n'
             f'touch "{self.users}/kittygram"; mkdir -p "{self.home}"\n')
        # runuser -u kittygram -- CMD...: run CMD here, marked as kittygram's
        _exe(self.fakes / "runuser", "#!/bin/sh\n"
             'while [ "$1" != -- ]; do shift; done; shift\n'
             'export AS_KITTYGRAM=1\n'
             'exec "$@"\n')
        curl = ("#!/bin/sh\n" + rec +
                'out=""; url=""; all=" $* "\n'
                'while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift 2;; --max-time) shift 2;; -*) shift;; *) url=$1; shift;; esac; done\n'
                'case "$url" in http://127.0.0.1:*)\n'
                f'  if [ "${{FAKE_PORT_TAKEN:-}}" = 1 ]; then echo "<p>some other program</p>"; exit 0; fi\n'
                f'  if [ "${{FAKE_PORT_TAKEN:-}}" = 404 ]; then case "$all" in *" -fsS "*) exit 22;; esac; exit 0; fi\n'
                f'  [ -f "{up}" ] || exit 7; echo "<p>Kittygram is an anonymous Instagram frontend</p>"; exit 0;;\n'
                'esac\n'
                '[ -n "$out" ] && echo junk > "$out"\n')
        _exe(self.fakes / "curl", curl)
        # tools kittygram's user runs (as_kg's PATH starts with $home/luarocks51/bin)
        lr = self.home / "luarocks51" / "bin"
        _exe(lr / "curl", curl)
        _exe(lr / "git", "#!/bin/sh\n" + rec +
             'if [ "$1" = clone ]; then\n'
             f'  cp -r "{self.upstream}" "$4"; mkdir "$4/.git"; echo "${{FAKE_UPSTREAM_HEAD:-{COMMIT}}}" > "$4/.git/HEAD_SHA"; exit 0\n'
             'fi\n'
             'd=$2; shift 2\n'
             'case "$1" in\n'
             '  rev-parse) cat "$d/.git/HEAD_SHA";;\n'
             '  checkout) echo "$3" > "$d/.git/HEAD_SHA";;\n'
             'esac\n')
        _exe(lr / "lapis", "#!/bin/sh\n" + rec)
        # who ran a program in kittygram's folder: root never should
        who = f'echo "ran $0 as ${{AS_KITTYGRAM:-root}}" >> "{log}"\n'
        _exe(lr / "luarocks", "#!/bin/sh\n" + rec + who +
             'if [ "$1" = --version ]; then echo "/x/luarocks 3.11.1"; exit 0; fi\n'
             'if [ "$1" = init ]; then printf \'#!/bin/sh\\necho "app-luarocks $*" >> "%s"\\nmkdir -p lua_modules\\n\' '
             f'"{log}" > ./luarocks; [ "${{FAKE_BUILD_FAIL:-}}" = 1 ] && echo "exit 1" >> ./luarocks; chmod +x ./luarocks; fi\n')
        if built:
            _exe(self.home / "openresty" / "bin" / "openresty",
                 "#!/bin/sh\n" + who + "echo 'nginx version: openresty/1.27.1.2' >&2\n")
        else:
            (lr / "luarocks").unlink()
        self.env = dict(os.environ, PATH=f"{self.fakes}:{os.environ['PATH']}", KG_HOME=str(self.home),
                        KG_UNITS=str(self.units), KG_LIBEXEC=str(tmp / "libexec"), KG_WAIT_TRIES="2")

    def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(["sh", str(SCRIPT), *args], env=dict(self.env, **extra), capture_output=True,
                              text=True, timeout=60, check=False)

    def calls(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def config(self) -> str:
        return (self.home / "app" / "config.lua").read_text()

    def production(self) -> str:
        return re.search(r'config\("production", \{.*?\n\}\)', self.config(), re.DOTALL).group(0)


def test_a_first_install_sets_kittygram_up_for_the_bot(tmp_path):
    h = Host(tmp_path)
    r = h.run()
    assert r.returncode == 0, r.stderr
    assert '"instagram_frontend": "http://127.0.0.1:8430"' in r.stdout
    assert "listen 127.0.0.1:${{PORT}};" in (h.home / "app" / "nginx.conf").read_text()
    prod = h.production()
    assert 'port = "8430"' in prod and 'num_workers = "1"' in prod and "allow_json = true" in prod
    dev = re.search(r'config\("development", \{.*?\n\}\)', h.config(), re.DOTALL).group(0)
    assert 'port = "8080"' in dev and "allow_json = false" in dev          # only production changed
    assert "CHANGE_THIS!\"," not in h.config()
    assert re.search(r'secret = "[0-9a-f]{48}",', h.config())
    assert 'or "CHANGE_THIS!!"' in h.config()                               # the docker block's own
    assert stat.S_IMODE((h.home / "app" / "config.lua").stat().st_mode) == 0o600
    unit = (h.units / "kittygram.service").read_text()
    start = tmp_path / "libexec" / "kittygram-start"
    assert f"ExecStart={start}" in unit and "127.0.0.1:8430" in unit
    for line in ("User=kittygram", "MemoryMax=300M", "NoNewPrivileges=yes", "ProtectSystem=strict",
                 f"ReadWritePaths={h.home}/app", "ProtectHome=yes", "PrivateTmp=yes",
                 "After=network-online.target valkey-server.service"):
        assert line in unit.splitlines(), line
    assert f"cd {h.home}/app" in start.read_text()
    assert not (h.home / "start.sh").exists()                 # not where kittygram's user could change it
    assert (h.home / "app" / "luarocks.lock").read_text() == (REPO / "deploy" / "kittygram-luarocks.lock").read_text()
    calls = h.calls()
    assert "useradd --system" in calls and "app-luarocks build" in calls and "lapis migrate production" in calls
    assert "systemctl enable kittygram" in calls and "systemctl start kittygram" in calls
    assert "systemctl enable --now valkey-server" in calls
    assert "as root" not in calls                               # kittygram's programs ran as kittygram
    assert "ran " in calls


def test_running_it_again_keeps_the_secret_and_rebuilds_nothing(tmp_path):
    h = Host(tmp_path)
    assert h.run().returncode == 0
    secret = re.search(r'secret = "([0-9a-f]{48})",', h.config()).group(1)
    h.log.unlink()
    r = h.run()
    assert r.returncode == 0, r.stderr
    assert secret in h.config()
    calls = h.calls()
    assert "useradd" not in calls and "git clone" not in calls and "app-luarocks build" not in calls
    assert "systemctl start kittygram" in calls


def test_another_port(tmp_path):
    h = Host(tmp_path)
    assert h.run("8431").returncode == 0
    assert 'port = "8431"' in h.production()
    assert "127.0.0.1:8431" in (h.units / "kittygram.service").read_text()


def test_a_bad_port_or_not_root_changes_nothing(tmp_path):
    for args, root, why in ((("80",), True, "between 1024"), (("x1",), True, "a number"), ((), False, "root")):
        d = tmp_path / why.replace(" ", "")
        d.mkdir()
        h = Host(d, root=root)
        r = h.run(*args)
        assert r.returncode != 0 and why in r.stderr
        assert h.calls() == ""


def test_a_download_that_doesnt_match_its_checksum_is_not_built(tmp_path):
    h = Host(tmp_path, built=False)
    r = h.run()
    assert r.returncode != 0
    assert "doesn't match its expected checksum" in r.stderr
    assert not (h.home / "src" / "openresty-1.27.1.2.tar.gz").exists()
    assert not (h.home / "src" / "openresty-1.27.1.2").exists()
    assert "systemctl start kittygram" not in h.calls()


def test_a_kittygram_that_doesnt_answer_is_reported(tmp_path):
    h = Host(tmp_path)
    r = h.run(FAKE_DOWN="1")
    assert r.returncode != 0 and "journalctl -u kittygram" in r.stderr


def test_another_commit_is_checked_out_and_its_modules_built(tmp_path):
    h = Host(tmp_path)
    r = h.run(FAKE_UPSTREAM_HEAD="0" * 40)
    assert r.returncode == 0, r.stderr
    assert f"checkout -q {COMMIT}" in h.calls()
    assert (h.home / "app" / ".git" / "HEAD_SHA").read_text().strip() == COMMIT
    assert "app-luarocks build" in h.calls()


def test_redis_where_there_is_no_valkey(tmp_path):
    """Debian 12 and Ubuntu 22.04 have no valkey package; redis speaks the same protocol on 6379."""
    h = Host(tmp_path)
    r = h.run(FAKE_NO_VALKEY="1")
    assert r.returncode == 0, r.stderr
    assert "redis-server" in h.calls() and "valkey" not in h.calls()
    assert "After=network-online.target redis-server.service" in (h.units / "kittygram.service").read_text()


def test_new_pins_rebuild_the_modules(tmp_path):
    h = Host(tmp_path)
    assert h.run().returncode == 0
    marker = h.home / "app" / "lua_modules" / ".tele-notify-modules"
    marker.write_text(marker.read_text().split()[0] + " an-older-lock\n")
    h.log.unlink()
    assert h.run().returncode == 0
    assert "app-luarocks build" in h.calls()


def test_the_lock_pins_every_module_running_with_the_bot():
    text = (REPO / "deploy" / "kittygram-luarocks.lock").read_text()
    pins = dict(re.findall(r'^\s+\[?"?([a-z0-9-]+)"?\]? = "([^"]+)",$', text, re.MULTILINE))
    for name in ("lapis", "lua-resty-http", "lua-resty-openssl", "lua-resty-redis", "lapis-redis", "htmlparser",
                 "lua-cjson", "lsqlite3", "luautf8"):                 # kittygram's own dependencies
        assert name in pins, name
    assert len(pins) == 18


def test_a_port_another_program_holds_is_refused_before_starting(tmp_path):
    h = Host(tmp_path)
    r = h.run(FAKE_PORT_TAKEN="1")
    assert r.returncode != 0 and "another program answers" in r.stderr
    assert "systemctl start kittygram" not in h.calls()


def test_a_kittygram_that_doesnt_come_up_is_stopped_not_left_restarting(tmp_path):
    h = Host(tmp_path)
    r = h.run(FAKE_DOWN="1")
    assert r.returncode != 0 and "so it was stopped" in r.stderr
    assert h.calls().strip().splitlines()[-1].startswith("systemctl stop kittygram")


def test_a_redis_already_installed_is_used_not_joined_by_valkey(tmp_path):
    h = Host(tmp_path)
    r = h.run(FAKE_INSTALLED="redis-server")
    assert r.returncode == 0, r.stderr
    assert "valkey" not in h.calls()


def test_a_secret_line_without_its_comma_is_still_replaced(tmp_path):
    h = Host(tmp_path)
    example = h.upstream / "config.example.lua"
    example.write_text(example.read_text().replace('"CHANGE_THIS!", --', '"CHANGE_THIS!" --'))
    r = h.run()
    assert r.returncode == 0, r.stderr
    assert re.search(r'secret = "[0-9a-f]{48}" --', h.config())


def test_an_existing_install_that_doesnt_fit_is_left_alone(tmp_path):
    for name, extra, why in (("home", {"FAKE_KG_HOME": "/srv/elsewhere"}, "another home folder"),
                             ("owner", {"FAKE_OWNER": "root"}, "doesn't belong to the kittygram user")):
        d = tmp_path / name
        d.mkdir()
        h = Host(d)
        r = h.run(**extra)
        assert r.returncode != 0 and why in r.stderr
        assert "systemctl restart kittygram" not in h.calls() and "systemctl start kittygram" not in h.calls()


def test_a_port_with_a_leading_zero_is_refused(tmp_path):
    h = Host(tmp_path)
    assert h.run("08430").returncode != 0


def test_modules_are_installed_from_scratch(tmp_path):
    """LuaRocks keeps loading a newer version already installed, so a pin could never go back."""
    h = Host(tmp_path)
    assert h.run().returncode == 0
    newer = h.home / "app" / "lua_modules" / "lib" / "lapis-9.9"
    newer.mkdir(parents=True)
    marker = h.home / "app" / "lua_modules" / ".tele-notify-modules"
    marker.write_text("an older lock\n")
    assert h.run().returncode == 0
    assert not newer.exists()


def test_a_port_answering_with_an_error_page_is_taken_too(tmp_path):
    h = Host(tmp_path)
    r = h.run(FAKE_PORT_TAKEN="404")
    assert r.returncode != 0 and "another program answers" in r.stderr


def test_a_failed_module_build_leaves_kittygram_stopped_not_running_without_modules(tmp_path):
    h = Host(tmp_path)
    r = h.run(FAKE_BUILD_FAIL="1")
    assert r.returncode != 0 and "Lua modules failed" in r.stderr
    calls = h.calls()
    assert "systemctl stop kittygram" in calls
    assert calls.index("systemctl stop kittygram") < calls.index("app-luarocks build")
