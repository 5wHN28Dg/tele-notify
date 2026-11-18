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


def test_stats_counts_like_the_bot(state):
    with Store(state) as s:
        rows = {r.chat: r for r in s.stats(1)}
        assert (rows["Basra Jobs"].publish, rows["Basra Jobs"].review, rows["Basra Jobs"].forwarded) == (1, 1, 1)
        assert rows["Iraq Jobs"].total == 1                          # the 3-day-old one is outside
        assert sum(d[3] for d in s.per_day(7)) == 2


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
