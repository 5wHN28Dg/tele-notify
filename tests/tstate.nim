import std/[unittest, os, tables]
import telenotify/state

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

st.close()
removeDir(dir)
