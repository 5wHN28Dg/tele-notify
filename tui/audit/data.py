"""Synthetic data only: invented posts, chats, keywords and reviews.

Links and emails are at example.com, phone numbers are invented (555-01xx
style). Nothing here is copied or edited from real posts.

* `small_state()` / `config()`: the everyday evidence set, built fresh in
  the sandbox for every run (times relative to now).
* `big_state()`: the fixed large file for R7 (1 000 000 judged posts,
  2 000 reviews, 300 chats), built once under ~/.cache/agent-tmp/tn-audit/
  and reused while its manifest matches.

The tables follow src/telenotify/state.nim, columns added later included
(tests/test_audit.py checks them against state.nim).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import random
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed (
  chat_id INTEGER, msg_id INTEGER, chat_title TEXT, verdict TEXT, role TEXT,
  reasons TEXT, action TEXT, at INTEGER, text_hash TEXT, raw_text TEXT, album_id INTEGER,
  PRIMARY KEY (chat_id, msg_id));
CREATE TABLE IF NOT EXISTS published (
  id INTEGER PRIMARY KEY, chat_id INTEGER, msg_id INTEGER, text TEXT, at INTEGER);
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY, chat_id INTEGER, msg_id INTEGER, chat_title TEXT, text TEXT,
  reasons TEXT, role TEXT, link TEXT, status TEXT DEFAULT 'pending', at INTEGER,
  decided_at INTEGER, album_ids TEXT, UNIQUE (chat_id, msg_id));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY, at INTEGER, text TEXT);
CREATE TABLE IF NOT EXISTS chats (
  id INTEGER PRIMARY KEY, title TEXT, kind TEXT, watched INTEGER, status TEXT, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS other_bot (
  id INTEGER PRIMARY KEY, at INTEGER, msg_id INTEGER, origin_chat INTEGER,
  origin_msg INTEGER, text TEXT, album_id INTEGER, UNIQUE (msg_id));
"""
TABLES = ("processed", "published", "reviews", "meta", "alerts", "chats", "other_bot")

# ------------------------------------------------------------------ posts

LONG_AR = (
    "مطلوب مهندس كهرباء حديث التخرج للعمل في شركة نور الجنوب للمقاولات (عاجل) 🔌\n"
    "\n"
    "المتطلبات:\n"
    "- شهادة بكالوريوس هندسة كهربائية\n"
    "- خبرة 0-2 سنوات في الصيانة أو التصميم\n"
    "- إجادة برنامج AutoCAD و Excel\n"
    "\n"
    "الراتب: 900,000 دينار شهرياً + سكن\n"
    "للتقديم ارسل السيرة الذاتية الى jobs@example.com او عبر الرابط https://example.com/apply/4471\n"
    "للاستفسار: 0555 0100 147 (واتساب فقط)\n"
    "آخر موعد للتقديم 2026-12-01"
)
POSTS = [
    LONG_AR,
    "شركة الواحة للبرمجيات تبحث عن مطور Python مبتدئ للعمل عن بعد. ارسل سيرتك الى hr@example.com",
    "Hiring: junior network technician in Basra, fresh graduates welcome. Apply: https://example.com/jobs/net-7",
    "مطلوب محاسب خبرة 5 سنوات في بغداد، الراتب حسب المقابلة 📊",
    "دورة مجانية في Excel للمبتدئين! سجل الآن https://example.com/course",
    "We are looking for a Senior Electrical Engineer (10+ years) for an oil field project. CV to cv@example.com",
    "فرصة تدريب صيفي لطلاب هندسة الحاسوب 👩‍💻 في البصرة — المقاعد محدودة",
    "مطلوب فني دعم Help Desk حديث التخرج، الدوام 8 ساعات، التواصل 0555 0100 162",
    "Internship: data analyst (Arabic/English), remote, 3 months. Details: https://example.com/i/da-2026",
    "كَتَبَ المُدِيرُ: نَحتاجُ مُهَندِسَ صِيانةٍ في الزُبَير",
    "مطلوب مهندس مدني\u202e مع تجاوز اتجاه\u202c للعمل في أم قصر",
    "A very long word: " + "Supercalifragilisticexpialidocious" * 3 + " and then the rest of the line.",
    "مهندسينكهرباءبدونمسافات" * 4 + " نهاية",
    "Flags and families: 🇮🇶 👨‍👩‍👧‍👦 1️⃣ ✅ — mixed with عربي and 123.",
    "رقم الهاتف +1 555 0100 199 والبريد info@example.com والرابط https://example.com/a?b=1&c=2",
    "مطلوب مبرمج PHP/Laravel (خبرة سنة) في شركة [الرافدين] للحلول — الراتب 1,200$",
]
CHAT_TITLES = [
    "Basra Jobs", "وظائف البصرة", "Iraq Engineers Network", "فرص عمل العراق 🇮🇶", "Remote Tech Jobs",
    "تدريب وتطوير", "Oil & Gas Careers", "قناة الإعلانات", "Fresh Graduates Hub",
    "مجموعة مهندسي الجنوب — للنقاش والإعلانات والوظائف والفرص التدريبية الطويلة جداً",
    "Blocked Channel", "Family",
]
ROLES = ["مهندس كهرباء", "electrical engineer", "help desk", "فني دعم", "python developer", "", "network technician"]
REASONS = {
    "publish": ["role; location: basra; entry level", "role; remote; fresh graduate"],
    "review": ["role; no sign of level either way", "role; place unclear"],
    "reject": ["no matching role", "no job-advert wording", "senior: 10+ years", "not a job: course"],
}
ACTIONS = {"publish": "forwarded", "review": "queued for review", "reject": "rejected"}
ALERTS = [
    "no connection to Telegram for 12m. Back now.",
    "Basra Jobs: no posts for 3 days",
    "تعذر قراءة قناة الإعلانات: لا صلاحية",
    "the review queue has 12 posts waiting",
]

