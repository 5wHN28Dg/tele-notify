## fetch(): redirects, errors and time limits, against a local test server.

import std/[unittest, asyncdispatch, asynchttpserver, asyncnet, strutils, os]
import telenotify/fetch

proc handler(req: Request) {.async, gcsafe.} =
  case req.url.path
  of "/ok": await req.respond(Http200, "hello")
  of "/redir": await req.respond(Http302, "", newHttpHeaders({"Location": "/ok"}))
  of "/missing": await req.respond(Http404, "nope")
  of "/big":
    await req.respond(Http200, "x".repeat(10), newHttpHeaders({"Content-Length": "200000000"}))
  of "/huge":
    await req.respond(Http200, "y".repeat(4_000_000))
  of "/to-private": await req.respond(Http302, "", newHttpHeaders({"Location": "http://10.0.0.1/admin"}))
  of "/to-file": await req.respond(Http302, "", newHttpHeaders({"Location": "file:///etc/passwd"}))
  of "/redir-big": await req.respond(Http302, "z".repeat(4_000_000), newHttpHeaders({"Location": "/ok"}))
  of "/redir-upper": await req.respond(Http302, "", newHttpHeaders({"Location": "HTTP://127.0.0.1:" & $req.hostname.len & "/never"}))
  of "/chunk": await req.respond(Http200, "chunked body")
  of "/slow":
    await sleepAsync(3000)
    await req.respond(Http200, "late")
  else: await req.respond(Http500, "")

let server = newAsyncHttpServer()
server.listen(Port(0))
let port = server.getPort.uint16
asyncCheck (proc () {.async.} =
  while true:
    if server.shouldAcceptRequest: await server.acceptRequest(handler)
    else: await sleepAsync(10))()
let base = "http://127.0.0.1:" & $port

# A server that accepts connections and never answers.
let silent = newAsyncSocket()
silent.setSockOpt(OptReuseAddr, true)
silent.bindAddr(Port(0), "127.0.0.1")
silent.listen()
let silentPort = silent.getLocalAddr()[1].uint16
proc holdSilently(c: AsyncSocket) {.async.} =
  # Never answer; just wait until the *client* hangs up, then close our side.
  # A client that leaks its socket keeps both ends open, which the test sees.
  try:
    while (await c.recv(4096)).len > 0: discard
  except CatchableError: discard
  c.close()
asyncCheck (proc () {.async.} =
  while true:
    let c = await silent.accept()
    asyncCheck holdSilently(c))()

proc openFds(): int =
  for _ in walkDir("/proc/self/fd"): inc result

# The tests talk to 127.0.0.1, which real fetches refuse (see below).
proc get(path: string, timeoutMs = 20_000): Fetched =
  waitFor fetch(base & path, timeoutMs, allowPrivate = true)

suite "fetch":

  test "a page":
    let r = get("/ok")
    check r.ok and r.body == "hello"

  test "redirects are followed and the final URL is known":
    let r = get("/redir")
    check r.ok and r.body == "hello"
    check r.finalUrl.endsWith("/ok")

  test "HTTP errors are results, not exceptions":
    let r = get("/missing")
    check not r.ok and r.status == 404

  test "time limit":
    let r = get("/slow", 300)
    check not r.ok and "timed out" in r.error

  test "connection refused":
    let r = waitFor fetch("http://127.0.0.1:1/", allowPrivate = true)
    check not r.ok and r.error.len > 0

suite "fetch: review pass 1 (stage 2) findings":
  test "timed-out downloads don't leak sockets":
    let before = openFds()
    for i in 0 ..< 30:
      let r = waitFor fetch("http://127.0.0.1:" & $silentPort & "/", 100, allowPrivate = true)
      check not r.ok
    waitFor sleepAsync(300)   # let the server notice the closed connections
    check openFds() - before < 5

  test "a declared size over the cap is refused before downloading":
    let r = waitFor fetch(base & "/big", allowPrivate = true)
    check not r.ok and "too large" in r.error

  test "an actual body over the cap is refused":
    let r = waitFor fetch(base & "/huge", allowPrivate = true)
    check not r.ok and "too large" in r.error

  test "local and private addresses are refused, however they are written":
    for u in ["http://127.0.0.1:" & $port & "/ok", "http://127.1:" & $port & "/",
              "http://2130706433:" & $port & "/", "http://0x7f000001:" & $port & "/",
              "http://0:" & $port & "/", "http://localhost.:" & $port & "/",
              "http://localhost:" & $port & "/", "http://[::1]:" & $port & "/",
              "http://[::ffff:127.0.0.1]:" & $port & "/", "http://[::]:" & $port & "/",
              "http://10.1.2.3/", "http://192.168.1.1/", "http://169.254.169.254/"]:
      let r = waitFor fetch(u, 2000)
      check not r.ok
      check "private" in r.error
    # ... including after a redirect from an allowed page
    check "private" in (waitFor fetch(base & "/to-private")).error

  test "non-web schemes are refused":
    check "scheme" in (waitFor fetch(base & "/to-file", allowPrivate = true)).error
    check "scheme" in (waitFor fetch("ftp://example.com/x")).error

  test "an upper-case scheme in a redirect is fine":
    let r = waitFor fetch(base & "/redir-upper", 2000, allowPrivate = true)
    check "scheme" notin r.error

  test "a redirect's body is never read":
    let r = waitFor fetch(base & "/redir-big", allowPrivate = true)
    check r.ok and r.body == "hello"

  test "a connection still being opened is cancelled by the time limit":
    let before = openFds()
    for i in 0 ..< 10:   # 10.255.255.1 never answers, so connect() hangs
      let r = waitFor fetch("http://10.255.255.1/", 100, allowPrivate = true)
      check not r.ok and "timed out" in r.error
    waitFor sleepAsync(100)
    check openFds() - before < 3

  test "sub-second time limits are reported sensibly":
    check "300 ms" in get("/slow", 300).error
