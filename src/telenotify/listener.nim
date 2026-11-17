## The bot itself: what happens to each message.
##
##   message --gather--> text --classify--> verdict --act--> forward / queue / skip
##
## Fixes for the old bot's listener problems (problems report section 4):
## * **One queue, one message at a time.** Startup backlog and live
##   messages go through the same queue, processed one after another. The
##   old bot handled messages concurrently, so two channels cross-posting
##   the same ad at the same moment were both forwarded (4.8, 4.9).
## * **Every message is remembered** (state.nim), so a message seen twice
##   (restart, backlog overlapping live updates) is skipped.
## * **The real read marker** for the backlog, oldest first (4.6, 4.7).
## * **A failure only affects its own message** (4.1). That message isn't
##   lost either: it goes to the review queue with the error, so you decide.
## * **Backlog first.** Live messages that arrive while the backlog is being
##   read wait until it's done, so "oldest first" holds and a chat is never
##   marked read past messages still waiting.
## * **Stops promptly.** A stop request (Ctrl-C, `systemctl stop`) is
##   honoured after the current message, not after the whole queue.
## * **Review posts stay in tele-notify's review queue** (`telenotify
##   review`), not in Saved Messages, as you wanted. `review` only writes
##   your decision to the database (so it works while the bot runs); the
##   running bot sends the posts you approved (`sendApproved`).
## * **Dry run** (`--dry-run` or `"dry_run": true`): judge and record
##   everything, but forward nothing and mark nothing read. That's stage 4:
##   running next to the Python bot to compare.
## * **Downtime alerts** (#3, #16): a heartbeat is saved every minute; on
##   start-up, and when the connection comes back, a long gap is reported
##   to your Saved Messages.

import std/[asyncdispatch, deques, strutils, times, strformat]
import config, classify, gather, fetch, telegram, state, dedup, boards

type
  Listener* = ref object
    cfg*: Config
    tg*: Telegram
    state*: State
    fetcher*: Fetcher
    ocr*: Ocr
    dryRun*: bool
    queue: Deque[TgMessage]
    bootstrapping: bool
    held: seq[TgMessage]          # live messages that arrived during bootstrap
    stop*: ref bool               # set to stop after the current message
    log*: proc (line: string) {.closure, gcsafe.}
    downSince: int64
    selfChat: int64

proc newListener*(cfg: Config, tg: Telegram, st: State, fetcher: Fetcher, ocr: Ocr,
                  dryRun: bool): Listener =
  Listener(cfg: cfg, tg: tg, state: st, fetcher: fetcher, ocr: ocr, dryRun: dryRun,
           queue: initDeque[TgMessage](), stop: new bool,
           log: proc (line: string) {.closure, gcsafe.} =
             echo now().format("yyyy-MM-dd HH:mm:ss"), "  ", line)

proc say(l: Listener, line: string) = l.log(line)

proc duration(seconds: int64): string =
  let h = seconds div 3600
  let m = (seconds mod 3600) div 60
  if h > 0: &"{h}h {m}m" else: &"{m}m"

proc alert*(l: Listener, text: string) {.async.} =
  ## Log loudly and, if alerts are on, tell you in Saved Messages.
  l.say "⚠️  " & text
  if not l.cfg.alerts: return
  try:
    if l.selfChat == 0: l.selfChat = await l.tg.selfChat()
    await l.tg.sendText(l.selfChat, "⚠️ tele-notify: " & text)
  except CatchableError as e:
    l.say "   (could not send the alert: " & e.msg & ")"

proc handle*(l: Listener, m: TgMessage) {.async.} =
  ## Judge one message and act on it. Never raises.
  if l.state.seen(m.chatId, m.id): return
  var verdict, role, reasons = ""
  var action = "error"
  try:
    var post = Post(text: m.text, buttons: m.buttons)
    if m.photoFileId != 0:
      try: post.images.add await l.tg.downloadPhoto(m.photoFileId)
      except CatchableError as e: l.say &"   photo download failed: {e.msg}"
    let g = await gather(post, l.fetcher, l.ocr, @defaultJobPostingSites & l.cfg.extraJobSites)
    let d = classify(g.text, l.cfg)
    verdict = $d.verdict
    role = d.role
    reasons = (g.notes & d.reasons).join("; ")
    case d.verdict
    of vPublish:
      let dup = duplicateOf(g.text, l.state.recentPublished())
      if dup != "":
        action = "duplicate"
        reasons.add "; " & dup
      elif l.dryRun:
        action = "would publish"
        l.state.addPublished(m.chatId, m.id, g.text)
      elif await l.tg.forward(l.cfg.targetChat, m.chatId, m.id):
        action = "forwarded"
        l.state.addPublished(m.chatId, m.id, g.text)
      else:
        # forwarding is forbidden in this chat: send the role and a link
        let link = await l.tg.link(m.chatId, m.id)
        await l.tg.sendText(l.cfg.targetChat, "match: " & role & "\npost link: " & link)
        action = "linked"
        l.state.addPublished(m.chatId, m.id, g.text)
    of vReview:
      var link = ""
      try: link = await l.tg.link(m.chatId, m.id)
      except CatchableError: discard
      l.state.addReview(m.chatId, m.id, m.chatTitle, g.text, reasons, role, link)
      action = "queued for review"
    of vReject:
      action = "rejected"
  except CatchableError as e:
    # Don't lose the post: put it in the review queue with the error.
    reasons.add "; error: " & e.msg
    action = "error, queued for review"
    l.say &"   ✗ {m.chatTitle} #{m.id}: {e.msg}"
    try:
      var link = ""
      try: link = await l.tg.link(m.chatId, m.id)
      except CatchableError: discard
      l.state.addReview(m.chatId, m.id, m.chatTitle, m.text, reasons.strip(chars = {';', ' '}),
                        role, link)
    except CatchableError as e2:
      l.say &"   could not queue it for review either: {e2.msg}"
  try:
    l.state.markProcessed(m.chatId, m.id, m.chatTitle, verdict, role, reasons, action)
  except CatchableError as e:
    l.say &"   could not record {m.chatTitle} #{m.id}: {e.msg}"

  let mark = case action
    of "forwarded", "linked", "would publish": "✅"
    of "queued for review", "error, queued for review": "🤔"
    of "duplicate": "♻️"
    else: "·"
  l.say &"{mark} {m.chatTitle} #{m.id}: {action}" & (if role != "": " (" & role & ")" else: "")

