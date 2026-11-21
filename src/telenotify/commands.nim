## The commands that talk to Telegram: login, chats, run, review, stats.

import std/[asyncdispatch, json, os, strutils, strformat, tables, times, posix, unicode]
import config, td, session, telegram, state, listener, fetch, gather, compare, notify, instagram

var stopRequested = new bool   # set by Ctrl-C or `systemctl stop` (SIGTERM)

proc onSignal(sig: cint) {.noconv.} = stopRequested[] = true

proc sessionEnded*(authState: string, stopping: bool): bool =
  ## Does this authorization update mean the login is gone? Not when we
  ## are closing TDLib ourselves (a normal stop goes through Closing and
  ## Closed too).
  authState != "" and authState != "authorizationStateReady" and not stopping

proc isLogOut*(authState: string): bool

proc loginAlert*(why: string, dryRun: bool): string =
  ## The cause first (a phone's lock screen shows only the start), then the
  ## commands, in plain text (no markup: it's shown as typed). `why` is "not
  ## logged in" (the bot started without a login) or a TDLib authorization state.
  let service = if dryRun: "tele-notify-dryrun" else: "tele-notify"
  let cause =
    if why == "not logged in": "Not logged in to Telegram. To log in"
    elif why.isLogOut: "Logged out of Telegram. To log in again"
    else: "Telegram ended this session. To log in again"
  # stopped first: `telenotify login` can't share TDLib's folder with the
  # bot that systemd keeps restarting
  &"{cause}: systemctl --user stop {service}; telenotify login; systemctl --user start {service}"

proc recordLoginNeeded*(st: State, why: string, dryRun: bool,
                        tell: proc (text: string) {.closure, gcsafe.} = nil): bool {.discardable.} =
  ## The account isn't logged in: keep one alert with the commands to run, so
  ## the terminal screen can show it (Saved Messages can't be written any
  ## more), and pass it to `tell` (your own bot, which doesn't need the login;
  ## nil: none). Once until the next successful login, however often systemd
  ## restarts the bot. True when the alert is new. Never raises.
  let text = loginAlert(why, dryRun)
  try: result = st.markLoginNeeded(text)
  except CatchableError: discard
  if result and tell != nil: tell(alertText(text, dryRun))

proc loginTeller*(q: NotifyQueue, alerts: bool): proc (text: string) {.closure, gcsafe.} =
  ## How the log-out alert reaches your bot: at the front of its queue. nil
  ## (not at all) without a bot, or with `"alerts": false`.
  if q == nil or not alerts: return nil
  result = proc (text: string) {.closure, gcsafe.} = q.pushFirst text

proc isLogOut*(authState: string): bool =
  ## A lost session that means the login is gone (not TDLib closing by itself).
  authState in ["authorizationStateLoggingOut", "authorizationStateWaitPhoneNumber",
                "authorizationStateWaitCode", "authorizationStateWaitPassword",
                "authorizationStateClosed"]

proc finishNotifying*(l: Listener, st: State, sessionLost: string, dryRun: bool,
                      tell: proc (text: string) {.closure, gcsafe.},
                      stopFlushMs = notify.stopFlushMs, afterLogOutMs = logOutFlushMs) {.async.} =
  ## At a stop: a log-out is one alert (screen, and your bot through `tell`),
  ## then what's still queued for your bot is sent: for at most 13 s, or
  ## after a log-out long enough for a send waiting out a 429 and then the
  ## alert (`logOutFlushMs`, 70 s, under systemd's 90 s stop limit).
  if sessionLost.isLogOut:
    recordLoginNeeded(st, sessionLost, dryRun, tell)   # before closing the state: the screen shows it
    await l.flushNotifications(afterLogOutMs)
  else:
    await l.flushNotifications(stopFlushMs)

proc clearLoginNeeded*(st: State) =
  ## Logged in again: a later log-out alerts again. Never raises.
  try: st.setMeta("login_needed", "")
  except CatchableError: discard

proc loggedInOrRecord*(st: State, dryRun: bool, connecting: Future[Session],
                       tell: proc (text: string) {.closure, gcsafe.} = nil): Future[Session] {.async.} =
  ## Wait for the login. Not logged in: tell the terminal screen (the bot
  ## can't message you any more) and `tell` (your own bot). Logged in: clear
  ## that at once, as TDLib is ready, not after the chat list loads (minutes
  ## after a log-out wiped it).
  try:
    result = await connecting
  except NotLoggedIn as e:
    recordLoginNeeded(st, "not logged in", dryRun, tell)
    raise e
  clearLoginNeeded(st)

