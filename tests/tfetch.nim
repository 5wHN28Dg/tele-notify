## fetch(): redirects, errors and time limits, against a local test server.

import std/[unittest, asyncdispatch, asynchttpserver, asyncnet, strutils, os, net]
import telenotify/[fetch, notify]

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

# A raw server that answers every request with fixed (malformed) bytes,
# chosen by the request path.
let raw = newAsyncSocket()
raw.setSockOpt(OptReuseAddr, true)
raw.bindAddr(Port(0), "127.0.0.1")
raw.listen()
let rawPort = raw.getLocalAddr()[1].uint16
const rawAnswers = {
  "/chunk-neg": "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\nFFFFFFFFFFFFFFFF\r\n",
  "/chunk-min": "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n8000000000000000\r\n",
  "/chunk-big": "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n7FFFFFFFFFFFFFFF\r\n",
  "/chunk-wrap": "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n10000000000000003\r\nabc\r\n0\r\n\r\n",
  "/chunk-ok": "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n"}
proc rawServe(c: AsyncSocket) {.async.} =
  try:
    let first = await c.recvLine()
    while (await c.recvLine()) notin ["\r\n", ""]: discard
    let path = first.splitWhitespace()[1]
    for (k, v) in rawAnswers:
      if k == path: await c.send(v)
  except CatchableError: discard
  c.close()
asyncCheck (proc () {.async.} =
  while true:
    let c = await raw.accept()
    asyncCheck rawServe(c))()

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

suite "fetch: review pass 3 (stage 2) findings":
  test "hostile chunk sizes are errors, not crashes":
    for path in ["/chunk-neg", "/chunk-min", "/chunk-big", "/chunk-wrap"]:
      let r = waitFor fetch("http://127.0.0.1:" & $rawPort & path, 2000, allowPrivate = true)
      check not r.ok
      check "chunk" in r.error or "too large" in r.error

  test "normal chunked bodies still work":
    let r = waitFor fetch("http://127.0.0.1:" & $rawPort & "/chunk-ok", 2000, allowPrivate = true)
    check r.ok and r.body == "hello world"

  test "more address forms that point at private networks":
    for h in ["64:ff9b::7f00:1", "2002:7f00:1::1", "::ffff:0:7f00:1", "fec0::1", "198.18.0.1", "192.0.0.1"]:
      check isPrivateIp(parseIpAddress(h))
    check not isPrivateIp(parseIpAddress("8.8.8.8"))
    check not isPrivateIp(parseIpAddress("2a00:1450:4001::200e"))

  test "time limits that aren't whole seconds are reported exactly":
    check "1500 ms" in get("/slow", 1500).error

suite "name lookups for your bot (#122, review run 2)":
  test "an address can be kept a while, so a slow resolver blocks once per period":
    let before = addressLookups
    check waitFor(fetch(base & "/ok", allowPrivate = true)).ok
    check waitFor(fetch(base & "/ok", allowPrivate = true)).ok
    check addressLookups == before + 2                   # job-board links: every time
    let kept = addressLookups
    for _ in 1 .. 3:
      check waitFor(fetch(base & "/ok", allowPrivate = true, keepAddressMs = 600_000)).ok
    check addressLookups == kept + 1

  test "a failed lookup is remembered a minute, so a failing resolver blocks once too (review run 3)":
    let before = addressLookups
    for _ in 1 .. 3:
      let r = waitFor fetch("http://no-such-host.invalid/x", keepAddressMs = 600_000)
      check not r.ok and "resolve" in r.error
    check addressLookups == before + 1
    # without keeping, every fetch looks it up again (job-board links)
    let plain = addressLookups
    for _ in 1 .. 2: discard waitFor fetch("http://no-such-host.invalid/x")
    check addressLookups == plain + 2

  test "the bot's fetcher keeps it for 10 minutes":
    forgetKeptAddresses()                     # an earlier test kept 127.0.0.1 already
    let f = botFetcher(allowPrivate = true)
    let before = addressLookups
    # 127.0.0.1, not localhost: CI's localhost answers ::1 first, and the test server is IPv4 only
    for _ in 1 .. 3: check waitFor(f("http://127.0.0.1:" & $port & "/ok")).ok
    check addressLookups == before + 1
    check keepBotAddressMs == 600_000

