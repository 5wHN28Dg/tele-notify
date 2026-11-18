## The four C functions of TDLib's JSON interface, loaded at run time.
##
## TDLib (Telegram's official client library, C++) ships `libtdjson.so`,
## whose whole public interface is four functions that take and return JSON
## strings. This file is the only place that talks C.
##
## The library is loaded when the listener starts (`std/dynlib`), not linked
## at build time, so building and testing tele-notify never needs TDLib.
## Where it is looked for, in order:
## 1. the `tdlib_path` config key, if set;
## 2. `lib/libtdjson.so` next to the program (where `deploy/build-tdlib.sh`
##    puts it);
## 3. the system library path (`libtdjson.so`).

import std/[dynlib, os]

type
  TdCreateClientId = proc (): cint {.cdecl, gcsafe.}
  TdSend = proc (clientId: cint, request: cstring) {.cdecl, gcsafe.}
  TdReceive = proc (timeout: cdouble): cstring {.cdecl, gcsafe.}
  TdExecute = proc (request: cstring): cstring {.cdecl, gcsafe.}

  TdLib* = object
    handle: LibHandle
    createClientId*: TdCreateClientId
    send*: TdSend
    receive*: TdReceive
    execute*: TdExecute
    path*: string

  TdLibError* = object of CatchableError

proc candidates(configured: string): seq[string] =
  # A path you set is the only one tried: silently loading another copy
  # would hide the mistake (and run a TDLib you didn't choose).
  if configured != "": return @[configured]
  result.add getAppDir() / "lib" / "libtdjson.so"
  result.add getAppDir() / ".." / "lib" / "libtdjson.so"
  result.add "libtdjson.so"

proc loadTdLib*(configured = ""): TdLib =
  ## Find and load libtdjson. Raises TdLibError with every place it looked.
  var tried: seq[string]
  for path in candidates(configured):
    let h = loadLib(path)
    if h == nil:
      tried.add path
      continue
    result = TdLib(handle: h, path: path,
      createClientId: cast[TdCreateClientId](h.symAddr("td_create_client_id")),
      send: cast[TdSend](h.symAddr("td_send")),
      receive: cast[TdReceive](h.symAddr("td_receive")),
      execute: cast[TdExecute](h.symAddr("td_execute")))
    if result.createClientId == nil or result.send == nil or
        result.receive == nil or result.execute == nil:
      raise newException(TdLibError, path & " is not a TDLib JSON library")
    return
  raise newException(TdLibError, "libtdjson.so not found; looked in: " & $tried &
    ". Build it with deploy/build-tdlib.sh or set tdlib_path in config.json")
