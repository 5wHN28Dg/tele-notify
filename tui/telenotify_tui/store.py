"""Reading and writing the bot's SQLite state.

The tables are created by the Nim bot (src/telenotify/state.nim). This
module never creates or migrates them: a table or column an older bot
doesn't have yet simply reads as empty. The only writes are review
decisions, made exactly the way `telenotify review` makes them, so the
running bot sends what you approve and nothing is approved twice.

Tables (see state.nim for the authoritative schema):

* processed (chat_id, msg_id, chat_title, verdict, role, reasons, action,
  at, text_hash, raw_text, album_id): every message judged.
* reviews (id, chat_id, msg_id, chat_title, text, reasons, role, link,
  status, at, decided_at, album_ids): the review queue;
  status pending -> to_send -> sent, or discarded.
* meta (key, value): last_alive, started_at, mode, connection, ...
* alerts (id, at, text): warnings the bot sent to Saved Messages.
* chats (id, title, kind, watched, status, updated_at): the account's chats,
  refreshed by the bot at startup; status is ok / banned / left / unreadable.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

VERDICTS = ("publish", "review", "reject")


@dataclass
class Judged:
    chat_id: int
    msg_id: int
    chat_title: str
    verdict: str
    role: str
    reasons: str
    action: str
    at: int
    text: str = ""


@dataclass
class Review:
    id: int
    chat_id: int
    msg_id: int
    chat_title: str
    text: str
    reasons: str
    role: str
    link: str
    status: str
    at: int
    decided_at: int | None = None
    album_ids: list[int] = field(default_factory=list)


@dataclass
class ChatStats:
    chat: str
    publish: int
    review: int
    reject: int
    forwarded: int

    @property
    def total(self) -> int:
        return self.publish + self.review + self.reject


@dataclass
class Alert:
    id: int
    at: int
    text: str


@dataclass
class Chat:
    id: int
    title: str
    kind: str
    watched: bool
    status: str
    updated_at: int


class Store:
    """One connection to the bot's state file. Open it, use it, close it."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.db: sqlite3.Connection | None = None
        if self.path.exists():
            self.db = sqlite3.connect(self.path, timeout=10)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA busy_timeout=10000")

    @property
    def exists(self) -> bool:
        return self.db is not None

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------ helpers

    def _columns(self, table: str) -> set[str]:
        if self.db is None:
            return set()
        return {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}

    def has_table(self, table: str) -> bool:
        return bool(self._columns(table))

    def _rows(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        if self.db is None:
            return []
        return list(self.db.execute(sql, args))

    # --------------------------------------------------------------- meta

    def meta(self, key: str) -> str:
        if not self.has_table("meta"):
            return ""
        rows = self._rows("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0][0] if rows and rows[0][0] is not None else ""

    def last_alive(self) -> int | None:
        v = self.meta("last_alive")
        return int(v) if v.isdigit() else None

    # ---------------------------------------------------------- processed

    def _judged(self, r: sqlite3.Row, has_text: bool) -> Judged:
        return Judged(chat_id=r["chat_id"], msg_id=r["msg_id"], chat_title=r["chat_title"] or "",
                      verdict=r["verdict"] or "", role=r["role"] or "", reasons=r["reasons"] or "",
                      action=r["action"] or "", at=r["at"] or 0,
                      text=(r["raw_text"] or "") if has_text else "")

    def recent(self, limit: int = 100, verdict: str | None = None) -> list[Judged]:
        """The latest judged messages, newest first."""
        cols = self._columns("processed")
        if not cols:
            return []
        has_text = "raw_text" in cols
        sql = "SELECT * FROM processed"
        args: tuple = ()
        if verdict:
            sql += " WHERE verdict = ?"
            args = (verdict,)
        sql += " ORDER BY at DESC, msg_id DESC LIMIT ?"
        return [self._judged(r, has_text) for r in self._rows(sql, args + (limit,))]

    def search(self, query: str, verdict: str | None = None, limit: int = 200) -> list[Judged]:
        """Judged messages whose text, chat, role or reasons contain `query` (any case)."""
        cols = self._columns("processed")
        if not cols:
            return []
        has_text = "raw_text" in cols
        fields = ["chat_title", "role", "reasons"] + (["raw_text"] if has_text else [])
        like = f"%{query}%"
        where = " OR ".join(f"{f} LIKE ?" for f in fields)
        sql = f"SELECT * FROM processed WHERE ({where})"
        args: tuple = tuple(like for _ in fields)
        if verdict:
            sql += " AND verdict = ?"
            args += (verdict,)
        sql += " ORDER BY at DESC LIMIT ?"
        return [self._judged(r, has_text) for r in self._rows(sql, args + (limit,))]

    def stats(self, days: int) -> list[ChatStats]:
        """Per chat over the last `days` days, like `telenotify stats`."""
        if not self.has_table("processed"):
            return []
        album = "album_id" in self._columns("processed")
        sent_review = ("OR EXISTS (SELECT 1 FROM reviews r WHERE r.chat_id = p.chat_id AND "
                       "r.msg_id = p.msg_id AND r.status = 'sent')") if self.has_table("reviews") else ""
        first_of_album = ("AND (COALESCE(album_id, 0) = 0 OR msg_id = (SELECT MIN(msg_id) FROM processed q "
                          "WHERE q.chat_id = p.chat_id AND q.album_id = p.album_id))") if album else ""
        sql = f"""SELECT chat_title, SUM(verdict = 'publish'), SUM(verdict = 'review'),
                  SUM(verdict = 'reject'), SUM(action = 'forwarded' OR action = 'linked' {sent_review})
                  FROM processed p WHERE at > ? {first_of_album}
                  GROUP BY chat_id ORDER BY COUNT(*) DESC"""
        since = int(time.time()) - days * 86400
        return [ChatStats(r[0] or "", r[1] or 0, r[2] or 0, r[3] or 0, r[4] or 0)
                for r in self._rows(sql, (since,))]

    def per_day(self, days: int) -> list[tuple[str, int, int, int]]:
        """(yyyy-mm-dd, publish, review, reject) for each day with activity, oldest first."""
        if not self.has_table("processed"):
            return []
        since = int(time.time()) - days * 86400
        sql = """SELECT date(at, 'unixepoch', 'localtime') d, SUM(verdict = 'publish'),
                 SUM(verdict = 'review'), SUM(verdict = 'reject')
                 FROM processed WHERE at > ? GROUP BY d ORDER BY d"""
        return [(r[0], r[1] or 0, r[2] or 0, r[3] or 0) for r in self._rows(sql, (since,))]

    # ------------------------------------------------------------ reviews

    def _review(self, r: sqlite3.Row, cols: set[str]) -> Review:
        ids: list[int] = []
        if "album_ids" in cols and r["album_ids"]:
            ids = [int(x) for x in str(r["album_ids"]).split(",") if x.strip().lstrip("-").isdigit()]
        return Review(id=r["id"], chat_id=r["chat_id"], msg_id=r["msg_id"], chat_title=r["chat_title"] or "",
                      text=r["text"] or "", reasons=r["reasons"] or "", role=r["role"] or "",
                      link=r["link"] or "", status=r["status"] or "", at=r["at"] or 0,
                      decided_at=r["decided_at"], album_ids=ids or [r["msg_id"]])

    def reviews(self, status: str = "pending") -> list[Review]:
        cols = self._columns("reviews")
        if not cols:
            return []
        return [self._review(r, cols)
                for r in self._rows("SELECT * FROM reviews WHERE status = ? ORDER BY at, id", (status,))]

    def review_counts(self) -> dict[str, int]:
        if not self.has_table("reviews"):
            return {}
        return {r[0]: r[1] for r in self._rows("SELECT status, COUNT(*) FROM reviews GROUP BY status")}

    def decide(self, review_id: int, status: str, from_status: str = "pending") -> bool:
        """Approve ("to_send") or discard ("discarded") a review. False when it was
        already decided elsewhere (another window, `telenotify review`)."""
        if status not in ("to_send", "discarded"):
            raise ValueError(f"not a decision: {status}")
        if self.db is None:
            return False
        cur = self.db.execute("UPDATE reviews SET status = ?, decided_at = ? WHERE id = ? AND status = ?",
                              (status, int(time.time()), review_id, from_status))
        self.db.commit()
        return cur.rowcount == 1

    # ------------------------------------------------------- alerts, chats

    def alerts(self, limit: int = 200) -> list[Alert]:
        if not self.has_table("alerts"):
            return []
        return [Alert(r["id"], r["at"] or 0, r["text"] or "")
                for r in self._rows("SELECT * FROM alerts ORDER BY at DESC, id DESC LIMIT ?", (limit,))]

    def chats(self) -> list[Chat]:
        if not self.has_table("chats"):
            return []
        return [Chat(r["id"], r["title"] or "", r["kind"] or "", bool(r["watched"]), r["status"] or "ok",
                     r["updated_at"] or 0)
                for r in self._rows("SELECT * FROM chats ORDER BY title COLLATE NOCASE")]
