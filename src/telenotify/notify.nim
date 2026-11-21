## Notifications through your own Telegram bot (#122).
##
## Messages in Saved Messages never notify you (you wrote them yourself), so
## the bot can also tell you through a bot of your own: make one with
## @BotFather, press Start in its chat, and put its token in config.json as
## `notify_bot_token`. You then get a normal Telegram notification, on your
## phone and your laptop, when a post goes to the review queue, when one is
## published to your channel, and for every alert.
##
## The message goes to your own user id (your private chat with the bot),
## through the Bot API's `sendMessage`. The token is part of the URL, so it
## is kept out of every error and log line.
##
## Sending never holds the bot up: messages wait in a small queue
## (`NotifyQueue`) that one background loop sends, each with a 5 s time
## limit. Your user id is looked up once, and kept in the state database, so
## the log-out alert still reaches you when Telegram can't tell it any more.

import std/[asyncdispatch, deques, json, strutils, uri, unicode]
import fetch, state

const
  telegramLimit = 4096   ## Telegram's limit for one message, in UTF-16 units
  maxText* = 1000        ## ours: these messages are short, and so are the URLs
  sendTimeoutMs* = 5000  ## one send's time limit
  chatIdKey* = "notify_chat_id"   ## your user id, in the state's meta table

type
  Notifier* = proc (text: string): Future[string] {.closure, gcsafe.}
    ## Send `text` to you; "" when sent, else why not (without the token).
  ChatId* = proc (): Future[int64] {.closure, gcsafe.}
    ## Your own user id: the chat with your bot.
  LogProc* = proc (line: string) {.closure, gcsafe.}

proc units(r: Rune): int = (if r.int > 0xFFFF: 2 else: 1)

proc clip*(text: string, maxUnits = maxText): string =
  ## At most `maxUnits` UTF-16 units (Telegram's way of counting: an emoji
  ## outside the basic plane counts twice), ending in "…" when cut.
  let limit = min(maxUnits, telegramLimit)
  var total = 0
  for r in text.runes: total += r.units
  if total <= limit: return text
  var used = 1   # the "…"
  for r in text.runes:
    if used + r.units > limit: break
    used += r.units
    result.add r
  result.add "…"

proc hide(s, token: string): string =
  if token == "": s else: s.replace(token, "<token>")

proc validToken*(token: string): bool =
  ## A Bot API token looks like `123456:AAH...`: digits, a colon, then
  ## letters, digits, `_` or `-`.
  let i = token.find(':')
  if i <= 0 or i == token.high: return false
  for c in token[0 ..< i]:
    if c notin Digits: return false
  for c in token[i + 1 .. ^1]:
    if c notin IdentChars + {'-'}: return false
  true

proc reason(r: Fetched): string =
  ## Why a send failed: the status and Telegram's own description.
  result = if r.status != 0: "HTTP " & $r.status else: r.error
  try:
    let d = parseJson(r.body){"description"}.getStr
    if d != "": result.add ": " & d
  except CatchableError: discard
  if result == "": result = "unknown error"

proc botNotifier*(token: string, chatId: ChatId, fetcher: Fetcher): Notifier =
  ## Sends through the Bot API. Your user id is asked once, after that it's
  ## kept. Never raises.
  var chat = 0'i64
  result = proc (text: string): Future[string] {.async, closure, gcsafe.} =
    try:
      if chat == 0: chat = await chatId()
      let url = "https://api.telegram.org/bot" & token & "/sendMessage?" &
        encodeQuery({"chat_id": $chat, "text": clip(text), "disable_web_page_preview": "true"})
      let r = await fetcher(url)
      if r.ok: return ""
      return hide(reason(r), token)
    except CatchableError as e:
      return hide(e.msg.splitLines()[0], token)

proc botFetcher*(allowPrivate = false): Fetcher =
  ## Downloads with the 5 s time limit. (A slow name lookup can't be cut
  ## short: see fetch.nim.)
  result = proc (url: string): Future[Fetched] {.closure, gcsafe.} =
    fetch(url, timeoutMs = sendTimeoutMs, allowPrivate = allowPrivate)

