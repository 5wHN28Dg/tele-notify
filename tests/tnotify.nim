## Notifications through your own Telegram bot (#122): the Bot API request.

import std/[unittest, asyncdispatch, strutils, uri, unicode]
import telenotify/[notify, fetch]

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
