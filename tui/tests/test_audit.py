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


# ------------------------------------------------------- the real-terminal job

def _png(*chunks: tuple[bytes, bytes], tail: bytes = b"") -> bytes:
    import zlib
    out = evidence.PNG_SIGNATURE
    for kind, body in chunks:
        out += len(body).to_bytes(4, "big") + kind + body + zlib.crc32(kind + body).to_bytes(4, "big")
    return out + tail


def _ihdr() -> tuple[bytes, bytes]:
    return b"IHDR", (4).to_bytes(4, "big") + (1).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"


def test_the_leak_scan_reads_a_pngs_text_not_its_pixels(tmp_path: Path):
    import zlib
    home, login = Path("/home/someone"), "someone"
    # compressed pixels that happen to hold the login name's bytes: noise, not a leak
    pixels = zlib.compress(b"\x00" + b"someone" + b"\x00" * 4, 0)
    assert b"someone" in pixels
    (tmp_path / "pixels.png").write_bytes(_png(_ihdr(), (b"IDAT", pixels), (b"IEND", b"")))
    assert evidence.leaks(tmp_path, home=home, login=login, names=[]) == []
    # words in text chunks are read, compressed or not
    (tmp_path / "text.png").write_bytes(_png(_ihdr(), (b"tEXt", b"Comment\x00/home/someone/x"), (b"IEND", b"")))
    (tmp_path / "ztext.png").write_bytes(_png(_ihdr(), (b"zTXt", b"Title\x00\x00" + zlib.compress(b"by someone")),
                                              (b"IEND", b"")))
    (tmp_path / "itext.png").write_bytes(_png(_ihdr(), (b"iTXt", b"T\x00\x01\x00en\x00\x00" +
                                                         zlib.compress(b"someone")), (b"IEND", b"")))
    # a file that only starts like a PNG, or has bytes after its end, is read whole
    (tmp_path / "fake.png").write_bytes(evidence.PNG_SIGNATURE + b"someone")
    (tmp_path / "tail.png").write_bytes(_png(_ihdr(), (b"IDAT", pixels), (b"IEND", b""), tail=b"someone"))
    (tmp_path / "badcrc.png").write_bytes(_png(_ihdr())[:-1] + b"\x00" + b"someone")
    hits = {h["file"] for h in evidence.leaks(tmp_path, home=home, login=login, names=[])}
    assert hits == {"text.png", "ztext.png", "itext.png", "fake.png", "tail.png", "badcrc.png"}


def test_the_terminal_shots_are_planned_per_terminal_mode_theme_bidi_and_size():
    from audit import terminal
    shots = terminal.plan_shots(["kitty", "ptyxis"], ["80x24", "200x50"], ["dark", "light"], ["dry-run", "real"],
                                None, {"1-", "keys"})
    assert len(shots) == 2 * 2 * 2 * 2 * 2
    assert {s.scene for s in shots} == {"1-dashboard", "keys-panel"}
    # without --bidi each terminal gets what the screen's auto mode picks there
    assert {s.bidi for s in shots if s.terminal == "kitty"} == {"words"}
    assert {s.bidi for s in shots if s.terminal == "ptyxis"} == {"full"}
    assert shots[0].file == "terminal/kitty/dry-run/dark/bidi-words/80x24/1-dashboard.png"
    assert terminal.plan_shots(["ptyxis"], ["80x24"], ["dark"], ["real"], ["words", "full"])[0].bidi == "words"
    for bad in ((["xterm"], ["80x24"], ["dark"], ["real"], None), (["kitty"], ["80"], ["dark"], ["real"], None),
                (["kitty"], ["80x24"], ["blue"], ["real"], None), (["kitty"], ["80x24"], ["dark"], ["live"], None),
                (["kitty"], ["80x24"], ["dark"], ["real"], ["rtl"])):
        with pytest.raises(ValueError):
            terminal.plan_shots(*bad)
    with pytest.raises(ValueError, match="no scene"):
        terminal.plan_shots(["kitty"], ["80x24"], ["dark"], ["real"], None, {"9-nothing"})


