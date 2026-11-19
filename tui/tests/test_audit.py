"""The audit harness (tui/audit/): its guard, sandbox, fakes, synthetic data
and parsers, and each collector run end to end as auditors run it (a child
process, so none of this suite's autouse fixtures reach it)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from conftest import REPO

from audit import data, evidence, fakes, guard, styles, svg
from audit import sandbox as sbx

TUI = REPO / "tui"


# ------------------------------------------------------------------ guard

@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    """A home with the protected places (and the bot's data folder a symlink elsewhere)."""
    home = tmp_path / "home"
    share = home / ".local" / "share"
    (share / "tele-notify-corpus").mkdir(parents=True)
    (home / "Projects" / "bqtechjobs").mkdir(parents=True)
    (home / "tele-notify").mkdir()
    elsewhere = tmp_path / "disk" / "tn-data"
    elsewhere.mkdir(parents=True)
    (share / "tele-notify").symlink_to(elsewhere)
    (share / "tele-notify-old").mkdir()
    return home


def test_the_real_home_comes_from_the_password_database(monkeypatch):
    import pwd
    monkeypatch.setenv("HOME", "/somewhere/else")
    assert guard.real_home() == Path(pwd.getpwuid(os.getuid()).pw_dir)


def test_the_guard_refuses_every_protected_place(fake_home: Path, tmp_path: Path):
    share = fake_home / ".local" / "share"
    for p in (share / "tele-notify" / "state.sqlite3", share / "tele-notify-corpus" / "eval.jsonl",
              share / "tele-notify-old" / "x", share / "tele-notify", fake_home / "tele-notify" / "telenotify",
              fake_home / "Projects" / "bqtechjobs" / "config.json",
              tmp_path / "disk" / "tn-data" / "state.sqlite3",           # the data folder's real place
              fake_home / ".config" / "tele-notify" / "config.json",     # the installed config
              fake_home / ".local" / "lib" / "tele-notify" / "libtdjson.so",
              fake_home / ".local" / "bin" / "telenotify"):
        assert guard.why_forbidden(p, fake_home), p
    link = tmp_path / "innocent"
    link.symlink_to(fake_home / "Projects" / "bqtechjobs")
    assert guard.why_forbidden(link / "config.json", fake_home)         # symlinks are followed
    for ok in (share / "other-app" / "x", fake_home / ".cache" / "agent-tmp" / "tn-audit", Path("/tmp/tn-audit/r")):
        assert guard.why_forbidden(ok, fake_home) is None, ok
    with pytest.raises(guard.Refused):
        guard.check([share / "tele-notify-corpus"], fake_home)


def test_a_sandbox_in_a_protected_place_is_refused(fake_home: Path, tmp_path: Path):
    sb = sbx.plan(tmp_path / "out", real_home=fake_home)
    sbx.check(sb)
    for out in (fake_home / "tele-notify" / "evidence", Path("/tmp/tn-audit/x")):
        with pytest.raises(guard.Refused):
            sbx.check(sbx.plan(out, real_home=fake_home))
    with pytest.raises(guard.Refused):
        sbx.check(sbx.plan(tmp_path / "out", base=fake_home / ".local" / "share" / "tele-notify-x",
                           real_home=fake_home))


def test_the_sandbox_environment_has_no_desktop_and_fakes_first(tmp_path: Path):
    sb = sbx.plan(tmp_path / "out", base=tmp_path / "base")
    env = sb.env("words")
    for gone in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "USER", "LOGNAME"):
        assert gone not in env
    assert env["PATH"] == f"{sb.bin}:/usr/bin:/bin"
    assert env["HOME"].startswith(str(sb.root)) and env["BROWSER"].startswith(str(sb.bin))
    for k in ("TELENOTIFY_LABELS", "TELENOTIFY_CORPUS", "TELENOTIFY_BIN"):
        assert env[k].startswith(str(sb.root)), k
    assert env["TELENOTIFY_BIDI"] == "words"
    other = sbx.plan(tmp_path / "out", base=tmp_path / "base")
    assert other.root.parent == sb.root.parent


