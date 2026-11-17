## Downloading pages, safely.
##
## Rules learned from the old version (problems report 4.1, 5.3):
## * a download can never crash the caller: every failure becomes a
##   `Fetched` with `ok = false` and a reason;
## * every download has a time limit (20 s by default), so a dead site can't
##   stall the bot for minutes;
## * pages are capped at 3 MB.
##
## Redirects are followed by hand (up to 5) so we know the *final* URL;
## lnkd.in short links need it. Uses Nim's standard async HTTP client,
## which needs `-d:ssl` (set in config.nims).

import std/[asyncdispatch, httpclient, uri, strutils]

const
  userAgent* = "tele-notify (+https://github.com/5wHN28Dg/tele-notify)"
  maxBytes = 3_000_000

type
  Fetched* = object
    ok*: bool
    status*: int
    finalUrl*: string
    body*: string
    error*: string     ## why it failed, for logs and verdict reasons

  Fetcher* = proc (url: string): Future[Fetched] {.closure, gcsafe.}
    ## Anything that can download a URL. Tests pass a fake one.

proc fetchOnce(url: string): Future[Fetched] {.async.} =
  var client = newAsyncHttpClient(userAgent = userAgent, maxRedirects = 0)
  try:
    var current = url
    for hop in 0 .. 5:
      let resp = await client.get(current)
      let code = resp.code.int
      if code in 300 .. 399 and resp.headers.hasKey("location"):
        current = $(parseUri(current).combine(parseUri(resp.headers["location"])))
        discard await resp.body   # drain before reusing the connection
        continue
      let body = await resp.body
      if body.len > maxBytes:
        return Fetched(ok: false, status: code, finalUrl: current, error: "page too large")
      if code != 200:
        return Fetched(ok: false, status: code, finalUrl: current, body: body,
                       error: "HTTP " & $code)
      return Fetched(ok: true, status: code, finalUrl: current, body: body)
    return Fetched(ok: false, finalUrl: current, error: "too many redirects")
  except CatchableError as e:
    return Fetched(ok: false, finalUrl: url, error: e.msg.splitLines[0])
  finally:
    client.close()

proc fetch*(url: string, timeoutMs = 20_000): Future[Fetched] {.async.} =
  ## Download `url`. Never raises; check `ok`.
  let fut = fetchOnce(url)
  if await withTimeout(fut, timeoutMs):
    return fut.read
  return Fetched(ok: false, finalUrl: url, error: "timed out after " & $(timeoutMs div 1000) & " s")

proc defaultFetcher*(): Fetcher =
  result = proc (url: string): Future[Fetched] {.closure, gcsafe.} = fetch(url)
