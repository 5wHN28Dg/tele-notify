from pathlib import Path

from telenotify_tui.store import Store


def test_missing_file_reads_as_empty(tmp_path: Path):
    with Store(tmp_path / "nope.sqlite3") as s:
        assert not s.exists
        assert s.recent() == [] and s.stats(7) == [] and s.reviews() == [] and s.alerts() == []
        assert s.meta("last_alive") == ""


def test_recent_and_search(state):
    with Store(state) as s:
        r = s.recent(10)
        assert [j.msg_id for j in r][:3] == [5, 11, 10]           # newest first
        assert s.search("بغداد")[0].msg_id == 5                     # text
        assert {j.msg_id for j in s.search("basra jobs")} == {10, 11}   # chat, any case
        assert [j.msg_id for j in s.search("", verdict="publish")] == [10]


def test_search_takes_wildcards_literally(state):
    import sqlite3
    db = sqlite3.connect(state)
    db.execute("UPDATE processed SET reasons = 'salary 100% paid' WHERE msg_id = 10")
    db.execute("UPDATE processed SET reasons = 'see c:\\docs' WHERE msg_id = 5")
    db.commit()
    db.close()
    with Store(state) as s:
        assert [j.msg_id for j in s.search("%")] == [10]
        assert s.search("_") == []
        assert [j.msg_id for j in s.search("100%")] == [10]
        assert s.search("1_0") == []
        assert [j.msg_id for j in s.search("c:\\docs")] == [5]
        assert s.search("\\") and len(s.search("")) == 4


def test_chats_table_with_missing_columns(tmp_path: Path):
    import sqlite3
    p = tmp_path / "s.sqlite3"
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE chats (id INTEGER PRIMARY KEY, title TEXT)")
    db.execute("INSERT INTO chats VALUES (1, 'x')")
    db.commit()
    db.close()
    with Store(p) as s:
        [c] = s.chats()
        assert (c.id, c.title, c.kind, c.status, c.watched) == (1, "x", "", "ok", False)


def test_stats_counts_like_the_bot(state):
    with Store(state) as s:
        rows = {r.chat: r for r in s.stats(1)}
        assert (rows["Basra Jobs"].publish, rows["Basra Jobs"].review, rows["Basra Jobs"].forwarded) == (1, 1, 1)
        assert rows["Iraq Jobs"].total == 1                          # the 3-day-old one is outside
        assert sum(d[3] for d in s.per_day(7)) == 2



def test_stats_reads_the_period_through_the_time_index(state, monkeypatch):
    """Grouping by plain chat_id lets SQLite walk a whole chat_id index (every post
    ever judged) instead of reading the last N days through processed_at."""
    with Store(state) as s:
        seen: list[tuple[str, tuple]] = []
        rows = s._rows
        monkeypatch.setattr(s, "_rows", lambda sql, args=(): seen.append((sql, args)) or rows(sql, args))
        s.stats(7)
        [(sql, args)] = [q for q in seen if "GROUP BY" in q[0]]
        plan = [r[-1] for r in s.db.execute("EXPLAIN QUERY PLAN " + sql, args)]
        assert "SEARCH p USING INDEX processed_at (at>?)" in plan, plan
        assert any("processed_chat_album (chat_id=? AND album_id=?)" in line for line in plan), plan
        assert not any(line.startswith("SCAN") for line in plan), plan


def test_stats_ties_in_chat_order_under_the_newest_title(tmp_path):
    import sqlite3
    import time

    from conftest import SCHEMA

    p = tmp_path / "s.sqlite3"
    db = sqlite3.connect(p)
    db.executescript(SCHEMA)
    now = int(time.time())
    db.executemany("INSERT INTO processed (chat_id, msg_id, chat_title, verdict, action, at) VALUES (?,?,?,?,?,?)",
                   [(-100, 1, "Old name", "publish", "forwarded", now - 86400),
                    (-100, 2, "New name", "publish", "forwarded", now - 60),
                    (-300, 1, "C", "reject", "rejected", now - 50), (-300, 2, "C", "reject", "rejected", now - 40),
                    (-200, 1, "B", "review", "queued", now - 30), (-200, 2, "B", "review", "queued", now - 20)])
    db.commit()
    db.close()
    with Store(p) as s:
        assert [c.chat for c in s.stats(7)] == ["C", "B", "New name"]

