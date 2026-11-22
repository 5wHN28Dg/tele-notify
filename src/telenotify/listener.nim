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
##   running bot sends the posts you approved (`sendApproved`) once their
##   30-second undo window is over (#62).
## * **Dry run** (`--dry-run` or `"dry_run": true`): judge and record
##   everything, but forward nothing and mark nothing read. That's stage 4:
##   running next to the Python bot to compare.
## * **Albums are one post.** The pictures of an album arrive as separate
##   messages; they're judged together (caption + text in every picture)
##   and forwarded together.
## * **Edits.** A rejected post whose text is edited later ("job coming
##   soon" → the real ad) is judged again. Published or reviewed posts are
##   left alone.
## * **Lost chats** (#16): if a watched chat bans you, you leave it, or it
##   can't be read any more, you're told once (checked at start-up, on
##   TDLib's live updates, and when its backlog can't be read).
## * **Downtime alerts** (#3, #16): a heartbeat is saved every minute; on
##   start-up, and when the connection comes back, a long gap is reported
##   to your Saved Messages.

import std/[asyncdispatch, deques, strutils, times, strformat, sequtils, algorithm, tables, sets]
import config, classify, gather, fetch, telegram, state, dedup, boards, notify, instagram, summary

type
  Listener* = ref object
    sendingReady*: bool        ## the worker runs: approved posts are sent (`sending_ready`, #62)
    cfg*: Config
    tg*: Telegram
    state*: State
    fetcher*: Fetcher
    ocr*: Ocr
    instagram*: Instagram   ## linked Instagram posts (#114); nil: off
    dryRun*: bool
    queue: Deque[TgMessage]
    bootstrapping: bool
    held: seq[TgMessage]          # live messages that arrived during bootstrap
    stop*: ref bool               # set to stop after the current message
    albumWaitMs*: int             # how long to wait for the rest of an album
    progressEvery*: int           # backlog progress line every this many messages (#11)
    backlogTotal, backlogDone: int
    retryChats: seq[int64]        # chats whose backlog couldn't be read yet (#5)
    retryEveryTicks*: int         # worker ticks (~0.1 s) between retries; backs off
    log*: proc (line: string) {.closure, gcsafe.}
    notify*: Notifier               ## your own bot (#122); nil: off (or `outbox`)
    outbox*: NotifyQueue            ## sends `notify`'s messages in the background
    awayPublished, awayReview: int  # the backlog's posts, for one summary
    retrying: bool                  # handling a backlog read on retry (summarized too)
    downSince: int64
    connection: string            # TDLib's last connection state ("" = none yet)
    selfChat: int64
    titles*: Table[int64, string]   # watched chats' titles, for alerts
    checkingAccess: bool            # the start-up access check is running
    accessChecked: HashSet[int64]   # chats it has looked at

proc newListener*(cfg: Config, tg: Telegram, st: State, fetcher: Fetcher, ocr: Ocr,
                  dryRun: bool): Listener =
  Listener(cfg: cfg, tg: tg, state: st, fetcher: fetcher, ocr: ocr, dryRun: dryRun,
           queue: initDeque[TgMessage](), stop: new bool,
           # live messages wait from the very start until the backlog is done
           # (they can arrive while chats are still being opened)
           bootstrapping: true, albumWaitMs: 1500, progressEvery: 25, retryEveryTicks: 600,
           log: proc (line: string) {.closure, gcsafe.} =
             echo now().format("yyyy-MM-dd HH:mm:ss"), "  ", line)

proc say(l: Listener, line: string) = l.log(line)

proc duration(seconds: int64): string =
  let h = seconds div 3600
  let m = (seconds mod 3600) div 60
  if h > 0: &"{h}h {m}m" else: &"{m}m"

proc notifying(l: Listener): bool =
  ## Is there a bot to tell (#122)? Makes the queue for `notify` if needed.
  if l.outbox == nil and l.notify != nil:
    l.outbox = newNotifyQueue(l.notify, proc (line: string) {.closure, gcsafe.} = l.say line)
  l.outbox != nil

proc tell(l: Listener, text: string) =
  ## Through your own bot, if there is one (#122): queued, sent in the
  ## background, so a slow or unreachable Bot API never holds up a post or
  ## an alert. A failure is logged.
  if l.notifying: l.outbox.push text