suite "kept addresses (#127)":
  test "a kept address expires":
    forgetKeptAddresses()
    let before = addressLookups
    check waitFor(fetch("http://127.0.0.1:" & $port & "/ok", allowPrivate = true, keepAddressMs = 150)).ok
    check waitFor(fetch("http://127.0.0.1:" & $port & "/ok", allowPrivate = true, keepAddressMs = 150)).ok
    check addressLookups == before + 1
    sleep 300
    check waitFor(fetch("http://127.0.0.1:" & $port & "/ok", allowPrivate = true, keepAddressMs = 150)).ok
    check addressLookups == before + 2

  test "an address kept for a fetch that allows local ones isn't used by one that refuses them":
    forgetKeptAddresses()
    check waitFor(fetch("http://127.0.0.1:" & $port & "/ok", allowPrivate = true, keepAddressMs = 600_000)).ok
    let r = waitFor fetch("http://127.0.0.1:" & $port & "/ok", keepAddressMs = 600_000)
    check not r.ok and "private" in r.error

  test "a kept address that can't be connected to is looked up again next time":
    forgetKeptAddresses()
    let probe = newSocket()
    probe.bindAddr(Port(0), "127.0.0.1")
    let closed = probe.getLocalAddr()[1]
    probe.close()                                    # nothing listens there now
    let url = "http://127.0.0.1:" & $closed & "/x"
    check not waitFor(fetch(url, allowPrivate = true, keepAddressMs = 600_000)).ok
    let before = addressLookups
    check not waitFor(fetch(url, allowPrivate = true, keepAddressMs = 600_000)).ok
    check addressLookups == before + 1

  test "a kept address whose fetch times out is looked up again next time (a hang, not a refusal)":
    forgetKeptAddresses()
    let slow = "http://127.0.0.1:" & $port & "/slow"
    check not waitFor(fetch(slow, timeoutMs = 200, allowPrivate = true, keepAddressMs = 600_000)).ok
    let before = addressLookups
    check waitFor(fetch("http://127.0.0.1:" & $port & "/ok", allowPrivate = true, keepAddressMs = 600_000)).ok
    check addressLookups == before + 1


  test "after a failed connect, a failing lookup still falls back to the address kept before":
    forgetKeptAddresses()
    let probe = newSocket()
    probe.bindAddr(Port(0), "127.0.0.1")
    let closed = probe.getLocalAddr()[1]
    probe.close()
    # the address stays in the table, expired: resolve's fallback for a lookup that fails
    check not waitFor(fetch("http://127.0.0.1:" & $closed & "/x", allowPrivate = true,
                            keepAddressMs = 600_000)).ok
    check keptAddressFor("127.0.0.1", closed, allowPrivate = true) == "127.0.0.1"   # kept, only expired


  test "while lookups fail, the address known before is used, with one lookup a minute (an outage)":
    forgetKeptAddresses()
    let name = "tn-gone.invalid"           # its lookup always fails
    keepAddressExpired(name, Port(port), allowPrivate = true, "127.0.0.1")
    let url = "http://" & name & ":" & $port & "/ok"
    let before = addressLookups
    check waitFor(fetch(url, allowPrivate = true, keepAddressMs = 600_000)).ok   # the fallback reached it
    check addressLookups == before + 1
    # the address stops answering (a failed connect marks it expired): still no new lookup this minute
    keepAddressExpired(name, Port(port), allowPrivate = true, "127.0.0.1")
    for _ in 1 .. 3: discard waitFor fetch(url, allowPrivate = true, keepAddressMs = 600_000)
    check addressLookups == before + 1
