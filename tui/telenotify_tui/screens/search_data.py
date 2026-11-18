"""Queries the search screen needs beyond Store.search: a chat filter and
the list of chats that appear among judged posts.

Read-only, and like Store, a missing table or column reads as empty.
"""

from __future__ import annotations

from ..store import Judged, Store


def search(store: Store, query: str, verdict: str | None = None, chat_id: int | None = None,
           limit: int = 200) -> list[Judged]:
    """Like Store.search, plus an optional chat filter applied in SQL (so the
    limit counts only that chat's posts). An empty query matches everything."""
    if chat_id is None:
        return store.search(query, verdict=verdict, limit=limit)
    cols = store._columns("processed")
    if not cols:
        return []
    has_text = "raw_text" in cols
    fields = ["chat_title", "role", "reasons"] + (["raw_text"] if has_text else [])
    like = f"%{query}%"
    where = " OR ".join(f"COALESCE({f}, '') LIKE ?" for f in fields)
    sql = f"SELECT * FROM processed WHERE ({where}) AND chat_id = ?"
    args: tuple = tuple(like for _ in fields) + (chat_id,)
    if verdict:
        sql += " AND verdict = ?"
        args += (verdict,)
    sql += " ORDER BY at DESC, msg_id DESC LIMIT ?"
    return [store._judged(r, has_text) for r in store._rows(sql, args + (limit,))]


def judged_chats(store: Store) -> list[tuple[int, str]]:
    """(chat_id, title) for every chat with judged posts, plus the account's
    known chats, sorted by title. Titles from the chats table win."""
    titles: dict[int, str] = {}
    if store.has_table("processed"):
        for r in store._rows("SELECT chat_id, MAX(chat_title) FROM processed GROUP BY chat_id"):
            titles[r[0]] = r[1] or str(r[0])
    for c in store.chats():
        titles[c.id] = c.title or titles.get(c.id) or str(c.id)
    return sorted(titles.items(), key=lambda kv: kv[1].casefold())


def split_reasons(reasons: str) -> list[str]:
    """The bot stores reasons joined with '; '."""
    return [r.strip() for r in reasons.split(";") if r.strip()]
