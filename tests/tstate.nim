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
    st.decideReview(p[0].id, "sent")
    check st.pendingReviews().len == 0

  test "stats per chat":
    st.markProcessed(-100, 2, "Jobs", "reject", "", "", "rejected")
    st.markProcessed(-200, 1, "Other", "review", "", "", "queued for review")
    let s = st.stats(7)
    check s.len == 2
    check s[0].chat == "Jobs" and s[0].verdicts == [1, 0, 1] and s[0].forwarded == 1

  test "meta values":
    st.setMeta("last_alive", "123")
    check st.getMeta("last_alive") == "123"
    check st.getMeta("missing") == ""

st.close()
removeDir(dir)
