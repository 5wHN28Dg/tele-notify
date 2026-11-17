## fetch(): redirects, errors and time limits, against a local test server.

import std/[unittest, asyncdispatch, asynchttpserver, strutils]
import telenotify/fetch

proc handler(req: Request) {.async, gcsafe.} =
  case req.url.path
  of "/ok": await req.respond(Http200, "hello")
  of "/redir": await req.respond(Http302, "", newHttpHeaders({"Location": "/ok"}))
  of "/missing": await req.respond(Http404, "nope")
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

suite "fetch":
  test "a page":
    let r = waitFor fetch(base & "/ok")
    check r.ok and r.body == "hello"

  test "redirects are followed and the final URL is known":
    let r = waitFor fetch(base & "/redir")
    check r.ok and r.body == "hello"
    check r.finalUrl.endsWith("/ok")

  test "HTTP errors are results, not exceptions":
    let r = waitFor fetch(base & "/missing")
    check not r.ok and r.status == 404

  test "time limit":
    let r = waitFor fetch(base & "/slow", timeoutMs = 300)
    check not r.ok and "timed out" in r.error

  test "connection refused":
    let r = waitFor fetch("http://127.0.0.1:1/")
    check not r.ok and r.error.len > 0
