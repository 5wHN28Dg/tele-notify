## The commands that talk to Telegram: login, chats, run, review, stats.

import std/[asyncdispatch, json, os, strutils, strformat, tables, times, posix, unicode]
import config, td, session, telegram, state, listener, fetch, gather, compare

var stopRequested = new bool   # set by Ctrl-C or `systemctl stop` (SIGTERM)

proc onSignal(sig: cint) {.noconv.} = stopRequested[] = true

proc sessionEnded*(authState: string, stopping: bool): bool =
  ## Does this authorization update mean the login is gone? Not when we
  ## are closing TDLib ourselves (a normal stop goes through Closing and
  ## Closed too).
  authState != "" and authState != "authorizationStateReady" and not stopping

proc recordLoginNeeded*(st: State, why: string, dryRun: bool) =
  ## Telegram logged the account out (or it never logged in): keep one alert
  ## with the commands to run, so the terminal screen can show it (the bot
  ## can't message you any more). Once until the next successful login, however
  ## often systemd restarts the bot. Never raises.
  try:
    st.setMeta("connection", "logged out")
    if st.getMeta("login_needed") != "1":
      let service = if dryRun: "tele-notify-dryrun" else: "tele-notify"
      st.addAlert(&"Telegram has logged this account out ({why}), so the bot can't read or " &
                  &"forward anything. To log in again, run `telenotify login`, then " &
                  &"`systemctl --user restart {service}`.")
      st.setMeta("login_needed", "1")
  except CatchableError:
    discard

proc clearLoginNeeded*(st: State) =
  ## Logged in again: a later log-out alerts again. Never raises.
  try: st.setMeta("login_needed", "")
  except CatchableError: discard

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
  except CatchableError as e:
    echo "   could not update the state database: ", e.msg
  var l: Listener
  var sessionLost = ""
  var titles = initTable[int64, string]()
  let watched = cfg.chats
  var accessReady = false   # the start-up check reads the current state first
  var s: Session
  try:
    s = await connect(cfg, interactive = false, onUpdate =
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
        else: discard)
  except NotLoggedIn as e:
    recordLoginNeeded(st, "not logged in", dry)
    raise e
  l = newListener(cfg, tdTelegram(s.client), st, defaultFetcher(), defaultOcr(), dry)
  l.stop = stopRequested
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
  clearLoginNeeded(st)
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
  if sessionLost != "":
    recordLoginNeeded(st, sessionLost, dry)   # before closing the state: the screen shows it
  await s.client.close()
  st.close()
  if sessionLost != "":
    raise newException(ValueError, "the Telegram session ended (" & sessionLost &
      "). If you logged out, run `telenotify login` again")

proc cmdReview*(cfg: Config, dryRun: bool) =
  ## Go through the review queue: approve each post or discard it. This
  ## only records your decision, so it works while the bot runs; the
  ## running bot sends what you approved within a few seconds.
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
      if not stdin.readLine(answer): answer = "q"    # end of input
      case answer.strip.toLowerAscii
      of "s":
        if st.decideReview(r.id, "to_send"):
          inc approved
          echo "approved ✅"
        else: echo "already decided elsewhere"
        done = true
      of "d":
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
    else: echo &"\n{approved} approved: the running bot sends them within seconds (or when it next starts)."

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
  st.close()