def test_runs_get_their_own_folder_and_the_master_is_only_copied(tmp_path: Path):
    sb = sbx.plan(tmp_path / "out", base=tmp_path / "base")
    sb.real_home = tmp_path / "home"
    sbx.create(sb)
    with pytest.raises(FileExistsError):
        sbx.create(sb)                                       # never reused
    sbx.prepare(sb)
    cfg = json.loads(sb.config.read_text(encoding="utf-8"))
    assert "api_id" not in cfg and "api_hash" not in cfg and cfg["data_dir"].startswith(str(sb.root))
    master, _man, _ = data.big_state(sb.cache, posts=500, reviews=20, chats=10)
    assert not os.access(master, os.W_OK)
    copy = sbx.use_big(sb, master)
    assert sb.state.resolve() == copy and copy != master
    sbx.remove(sb)
    assert not sb.root.exists() and not sb.scratch.exists() and master.exists()


# ------------------------------------------------------------------ fakes

def test_fake_systemctl_answers_per_service_and_logs(tmp_path: Path):
    progs = fakes.install(tmp_path, tmp_path / "bin")
    fakes.set_services(tmp_path, {"a": {"state": "active"}, "b": {"state": "failed"}, "c": {"state": "missing"}})

    def ctl(*args):
        return subprocess.run([progs["systemctl"], "--user", *args], capture_output=True, text=True, check=False)

    assert (ctl("is-active", "a").stdout.strip(), ctl("is-active", "a").returncode) == ("active", 0)
    assert (ctl("is-active", "b").stdout.strip(), ctl("is-active", "b").returncode) == ("failed", 3)
    assert ctl("is-active", "c").stdout.strip() == "inactive"
    assert ctl("start", "c").returncode == 5
    assert ctl("stop", "a").returncode == 0 and ctl("is-active", "a").stdout.strip() == "inactive"
    assert ctl("start", "a").returncode == 0 and ctl("is-active", "a").stdout.strip() == "active"
    assert ["--user", "stop", "a"] in [c["argv"] for c in fakes.calls(tmp_path, "systemctl")]
    subprocess.run([progs["browser"], "https://example.com/x"], check=True)
    assert [c["url"] for c in fakes.calls(tmp_path, "browser")] == ["https://example.com/x"]


def test_fake_telenotify_judges_and_evaluates(tmp_path: Path):
    progs = fakes.install(tmp_path, tmp_path / "bin")
    post = tmp_path / "post.txt"
    post.write_text("hello", encoding="utf-8")
    out = subprocess.run([progs["telenotify"], "check", "--show-text", str(post)], capture_output=True, text=True, check=False)
    from telenotify_tui.bot import parse_check, parse_eval
    v = parse_check(out.stdout)
    assert v.verdict == "review" and v.text == "hello"
    ev = subprocess.run([progs["telenotify"], "eval", "x"], capture_output=True, text=True, check=False)
    assert parse_eval(ev.stdout).rows["TRUE"] == (7, 2, 1)


# ------------------------------------------------------------------- data

def _nim_columns() -> dict[str, list[str]]:
    src = (REPO / "src" / "telenotify" / "state.nim").read_text(encoding="utf-8")
    out: dict[str, list[str]] = {}
    for name, body in re.findall(r'CREATE TABLE IF NOT EXISTS (\w+) \(\s*(.*?)\)(?:"""|")', src, re.DOTALL):
        cols = [c.strip().split()[0] for c in re.split(r",(?![^(]*\))", body) if c.strip()]
        out[name] = [c for c in cols if c.upper() not in ("PRIMARY", "UNIQUE")]
    for table, column in re.findall(r'\("(\w+)", "(\w+) [A-Z]+"\)', src):
        out[table].append(column)
    return out


