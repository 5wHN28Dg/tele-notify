import std/[unittest, os, tables, strutils, times, random]
import telenotify/state
import db_connector/db_sqlite
from std/posix import nil

proc flock(fd, operation: cint): cint {.importc, header: "<sys/file.h>".}

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

  test "a failed send adds its reason once, compared as whole parts, and counts repeats (#142 run 4)":
    check withProblem("no level", "level") == "no level; could not send: level"
    check withProblem("no link to the post", "no link") == "no link to the post; could not send: no link"
    let held = "error: no link to the post from Telegram (no link), so no summary was sent. Approve it again to retry"
    check withProblem("role x; " & held, "no link") == "role x; " & held & "; could not send: no link"
    var r = "role x"
    for _ in 1 .. 3: r = withProblem(r, "FLOOD_WAIT")
    check r == "role x; could not send (3 tries): FLOOD_WAIT"
    # a reason the live post was held back for (or the filter wrote) is its first try
    r = "role x; not posted: it isn't from a public channel"
    r = withProblem(r, "not posted: it isn't from a public channel")
    check r == "role x; not posted (2 tries): it isn't from a public channel"
    r = withProblem(r, "not posted: it isn't from a public channel")
    check r == "role x; not posted (3 tries): it isn't from a public channel"
    check withProblem("", "no link") == "could not send: no link"

  test "a live error isn't repeated on approval; old copies fold; counts stay sane (#142 run 5)":
    # the live post's "error: <problem>" is the same reason
    let e = "no link to the post from Telegram (no link), so no summary was sent. Approve it again to retry"
    check withProblem("role x; error: " & e, e) == "role x; error (2 tries): " & e   # the count after "error"
    # copies an older bot wrote are folded into one, with their tries summed
    check withProblem("no level; could not send: FLOOD_WAIT; could not send: FLOOD_WAIT", "FLOOD_WAIT") ==
      "no level; could not send (3 tries): FLOOD_WAIT"
    check withProblem("x; could not send: A (2 tries); error: A; could not send: A", "A") ==
      "x; could not send (5 tries): A"
    # a problem never holds "; ": it can't match a piece of another reason
    check withProblem("could not send: a, b", "a; b") == "could not send (2 tries): a, b"
    check problemText("a; b\nAsync traceback: …") == "a, b"
    # a count that can't be right isn't trusted: a new part instead, never an overflow
    check withProblem("could not send: A (9223372036854775807 tries)", "A") ==
      "could not send: A (9223372036854775807 tries); could not send: A"
    check withProblem("could not send: A (1000001 tries)", "A") ==
      "could not send: A (1000001 tries); could not send: A"
    check withProblem("could not send: A (-3 tries)", "A") == "could not send: A (-3 tries); could not send: A"
    check withProblem("could not send: A (1000000 tries)", "A") == "could not send (1000000 tries): A"
    # through the database
    st.addReview(-300, 1, "Jobs", "text", "no level", "role", "")
    var id = 0'i64
    for x in st.pendingReviews():
      if x.chatId == -300: id = x.id
    for _ in 1 .. 2:
      check st.decideReview(id, "to_send")
      st.reopenReview(id, "level")
    for x in st.pendingReviews():
      if x.chatId == -300: check x.reasons == "no level; could not send (2 tries): level"

  test "reasons read cleanly: no stacked lead-ins, no \".;\", a fixed text for no message (#142 run 6)":
    # held back on approval: added as it is, not after "could not send: " (wp6 P13)
    let empty = problemText("not posted: the summary would say nothing. Otherwise discard it.")
    let priv = "not posted: it isn't from a public channel. Otherwise discard it."
    check withProblem("role x; " & empty, priv, heldBack = true) == "role x; " & empty & "; " & problemText(priv)
    check problemText(priv) == "not posted: it isn't from a public channel. Otherwise discard it"
    check ".; " notin withProblem("role x; " & empty, "no link", heldBack = false)
    # no message at all: a fixed text, counted like any other (wp6 P3, P4)
    check problemText("") == unknownProblem and problemText("  \n ") == unknownProblem
    check problemText("\nAsync traceback:\n  a.nim(1)") == unknownProblem   # a debug build's empty message
    var r = withProblem("role", "")
    check r == "role; could not send: unknown error"
    r = withProblem(r, "  \n ")
    check r == "role; could not send (2 tries): unknown error"
    # a problem that itself ends like a count still folds (wp6 P1b)
    r = "role"
    for _ in 1 .. 3: r = withProblem(r, "boom (2 tries)")
    check r == "role; could not send (3 tries): boom (2 tries)"
    r = "role; boom (2 tries)"                           # written by the filter: its first try
    r = withProblem(r, "boom (2 tries)")
    check r == "role; boom (2 tries) (2 tries)"
    check withProblem(r, "boom (2 tries)") == "role; boom (2 tries) (3 tries)"

  test "older rows fold too: a closing \".\", empty lead-ins (#142 run 7)":
    # a stored part ending in "." is the same reason (wp7 N10, N5, N8)
    check withProblem("x; error: Bad thing happened.", "Bad thing happened.") ==
      "x; error (2 tries): Bad thing happened"
    let pv = "not posted: it isn't from a public channel. Otherwise discard it."
    check withProblem("x; " & pv & " (2 tries)", pv, heldBack = true) ==
      "x; not posted (3 tries): it isn't from a public channel. Otherwise discard it"
    check withProblem("x; could not send: " & pv & " (3 tries)", "could not send: " & pv) ==
      "x; could not send (4 tries): not posted: it isn't from a public channel. Otherwise discard it"
    # an older bot's "error: " / "could not send: " with nothing after is "unknown error" (U3, U4)
    check withProblem("role; error: ", "") == "role; error (2 tries): unknown error"
    check withProblem("role; could not send: ", "  ") == "role; could not send (2 tries): unknown error"
    check withProblem("a; ; x", "") == "a; ; x; could not send: unknown error"   # an empty part isn't one
    # nor is a part of only dots and spaces (#142 run 8: wp8 C1, C21)
    check withProblem("a; .; x", "") == "a; .; x; could not send: unknown error"
    check withProblem("a;  . ; b", "") == "a;  . ; b; could not send: unknown error"

  test "closing dots go until none is left, so a message always folds with itself (#142 run 9)":
    # wp9 G21, G22, G24
    var r = "role"
    for _ in 1 .. 3: r = withProblem(r, ". .")
    check r == "role; could not send (3 tries): unknown error"
    r = "role"
    for _ in 1 .. 3: r = withProblem(r, "x. .")
    check r == "role; could not send (3 tries): x"
    check withProblem(withProblem("role", ". ."), "") == "role; could not send (2 tries): unknown error"
    check problemText("Failed . .") == "Failed"
    # ellipses stay as written
    check problemText("..") == ".." and problemText("wait...") == "wait..." and problemText("x…") == "x…"
    check problemText("x. ..") == "x. .."
    check problemText("error:") == "error: unknown error"     # a bare lead-in
    # the property: for seeded messages of awkward pieces, problemText's output is as comparable
    # leaves it, and the same message twice folds into one counted part
    const pieces = [".", " ", "\t", "\n", ";", "; ", "(", ")", "tries", "try", ":", "error", "could not send",
                    "not posted", "1", "2", " (", "وظيفة", "..", "…", "x"]
    var rng = initRand(80142)
    for _ in 1 .. 3000:
      var m = ""
      for _ in 1 .. rng.rand(1 .. 8): m.add pieces[rng.rand(pieces.high)]
      let p = problemText(m)
      check comparable(p) == p and comparable("could not send: " & p) == "could not send: " & p
      for held in [false, true]:
        let once = withProblem("role", m, held)
        let twice = withProblem(once, m, held)
        let folded = twice.split("; ").len == once.split("; ").len and
                     twice.count("(2 tries)") == once.count("(2 tries)") + 1
        if not folded:
          checkpoint "message: " & m.escape & " held: " & $held & "\n once: " & once & "\n twice: " & twice
        check folded

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
    # read from a backlog after downtime, half an hour after the post
    ls.markProcessed(-5, 1, "E", "publish", "", "", "forwarded", postedAt = t - 1800, caughtUp = true)
    ls.addPublished(-5, 1, "caught up")
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
    check l.live.count == 20 and l.live.median == 100 and l.live.p95 == 190
    check l.caughtUp.count == 1 and l.caughtUp.median == 1800
    check l.reviewed.count == 1 and l.reviewed.median == 3600 and l.reviewed.p95 == 3600
    check ls.latency(0).live.count == 0            # outside the window

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
    check up.latency(100000).live.count == 0
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
      check "posted_at" notin cols and "text_hash" notin cols and "caught_up" notin cols
      check db.getValue(sql"SELECT COUNT(*) FROM sqlite_master WHERE name = 'reviews'") == "0"
    removeDir(backupPath(old, 0))
    let ok = openState(old)                  # the next start, once the copy can be made
    check ok.seen(-1, 5)
    ok.close()
    check fileExists(backupPath(old, 0))

  proc oldFile(name: string): string =
    ## A version-0 state file with one judged post.
    result = dir / name
    let raw = open(result, "", "", "")
    raw.exec(sql"""CREATE TABLE processed (chat_id INTEGER, msg_id INTEGER, chat_title TEXT,
      verdict TEXT, role TEXT, reasons TEXT, action TEXT, at INTEGER, PRIMARY KEY (chat_id, msg_id))""")
    raw.exec(sql"INSERT INTO processed VALUES (-1, 5, 'Jobs', 'publish', 'r', 'x', 'forwarded', 100)")
    raw.close()

  test "an old copy from before a rollback is replaced by a fresh one (#74)":
    let old = oldFile("stale.sqlite3")
    # e.g. an earlier upgrade's copy, then a rollback to the old bot, which
    # kept writing to the version-0 file; or a broken copy from a power cut
    writeFile(backupPath(old, 0), "stale")
    let up = openState(old)
    up.close()
    let bak = open(backupPath(old, 0), "", "", "")
    defer: bak.close()
    check bak.getValue(sql"SELECT chat_title FROM processed WHERE msg_id = 5") == "Jobs"
    check getFilePermissions(backupPath(old, 0)) == {fpUserRead, fpUserWrite}

  test "a half-made copy left by a process that died is removed (#74)":
    let old = oldFile("leftover.sqlite3")
    let leftover = backupPath(old, 0) & ".tmp-999999999"
    writeFile(leftover, "half a copy, with post texts")
    let up = openState(old)
    up.close()
    check not fileExists(leftover)
    check fileExists(backupPath(old, 0))

  test "while another process holds the upgrade lock, nothing is copied or changed (#74)":
    let old = oldFile("locked.sqlite3")
    let fd = posix.open(cstring(old & ".lock"), posix.O_RDWR or posix.O_CREAT, 0o600)
    check fd >= 0
    check flock(fd, 2) == 0                       # LOCK_EX: another telenotify is upgrading
    check upgradeLockWaitMs == 20_000              # a copy takes well under a second
    let saved = upgradeLockWaitMs
    upgradeLockWaitMs = 300
    defer: upgradeLockWaitMs = saved
    try:
      discard openState(old)
      check false                                 # it must not go on without the lock
    except IOError as e:
      check "still upgrading" in e.msg
    check not fileExists(backupPath(old, 0))
    block:
      let db = open(old, "", "", "")
      defer: db.close()
      check db.getValue(sql"PRAGMA user_version") == "0"
    discard posix.close(fd)                       # the other process is done
    let up = openState(old)
    up.close()
    check fileExists(backupPath(old, 0))

  proc columnsOf(path: string): seq[string] =
    let db = open(path, "", "", "")
    defer: db.close()
    for r in db.fastRows(sql"PRAGMA table_info(processed)"): result.add r[1]

  proc versionOf(path: string): string =
    let db = open(path, "", "", "")
    defer: db.close()
    db.getValue(sql"PRAGMA user_version")

  test "the columns and the version are written together, or neither; the failure is one line (#74)":
    let old = oldFile("halfway.sqlite3")
    upgradeTestHook = proc (stage: string) =
      if stage == "columns": raise newException(DbError, "database is locked\nsecond line")
    defer: upgradeTestHook = nil
    try:
      discard openState(old)
      check false
    except IOError as e:
      check "left as it was" in e.msg and "database is locked" in e.msg
      check '\n' notin e.msg                     # one line in the journal
    check versionOf(old) == "0"
    let cols = columnsOf(old)
    check "posted_at" notin cols and "caught_up" notin cols and "text_hash" notin cols
    upgradeTestHook = nil
    let up = openState(old)                      # the next start
    check up.seen(-1, 5)
    up.close()
    check versionOf(old) == $schemaVersion and "caught_up" in columnsOf(old)

  test "the version is looked at again once the lock is held (#74)":
    let old = oldFile("raced.sqlite3")
    # another telenotify finished the upgrade while this one waited for the lock
    upgradeTestHook = proc (stage: string) =
      if stage == "locked":
        let other = open(old, "", "", "")
        other.exec(sql"ALTER TABLE processed ADD COLUMN posted_at INTEGER")
        other.exec(sql("PRAGMA user_version = " & $schemaVersion))
        other.close()
    defer: upgradeTestHook = nil
    let up = openState(old)
    up.close()
    check not fileExists(backupPath(old, 0))     # nothing to copy any more: it's upgraded
    check not fileExists(backupPath(old, 1))
    check "caught_up" in columnsOf(old)

  test "a copy not yet confirmed on the disk says so, and the file is not upgraded (#74)":
    let old = oldFile("unsynced.sqlite3")
    upgradeTestHook = proc (stage: string) =
      if stage == "renamed": raise newException(OSError, "Input/output error")
    defer: upgradeTestHook = nil
    try:
      discard openState(old)
      check false
    except IOError as e:
      check "kept a copy" in e.msg and "was not upgraded and is unchanged" in e.msg
    check fileExists(backupPath(old, 0))
    check versionOf(old) == "0" and "posted_at" notin columnsOf(old)

  test "the copy-failure message says what to do next (#74)":
    let old = oldFile("hint.sqlite3")
    createDir(backupPath(old, 0))
    defer: removeDir(backupPath(old, 0))
    try:
      discard openState(old)
      check false
    except IOError as e:
      check "left as it was" in e.msg and "Free some disk space" in e.msg

  test "a version-1 state file gets the undo window, with a copy kept first (#62)":
    let old = dir / "v1.sqlite3"
    let raw = open(old, "", "", "")
    raw.exec(sql"""CREATE TABLE processed (chat_id INTEGER, msg_id INTEGER, chat_title TEXT,
      verdict TEXT, role TEXT, reasons TEXT, action TEXT, at INTEGER, text_hash TEXT, raw_text TEXT,
      album_id INTEGER, posted_at INTEGER, caught_up INTEGER, PRIMARY KEY (chat_id, msg_id))""")
    raw.exec(sql"""CREATE TABLE reviews (id INTEGER PRIMARY KEY, chat_id INTEGER, msg_id INTEGER,
      chat_title TEXT, text TEXT, reasons TEXT, role TEXT, link TEXT, status TEXT DEFAULT 'pending',
      at INTEGER, decided_at INTEGER, album_ids TEXT, UNIQUE (chat_id, msg_id))""")
    raw.exec(sql"""INSERT INTO reviews (chat_id, msg_id, chat_title, text, reasons, role, link, status, at,
      decided_at) VALUES (-1, 5, 'Jobs', 'approved before the window', 'x', 'r', '', 'to_send', 100, 110)""")
    raw.exec(sql"PRAGMA user_version = 1")
    raw.close()
    let up = openState(old)
    check fileExists(backupPath(old, 1))
    # approved before the upgrade: no window, sent as before
    let due = up.dueReviews()
    check due.len == 1 and due[0].msgId == 5 and due[0].sendAfter == 0
    up.close()
    check versionOf(old) == $schemaVersion and schemaVersion == 2
    let db = open(old, "", "", "")
    defer: db.close()
    var cols: seq[string]
    for r in db.fastRows(sql"PRAGMA table_info(reviews)"): cols.add r[1]
    check "send_after" in cols
    let bak = open(backupPath(old, 1), "", "", "")
    defer: bak.close()
    check bak.getValue(sql"PRAGMA user_version") == "1"
    check bak.getValue(sql"SELECT status FROM reviews WHERE msg_id = 5") == "to_send"

  test "an approved post is due only after its window; the bot claims it once (#62)":
    st.addReview(-700, 1, "Win", "post", "x", "r", "")
    var id = 0'i64
    for r in st.pendingReviews():
      if r.chatId == -700: id = r.id
    let t0 = getTime().toUnix
    check st.decideReview(id, "to_send")
    var sendAfter = 0'i64
    for r in st.approvedReviews():
      if r.id == id: sendAfter = r.sendAfter
    check sendAfter in t0 + undoWindowSeconds .. t0 + undoWindowSeconds + 2
    proc dueIds(at: int64): seq[int64] =
      for r in st.dueReviews(at): result.add r.id
    check id notin dueIds(sendAfter - 1)
    check not st.claimReview(id, sendAfter - 1)       # the window is still open
    check id in dueIds(sendAfter)
    check st.claimReview(id, sendAfter)
    check not st.claimReview(id, sendAfter)           # once
    check id notin dueIds(sendAfter + 100)
    check st.decideReview(id, "sent", fromStatus = "sending")

  test "sending and sent keep the time you decided, so History doesn't reorder (review run 1)":
    st.addReview(-702, 1, "Order", "post", "x", "r", "")
    var id = 0'i64
    for r in st.pendingReviews():
      if r.chatId == -702: id = r.id
    check st.decideReview(id, "to_send")
    let raw = open(dir / "state.sqlite3", "", "", "")
    defer: raw.close()
    raw.exec(sql"UPDATE reviews SET decided_at = 1000 WHERE id = ?", id)
    check st.claimReview(id, getTime().toUnix + undoWindowSeconds + 1)
    check st.decideReview(id, "sent", fromStatus = "sending")
    check raw.getValue(sql"SELECT decided_at FROM reviews WHERE id = ?", id) == "1000"

  test "a post the bot was sending when it stopped goes back to the queue, not out again":
    st.addReview(-701, 1, "Crash", "post", "x", "r", "")
    var id = 0'i64
    for r in st.pendingReviews():
      if r.chatId == -701: id = r.id
    check st.decideReview(id, "to_send")
    check st.claimReview(id, getTime().toUnix + undoWindowSeconds + 1)
    check st.reopenInterruptedSends() == 1
    var back = false
    for r in st.pendingReviews():
      if r.id == id: back = "stopped while sending" in r.reasons
    check back
    check st.reopenInterruptedSends() == 0

  test "an interrupted send already in published is marked sent, not reopened (review run 2)":
    st.addReview(-703, 1, "Lost mark", "post", "x", "r", "")
    st.addReview(-703, 2, "Lost send", "post 2", "x", "r", "")
    var ids: seq[int64]
    for r in st.pendingReviews():
      if r.chatId == -703:
        check st.decideReview(r.id, "to_send")
        check st.claimReview(r.id, getTime().toUnix + undoWindowSeconds + 1)
        ids.add r.id
    st.addPublished(-703, 1, "post")              # #1 reached the channel; its "sent" mark was lost
    check st.reopenInterruptedSends() == 1        # only #2 goes back
    var pending: seq[int64]
    for r in st.pendingReviews():
      if r.chatId == -703: pending.add r.msgId
    check pending == @[2'i64]
    let raw = open(dir / "state.sqlite3", "", "", "")
    defer: raw.close()
    check raw.getValue(sql"SELECT status FROM reviews WHERE chat_id = -703 AND msg_id = 1") == "sent"

  test "botSending: a fresh heartbeat, no clean stop, and the worker started (review run 2)":
    let t = getTime().toUnix
    st.setMeta("last_alive", $(t - 30))
    st.setMeta("connection", "ready")
    st.setMeta("sending_ready", "1")
    check st.botSending(t)
    check not st.botSending(t + 100)              # heartbeat 130 s old
    st.setMeta("sending_ready", "0")              # still reading its backlog
    check not st.botSending(t)
    st.setMeta("sending_ready", "1")
    st.setMeta("connection", "stopped")
    check not st.botSending(t)

  test "botRunning without sending_ready: running, not sending yet (review run 3, F5)":
    let t = getTime().toUnix
    st.setMeta("last_alive", $(t - 10))
    st.setMeta("connection", "starting")
    st.setMeta("sending_ready", "0")
    check st.botRunning(t) and not st.botSending(t)
    st.setMeta("sending_ready", "1")
    check st.botRunning(t) and st.botSending(t)
    st.setMeta("connection", "stopped")
    check not st.botRunning(t) and not st.botSending(t)

  test "a run's start is written all or nothing, and clears a crashed bot's sending_ready (review run 4)":
    st.setMeta("connection", "ready")
    st.setMeta("sending_ready", "1")                # a bot that crashed a moment ago
    st.markRunStarting(false, at = 1234)
    check st.getMeta("mode") == "real" and st.getMeta("started_at") == "1234"
    check st.getMeta("connection") == "starting" and st.getMeta("sending_ready") == "0"
    # the fourth write fails: none of them stays
    st.setMeta("connection", "ready")
    st.setMeta("sending_ready", "1")
    let raw = open(dir / "state.sqlite3", "", "", "")
    raw.exec(sql"""CREATE TRIGGER fail_ready BEFORE INSERT ON meta WHEN NEW.key = 'sending_ready'
                   BEGIN SELECT RAISE(ABORT, 'disk full'); END""")
    expect DbError:
      st.markRunStarting(true, at = 5678)
    raw.exec(sql"DROP TRIGGER fail_ready")
    raw.close()
    check st.getMeta("mode") == "real" and st.getMeta("started_at") == "1234"
    check st.getMeta("connection") == "ready" and st.getMeta("sending_ready") == "1"

  test "a log-out is not a running bot; it needs a new login (review run 4, R4-2)":
    let t = getTime().toUnix
    st.setMeta("last_alive", $(t - 10))
    st.setMeta("sending_ready", "0")
    st.setMeta("login_needed", "")
    st.setMeta("connection", "logged out")
    check not st.botRunning(t) and not st.botSending(t) and st.loggedOut()
    st.setMeta("connection", "ready")
    check st.botRunning(t) and not st.loggedOut()

st.close()
removeDir(dir)
