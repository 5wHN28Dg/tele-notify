"""deploy/self-update.sh (#123): a nightly look for a newer release, checked and installed by the
release's own installer. GitHub is faked: a fake curl answers from files in a folder; the
packages are built here, with a fake installer that records it ran."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "self-update.sh"


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class Github:
    def __init__(self, tmp: Path):
        self.home = tmp / "home"
        self.lib = self.home / ".local" / "lib" / "tele-notify"
        self.lib.mkdir(parents=True)
        (self.home / ".local" / "bin").mkdir(parents=True)
        self.web = tmp / "web"
        self.web.mkdir()
        self.log = tmp / "calls.log"
        fakes = tmp / "fakes"
        fakes.mkdir()
        # curl -fsSL [--max-time N] [-H h] -o OUT URL: copy web/<last path parts> to OUT, or fail
        _exe(fakes / "curl", "#!/bin/sh\n"
                             f'echo "curl $*" >> "{self.log}"\n'
                             'out=""; url=""\n'
                             'while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift 2;; --max-time|-H) shift 2;; -*) shift;; *) url=$1; shift;; esac; done\n'
                             'name=$(printf "%s" "$url" | sed "s|.*/||")\n'
                             f'[ -f "{self.web}/$name" ] || exit 22\n'
                             f'cp "{self.web}/$name" "$out"\n')
        # the bot's `alert` command: record the text
        _exe(self.home / ".local" / "bin" / "telenotify", f'#!/bin/sh\necho "alert: $2" >> "{self.log}"\n')
        self.env = dict(os.environ, HOME=str(self.home), PATH=f"{fakes}:{os.environ['PATH']}",
                        TN_API="https://api.example", TMPDIR=str(tmp), TN_RETRY_WAIT="0")

    def release(self, tag: str, installer_ok: bool = True, bad_sum: bool = False, assets: bool = True) -> None:
        stage = self.web.parent / f"stage-{tag}"
        stage.mkdir()
        _exe(stage / "install-staged.sh", "#!/bin/sh\n"
                                          f'echo "installer ran" >> "{self.log}"\n'
                                          'cat > /dev/null\n' +
                                          (f'printf "%s\\n" {tag} > "$HOME/.local/lib/tele-notify/version"\n'
                                           if installer_ok else "exit 1\n"))
        (stage / "version").write_text(tag + "\n")
        pkg = self.web / f"tele-notify-{tag}.tar.gz"
        with tarfile.open(pkg, "w:gz") as t:
            t.add(stage, arcname=".")
        digest = hashlib.sha256(pkg.read_bytes()).hexdigest()
        (self.web / f"tele-notify-{tag}.tar.gz.sha256").write_text(
            ("0" * 64 if bad_sum else digest) + f"  tele-notify-{tag}.tar.gz\n")
        base = "https://github.com/x/releases/download/" + tag + "/"
        latest = {"tag_name": tag, "assets": [
            {"name": f"tele-notify-{tag}.tar.gz", "browser_download_url": base + f"tele-notify-{tag}.tar.gz"},
            {"name": f"tele-notify-{tag}.tar.gz.sha256",
             "browser_download_url": base + f"tele-notify-{tag}.tar.gz.sha256"}] if assets else []}
        (self.web / "latest").write_text(json.dumps(latest))

    def run(self) -> subprocess.CompletedProcess:
        return subprocess.run(["sh", str(SCRIPT)], env=self.env, capture_output=True, text=True,
                              timeout=60, check=False)

    def calls(self) -> str:
        return self.log.read_text() if self.log.exists() else ""


def test_a_newer_release_is_checked_and_installed(tmp_path):
    g = Github(tmp_path)
    (g.lib / "version").write_text("v4.0.0\n")
    g.release("v4.1.0")
    r = g.run()
    assert r.returncode == 0, r.stderr
    assert "installer ran" in g.calls() and "installed v4.1.0" in r.stdout
    assert (g.lib / "version").read_text().strip() == "v4.1.0"
    assert "alert:" not in g.calls()
    assert not list(tmp_path.glob("tn-update-*"))                 # its temporary folder is gone


def test_the_installed_release_is_left_alone(tmp_path):
    g = Github(tmp_path)
    (g.lib / "version").write_text("v4.1.0\n")
    g.release("v4.1.0")
    r = g.run()
    assert r.returncode == 0 and "up to date" in r.stdout
    assert "installer ran" not in g.calls() and "tar.gz" not in g.calls()   # nothing downloaded


def test_a_package_that_doesnt_match_its_checksum_is_not_installed(tmp_path):
    g = Github(tmp_path)
    g.release("v4.1.0", bad_sum=True)
    r = g.run()
    assert r.returncode != 0
    assert "installer ran" not in g.calls()
    assert "alert: Self-update: the v4.1.0 package doesn't match its checksum" in g.calls()


def test_a_failed_install_is_reported(tmp_path):
    g = Github(tmp_path)
    g.release("v4.1.0", installer_ok=False)
    r = g.run()
    assert r.returncode != 0
    assert "installer ran" in g.calls()
    assert "alert: Self-update: installing v4.1.0 failed, so the previous version is kept" in g.calls()


def test_github_unreachable_or_odd_answers_are_reported_not_installed(tmp_path):
    g = Github(tmp_path)
    r = g.run()                                                  # no "latest": the API call fails
    assert r.returncode != 0 and "alert: Self-update: could not ask GitHub" in g.calls()
    g.release("v4.1.0", assets=False)                            # a release without its package
    r = g.run()
    assert r.returncode != 0 and "isn't readable" in g.calls() and "installer ran" not in g.calls()
    (g.web / "latest").write_text('{"tag_name": "v4.1.0; rm -rf /"}')
    r = g.run()
    assert r.returncode != 0 and "installer ran" not in g.calls()


def test_a_pushed_build_is_not_replaced_by_the_release_it_came_after(tmp_path):
    """A push records `git describe` (v4.1.0-3-g…: newer than v4.1.0); an older release mustn't replace it."""
    g = Github(tmp_path)
    g.release("v4.1.0")
    for pushed in ("v4.1.0-3-gabc1234", "v4.2.0"):
        (g.lib / "version").write_text(pushed + "\n")
        r = g.run()
        assert r.returncode == 0 and "up to date" in r.stdout, pushed
        assert "installer ran" not in g.calls()