proc tellWithLink(l: Listener, text: string, chat, msg: int64) =
  ## `text` and the post's link, asked from TDLib when its turn comes (at
  ## most 5 s), in the background: a slow answer never holds up a post.
  if not l.notifying: return
  let tg = l.tg
  l.outbox.pushLater proc (): Future[string] {.async, closure, gcsafe.} =
    var link = ""
    try:
      let asking = tg.link(chat, msg)
      if await withTimeout(asking, sendTimeoutMs): link = asking.read
    except CatchableError: discard
    return text & (if link != "": "\n" & link else: "")

proc flushNotifications*(l: Listener, timeoutMs = 10_000) {.async.} =
  ## Wait (at most `timeoutMs`) until the queued notifications are sent: at
  ## a stop, and in tests.
  if l.outbox != nil: await l.outbox.flush(timeoutMs)

const reviewHint* = "Review: telenotify review, or the screen's Review tab (2)"
  ## the last line of a review message: what to do next

proc isolated(role: string): string =
  ## A role (often Arabic) inside an English line, isolated so neither
  ## reorders the other; "a post" when there's none.
  if role == "": "a post" else: "\u2068" & role & "\u2069"

proc tellSummary(l: Listener) =
  ## One message for a backlog (at start-up, or read on retry), instead of
  ## one per post. Only the kinds you get notified about.
  if l.awayPublished + l.awayReview == 0: return
  var parts: seq[string]
  if l.cfg.notifyPublished: parts.add &"{l.awayPublished} published"
  if l.cfg.notifyReview: parts.add &"{l.awayReview} for review"
  l.tell "📥 Backlog read: " & parts.join(", ") &
         (if l.awayReview > 0: "\n" & reviewHint else: "")

proc alertText*(text: string, dryRun: bool): string =
  ## An alert as sent to you. Two bots (a dry run beside the real one) both
  ## alert: say which.
  "⚠️ tele-notify" & (if dryRun: " [dry run]" else: "") & ": " & text

proc alert*(l: Listener, text: string) {.async.} =
  ## Log loudly, record it for the terminal screen and, if alerts are on,
  ## tell you in Saved Messages and through your bot.
  l.say "⚠️  " & text
  try: l.state.addAlert(text)        # for the terminal screen
  except CatchableError as e: l.say "   (could not record the alert: " & e.msg & ")"
  if not l.cfg.alerts: return
  try:
    if l.selfChat == 0: l.selfChat = await l.tg.selfChat()
    await l.tg.sendText(l.selfChat, alertText(text, l.dryRun))
  except CatchableError as e:
    l.say "   (could not send the alert: " & e.msg & ")"
  l.tell alertText(text, l.dryRun)

# ------------------------------------------------- lost access to a chat (#16)

proc accessKey(chat: int64): string = "chat_access:" & $chat

proc accessText*(chat: int64, title: string, a: ChatAccess, detail = ""): string =
  ## The alert for a watched chat this account can't read any more.
  case a.status
  of "banned":
    &"you were banned from {title}: tele-notify can't read it any more. " &
      "Remove it from config.json, or rejoin it if you can."
  of "left":
    &"you're no longer a member of {title}: tele-notify may not get its posts any more. " &
      "Rejoin it, or remove it from config.json."
  of "upgraded":
    &"{title} became a supergroup with a new id, {a.newChat}: tele-notify no longer gets " &
      &"its posts. Replace {chat} with {a.newChat} in \"chats\" in config.json."
  else:
    &"{title} can't be read" & (if detail != "": &" ({detail})" else: "") &
      ": tele-notify gets nothing from it any more. Remove it from config.json, or rejoin it."

