## `telenotify review` asks before sending or discarding (#62); input that
## isn't a terminal does nothing unless --yes or --no answers for it; a dry
## run says so before anything; `undo_seconds` sets the undo window.

import std/[unittest, os, json, strutils]
from std/posix import nil
import telenotify/[state, commands, config]

let dir = getTempDir() / "tn-review-cli-test"
removeDir(dir)
createDir(dir)

proc configWith(extra: JsonNode = newJObject()): Config =
  var j = parseFile(currentSourcePath.parentDir / "keywords.json")
  j["data_dir"] = %(dir / "data")
  j["target_chat"] = %(-999)
  j["chats"] = %[-1]
  for k, v in extra: j[k] = v
  parseConfig(j)

let cfg = configWith()

proc answers(name, text: string): File =
  writeFile(dir / name, text)
  open(dir / name)

proc statusOf(msg: int64, dry = false): string =
  let st = openState(statePath(cfg, dry))
  defer: st.close()
  for r in st.pendingReviews():
    if r.msgId == msg: return "pending"
  for r in st.approvedReviews():
    if r.msgId == msg: return "to_send"
  "decided"

proc addOne(msg: int64, dry = false) =
  let st = openState(statePath(cfg, dry))
  defer: st.close()
  st.addReview(-1, msg, "chat1", "post " & $msg, "no level", "role", "")

proc captured(body: proc ()): string =
  ## What `body` prints on stdout.
  let path = dir / "out.txt"
  flushFile(stdout)
  let saved = posix.dup(1)
  let f = open(path, fmWrite)
  discard posix.dup2(getFileHandle(f), 1)
  try: body()
  finally:
    flushFile(stdout)
    discard posix.dup2(saved, 1)
    discard posix.close(saved)
    f.close()
  readFile(path)

suite "telenotify review asks first (#62)":
  test "send: only after a yes, with the undo window":
    addOne(1)
    var f = answers("no.txt", "s\nn\nq\n")
    check cmdReview(cfg, false, f, interactive = true) == 0
    f.close()
    check statusOf(1) == "pending"
    f = answers("yes.txt", "s\ny\n")
    check cmdReview(cfg, false, f, interactive = true) == 0
    f.close()
    check statusOf(1) == "to_send"
    let st = openState(statePath(cfg, false))
    defer: st.close()
    check st.approvedReviews()[0].sendAfter > 0 and st.dueReviews().len == 0   # waits its window

  test "discard: only after a yes":
    addOne(2)
    var f = answers("dno.txt", "d\n\nq\n")
    check cmdReview(cfg, false, f, interactive = true) == 0
    f.close()
    check statusOf(2) == "pending"
    f = answers("dyes.txt", "d\nyes\n")
    check cmdReview(cfg, false, f, interactive = true) == 0
    f.close()
    check statusOf(2) == "decided"

  test "input that isn't a terminal: exit 2 and nothing done, unless --yes or --no (CLI3)":
    addOne(3)
    var f = answers("pipe.txt", "s\ny\nd\ny\n")
    check cmdReview(cfg, false, f, interactive = false) == 2
    f.close()
    check statusOf(3) == "pending"
    check "isn't a terminal" in notATerminal and "--yes" in notATerminal
    f = answers("pipe-no.txt", "s\n")
    check cmdReview(cfg, false, f, answers = allNo, interactive = false) == 0
    f.close()
    check statusOf(3) == "pending"
    f = answers("pipe-yes.txt", "s\n")
    let shown = captured(proc () = check cmdReview(cfg, false, f, answers = allYes, interactive = false) == 0)
    f.close()
    check statusOf(3) == "to_send"
    check "Send this post to the channel? [y/N] yes (--yes)" in shown

  test "a dry run says so before anything, and asks in dry-run words (S7)":
    addOne(4, dry = true)
    let f = answers("dry.txt", "s\ny\n")
    let printed = captured(proc () = check cmdReview(cfg, true, f, interactive = true) == 0)
    f.close()
    check printed.startsWith("DRY RUN:")
    check "Record as approved (dry run, nothing is sent)? [y/N]" in printed
    check "Send this post to the channel?" notin printed and "[s]end to channel" notin printed
    check statusOf(4, dry = true) == "to_send"

suite "undo_seconds (A21)":
  test "default 30; 0 sends at once; out of range or not a number is refused":
    check cfg.undoSeconds == 30
    let off = configWith(%*{"undo_seconds": 0})
    check off.undoSeconds == 0
    check configWith(%*{"undo_seconds": 600}).undoSeconds == 600
    for bad in [%601, %(-1), %"30", %1.5]:
      expect ConfigError:
        discard configWith(%*{"undo_seconds": bad})
    addOne(5)
    let f = answers("off.txt", "s\ny\n")
    check cmdReview(off, false, f, interactive = true) == 0
    f.close()
    let st = openState(statePath(cfg, false))
    defer: st.close()
    var due = false
    for r in st.dueReviews():
      if r.msgId == 5: due = true
    check due                                         # no window: the bot may send it now

removeDir(dir)
