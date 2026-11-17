## Downloading pages, safely.
##
## Rules learned from the old version (problems report 4.1, 5.3) and from
## the stage 2 review:
## * a download can never crash the caller: every failure becomes a
##   `Fetched` with `ok = false` and a reason;
## * every download has a time limit (20 s by default). When it fires the
##   connection is *closed*, not just abandoned: abandoned connections
##   leaked one socket each;
## * pages are capped at 3 MB, checked against the announced size first and
##   then while reading, so a huge page is never held in memory;
## * only http and https, and never local or private network addresses
##   (127.0.0.1, 10.x, 192.168.x, ...): a short link in a post must not be
##   able to make us poke at the home network. Tests turn this off with
##   `allowPrivate` to talk to their own local server.
##
## Redirects are followed by hand (up to 5) so we know the *final* URL;
## lnkd.in short links need it. Uses Nim's standard async HTTP client,
## which needs `-d:ssl` (set in config.nims).
##
## Known limit: the standard client looks up host names synchronously, so
## a very slow DNS server can pause the bot briefly; the time limit can't
## interrupt that part.

import std/[asyncdispatch, httpclient, uri, strutils, net]

const
  userAgent* = "tele-notify (+https://github.com/5wHN28Dg/tele-notify)"
  maxBytes* = 3_000_000

type
  Fetched* = object
    ok*: bool
    status*: int
    finalUrl*: string
    body*: string
    error*: string     ## why it failed, for logs and verdict reasons

  Fetcher* = proc (url: string): Future[Fetched] {.closure, gcsafe.}
    ## Anything that can download a URL. Tests pass a fake one.

proc isPrivateHost*(host: string): bool =
  ## Loopback, private, link-local or "this host" addresses, and localhost.
  let h = host.toLowerAscii.strip(chars = {'[', ']'})
  if h == "localhost" or h.endsWith(".localhost") or h.endsWith(".local"): return true
  if not isIpAddress(h): return false
  let ip = parseIpAddress(h)
  case ip.family
  of IpAddressFamily.IPv4:
    let a = ip.address_v4
    a[0] in [0'u8, 10, 127] or (a[0] == 172 and a[1] in 16'u8 .. 31'u8) or
      (a[0] == 192 and a[1] == 168) or (a[0] == 169 and a[1] == 254) or
      (a[0] == 100 and a[1] in 64'u8 .. 127'u8)
  of IpAddressFamily.IPv6:
    let a = ip.address_v6
    var loopback = a[15] == 1
    for i in 0 .. 14:
      if a[i] != 0: loopback = false
    loopback or (a[0] and 0xFE) == 0xFC or (a[0] == 0xFE and (a[1] and 0xC0) == 0x80)

proc refuse(url: string, allowPrivate: bool): string =
  ## Why `url` must not be downloaded, or "" if it's fine.
  let u = parseUri(url)
  if u.scheme notin ["http", "https"]: return "unsupported scheme '" & u.scheme & "'"
  if not allowPrivate and u.hostname.isPrivateHost: return "refused a private/local address"
  ""

proc readCapped(resp: AsyncResponse): Future[(bool, string)] {.async.} =
  ## The body, stopping as soon as it passes `maxBytes`.
  var body = ""
  while true:
    let (more, chunk) = await resp.bodyStream.read()
    if not more: break
    body.add chunk
    if body.len > maxBytes: return (false, "")
  return (true, body)

proc fetchWith(client: AsyncHttpClient, url: string, allowPrivate: bool): Future[Fetched] {.async.} =
  var current = url
  try:
    for hop in 0 .. 5:
      let why = refuse(current, allowPrivate)
      if why != "":
        return Fetched(ok: false, finalUrl: current, error: why)
      let resp = await client.get(current)
      let code = resp.code.int
      if code in 300 .. 399 and resp.headers.hasKey("location"):
        current = $(parseUri(current).combine(parseUri(resp.headers["location"])))
        discard await resp.readCapped   # drain before reusing the connection
        continue
      if resp.headers.hasKey("content-length"):
        var announced = 0
        try: announced = parseInt(resp.headers["content-length"])
        except ValueError: discard
        if announced > maxBytes:
          return Fetched(ok: false, status: code, finalUrl: current, error: "page too large")
      let (fits, body) = await resp.readCapped
      if not fits:
        return Fetched(ok: false, status: code, finalUrl: current, error: "page too large")
      if code != 200:
        return Fetched(ok: false, status: code, finalUrl: current, body: body,
                       error: "HTTP " & $code)
      return Fetched(ok: true, status: code, finalUrl: current, body: body)
    return Fetched(ok: false, finalUrl: current, error: "too many redirects")
  except CatchableError as e:
    return Fetched(ok: false, finalUrl: current, error: e.msg.splitLines[0])

proc fetch*(url: string, timeoutMs = 20_000, allowPrivate = false): Future[Fetched] {.async.} =
  ## Download `url`. Never raises; check `ok`.
  let client = newAsyncHttpClient(userAgent = userAgent, maxRedirects = 0)
  let fut = fetchWith(client, url, allowPrivate)
  if await withTimeout(fut, timeoutMs):
    client.close()
    return fut.read
  client.close()   # fails the pending request and frees its socket
  let limit = if timeoutMs < 1000: $timeoutMs & " ms" else: $(timeoutMs div 1000) & " s"
  return Fetched(ok: false, finalUrl: url, error: "timed out after " & limit)

proc defaultFetcher*(): Fetcher =
  result = proc (url: string): Future[Fetched] {.closure, gcsafe.} = fetch(url)
