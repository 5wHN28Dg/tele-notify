## Stage 4: comparing the dry run with what the Python bot published.

import std/[unittest, os, times]
import telenotify/[state, compare]

let dir = getTempDir() / "tn-compare-test"
removeDir(dir)
let st = openState(dir / "state.sqlite3")
const ad = "مطلوب مهندس كهرباء حديث التخرج للعمل في حقل مجنون البصرة"

# Nim (dry run):
st.markProcessed(-1, 10, "Jobs", "publish", "مهندس كهرباء", "entry level", "would publish")
st.addPublished(-1, 10, ad)                                        # 1. both published it
st.markProcessed(-1, 11, "Jobs", "reject", "محاسب", "needs 5 years", "rejected")   # 2. Python only
st.markProcessed(-1, 12, "Jobs", "publish", "help desk", "entry role", "would publish")
st.addPublished(-1, 12, "IT help desk technician wanted in Basra, no experience needed")  # 3. Nim only
st.markProcessed(-2, 7, "Other", "publish", "مهندس كهرباء", "copy", "duplicate")    # 4. a copy
st.markProcessed(-1, 13, "Jobs", "publish", "مهندس كهرباء", "", "would publish",
                 rawText = "Electrical engineer, Rumaila, fresh graduates, apply by email")
st.addPublished(-1, 13, "Electrical engineer, Rumaila, fresh graduates, apply by email\n" &
                "(text read from the picture and the job page makes this much longer...)")  # 5. by text
st.markProcessed(-1, 14, "Jobs", "review", "مبرمج", "no level", "queued for review",
                 rawText = "Python developer needed in Basra, send your CV to the email below please")
                                                                     # 6. a forward of a forward

# The Python bot, in the target chat:
st.addOtherBot(100, -1, 10, ad)                      # forward from a channel
st.addOtherBot(101, -1, 11, "مطلوب محاسب")           # Nim rejected it
st.addOtherBot(102, -2, 7, ad)                       # the same ad from another channel
st.addOtherBot(103, 0, 0, "Electrical engineer, Rumaila, fresh graduates, apply by email!")  # from a group
st.addOtherBot(104, -9, 1, "something Nim never saw")
st.addOtherBot(104, -9, 1, "the same message noted twice")   # ignored
st.addOtherBot(105, -77, 3, "Python developer needed in Basra, send your CV to the email below please")
st.addOtherBot(108, 0, 12, "match: help desk\npost link: https://t.me/None/12")  # chat unknown
st.addOtherBot(106, -9, 5, "", albumId = 55)                 # an album: one post
st.addOtherBot(107, -9, 6, "album caption", albumId = 55)

suite "compare":
  test "both, Python only (with Nim's reasons), Nim only":
    let c = st.compareRuns(getTime().toUnix - 86400)
    check c.both.len == 4                  # 10, the copy of it, 13 by text, 12 by id
    check c.pythonOnly.len == 4                # 11, 104, 105 (by text), the album
    check c.pythonOnly[0].nimSaw and c.pythonOnly[0].nim.reasons == "needs 5 years"
    check not c.pythonOnly[1].nimSaw
    check c.pythonOnly[2].nimSaw and c.pythonOnly[2].nim.msgId == 14   # found by its text
    check c.pythonOnly[3].other.text == "album caption" and not c.pythonOnly[3].nimSaw
    check c.nimOnly.len == 0

  test "the time window applies":
    let c = st.compareRuns(getTime().toUnix + 60)
    check c.both.len + c.pythonOnly.len + c.nimOnly.len == 0

  test "a post the real bot summarized (#80) counts as published by Nim":
    let d2 = getTempDir() / "tn-compare-test-summary"
    removeDir(d2)
    let s2 = openState(d2 / "state.sqlite3")
    s2.markProcessed(-1, 20, "Jobs", "publish", "help desk", "entry role", "summarized")
    s2.addPublished(-1, 20, "IT help desk intern wanted in Basra")
    s2.addOtherBot(200, -1, 20, "Job match: help desk\nFrom: Jobs\nPost link: https://t.me/jobs/20")
    let c = s2.compareRuns(getTime().toUnix - 86400)
    check c.both.len == 1 and c.pythonOnly.len == 0 and c.nimOnly.len == 0