def test_the_terminals_are_started_sized_in_cells_and_isolated(tmp_path: Path):
    from audit import terminal
    cmd = terminal.screen_cmd("/py", Path("/x/tui"), Path("/tmp/tn-audit/r-term/shots/0/spec.json"))
    assert cmd[:2] == ["/py", "-c"] and cmd[-2:] == ["terminal", "/tmp/tn-audit/r-term/shots/0/spec.json"]
    k = terminal.kitty_argv(Path("/k/kitty"), 120, 40, "t", cmd, None)
    assert k[1:3] == ["--config", "NONE"] and "initial_window_width=120c" in k and "initial_window_height=40c" in k
    assert "linux_display_server=x11" in k and f"font_size={terminal.FONT_SIZE}" in k
    assert k[k.index("--title") + 2:] == cmd                     # the command comes last, whole
    mine = terminal.kitty_argv(Path("/k/kitty"), 80, 24, "t", cmd, Path("/c/kitty.conf"))
    assert mine[1:3] == ["--config", "/c/kitty.conf"] and not any(a.startswith("font_size") for a in mine)
    p = terminal.ptyxis_argv(Path("/usr/bin/ptyxis"), "t", cmd)
    assert "-s" in p and p[p.index("--") + 1:] == cmd
    kf = terminal.ptyxis_keyfile(200, 50, "light")
    assert "default-columns=uint32 200" in kf and "default-rows=uint32 50" in kf
    assert "restore-window-size=false" in kf and "interface-style='light'" in kf
    sess = terminal.Session(tmp_path / "r-term")
    env = sess.base_env()
    assert not {"DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XAUTHORITY"} & set(env)
    assert env["XDG_RUNTIME_DIR"] == str(tmp_path / "r-term" / "run") and env["HOME"].startswith(str(tmp_path))
    assert env["GSETTINGS_BACKEND"] == "keyfile"
    # the systemd-run stand-in first, then the system's folders only (terminals are started by absolute path)
    assert env["PATH"] == f"{tmp_path / 'r-term' / 'bin'}:/usr/local/bin:/usr/bin:/bin"
    assert env["GVFS_DISABLE_FUSE"] == "1" and env["GTK_USE_PORTAL"] == "0"
    assert env["FONTCONFIG_FILE"] == str(tmp_path / "r-term" / "fonts.conf")
    sess.display, sess.bus, sess.xauth = ":7", "unix:path=/x", "/a"
    assert sess.env()["DISPLAY"] == ":7" and "WAYLAND_DISPLAY" not in sess.env()
    assert terminal.session_folder(sbx.plan(tmp_path / "o", base=tmp_path / "b")).parent == tmp_path / "b"


def test_the_sessions_path_holds_no_protected_place(fake_home: Path):
    from audit import terminal
    # the real home: no folder on the session's PATH is, or holds, a protected place
    assert terminal.path_problems(terminal.Session(Path("/tmp/tn-audit/x-term")).base_env()["PATH"],
                                  guard.real_home()) == []
    # ~/.local/bin holds the installed telenotify: never on the PATH (kitty's folder used to be put there)
    (fake_home / ".local" / "bin").mkdir(parents=True)
    bad = terminal.path_problems(f"/usr/bin:{fake_home / '.local' / 'bin'}", fake_home)
    assert len(bad) == 1 and "telenotify" in bad[0]
    sess = terminal.Session(fake_home / "s-term")
    sess.base_env = lambda: {"PATH": str(fake_home / ".local" / "bin")}      # as if it were on the PATH
    with pytest.raises(RuntimeError, match="refusing the session's PATH"):
        sess.start(home=fake_home)
    assert not (fake_home / "s-term").exists()                                 # refused before anything is made


def test_the_private_fonts_conf_adds_the_font_folder_without_linking_it(tmp_path: Path):
    from audit import terminal
    conf = terminal.FONTS_CONF.format(dirs=f"  <dir>{tmp_path / 'fonts'}</dir>\n", cache=tmp_path / "cache")
    assert "<include ignore_missing=\"yes\">/etc/fonts/fonts.conf</include>" in conf
    assert f"<dir>{tmp_path / 'fonts'}</dir>" in conf and f"<cachedir>{tmp_path / 'cache'}</cachedir>" in conf