def test_review_decisions_happen_once(state):
    with Store(state) as s:
        [r] = s.reviews()
        assert r.album_ids == [11] and r.link.endswith("/11")
        assert s.decide(r.id, "to_send")
        assert not s.decide(r.id, "discarded")                      # already decided
        assert s.reviews() == [] and s.review_counts()["to_send"] == 1


def test_alerts_chats_meta(state, now):
    with Store(state) as s:
        assert "no connection" in s.alerts()[0].text
        chats = {c.title: c for c in s.chats()}
        assert chats["Blocked Channel"].status == "banned" and not chats["Family"].watched
        assert s.last_alive() == now - 30 and s.meta("mode") == "dry-run"


def test_old_state_without_new_tables(tmp_path: Path):
    import sqlite3
    p = tmp_path / "old.sqlite3"
    db = sqlite3.connect(p)
    db.execute("""CREATE TABLE processed (chat_id INTEGER, msg_id INTEGER, chat_title TEXT, verdict TEXT,
                  role TEXT, reasons TEXT, action TEXT, at INTEGER)""")
    db.execute("INSERT INTO processed VALUES (-1, 1, 'X', 'reject', '', 'r', 'rejected', strftime('%s','now'))")
    db.commit(); db.close()
    with Store(p) as s:
        assert s.recent()[0].text == "" and s.alerts() == [] and s.chats() == []
        assert s.stats(1)[0].reject == 1


def _insert_review(state, msg_id, text_sql, album="NULL"):
    import sqlite3
    db = sqlite3.connect(state)
    db.execute(f"""INSERT INTO reviews (chat_id, msg_id, chat_title, text, reasons, role, link, status, at, album_ids)
                   VALUES (-2, {msg_id}, 'x', {text_sql}, 'r', 'it', '', 'pending', 1, {album})""")
    db.commit()
    db.close()


def test_invalid_utf8_reads_as_replacement_characters(state):
    _insert_review(state, 200, "CAST(X'61FF62' AS TEXT)")
    with Store(state) as s:
        [r] = [r for r in s.reviews() if r.msg_id == 200]
    assert r.text == "a�b"


def test_album_ids_take_ascii_digits_only(state):
    _insert_review(state, 300, "'post'", album="'²'")
    _insert_review(state, 301, "'post'", album="'301, -5,٣,1²,x'")
    with Store(state) as s:
        rows = {r.msg_id: r for r in s.reviews()}
    assert rows[300].album_ids == [300]
    assert rows[301].album_ids == [301, -5]


def test_text_from_the_state_has_no_control_characters(state):
    import sqlite3
    db = sqlite3.connect(state)
    db.execute("UPDATE reviews SET chat_title = ?, text = ?", ("T\x1b[41mRED", "a\x1b]0;X\x07b\x9bc\nd"))
    db.execute("UPDATE processed SET chat_title = ?", ("T\x1b[41mRED",))
    db.execute("UPDATE meta SET value = ? WHERE key = 'connection'", ("re\x1bady",))
    db.commit()
    db.close()
    with Store(state) as s:
        [r] = s.reviews()
        assert (r.chat_title, r.text) == ("T[41mRED", "a]0;Xbc\nd")
        assert s.meta("connection") == "ready"
        assert {j.chat_title for j in s.recent()} == {"T[41mRED"}


def test_last_alive_with_non_ascii_digits_is_unknown(state):
    import sqlite3
    db = sqlite3.connect(state)
    db.execute("UPDATE meta SET value = '²' WHERE key = 'last_alive'")
    db.commit()
    db.close()
    with Store(state) as s:
        assert s.last_alive() is None


def test_undo_only_while_the_decision_stands(state):
    with Store(state) as s:
        [r] = s.reviews()
        assert s.decide(r.id, "to_send")
        assert s.undo(r.id, "to_send") == (True, "pending")
        assert [x.id for x in s.reviews()] == [r.id]
        assert s.decide(r.id, "discarded")
        assert s.undo(r.id, "to_send") == (False, "discarded")     # not the decision being undone
        assert s.undo(r.id, "discarded") == (True, "pending")
        assert s.decide(r.id, "to_send")
        s.db.execute("UPDATE reviews SET status = 'sent' WHERE id = ?", (r.id,))   # the bot sent it
        s.db.commit()
        assert s.undo(r.id, "to_send") == (False, "sent")
        assert s.reviews() == []
        assert s.undo(9999, "discarded") == (False, None)