proc printable*(s: string): string =
  ## Post text without control characters, so a post (or a scraped job
  ## page) can't clear or rewrite your terminal in `review`.
  for r in s.runes:
    let c = r.int
    if c == 0x0A or c == 0x09 or (c >= 0x20 and c != 0x7F and (c < 0x80 or c > 0x9F)):
      result.add r

proc statePath*(cfg: Config, dryRun: bool): string =
  ## Dry runs keep their own state, so switching to the real bot later
  ## doesn't skip messages the dry run had already judged.
  cfg.dataDir / (if dryRun: "state-dryrun.sqlite3" else: "state.sqlite3")

proc cmdLogin*(cfg: Config) {.async.} =
  let s = await connect(cfg, interactive = true)
  echo "Logged in. Your chats (use these IDs for \"chats\" and \"target_chat\"):"
  for (id, title) in await s.listChats():
    echo &"{id:>16}  {title}"
  await s.client.close()

proc saveChatList(s: Session, st: State, cfg: Config, titles: Table[int64, string],
                  l: Listener) {.async.} =
  ## Never raises.
  try:
    var rows: seq[ChatRow]
    for (id, title, kind) in await s.chatRows(timeoutMs = 10_000):
      rows.add ChatRow(id: id, title: title, kind: kind)
    st.saveChats(rows, cfg.chats, titles)
  except CatchableError as e:
    l.log "   could not save the chat list: " & e.msg.splitLines()[0]

proc corpusId*(j: JsonNode): string =
  ## A corpus line's id as text: numbers in the Saved Messages corpus,
  ## "chat:msg" in the labels the terminal screen writes, "?" if missing.
  let id = j{"id"}
  if id == nil: "?"
  elif id.kind == JString: id.getStr
  elif id.kind == JInt: $id.getBiggestInt
  else: $id

proc cmdAlert*(cfg: Config, text: string, fetcher: Fetcher = nil) {.async.} =
  ## An alert from outside the bot (the nightly self-update, #123): kept for
  ## the terminal screen, and sent through your own bot unless `"alerts":
  ## false`. It goes to the user id the bot kept (no Telegram login here).
  ## A send that fails is said on stderr; the alert is still kept.
  let st = openState(statePath(cfg, cfg.dryRun))
  defer: st.close()
  st.addAlert(text)
  if not cfg.alerts: return
  let log = proc (line: string) {.closure, gcsafe.} = stderr.writeLine line
  let send = notifierFor(cfg.notifyBotToken, storedChatId(st, nil),
                         (if fetcher != nil: fetcher else: botFetcher()), log, st)
  if send == nil: return
  let err = await send(alertText(text, cfg.dryRun))
  if err != "": log "could not send the alert through your bot: " & err