def _stand_in(tmp_path: Path) -> Path:
    from audit import terminal
    stand_in = tmp_path / "systemd-run"
    stand_in.write_text(terminal.SYSTEMD_RUN, encoding="utf-8")
    stand_in.chmod(0o700)
    return stand_in


def test_the_systemd_run_stand_in_says_no_and_runs_a_command_without_a_scope(tmp_path: Path):
    stand_in, log = _stand_in(tmp_path), tmp_path / "calls.log"

    def run(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(stand_in), *argv], capture_output=True, text=True, timeout=10, check=False,
                              env={"PATH": "/usr/bin:/bin", "TN_AUDIT_SCOPE_LOG": str(log)})
    # Ptyxis's probe: no systemd here, so it starts the tab itself
    assert run("--version").returncode == 1
    for argv in (["--user", "--scope", "--collect", "--quiet", "--", "echo", "hi", "--x"],
                 ["--user", "--scope", "--unit", "u1", "-p", "Delegate=yes", "--same-dir", "echo", "hi", "--x"],
                 ["--user", "--scope", "--unit=u2", "echo", "hi", "--x"]):
        r = run(*argv)
        assert (r.returncode, r.stdout) == (0, "hi --x\n"), argv
    assert run("--user", "--scope", "echo", "a", "--", "b").stdout == "a -- b\n"     # the command's own "--"
    for bad in (["--user", "--scope"], ["--user", "--"], ["--user", "-p"]):           # nothing to run
        r = run(*bad)
        assert r.returncode == 1 and r.stdout == "" and "systemd-run:" in r.stderr, bad
    assert len(log.read_text(encoding="utf-8").splitlines()) == 8


def test_mounts_inside_the_session_folder_are_found():
    from audit import terminal
    mounts = ("proc /proc proc rw 0 0\n"
              "gvfsd-fuse /tmp/tn-audit/r-term/run/gvfs fuse.gvfsd-fuse rw 0 0\n"
              "x /tmp/tn-audit/r-term-other/run x rw 0 0\n"
              "y /tmp/tn-audit/r-term/run/doc\\040x fuse rw 0 0\n")
    assert terminal.mounts_under(Path("/tmp/tn-audit/r-term"), mounts) == [
        "/tmp/tn-audit/r-term/run/doc x", "/tmp/tn-audit/r-term/run/gvfs"]


def test_processes_are_found_by_their_mark_and_ended(tmp_path: Path):
    from audit import terminal
    proc = tmp_path / "proc"
    for pid, env in (("101", b"A=1\0TN_AUDIT_TERM_SHOT=/x/3\0"), ("102", b"TN_AUDIT_TERM_SHOT=/x/30\0"),
                     ("103", b"B=TN_AUDIT_TERM_SHOT=/x/3\0"), ("self", b"TN_AUDIT_TERM_SHOT=/x/3\0")):
        (proc / pid).mkdir(parents=True)
        (proc / pid / "environ").write_bytes(env)
    assert terminal.marked_pids(terminal.SHOT_MARK, "/x/3", str(proc)) == [101]
    # a real one, started in a session of its own like the screen child
    p = subprocess.Popen(["sleep", "30"], env={"PATH": "/usr/bin:/bin", terminal.SHOT_MARK: str(tmp_path)},
                         start_new_session=True)
    unmarked = subprocess.Popen(["sleep", "30"], env={"PATH": "/usr/bin:/bin"})
    try:
        assert terminal.marked_pids(terminal.SHOT_MARK, str(tmp_path)) == [p.pid]
        # a PID without the mark (as if reused by another process) is never signalled
        assert terminal.end_marked(terminal.SHOT_MARK, str(tmp_path), only=[unmarked.pid]) == 0
        assert unmarked.poll() is None
        terminal.end_marked(terminal.SHOT_MARK, str(tmp_path))
        assert p.wait(5) is not None and terminal.marked_pids(terminal.SHOT_MARK, str(tmp_path)) == []
    finally:
        for q in (p, unmarked):
            q.kill()
            q.wait()