proc enqueue*(l: Listener, m: TgMessage) =
  ## A live message. During bootstrap it waits until the backlog is done.
  if l.bootstrapping: l.held.add m
  else: l.queue.addLast m

proc processQueue*(l: Listener) {.async.} =
  ## Handle everything queued, one by one, marking each read afterwards.
  ## Stops early when `stop` is set; the rest stays unread for next time.
  while l.queue.len > 0 and not l.stop[]:
    let m = l.queue.popFirst
    await l.handle(m)
    if not l.dryRun:
      try: await l.tg.markRead(m.chatId, @[m.id])
      except CatchableError as e: l.say "   could not mark read: " & e.msg

proc bootstrap*(l: Listener) {.async.} =
  ## Handle every chat's unread messages, oldest first, then the live
  ## messages that arrived meanwhile.
  l.bootstrapping = true
  try:
    for chat in l.cfg.chats:
      if l.stop[]: break
      try:
        let msgs = await l.tg.unread(chat)
        if msgs.len > 0:
          l.say &"{msgs.len} unread in {msgs[0].chatTitle}"
        for m in msgs: l.queue.addLast m
      except CatchableError as e:
        l.say &"   could not read chat {chat}: {e.msg}"
    await l.processQueue()
  finally:
    l.bootstrapping = false
    for m in l.held: l.queue.addLast m
    l.held.setLen 0
  await l.processQueue()

proc sendApproved*(l: Listener) {.async.} =
  ## Send the posts you approved in `telenotify review`. A failure puts the
  ## post back in the review queue, with the reason.
  for r in l.state.approvedReviews():
    if l.stop[]: return
    try:
      if not await l.tg.forward(l.cfg.targetChat, r.chatId, r.msgId):
        var link = r.link
        if link == "": link = await l.tg.link(r.chatId, r.msgId)
        if link == "": raise newException(ValueError, "no link to the post")
        await l.tg.sendText(l.cfg.targetChat, "match: " & r.role & "\npost link: " & link)
      l.state.addPublished(r.chatId, r.msgId, r.text)
      l.state.decideReview(r.id, "sent")
      l.say &"✅ {r.chatTitle} #{r.msgId}: sent after review"
    except CatchableError as e:
      l.say &"   could not send reviewed {r.chatTitle} #{r.msgId}: {e.msg}"
      try: l.state.reopenReview(r.id, e.msg)
      except CatchableError: discard

proc worker*(l: Listener) {.async.} =
  ## Process live messages as they arrive, and approved reviews every few
  ## seconds, until `stop` is set.
  var ticks = 0
  while not l.stop[]:
    if l.queue.len > 0: await l.processQueue()
    else: await sleepAsync(100)
    inc ticks
    if ticks mod 50 == 0 and not l.dryRun:
      try: await l.sendApproved()
      except CatchableError as e: l.say "   reviewed posts: " & e.msg

proc checkDowntimeAtStart*(l: Listener) {.async.} =
  let last = l.state.getMeta("last_alive")
  if last == "": return
  let gap = getTime().toUnix - parseBiggestInt(last)
  if gap > l.cfg.alertAfterMinutes * 60:
    let since = fromUnix(parseBiggestInt(last)).local.format("yyyy-MM-dd HH:mm")
    await l.alert(&"was not running for {duration(gap)} (since {since}). Back now.")

proc heartbeat*(l: Listener) {.async.} =
  while not l.stop[]:
    try: l.state.setMeta("last_alive", $getTime().toUnix)
    except CatchableError as e: l.say "   heartbeat: " & e.msg
    for _ in 0 ..< 60:
      if l.stop[]: break
      await sleepAsync(1000)

proc onConnectionState*(l: Listener, state: string) =
  ## Called with TDLib's connection state ("connectionStateReady", ...).
  let t = getTime().toUnix
  if state == "connectionStateReady":
    if l.downSince > 0:
      let gap = t - l.downSince
      if gap > l.cfg.alertAfterMinutes * 60:
        let since = fromUnix(l.downSince).local.format("yyyy-MM-dd HH:mm")
        asyncCheck l.alert(&"no connection to Telegram for {duration(gap)} (since {since}). Back now.")
      else:
        l.say &"connection back after {duration(gap)}"
      l.downSince = 0
  elif l.downSince == 0:
    l.downSince = t
    l.say "connection lost (" & state.replace("connectionState", "") & ")"