def test_the_synthetic_schema_matches_state_nim(tmp_path: Path):
    nim = _nim_columns()
    assert set(nim) == set(data.TABLES)
    db = sqlite3.connect(tmp_path / "s.sqlite3")
    data.create(db)
    for table, cols in nim.items():
        mine = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
        assert sorted(mine) == sorted(cols), table


def test_the_small_state_reads_in_the_screen(tmp_path: Path):
    from telenotify_tui.store import Store
    p = tmp_path / "s.sqlite3"
    data.small_state(p)
    with Store(p) as st:
        assert len(st.reviews()) == 8 and len(st.chats()) == len(data.CHAT_TITLES) and st.alerts()
        assert st.recent(5) and st.search("example.com")


def test_the_large_state_is_built_once_and_reused(tmp_path: Path):
    p, man, built = data.big_state(tmp_path, posts=3000, reviews=50, chats=30)
    assert built and man["posts"] == 3000
    db = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    assert db.execute("SELECT count(*), count(DISTINCT chat_id) FROM processed").fetchone() == (3000, 30)
    assert db.execute("SELECT count(*) FROM reviews").fetchone()[0] == 50
    assert db.execute("SELECT count(*) FROM chats").fetchone()[0] == 30
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    db.close()
    assert data.big_state(tmp_path, posts=3000, reviews=50, chats=30)[2] is False


def test_synthetic_texts_use_only_example_domains():
    text = "\n".join(data.POSTS + data.ALERTS + data.CHAT_TITLES)
    for host in re.findall(r"https?://([^/\s]+)", text) + re.findall(r"@([\w.]+)", text):
        assert host == "example.com", host


# ---------------------------------------------------------- svg, evidence

def test_contrast_and_greyscale():
    assert svg.contrast("#000000", "#ffffff") == 21.0
    assert svg.contrast("#777777", "#777777") == 1.0
    assert svg.greyscale('fill="#ff0000"') == 'fill="#363636"'


async def test_svg_cells_have_the_drawn_text_and_colours():
    from textual.app import App
    from textual.widgets import Static

    class Tiny(App):
        CSS = "Static { color: #ffffff; background: #000000; }"

        def compose(self):
            yield Static("hello مرحبا")

    app = Tiny()
    async with app.run_test(size=(30, 3)):
        data_ = app.export_screenshot()
    g = svg.parse(data_, 30, 3)
    assert g.text()[0].startswith("hello")
    [pair] = [p for p in svg.pairs(g, (0, 0, 5, 1))]
    assert (pair["fg"], pair["bg"], pair["contrast"], pair["below_minimum"]) == ("#ffffff", "#000000", 21.0, False)


def test_the_leak_scan_finds_the_login_name_and_home(tmp_path: Path):
    (tmp_path / "ok.txt").write_text("/tmp/tn-audit/r/config.json", encoding="utf-8")
    assert evidence.leaks(tmp_path, home=Path("/home/someone"), login="someone", names=[]) == []
    (tmp_path / "bad.svg").write_text("<text>/home/someone/x</text>", encoding="utf-8")
    (tmp_path / "someone.txt").write_text("", encoding="utf-8")
    hits = evidence.leaks(tmp_path, home=Path("/home/someone"), login="someone", names=[])
    assert {h["what"] for h in hits} == {"real home path", "login name", "login name in the file name"}
    assert evidence.redact("at /home/someone/x", Path("/home/someone")) == "at ~/x"


def test_the_style_scan():
    css = "A { color: red; background: $primary; padding: 0 3; margin: 1 2; border: tall #ff0000; }"
    hits = styles.scan_css(css, "x.tcss")
    assert [(h["rule"], h["property"]) for h in hits] == [("R14.1", "color"), ("R14.2", "padding"),
                                                          ("R14.1", "border")]
    py = 'Text("x", style="bold green")\nq = "#feed"\nm = "[red]hi[/red]"\nok = "bold reverse"\n'
    assert [h["literal"] for h in styles.scan_python(py, "x.py")] == [["green"], ["red"]]


