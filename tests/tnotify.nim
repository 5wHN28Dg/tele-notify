## Notifications through your own Telegram bot (#122): the Bot API request.

import std/[unittest, asyncdispatch, strutils, uri, unicode, times, os, asyncnet, net, sequtils]
import telenotify/[notify, fetch, state]

const notifyPaceDefault = 1000
doAssert notifyPaceMs == notifyPaceDefault    # the bot paces messages to about one a second (#127)
notifyPaceMs = 0                 # but these tests don't wait for it, apart from the pacing test

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

suite "failures, retries and the queue (review run 2)":
  test "a bot that can't reach you (401, 403) is one alert for the screen, again only after a send worked":
    let dir = getTempDir() / "tn-notify-unreachable"
    removeDir(dir)
    let st = openState(dir / "state.sqlite3")
    defer: st.close()
    var status = 403
    let fake: Fetcher = proc (url: string): Future[Fetched] {.async, closure, gcsafe.} =
      {.cast(gcsafe).}:
        if status == 200: return Fetched(ok: true, status: 200, body: """{"ok":true}""")
        return Fetched(ok: false, status: status, error: "HTTP " & $status,
          body: """{"ok":false,"description":"Forbidden: bot can't initiate conversation with a user"}""")
    let n = botNotifier("1:t", fixedId(42), fake, st)
    for _ in 1 .. 3: check waitFor(n("x")) != ""
    check st.alertTexts() == @[unreachableAlert]
    status = 401
    discard waitFor n("x")
    check st.alertTexts().len == 1
    status = 200
    check waitFor(n("x")) == ""
    status = 403
    discard waitFor n("x")
    check st.alertTexts().len == 2
    check "press Start" in unreachableAlert and "notify_bot_token" in unreachableAlert

  test "429: tried once more after retry_after, capped (review run 2)":
    var calls = 0
    var always = false
    let fake: Fetcher = proc (url: string): Future[Fetched] {.async, closure, gcsafe.} =
      {.cast(gcsafe).}:
        inc calls
        if calls == 1 or always:
          return Fetched(ok: false, status: 429, error: "HTTP 429",
            body: """{"ok":false,"description":"Too Many Requests: retry after 100","parameters":{"retry_after":100}}""")
      return Fetched(ok: true, status: 200)
    let n = botNotifier("1:t", fixedId(42), fake, retryCapMs = 300)
    var t0 = epochTime()
    check waitFor(n("x")) == ""
    check calls == 2 and epochTime() - t0 >= 0.25 and epochTime() - t0 < 2
    always = true
    calls = 0
    check "429" in waitFor(n("x"))
    check calls == 2                                     # once more, not forever

  test "the cap on retry_after is 30 s":
    check maxRetryAfterMs == 30_000

  test "an urgent message goes to the front of the queue (review run 2)":
    var sent: seq[string]
    let slow: Notifier = proc (text: string): Future[string] {.async, closure, gcsafe.} =
      await sleepAsync(50)
      {.cast(gcsafe).}: sent.add text
      return ""
    let q = newNotifyQueue(slow, proc (line: string) {.closure, gcsafe.} = discard)
    for t in ["1", "2", "3"]: q.push t
    q.pushFirst "logged out"
    q.waitIdle()
    check sent == @["1", "logged out", "2", "3"]       # "1" was already being sent

  test "a flush that gives up says how many messages are left (review run 2)":
    var lines: seq[string]
    let log = proc (line: string) {.closure, gcsafe.} =
      {.cast(gcsafe).}: lines.add line
    let slow: Notifier = proc (text: string): Future[string] {.async, closure, gcsafe.} =
      await sleepAsync(300)
      return ""
    let q = newNotifyQueue(slow, log)
    for t in ["1", "2", "3"]: q.push t
    waitFor q.flush(100)
    check lines.len == 1 and "3" in lines[0] and "not sent" in lines[0]
    q.waitIdle()

  test "each burst past the cap says so once (review run 2)":
    var lines: seq[string]
    let log = proc (line: string) {.closure, gcsafe.} =
      {.cast(gcsafe).}: lines.add line
    let slow: Notifier = proc (text: string): Future[string] {.async, closure, gcsafe.} =
      await sleepAsync(20)
      return ""
    let q = newNotifyQueue(slow, log, cap = 2)
    for i in 1 .. 6: q.push $i
    q.waitIdle()
    for i in 1 .. 6: q.push $i
    q.waitIdle()
    check lines.len == 2 and lines.allIt("dropped" in it)

  test "a user id lookup that hangs gives up after 5 s and uses the kept id (review run 2)":
    let dir = getTempDir() / "tn-notify-id-hang"
    removeDir(dir)
    let st = openState(dir / "state.sqlite3")
    defer: st.close()
    st.setMeta("notify_chat_id", "42")
    let hang: ChatId = proc (): Future[int64] {.closure, gcsafe.} = newFuture[int64]("hangs")
    let t0 = epochTime()
    check waitFor(storedChatId(st, hang)()) == 42
    check epochTime() - t0 >= 4.5 and epochTime() - t0 < 7

  test "\"notifications are on\" once, again when your user id changes or the send failed (review run 2)":
    let dir = getTempDir() / "tn-notify-on"
    removeDir(dir)
    let st = openState(dir / "state.sqlite3")
    defer: st.close()
    var sent: seq[string]
    var err = "HTTP 403"
    let send: Notifier = proc (text: string): Future[string] {.async, closure, gcsafe.} =
      {.cast(gcsafe).}:
        sent.add text
        return err
    var id = 42'i64
    let who: ChatId = proc (): Future[int64] {.async, closure, gcsafe.} =
      {.cast(gcsafe).}: return id
    waitFor announceOn(st, send, who, dryRun = false)       # Start not pressed yet: failed
    err = ""
    waitFor announceOn(st, send, who, dryRun = false)
    waitFor announceOn(st, send, who, dryRun = false)       # a restart: already said
    check sent == @["🔔 tele-notify notifications are on", "🔔 tele-notify notifications are on"]
    id = 43                                                 # another account
    waitFor announceOn(st, send, who, dryRun = true)
    check sent.len == 3 and sent[2] == "🔔 tele-notify [dry run] notifications are on"


