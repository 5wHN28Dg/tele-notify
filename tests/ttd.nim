## TdClient against a fake libtdjson: four cdecl procs that answer like
## TDLib does (copying "@extra", adding "@client_id").

import std/[unittest, asyncdispatch, json, deques]
import telenotify/[td, tdjson]

var outbox = initDeque[string]()
var sent: seq[JsonNode]
var current: string            # keeps the returned C string alive

proc fakeCreate(): cint {.cdecl, gcsafe.} = 7
proc fakeExecute(req: cstring): cstring {.cdecl, gcsafe.} = nil
proc fakeSend(id: cint, req: cstring) {.cdecl, gcsafe.} = {.cast(gcsafe).}:
  let r = parseJson($req)
  sent.add r
  let extra = r{"@extra"}
  var answer: JsonNode
  case r{"@type"}.getStr
  of "getChat": answer = %*{"@type": "chat", "id": r["chat_id"], "title": "Jobs"}
  of "fail": answer = %*{"@type": "error", "code": 400, "message": "CHAT_NOT_FOUND"}
  of "silent": return              # TDLib never answers (it is closing)
  else: answer = %*{"@type": "ok"}
  if extra != nil: answer["@extra"] = extra
  answer["@client_id"] = %id
  outbox.addLast $answer
proc fakeReceive(timeout: cdouble): cstring {.cdecl, gcsafe.} = {.cast(gcsafe).}:
  if outbox.len == 0: return nil
  current = outbox.popFirst
  current.cstring

let lib = TdLib(createClientId: fakeCreate, send: fakeSend, receive: fakeReceive, execute: fakeExecute)

suite "TdClient":
  test "answers reach the right caller, errors become exceptions, updates go to the handler":
    let c = newTdClient(lib)
    var updates: seq[string]
    c.onUpdate = proc (u: JsonNode) {.closure, gcsafe.} =
      {.cast(gcsafe).}: updates.add u{"@type"}.getStr
    asyncCheck c.pump()
    # an update from TDLib (no @extra) and one for another client (ignored)
    outbox.addLast $(%*{"@type": "updateConnectionState", "@client_id": 7,
                        "state": {"@type": "connectionStateReady"}})
    outbox.addLast $(%*{"@type": "updateConnectionState", "@client_id": 99})
    let a = c.call(%*{"@type": "getChat", "chat_id": 5})
    let b = c.call(%*{"@type": "getChat", "chat_id": 6})
    check (waitFor b)["id"].getInt == 6
    check (waitFor a)["id"].getInt == 5
    expect TdError:
      discard waitFor c.call(%*{"@type": "fail"})
    check updates == @["updateConnectionState"]   # the other client's update is ignored
    check sent[0]{"@type"}.getStr == "getOption"   # the first request wakes TDLib up
    c.stopped = true

  test "when TDLib closes, waiting calls fail instead of hanging forever":
    let c = newTdClient(lib)
    let p = c.pump()
    let waiting = c.call(%*{"@type": "silent"})
    waitFor sleepAsync(60)
    check not waiting.finished
    c.stopped = true                # what authorizationStateClosed does
    waitFor p
    check waiting.failed
    expect TdError:
      discard waitFor c.call(%*{"@type": "getChat", "chat_id": 1})   # later calls fail at once

  test "a request TDLib never answers times out":
    let c = newTdClient(lib)
    asyncCheck c.pump()
    let f = c.call(%*{"@type": "silent"}, timeoutMs = 100)
    expect TdError:
      discard waitFor f
    c.stopped = true

  test "helpers":
    check authState(%*{"@type": "updateAuthorizationState",
                       "authorization_state": {"@type": "authorizationStateReady"}}) == "authorizationStateReady"
    check authState(%*{"@type": "updateNewMessage"}) == ""
    check isClosedUpdate(%*{"@type": "updateAuthorizationState",
                            "authorization_state": {"@type": "authorizationStateClosed"}})

  test "a missing library is a clear error":
    expect TdLibError:
      discard loadTdLib("/nonexistent/libtdjson.so")