def test_a_reaped_groups_id_is_never_signalled(monkeypatch):
    from audit import terminal
    p = subprocess.Popen(["true"], start_new_session=True)
    p.wait()                                                # reaped: its group ID may be someone else's now
    sent = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: sent.append((pgid, sig)))
    terminal._kill_group(p)
    assert sent == []
    q = subprocess.Popen(["sleep", "30"], start_new_session=True)
    monkeypatch.undo()
    terminal._kill_group(q, grace=2)                         # unreaped until the group is signalled
    assert q.returncode is not None


def test_both_of_mutters_displays_are_read_from_its_log():
    from audit import terminal
    log = ("libmutter-Message: Using public X11 display :2, (using unix:/tmp/.X11-unix/X3 for managed "
           "services)\nother line\n")
    assert terminal.x_displays(log) == [":2", ":3"]
    assert terminal.x_displays("nothing here") == []


def test_a_dead_displays_lock_and_socket_are_removed_only_if_ours_and_dead(tmp_path: Path):
    from audit import terminal
    dead = subprocess.Popen(["true"])
    dead.wait()
    (tmp_path / ".X11-unix").mkdir()
    (tmp_path / ".X7-lock").write_text(f"{dead.pid:>10}\n", encoding="ascii")
    (tmp_path / ".X11-unix" / "X7").write_text("", encoding="ascii")
    (tmp_path / ".X8-lock").write_text(f"{os.getpid():>10}\n", encoding="ascii")     # still in use
    (tmp_path / ".X11-unix" / "X8").write_text("", encoding="ascii")
    (tmp_path / ".X11-unix" / "X9").write_text("", encoding="ascii")                # a socket with no lock
    assert len(terminal.clear_x_display(":7", str(tmp_path))) == 2
    assert not (tmp_path / ".X7-lock").exists() and not (tmp_path / ".X11-unix" / "X7").exists()
    assert terminal.clear_x_display(":8", str(tmp_path)) == [] and (tmp_path / ".X8-lock").exists()
    assert terminal.clear_x_display(":9", str(tmp_path)) == [] and (tmp_path / ".X11-unix" / "X9").exists()
    assert terminal.clear_x_display("", str(tmp_path)) == []


def test_stale_session_folders_are_swept_but_not_live_ones(tmp_path: Path):
    from audit import terminal
    stale = tmp_path / "terminal-20261119-200000-11-term"          # its lock file isn't held
    live = tmp_path / "terminal-20261119-200000-12-term"           # held, as a running run holds it
    young = tmp_path / "terminal-20261119-200000-13-term"          # no lock yet: just being made
    old = tmp_path / "terminal-20261119-200000-14-term"            # no lock, long ago: a killed old run
    other = tmp_path / "screenshots-20261119-200000-11"
    for d in (stale / "run", live / "run", young / "run", old / "run", other):
        d.mkdir(parents=True)
    (stale / "lock").write_text("", encoding="ascii")
    (stale / "displays").write_text(":7\n:8", encoding="ascii")
    os.utime(old, (0, 0))
    held = terminal.hold_folder(live)
    try:
        assert terminal.sweep_stale(tmp_path) == [stale.name, old.name]
    finally:
        os.close(held)
    assert not stale.exists() and not old.exists()
    assert live.exists() and young.exists() and other.exists()


def _cells(*cells: tuple[tuple[int, int, int], tuple[int, int, int] | None, int]) -> bytes:
    """One row of 4x2-pixel cells: (background, ink or None, how many of the cell's 8 pixels are ink)."""
    rows = []
    for y in range(2):
        line = b""
        for bg, ink, n in cells:
            px = [bytes(bg)] * 4
            if ink is not None:
                for i in range(4 * y, min(n, 4 * y + 4)):
                    px[i - 4 * y] = bytes(ink)
            line += b"".join(px)
        rows.append(line)
    return b"".join(rows)


