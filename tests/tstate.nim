import std/[unittest, os]
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

  test "a new data folder is private to you":
    check getFilePermissions(dir) == {fpUserRead, fpUserWrite, fpUserExec}

  test "meta values":
    st.setMeta("last_alive", "123")
    check st.getMeta("last_alive") == "123"
    check st.getMeta("missing") == ""

st.close()
removeDir(dir)
