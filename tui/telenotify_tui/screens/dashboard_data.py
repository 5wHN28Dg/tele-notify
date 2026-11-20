"""Extra read-only queries for the dashboard (the shared ones live in store.py).

Every function takes an open Store and returns empties when the state file
or a table is missing, like Store itself.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..store import Chat, ChatStats, Store

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
class ChatProblem:
    title: str
    status: str   # banned / left / unreadable / silent
    detail: str


# ------------------------------------------------------------- periods
#
# A period is a run of whole local days, first and last included. Its unix
# range is [local midnight of `first`, local midnight of the day after
# `last`), so a day is whole even when daylight saving makes it 23 or 25 hours.

PERIODS: tuple[tuple[str, str], ...] = (
    ("today", "Today"),
    ("7d", "Last 7 days"),
    ("30d", "Last 30 days"),
    ("month", "This month"),
    ("year", "This year"),
    ("all", "All time"),
    ("custom", "Custom range…"),
)
PERIOD_NAMES = dict(PERIODS)
EARLIEST_YEAR = 2000
LATEST_YEAR = 2100


@dataclass(frozen=True)
class PeriodChoice:
    """What the picker says: a kind from PERIODS, and the days for "custom"."""
    kind: str = "7d"
    first: date | None = None
    last: date | None = None


@dataclass(frozen=True)
class Period:
    kind: str
    first: date        # first local day, included
    last: date         # last local day, included
    since: date | None = None   # all time: the day of the first judged post (None: nothing judged yet)
    since_known: bool = True    # all time: False when the first post's time in the state file is unreadable

    @property
    def start(self) -> int:
        return midnight(self.first)

    @property
    def end(self) -> int:
        """Exclusive: local midnight after the last day."""
        return midnight(self.last + timedelta(days=1))

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1

    def describe(self) -> str:
        """A short line for the screen, e.g. "Last 7 days: 12 Nov – 18 Nov 2026"."""
        name = "Custom" if self.kind == "custom" else PERIOD_NAMES.get(self.kind, self.kind)
        if self.kind == "all":
            if not self.since_known:
                return f"{name}: start unknown"
            return f"{name}: since {fmt_day(self.since)}" if self.since else f"{name}: nothing judged yet"
        if self.first == self.last:
            return f"{name}: {fmt_day(self.first)}"
        return f"{name}: {fmt_day(self.first, self.first.year == self.last.year)} – {fmt_day(self.last)}"


def fmt_day(d: date, short: bool = False) -> str:
    """"18 Nov 2026", or "18 Nov" when `short`."""
    return f"{d.day} {d:%b}" if short else f"{d.day} {d:%b %Y}"


def midnight(d: date) -> int:
    """Unix time of local midnight at the start of `d`."""
    return int(datetime(d.year, d.month, d.day).timestamp())


def today(now: float | None = None) -> date:
    return datetime.fromtimestamp(now if now is not None else time.time()).date()


def parse_day(text: str, what: str = "date") -> date:
    """A YYYY-MM-DD date; ValueError with a message for the screen otherwise."""
    t = text.strip()
    if not t:
        raise ValueError(f"Type the {what} as YYYY-MM-DD, e.g. 2026-11-01.")
    digits = t.replace("-", "")
    if not (len(t) == 10 and t[4] == "-" and t[7] == "-" and len(digits) == 8
            and digits.isascii() and digits.isdigit()):
        raise ValueError(f"The {what} \"{t[:20]}\" isn't YYYY-MM-DD (e.g. 2026-11-01).")
    try:
        d = date.fromisoformat(t)
    except ValueError:
        raise ValueError(f"The {what} {t} isn't a real day.") from None
    if not EARLIEST_YEAR <= d.year <= LATEST_YEAR:
        raise ValueError(f"The {what} must be in the years {EARLIEST_YEAR} to {LATEST_YEAR}.")
    return d


def parse_range(first: str, last: str) -> tuple[date, date]:
    """From / to as typed in the custom range form, both days included."""
    a = parse_day(first, "start date")
    b = parse_day(last, "end date")
    if b < a:
        raise ValueError(f"The end date {b.isoformat()} is before the start date {a.isoformat()}.")
    return a, b


def resolve(choice: PeriodChoice, day: date, first_judged: date | None = None, first_known: bool = True) -> Period:
    """The days `choice` covers when today is `day`. Periods up to today end
    today, and are whole calendar days: "last 7 days" is today and the 6 days
    before it (`telenotify stats 7` counts the last 7 * 24 hours instead).
    "all" starts on the day of the first judged post (today if none);
    `first_known` False: that post's time was unreadable, `first_judged` is
    the earliest readable one."""
    kind = choice.kind
    if kind == "today":
        return Period(kind, day, day)
    if kind == "7d":
        return Period(kind, day - timedelta(days=6), day)
    if kind == "30d":
        return Period(kind, day - timedelta(days=29), day)
    if kind == "month":
        return Period(kind, day.replace(day=1), day)
    if kind == "year":
        return Period(kind, day.replace(month=1, day=1), day)
    if kind == "all":
        first = min(first_judged, day) if first_judged else day
        return Period(kind, first, day, since=first_judged, since_known=first_known)
    if kind == "custom" and choice.first and choice.last and choice.first <= choice.last:
        return Period(kind, choice.first, choice.last)
    raise ValueError(f"unknown period {choice!r}")


# ------------------------------------------------------------- buckets

BUCKETS = ("day", "week", "month", "year")


def bucket_start(d: date, bucket: str) -> date:
    if bucket == "week":
        return d - timedelta(days=d.weekday())   # Monday
    if bucket == "month":
        return d.replace(day=1)
    if bucket == "year":
        return d.replace(month=1, day=1)
    return d


def _next_bucket(d: date, bucket: str) -> date:
    if bucket == "day":
        return d + timedelta(days=1)
    if bucket == "week":
        return d + timedelta(days=7)
    if bucket == "month":
        return date(d.year + d.month // 12, d.month % 12 + 1, 1)
    return date(d.year + 1, 1, 1)


def bucket_starts(period: Period, bucket: str) -> list[date]:
    """The first day of each bar in the period; the first bar is clipped to the period's first day."""
    out = [period.first]
    d = _next_bucket(bucket_start(period.first, bucket), bucket)
    while d <= period.last:
        out.append(d)
        d = _next_bucket(d, bucket)
    return out