proc cmdRun*(cfg: Config, dryRunFlag: bool) {.async.} =
  let dry = dryRunFlag or cfg.dryRun
  let st = openState(statePath(cfg, dry))
  # for the terminal screen, at once: the last run's "stopped" / "ready" mustn't
  # show while this one logs in (connection updates before the listener exists
  # are dropped)
  try:
    st.setMeta("mode", if dry: "dry-run" else: "real")
    st.setMeta("started_at", $getTime().toUnix)
    st.setMeta("connection", "starting")
    # a post this bot was sending when it last stopped: back to the queue,
    # for you to check, never sent twice
    let reopened = st.reopenInterruptedSends()
    if reopened > 0:
      echo &"   {reopened} approved post(s) were being sent when the bot stopped: back in the review queue"
  except CatchableError as e:
    echo "   could not update the state database: ", e.msg
  var l: Listener
  var sessionLost = ""
  # Your own bot (#122), before connecting: the log-out alert goes through it,
  # and that needs no login. Your user id comes from TDLib once it's there,
  # else the one kept in the state database.
  var outbox: NotifyQueue
  let chatId = storedChatId(st, proc (): Future[int64] {.async, closure, gcsafe.} =
      if l == nil: raise newException(IOError, "Telegram isn't connected yet")
      return await l.tg.selfChat())
  let notifier = notifierFor(cfg.notifyBotToken, chatId, botFetcher(),
                             (proc (line: string) {.closure, gcsafe.} = echo line), st)
  if notifier != nil:
    outbox = newNotifyQueue(notifier, proc (line: string) {.closure, gcsafe.} =
      if l != nil: l.log line
      else: echo line)
    echo "notifications: through your own bot (notify_bot_token)"
  let tellAlert = loginTeller(outbox, cfg.alerts)   # alerts: false keeps them out of your bot too
  var titles = initTable[int64, string]()
  let watched = cfg.chats
  var accessReady = false   # the start-up check reads the current state first
  let connecting = loggedInOrRecord(st, dry, connect(cfg, interactive = false, onUpdate =
    proc (u: JsonNode) {.closure, gcsafe.} =
      if l == nil: return
      case u{"@type"}.getStr
      of "updateNewMessage":
        var m = parseMessage(u["message"])
        if m.outgoing:
          # in a dry run, your own posts in the target chat are the Python bot's
          if l.dryRun and m.chatId == l.cfg.targetChat: asyncCheck l.noteOtherBot(m)
          return
        if m.chatId notin watched: return
        m.chatTitle = titles.getOrDefault(m.chatId, $m.chatId)
        l.enqueue m
      of "updateMessageContent":
        # an edit: look again if it's recent (handleGroup decides if needed)
        let chat = u{"chat_id"}.getBiggestInt
        if chat in watched:
          let id = u{"message_id"}.getBiggestInt
          let tg = l.tg
          proc refetch() {.async.} =
            try:
              var m = await tg.getMessage(chat, id)
              if getTime().toUnix - m.date > 2 * 86400: return
              m.edited = true
              m.chatTitle = titles.getOrDefault(chat, $chat)
              l.enqueue m
            except CatchableError: discard   # deleted meanwhile, etc.
          asyncCheck refetch()
      of "updateSupergroup", "updateBasicGroup":
        # banned from / left a watched channel or group (#16). TDLib sends
        # these for every chat while starting up: until the start-up check
        # has run (and the titles are known) they're left to it.
        if not accessReady: return
        let (chat, access) = parseAccessUpdate(u)
        if chat in watched: asyncCheck l.onChatAccess(chat, access, live = true)
      of "updateConnectionState":
        l.onConnectionState(u{"state", "@type"}.getStr)
      of "updateAuthorizationState":
        # logged out from another device, or TDLib closing: stop (and let
        # systemd restart us) instead of hanging while looking alive
        let a = u.authState
        if sessionLost == "" and sessionEnded(a, stopping = l.stop[]):
          sessionLost = a
          l.stop[] = true      # l.stop is stopRequested (set below)
      of "updateMessageSendFailed":
        # TDLib accepted a forward/send but Telegram refused it later
        # (no right to post any more, slow mode, flood limit...)
        let err = u{"error", "message"}.getStr(u{"error_message"}.getStr("unknown error"))
        asyncCheck l.alert("a message to chat " & $u{"message", "chat_id"}.getBiggestInt &
                           " could not be sent: " & err)
      else: discard), tellAlert)
  yield connecting
  if connecting.failed:
    # not logged in: give the alert a moment to reach your bot
    if outbox != nil: await outbox.flush(stopFlushMs)
    raise connecting.readError
  let s = connecting.read
  l = newListener(cfg, tdTelegram(s.client), st, defaultFetcher(), defaultOcr(), dry)
  l.stop = stopRequested
  l.outbox = outbox     # your private chat with your bot has your user id
  # "notifications are on", once (and after another account logs in)
  if notifier != nil:
    asyncCheck announceOn(st, notifier, chatId, dry,
                          anyOn = anyNotifications(cfg.notifyPublished, cfg.notifyReview, cfg.alerts))
  l.instagram = instagramFor(cfg.instagramFrontend, defaultOcr())
  if l.instagram != nil: l.log "instagram links: read through " & cfg.instagramFrontend
  if l.dryRun: l.log "DRY RUN: nothing will be forwarded or marked read"
  await s.loadAllChats()
  for chat in cfg.chats:
    try:
      let c = await s.client.call(%*{"@type": "getChat", "chat_id": chat})
      titles[chat] = c{"title"}.getStr
      discard await s.client.call(%*{"@type": "openChat", "chat_id": chat})
    except TdError as e:
      l.log &"⚠️  chat {chat} from config.json is not available: {e.msg}"
  if l.dryRun:
    # so TDLib reliably delivers the Python bot's posts there (compare)
    try: discard await s.client.call(%*{"@type": "openChat", "chat_id": cfg.targetChat})
    except TdError as e: l.log &"⚠️  target_chat {cfg.targetChat} is not available: {e.msg}"
  setControlCHook(proc () {.noconv.} = stopRequested[] = true)
  signal(SIGTERM, onSignal)
  await l.checkDowntimeAtStart()
  l.titles = titles
  accessReady = true
  await l.checkWatchedChats()
  asyncCheck l.heartbeat()
  await l.bootstrap()
  if not stopRequested[]: l.log &"watching {titles.len} chats"
  # your chats by name, for the terminal screen's settings: after the
  # backlog, so a slow TDLib answer here never holds the bot up
  asyncCheck saveChatList(s, st, cfg, titles, l)
  await l.worker()
  l.log "stopping"
  try: st.setMeta("connection", "stopped")
  except CatchableError: discard
  await finishNotifying(l, st, sessionLost, dry, tellAlert)
  await s.client.close()
  st.close()
  if sessionLost != "":
    raise newException(ValueError, "the Telegram session ended (" & sessionLost &
      "). If you logged out, run `telenotify login` again")

