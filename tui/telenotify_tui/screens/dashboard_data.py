"""Extra read-only queries for the dashboard (the shared ones live in store.py).

Every function takes an open Store and returns empties when the state file
or a table is missing, like Store itself.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..store import Chat, Store

SILENT_DAYS = 7


@dataclass(frozen=True)
class Totals:
    publish: int = 0
    review: int = 0
    reject: int = 0
    forwarded: int = 0

    @property
    def judged(self) -> int:
        return self.publish + self.review + self.reject


@dataclass(frozen=True)
class DayCount:
    day: str  # yyyy-mm-dd, local time
    publish: int = 0
    review: int = 0
    reject: int = 0

    @property
    def total(self) -> int:
        return self.publish + self.review + self.reject


@dataclass(frozen=True)
class ChatProblem:
    title: str
    status: str   # banned / left / unreadable / silent
    detail: str


def local_midnight(days_ago: int = 0, now: float | None = None) -> int:
    """Unix time of local midnight `days_ago` days before today."""
    d = datetime.fromtimestamp(now if now is not None else time.time()).date() - timedelta(days=days_ago)
    return int(datetime(d.year, d.month, d.day).timestamp())


def _filters(store: Store) -> tuple[str, str]:
    """(album filter, "sent from review" condition) matching Store.stats, so totals agree with it."""
    album = "album_id" in store._columns("processed")
    first_of_album = ("AND (COALESCE(album_id, 0) = 0 OR msg_id = (SELECT MIN(msg_id) FROM processed q "
                      "WHERE q.chat_id = p.chat_id AND q.album_id = p.album_id))") if album else ""
    sent_review = ("OR EXISTS (SELECT 1 FROM reviews r WHERE r.chat_id = p.chat_id AND "
                   "r.msg_id = p.msg_id AND r.status = 'sent')") if store.has_table("reviews") else ""
    return first_of_album, sent_review


def totals(store: Store, since: int) -> Totals:
    """Posts judged since `since` (unix time), by verdict, and how many were forwarded."""
    if not store.has_table("processed"):
        return Totals()
    first_of_album, sent_review = _filters(store)
    sql = f"""SELECT SUM(verdict = 'publish'), SUM(verdict = 'review'), SUM(verdict = 'reject'),
              SUM(action = 'forwarded' OR action = 'linked' {sent_review})
              FROM processed p WHERE at >= ? {first_of_album}"""
    rows = store._rows(sql, (since,))
    if not rows:
        return Totals()
    r = rows[0]
    return Totals(r[0] or 0, r[1] or 0, r[2] or 0, r[3] or 0)


def per_day(store: Store, days: int = 14, now: float | None = None) -> list[DayCount]:
    """One entry per calendar day (local time) for the last `days` days, today last; empty days are zeros."""
    start = local_midnight(days - 1, now)
    first = date.fromtimestamp(start)
    counts: dict[str, DayCount] = {}
    if store.has_table("processed"):
        first_of_album, _ = _filters(store)
        sql = f"""SELECT date(at, 'unixepoch', 'localtime') d, SUM(verdict = 'publish'),
                  SUM(verdict = 'review'), SUM(verdict = 'reject')
                  FROM processed p WHERE at >= ? {first_of_album} GROUP BY d"""
        for r in store._rows(sql, (start,)):
            counts[r[0]] = DayCount(r[0], r[1] or 0, r[2] or 0, r[3] or 0)
    out = []
    for i in range(days):
        d = (first + timedelta(days=i)).isoformat()
        out.append(counts.get(d, DayCount(d)))
    return out


def last_judged(store: Store) -> dict[int, int]:
    """chat id -> unix time of the newest judged message from it."""
    if not store.has_table("processed"):
        return {}
    return {r[0]: r[1] or 0 for r in store._rows("SELECT chat_id, MAX(at) FROM processed GROUP BY chat_id")}


def chat_problems(store: Store, now: float | None = None, silent_days: int = SILENT_DAYS) -> list[ChatProblem]:
    """Watched chats that need a look: status not ok (banned / left / unreadable), or
    nothing judged from them in `silent_days` days."""
    now = now if now is not None else time.time()
    chats: list[Chat] = [c for c in store.chats() if c.watched]
    if not chats:
        return []
    last = last_judged(store)
    bad, silent = [], []
    for c in chats:
        title = c.title or str(c.id)
        if c.status != "ok":
            bad.append(ChatProblem(title, c.status, "the bot can't read it"))
            continue
        seen = last.get(c.id)
        if seen is None:
            silent.append(ChatProblem(title, "silent", "nothing judged yet"))
        elif now - seen > silent_days * 86400:
            silent.append(ChatProblem(title, "silent", f"last post {int((now - seen) // 86400)} days ago"))
    return bad + silent