proc onChatAccess*(l: Listener, chat: int64, access: ChatAccess, detail = "",
                   live = false) {.async.} =
  ## This account's access to a chat, from a live TDLib update, the
  ## start-up check or a failed backlog read. For a watched chat that can't
  ## be read any more, alert once per state: TDLib repeats these updates on
  ## every start, so the last state alerted is kept in the state database.
  ## Never raises. `live`: from a TDLib update (not the start-up check or a read).
  if chat notin l.cfg.chats or access.status == "": return
  let title = if access.title != "": access.title else: l.titles.getOrDefault(chat, &"chat {chat}")
  let key = accessKey(chat)
  var a = access
  var detail = detail
  try:
    let before = l.state.getMeta(key)
    if a.status == "ok":
      # Membership back. "unavailable" (the chat couldn't be read) is only
      # cleared by actually reading it (chatReadable): a member can still
      # get "can't access the chat".
      if before notin ["", "unavailable"]:
        l.state.setMeta(key, "")
        l.state.setChatStatus(chat, "ok")
        l.say &"✅ can read {title} again"
      return
    # While the start-up check runs, a live update for a chat it hasn't
    # reached yet is skipped: the check reads the same state (and knows a
    # public channel you never joined isn't a lost chat).
    if live and l.checkingAccess and chat notin l.accessChecked: return
    # A public channel (it has a username) is readable without being a
    # member: the bot opens every watched chat. Not an alert.
    if a.status == "left" and a.public:
      if before == "":
        l.state.setMeta(key, "left-public")
        l.say &"   not a member of {title} (fine: it's public)"
      return
    if a.status == "left" and before == "left-public":
      # it lost its username: private now, and this account isn't a member
      a = ChatAccess(title: a.title, status: "unavailable")
      detail = "it's no longer public and this account isn't a member"
    if before == a.status: return
    l.state.setMeta(key, a.status)   # before the await: no double alert
    l.state.setChatStatus(chat, a.status)
  except CatchableError as e:
    l.say &"   could not record access to {title}: {e.msg}"
    return
  await l.alert(accessText(chat, title, a, detail))

proc chatReadable(l: Listener, chat: int64, title: string) =
  ## The chat's messages were just read: clear an "unavailable" state.
  try:
    if l.state.getMeta(accessKey(chat)) == "unavailable":
      l.state.setMeta(accessKey(chat), "")
      l.state.setChatStatus(chat, "ok")
      let t = if title != "": title else: l.titles.getOrDefault(chat, &"chat {chat}")
      l.say &"✅ can read {t} again"
  except CatchableError as e:
    l.say &"   could not record access to chat {chat}: {e.msg}"

proc unreadFailed(l: Listener, chat: int64, e: ref CatchableError) {.async.} =
  ## Reading a chat failed. If it can't be read any more (not just
  ## offline), say so.
  if isAccessError(e.msg):
    # A known ban or removal already explains it: "unavailable" would
    # replace it and both would alert again after every restart.
    var known = ""
    try: known = l.state.getMeta(accessKey(chat))
    except CatchableError: discard
    if known in ["banned", "left", "upgraded"]: return
    # only TDLib's message, without the async traceback debug builds add
    await l.onChatAccess(chat, ChatAccess(status: "unavailable"), e.msg.splitLines()[0].strip)

proc checkWatchedChats*(l: Listener) {.async.} =
  ## At start-up: is this account still a member of every watched chat?
  l.checkingAccess = true
  defer: l.checkingAccess = false
  for chat in l.cfg.chats:
    if l.stop[]: break
    try:
      let a = await l.tg.chatAccess(chat)
      if a.title != "": l.titles[chat] = a.title
      await l.onChatAccess(chat, a)
    except CatchableError as e:
      if isAccessError(e.msg): await l.unreadFailed(chat, e)
      else: l.say &"   could not check access to chat {chat}: " & e.msg.splitLines()[0]
    l.accessChecked.incl chat

type HeldBack* = object of CatchableError
  ## Not sent on purpose, not a failure: the post goes to the review queue
  ## with this reason, for you to decide by hand.

const privateHeldBack* = "not posted: it isn't from a public channel (it's a private or members-only " &
  "chat, or a group), and only public channels' posts are republished in your public channel (#80). " &
  "Review's Send can't post it. If its admins agree, post it yourself in Telegram. Otherwise discard it."
const emptyHeldBack* = "not posted: the summary would say nothing (no role and no way to apply found " &
  "in the post). Review's Send can't post it. If you want it in your channel, post it yourself in " &
  "Telegram. Otherwise discard it."