proc stdinIsTerminal(): bool = isatty(getFileHandle(stdin)) == 1

proc askYesNo*(question: string, input: File = stdin, interactive = stdinIsTerminal()): bool =
  ## Ask `question` and wait for an answer: true only for y / yes. When the
  ## input isn't a terminal (a pipe, a script), the answer is no without
  ## reading anything: sending and discarding need a person (#62).
  stdout.write question & " [y/N] "
  stdout.flushFile
  if not interactive:
    echo "no (not asked: the input isn't a terminal)"
    return false
  var answer = ""
  if not input.readLine(answer): return false
  answer.strip.toLowerAscii in ["y", "yes"]

proc cmdReview*(cfg: Config, dryRun: bool, input: File = stdin, interactive = stdinIsTerminal()) =
  ## Go through the review queue: approve each post or discard it, each
  ## after a y/N question. This only records your decision, so it works
  ## while the bot runs; the running bot sends what you approved once its
  ## undo window (`undoWindowSeconds`) is over. Until then, and for anything
  ## discarded, the terminal screen's Review tab (History, u) can take it back.
  let st = openState(statePath(cfg, dryRun))
  defer: st.close()
  let pending = st.pendingReviews()
  if pending.len == 0:
    echo "Nothing to review."
    return
  var approved = 0
  var quitting = false
  for i, r in pending:
    echo "\n", "═".repeat(70)
    echo &"[{i + 1}/{pending.len}]  {r.chatTitle.printable}  ·  {fromUnix(r.at).local.format(\"yyyy-MM-dd HH:mm\")}"
    if r.link != "": echo r.link.printable
    echo "role: ", r.role.printable
    echo "why review: ", r.reasons.printable
    echo "─".repeat(70)
    let lines = r.text.printable.splitLines
    echo lines[0 ..< min(lines.len, 40)].join("\n")
    if lines.len > 40: echo &"… ({lines.len - 40} more lines)"
    echo "─".repeat(70)
    var done = false
    while not done:
      stdout.write "[s]end to channel  [d]iscard  [n]ext  [q]uit > "
      stdout.flushFile
      var answer = ""
      if not input.readLine(answer): answer = "q"    # end of input
      case answer.strip.toLowerAscii
      of "s":
        if not askYesNo("Send this post to the channel?", input, interactive):
          echo "not sent; still waiting for review"
        elif st.decideReview(r.id, "to_send"):
          inc approved
          if dryRun: echo "approved ✅ (dry run: recorded, never sent)"
          else: echo &"approved ✅ (sent in {undoWindowSeconds} s; until then the terminal screen's Review tab can undo it)"
        else: echo "already decided elsewhere"
        done = true
      of "d":
        if not askYesNo("Discard?", input, interactive):
          echo "not discarded; still waiting for review"
        else:
          echo (if st.decideReview(r.id, "discarded"): "discarded" else: "already decided elsewhere")
        done = true
      of "n", "": done = true
      of "q":
        quitting = true
        done = true
      else: discard
    if quitting: break
  if approved > 0:
    if dryRun: echo &"\n{approved} approved (dry run: recorded, never sent)."
    else: echo &"\n{approved} approved: the running bot sends them after {undoWindowSeconds} s (or when it next starts)."

proc fitColumn*(s: string, width: int): string =
  ## `s` cut or padded to `width` characters (not bytes: channel names are
  ## often Arabic, and cutting bytes breaks a letter in half).
  let r = s.toRunes
  result = if r.len > width: $r[0 ..< width - 1] & "…" else: s
  result.add " ".repeat(max(0, width - result.runeLen))