def test_a_later_release_replaces_a_pushed_build(tmp_path):
    g = Github(tmp_path)
    (g.lib / "version").write_text("v4.0.0-7-gabc1234\n")
    g.release("v4.0.10")                                          # numbers, not text: 10 > 0
    r = g.run()
    assert r.returncode == 0, r.stderr
    assert "installer ran" in g.calls()


def test_a_build_from_before_the_first_release_is_updated(tmp_path):
    g = Github(tmp_path)
    (g.lib / "version").write_text("abc1234\n")                   # `git describe --always` without tags
    g.release("v4.1.0")
    assert g.run().returncode == 0 and "installer ran" in g.calls()


def test_the_package_holds_what_the_installer_needs(tmp_path):
    """deploy/make-package.sh: the same set for a push and a release."""
    stage = tmp_path / "stage"
    r = subprocess.run(["sh", str(REPO / "deploy" / "make-package.sh"), str(stage), "v9.9.9"],
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    for f in ("install-staged.sh", "self-update.sh", "tele-notify.service", "tele-notify-update.service",
              "tele-notify-update.timer", "tui-tree", "tui/pyproject.toml"):
        assert (stage / f).is_file(), f
    assert (stage / "version").read_text() == "v9.9.9\n"
    tree = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD:tui"], capture_output=True, text=True,
                          check=True).stdout
    assert (stage / "tui-tree").read_text() == tree


def test_a_release_that_failed_here_is_not_tried_every_night(tmp_path):
    """A release that doesn't stay up here would restart the bot onto it and back every night."""
    g = Github(tmp_path)
    (g.lib / "version").write_text("v4.0.0\n")
    g.release("v4.1.0", installer_ok=False)
    assert g.run().returncode != 0
    g.log.unlink()
    r = g.run()                                                   # the next night
    assert r.returncode == 0 and "skipping v4.1.0" in r.stdout
    assert "installer ran" not in g.calls() and "tar.gz" not in g.calls()


def test_the_next_release_after_a_failed_one_is_installed(tmp_path):
    g = Github(tmp_path)
    (g.lib / "version").write_text("v4.0.0\n")
    (g.lib / "failed-version").write_text("v4.1.0\n")
    g.release("v4.1.1")
    assert g.run().returncode == 0 and "installer ran" in g.calls()
    assert not (g.lib / "failed-version").exists()


def test_github_is_asked_again_before_giving_up(tmp_path):
    """After a reboot the network may not be up yet: three tries, then an alert."""
    g = Github(tmp_path)
    r = g.run()
    assert r.returncode != 0
    assert g.calls().count("releases/latest") == 3


def test_a_release_without_its_checksum_is_not_installed(tmp_path):
    g = Github(tmp_path)
    g.release("v4.1.0")
    (g.web / "tele-notify-v4.1.0.tar.gz.sha256").unlink()
    r = g.run()
    assert r.returncode != 0
    assert "installer ran" not in g.calls() and "alert: Self-update: could not download v4.1.0's checksum" in g.calls()
