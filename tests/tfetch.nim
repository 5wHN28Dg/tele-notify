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

  test "local and private addresses and non-web schemes are refused":
    check "private" in (waitFor fetch(base & "/ok")).error
    check "private" in (waitFor fetch(base & "/to-private", allowPrivate = false)).error
    let r = waitFor fetch(base & "/to-private", allowPrivate = true)
    check not r.ok   # 10.0.0.1 is not refused here but unreachable/slow; must still not crash
    check "scheme" in (waitFor fetch(base & "/to-file", allowPrivate = true)).error
    check "scheme" in (waitFor fetch("ftp://example.com/x")).error

  test "sub-second time limits are reported sensibly":
    check "300 ms" in get("/slow", 300).error