# ------------------------------------------------------- collectors, end to end

@pytest.fixture
def out():
    """Evidence outside the run folders, in a path without the login name."""
    d = Path(tempfile.mkdtemp(prefix="tn-audit-test-", dir="/tmp"))
    yield d
    shutil.rmtree(d)


def audit(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    return subprocess.run([sys.executable, "-m", "audit", *args], cwd=TUI, capture_output=True, text=True,
                          timeout=timeout, env=env, check=False)


def test_isolation_only_fakes_are_reached(out: Path):
    r = audit("isolation", "--out", str(out))
    assert r.returncode == 0, r.stderr
    rec = json.loads((out / "isolation.json").read_text(encoding="utf-8"))
    assert rec["isolated"] and rec["programs_inside_sandbox"] and all(rec["env"]["unset"].values())
    assert rec["browser_urls"] and rec["browser_urls"][0].startswith("https://example.com/")
    assert rec["systemctl_actions"] == [["--user", "stop", "tele-notify-dryrun"],
                                        ["--user", "start", "tele-notify-dryrun"]]
    assert all(rec["guard_refuses"].values())
    assert json.loads((out / "leaks.json").read_text())["clean"]
    assert not any(Path("/tmp/tn-audit").glob("isolation-*"))       # the run folder is gone


def test_the_cli_refuses_protected_places_before_changing_anything():
    target = guard.real_home() / ".local" / "share" / "tele-notify-corpus" / "audit-out"
    r = audit("leaks", "--out", str(target))
    assert r.returncode == 2 and "refusing" in r.stderr
    assert not target.exists()


def test_interactive_mode_uses_the_same_isolation(out: Path):
    r = audit("interactive", "--headless-check", "--bidi", "words")
    assert r.returncode == 0, r.stderr
    rec = json.loads(r.stdout.strip().splitlines()[-1])
    assert "DISPLAY" not in rec["env_keys"] and "WAYLAND_DISPLAY" not in rec["env_keys"]
    assert rec["home"].startswith("/tmp/tn-audit/") and rec["path"].endswith(":/usr/bin:/bin")
    assert rec["systemctl"] == [["--user", a, "tele-notify-dryrun"] for a in ("restart", "stop", "start")]
    assert "--bidi" in rec["argv"] and "/tmp/tn-audit/" in rec["sub_title"]


def test_screenshots_keys_visible_and_styles(out: Path):
    r = audit("screenshots", "--sizes", "80x24", "--bidi", "words", "--modes", "real",
              "--only", "1-dashboard-feed,4-settings-roles", "--out", str(out))
    assert r.returncode == 0, r.stderr
    shots = out / "screenshots" / "real" / "dark" / "bidi-words" / "80x24"
    assert (shots / "1-dashboard-feed.svg").exists() and "Dashboard" in (shots / "1-dashboard-feed.txt").read_text()
    for job, extra in (("keys", ["--sizes", "80x24"]), ("visible", ["--sizes", "80x24", "--modes", "dry-run"]),
                       ("styles", [])):
        r = audit(job, *extra, "--out", str(out))
        assert r.returncode == 0, (job, r.stderr)
    keys = json.loads((out / "keys" / "80x24.json").read_text())
    assert {p["place"] for p in keys} >= {"1-dashboard", "dialog-stop-confirm", "4-settings-chats"}
    items = json.loads((out / "visible" / "dry-run" / "80x24" / "items.json").read_text())
    assert {i["tab"] for i in items} == {"Dashboard", "Review", "Search", "Settings"}
    assert (out / "styles" / "summary.md").exists()


def test_colours_failures_tasks_and_timing(out: Path):
    for job, extra in (("colours", []), ("failures", ["--only", "no-telenotify"]),
                       ("tasks", ["--only", "test on the corpus"]),
                       ("timing", ["--posts", "3000", "--runs", "1", "--locks", "none,write"])):
        r = audit(job, *extra, "--out", str(out))
        assert r.returncode == 0, (job, r.stderr)
    states = json.loads((out / "colours" / "dark" / "states.json").read_text())
    assert all("pairs" in s for s in states), [s for s in states if "error" in s]
    [cond] = json.loads((out / "failures" / "index.json").read_text())
    assert cond["condition"] == "no-telenotify" and len(cond["tabs"]) == 4
    [task] = json.loads((out / "tasks" / "costs.json").read_text())
    assert task["keyboard"]["reached_end_state"] and task["mouse"]["clicks"] >= 1
    summary = json.loads((out / "timing" / "summary.json").read_text())
    assert set(summary["by_app_and_lock"]) == {"A/none", "A/write"}


def test_hostile_text_run(out: Path):
    r = audit("hostile", "--bidi", "full", "--out", str(out))
    assert r.returncode == 0, r.stderr
    rec = json.loads((out / "hostile" / "bidi-full" / "steps.json").read_text())
    assert rec["steps"] and "payload_sequences_found" in rec
    seen = {m for s in rec["steps"] for m in s["markers_seen"]}
    assert any(m.startswith("ZqPO") for m in seen) and any(m.startswith("ZqKW") for m in seen)


def test_an_out_that_holds_protected_places_is_refused(fake_home: Path, tmp_path: Path):
    # the leak scan reads everything under --out: never the home or a folder above a protected place
    for out in (fake_home, fake_home / ".local", fake_home / ".local" / "share", fake_home / ".config"):
        (fake_home / ".config" / "tele-notify").mkdir(parents=True, exist_ok=True)
        with pytest.raises(guard.Refused):
            sbx.check(sbx.plan(out, real_home=fake_home))
    base = tmp_path / "base"
    base.mkdir()
    sneaky = tmp_path / "sneaky"
    sneaky.symlink_to(base)
    with pytest.raises(guard.Refused):                       # a symlink into the run folders
        sbx.check(sbx.plan(sneaky / "x", base=base, real_home=fake_home))


def test_the_leak_scan_follows_no_symlinks_and_reads_nothing_protected(fake_home: Path, tmp_path: Path,
                                                                     monkeypatch):
    corpus = fake_home / ".local" / "share" / "tele-notify-corpus" / "eval.jsonl"
    corpus.write_text("private", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    (out / "link").symlink_to(corpus)
    (out / "dirlink").symlink_to(corpus.parent)
    read = []
    real_read = Path.read_bytes

    def spy(self):
        read.append(self)
        return real_read(self)
    monkeypatch.setattr(Path, "read_bytes", spy)
    hits = evidence.leaks(out, home=fake_home, login="someone", names=[])
    assert read == []
    assert {h["file"] for h in hits if h["what"] == "symlink (not followed)"} == {"link", "dirlink"}


def test_the_leak_scan_finds_the_real_name(tmp_path: Path):
    (tmp_path / "a.txt").write_text("by Zainab Example on box1", encoding="utf-8")
    hits = evidence.leaks(tmp_path, home=Path("/home/someone"), login="someone", names=["Zainab", "box1"])
    assert {h["what"] for h in hits} == {"real name or host name"}


def test_contrast_isnt_rounded_up_to_the_minimum():
    assert svg.contrast("#777777", "#070707") == 4.5                 # shown rounded
    from textual.app import App
    from textual.widgets import Static

    class Edge(App):
        CSS = "Static { color: #777777; background: #070707; }"

        def compose(self):
            yield Static("edge")
    import asyncio

    async def shot():
        app = Edge()
        async with app.run_test(size=(10, 1)):
            return app.export_screenshot()
    g = svg.parse(asyncio.run(shot()), 10, 1)
    [pair] = svg.pairs(g, (0, 0, 4, 1))
    assert pair["below_minimum"]                                        # 4.498 is below 4.5


def test_the_app_folder_cant_be_a_protected_place(fake_home: Path, monkeypatch):
    from audit import appsrc
    installed = fake_home / ".local" / "lib" / "tele-notify" / "tui-venv" / "site-packages"
    (installed / "telenotify_tui").mkdir(parents=True)
    (installed / "telenotify_tui" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(guard, "real_home", lambda: fake_home)
    with pytest.raises(SystemExit, match="refusing"):
        appsrc.use(installed)


def test_out_must_be_new_or_empty_and_leaks_json_isnt_written_through_a_link(tmp_path: Path):
    decoy = tmp_path / "decoy"
    decoy.write_text("keep me", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    (out / "leaks.json").symlink_to(decoy)
    with pytest.raises(guard.Refused, match="a link"):
        sbx.fresh_out(out)
    (out / "earlier.svg").write_text("x", encoding="utf-8")
    (out / "leaks.json").unlink()
    sbx.fresh_out(out)                                           # earlier plain evidence is fine
    (out / "leaks.json").symlink_to(decoy)
    with pytest.raises(OSError):
        evidence.scan(out)                                       # the leaks job on an existing folder
    assert decoy.read_text(encoding="utf-8") == "keep me"
    (out / "leaks.json").unlink()
    os.link(decoy, out / "leaks.json")                           # a hard link is refused too
    with pytest.raises(guard.Refused, match="a link"):
        sbx.fresh_out(out)
    with pytest.raises(OSError):
        evidence.scan(out)
    assert decoy.read_text(encoding="utf-8") == "keep me"
    new = tmp_path / "new" / "out"
    sbx.fresh_out(new)
    assert new.is_dir() and (new.stat().st_mode & 0o777) == 0o700
    link = tmp_path / "linked-out"
    link.symlink_to(tmp_path / "new")
    with pytest.raises(guard.Refused):
        sbx.fresh_out(link)


def test_the_leak_scan_reads_no_fifo_or_hard_link(fake_home: Path, tmp_path: Path):
    state = fake_home / ".local" / "share" / "tele-notify" / "state.sqlite3"
    state.write_text("someone's data", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    os.mkfifo(out / "pipe")                                      # would block a read forever
    os.link(state, out / "hard")
    hits = evidence.leaks(out, home=fake_home, login="someone", names=[])
    whats = {h["file"]: h["what"] for h in hits}
    assert "not read" in whats["pipe"] and "not read" in whats["hard"]


def test_a_linked_package_counts_as_its_target(fake_home: Path, tmp_path: Path, monkeypatch):
    from audit import appsrc
    pkg = fake_home / ".local" / "lib" / "tele-notify" / "telenotify_tui"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    (app / "telenotify_tui").symlink_to(pkg)
    monkeypatch.setattr(guard, "real_home", lambda: fake_home)
    with pytest.raises(SystemExit, match="refusing"):
        appsrc.use(app)


def test_names_are_found_escaped_and_in_any_case(tmp_path: Path):
    (tmp_path / "a.json").write_text('{"by": "Z\\u00e9na"}', encoding="utf-8")
    (tmp_path / "b.txt").write_text("ZÉNA was here", encoding="utf-8")
    (tmp_path / "c.txt").write_text("/usr/bin/env", encoding="utf-8")       # "bin" is too common to count
    hits = evidence.leaks(tmp_path, home=Path("/home/someone"), login="someone", names=["Zéna", "bin"])
    assert {h["file"] for h in hits} == {"a.json", "b.txt"}


def test_the_repo_binary_runs_without_scraping(tmp_path: Path, monkeypatch):
    sb = sbx.plan(tmp_path / "out", base=tmp_path / "base", use_repo_bin=True)
    monkeypatch.setattr(sbx, "REPO", tmp_path)
    (tmp_path / "telenotify").write_text("", encoding="utf-8")
    sbx.create(sb)
    try:
        sbx.prepare(sb, state=False)
        wrapper = sb.telenotify.read_text(encoding="utf-8")
        assert "--no-scrape" in wrapper and sb.telenotify.parent == sb.bin
    finally:
        sbx.remove(sb)
