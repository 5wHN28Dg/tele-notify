## The commands that talk to Telegram: login, chats, run, review, stats.

import std/[asyncdispatch, json, os, strutils, strformat, tables, times, posix]
import config, td, session, telegram, state, listener, fetch, gather

var stopRequested = new bool   # set by Ctrl-C or `systemctl stop` (SIGTERM)

proc onSignal(sig: cint) {.noconv.} = stopRequested[] = true

proc cmdLogin*(cfg: Config) {.async.} =
  let s = await connect(cfg, interactive = true)
  echo "Logged in. Your chats (use these IDs for \"chats\" and \"target_chat\"):"
  for (id, title) in await s.listChats():
    echo &"{id:>16}  {title}"
  await s.client.close()

proc cmdRun*(cfg: Config, dryRunFlag: bool) {.async.} =
  let st = openState(cfg.dataDir / "state.sqlite3")
  var l: Listener
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
      else: discard)
  l = newListener(cfg, tdTelegram(s.client), st, defaultFetcher(), defaultOcr(),
                  dryRunFlag or cfg.dryRun)
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
  asyncCheck l.heartbeat(stopRequested)
  await l.bootstrap()
  l.log &"watching {titles.len} chats"
  await l.worker(stopRequested)
  l.log "stopping"
  await s.client.close()
  st.close()

proc cmdReview*(cfg: Config) {.async.} =
  ## Go through the review queue: send each post to your channel or discard it.
  let st = openState(cfg.dataDir / "state.sqlite3")
  let pending = st.pendingReviews()
  if pending.len == 0:
    echo "Nothing to review."
    return
  let s = await connect(cfg, interactive = false)
  let tg = tdTelegram(s.client)
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
      case stdin.readLine.strip.toLowerAscii
      of "s":
        try:
          if not await tg.forward(cfg.targetChat, r.chatId, r.msgId):
            await tg.sendText(cfg.targetChat, "match: " & r.role & "\npost link: " & r.link)
          st.addPublished(r.chatId, r.msgId, r.text)
          st.decideReview(r.id, "sent")
          echo "sent ✅"
        except CatchableError as e:
          echo "could not send: ", e.msg
        done = true
      of "d":
        st.decideReview(r.id, "discarded")
        echo "discarded"
        done = true
      of "n", "": done = true
      of "q":
        await s.client.close()
        return
      else: discard
  await s.client.close()

proc cmdStats*(cfg: Config, days: int) =
  let st = openState(cfg.dataDir / "state.sqlite3")
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
