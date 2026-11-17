## Logging in against a fake libtdjson, and where state is kept.

import std/[unittest, asyncdispatch, json, deques, os, strutils]
import telenotify/[session, tdjson, config, commands]

var outbox = initDeque[string]()
var current: string
var nextState = "authorizationStateWaitEmailAddress"

proc authUpdate(s: string): string =
  $(%*{"@type": "updateAuthorizationState", "authorization_state": {"@type": s}})

proc fakeCreate(): cint {.cdecl, gcsafe.} = 1
proc fakeExecute(req: cstring): cstring {.cdecl, gcsafe.} = nil
proc fakeSend(id: cint, req: cstring) {.cdecl, gcsafe.} = {.cast(gcsafe).}:
  let r = parseJson($req)
  outbox.addLast $(%*{"@type": "ok", "@extra": r["@extra"]})
  if r{"@type"}.getStr == "setTdlibParameters": outbox.addLast authUpdate(nextState)
proc fakeReceive(timeout: cdouble): cstring {.cdecl, gcsafe.} = {.cast(gcsafe).}:
  if outbox.len == 0: return nil
  current = outbox.popFirst
  current.cstring

let lib = TdLib(createClientId: fakeCreate, send: fakeSend, receive: fakeReceive, execute: fakeExecute)
var j = parseFile(currentSourcePath.parentDir / "keywords.json")
j["api_id"] = %1
j["api_hash"] = %"x"
j["data_dir"] = %(getTempDir() / "tn-session")
let cfg = parseConfig(j)

suite "session":
  test "a login step we can't handle is an error, not an endless wait":
    outbox.addLast authUpdate("authorizationStateWaitTdlibParameters")
    let fut = connectWith(lib, cfg, interactive = false)
    for _ in 0 ..< 40:                       # give up after ~2 s instead of hanging
      if fut.finished: break
      poll(50)
    check fut.finished and fut.failed and "unsupported login step" in fut.readError.msg

  test "our own shutdown is not a lost session; a logout is":
    check not sessionEnded("authorizationStateClosing", stopping = true)
    check not sessionEnded("authorizationStateClosed", stopping = true)
    check sessionEnded("authorizationStateLoggingOut", stopping = false)
    check sessionEnded("authorizationStateClosed", stopping = false)
    check not sessionEnded("authorizationStateReady", stopping = false)

  test "control characters are removed before printing a post":
    check printable("a\e]0;pwned\a\e[2Jb\nc\td\u009bé ع") == "a]0;pwned[2Jb\nc\tdé ع"

  test "dry runs keep their own state":
    check statePath(cfg, false).endsWith "state.sqlite3"
    check statePath(cfg, true).endsWith "state-dryrun.sqlite3"
