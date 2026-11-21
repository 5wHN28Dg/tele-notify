## Notifications through your own Telegram bot (#122): the Bot API request.

import std/[unittest, asyncdispatch, strutils, uri, unicode, times, os, asyncnet, net, sequtils]
import telenotify/[notify, fetch, state]

proc utf16Len(s: string): int =
  ## How Telegram counts a message's length.
  for r in s.runes: result += (if r.int > 0xFFFF: 2 else: 1)

proc textOf(url: string): string =
  for (k, v) in decodeQuery(parseUri(url).query):
    if k == "text": return v

proc fixedId(id: int64): ChatId =
  result = proc (): Future[int64] {.async, closure, gcsafe.} = return id

proc okFetcher(urls: ref seq[string]): Fetcher =
  result = proc (url: string): Future[Fetched] {.async, closure, gcsafe.} =
    {.cast(gcsafe).}: urls[].add url
    return Fetched(ok: true, status: 200, body: """{"ok":true}""")

proc waitIdle(q: NotifyQueue, ms = 5000) =
  let t0 = epochTime()
  while not q.idle and epochTime() - t0 < ms / 1000: poll(10)

suite "bot notifier":
  test "sends one sendMessage GET to your chat, the text encoded":
    var urls: seq[string]
    let fake: Fetcher = proc (url: string): Future[Fetched] {.async, closure, gcsafe.} =
      {.cast(gcsafe).}: urls.add url
      return Fetched(ok: true, status: 200, body: """{"ok":true}""")
    let n = botNotifier("123:ABC", proc (): Future[int64] {.async, closure, gcsafe.} = return 42, fake)
    check waitFor(n("✅ Published: مهندس & co\nhttps://t.me/c/1/10")) == ""
    check urls.len == 1
    let u = parseUri(urls[0])
    check u.hostname == "api.telegram.org" and u.path == "/bot123:ABC/sendMessage"
    var chat, text = ""
    for (k, v) in decodeQuery(u.query):
      if k == "chat_id": chat = v
      if k == "text": text = v
    check chat == "42" and text == "✅ Published: مهندس & co\nhttps://t.me/c/1/10"

  test "a failure is returned without the token":
    let fake: Fetcher = proc (url: string): Future[Fetched] {.async, closure, gcsafe.} =
      return Fetched(ok: false, status: 401, finalUrl: url, error: "HTTP 401 at " & url)
    let n = botNotifier("123:SECRET", proc (): Future[int64] {.async, closure, gcsafe.} = return 42, fake)
    let err = waitFor n("x")
    check err != "" and "SECRET" notin err and "HTTP 401" in err

  test "a long text is cut to Telegram's limit":
    var got = ""
    let fake: Fetcher = proc (url: string): Future[Fetched] {.async, closure, gcsafe.} =
      {.cast(gcsafe).}:
        for (k, v) in decodeQuery(parseUri(url).query):
          if k == "text": got = v
      return Fetched(ok: true, status: 200)
    let n = botNotifier("t", proc (): Future[int64] {.async, closure, gcsafe.} = return 1, fake)
    discard waitFor n("ب".repeat(5000))
    check got.runeLen <= 4096 and got.endsWith("…")

  test "the text is clipped in Telegram's units (UTF-16): an emoji counts twice (review run 1)":
    let urls = new seq[string]
    let n = botNotifier("1:t", fixedId(1), okFetcher(urls))
    discard waitFor n("😀".repeat(3000))            # 3000 characters, 6000 UTF-16 units
    let got = textOf(urls[0])
    check got.utf16Len <= 1000 and got.endsWith("…")   # short messages: short URLs
    check got.validateUtf8 == -1
    let big = clip("😀".repeat(3000), 4096)
    check big.utf16Len <= 4096 and big.utf16Len > 4000 and big.endsWith("…")
    check clip("short 😀") == "short 😀"

  test "a failed send says Telegram's reason, without the token (review run 1)":
    let fake: Fetcher = proc (url: string): Future[Fetched] {.async, closure, gcsafe.} =
      return Fetched(ok: false, status: 403, error: "HTTP 403",
        body: """{"ok":false,"error_code":403,"description":"Forbidden: bot was blocked by the user (123:SECRET)"}""")
    let n = botNotifier("123:SECRET", fixedId(42), fake)
    let err = waitFor n("x")
    check "bot was blocked by the user" in err and "SECRET" notin err and "403" in err

  test "your user id is looked up once (review run 1)":
    var lookups = 0
    let id: ChatId = proc (): Future[int64] {.async, closure, gcsafe.} =
      {.cast(gcsafe).}: inc lookups
      return 42
    let urls = new seq[string]
    let n = botNotifier("1:t", id, okFetcher(urls))
    for i in 1 .. 3: check waitFor(n("x" & $i)) == ""
    check lookups == 1 and urls[].len == 3

  test "a malformed token turns notifications off with one clear line, the token left out (review run 1)":
    var lines: seq[string]
    let log = proc (line: string) {.closure, gcsafe.} =
      {.cast(gcsafe).}: lines.add line
    let urls = new seq[string]
    check notifierFor("not a token SECRET", fixedId(1), okFetcher(urls), log) == nil
    check lines.len == 1 and "SECRET" notin lines[0] and "notify_bot_token" in lines[0]
    lines.setLen 0
    check notifierFor("", fixedId(1), okFetcher(urls), log) == nil and lines.len == 0
    check notifierFor("123456:AAH-x_y9", fixedId(1), okFetcher(urls), log) != nil and lines.len == 0
    check not validToken("abc:def") and not validToken("123:") and not validToken("123") and
          not validToken("123:a/b") and validToken("123456:AAH-x_y9")

  test "your user id is kept, and used when Telegram can't give it (logged out) (review run 1)":
    let dir = getTempDir() / "tn-notify-id"
    removeDir(dir)
    let st = openState(dir / "state.sqlite3")
    defer: st.close()
    var up = true
    let lookup: ChatId = proc (): Future[int64] {.async, closure, gcsafe.} =
      {.cast(gcsafe).}:
        if not up: raise newException(IOError, "Unauthorized")
      return 42
    check waitFor(storedChatId(st, lookup)()) == 42
    check st.getMeta("notify_chat_id") == "42"
    up = false
    check waitFor(storedChatId(st, lookup)()) == 42          # a new run, logged out
    check waitFor(storedChatId(st, nil)()) == 42             # before TDLib is there
    st.setMeta("notify_chat_id", "")
    expect CatchableError: discard waitFor(storedChatId(st, lookup)())

  test "sending happens in the background, oldest dropped past the cap (review run 1)":
    var lines, sent: seq[string]
    let log = proc (line: string) {.closure, gcsafe.} =
      {.cast(gcsafe).}: lines.add line
    let slow: Notifier = proc (text: string): Future[string] {.async, closure, gcsafe.} =
      await sleepAsync(200)
      {.cast(gcsafe).}: sent.add text
      return ""
    let q = newNotifyQueue(slow, log, cap = 3)
    let t0 = epochTime()
    for i in 1 .. 6: q.push $i
    check epochTime() - t0 < 0.1               # push never waits for the send
    q.waitIdle()
    check q.idle
    # "1" was already being sent; of the rest only the newest 3 were kept
    check sent == @["1", "4", "5", "6"]
    check lines.len == 1 and "dropped" in lines[0]

  test "a failed send is logged and the queue goes on (review run 1)":
    var lines: seq[string]
    let log = proc (line: string) {.closure, gcsafe.} =
      {.cast(gcsafe).}: lines.add line
    var sent = 0
    let bad: Notifier = proc (text: string): Future[string] {.async, closure, gcsafe.} =
      {.cast(gcsafe).}: inc sent
      return "HTTP 401: Unauthorized"
    let q = newNotifyQueue(bad, log)
    q.push "a"
    q.push "b"
    q.waitIdle()
    check sent == 2 and lines.len == 2 and lines.allIt("could not notify" in it and "Unauthorized" in it)

  test "a send has a 5 s time limit (review run 1)":
    # a local server that accepts and never answers
    let silent = newAsyncSocket()
    silent.setSockOpt(OptReuseAddr, true)
    silent.bindAddr(Port(0), "127.0.0.1")
    silent.listen()
    let port = silent.getLocalAddr()[1].uint16
    var held: seq[AsyncSocket]
    asyncCheck (proc () {.async.} =
      while true:
        let c = await silent.accept()
        {.cast(gcsafe).}: held.add c)()
    let t0 = epochTime()
    let r = waitFor botFetcher(allowPrivate = true)("http://127.0.0.1:" & $port & "/x")
    check not r.ok and r.error == "timed out after 5 s"
    check epochTime() - t0 < 7