def test_the_footer_reading_finds_each_run_and_the_lowest_contrast():
    from audit import terminal
    grey, black, white, blue = (208, 208, 208), (31, 31, 31), (255, 255, 255), (1, 120, 212)
    mid, light_u, dim, divider = (163, 163, 163), (100, 100, 100), (126, 126, 126), (172, 172, 172)
    blank = (grey, None, 0)
    pix = _cells((grey, blue, 3), (grey, black, 3), (grey, mid, 2), (grey, light_u, 3), (grey, black, 3), blank,
                 (grey, dim, 3), (grey, dim, 3), blank, (grey, divider, 3), (black, white, 2), blank,
                 (grey, black, 3), (grey, mid, 3))
    cells = terminal.cell_colours(pix, 14 * 4, 4, 2, 14)
    assert cells[0] == ("#d0d0d0", "#0178d4", True) and cells[2] == ("#d0d0d0", "#a3a3a3", False)
    assert cells[5] == ("#d0d0d0", None, False)
    found = terminal.runs(cells, "kQ/ui Un ▏. ab", 10)
    assert [(r["cells"], r["text"], r["ink"], r["thin"], r["graphic"]) for r in found] == [
        ([10, 10], "k", "#0178d4", False, False),
        ([11, 14], "Q/ui", "#1f1f1f", False, False),      # lighter glyphs inside a word join it
        ([16, 17], "Un", "#7e7e7e", False, False),        # a word in a dimmer ink keeps its own run
        ([19, 19], "▏", "#acacac", False, True),          # a divider: lines and blocks, read apart from text
        ([20, 20], ".", "#ffffff", False, False),         # a word of its own is never thin
        ([22, 22], "a", "#1f1f1f", False, False),
        ([23, 23], "b", "#a3a3a3", True, False)]          # lighter than its word's ink: thin
    assert found[0]["contrast"] == 2.93                   # the key letter (2.937, rounded down)
    low = terminal.lowest(found)
    assert low["min_contrast"] == 2.63 and low["lowest"]["text"] == "Un"
    assert low["min_graphic_contrast"] == 1.47
    assert [t["text"] for t in low["thin_below_4_5"]] == ["b"]                # left out, but listed
    shown = terminal._lowest(low)
    assert shown.startswith("2.63 (`Un`") and "lines 1.47" in shown and "left out (thin): `b`" in shown


def test_a_shot_in_another_state_than_asked_is_an_error():
    from audit import terminal
    s = terminal.Shot("kitty", "80x24", "light", "real", "words", "keys-panel")
    ok = {"cols": 80, "rows": 24, "theme": "textual-light", "tab": "dashboard", "keys_panel": True}
    assert terminal.mismatches(s, ok) == []
    wrong = terminal.mismatches(s, {**ok, "cols": 79, "theme": "textual-dark", "tab": "review", "keys_panel": False})
    assert len(wrong) == 4 and "size 79x24" in wrong[0]
    assert terminal.mismatches(terminal.Shot("kitty", "80x24", "dark", "real", "words", "3-search"),
                               {**ok, "theme": "textual-dark", "tab": "search", "keys_panel": False}) == []


def test_the_leak_scan_caps_decompression_and_says_where_it_found(tmp_path: Path, monkeypatch):
    import zlib
    monkeypatch.setattr(evidence, "MAX_SCAN", 100)
    (tmp_path / "late.png").write_bytes(_png(_ihdr(), (b"zTXt", b"T\x00\x00" + zlib.compress(b"x" * 1000 +
                                                                                           b"someone")),
                                             (b"IEND", b"")))
    (tmp_path / "early.png").write_bytes(_png(_ihdr(), (b"zTXt", b"T\x00\x00" + zlib.compress(b"by someone")),
                                              (b"IEND", b"")))
    hits = evidence.leaks(tmp_path, home=Path("/home/someone"), login="someone", names=[])
    assert [h["file"] for h in hits] == ["early.png"]
    assert hits[0]["at"].startswith("zTXt chunk at byte 33, decompressed")


