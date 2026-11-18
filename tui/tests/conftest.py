"""Fixtures: a state file shaped like the Nim bot's, with a few rows, and a config."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from telenotify_tui.paths import Paths

REPO = Path(__file__).resolve().parents[2]

# Kept in step with src/telenotify/state.nim (plus the alerts / chats tables
# the bot adds for this screen).
SCHEMA = """
CREATE TABLE processed (chat_id INTEGER, msg_id INTEGER, chat_title TEXT, verdict TEXT, role TEXT,
  reasons TEXT, action TEXT, at INTEGER, text_hash TEXT, raw_text TEXT, album_id INTEGER,
  PRIMARY KEY (chat_id, msg_id));
CREATE TABLE published (id INTEGER PRIMARY KEY, chat_id INTEGER, msg_id INTEGER, text TEXT, at INTEGER);
CREATE TABLE reviews (id INTEGER PRIMARY KEY, chat_id INTEGER, msg_id INTEGER, chat_title TEXT, text TEXT,
  reasons TEXT, role TEXT, link TEXT, status TEXT DEFAULT 'pending', at INTEGER, decided_at INTEGER,
  album_ids TEXT, UNIQUE (chat_id, msg_id));
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE alerts (id INTEGER PRIMARY KEY, at INTEGER, text TEXT);
CREATE TABLE chats (id INTEGER PRIMARY KEY, title TEXT, kind TEXT, watched INTEGER, status TEXT,
  updated_at INTEGER);
"""

GOOD = "مطلوب مهندس كهرباء حديث التخرج للعمل في البصرة"
UNCLEAR = "شركة في البصرة تعلن عن حاجتها الى مهندس كهرباء\nارسال السيرة الذاتية"
BAD = "مطلوب محاسب خبرة 5 سنوات في بغداد"


def fill(db: sqlite3.Connection, now: int) -> None:
    rows = [
        (-1, 10, "Basra Jobs", "publish", "مهندس كهرباء", "role; location: basra; entry level", "forwarded", now - 60, GOOD),
        (-1, 11, "Basra Jobs", "review", "مهندس كهرباء", "role; no sign of level either way", "queued for review", now - 50, UNCLEAR),
        (-2, 5, "Iraq Jobs", "reject", "", "no matching role", "rejected", now - 40, BAD),
        (-2, 6, "Iraq Jobs", "reject", "", "no job-advert wording", "rejected", now - 3 * 86400, "hello"),
    ]
    db.executemany("""INSERT INTO processed (chat_id, msg_id, chat_title, verdict, role, reasons, action, at, raw_text)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
    db.execute("""INSERT INTO reviews (chat_id, msg_id, chat_title, text, reasons, role, link, status, at, album_ids)
                  VALUES (-1, 11, 'Basra Jobs', ?, 'no sign of level either way', 'مهندس كهرباء',
                  'https://t.me/c/1/11', 'pending', ?, '11')""", (UNCLEAR, now - 50))
    db.execute("""INSERT INTO reviews (chat_id, msg_id, chat_title, text, reasons, role, link, status, at)
                  VALUES (-1, 9, 'Basra Jobs', 'older post', 'no sign of level', 'it support', '', 'sent', ?)""",
               (now - 7200,))
    db.executemany("INSERT INTO meta VALUES (?, ?)",
                   [("last_alive", str(now - 30)), ("started_at", str(now - 3600)), ("mode", "dry-run"),
                    ("connection", "ready")])
    db.execute("INSERT INTO alerts (at, text) VALUES (?, ?)", (now - 600, "no connection to Telegram for 12m. Back now."))
    db.executemany("INSERT INTO chats VALUES (?, ?, ?, ?, ?, ?)",
                   [(-1, "Basra Jobs", "channel", 1, "ok", now), (-2, "Iraq Jobs", "channel", 1, "ok", now),
                    (-3, "Blocked Channel", "channel", 1, "banned", now), (-4, "Family", "group", 0, "ok", now)])
    db.commit()


@pytest.fixture
def now() -> int:
    return int(time.time())


@pytest.fixture
def config(tmp_path: Path) -> Path:
    cfg = json.loads((REPO / "tests" / "keywords.json").read_text(encoding="utf-8"))
    cfg.update({"data_dir": str(tmp_path / "data"), "chats": [-1, -2, -3], "target_chat": -999})
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def state(tmp_path: Path, now: int) -> Path:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    path = data / "state-dryrun.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    fill(db, now)
    db.close()
    return path


@pytest.fixture
def binary() -> Path:
    """The real telenotify program built in the repo (tests needing it are skipped otherwise)."""
    b = REPO / "telenotify"
    if not b.exists():
        pytest.skip("telenotify isn't built (nimble build -d:release)")
    return b


@pytest.fixture
def paths(config: Path, state: Path, tmp_path: Path) -> Paths:
    b = REPO / "telenotify"
    return Paths(config=config, data_dir=state.parent, state=state, binary=b if b.exists() else None, dry_run=True)


@pytest.fixture(autouse=True)
def no_decide_delay(monkeypatch):
    """Tests press s / d right after a post appears; the double-tap guard is
    tested on its own (test_review.py sets the delay back)."""
    from telenotify_tui.screens.review import ReviewPane

    monkeypatch.setattr(ReviewPane, "DECIDE_DELAY", 0.0)


@pytest.fixture(autouse=True)
def no_systemctl(monkeypatch):
    """Tests never talk to the real systemd: the services look stopped and
    actions are refused (tests that need other answers patch these again)."""
    from telenotify_tui import bot

    async def state(mode: str) -> str:
        return "inactive"

    async def action(mode: str, action: str):
        raise AssertionError("a test tried to control a real service")

    monkeypatch.setattr(bot, "service_state", state)
    monkeypatch.setattr(bot, "service_action", action)


@pytest.fixture(autouse=True)
def bidi_default():
    """The screen's own bidi on, as users get it, whatever $TELENOTIFY_BIDI
    says where the tests run (test_rtl.py turns it off where it needs to)."""
    from telenotify_tui import rtl

    was = rtl.enabled()
    rtl.set_enabled(True)
    yield
    rtl.set_enabled(was)
