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
## limit; api.telegram.org's address is looked up at most every 10 minutes.
## Your user id is looked up once, and kept in the state database, so the
## log-out alert still reaches you when Telegram can't tell it any more.
##
## A 429 (too many messages) is tried once more after Telegram's
## `retry_after` (at most 30 s); a timeout, a lost connection, a failed name
## lookup or a 5xx (Telegram's own trouble) once more after 2 s. A 401 or 403
## (wrong token), or a 400 "chat not found" (Start not pressed), is one alert
## for the terminal screen, repeated only after a send has worked again.
## Messages go out at most about one a second (Telegram's Bot FAQ asks that
## per chat).

import std/[asyncdispatch, deques, json, strutils, strformat, uri, unicode, times]
import fetch, state

const
  telegramLimit = 4096   ## Telegram's limit for one message, in UTF-16 units
  maxText* = 1000        ## ours: these messages are short, and so are the URLs
  sendTimeoutMs* = 5000  ## one send's time limit
  chatIdKey* = "notify_chat_id"   ## your user id, in the state's meta table
  keepBotAddressMs* = 600_000     ## api.telegram.org's address is looked up this often at most
  maxRetryAfterMs* = 30_000       ## the longest wait after a 429
  transientRetryMs* = 2000        ## the wait before trying a timed-out or 5xx send again
  logOutFlushMs* = maxRetryAfterMs + 4 * sendTimeoutMs + transientRetryMs + 1000
    ## at a stop after a log-out: long enough for a send waiting out a 429, then the alert
  unreachableKey = "notify_unreachable"
  onKey = "notify_on_for"         ## the user id told "notifications are on"
  unreachableAlert* = "Your notification bot can't reach you: press Start in its chat, " &
                      "or check notify_bot_token"

var notifyPaceMs* = 1000
  ## the least time between two messages to you (tests set 0)

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

proc retryAfterMs(r: Fetched, cap: int): int =
  ## How long Telegram asks to wait after a 429 (`parameters.retry_after`,
  ## seconds), at most `cap`.
  result = 1000
  try:
    let s = parseJson(r.body){"parameters", "retry_after"}.getInt(1)
    result = max(s, 0) * 1000
  except CatchableError: discard
  result = min(result, cap)

proc cantReach(r: Fetched): bool =
  ## Telegram says this bot can't message you: a wrong token (401), you
  ## blocked it (403), or you never pressed Start (often 400 "chat not found").
  r.status in [401, 403] or (r.status == 400 and "chat not found" in reason(r).toLowerAscii)

proc botNotifier*(token: string, chatId: ChatId, fetcher: Fetcher, st: State = nil,
                  retryCapMs = maxRetryAfterMs, transientWaitMs = transientRetryMs): Notifier =
  ## Sends through the Bot API. Your user id is asked once, after that it's
  ## kept. `st`: where a bot that can't reach you is recorded as an alert
  ## (nil: nowhere). Never raises.
  var chat = 0'i64
  result = proc (text: string): Future[string] {.async, closure, gcsafe.} =
    try:
      if chat == 0: chat = await chatId()
      let url = "https://api.telegram.org/bot" & token & "/sendMessage?" &
        encodeQuery({"chat_id": $chat, "text": clip(text), "disable_web_page_preview": "true"})
      var r = await fetcher(url)
      if r.status == 429:
        await sleepAsync(retryAfterMs(r, retryCapMs))
        r = await fetcher(url)
      elif r.status == 0 or r.status >= 500:
        # a timeout, a lost connection, a failed lookup or Telegram's own
        # trouble: often gone a moment later (MSG3)
        await sleepAsync(transientWaitMs)
        r = await fetcher(url)
      if r.ok:
        if st != nil:
          try:
            if st.getMeta(unreachableKey) != "": st.setMeta(unreachableKey, "")
          except CatchableError: discard
        return ""
      if r.cantReach and st != nil:
        try:
          if st.getMeta(unreachableKey) == "":
            st.addAlert(unreachableAlert)     # for the terminal screen
            st.setMeta(unreachableKey, "1")
        except CatchableError: discard
      return hide(reason(r), token)
    except CatchableError as e:
      return hide(e.msg.splitLines()[0], token)