KEYWORDS = {
    "level_keywords_en": ["intern", "fresh graduate", "junior", "entry level"],
    "level_keywords_ar": ["متدرب", "حديث التخرج", "حديثي التخرج", "بدون خبرة"],
    "entry_level_role_en": ["help desk", "network technician"],
    "entry_level_role_ar": ["فني دعم", "فني شبكات"],
    "mid_level_general_role_en": ["electrical engineer", "python developer", "data analyst"],
    "mid_level_general_role_ar": ["مهندس كهرباء", "مهندس صيانة", "مطور بايثون"],
    "field_keywords_en": ["computer science"],
    "field_keywords_ar": ["علوم حاسبات", "هندسة كهربائية"],
    "location_keywords_en": ["basra", "zubair", "umm qasr", "remote"],
    "location_keywords_ar": ["البصرة", "الزبير", "أم قصر", "عن بعد"],
    "extra_other_places": ["baghdad", "بغداد"],
    "certifications": ["PMP", "CCIE"],
    "extra_not_a_job": ["دورة", "course"],
}


def config(data_dir: Path, *, dry_run: bool = True, chats: list[int] | None = None, **extra: object) -> dict:
    """The sandbox's config.json: invented keywords, no api_id / api_hash."""
    cfg: dict = dict(KEYWORDS)
    cfg.update({"chats": chats if chats is not None else [-1001, -1002, -1003, -1011],
                "target_chat": -1999, "data_dir": str(data_dir), "dry_run": dry_run,
                "alerts": True, "alert_after_minutes": 10, "max_experience_years": 1})
    cfg.update(extra)
    return cfg


def write_config(path: Path, cfg: dict) -> None:
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _hash(s: str) -> str:
    return hashlib.blake2b(s.encode(), digest_size=8).hexdigest()


def create(db: sqlite3.Connection, *, skip: tuple[str, ...] = ()) -> None:
    for stmt in SCHEMA.split(";"):
        name = next((t for t in TABLES if f"EXISTS {t} " in stmt), None)
        if stmt.strip() and name not in skip:
            db.execute(stmt)


