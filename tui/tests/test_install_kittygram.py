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
        _exe(self.fakes / "systemctl", "#!/bin/sh\n" + rec)
        _exe(self.fakes / "id", "#!/bin/sh\n"
             f'if [ "$1" = -u ]; then echo {0 if root else 1000}; exit 0; fi\n'
             f'[ -f "{self.users}/$1" ]\n')
        _exe(self.fakes / "useradd", "#!/bin/sh\n" + rec +
             'for last; do :; done\n'
             f'touch "{self.users}/kittygram"; mkdir -p "{self.home}"\n')
        # runuser -u kittygram -- CMD...: run CMD here, with the fakes still found
        _exe(self.fakes / "runuser", "#!/bin/sh\n"
             'while [ "$1" != -- ]; do shift; done; shift\n'
             'exec "$@"\n')
        curl = ("#!/bin/sh\n" + rec +
                'out=""; url=""\n'
                'while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift 2;; --max-time) shift 2;; -*) shift;; *) url=$1; shift;; esac; done\n'
                'case "$url" in http://127.0.0.1:*) [ "${FAKE_DOWN:-}" != 1 ]; exit $?;; esac\n'
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
        _exe(lr / "luarocks", "#!/bin/sh\n" + rec +
             'if [ "$1" = --version ]; then echo "/x/luarocks 3.11.1"; exit 0; fi\n'
             'if [ "$1" = init ]; then printf \'#!/bin/sh\\necho "app-luarocks $*" >> "%s"\\n\' '
             f'"{log}" > ./luarocks; chmod +x ./luarocks; fi\n')
        if built:
            _exe(self.home / "openresty" / "bin" / "openresty",
                 "#!/bin/sh\necho 'nginx version: openresty/1.27.1.2' >&2\n")
        else:
            (lr / "luarocks").unlink()
        self.env = dict(os.environ, PATH=f"{self.fakes}:{os.environ['PATH']}", KG_HOME=str(self.home),
                        KG_UNITS=str(self.units), KG_WAIT_TRIES="2")

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
    assert f"ExecStart={h.home}/start.sh" in unit and "127.0.0.1:8430" in unit and "MemoryMax=300M" in unit
    assert f"cd {h.home}/app" in (h.home / "start.sh").read_text()
    calls = h.calls()
    assert "useradd --system" in calls and "app-luarocks build" in calls and "lapis migrate production" in calls
    assert "systemctl enable kittygram" in calls and "systemctl restart kittygram" in calls
    assert "systemctl enable --now valkey-server" in calls
    assert not list((h.home / "src").glob(".config-edits.*"))


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
    assert "systemctl restart kittygram" in calls


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
    assert "systemctl restart kittygram" not in h.calls()


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
