## Downloading pages, safely: a small HTTP client of our own.
##
## Why not Nim's `std/httpclient`? Reviews of stage 2 broke it in ways it
## doesn't let us fix:
## * it resolves host names itself, so we can't check the address it really
##   connects to: `127.1`, `0x7f000001`, `localhost.` or a name like
##   `localtest.me` all reached the local machine past our checks;
## * a connection still being opened can't be cancelled, so a time limit
##   left a socket behind and the request could still go out later;
## * on a redirect it insisted on reading the whole old body (200 MB once).
##
## Here we hold the socket ourselves:
## 1. resolve the host name, and refuse if *any* address is local or
##    private (127.x, 10.x, 192.168.x, ::1, ::ffff:127.0.0.1, ...);
## 2. connect to that checked address (so a second DNS answer can't swap
##    it for a local one);
## 3. for https, make the TLS handshake itself check that the certificate
##    belongs to the host name (Nim's async TLS layer doesn't), so nothing
##    is sent to an impostor;
## 4. send one GET with `Connection: close`, read at most 3 MB, close.
## Each redirect is a new connection (up to 5). When the time limit fires
## we close the socket, which cancels whatever was in progress.
##
## Every failure is returned as `Fetched(ok: false, error: ...)`; nothing
## raises. Tests use `allowPrivate = true` to talk to their local server.
## Known limit: name lookups are synchronous (as in the standard library),
## so a very slow DNS server can pause the bot; the time limit can't cut
## a lookup short.

import std/[asyncdispatch, asyncnet, net, uri, strutils, nativesockets, openssl]

const
  userAgent* = "tele-notify (+https://github.com/5wHN28Dg/tele-notify)"
  maxBytes* = 3_000_000
  maxHeaderBytes = 64_000

type
  Fetched* = object
    ok*: bool
    status*: int
    finalUrl*: string
    body*: string
    error*: string     ## why it failed, for logs and verdict reasons

  Fetcher* = proc (url: string): Future[Fetched] {.closure, gcsafe.}
    ## Anything that can download a URL. Tests pass a fake one.

  FetchError = object of CatchableError

  Live = ref object
    ## What a running download is doing, so the time limit can stop it.
    sock: AsyncSocket
    current: string

# ------------------------------------------------------------ addresses