proc botFetcher*(allowPrivate = false): Fetcher =
  ## Downloads with the 5 s time limit. (A slow name lookup can't be cut
  ## short: see fetch.nim.)
  result = proc (url: string): Future[Fetched] {.closure, gcsafe.} =
    fetch(url, timeoutMs = sendTimeoutMs, allowPrivate = allowPrivate, keepAddressMs = keepBotAddressMs)

proc notifierFor*(token: string, chatId: ChatId, fetcher: Fetcher, log: LogProc,
                  st: State = nil): Notifier =
  ## The notifier for `notify_bot_token`, or nil: no token, or one that
  ## can't be a bot token (said once, without the token).
  if token == "": return nil
  if not token.validToken:
    log "⚠️  notify_bot_token in config.json doesn't look like a bot token " &
        "(digits, a colon, then letters, as @BotFather gives it): notifications are off"
    return nil
  botNotifier(token, chatId, fetcher, st)

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
    gapMs: int           # the least time between two sends
    lastSend: float      # when the last send started (epochTime)

proc newNotifyQueue*(send: Notifier, log: LogProc, cap = 50, gapMs = -1): NotifyQueue =
  ## `gapMs`: the least time between two sends; -1: `notifyPaceMs`.
  NotifyQueue(send: send, log: log, cap: max(cap, 1), pending: initDeque[Later](),
              gapMs: (if gapMs < 0: notifyPaceMs else: gapMs))

proc idle*(q: NotifyQueue): bool =
  ## Nothing waiting and nothing being sent.
  q.pending.len == 0 and not q.sending

proc drain(q: NotifyQueue) {.async.} =
  q.sending = true
  try:
    while q.pending.len > 0:
      let later = q.pending.popFirst
      var err = ""
      try:
        let text = await later()
        let wait = q.lastSend + q.gapMs / 1000 - epochTime()
        if wait > 0: await sleepAsync(int(wait * 1000))
        q.lastSend = epochTime()
        err = await q.send(text)
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

proc pushFirst*(q: NotifyQueue, text: string) =
  ## Queue `text` before everything waiting (the log-out alert), and return
  ## at once.
  if q.pending.len >= q.cap:
    discard q.pending.popLast     # the newest goes: the front is what's urgent
    if not q.dropped:
      q.dropped = true
      q.log "   (notifications are piling up: the oldest ones are being dropped)"
  q.pending.addFirst proc (): Future[string] {.async, closure, gcsafe.} = return text
  if not q.sending: asyncCheck q.drain()

proc flush*(q: NotifyQueue, timeoutMs = 10_000) {.async.} =
  ## Wait until everything queued is sent, at most `timeoutMs`. Gives up
  ## with one log line saying how many weren't sent.
  var waited = 0
  while not q.idle and waited < timeoutMs:
    await sleepAsync(20)
    waited += 20
  if not q.idle:
    let left = q.pending.len + (if q.sending: 1 else: 0)
    q.log &"   ({left} notifications not sent: stopped waiting for your bot)"

proc announceOn*(st: State, send: Notifier, chatId: ChatId, dryRun: bool, anyOn = true) {.async.} =
  ## "Notifications are on", once: at the first start with a working token,
  ## and again when your user id changes (another account). Kept in the
  ## state database only once sent, so a failed send (Start not pressed yet)
  ## is tried at the next start. Never raises.
  if not anyOn: return     # every kind turned off: nothing will come
  try:
    let id = await chatId()
    if st.getMeta(onKey) == $id: return
    if await(send("🔔 tele-notify" & (if dryRun: " [dry run]" else: "") & " notifications are on")) == "":
      st.setMeta(onKey, $id)
  except CatchableError: discard
