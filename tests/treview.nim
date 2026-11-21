## `telenotify review` asks before sending or discarding, and a pipe or a
## script is never taken as a yes (#62).

import std/[unittest, os, json]
import telenotify/[state, commands, config]

let dir = getTempDir() / "tn-review-cli-test"
removeDir(dir)
createDir(dir)

var j = parseFile(currentSourcePath.parentDir / "keywords.json")
j["data_dir"] = %(dir / "data")
j["target_chat"] = %(-999)
j["chats"] = %[-1]
let cfg = parseConfig(j)

proc answers(name, text: string): File =
  writeFile(dir / name, text)
  open(dir / name)

proc statusOf(msg: int64): string =
  let st = openState(statePath(cfg, false))
  defer: st.close()
  for r in st.pendingReviews():
    if r.msgId == msg: return "pending"
  for r in st.approvedReviews():
    if r.msgId == msg: return "to_send"
  "decided"

proc addOne(msg: int64) =
  let st = openState(statePath(cfg, false))
  defer: st.close()
  st.addReview(-1, msg, "chat1", "post " & $msg, "no level", "role", "")

suite "telenotify review asks first (#62)":
  test "send: only after a yes, with the undo window":
    addOne(1)
    var f = answers("no.txt", "s\nn\nq\n")
    cmdReview(cfg, false, f, interactive = true)
    f.close()
    check statusOf(1) == "pending"
    f = answers("yes.txt", "s\ny\n")
    cmdReview(cfg, false, f, interactive = true)
    f.close()
    check statusOf(1) == "to_send"
    let st = openState(statePath(cfg, false))
    defer: st.close()
    check st.approvedReviews()[0].sendAfter > 0 and st.dueReviews().len == 0   # waits its window

  test "discard: only after a yes":
    addOne(2)
    var f = answers("dno.txt", "d\n\nq\n")
    cmdReview(cfg, false, f, interactive = true)
    f.close()
    check statusOf(2) == "pending"
    f = answers("dyes.txt", "d\nyes\n")
    cmdReview(cfg, false, f, interactive = true)
    f.close()
    check statusOf(2) == "decided"

  test "input that isn't a terminal is a no, even a piped y":
    addOne(3)
    let f = answers("pipe.txt", "s\ny\nd\ny\n")
    cmdReview(cfg, false, f, interactive = false)
    f.close()
    check statusOf(3) == "pending"
    check not askYesNo("Send?", f, interactive = false)

removeDir(dir)