proc isPrivateV4(a: array[4, uint8]): bool =
  a[0] in [0'u8, 10, 127] or (a[0] == 172 and a[1] in 16'u8 .. 31'u8) or
    (a[0] == 192 and a[1] == 168) or (a[0] == 169 and a[1] == 254) or
    (a[0] == 100 and a[1] in 64'u8 .. 127'u8) or a[0] >= 224 or
    (a[0] == 198 and a[1] in 18'u8 .. 19'u8) or (a[0] == 192 and a[1] == 0 and a[2] == 0)

proc isPrivateIp*(ip: IpAddress): bool =
  ## Loopback, private, link-local, carrier-grade NAT, benchmarking,
  ## multicast, "this host" and unspecified addresses, including IPv4
  ## addresses wrapped in IPv6 (mapped, translated, NAT64, 6to4).
  case ip.family
  of IpAddressFamily.IPv4:
    isPrivateV4(ip.address_v4)
  of IpAddressFamily.IPv6:
    let a = ip.address_v6
    let at12 = [a[12], a[13], a[14], a[15]]
    var zeroUpTo8 = true
    for i in 0 .. 7:
      if a[i] != 0: zeroUpTo8 = false
    if zeroUpTo8 and a[8] == 0 and a[9] == 0 and
        ((a[10] == 0xFF and a[11] == 0xFF) or (a[10] == 0 and a[11] == 0)):
      return isPrivateV4(at12)   # ::ffff:a.b.c.d (mapped), ::a.b.c.d, :: and ::1
    if zeroUpTo8 and a[8] == 0xFF and a[9] == 0xFF and a[10] == 0 and a[11] == 0:
      return isPrivateV4(at12)   # ::ffff:0:a.b.c.d (translated)
    if a[0] == 0x00 and a[1] == 0x64 and a[2] == 0xFF and a[3] == 0x9B:
      return isPrivateV4(at12)   # 64:ff9b::a.b.c.d (NAT64)
    if a[0] == 0x20 and a[1] == 0x02:
      return isPrivateV4([a[2], a[3], a[4], a[5]])   # 2002:aabb:ccdd:: (6to4)
    (a[0] and 0xFE) == 0xFC or (a[0] == 0xFE and (a[1] and 0x80) == 0x80) or a[0] == 0xFF

proc resolve(host: string, port: Port, allowPrivate: bool): (string, Domain) =
  ## A checked address to connect to. Raises FetchError when refused.
  var info: ptr AddrInfo
  try:
    info = getAddrInfo(host, port, Domain.AF_UNSPEC)
  except OSError as e:
    raise newException(FetchError, "cannot resolve " & host & ": " & e.msg.splitLines[0])
  defer: freeAddrInfo(info)
  var first = ""
  var domain = Domain.AF_INET
  var it = info
  while it != nil:
    if it.ai_family.cint in [toInt(Domain.AF_INET), toInt(Domain.AF_INET6)]:
      let ip = parseIpAddress(getAddrString(it.ai_addr))
      if not allowPrivate and ip.isPrivateIp:
        raise newException(FetchError, "refused a private/local address (" & host & ")")
      if first == "":
        first = $ip
        domain = if ip.family == IpAddressFamily.IPv4: Domain.AF_INET else: Domain.AF_INET6
    it = it.ai_next
  if first == "": raise newException(FetchError, "no address for " & host)
  (first, domain)

# ------------------------------------------------------------ one request

proc closeQuietly(live: Live) =
  ## Close the current socket, if any. Closing a socket whose connect was
  ## still in progress can raise "Bad file descriptor"; that's fine here.
  if live.sock != nil:
    try: live.sock.close()
    except CatchableError: discard
    live.sock = nil

when defined(ssl):
  # Two OpenSSL calls the standard wrapper doesn't declare. They make the
  # TLS handshake itself fail when the certificate isn't for `host`, so
  # nothing (not even the path we ask for) is sent to an impostor.
  proc SSL_get0_param(ssl: SslPtr): pointer {.cdecl, dynlib: DLLSSLName, importc.}
  proc X509_VERIFY_PARAM_set1_host(param: pointer, name: cstring, namelen: csize_t): cint
    {.cdecl, dynlib: DLLSSLName, importc.}

proc checkCertificate(sock: AsyncSocket, host: string) =
  when defined(ssl):
    let cert = SSL_get_peer_certificate(sock.sslHandle)
    if cert == nil: raise newException(FetchError, "no TLS certificate from " & host)
    defer: X509_free(cert)
    if X509_check_host(cert, host.cstring, host.len.cint, 0, nil) != 1:
      raise newException(FetchError, "TLS certificate is not for " & host)

proc recvExactly(sock: AsyncSocket, n: int): Future[string] {.async.} =
  result = await sock.recv(n)
  while result.len < n:
    let more = await sock.recv(n - result.len)
    if more.len == 0: raise newException(FetchError, "connection closed early")
    result.add more

proc getOnce(live: Live, url: string, allowPrivate: bool): Future[(int, string, string)] {.async.} =
  ## One GET without following redirects: (status, location header, body).
  let u = parseUri(url)
  let scheme = u.scheme.toLowerAscii
  if scheme notin ["http", "https"]:
    raise newException(FetchError, "unsupported scheme '" & u.scheme & "'")
  let host = u.hostname.strip(chars = {'[', ']'})
  if host.len == 0: raise newException(FetchError, "no host in " & url)
  var port = if scheme == "https": 443 else: 80
  if u.port != "":
    try: port = parseInt(u.port)
    except ValueError: raise newException(FetchError, "bad port " & u.port)
    if port notin 1 .. 65535: raise newException(FetchError, "bad port " & u.port)
  let (ip, domain) = resolve(host, Port(port), allowPrivate)
  let sock = newAsyncSocket(domain, SockType.SOCK_STREAM, Protocol.IPPROTO_TCP, buffered = true)
  live.sock = sock
  await sock.connect(ip, Port(port))
  if scheme == "https":
    when defined(ssl):
      let ctx = newContext(verifyMode = CVerifyPeer)
      wrapConnectedSocket(ctx, sock, handshakeAsClient, host)
      if X509_VERIFY_PARAM_set1_host(SSL_get0_param(sock.sslHandle), host.cstring, host.len.csize_t) != 1:
        raise newException(FetchError, "cannot set TLS host name check")
    else:
      raise newException(FetchError, "built without -d:ssl")
  var path = (if u.path == "": "/" else: u.path)
  if u.query != "": path.add "?" & u.query
  let hostHeader = (if ':' in host: "[" & host & "]" else: host) &
                   (if u.port != "": ":" & u.port else: "")
  await sock.send("GET " & path & " HTTP/1.1\r\nHost: " & hostHeader &
                  "\r\nUser-Agent: " & userAgent &
                  "\r\nAccept: */*\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n")
  if scheme == "https": checkCertificate(sock, host)   # second, belt-and-braces check

  # status line and headers
  let statusLine = await sock.recvLine(maxLength = maxHeaderBytes)
  let parts = statusLine.splitWhitespace
  if parts.len < 2 or not parts[0].startsWith("HTTP/"):
    raise newException(FetchError, "not an HTTP answer")
  var status = 0
  try: status = parseInt(parts[1])
  except ValueError: raise newException(FetchError, "bad HTTP status")
  var location, transfer = ""
  var length = -1
  var headerBytes = statusLine.len
  while true:
    let line = await sock.recvLine(maxLength = maxHeaderBytes)
    headerBytes += line.len
    if headerBytes > maxHeaderBytes: raise newException(FetchError, "headers too large")
    if line == "\r\n" or line == "": break
    let i = line.find(':')
    if i <= 0: continue
    let (k, v) = (line[0 ..< i].strip.toLowerAscii, line[i + 1 .. ^1].strip)
    case k
    of "location": location = v
    of "transfer-encoding": transfer = v.toLowerAscii
    of "content-length":
      try: length = parseInt(v)
      except ValueError: discard
    else: discard
  if status in 300 .. 399 and location != "":
    return (status, location, "")       # don't read a redirect's body at all
  if length > maxBytes: raise newException(FetchError, "page too large")

  # body: chunked, fixed length, or until the server closes
  var body = ""
  if "chunked" in transfer:
    while true:
      let sizeLine = (await sock.recvLine(maxLength = 100)).strip.split(';')[0].strip
      # At most 8 hex digits (plenty for 3 MB): parseHexInt silently wraps
      # around on longer input, which crashed us or broke the framing.
      if sizeLine.len == 0 or sizeLine.len > 8 or not sizeLine.allCharsInSet(HexDigits):
        raise newException(FetchError, "bad chunk size")
      let size = parseHexInt(sizeLine)
      if size == 0: break
      if size > maxBytes - body.len: raise newException(FetchError, "page too large")
      body.add await sock.recvExactly(size)
      discard await sock.recvLine(maxLength = 10)
  elif length >= 0:
    body = await sock.recvExactly(length)
  else:
    while true:
      let chunk = await sock.recv(65_536)
      if chunk.len == 0: break
      body.add chunk
      if body.len > maxBytes: raise newException(FetchError, "page too large")
  return (status, "", body)

proc fetchWith(live: Live, url: string, allowPrivate: bool): Future[Fetched] {.async.} =
  live.current = url
  try:
    for hop in 0 .. 5:
      let (status, location, body) = await getOnce(live, live.current, allowPrivate)
      live.closeQuietly()
      if location != "":
        live.current = $(parseUri(live.current).combine(parseUri(location)))
        continue
      if status != 200:
        return Fetched(ok: false, status: status, finalUrl: live.current, body: body,
                       error: "HTTP " & $status)
      return Fetched(ok: true, status: status, finalUrl: live.current, body: body)
    return Fetched(ok: false, finalUrl: live.current, error: "too many redirects")
  except CatchableError as e:
    live.closeQuietly()
    return Fetched(ok: false, finalUrl: live.current, error: e.msg.splitLines[0])

proc fetch*(url: string, timeoutMs = 20_000, allowPrivate = false): Future[Fetched] {.async.} =
  ## Download `url`. Never raises; check `ok`.
  let live = Live()
  let fut = fetchWith(live, url, allowPrivate)
  if await withTimeout(fut, timeoutMs):
    return fut.read
  live.closeQuietly()   # cancels a connect or read in progress
  let limit = if timeoutMs mod 1000 != 0: $timeoutMs & " ms" else: $(timeoutMs div 1000) & " s"
  return Fetched(ok: false, finalUrl: live.current, error: "timed out after " & limit)

proc defaultFetcher*(): Fetcher =
  result = proc (url: string): Future[Fetched] {.closure, gcsafe.} = fetch(url)
