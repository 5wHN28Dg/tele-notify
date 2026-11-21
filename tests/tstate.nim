import std/[unittest, os, tables, strutils, times]
import telenotify/state
import db_connector/db_sqlite

let dir = getTempDir() / "tn-state-test"
removeDir(dir)
let st = openState(dir / "state.sqlite3")

suite "state":
  test "processed messages are remembered":
    check not st.seen(-100, 1)
    st.markProcessed(-100, 1, "Jobs", "publish", "مهندس كهرباء", "entry level", "forwarded")
    check st.seen(-100, 1)
    st.markProcessed(-100, 1, "Jobs", "publish", "مهندس كهرباء", "again", "forwarded") # no crash

  test "published texts for duplicate checks":
    st.addPublished(-100, 1, "text one")
    st.addPublished(-100, 2, "text two")
    check st.recentPublished() == @["text two", "text one"] or st.recentPublished().len == 2

  test "review queue":
    st.addReview(-100, 5, "Jobs", "post", "no level", "electrical engineer", "https://t.me/c/1/5")
    st.addReview(-100, 5, "Jobs", "post", "no level", "electrical engineer", "")   # same message: ignored
    let p = st.pendingReviews()
    check p.len == 1 and p[0].msgId == 5 and p[0].link == "https://t.me/c/1/5"
    check st.decideReview(p[0].id, "to_send")
    check st.pendingReviews().len == 0 and st.approvedReviews().len == 1

  test "a decided review can't be approved again (two review windows)":
    let id = st.approvedReviews()[0].id
    check st.decideReview(id, "sent", fromStatus = "to_send")
    check not st.decideReview(id, "to_send")          # an old window still showing it
    check st.approvedReviews().len == 0               # so it isn't sent twice

  test "stats per chat":
    st.markProcessed(-100, 2, "Jobs", "reject", "", "", "rejected")
    st.markProcessed(-200, 1, "Other", "review", "", "", "queued for review")
    let s = st.stats(7)
    check s.len == 2
    check s[0].chat == "Jobs" and s[0].verdicts == [1, 0, 1] and s[0].forwarded == 1

  test "a post you approved in review counts as forwarded (#13)":
    st.markProcessed(-300, 7, "Third", "review", "", "", "queued for review")
    st.addReview(-300, 7, "Third", "post", "no level", "electrical engineer", "")
    var id = 0'i64
    for r in st.pendingReviews():
      if r.chatId == -300: id = r.id
    check st.decideReview(id, "to_send")
    proc third(): StatRow =
      for r in st.stats(7):
        if r.chat == "Third": return r
    check third().forwarded == 0                       # approved, not sent yet
    check st.decideReview(id, "sent", fromStatus = "to_send")
    check third().verdicts == [0, 1, 0] and third().forwarded == 1

  test "a review kept as a match (already sent elsewhere) is never sent, pending or forwarded":
    # the terminal screen's `k`: status "kept"
    st.markProcessed(-400, 8, "Fourth", "review", "", "", "queued for review")
    st.addReview(-400, 8, "Fourth", "post", "no level", "electrical engineer", "")
    var id = 0'i64
    for r in st.pendingReviews():
      if r.chatId == -400: id = r.id
    check id != 0
    check st.decideReview(id, "kept")
    for r in st.pendingReviews(): check r.chatId != -400
    for r in st.approvedReviews(): check r.chatId != -400   # sendApproved never sees it
    check not st.decideReview(id, "to_send")                 # an old window can't approve it
    for r in st.stats(7):
      if r.chat == "Fourth": check r.verdicts == [0, 1, 0] and r.forwarded == 0

  test "alerts are kept for the terminal screen, newest first":
    st.addAlert("first")
    st.addAlert("second")
    check st.alertTexts(2) == @["second", "first"]

  test "the chat list keeps names, watched flags and access status":
    st.setMeta("chat_access:-200", "banned")
    st.setMeta("chat_access:-100", "left-public")
    st.saveChats(@[ChatRow(id: -100, title: "Jobs", kind: "channel"),
                   ChatRow(id: -200, title: "Other", kind: "group")], @[-100'i64, -200, -300])
    check st.chatStatus(-100) == "ok" and st.chatStatus(-200) == "banned"
    check st.chatStatus(-300) == "unavailable"          # watched, unknown to Telegram
    st.setChatStatus(-200, "")
    check st.chatStatus(-200) == "ok"
    st.saveChats(@[ChatRow(id: -100, title: "Jobs", kind: "channel")], @[-100'i64])
    check st.chatStatus(-200) == ""                      # gone from the account: removed

  test "a watched public channel you never joined is fine, with its name (review)":
    st.setMeta("chat_access:-400", "left-public")      # not in your chat lists
    st.saveChats(@[ChatRow(id: -100, title: "Jobs", kind: "channel")], @[-100'i64, -400, -500],
                 {-400'i64: "Public Jobs"}.toTable)
    check st.chatStatus(-400) == "ok"
    check st.chatTitle(-400) == "Public Jobs"
    check st.chatStatus(-500) == "unavailable"          # no title, never opened

  test "only the newest 500 alerts are kept (review)":
    for i in 1 .. 510: st.addAlert("alert " & $i)
    check st.alertCount() == 500
    check st.alertTexts(1) == @["alert 510"]

  test "old post texts are forgotten, verdicts kept (review)":
    st.markProcessed(-600, 1, "Old", "reject", "", "r", "rejected", rawText = "old text")
    st.ageProcessed(-600, 100)
    st.markProcessed(-600, 2, "Old", "reject", "", "r", "rejected", rawText = "new text")
    st.forgetOldTexts(90)
    check st.rawTextOf(-600, 1) == "" and st.rawTextOf(-600, 2) == "new text"
    check st.seen(-600, 1)
    st.forgetOldTexts(0)                                 # 0: keep everything
    check st.rawTextOf(-600, 2) == "new text"

  test "the state file is private to you (review)":
    check getFilePermissions(dir / "state.sqlite3") == {fpUserRead, fpUserWrite}

  test "a new data folder is private to you":
    check getFilePermissions(dir) == {fpUserRead, fpUserWrite, fpUserExec}

  test "meta values":
    st.setMeta("last_alive", "123")
    check st.getMeta("last_alive") == "123"
    check st.getMeta("missing") == ""

  test "the bot's reads of processed use an index, never a whole-table walk":
    # the real queries: a full scan, or a walk over a whole index, grows
    # with every post ever judged
    let db = open(dir / "state.sqlite3", "", "", "")
    defer: db.close()
    proc plan(q: string, args: varargs[string]): seq[string] =
      for r in db.fastRows(sql("EXPLAIN QUERY PLAN " & q), args): result.add r[^1]
    let st7 = plan(statsSql, "0")
    check "SEARCH p USING INDEX processed_at (at>?)" in st7
    check "SEARCH q USING COVERING INDEX processed_chat_album (chat_id=? AND album_id=?)" in st7
    for line in st7: check not line.startsWith("SCAN")   # nothing else walks a whole table
    check plan(processedSinceSql, "0") == @["SEARCH processed USING INDEX processed_at (at>?)"]
    check plan(albumHandledSql, "-100", "7") ==
      @["SEARCH processed USING INDEX processed_chat_album (chat_id=? AND album_id=?)"]

  test "stats: equal counts in chat order, each chat under its newest title":
    let ts = openState(dir / "ties.sqlite3")
    defer: ts.close()
    ts.markProcessed(-100, 1, "Old name", "publish", "", "", "forwarded")
    ts.ageProcessed(-100, 1)                       # judged yesterday, under its old name
    ts.markProcessed(-100, 2, "New name", "publish", "", "", "forwarded")
    ts.markProcessed(-300, 1, "C", "reject", "", "", "rejected")
    ts.markProcessed(-300, 2, "C", "reject", "", "", "rejected")
    ts.markProcessed(-200, 1, "B", "review", "", "", "queued for review")
    ts.markProcessed(-200, 2, "B", "review", "", "", "queued for review")
    var order: seq[string]
    for r in ts.stats(7): order.add r.chat
    check order == @["C", "B", "New name"]          # 2 each: by chat id, the same every time

  test "an old state file gets the indexes when opened":
    let old = dir / "old.sqlite3"
    let raw = open(old, "", "", "")
    raw.exec(sql"""CREATE TABLE processed (chat_id INTEGER, msg_id INTEGER, chat_title TEXT,
      verdict TEXT, role TEXT, reasons TEXT, action TEXT, at INTEGER, PRIMARY KEY (chat_id, msg_id))""")
    raw.close()
    let st2 = openState(old)
    st2.close()
    let db = open(old, "", "", "")
    defer: db.close()
    var names: seq[string]
    for r in db.fastRows(sql"SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'processed'"):
      names.add r[0]
    check "processed_at" in names and "processed_chat_at" in names and "processed_chat_album" in names

  test "a post's own Telegram date is kept; unknown stays empty (#74)":
    st.markProcessed(-700, 1, "Dated", "publish", "", "", "forwarded", postedAt = 1_700_000_000)
    st.markProcessed(-700, 2, "Dated", "reject", "", "", "rejected")     # no date known
    check st.postedAt(-700, 1) == 1_700_000_000
    check st.postedAt(-700, 2) == 0
    let db = open(dir / "state.sqlite3", "", "", "")
    defer: db.close()
    check db.getValue(sql"SELECT typeof(posted_at) FROM processed WHERE chat_id = -700 AND msg_id = 1") == "integer"
    check db.getValue(sql"SELECT posted_at IS NULL FROM processed WHERE chat_id = -700 AND msg_id = 2") == "1"

  test "nearest-rank percentiles":
    check percentile(@[], 50) == 0
    check percentile(@[7'i64], 95) == 7
    check percentile(@[1'i64, 2, 3, 4], 50) == 2
    var xs: seq[int64]
    for i in 1 .. 100: xs.add i
    check percentile(xs, 50) == 50 and percentile(xs, 95) == 95
    xs.add 1000                                   # 101 values: ceil(95.95) = 96th
    check percentile(xs, 95) == 96

  test "forwarding latency: median and 95%, posts without a date left out (#74)":
    let ls = openState(dir / "latency.sqlite3")
    defer: ls.close()
    let t = getTime().toUnix
    # published right away, 10..200 seconds after the post went up
    for i in 1 .. 20:
      ls.markProcessed(-1, i, "A", "publish", "", "", "forwarded", postedAt = t - 10 * i)
      ls.addPublished(-1, i, "post " & $i)
    # sent after your review, an hour after the post
    ls.markProcessed(-2, 1, "B", "review", "", "", "queued for review", postedAt = t - 3600)
    ls.addPublished(-2, 1, "reviewed")
    # judged before dates were kept: no posted_at, left out
    ls.markProcessed(-3, 1, "C", "publish", "", "", "forwarded")
    ls.addPublished(-3, 1, "old")
    # judged but never published: not a forwarding time
    ls.markProcessed(-4, 1, "D", "reject", "", "", "rejected", postedAt = t - 99999)
    # every publish time pinned to t, so a clock tick can't move the numbers
    let db = open(dir / "latency.sqlite3", "", "", "")
    db.exec(sql"UPDATE published SET at = ?", t)
    db.close()
    let l = ls.latency(7)
    check l.direct.count == 20 and l.direct.median == 100 and l.direct.p95 == 190
    check l.reviewed.count == 1 and l.reviewed.median == 3600 and l.reviewed.p95 == 3600
    check ls.latency(0).direct.count == 0          # outside the window

  test "an old state file is upgraded with a copy kept first, and only once (#74)":
    let old = dir / "v0.sqlite3"
    let raw = open(old, "", "", "")
    raw.exec(sql"PRAGMA journal_mode=WAL")
    raw.exec(sql"""CREATE TABLE processed (chat_id INTEGER, msg_id INTEGER, chat_title TEXT,
      verdict TEXT, role TEXT, reasons TEXT, action TEXT, at INTEGER, text_hash TEXT, raw_text TEXT,
      album_id INTEGER, PRIMARY KEY (chat_id, msg_id))""")
    raw.exec(sql"CREATE TABLE published (id INTEGER PRIMARY KEY, chat_id INTEGER, msg_id INTEGER, text TEXT, at INTEGER)")
    raw.exec(sql"""INSERT INTO processed VALUES (-1, 5, 'Jobs', 'publish', 'r', 'x', 'forwarded', 100,
      'h', 'the old text', 0)""")
    raw.exec(sql"INSERT INTO published (chat_id, msg_id, text, at) VALUES (-1, 5, 'the old text', 160)")
    # `raw` stays open: its writes are still only in the WAL, which a plain
    # file copy would miss
    check fileExists(old & "-wal")
    let up = openState(old)
    check fileExists(backupPath(old, 0))
    check not fileExists(backupPath(old, 0) & ".tmp-" & $getCurrentProcessId())
    check getFilePermissions(backupPath(old, 0)) == {fpUserRead, fpUserWrite}
    # the data is kept, old posts have no date and are left out of latency
    check up.seen(-1, 5) and up.rawTextOf(-1, 5) == "the old text" and up.postedAt(-1, 5) == 0
    check up.latency(100000).direct.count == 0
    up.markProcessed(-1, 6, "Jobs", "publish", "", "", "forwarded", postedAt = 1_700_000_000)
    check up.postedAt(-1, 6) == 1_700_000_000
    up.close()
    raw.close()
    # the copy is the file as it was: version 0, no posted_at, the rows from the WAL
    let bak = open(backupPath(old, 0), "", "", "")
    check bak.getValue(sql"PRAGMA user_version") == "0"
    check bak.getValue(sql"SELECT raw_text FROM processed WHERE msg_id = 5") == "the old text"
    check bak.getValue(sql"SELECT COUNT(*) FROM published") == "1"
    var cols: seq[string]
    for r in bak.fastRows(sql"PRAGMA table_info(processed)"): cols.add r[1]
    check "posted_at" notin cols
    bak.close()
    let before = readFile(backupPath(old, 0))
    # a second open changes nothing: no new copy, the old one untouched
    let again = openState(old)
    check again.postedAt(-1, 6) == 1_700_000_000 and again.seen(-1, 5)
    again.close()
    check readFile(backupPath(old, 0)) == before
    check not fileExists(backupPath(old, 1))
    let db = open(old, "", "", "")
    defer: db.close()
    check db.getValue(sql"PRAGMA user_version") == $schemaVersion

  test "a new state file needs no copy (#74)":
    let fresh = dir / "fresh.sqlite3"
    let f = openState(fresh)
    f.close()
    check not fileExists(backupPath(fresh, 0))
    let db = open(fresh, "", "", "")
    defer: db.close()
    check db.getValue(sql"PRAGMA user_version") == $schemaVersion

  test "if the copy can't be made, the old file is left as it was (#74)":
    let old = dir / "nocopy.sqlite3"
    let raw = open(old, "", "", "")
    raw.exec(sql"""CREATE TABLE processed (chat_id INTEGER, msg_id INTEGER, chat_title TEXT,
      verdict TEXT, role TEXT, reasons TEXT, action TEXT, at INTEGER, PRIMARY KEY (chat_id, msg_id))""")
    raw.exec(sql"INSERT INTO processed VALUES (-1, 5, 'Jobs', 'publish', 'r', 'x', 'forwarded', 100)")
    raw.close()
    createDir(backupPath(old, 0))           # the copy's name is taken: renaming onto it fails
    expect IOError:
      discard openState(old)
    check not fileExists(backupPath(old, 0) & ".tmp-" & $getCurrentProcessId())
    block:
      let db = open(old, "", "", "")
      defer: db.close()
      check db.getValue(sql"PRAGMA user_version") == "0"
      var cols: seq[string]
      for r in db.fastRows(sql"PRAGMA table_info(processed)"): cols.add r[1]
      check "posted_at" notin cols and "text_hash" notin cols
      check db.getValue(sql"SELECT COUNT(*) FROM sqlite_master WHERE name = 'reviews'") == "0"
    removeDir(backupPath(old, 0))
    let ok = openState(old)                  # the next start, once the copy can be made
    check ok.seen(-1, 5)
    ok.close()
    check fileExists(backupPath(old, 0))

st.close()
removeDir(dir)
