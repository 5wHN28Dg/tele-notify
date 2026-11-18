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
## * **Albums are one post.** The pictures of an album arrive as separate
##   messages; they're judged together (caption + text in every picture)
##   and forwarded together.
## * **Edits.** A rejected post whose text is edited later ("job coming
##   soon" → the real ad) is judged again. Published or reviewed posts are
##   left alone.
## * **Downtime alerts** (#3, #16): a heartbeat is saved every minute; on
##   start-up, and when the connection comes back, a long gap is reported
##   to your Saved Messages.

import std/[asyncdispatch, deques, strutils, times, strformat, sequtils, algorithm]
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
    albumWaitMs*: int             # how long to wait for the rest of an album
    progressEvery*: int           # backlog progress line every this many messages (#11)
    backlogTotal, backlogDone: int
    retryChats: seq[int64]        # chats whose backlog couldn't be read yet (#5)
    log*: proc (line: string) {.closure, gcsafe.}
    downSince: int64
    selfChat: int64

proc newListener*(cfg: Config, tg: Telegram, st: State, fetcher: Fetcher, ocr: Ocr,
                  dryRun: bool): Listener =
  Listener(cfg: cfg, tg: tg, state: st, fetcher: fetcher, ocr: ocr, dryRun: dryRun,
           queue: initDeque[TgMessage](), stop: new bool,
           # live messages wait from the very start until the backlog is done
           # (they can arrive while chats are still being opened)
           bootstrapping: true, albumWaitMs: 1500, progressEvery: 25,
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

const maxAlbumPhotos = 6   ## OCR is slow; the ad is almost always in the first few

proc fingerprint(group: seq[TgMessage]): string =
  ## FNV-1a of the text: stable across Nim versions (std/hashes isn't
  ## promised to be), since it's kept in the database.
  var h = 0xcbf29ce484222325'u64
  for m in group:
    for c in m.text & "\n" & m.buttons.join("\n") & "\n":
      h = (h xor c.uint64) * 0x100000001b3'u64
  h.toHex

proc handleGroup*(l: Listener, group: seq[TgMessage]) {.async.} =
  ## Judge one post (a message, or all pictures of an album) and act on
  ## it. Never raises.
  let m = group[0]
  let ids = group.mapIt(it.id)
  let fp = fingerprint(group)
  # Seen before? Any part of an album counts (e.g. the backlog had only its
  # first picture, and the rest arrived live).
  var prev = l.state.previous(m.chatId, m.id)
  for x in group[1 .. ^1]:
    if not prev.found: prev = l.state.previous(x.chatId, x.id)
  if not prev.found and not m.edited:
    # a picture of an album handled earlier (it arrived late, or the queue
    # split the album)
    prev = l.state.albumHandled(m.chatId, m.albumId)
  if m.edited and not prev.found:
    # An edit of a message the bot never saw (it wasn't running, or a dry
    # run started later). Its original would have been judged if seen;
    # judging the edit as a new post would forward old posts.
    return
  if prev.found:
    # Only an edit of a rejected post with new text is judged again ("job
    # ad coming soon..." edited into the real ad). Not for album pictures
    # (one picture isn't the post), and not for rows from before
    # fingerprints were kept (empty: we can't tell what changed).
    let again = m.edited and group.len == 1 and m.albumId == 0 and
                prev.action == "rejected" and prev.textHash notin ["", fp]
    if not again:
      for y in group:      # parts not recorded yet belong to the handled post
        if not l.state.seen(y.chatId, y.id):
          l.state.markProcessed(y.chatId, y.id, y.chatTitle, "", "", "part of a post already handled",
                                prev.action, prev.textHash, albumId = y.albumId)
      return
    l.say &"✏️  {m.chatTitle} #{m.id} was edited: judging it again"
  var verdict, role, reasons = ""
  var action = "error"
  try:
    var post = Post(text: group.mapIt(it.text).filterIt(it.strip != "").join("\n"))
    for x in group: post.buttons.add x.buttons
    var photoProblem = ""
    for x in group:
      if x.photoFileId == 0 or post.images.len >= maxAlbumPhotos: continue
      try: post.images.add await l.tg.downloadPhoto(x.photoFileId)
      except CatchableError as e:
        photoProblem = "photo download failed: " & e.msg
        l.say "   " & photoProblem
    let g = await gather(post, l.fetcher, l.ocr, @defaultJobPostingSites & l.cfg.extraJobSites)
    for n in g.notes:
      if n.startsWith("OCR failed"): photoProblem = n
    var d = classify(g.text, l.cfg)
    if d.verdict == vReject and photoProblem != "":
      # The picture is often the whole ad: unread, it's unknown, not bad.
      d.verdict = vReview
      d.reasons.add "the picture could not be read, so this is a guess"
    verdict = $d.verdict
    role = d.role
    reasons = (g.notes & d.reasons).join("; ")
    if group.len > 1: reasons = &"album of {group.len}; " & reasons
    case d.verdict
    of vPublish:
      let dup = duplicateOf(g.text, l.state.recentPublished())
      if dup != "":
        action = "duplicate"
        reasons.add "; " & dup
      elif l.dryRun:
        action = "would publish"
        l.state.addPublished(m.chatId, m.id, g.text)
      elif await l.tg.forward(l.cfg.targetChat, m.chatId, ids):
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
      l.state.addReview(m.chatId, m.id, m.chatTitle, g.text, reasons, role, link, ids)
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
      l.state.addReview(m.chatId, m.id, m.chatTitle, group.mapIt(it.text).join("\n"),
                        reasons.strip(chars = {';', ' '}), role, link, ids)
    except CatchableError as e2:
      l.say &"   could not queue it for review either: {e2.msg}"
  for x in group:
    try:
      l.state.markProcessed(x.chatId, x.id, x.chatTitle, verdict, role, reasons, action, fp,
                            rawText = (if l.dryRun: x.text else: ""),   # for compare
                            albumId = x.albumId)
    except CatchableError as e:
      l.say &"   could not record {x.chatTitle} #{x.id}: {e.msg}"

  let mark = case action
    of "forwarded", "linked", "would publish": "✅"
    of "queued for review", "error, queued for review": "🤔"
    of "duplicate": "♻️"
    else: "·"
  let what = (if group.len > 1: &"#{m.id} (album of {group.len})" else: &"#{m.id}")
  l.say &"{mark} {m.chatTitle} {what}: {action}" & (if role != "": " (" & role & ")" else: "")

proc handle*(l: Listener, m: TgMessage) {.async.} =
  ## Judge one message on its own.
  await l.handleGroup(@[m])

proc noteOtherBot*(l: Listener, m: TgMessage) {.async.} =
  ## Dry run next to the Python bot: remember what it sent to your channel,
  ## for `telenotify compare`. Never raises.
  try:
    var (chat, msg) = (m.originChat, m.originMsg)
    let i = m.text.find("post link: ")
    if chat == 0 and i >= 0:
      let words = m.text[i + "post link: ".len .. ^1].splitWhitespace
      if words.len > 0:
        try: (chat, msg) = await l.tg.linkTarget(words[0])
        except CatchableError:
          # The Python bot writes t.me/None/<id> for channels without a
          # username: keep the id, compare finds the chat.
          let parts = words[0].strip(chars = {'/'}).split('/')
          try: msg = parseBiggestInt(parts[^1])
          except ValueError: discard
    l.state.addOtherBot(m.id, chat, msg, m.text, m.albumId)
  except CatchableError as e:
    l.say "   could not note the other bot's post: " & e.msg

proc enqueue*(l: Listener, m: TgMessage) =
  ## A live message. During bootstrap it waits until the backlog is done.
  if l.bootstrapping: l.held.add m
  else: l.queue.addLast m

proc processQueue*(l: Listener) {.async.} =
  ## Handle everything queued, one by one, marking each read afterwards.
  ## Stops early when `stop` is set; the rest stays unread for next time.
  while l.queue.len > 0 and not l.stop[]:
    let m = l.queue.popFirst
    var group = @[m]
    if m.albumId != 0 and not m.edited:
      # The pictures of an album arrive as separate messages, back to back.
      if l.queue.len == 0: await sleepAsync(l.albumWaitMs)
      while l.queue.len > 0 and l.queue.peekFirst.chatId == m.chatId and
          l.queue.peekFirst.albumId == m.albumId and not l.queue.peekFirst.edited:
        let x = l.queue.popFirst
        if group.allIt(it.id != x.id): group.add x      # the same picture twice: once
      # Telegram wants the ids in increasing order (forwardMessages)
      group.sort(proc (a, b: TgMessage): int = cmp(a.id, b.id))
    await l.handleGroup(group)
    if not l.dryRun:
      try: await l.tg.markRead(m.chatId, group.mapIt(it.id))
      except CatchableError as e: l.say "   could not mark read: " & e.msg
    if l.bootstrapping and l.backlogTotal > 0:
      # A progress bar doesn't suit a service whose output goes to the
      # journal; a count every few messages does.
      let before = l.backlogDone
      l.backlogDone = min(l.backlogDone + group.len, l.backlogTotal)
      if l.backlogDone == l.backlogTotal or
          l.backlogDone div max(l.progressEvery, 1) > before div max(l.progressEvery, 1):
        l.say &"backlog: {l.backlogDone}/{l.backlogTotal} checked"

proc bootstrap*(l: Listener) {.async.} =
  ## Handle every chat's unread messages, oldest first, then the live
  ## messages that arrived meanwhile.
  l.bootstrapping = true
  try:
    for chat in l.cfg.chats:
      if l.stop[]: break
      try:
        let u = await l.tg.unread(chat)
        if u.messages.len > 0:
          l.say &"{u.messages.len} unread in {u.messages[0].chatTitle}"
        if u.cut:
          await l.alert(&"{u.messages[0].chatTitle} had more than {u.messages.len} unread " &
                        "messages; only the newest were checked.")
        for m in u.messages: l.queue.addLast m
      except CatchableError as e:
        l.say &"   could not read chat {chat}: {e.msg} (will try again every minute)"
        l.retryChats.add chat
    l.backlogTotal = l.queue.len
    l.backlogDone = 0
    await l.processQueue()
  finally:
    l.bootstrapping = false
    for m in l.held: l.queue.addLast m
    l.held.setLen 0
  await l.processQueue()

proc retryFailedChats*(l: Listener) {.async.} =
  ## Read the backlog of chats that failed at startup (no connection yet, a
  ## timeout). Without this, their unread messages waited for the next
  ## restart. Messages already handled live meanwhile are skipped as seen.
  var still: seq[int64]
  for chat in l.retryChats:
    if l.stop[]:
      still.add chat
      continue
    try:
      let u = await l.tg.unread(chat)
      if u.messages.len > 0:
        l.say &"{u.messages.len} unread in {u.messages[0].chatTitle} (read on retry)"
      for m in u.messages: l.queue.addLast m
    except CatchableError as e:
      l.say &"   could not read chat {chat} again: {e.msg}"
      still.add chat
  l.retryChats = still

proc sendApproved*(l: Listener) {.async.} =
  ## Send the posts you approved in `telenotify review`. A failure puts the
  ## post back in the review queue, with the reason.
  for r in l.state.approvedReviews():
    if l.stop[]: return
    try:
      if not await l.tg.forward(l.cfg.targetChat, r.chatId, r.albumIds):
        var link = r.link
        if link == "": link = await l.tg.link(r.chatId, r.msgId)
        if link == "": raise newException(ValueError, "no link to the post")
        await l.tg.sendText(l.cfg.targetChat, "match: " & r.role & "\npost link: " & link)
      l.state.addPublished(r.chatId, r.msgId, r.text)
      discard l.state.decideReview(r.id, "sent", fromStatus = "to_send")
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
    if ticks mod 600 == 0 and l.retryChats.len > 0:   # about every minute
      await l.retryFailedChats()

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
  # "Updating" means connected and catching up on missed updates: not down
  if state in ["connectionStateReady", "connectionStateUpdating"]:
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