proc fallbackText(l: Listener, chat, msg: int64, title, role, place, text,
                  known: string): Future[tuple[text, link: string]] {.async.} =
  ## What goes to your channel when Telegram won't forward a matched post
  ## (#80). `forward_fallback` "link": the old "match: <role>" + "post link:
  ## <link>" text (`known`, the post's link if already known, else asked).
  ## "summary": the bot's own summary (summary.nim), only for a public
  ## channel (not a group: the owner's T0 answer was "public channels
  ## only"), named, with the post's link asked again (a stored one may be from
  ## before the chat went public, or from an old username). Owner's
  ## decision, #135, 2026-11-22: "Public only; private to review".
  ## Raises HeldBack (to review, plainly) for anything but a public channel
  ## and for a summary that would say nothing; other errors (the
  ## access check or the link failed) go to review as errors. Returns the
  ## text and the link (for your notification).
  var link = known
  if l.cfg.forwardFallback == ffLink:
    if link == "": link = await l.tg.link(chat, msg)
    if link == "": raise newException(ValueError, "no link to the post")
    return ("match: " & role & "\npost link: " & link, link)
  let apply = applyMethods(text, @defaultJobPostingSites & l.cfg.extraJobSites)
  if role.strip == "" and apply.len == 0:
    raise newException(HeldBack, emptyHeldBack)
  var public = false
  var name = title
  try:
    let a = await l.tg.chatAccess(chat)
    public = a.public and a.channel
    if name == "": name = a.title
  except CatchableError as e:
    # calling a public chat private would send it to review for the wrong reason
    raise newException(ValueError, "could not tell whether the chat is public, so no summary was sent " &
                       "(" & problemText(e.msg) & "). Approve it again to retry")   # never "()"
  if not public: raise newException(HeldBack, privateHeldBack)
  try: link = await l.tg.link(chat, msg)
  except CatchableError as e:
    raise newException(ValueError, "no link to the post from Telegram (" & problemText(e.msg) &
                       "), so no summary was sent. Approve it again to retry")
  # TDLib's links to private chats are t.me/c/...: never in a public summary
  if link == "" or "/c/" in link:
    raise newException(ValueError, "Telegram gave no public link to the post, so no summary was sent. " &
                       "Approve it again to retry, or send it by hand")
  return (summaryText(role, place, apply, name, link), link)

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
                                prev.action, prev.textHash, albumId = y.albumId, postedAt = y.date,
                                caughtUp = y.caughtUp)
      return
    l.say &"✏️  {m.chatTitle} #{m.id} was edited: judging it again"
  var verdict, role, reasons, postLink = ""
  var action = "error"
  var queued = false       # in the review queue (it can fail in the error path)
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
    let g = await gather(post, l.fetcher, l.ocr, @defaultJobPostingSites & l.cfg.extraJobSites,
                         instagram = l.instagram)
    for n in g.notes:
      if n.startsWith("OCR failed"): photoProblem = n
    var unread: seq[string]
    if photoProblem != "": unread.add "the picture could not be read"
    if g.unreadable != "": unread.add g.unreadable   # a short post whose link was the whole ad
    let guess = unread.join(" and ") & ", so this is a guess"
    var d = classify(g.text, l.cfg)
    if d.verdict == vReject and unread.len > 0:
      # The picture is often the whole ad: unread, it's unknown, not bad.
      d.verdict = vReview
      d.reasons.add guess
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
        # forwarding is forbidden in this chat: the role and a link, or a summary (#80)
        try:
          let fallback = await l.fallbackText(m.chatId, m.id, m.chatTitle, role, d.place, g.text, postLink)
          postLink = fallback.link
          await l.tg.sendText(l.cfg.targetChat, fallback.text)
          action = if l.cfg.forwardFallback == ffSummary: "summarized" else: "linked"
          l.state.addPublished(m.chatId, m.id, g.text)
        except HeldBack as e:
          # not an error: you decide by hand (#135). Its own words only: a
          # debug build adds an async traceback to the message
          reasons.add "; " & problemText(e.msg)
          if postLink == "":
            try: postLink = await l.tg.link(m.chatId, m.id)
            except CatchableError: discard
          l.state.addReview(m.chatId, m.id, m.chatTitle, g.text, reasons, role, postLink, ids)
          action = "queued for review"
          queued = true
    of vReview:
      try: postLink = await l.tg.link(m.chatId, m.id)
      except CatchableError: discard
      l.state.addReview(m.chatId, m.id, m.chatTitle, g.text, reasons, role, postLink, ids)
      action = "queued for review"
      queued = true
    of vReject:
      action = "rejected"
  except CatchableError as e:
    # Don't lose the post: put it in the review queue with the error.
    # one line without "; ", so an approval failing the same way counts it (#142)
    reasons.add "; error: " & problemText(e.msg)
    action = "error, queued for review"
    l.say &"   ✗ {m.chatTitle} #{m.id}: {e.msg}"
    try:
      if postLink == "":
        try: postLink = await l.tg.link(m.chatId, m.id)
        except CatchableError: discard
      l.state.addReview(m.chatId, m.id, m.chatTitle, group.mapIt(it.text).join("\n"),
                        reasons.strip(chars = {';', ' '}), role, postLink, ids)
      queued = true
    except CatchableError as e2:
      l.say &"   could not queue it for review either: {e2.msg}"
  for x in group:
    try:
      l.state.markProcessed(x.chatId, x.id, x.chatTitle, verdict, role, reasons, action, fp,
                            rawText = x.text,   # compare; search in the terminal screen
                            albumId = x.albumId,
                            # forwarding time (#74): from when the post went up or, for
                            # an edit judged again, from the edit (unknown: left out)
                            postedAt = (if m.edited: x.editDate else: x.date),
                            caughtUp = group.anyIt(it.caughtUp))
    except CatchableError as e:
      l.say &"   could not record {x.chatTitle} #{x.id}: {e.msg}"

  # Your bot (#122): not in a dry run (it changes nothing); the backlog
  # after downtime is one summary at its end, not a ping per post.
  # Each kind can be turned off (notify_published, notify_review).
  let published = action in ["forwarded", "linked", "summarized"]
  let wanted = (published and l.cfg.notifyPublished) or (queued and l.cfg.notifyReview)
  if wanted and not l.dryRun:
    if l.bootstrapping or l.retrying:
      if published: inc l.awayPublished
      else: inc l.awayReview
    elif published:
      let text = "✅ Published: " & isolated(role) & "\n" & m.chatTitle
      if postLink == "": l.tellWithLink(text, m.chatId, m.id)   # a forward: its link is asked aside
      else: l.tell text & "\n" & postLink
    else:
      l.tell "🔎 For review: " & isolated(role) & "\n" & m.chatTitle &
             (if postLink != "": "\n" & postLink else: "") & "\n" & reviewHint
  let mark = case action
    of "forwarded", "linked", "summarized", "would publish": "✅"
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
    # "post link: " (the old text, any case) or "رابط المنشور: " (the
    # summary, #80), at the start of a line: the last such line, so no text
    # above it can stand in for it
    let lower = ("\n" & m.text).toLowerAscii
    var i = lower.rfind("\npost link: ")
    var label = "post link: "
    let j = lower.rfind("\n" & linkLabel)
    if j > i: (i, label) = (j, linkLabel)
    if chat == 0 and i >= 0:
      let words = m.text[i + label.len .. ^1].splitWhitespace
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