proc short(s: string, n: int): string =
  let one = s.printable.replace("\n", " ").strip
  let r = one.toRunes
  if r.len > n: $r[0 ..< n] & "…" else: one

proc cmdCompare*(cfg: Config, days: int) =
  ## The dry run next to the Python bot (compare.nim).
  let st = openState(statePath(cfg, dryRun = true))
  defer: st.close()
  let c = st.compareRuns(getTime().toUnix - days * 86400)
  echo &"Last {days} days: both published {c.both.len}, Python only {c.pythonOnly.len}, Nim only {c.nimOnly.len}"
  if c.both.len + c.pythonOnly.len == 0:
    echo "(nothing from the Python bot yet: is `telenotify run --dry-run` running?)"
  echo "\n── Python only (Nim's verdict and why) " & "─".repeat(32)
  for x in c.pythonOnly:
    echo "• ", x.other.text.short(90)
    if x.nimSaw:
      echo &"    Nim: {x.nim.action}, {x.nim.chatTitle.short(30)} #{x.nim.msgId}: {x.nim.reasons.short(160)}"
    else:
      echo "    Nim: never saw it (before the dry run started, or not from a watched chat)"
  echo "\n── Nim only " & "─".repeat(59)
  for x in c.nimOnly:
    echo &"• {x.nim.chatTitle.short(30)} #{x.nim.msgId} ({x.nim.role.short(30)}): " &
         st.publishedText(x.nim.chatId, x.nim.msgId).short(90)
    echo "    why: ", x.nim.reasons.short(160)

proc parseDays*(val: string): int =
  ## `--days:N`: a whole number from 1 to 3650 (ten years), or ValueError
  ## with the message to show.
  try: result = parseInt(val)
  except ValueError: raise newException(ValueError, "--days needs a number")
  if result notin 1 .. 3650: raise newException(ValueError, "--days must be between 1 and 3650")

proc duration(secs: int64): string =
  ## 45s, 3m 05s, 2h 07m, 3d 4h
  if secs < 60: fmt"{secs}s"
  elif secs < 3600: fmt"{secs div 60}m {secs mod 60:02}s"
  elif secs < 86400: fmt"{secs div 3600}h {secs mod 3600 div 60:02}m"
  else: fmt"{secs div 86400}d {secs mod 86400 div 3600}h"

proc latencyLines*(l: tuple[live, caughtUp, reviewed: Latency], days: int,
                   dryRun = false): seq[string] =
  ## How long forwarded posts took to reach your channel, from when they
  ## went up (or, for an edit judged again, from the edit) (#74). Posts
  ## judged before their date was kept are left out.
  proc line(what: string, x: Latency): string =
    let posts = if x.count == 1: "post" else: "posts"
    &"  {what}: typically within {duration(x.median)} (half are faster), " &
      &"95 of 100 within {duration(x.p95)} ({x.count} {posts})"
  if l.live.count == 0 and l.caughtUp.count == 0 and l.reviewed.count == 0:
    return @[if dryRun: &"Forwarding time: no post would have been forwarded in the last {days} days " &
                        "(or none with a known Telegram date)"
             else: &"Forwarding time: no post was forwarded in the last {days} days " &
                   "(or none with a known Telegram date)"]
  result.add(if dryRun: "Forwarding time (dry run), from when a post went up to when it would have reached your channel:"
             else: "Forwarding time, from when a post went up to when it reached your channel:")
  if l.live.count > 0: result.add line("live", l.live)
  if l.caughtUp.count > 0: result.add line("caught up after downtime", l.caughtUp)
  if l.reviewed.count > 0: result.add line("after your review", l.reviewed)

proc cmdStats*(cfg: Config, days: int, dryRun: bool) =
  let st = openState(statePath(cfg, dryRun))
  let rows = st.stats(days)
  echo &"Last {days} days                                  publish  review  reject  forwarded"
  var tot: array[4, int]
  for r in rows:
    let name = r.chat.printable.fitColumn(44)
    echo &"{name}{r.verdicts[0]:>9}{r.verdicts[1]:>8}{r.verdicts[2]:>8}{r.forwarded:>11}"
    for i in 0 .. 2: tot[i] += r.verdicts[i]
    tot[3] += r.forwarded
  echo &"{\"total\":<44}{tot[0]:>9}{tot[1]:>8}{tot[2]:>8}{tot[3]:>11}"
  echo &"pending reviews: {st.pendingReviews().len}"
  for line in latencyLines(st.latency(days), days, dryRun): echo line
  st.close()
