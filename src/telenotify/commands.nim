## The commands that talk to Telegram: login, chats, run, review, stats.

import std/[asyncdispatch, json, os, strutils, strformat, tables, times, posix]
import config, td, session, telegram, state, listener, fetch, gather

var stopRequested = new bool   # set by Ctrl-C or `systemctl stop` (SIGTERM)

proc onSignal(sig: cint) {.noconv.} = stopRequested[] = true

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

proc cmdRun*(cfg: Config, dryRunFlag: bool) {.async.} =
  let dry = dryRunFlag or cfg.dryRun
  let st = openState(statePath(cfg, dry))
  var l: Listener
  var sessionLost = ""
  var titles = initTable[int64, string]()
  let watched = cfg.chats
  let s = await connect(cfg, interactive = false, onUpdate =
    proc (u: JsonNode) {.closure, gcsafe.} =
      if l == nil: return
      case u{"@type"}.getStr
      of "updateNewMessage":
        let msg = u["message"]
        if msg{"is_outgoing"}.getBool: return
        var m = parseMessage(msg)
        if m.chatId notin watched: return
        m.chatTitle = titles.getOrDefault(m.chatId, $m.chatId)
        l.enqueue m
      of "updateConnectionState":
        l.onConnectionState(u{"state", "@type"}.getStr)
      of "updateAuthorizationState":
        # logged out from another device, or TDLib closing: stop (and let
        # systemd restart us) instead of hanging while looking alive
        let a = u.authState
        if a != "authorizationStateReady" and sessionLost == "":
          sessionLost = a
          l.stop[] = true      # l.stop is stopRequested (set below)
      else: discard)
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
  setControlCHook(proc () {.noconv.} = stopRequested[] = true)
  signal(SIGTERM, onSignal)
  await l.checkDowntimeAtStart()
  asyncCheck l.heartbeat()
  await l.bootstrap()
  if not stopRequested[]: l.log &"watching {titles.len} chats"
  await l.worker()
  l.log "stopping"
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
    echo &"[{i + 1}/{pending.len}]  {r.chatTitle}  ·  {fromUnix(r.at).local.format(\"yyyy-MM-dd HH:mm\")}"
    if r.link != "": echo r.link
    echo "role: ", r.role
    echo "why review: ", r.reasons
    echo "─".repeat(70)
    let lines = r.text.splitLines
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
        st.decideReview(r.id, "to_send")
        inc approved
        echo "approved ✅"
        done = true
      of "d":
        st.decideReview(r.id, "discarded")
        echo "discarded"
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

proc cmdStats*(cfg: Config, days: int, dryRun: bool) =
  let st = openState(statePath(cfg, dryRun))
  let rows = st.stats(days)
  echo &"Last {days} days                                  publish  review  reject  forwarded"
  var tot: array[4, int]
  for r in rows:
    let name = (if r.chat.len > 40: r.chat[0 ..< 40] else: r.chat)
    echo &"{name:<44}{r.verdicts[0]:>9}{r.verdicts[1]:>8}{r.verdicts[2]:>8}{r.forwarded:>11}"
    for i in 0 .. 2: tot[i] += r.verdicts[i]
    tot[3] += r.forwarded
  echo &"{\"total\":<44}{tot[0]:>9}{tot[1]:>8}{tot[2]:>8}{tot[3]:>11}"
  echo &"pending reviews: {st.pendingReviews().len}"
  st.close()
