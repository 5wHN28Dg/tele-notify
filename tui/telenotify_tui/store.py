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
  status, at, decided_at, album_ids, send_after): the review queue;
  status pending -> to_send -> sending -> sent, or discarded, or kept (a
  match that must not be sent: it already reached the channel another way).
  An approved post ("to_send") waits until send_after (UNDO_SECONDS after
  the decision) before the bot claims it ("sending") and sends it; until the
  bot claims it, undo() can put it back (#62, #76).
* meta (key, value): last_alive, started_at, mode, connection, ...
* alerts (id, at, text): warnings the bot sent to Saved Messages.
* chats (id, title, kind, watched, status, updated_at): the account's chats,
  refreshed by the bot at startup; status is ok / banned / left / unreadable.

Every text value is read leniently and made printable (clean.clean): bytes
that aren't valid UTF-8 become U+FFFD instead of failing the whole query,
and control characters a stranger put in a post or chat title never reach
the terminal.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from .clean import clean

VERDICTS = ("publish", "review", "reject")
# What the review screen can decide: "to_send" (the bot sends it), "discarded"
# (not a match) and "kept" (a match, but never sent: it reached the channel
# another way, e.g. the old bot already forwarded it). The bot only sends
# "to_send", only shows "pending" and only counts "sent" as forwarded.
DECISIONS = ("to_send", "discarded", "kept")
# How long an approved post waits before the bot sends it, so it can be undone
# (#62): the default of config.json's `undo_seconds` (the screen reads it there,
# settings_data.undo_seconds), as undoWindowSeconds in src/telenotify/state.nim.
UNDO_SECONDS = 30
# The bot writes `last_alive` every minute: older than this, it isn't running
# (the Dashboard's ALIVE_SECONDS).
ALIVE_SECONDS = 120


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
    send_after: int | None = None   # approved: not sent before this (Unix seconds); None = at once


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


def _text(b: bytes) -> str:
    return clean(b.decode("utf-8", "replace"))


_ALBUM_ID = re.compile(r"-?[0-9]+")
_DIGITS = re.compile(r"[0-9]+")


# One wording for "another program has the state file locked", on every tab.
BUSY = "The state file is busy (another program has it locked)"
BUSY_NOTE = f"{BUSY}; retrying…"   # where the tab reads again by itself (Dashboard, Review)


def unreadable_note(service: str) -> str:
    """The state file can't be read for another reason (damaged, no permission): plain words and where to look."""
    return f"Can't read the state file. Check the bot's log: journalctl --user -u {service}"


def is_busy(e: BaseException) -> bool:
    """The read failed because another program holds a lock on the file: it passes by itself."""
    return isinstance(e, sqlite3.OperationalError) and ("locked" in str(e) or "busy" in str(e))


class Store:
    """One connection to the bot's state file. Open it, use it, close it."""

    def __init__(self, path: Path, timeout: float = 10):
        """`timeout`: seconds to wait for the bot's write lock before giving up
        (sqlite3.OperationalError). Keep it short for reads on the UI thread."""
        self.path = Path(path)
        self.db: sqlite3.Connection | None = None
        if self.path.exists():
            self.db = sqlite3.connect(self.path, timeout=timeout)
            self.db.row_factory = sqlite3.Row
            self.db.text_factory = _text
            self.db.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")

    @property
    def exists(self) -> bool:
        return self.db is not None

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None

    def __enter__(self) -> Self:
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
        return int(v) if _DIGITS.fullmatch(v) else None   # ASCII only: int("²") raises

    def bot_running(self) -> bool:
        """Is a bot on this state file running? Its heartbeat is recent and it didn't
        record a clean stop. (A crash is noticed once the heartbeat is ALIVE_SECONDS old.)"""
        alive = self.last_alive()
        return (alive is not None and time.time() - alive < ALIVE_SECONDS
                and self.meta("connection") not in ("stopped", "logged out"))   # a log-out stops it too

    def logged_out(self) -> bool:
        """Telegram logged the account out (or it never logged in): `telenotify login` first,
        as the Dashboard says. (state.nim loggedOut)"""
        return self.meta("connection") == "logged out" or self.meta("login_needed") == "1"

    def bot_sending(self) -> bool:
        """Is the bot running and sending approved posts? Its worker has started (`sending_ready`,
        written after the start-up backlog and cleared at a stop), as state.nim botSending asks."""
        return self.bot_running() and self.meta("sending_ready") == "1"

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

    def search(self, query: str, verdict: str | None = None, limit: int = 200,
               chat_id: int | None = None) -> list[Judged]:
        """Judged messages whose text, chat, role or reasons contain `query`, taken
        literally (% and _ are plain characters). Case is ignored for ASCII
        letters only (SQLite's LIKE); Arabic has no case."""
        cols = self._columns("processed")
        if not cols:
            return []
        has_text = "raw_text" in cols
        fields = ["chat_title", "role", "reasons"] + (["raw_text"] if has_text else [])
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        where = " OR ".join(f"COALESCE({f}, '') LIKE ? ESCAPE '\\'" for f in fields)
        sql = f"SELECT * FROM processed WHERE ({where})"
        args: tuple = tuple(like for _ in fields)
        if verdict:
            sql += " AND verdict = ?"
            args += (verdict,)
        if chat_id is not None:
            sql += " AND chat_id = ?"
            args += (chat_id,)
        sql += " ORDER BY at DESC, msg_id DESC LIMIT ?"
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
                  SUM(verdict = 'reject'), SUM(action IN ('forwarded', 'linked', 'summarized') {sent_review}),
                  MAX(at)
                  FROM processed p WHERE at > ? {first_of_album}
                  GROUP BY +chat_id ORDER BY COUNT(*) DESC, chat_id"""
        # as in the bot's: `+chat_id` reads the last N days through processed_at, not a whole chat_id
        # index; MAX(at) takes the title from the newest row; `, chat_id` keeps equal counts in one order
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
            ids = [int(x) for x in (p.strip() for p in str(r["album_ids"]).split(","))
                   if _ALBUM_ID.fullmatch(x)]
        return Review(id=r["id"], chat_id=r["chat_id"], msg_id=r["msg_id"], chat_title=r["chat_title"] or "",
                      text=r["text"] or "", reasons=r["reasons"] or "", role=r["role"] or "",
                      link=r["link"] or "", status=r["status"] or "", at=r["at"] or 0,
                      decided_at=r["decided_at"], album_ids=ids or [r["msg_id"]],
                      send_after=r["send_after"] if "send_after" in cols else None)

    def reviews(self, status: str = "pending") -> list[Review]:
        cols = self._columns("reviews")
        if not cols:
            return []
        return [self._review(r, cols)
                for r in self._rows("SELECT * FROM reviews WHERE status = ? ORDER BY at, id", (status,))]

    def review(self, review_id: int) -> Review | None:
        """One review as it is now, whatever its status; None when it's gone."""
        cols = self._columns("reviews")
        if not cols:
            return None
        rows = self._rows("SELECT * FROM reviews WHERE id = ?", (review_id,))
        return self._review(rows[0], cols) if rows else None

    def review_counts(self) -> dict[str, int]:
        if not self.has_table("reviews"):
            return {}
        return {r[0]: r[1] for r in self._rows("SELECT status, COUNT(*) FROM reviews GROUP BY status")}

    def decide(self, review_id: int, status: str, from_status: str = "pending",
               undo_seconds: int = UNDO_SECONDS) -> bool:
        """Approve ("to_send"), discard ("discarded") or keep ("kept": a match,
        but never sent) a review. An approved post waits `undo_seconds` (config.json's
        `undo_seconds`; 0: none) before the bot sends it (send_after), as when
        `telenotify review` approves it. False
        when it was already decided elsewhere (another window, `telenotify review`)."""
        if status not in DECISIONS:
            raise ValueError(f"not a decision: {status}")
        if self.db is None:
            return False
        now = int(time.time())
        if status == "to_send" and "send_after" in self._columns("reviews"):
            cur = self.db.execute("UPDATE reviews SET status = ?, decided_at = ?, send_after = ? "
                                  "WHERE id = ? AND status = ?",
                                  (status, now, now + max(0, undo_seconds), review_id, from_status))
        else:
            cur = self.db.execute("UPDATE reviews SET status = ?, decided_at = ? WHERE id = ? AND status = ?",
                                  (status, now, review_id, from_status))
        self.db.commit()
        return cur.rowcount == 1

    def undo(self, review_id: int, status: str) -> tuple[bool, str | None]:
        """Put a review decided as `status` ("to_send" / "discarded" / "kept") back to
        pending, but only if it still has that status: once the bot has claimed an
        approved post to send it ("sending", then "sent") it can't be taken back.
        One UPDATE whose row count says who won: the bot claims a post with the
        same kind of statement on "to_send" (state.nim claimReview), so a post is
        never both undone and sent. Returns (undone, the status it has now; None
        when the row is gone)."""
        if status not in DECISIONS:
            raise ValueError(f"not a decision: {status}")
        if self.db is None:
            return False, None
        clear = ", send_after = NULL" if "send_after" in self._columns("reviews") else ""
        cur = self.db.execute(f"UPDATE reviews SET status = 'pending', decided_at = NULL{clear} "
                              "WHERE id = ? AND status = ?", (review_id, status))
        self.db.commit()
        if cur.rowcount == 1:
            return True, "pending"
        rows = self._rows("SELECT status FROM reviews WHERE id = ?", (review_id,))
        return False, (rows[0][0] if rows else None)

    # ------------------------------------------------------- alerts, chats

    def alerts(self, limit: int = 200) -> list[Alert]:
        if not self.has_table("alerts"):
            return []
        return [Alert(r["id"], r["at"] or 0, r["text"] or "")
                for r in self._rows("SELECT * FROM alerts ORDER BY at DESC, id DESC LIMIT ?", (limit,))]

    def chats(self) -> list[Chat]:
        if not self.has_table("chats"):
            return []
        cols = self._columns("chats")

        def col(r: sqlite3.Row, name: str):
            return r[name] if name in cols else None

        order = "title COLLATE NOCASE" if "title" in cols else "id"
        return [Chat(r["id"], col(r, "title") or "", col(r, "kind") or "", bool(col(r, "watched")),
                     col(r, "status") or "ok", col(r, "updated_at") or 0)
                for r in self._rows(f"SELECT * FROM chats ORDER BY {order}")]