suite "notification reliability (#127)":
  proc counting(calls: ref int, answers: seq[Fetched]): Fetcher =
    result = proc (url: string): Future[Fetched] {.async, closure, gcsafe.} =
      {.cast(gcsafe).}:
        let i = min(calls[], answers.high)
        inc calls[]
        return answers[i]

  test "a timeout, a failed lookup or a 5xx is tried once more (MSG3)":
    let ok = Fetched(ok: true, status: 200)
    for first in [Fetched(status: 0, error: "timed out after 5000 ms"),
                  Fetched(status: 0, error: "cannot resolve api.telegram.org"),
                  Fetched(status: 502, error: "HTTP 502")]:
      let calls = new int
      let n = botNotifier("1:t", fixedId(42), counting(calls, @[first, ok]), transientWaitMs = 50)
      check waitFor(n("x")) == ""
      check calls[] == 2

  test "a send that keeps failing is tried twice, not forever, and says why":
    let calls = new int
    let n = botNotifier("1:t", fixedId(42), counting(calls, @[Fetched(status: 503, error: "HTTP 503")]),
                        transientWaitMs = 50)
    check "503" in waitFor(n("x"))
    check calls[] == 2

  test "a 400 is not tried again (the message itself is wrong)":
    let calls = new int
    let n = botNotifier("1:t", fixedId(42), counting(calls, @[Fetched(status: 400, error: "HTTP 400",
      body: """{"ok":false,"description":"Bad Request: message text is empty"}""")]), transientWaitMs = 50)
    check "400" in waitFor(n("x"))
    check calls[] == 1

  test "\"chat not found\" (Start never pressed) and a bare 401 are the can't-reach-you alert":
    let dir = getTempDir() / "tn-notify-reach"
    removeDir(dir)
    createDir(dir)
    for (name, answer, alerted) in [
        ("notfound", Fetched(status: 400, error: "HTTP 400",
                             body: """{"ok":false,"description":"Bad Request: chat not found"}"""), true),
        ("401", Fetched(status: 401, error: "HTTP 401",
                        body: """{"ok":false,"description":"Unauthorized"}"""), true),
        ("empty", Fetched(status: 400, error: "HTTP 400",
                          body: """{"ok":false,"description":"Bad Request: message text is empty"}"""), false)]:
      let st = openState(dir / name & ".sqlite3")
      let calls = new int
      discard waitFor botNotifier("1:t", fixedId(42), counting(calls, @[answer]), st)("x")
      check (st.alertTexts() == @[unreachableAlert]) == alerted
      st.close()

  test "a full queue makes room for an urgent message by dropping the newest, not the front":
    var sent: seq[string]
    let slow: Notifier = proc (text: string): Future[string] {.async, closure, gcsafe.} =
      await sleepAsync(50)
      {.cast(gcsafe).}: sent.add text
      return ""
    let q = newNotifyQueue(slow, proc (line: string) {.closure, gcsafe.} = discard, cap = 3)
    for t in ["1", "2", "3", "4"]: q.push t          # "1" is being sent; 2, 3, 4 wait
    q.pushFirst "logged out"
    q.waitIdle()
    check sent == @["1", "logged out", "2", "3"]

  test "messages go out at most one per gap":
    var at: seq[float]
    let quick: Notifier = proc (text: string): Future[string] {.async, closure, gcsafe.} =
      {.cast(gcsafe).}: at.add epochTime()
      return ""
    let q = newNotifyQueue(quick, proc (line: string) {.closure, gcsafe.} = discard, gapMs = 300)
    for t in ["1", "2", "3"]: q.push t
    q.waitIdle()
    check at.len == 3
    check at[1] - at[0] >= 0.28 and at[2] - at[1] >= 0.28
    check at[2] - at[0] < 1.5

  test "the queue's gap is notifyPaceMs unless given":
    let saved = notifyPaceMs
    notifyPaceMs = 250
    var at: seq[float]
    let quick: Notifier = proc (text: string): Future[string] {.async, closure, gcsafe.} =
      {.cast(gcsafe).}: at.add epochTime()
      return ""
    let q = newNotifyQueue(quick, proc (line: string) {.closure, gcsafe.} = discard)
    notifyPaceMs = saved
    q.push "1"
    q.push "2"
    q.waitIdle()
    check at[1] - at[0] >= 0.23

  test "\"notifications are on\" isn't sent when every kind is off":
    let dir = getTempDir() / "tn-notify-allof"
    removeDir(dir)
    let st = openState(dir / "state.sqlite3")
    defer: st.close()
    var sent: seq[string]
    let send: Notifier = proc (text: string): Future[string] {.async, closure, gcsafe.} =
      {.cast(gcsafe).}: sent.add text
      return ""
    waitFor announceOn(st, send, fixedId(42), dryRun = false, anyOn = false)
    check sent.len == 0
    waitFor announceOn(st, send, fixedId(42), dryRun = false, anyOn = true)
    check sent.len == 1

  test "the waits at a stop fit what they wait for (under systemd's 90 s)":
    check oneSendMs == sendTimeoutMs + transientRetryMs + sendTimeoutMs        # a send tried twice
    check stopFlushMs >= oneSendMs
    let linkMs = sendTimeoutMs                     # tellWithLink asks TDLib for the post's link first
    let inFlight = linkMs + sendTimeoutMs + maxRetryAfterMs + sendTimeoutMs   # a send waiting out a 429
    check logOutFlushMs >= inFlight + notifyPaceDefault + oneSendMs
    check logOutFlushMs < 90_000

  test "\"notifications are on\" follows whether any kind is on":
    check anyNotifications(true, false, false) and anyNotifications(false, true, false)
    check anyNotifications(false, false, true)
    check not anyNotifications(false, false, false)
