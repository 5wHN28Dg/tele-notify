## Starting TDLib and logging in.
##
## TDLib keeps your login in its database (`<data_dir>/tdlib`), so you log
## in once with `telenotify login` (phone number, the code Telegram sends
## you, and your 2FA password if you have one). After that, `run` starts
## without asking anything. If the login is missing, `run` stops with a
## message instead of waiting for input that a systemd service can't give.

import std/[asyncdispatch, json, os, strutils, terminal, sets]
import config, td, tdjson

type
  NotLoggedIn* = object of ValueError
    ## The account isn't logged in (never was, or Telegram logged it out).
  Session* = object
    client*: TdClient
    lib*: TdLib

proc tdParams(cfg: Config): JsonNode =
  %*{"@type": "setTdlibParameters", "use_test_dc": false,
     "database_directory": cfg.dataDir / "tdlib",
     "files_directory": cfg.dataDir / "tdlib-files",
     "database_encryption_key": "",
     "use_file_database": true, "use_chat_info_database": true,
     "use_message_database": true, "use_secret_chats": false,
     "api_id": cfg.apiId, "api_hash": cfg.apiHash,
     "system_language_code": "en", "device_model": "tele-notify",
     "system_version": "", "application_version": "4.0"}

proc ask(prompt: string, secret = false): string =
  if secret: return readPasswordFromStdin(prompt)
  stdout.write prompt
  stdout.flushFile
  stdin.readLine.strip

proc connectWith*(lib: TdLib, cfg: Config, interactive: bool,
                  onUpdate: UpdateHandler = nil,
                  onLocked: proc () {.closure, gcsafe.} = nil): Future[Session] {.async.} =
  ## `connect` with a given library (tests pass a fake one). `onLocked` is
  ## called once TDLib has taken its database (setTdlibParameters answered:
  ## it holds the lock on `<data_dir>/tdlib`, so no other bot on this folder
  ## runs), before the login is checked.
  let c = newTdClient(lib)
  var authState = ""
  c.onUpdate = proc (u: JsonNode) {.closure, gcsafe.} =
    let s = u.authState
    if s != "": authState = s
    if u.isClosedUpdate: c.stopped = true
    if onUpdate != nil: onUpdate(u)
  asyncCheck c.pump()
  var handled = ""
  while true:
    if authState == handled:
      await sleepAsync(50)
      continue
    handled = authState
    case authState
    of "authorizationStateWaitTdlibParameters":
      discard await c.call(tdParams(cfg))
      if onLocked != nil: onLocked()
    of "authorizationStateWaitPhoneNumber", "authorizationStateWaitCode",
       "authorizationStateWaitPassword", "authorizationStateWaitOtherDeviceConfirmation":
      if not interactive:
        c.stopped = true   # as for an unsupported step: nothing more to read
        raise newException(NotLoggedIn, "not logged in to Telegram: run `telenotify login` first")
      try:
        case authState
        of "authorizationStateWaitPhoneNumber":
          discard await c.call(%*{"@type": "setAuthenticationPhoneNumber",
            "phone_number": ask("Phone number (international format, e.g. +964...): "),
            "settings": nil})
        of "authorizationStateWaitCode":
          discard await c.call(%*{"@type": "checkAuthenticationCode",
            "code": ask("Code Telegram sent you: ")})
        of "authorizationStateWaitPassword":
          discard await c.call(%*{"@type": "checkAuthenticationPassword",
            "password": ask("Two-step verification password: ", secret = true)})
        else:
          echo "Confirm the login on another device where you're logged in."
      except TdError as e:
        echo "Telegram said: ", e.msg, " — try again."
        handled = ""   # ask again
    of "authorizationStateReady":
      return Session(client: c, lib: lib)
    of "authorizationStateClosed", "authorizationStateClosing", "authorizationStateLoggingOut":
      raise newException(ValueError, "TDLib closed during login (" & authState & ")")
    else:
      # e.g. Telegram asking for an e-mail address, or a new account's
      # registration: waiting would hang forever, so say what it wants
      c.stopped = true
      raise newException(ValueError, "unsupported login step: " & authState &
        ". Log in once with an official Telegram app on this account, then try again")

proc connect*(cfg: Config, interactive: bool,
              onUpdate: UpdateHandler = nil,
              onLocked: proc () {.closure, gcsafe.} = nil): Future[Session] {.async.} =
  ## Start TDLib and wait until logged in. With `interactive`, asks for the
  ## phone number, code and password as needed; otherwise raises if not
  ## logged in.
  if cfg.apiId == 0 or cfg.apiHash == "":
    raise newException(ValueError, "api_id and api_hash are missing from config.json")
  createDir(cfg.dataDir)
  setFilePermissions(cfg.dataDir, {fpUserRead, fpUserWrite, fpUserExec})  # your login lives here
  return await connectWith(loadTdLib(cfg.tdlibPath), cfg, interactive, onUpdate, onLocked)

proc loadAllChats*(s: Session) {.async.} =
  ## TDLib only knows chats it has loaded; ask for the main list and the
  ## Archive (job channels are often archived or muted).
  for list in ["chatListMain", "chatListArchive"]:
    while true:
      try:
        discard await s.client.call(%*{"@type": "loadChats",
          "chat_list": {"@type": list}, "limit": 100})
      except TdError as e:
        if e.code == 404: break   # all chats of this list loaded
        raise

proc chatKind*(chat: JsonNode): string =
  ## "channel", "group" or "private", from a TDLib chat object.
  case chat{"type", "@type"}.getStr
  of "chatTypeSupergroup": (if chat{"type", "is_channel"}.getBool: "channel" else: "group")
  of "chatTypeBasicGroup": "group"
  of "chatTypePrivate", "chatTypeSecret": "private"
  else: ""

proc chatRows*(s: Session, timeoutMs = 300_000): Future[seq[(int64, string, string)]] {.async.} =
  ## Every chat in the main list and the Archive: (id, title, kind).
  var seen: HashSet[int64]
  for list in ["chatListMain", "chatListArchive"]:
    let r = await s.client.call(%*{"@type": "getChats",
      "chat_list": {"@type": list}, "limit": 1000}, timeoutMs)
    for id in r{"chat_ids"}.getElems:
      let cid = id.getBiggestInt
      if cid in seen: continue
      seen.incl cid
      let chat = await s.client.call(%*{"@type": "getChat", "chat_id": cid}, timeoutMs)
      result.add (cid, chat{"title"}.getStr, chatKind(chat))

proc listChats*(s: Session): Future[seq[(int64, string)]] {.async.} =
  await s.loadAllChats()
  for list in ["chatListMain", "chatListArchive"]:
    let r = await s.client.call(%*{"@type": "getChats",
      "chat_list": {"@type": list}, "limit": 1000})
    for id in r{"chat_ids"}.getElems:
      let chat = await s.client.call(%*{"@type": "getChat", "chat_id": id.getBiggestInt})
      result.add (id.getBiggestInt, chat{"title"}.getStr &
                  (if list == "chatListArchive": "  (archived)" else: ""))
