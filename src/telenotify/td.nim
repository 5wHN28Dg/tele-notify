## An async client on top of TDLib's JSON interface.
##
## TDLib works by messages: we *send* a request (a JSON object with an
## "@type" like "getChat"), and later *receive* JSON objects. These are
## either answers to our requests or "updates" TDLib pushes on its own
## (new message, connection state changed, ...).
##
## To pair answers with requests, every request gets an "@extra" field
## with a unique number; TDLib copies it into the answer. `call` returns a
## Future that completes when that answer arrives. Everything without our
## "@extra" is an update and goes to `onUpdate`.
##
## `pump` is the loop that receives. It asks TDLib with a zero timeout (so
## it never blocks) and sleeps 20 ms when nothing is waiting, letting other
## async work (OCR, downloads) run in between.

import std/[asyncdispatch, json, tables]
import tdjson

type
  TdError* = object of CatchableError
    code*: int

  UpdateHandler* = proc (update: JsonNode) {.closure, gcsafe.}

  TdClient* = ref object
    lib: TdLib
    id: cint
    nextExtra: int
    pending: Table[string, Future[JsonNode]]
    onUpdate*: UpdateHandler
    stopped*: bool

proc setLogLevel*(lib: TdLib, level: int) =
  ## TDLib logs a lot to stderr by default; 1 = errors only.
  discard lib.execute(cstring($(%*{"@type": "setLogVerbosityLevel",
                                    "new_verbosity_level": level})))

proc newTdClient*(lib: TdLib): TdClient =
  lib.setLogLevel(1)
  result = TdClient(lib: lib, id: lib.createClientId())
  # TDLib only starts talking once it has received a first request.
  lib.send(result.id, cstring($(%*{"@type": "getOption", "name": "version", "@extra": "wake-up"})))

proc call*(c: TdClient, request: JsonNode): Future[JsonNode] =
  ## Send `request`; the future completes with TDLib's answer, or fails
  ## with TdError when TDLib answers with an "error" object or has closed.
  if c.stopped:
    let fut = newFuture[JsonNode]("td.call")
    fut.fail(newException(TdError, "TDLib closed"))
    return fut
  inc c.nextExtra
  let extra = $c.nextExtra
  var req = request.copy
  req["@extra"] = %extra
  let fut = newFuture[JsonNode]("td.call " & request{"@type"}.getStr)
  c.pending[extra] = fut
  c.lib.send(c.id, cstring($req))
  fut

proc dispatch(c: TdClient, msg: JsonNode) =
  let extra = msg{"@extra"}.getStr("")
  if extra != "" and extra in c.pending:
    var fut: Future[JsonNode]
    discard c.pending.pop(extra, fut)
    if msg{"@type"}.getStr == "error":
      let e = newException(TdError, msg{"message"}.getStr("TDLib error") &
                           " (" & $msg{"code"}.getInt & ")")
      e.code = msg{"code"}.getInt
      fut.fail(e)
    else:
      fut.complete(msg)
  elif extra != "":
    discard   # an answer nobody waits for (e.g. the wake-up request)
  elif c.onUpdate != nil:
    c.onUpdate(msg)

proc failPending(c: TdClient) =
  ## TDLib is gone: nobody will answer, so fail every waiting call instead
  ## of leaving it hanging forever.
  let waiting = c.pending
  c.pending.clear()
  for _, fut in waiting:
    if not fut.finished:
      fut.fail(newException(TdError, "TDLib closed"))

proc pump*(c: TdClient) {.async.} =
  ## Receive and dispatch until `stopped`. Run it with `asyncCheck`.
  defer: c.failPending()
  while not c.stopped:
    let raw = c.lib.receive(0.0)
    if raw == nil:
      await sleepAsync(20)
      continue
    var msg: JsonNode
    try: msg = parseJson($raw)
    except CatchableError: continue
    if msg{"@client_id"}.getInt(c.id.int) != c.id.int: continue
    try: c.dispatch(msg)
    except CatchableError: discard   # a handler problem must not stop the pump

proc close*(c: TdClient) {.async.} =
  ## Ask TDLib to save its state and shut down cleanly.
  try: discard await c.call(%*{"@type": "close"})
  except CatchableError: discard
  for _ in 0 ..< 100:          # wait for authorizationStateClosed (≤ 5 s)
    if c.stopped: break
    await sleepAsync(50)
  c.stopped = true

proc isClosedUpdate*(u: JsonNode): bool =
  u{"@type"}.getStr == "updateAuthorizationState" and
    u{"authorization_state", "@type"}.getStr == "authorizationStateClosed"

proc authState*(u: JsonNode): string =
  ## "authorizationStateWaitPhoneNumber" etc., or "" for other updates.
  if u{"@type"}.getStr == "updateAuthorizationState":
    u{"authorization_state", "@type"}.getStr
  else: ""