def test_the_terminal_summary_names_errors_and_the_lowest_contrast():
    from audit import terminal
    s = terminal.Shot("kitty", "80x24", "dark", "real", "words", "1-dashboard")
    low = {"text": "q", "ink": "#0178d4", "background": "#d0d0d0", "contrast": 2.94}
    rows = [{**terminal._row(s, {"kitty": "kitty 0.47.0"}), "cells_seen": "80x24", "image_px": [1, 1],
             "rows_read": {"footer": {"min_contrast": 2.94, "lowest": low}}},
            {**terminal._row(s, {}), "error": "the screen didn't get ready"}]
    md = terminal.summary(rows, {"compositor": "mutter 50", "monitor": "1x1", "font": "f", "kitty_config": "NONE"},
                          "mutter died")
    assert "stopped early:** mutter died" in md and "80x24 (80x24)" in md
    assert "2.94 (`q` #0178d4 on #d0d0d0)" in md and "ERROR: the screen didn't get ready" in md


def test_the_terminal_job_refuses_a_protected_kitty_config_and_bad_sizes(out: Path):
    conf = guard.real_home() / ".config" / "tele-notify" / "kitty.conf"
    r = audit("terminal", "--kitty-config", str(conf), "--out", str(out / "t"))
    assert r.returncode == 2 and "refusing" in r.stderr and not (out / "t").exists()
    link = out / "innocent-kitty.conf"                      # a link into a protected place counts as its target
    link.symlink_to(conf)
    r = audit("terminal", "--kitty-config", str(link), "--out", str(out / "t"))
    assert r.returncode == 2 and "refusing" in r.stderr and not (out / "t").exists()
    r = audit("terminal", "--sizes", "80by24", "--out", str(out / "t"))
    assert r.returncode == 2 and "COLUMNSxROWS" in r.stderr and not (out / "t").exists()


def _live() -> bool:
    from audit import terminal
    return os.environ.get("TN_AUDIT_LIVE") == "1" and "kitty" in terminal.available(guard.real_home())


@pytest.mark.skipif(not _live(), reason="live: needs mutter, Xwayland, xdotool, ImageMagick, kitty and TN_AUDIT_LIVE=1")
def test_the_terminal_job_captures_a_real_kitty_window(out: Path):
    from audit import terminal
    r = audit("terminal", "--terminals", "kitty", "--sizes", "80x24", "--modes", "dry-run", "--only", "1-dash",
              "--out", str(out), timeout=300)
    assert r.returncode == 0, r.stderr + r.stdout
    [row] = json.loads((out / "terminal" / "index.json").read_text(encoding="utf-8"))
    assert {"terminal", "version", "size", "theme", "mode", "bidi", "file"} <= set(row) and "error" not in row
    assert row["cells_seen"] == "80x24" and row["bidi"] == "words" and row["term_env"]["TERM"] == "xterm-kitty"
    assert (row["theme_seen"], row["tab_seen"], row["keys_panel_seen"]) == ("textual-dark", "dashboard", False)
    png = out / row["file"]
    assert png.read_bytes().startswith(evidence.PNG_SIGNATURE) and row["image_px"][0] > 300
    assert "Dashboard" in (out / row["text_file"]).read_text(encoding="utf-8")     # what was drawn, for the scan
    footer = row["rows_read"]["footer"]
    assert footer["cells"] == [0, 23, 80, 1] and footer["runs"]
    assert footer["min_contrast"] == min(r["contrast"] for r in footer["runs"] if not r["thin"] and not r["graphic"])
    assert any("Quit" in r["text"] for r in footer["runs"])
    assert json.loads((out / "leaks.json").read_text())["clean"]
    # this run's session is gone: its folder, mounts, processes and X display
    session = json.loads((out / "run-terminal.json").read_text(encoding="utf-8"))["result"]["session"]
    folder = Path("/tmp/tn-audit") / session["folder"]
    assert not folder.exists() and terminal.mounts_under(folder) == []
    assert terminal.marked_pids(terminal.SESSION_MARK, str(folder)) == []
    assert session["display"] in session["displays"] and len(session["displays"]) == 2   # public + services
    for n in (d.lstrip(":") for d in session["displays"]):
        assert not Path(f"/tmp/.X{n}-lock").exists() and not Path(f"/tmp/.X11-unix/X{n}").exists()