def pick_bucket(period: Period, max_bars: int) -> str:
    """The finest of day / week / month / year that fits in `max_bars` bars."""
    for bucket in BUCKETS[:-1]:
        if len(bucket_starts(period, bucket)) <= max_bars:
            return bucket
    return "year"


# ------------------------------------------------------------- queries

@dataclass(frozen=True)
class DayCount:
    """Posts judged in one chart bar (a day, week, month or year starting on `day`)."""
    day: str  # yyyy-mm-dd, local time
    publish: int = 0
    review: int = 0
    reject: int = 0

    @property
    def total(self) -> int:
        return self.publish + self.review + self.reject


@dataclass(frozen=True)
class PeriodStats:
    period: Period
    bucket: str = "day"
    totals: Totals = field(default_factory=Totals)
    bars: list[DayCount] = field(default_factory=list)
    chats: list[ChatStats] = field(default_factory=list)


def _day_of(at: float) -> date | None:
    """The local day of a unix time, or None if it isn't one Python can show."""
    try:
        return datetime.fromtimestamp(at).date()
    except (OverflowError, ValueError, OSError, TypeError):
        return None


def first_judged(store: Store) -> tuple[date | None, bool]:
    """(local day of the first judged post, whether that time was readable).

    (None, True): nothing judged. A broken time in the state file (huge,
    negative, text) gives the earliest readable day with False, so all time
    still counts what it can and says its start is unknown."""
    if not store.has_table("processed"):
        return None, True
    rows = store._rows("SELECT MIN(at) FROM processed")
    if not rows or rows[0][0] is None:
        return None, True
    day = _day_of(rows[0][0])
    if day is not None and EARLIEST_YEAR <= day.year <= LATEST_YEAR:
        return day, True
    sane = store._rows("SELECT MIN(at) FROM processed WHERE at >= ? AND at < ?",
                       (midnight(date(EARLIEST_YEAR, 1, 1)), midnight(date(LATEST_YEAR + 1, 1, 1))))
    return (_day_of(sane[0][0]) if sane and sane[0][0] is not None else None), False


def _has_album_index(store: Store) -> bool:
    """Has `processed` the bot's album index (#109): a full (not partial) index on
    (chat_id, album_id, msg_id) with the default collation? Only that one lets
    SQLite look an album's first row up without reading its whole chat."""
    for idx in store._rows("PRAGMA index_list(processed)"):
        if idx[4]:   # partial
            continue
        name = str(idx[1]).replace("'", "''")   # read from the file: quoted as a string
        cols = [(r[2], r[4]) for r in store._rows(f"PRAGMA index_xinfo('{name}')") if r[5]]   # key columns
        if cols[:3] == [("chat_id", "BINARY"), ("album_id", "BINARY"), ("msg_id", "BINARY")]:
            return True
    return False