def small_state(path: Path, now: int | None = None, *, dry_run: bool = True,
                skip_tables: tuple[str, ...] = (), last_alive_ago: int = 30,
                posts: list[str] | None = None, titles: list[str] | None = None,
                alerts: list[str] | None = None, link: str | None = None) -> None:
    """A small state file shaped like the bot's, times relative to `now`.
    `posts`, `titles`, `alerts` and `link` replace the synthetic texts (hostile-text runs)."""
    now = int(time.time()) if now is None else now
    posts = POSTS if posts is None else posts
    titles = CHAT_TITLES if titles is None else titles
    alerts = ALERTS if alerts is None else alerts
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    create(db, skip=skip_tables)
    chat_ids = [-1001 - i for i in range(len(titles))]
    rows = []
    verdicts = ["publish", "review", "reject", "reject", "review", "reject", "publish", "review"]
    for i in range(160):
        v = verdicts[i % len(verdicts)]
        text = posts[i % len(posts)]
        ci = i % min(len(titles), 10)
        rows.append((chat_ids[ci], 100 + i, titles[ci], v, ROLES[i % len(ROLES)],
                     REASONS[v][i % len(REASONS[v])], ACTIONS[v], now - 40 - i * 1500, _hash(text), text,
                     None))
    if "processed" not in skip_tables:
        db.executemany("INSERT INTO processed VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    if "reviews" not in skip_tables:
        pending = [r for r in rows if r[3] == "review"][:8]
        for j, r in enumerate(pending):
            db.execute("""INSERT INTO reviews (chat_id, msg_id, chat_title, text, reasons, role, link, status,
                          at, album_ids) VALUES (?,?,?,?,?,?,?,'pending',?,?)""",
                       (r[0], r[1], r[2], r[9], r[5], r[4],
                        link if link is not None else f"https://example.com/t/{-r[0]}/{r[1]}",
                        now - 300 * (8 - j), str(r[1])))
        for j, (status, r) in enumerate(zip(("sent", "discarded", "kept", "to_send", "sent", "discarded"),
                                            [r for r in rows if r[3] == "review"][8:14], strict=False)):
            db.execute("""INSERT INTO reviews (chat_id, msg_id, chat_title, text, reasons, role, link, status,
                          at, decided_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                       (r[0], r[1], r[2], r[9], r[5], r[4], f"https://example.com/t/{-r[0]}/{r[1]}", status,
                        now - 86400 - j * 3600, now - 80000 - j * 3600))
    if "meta" not in skip_tables:
        db.executemany("INSERT INTO meta VALUES (?, ?)",
                       [("last_alive", str(now - last_alive_ago)), ("started_at", str(now - 3 * 3600)),
                        ("mode", "dry-run" if dry_run else "real"), ("connection", "ready")])
    if "alerts" not in skip_tables:
        db.executemany("INSERT INTO alerts (at, text) VALUES (?, ?)",
                       [(now - 600 * (k + 1), a) for k, a in enumerate(alerts)])
    if "chats" not in skip_tables:
        statuses = ["ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok", "banned", "ok"]
        db.executemany("INSERT INTO chats VALUES (?, ?, ?, ?, ?, ?)",
                       [(cid, t, "group" if t == "Family" else "channel", int(k < 4 or k == 10),
                         statuses[k % len(statuses)], now - 3600) for k, (cid, t) in enumerate(zip(chat_ids, titles))])
    db.commit()
    db.close()


# ------------------------------------------------------------ large file

BIG_VERSION = 1
BIG_POSTS, BIG_REVIEWS, BIG_CHATS = 1_000_000, 2_000, 300
TEXT_KEPT = 20_000      # the latest posts keep their text (the bot clears it after keep_text_days)
SPAN_DAYS = 730


def _big_rows(n: int, chats: int, start: int, span: int, rng: random.Random) -> Iterator[tuple]:
    msg = [1000] * chats
    titles = _big_titles(chats)
    album = 0
    i = 0
    while i < n:
        # a few busy chats and a long tail, every chat with some posts
        c = rng.randrange(chats) if rng.random() < 0.3 else min(int(rng.paretovariate(1.2)) - 1, chats - 1)
        r = rng.random()
        v = "reject" if r < 0.9 else "review" if r < 0.95 else "publish"
        at = start + (i * span) // n
        size = 3 if rng.random() < 0.01 else 1               # some albums
        album_id = 0
        if size > 1:
            album += 1
            album_id = 10_000_000 + album
        for _ in range(min(size, n - i)):
            msg[c] += 1
            text = POSTS[(i * 7 + c) % len(POSTS)] if i >= n - TEXT_KEPT else None
            yield (-1001 - c, msg[c], titles[c], v, ROLES[i % len(ROLES)], REASONS[v][i % len(REASONS[v])],
                   ACTIONS[v], at, f"{i:016x}", text, album_id)
            i += 1


def _big_titles(chats: int) -> list[str]:
    base = [t for t in CHAT_TITLES if t not in ("Family",)]
    return [f"{base[k % len(base)]} {k}" for k in range(chats)]


def build_big(path: Path, posts: int = BIG_POSTS, reviews: int = BIG_REVIEWS, chats: int = BIG_CHATS,
              now: int | None = None, seed: int = 7) -> dict:
    """Build the large state file at `path` (via a temporary file); returns its manifest."""
    now = int(time.time()) if now is None else now
    rng = random.Random(seed)
    tmp = path.with_name(path.name + ".building")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    create(db)
    start = now - SPAN_DAYS * 86400
    db.executemany("INSERT INTO processed VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   _big_rows(posts, chats, start, SPAN_DAYS * 86400, rng))
    picked = db.execute("SELECT chat_id, msg_id, chat_title, role, reasons, at FROM processed "
                        "WHERE verdict = 'review' ORDER BY at DESC LIMIT ?", (reviews,)).fetchall()
    rows = []
    for k, (cid, mid, title, role, reasons, at) in enumerate(picked):
        status = "pending" if k < 40 else ("sent", "discarded", "kept", "sent", "discarded")[k % 5]
        rows.append((cid, mid, title, POSTS[k % len(POSTS)], reasons, role, f"https://example.com/t/{-cid}/{mid}",
                     status, at, None if status == "pending" else at + 600, str(mid)))
    db.executemany("""INSERT INTO reviews (chat_id, msg_id, chat_title, text, reasons, role, link, status, at,
                      decided_at, album_ids) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)
    titles = _big_titles(chats)
    db.executemany("INSERT INTO chats VALUES (?, ?, ?, ?, ?, ?)",
                   [(-1001 - c, titles[c], "channel", int(c < 40), "banned" if c % 97 == 5 else "ok", now)
                    for c in range(chats)])
    db.executemany("INSERT INTO meta VALUES (?, ?)",
                   [("last_alive", str(now)), ("started_at", str(now - 86400)), ("mode", "dry-run"),
                    ("connection", "ready")])
    db.executemany("INSERT INTO alerts (at, text) VALUES (?, ?)",
                   [(now - 3600 * k, ALERTS[k % len(ALERTS)]) for k in range(200)])
    db.commit()
    db.execute("PRAGMA journal_mode=WAL")
    db.close()
    os.chmod(tmp, 0o444)   # the master is never opened for writing: runs use copies
    os.replace(tmp, path)
    st = path.stat()
    return {"version": BIG_VERSION, "posts": posts, "reviews": reviews, "chats": chats, "built_at": now,
            "seed": seed, "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def big_state(cache: Path, posts: int = BIG_POSTS, reviews: int = BIG_REVIEWS, chats: int = BIG_CHATS,
              rebuild: bool = False) -> tuple[Path, dict, bool]:
    """The large state file in `cache`, built when missing or changed. (path, manifest, built now)."""
    cache.mkdir(parents=True, exist_ok=True)
    name = f"state-{posts}-{reviews}-{chats}.sqlite3"
    path, man_path = cache / name, cache / (name + ".json")
    with open(cache / ".build.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        man = _manifest(man_path)
        want = {"version": BIG_VERSION, "posts": posts, "reviews": reviews, "chats": chats}
        ok = (not rebuild and path.exists() and man is not None
              and all(man.get(k) == v for k, v in want.items())
              and man.get("size") == path.stat().st_size and man.get("mtime_ns") == path.stat().st_mtime_ns)
        if ok:
            return path, man, False
        for extra in ("-wal", "-shm"):
            Path(str(path) + extra).unlink(missing_ok=True)
        man = build_big(path, posts, reviews, chats)
        man_path.write_text(json.dumps(man), encoding="utf-8")
        return path, man, True


def _manifest(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
