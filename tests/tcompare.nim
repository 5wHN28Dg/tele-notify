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
st.markProcessed(-1, 13, "Jobs", "publish", "مهندس كهرباء", "", "would publish")
st.addPublished(-1, 13, "Electrical engineer, Rumaila, fresh graduates, apply by email")  # 5. matched by text

# The Python bot, in the target chat:
st.addOtherBot(100, -1, 10, ad)                      # forward from a channel
st.addOtherBot(101, -1, 11, "مطلوب محاسب")           # Nim rejected it
st.addOtherBot(102, -2, 7, ad)                       # the same ad from another channel
st.addOtherBot(103, 0, 0, "Electrical engineer, Rumaila, fresh graduates, apply by email!")  # from a group
st.addOtherBot(104, -9, 1, "something Nim never saw")
st.addOtherBot(104, -9, 1, "the same message noted twice")   # ignored

suite "compare":
  test "both, Python only (with Nim's reasons), Nim only":
    let c = st.compareRuns(getTime().toUnix - 86400)
    check c.both.len == 3                  # 10, the copy of it, and 13 by text
    check c.pythonOnly.len == 2
    check c.pythonOnly[0].nimSaw and c.pythonOnly[0].nim.reasons == "needs 5 years"
    check not c.pythonOnly[1].nimSaw
    check c.nimOnly.len == 1 and c.nimOnly[0].nim.msgId == 12

  test "the time window applies":
    let c = st.compareRuns(getTime().toUnix + 60)
    check c.both.len + c.pythonOnly.len + c.nimOnly.len == 0