proc catchingUp(l: Listener): bool =
  ## Not simply connected: down, connecting, or Telegram replaying what was
  ## missed ("updating"). "" = no state reported yet.
  l.downSince > 0 or l.connection notin ["", "connectionStateReady"]

proc enqueue*(l: Listener, m: TgMessage) =
  ## A live message. During bootstrap it waits until the backlog is done.
  ## One that arrives while the backlog is read, or while the connection
  ## is down or catching up (TDLib replays the missed messages as new
  ## ones), counts as caught up, not live, in the forwarding time (#74).
  var m = m
  if l.bootstrapping or l.catchingUp: m.caughtUp = true
  if l.bootstrapping: l.held.add m
  else: l.queue.addLast m

proc processQueue*(l: Listener) {.async.} =
  ## Handle everything queued, one by one, marking each read afterwards.
  ## Stops early when `stop` is set; the rest stays unread for next time.
  while l.queue.len > 0 and not l.stop[]:
    let m = l.queue.popFirst
    var group = @[m]
    var popped = 1
    if m.albumId != 0 and not m.edited:
      # The pictures of an album arrive as separate messages, back to back.
      if l.queue.len == 0: await sleepAsync(l.albumWaitMs)
      while l.queue.len > 0 and l.queue.peekFirst.chatId == m.chatId and
          l.queue.peekFirst.albumId == m.albumId and not l.queue.peekFirst.edited:
        let x = l.queue.popFirst
        inc popped
        if group.allIt(it.id != x.id): group.add x      # the same picture twice: once
      # Telegram wants the ids in increasing order (forwardMessages)
      group.sort(proc (a, b: TgMessage): int = cmp(a.id, b.id))
    await l.handleGroup(group)
    l.chatReadable(m.chatId, m.chatTitle)   # a post arrived: the chat is readable
    # Marking a message read moves Telegram's read marker past everything
    # older: in a chat whose backlog couldn't be read yet, that backlog
    # would be lost to the retry. Its messages are marked once it works.
    if not l.dryRun and m.chatId notin l.retryChats:
      try: await l.tg.markRead(m.chatId, group.mapIt(it.id))
      except CatchableError as e: l.say "   could not mark read: " & e.msg
    if l.bootstrapping and l.backlogTotal > 0:
      # A progress bar doesn't suit a service whose output goes to the
      # journal; a count every few messages does.
      let before = l.backlogDone
      l.backlogDone = min(l.backlogDone + popped, l.backlogTotal)
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
        # only messages actually read prove access (no unread: history isn't fetched)
        if u.messages.len > 0: l.chatReadable(chat, u.messages[0].chatTitle)
        if u.messages.len > 0:
          l.say &"{u.messages.len} unread in {u.messages[0].chatTitle}"
        if u.cut:
          await l.alert(&"{u.messages[0].chatTitle} had more than {u.messages.len} unread " &
                        "messages; only the newest were checked.")
        for m in u.messages:
          var m = m
          m.caughtUp = true           # a backlog: its forwarding time is counted apart (#74)
          l.queue.addLast m
      except CatchableError as e:
        l.say &"   could not read chat {chat}: {e.msg} (will try again every minute)"
        l.retryChats.add chat
        await l.unreadFailed(chat, e)
    l.backlogTotal = l.queue.len
    l.backlogDone = 0
    l.awayPublished = 0
    l.awayReview = 0
    await l.processQueue()
    l.tellSummary()
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
  var read = 0
  for chat in l.retryChats:
    if l.stop[]:
      still.add chat
      continue
    try:
      let u = await l.tg.unread(chat)
      # only messages actually read prove access (no unread: history isn't fetched)
      if u.messages.len > 0: l.chatReadable(chat, u.messages[0].chatTitle)
      if u.messages.len > 0:
        l.say &"{u.messages.len} unread in {u.messages[0].chatTitle} (read on retry)"
      if u.cut:
        await l.alert(&"{u.messages[0].chatTitle} had more than {u.messages.len} unread " &
                      "messages; only the newest were checked.")
      for m in u.messages:
        var m = m
        m.caughtUp = true
        l.queue.addLast m
      read += u.messages.len
    except CatchableError as e:
      l.say &"   could not read chat {chat} again: {e.msg}"
      still.add chat
      await l.unreadFailed(chat, e)
  # a chat that keeps failing (left, banned) is tried less and less often,
  # up to every 30 minutes
  if still.len > 0 and still.len == l.retryChats.len:
    l.retryEveryTicks = min(l.retryEveryTicks * 2, 18_000)
  l.retryChats = still
  if read > 0:
    # that backlog now: one summary for it, not a ping per post (live
    # messages queued meanwhile are counted in it too)
    l.retrying = true
    l.awayPublished = 0
    l.awayReview = 0
    try: await l.processQueue()
    finally: l.retrying = false
    l.tellSummary()

