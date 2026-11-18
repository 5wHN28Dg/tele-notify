## tdTelegram (the real Telegram operations) against a fake libtdjson that
## answers getChat / getChatHistory / forwardMessages the way TDLib does.

import std/[unittest, asyncdispatch, json, deques, sequtils]
import telenotify/[td, tdjson, telegram]

var outbox = initDeque[string]()
var current: string
var history: seq[int64]          # message ids in the chat, oldest first
var lastRead = 0'i64
var forwardWorks = true
var partly = false             # only the first message of an album goes out

proc page(fromId: int64, limit: int): JsonNode =
  ## TDLib's getChatHistory with offset 0: newest first, starting *at*
  ## from_message_id (0 = the newest message).
  result = newJArray()
  var started = fromId == 0
  for i in countdown(history.high, 0):
    if history[i] == fromId: started = true
    if started and result.len < limit:
      result.add %*{"@type": "message", "id": history[i], "chat_id": -1,
                    "content": {"@type": "messageText", "text": {"text": "post " & $history[i]}}}

proc fakeCreate(): cint {.cdecl, gcsafe.} = 1
proc fakeExecute(req: cstring): cstring {.cdecl, gcsafe.} = nil
proc fakeSend(id: cint, req: cstring) {.cdecl, gcsafe.} = {.cast(gcsafe).}:
  let r = parseJson($req)
  var answer: JsonNode
  case r{"@type"}.getStr
  of "getChat":
    answer = %*{"@type": "chat", "id": -1, "title": "Jobs", "last_read_inbox_message_id": lastRead,
                "unread_count": history.filterIt(it > lastRead).len}
  of "getChatHistory":
    answer = %*{"@type": "messages", "messages": page(r["from_message_id"].getBiggestInt,
                                                      r["limit"].getInt)}
  of "getMessageProperties": answer = %*{"@type": "messageProperties", "can_be_forwarded": true}
  of "forwardMessages":
    var msgs = newJArray()
    for i, _ in r["message_ids"].getElems:
      msgs.add(if forwardWorks or (partly and i == 0): %*{"@type": "message", "id": 1} else: newJNull())
    answer = %*{"@type": "messages", "messages": msgs}
  else: answer = %*{"@type": "ok"}
  answer["@extra"] = r["@extra"]
  outbox.addLast $answer
proc fakeReceive(timeout: cdouble): cstring {.cdecl, gcsafe.} = {.cast(gcsafe).}:
  if outbox.len == 0: return nil
  current = outbox.popFirst
  current.cstring

let c = newTdClient(TdLib(createClientId: fakeCreate, send: fakeSend, receive: fakeReceive,
                          execute: fakeExecute))
asyncCheck c.pump()
let tg = tdTelegram(c)

suite "tdTelegram":
  test "unread: every unread message once, oldest first, across pages":
    history = toSeq(1 .. 250).mapIt(it.int64)
    lastRead = 20
    let u = waitFor tg.unread(-1)
    check u.messages.mapIt(it.id) == toSeq(21 .. 250).mapIt(it.int64) and not u.cut

  test "unread: a chat that is all unread ends at its first message":
    history = toSeq(1 .. 150).mapIt(it.int64)
    lastRead = 0
    check (waitFor tg.unread(-1)).messages.mapIt(it.id) == toSeq(1 .. 150).mapIt(it.int64)

  test "unread: a huge backlog is cut, and says so":
    history = toSeq(1 .. maxBacklog + 500).mapIt(it.int64)
    lastRead = 0
    let u = waitFor tg.unread(-1)
    check u.cut and u.messages.len == maxBacklog and u.messages[^1].id == int64(maxBacklog + 500)

  test "forward: a null result (could not forward) counts as not forwarded":
    forwardWorks = true
    check waitFor tg.forward(-9, -1, @[5'i64])
    check waitFor tg.forward(-9, -1, @[5'i64, 6, 7])         # an album
    forwardWorks = false
    check not waitFor tg.forward(-9, -1, @[5'i64])
    partly = true                                            # one picture failed:
    check waitFor tg.forward(-9, -1, @[5'i64, 6, 7])         # it's in the channel, no link too