def _first_of_album(store: Store) -> tuple[str, str]:
    """(WITH clause, condition): an album is one post, however many pictures,
    so only its first row (lowest msg_id, as in `telenotify stats`) counts.
    With the bot's (chat_id, album_id, msg_id) index each album row of the period
    looks its album's first row up, so a refresh reads the period, not every album
    row ever judged (#110). Without it (a file the bot hasn't opened since #109)
    such a lookup reads the album's whole chat each time, so the first rows are
    found in one pass over the album rows instead."""
    if "album_id" not in store._columns("processed"):
        return "", ""
    if _has_album_index(store):
        return "", ("AND (COALESCE(album_id, 0) = 0 OR msg_id = (SELECT MIN(msg_id) FROM processed q "
                    "WHERE q.chat_id = p.chat_id AND q.album_id = p.album_id))")
    # Grouping by album first (not chat_id, the key's first column) makes
    # SQLite scan the table once instead of walking it in key order.
    with_firsts = ("WITH firsts AS (SELECT chat_id, MIN(msg_id) AS msg_id FROM processed "
                   "WHERE COALESCE(album_id, 0) != 0 GROUP BY album_id, chat_id) ")
    condition = "AND (COALESCE(album_id, 0) = 0 OR (chat_id, msg_id) IN (SELECT chat_id, msg_id FROM firsts))"
    return with_firsts, condition


def _sent_review(store: Store) -> str:
    """A review counts as forwarded once the bot sent it (not while it is
    pending, nor when discarded or only kept). Rejected rows (most of them)
    skip the lookup: the bot only re-judges rejected posts, so a rejected
    row never has a review."""
    if not store.has_table("reviews"):
        return ""
    return ("OR (action <> 'rejected' AND EXISTS (SELECT 1 FROM reviews r WHERE r.chat_id = p.chat_id "
            "AND r.msg_id = p.msg_id AND r.status = 'sent'))")


def period_stats(store: Store, period: Period, max_bars: int) -> PeriodStats:
    """Totals, chart bars and per-chat numbers for `period`, from one pass over its rows."""
    bucket = pick_bucket(period, max_bars)
    starts = bucket_starts(period, bucket)
    bars: dict[date, list[int]] = {d: [0, 0, 0] for d in starts}
    chats: dict[int, list] = {}   # chat id -> [title, newest at, publish, review, reject, forwarded]
    if store.has_table("processed"):
        with_firsts, first_of_album = _first_of_album(store)
        # GROUP BY the day first: SQLite then reads the period through the bot's
        # processed_at index instead of walking the file in key order.
        sql = f"""{with_firsts}SELECT chat_id, chat_title, MAX(at), date(at, 'unixepoch', 'localtime') d,
                  SUM(verdict = 'publish'), SUM(verdict = 'review'), SUM(verdict = 'reject'),
                  SUM(action = 'forwarded' OR action = 'linked' {_sent_review(store)})
                  FROM processed p WHERE at >= ? AND at < ? {first_of_album}
                  GROUP BY d, chat_id"""
        for chat_id, title, newest, day, pub, rev, rej, fwd in store._rows(sql, (period.start, period.end)):
            c = chats.setdefault(chat_id, ["", -1, 0, 0, 0, 0])
            if (newest or 0) > c[1]:   # the chat's newest title
                c[0], c[1] = title or "", newest or 0
            for i, v in enumerate((pub, rev, rej, fwd)):
                c[2 + i] += v or 0
            try:
                d = date.fromisoformat(day)
            except (TypeError, ValueError):
                continue
            key = max(bucket_start(d, bucket), period.first)
            if key in bars:
                for i, v in enumerate((pub, rev, rej)):
                    bars[key][i] += v or 0
    rows = [ChatStats(c[0], c[2], c[3], c[4], c[5]) for c in chats.values()]
    rows.sort(key=lambda r: -r.total)
    totals = Totals(*(sum(getattr(r, k) for r in rows) for k in ("publish", "review", "reject", "forwarded")))
    return PeriodStats(period, bucket, totals, [DayCount(d.isoformat(), *bars[d]) for d in starts], rows)


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