proc sendApproved*(l: Listener, at = 0'i64) {.async.} =
  ## Send the posts you approved (`telenotify review`, the terminal screen)
  ## whose undo window is over at `at` (0: now). Each is claimed first
  ## (`claimReview`): one you undid meanwhile is skipped, and one being sent
  ## can't be undone any more. A failure puts the post back in the review
  ## queue, with the reason.
  let at = if at == 0: getTime().toUnix else: at
  for r in l.state.dueReviews(at):
    if l.stop[]: return
    if not l.state.claimReview(r.id, at): continue   # undone since it was listed
    var link = r.link
    try:
      if not await l.tg.forward(l.cfg.targetChat, r.chatId, r.albumIds):
        # the place: the filter's, from the post's text as queued
        let place = if l.cfg.forwardFallback == ffSummary: classify(r.text, l.cfg).place else: ""
        let fallback = await l.fallbackText(r.chatId, r.msgId, r.chatTitle, r.role, place, r.text, link)
        link = fallback.link
        await l.tg.sendText(l.cfg.targetChat, fallback.text)
      l.state.addPublished(r.chatId, r.msgId, r.text)
      if not l.state.decideReview(r.id, "sent", fromStatus = "sending"):
        # only another telenotify run on the same state file could have moved it
        l.say &"   {r.chatTitle} #{r.msgId} was sent, but could not be marked sent: it is no longer " &
              "'sending' (is a second telenotify run using this state file?)"
      l.say &"✅ {r.chatTitle} #{r.msgId}: sent after review"
      if l.cfg.notifyPublished:
        l.tell "✅ Published after your review: " & isolated(r.role) & "\n" &
               r.chatTitle & (if link != "": "\n" & link else: "")
    except HeldBack as e:
      # held back again (#80): its own words only, so a reason it already
      # has (from the live post, or an earlier approval) isn't added again
      l.say &"   reviewed {r.chatTitle} #{r.msgId}: {e.msg.splitLines()[0]}"
      try: l.state.reopenReview(r.id, e.msg, heldBack = true)
      except CatchableError: discard
    except CatchableError as e:
      l.say &"   could not send reviewed {r.chatTitle} #{r.msgId}: {e.msg}"
      try: l.state.reopenReview(r.id, e.msg)
      except CatchableError: discard

proc writeSendingReady*(l: Listener) =
  ## `sending_ready` as this bot is: "1" while its worker runs (never in a dry
  ## run). Written by the worker and again by every heartbeat, so a second
  ## `telenotify run` on the same file can't leave it wrong for long.
  try: l.state.setMeta("sending_ready", if l.sendingReady and not l.dryRun: "1" else: "0")
  except CatchableError as e: l.say "   could not update the state database: " & e.msg

proc worker*(l: Listener) {.async.} =
  ## Process live messages as they arrive, and approved reviews every few
  ## seconds, until `stop` is set. While it runs, the state's `sending_ready`
  ## is "1": the terminal screen promises a send only then (#62).
  proc ready(on: bool) =
    l.sendingReady = on
    l.writeSendingReady()
  ready(true)
  defer: ready(false)
  var ticks = 0
  while not l.stop[]:
    if l.queue.len > 0: await l.processQueue()
    else: await sleepAsync(100)
    inc ticks
    if ticks mod 50 == 0 and not l.dryRun:
      try: await l.sendApproved()
      except CatchableError as e: l.say "   reviewed posts: " & e.msg
    if ticks mod max(l.retryEveryTicks, 1) == 0 and l.retryChats.len > 0:
      await l.retryFailedChats()   # about every minute at first

proc checkDowntimeAtStart*(l: Listener) {.async.} =
  let last = l.state.getMeta("last_alive")
  if last == "": return
  let gap = getTime().toUnix - parseBiggestInt(last)
  if gap > l.cfg.alertAfterMinutes * 60:
    let since = fromUnix(parseBiggestInt(last)).local.format("yyyy-MM-dd HH:mm")
    await l.alert(&"was not running for {duration(gap)} (since {since}). Back now.")

proc forgetOldTexts*(l: Listener) =
  ## `keep_text_days`: clear old post texts. Not in a dry run: `compare`
  ## matches the other bot's posts by text, over any number of days.
  if l.dryRun: return
  try: l.state.forgetOldTexts(l.cfg.keepTextDays)
  except CatchableError as e: l.say "   could not clear old post texts: " & e.msg

proc beat*(l: Listener) =
  ## One heartbeat: "alive now", and whether approved posts are being sent.
  try: l.state.setMeta("last_alive", $getTime().toUnix)
  except CatchableError as e: l.say "   heartbeat: " & e.msg
  l.writeSendingReady()

proc heartbeat*(l: Listener) {.async.} =
  var lastCleanup = 0'i64
  while not l.stop[]:
    l.beat()
    if getTime().toUnix - lastCleanup >= 86_400:   # at start, then daily
      lastCleanup = getTime().toUnix
      l.forgetOldTexts()
    for _ in 0 ..< 60:
      if l.stop[]: break
      await sleepAsync(1000)

proc onConnectionState*(l: Listener, state: string) =
  ## Called with TDLib's connection state ("connectionStateReady", ...).
  let t = getTime().toUnix
  l.connection = state
  # for the terminal screen: "ready", "updating", "connecting", ...
  try: l.state.setMeta("connection", state.replace("connectionState", "").toLowerAscii)
  except CatchableError: discard
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
