## Logging in against a fake libtdjson, and where state is kept.

import std/[unittest, asyncdispatch, json, deques, os, strutils, unicode]
import telenotify/[session, tdjson, config, commands, state]

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

  test "logged out at start: the screen is told (#65)":
    let st = openState(getTempDir() / "tn-session-login" / "state.sqlite3")
    defer: st.close()
    st.setMeta("login_needed", "")
    nextState = "authorizationStateWaitPhoneNumber"
    outbox.addLast authUpdate("authorizationStateWaitTdlibParameters")
    let fut = loggedInOrRecord(st, false, connectWith(lib, cfg, interactive = false))
    for _ in 0 ..< 40:
      if fut.finished: break
      poll(50)
    check fut.finished and fut.failed and fut.readError of NotLoggedIn
    check st.getMeta("login_needed") == "1"
    check st.getMeta("connection") == "logged out"

  test "logged in again: cleared as soon as TDLib is ready, not after the chat list (#65)":
    let st = openState(getTempDir() / "tn-session-login" / "state.sqlite3")
    defer: st.close()
    st.setMeta("login_needed", "1")
    nextState = "authorizationStateReady"
    outbox.addLast authUpdate("authorizationStateWaitTdlibParameters")
    let fut = loggedInOrRecord(st, false, connectWith(lib, cfg, interactive = false))
    for _ in 0 ..< 40:
      if fut.finished: break
      poll(50)
    check fut.finished and not fut.failed
    check st.getMeta("login_needed") == ""

  test "our own shutdown is not a lost session; a logout is":
    check not sessionEnded("authorizationStateClosing", stopping = true)
    check not sessionEnded("authorizationStateClosed", stopping = true)
    check sessionEnded("authorizationStateLoggingOut", stopping = false)
    check sessionEnded("authorizationStateClosed", stopping = false)
    check not sessionEnded("authorizationStateReady", stopping = false)

  test "control characters are removed before printing a post":
    check printable("a\e]0;pwned\a\e[2Jb\nc\td\u009bé ع") == "a]0;pwned[2Jb\nc\tdé ع"

  test "stats columns count letters, not bytes":
    check fitColumn("وظائف العراق للشباب والبنات", 10).runeLen == 10
    check fitColumn("وظائف العراق للشباب والبنات", 10).validateUtf8 == -1
    check fitColumn("Jobs", 6) == "Jobs  "

  test "dry runs keep their own state":
    check statePath(cfg, false).endsWith "state.sqlite3"
    check statePath(cfg, true).endsWith "state-dryrun.sqlite3"

suite "chat kinds":
  test "channel, group or private, from TDLib's chat type":
    check chatKind(%*{"type": {"@type": "chatTypeSupergroup", "is_channel": true}}) == "channel"
    check chatKind(%*{"type": {"@type": "chatTypeSupergroup", "is_channel": false}}) == "group"
    check chatKind(%*{"type": {"@type": "chatTypeBasicGroup"}}) == "group"
    check chatKind(%*{"type": {"@type": "chatTypePrivate"}}) == "private"
    check chatKind(%*{}) == ""

suite "corpus ids":
  test "numbers, text ids, and a missing id (review)":
    check corpusId(%*{"id": 194637}) == "194637"
    check corpusId(%*{"id": "-1:11"}) == "-1:11"
    check corpusId(%*{"text": "x"}) == "?"

suite "config":
  test "notify_bot_token: none by default, spaces around it removed (#122)":
    var j2 = parseFile(currentSourcePath.parentDir / "keywords.json")
    check not j2.hasKey("notify_bot_token")
    check parseConfig(j2).notifyBotToken == ""
    j2["notify_bot_token"] = %"  123456:AAH-x_y9\n"
    check parseConfig(j2).notifyBotToken == "123456:AAH-x_y9"

  test "notify_published and notify_review: on by default, each can be turned off (review run 2)":
    var j2 = parseFile(currentSourcePath.parentDir / "keywords.json")
    check parseConfig(j2).notifyPublished and parseConfig(j2).notifyReview
    j2["notify_published"] = %false
    check not parseConfig(j2).notifyPublished and parseConfig(j2).notifyReview
    j2["notify_review"] = %false
    check not parseConfig(j2).notifyReview

  test "an empty data_dir means the default folder (review)":
    var j2 = parseFile(currentSourcePath.parentDir / "keywords.json")
    j2["data_dir"] = %""
    check parseConfig(j2).dataDir == getHomeDir() / ".local" / "share" / "tele-notify"

  test "data_dir: ~ is your home, a relative path is next to config.json (review)":
    let d = getTempDir() / "tn-cfg-paths"
    createDir(d)
    var j3 = parseFile(currentSourcePath.parentDir / "keywords.json")
    j3["data_dir"] = %"bot-state"
    writeFile(d / "config.json", $j3)
    check loadConfig(d / "config.json").dataDir == d / "bot-state"
    j3["data_dir"] = %"  ~/tn-data  "
    writeFile(d / "config.json", $j3)
    check loadConfig(d / "config.json").dataDir == getHomeDir() / "tn-data"

  test "without --config: $TELENOTIFY_CONFIG, then ~/.config/tele-notify, then ./config.json":
    let d = getTempDir() / "tn-cfg-lookup"
    removeDir(d)
    createDir(d / "xdg" / "tele-notify")
    let oldXdg = getEnv("XDG_CONFIG_HOME")
    let oldEnv = getEnv("TELENOTIFY_CONFIG")
    defer:
      putEnv("XDG_CONFIG_HOME", oldXdg)
      if oldEnv.len > 0: putEnv("TELENOTIFY_CONFIG", oldEnv) else: delEnv("TELENOTIFY_CONFIG")
      removeDir(d)
    delEnv("TELENOTIFY_CONFIG")
    putEnv("XDG_CONFIG_HOME", d / "xdg")
    let std = d / "xdg" / "tele-notify" / "config.json"
    # nothing there yet: the standard place, so the error names it
    if not fileExists("config.json"): check defaultConfigPath() == std
    writeFile(std, "{}")
    check defaultConfigPath() == std
    putEnv("TELENOTIFY_CONFIG", d / "mine.json")
    check defaultConfigPath() == d / "mine.json"
    putEnv("TELENOTIFY_CONFIG", "  ")
    check defaultConfigPath() == std