proc notifierFor*(token: string, chatId: ChatId, fetcher: Fetcher, log: LogProc): Notifier =
  ## The notifier for `notify_bot_token`, or nil: no token, or one that
  ## can't be a bot token (said once, without the token).
  if token == "": return nil
  if not token.validToken:
    log "⚠️  notify_bot_token in config.json doesn't look like a bot token " &
        "(digits, a colon, then letters, as @BotFather gives it): notifications are off"
    return nil
  botNotifier(token, chatId, fetcher)

proc storedChatId*(st: State, lookup: ChatId, timeoutMs = sendTimeoutMs): ChatId =
  ## Your user id from Telegram (`lookup`, TDLib's getMe), kept in the state
  ## database; the kept one when Telegram can't give it within `timeoutMs`
  ## (logged out, or TDLib not started yet). `lookup` may be nil.
  result = proc (): Future[int64] {.async, closure, gcsafe.} =
    var why = "Telegram isn't connected yet"
    if lookup != nil:
      try:
        let asking = lookup()
        if not await withTimeout(asking, timeoutMs):
          raise newException(IOError, "Telegram didn't answer")
        let id = asking.read
        if id != 0:
          try:
            if st.getMeta(chatIdKey) != $id: st.setMeta(chatIdKey, $id)
          except CatchableError: discard
          return id
      except CatchableError as e:
        why = e.msg.splitLines()[0]
    var kept = ""
    try: kept = st.getMeta(chatIdKey)
    except CatchableError: discard
    if kept != "":
      try: return parseBiggestInt(kept)
      except ValueError: discard
    raise newException(IOError, "your user id isn't known yet (" & why & ")")

# ------------------------------------------------- sending in the background

type
  Later* = proc (): Future[string] {.closure, gcsafe.}
    ## A message still to be written (it waits for a post's link), in the
    ## background. Never raises.
  NotifyQueue* = ref object
    ## Messages waiting to be sent, oldest first, at most `cap`; one loop
    ## sends them. A full queue drops its oldest message.
    send: Notifier
    log: LogProc
    cap: int
    pending: Deque[Later]
    sending: bool
    dropped: bool        # already said so, until the queue empties

proc newNotifyQueue*(send: Notifier, log: LogProc, cap = 50): NotifyQueue =
  NotifyQueue(send: send, log: log, cap: max(cap, 1), pending: initDeque[Later]())

proc idle*(q: NotifyQueue): bool =
  ## Nothing waiting and nothing being sent.
  q.pending.len == 0 and not q.sending

proc drain(q: NotifyQueue) {.async.} =
  q.sending = true
  try:
    while q.pending.len > 0:
      let later = q.pending.popFirst
      var err = ""
      try: err = await q.send(await later())
      except CatchableError as e: err = e.msg.splitLines()[0]
      if err != "": q.log "   (could not notify you through your bot: " & err & ")"
  finally:
    q.sending = false
    q.dropped = false

proc pushLater*(q: NotifyQueue, later: Later) =
  ## Queue a message written when its turn comes, and return at once.
  if q.pending.len >= q.cap:
    discard q.pending.popFirst
    if not q.dropped:
      q.dropped = true
      q.log "   (notifications are piling up: the oldest ones are being dropped)"
  q.pending.addLast later
  if not q.sending: asyncCheck q.drain()

proc push*(q: NotifyQueue, text: string) =
  ## Queue `text` and return at once.
  q.pushLater proc (): Future[string] {.async, closure, gcsafe.} = return text

proc flush*(q: NotifyQueue, timeoutMs = 10_000) {.async.} =
  ## Wait until everything queued is sent, at most `timeoutMs`.
  var waited = 0
  while not q.idle and waited < timeoutMs:
    await sleepAsync(20)
    waited += 20
