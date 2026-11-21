## The whole bot against a fake Telegram: no account, no network.

import std/[unittest, asyncdispatch, os, tables, sets, strutils, times, json, sequtils]
import telenotify/[listener, telegram, state, config, gather, fetch, ocr, dedup, notify, instagram]
import db_connector/db_sqlite

notifyPaceMs = 0   # no pacing between messages to your bot here (tnotify tests it)

const target = -999'i64

type FakeTg = ref object
  chats: Table[int64, seq[TgMessage]]
  noForward: HashSet[int64]          # chats with forwarding disabled
  brokenPhotos: HashSet[int]
  forwarded, read: seq[(int64, int64)]
  texts: seq[(int64, string)]
  duringUnread: proc () {.closure, gcsafe.}     # e.g. a live message arriving
  afterForward: proc () {.closure, gcsafe.}
  forwardFails, linkFails: bool
  cutChats: HashSet[int64]
  duringAccess: proc () {.closure, gcsafe.}   # e.g. a live update racing the check
  unreadFails: HashSet[int64]        # chats whose backlog can't be read (offline)
  readMarker: bool                   # like Telegram: unread = after the newest read id
  unreadError: Table[int64, string]  # chats whose backlog read fails with this TDLib error
  access: Table[int64, ChatAccess]   # chatAccess answers; default: a member
  accessError: Table[int64, string]  # chats chatAccess fails for
  linkCalls: int
  linkHangs: bool                    # link never answers

proc toTelegram(f: FakeTg): Telegram =
  result = Telegram()
  result.unread = proc (chat: int64): Future[Unread] {.async, closure, gcsafe.} =
    {.cast(gcsafe).}:
      if f.duringUnread != nil: f.duringUnread()
      if chat in f.unreadFails: raise newException(IOError, "timeout")
      if chat in f.unreadError: raise newException(IOError, f.unreadError[chat])
      var ms = f.chats.getOrDefault(chat)
      if f.readMarker:
        var upTo = 0'i64
        for (c, id) in f.read:
          if c == chat: upTo = max(upTo, id)
        var after: seq[TgMessage]
        for m in ms:
          if m.id > upTo: after.add m
        ms = after
      return Unread(messages: ms, cut: chat in f.cutChats)
  result.markRead = proc (chat: int64, ids: seq[int64]): Future[void] {.async, closure, gcsafe.} =
    {.cast(gcsafe).}:
      for id in ids: f.read.add (chat, id)
  result.downloadPhoto = proc (fileId: int): Future[string] {.async, closure, gcsafe.} =
    {.cast(gcsafe).}:
      if fileId in f.brokenPhotos: raise newException(IOError, "download failed")
      return readFile(currentSourcePath.parentDir / "fixtures" / "ocr_arabic.png")
  result.forward = proc (t, chat: int64, ids: seq[int64]): Future[bool] {.async, closure, gcsafe.} =
    {.cast(gcsafe).}:
      if f.forwardFails: raise newException(IOError, "FLOOD_WAIT")
      if chat in f.noForward: return false
      for id in ids: f.forwarded.add (chat, id)
      if f.afterForward != nil: f.afterForward()
      return true
  result.link = proc (chat, id: int64): Future[string] {.async, closure, gcsafe.} =
    {.cast(gcsafe).}:
      inc f.linkCalls
      if f.linkHangs: await newFuture[void]("hangs")
      if f.linkFails: raise newException(IOError, "no link")
    return "https://t.me/c/" & $(-chat) & "/" & $id
  result.sendText = proc (chat: int64, text: string): Future[void] {.async, closure, gcsafe.} =
    {.cast(gcsafe).}: f.texts.add (chat, text)
  result.getMessage = proc (chat, id: int64): Future[TgMessage] {.async, closure, gcsafe.} =
    {.cast(gcsafe).}:
      for m in f.chats.getOrDefault(chat):
        if m.id == id: return m
      raise newException(IOError, "message not found")
  result.linkTarget = proc (url: string): Future[(int64, int64)] {.async, closure, gcsafe.} =
    if "t.me/basrajobs/" notin url: raise newException(IOError, "unknown link")
    return (-5'i64, parseBiggestInt(url.split('/')[^1]))
  result.selfChat = proc (): Future[int64] {.async, closure, gcsafe.} = return 42
  result.chatAccess = proc (chat: int64): Future[ChatAccess] {.async, closure, gcsafe.} =
    {.cast(gcsafe).}:
      if f.duringAccess != nil: f.duringAccess()
      if chat in f.accessError: raise newException(IOError, f.accessError[chat])
      return f.access.getOrDefault(chat, ChatAccess(title: "chat" & $(-chat), status: "ok"))

proc noFetch(): Fetcher =
  result = proc (url: string): Future[Fetched] {.closure, gcsafe.} =
    var fut = newFuture[Fetched]("nofetch")
    fut.complete Fetched(ok: false, error: "offline test")
    fut

proc msg(chat, id: int64, text: string, photo = 0): TgMessage =
  TgMessage(chatId: chat, id: id, text: text, photoFileId: photo, chatTitle: "chat" & $(-chat))

const
  good = "مطلوب مهندس كهرباء حديث التخرج للعمل في البصرة"
  unclear = "شركة في البصرة تعلن عن حاجتها الى مهندس كهرباء\nارسال السيرة الذاتية"
  bad = "مطلوب محاسب خبرة 5 سنوات في بغداد"

proc afterWindow(): int64 =
  ## A time when every post approved so far is past its undo window (#62).
  getTime().toUnix + undoWindowSeconds + 1

var n = 0
proc statePathOf(i: int): string = getTempDir() / ("tn-listener-" & $i) / "state.sqlite3"
proc setup(f: FakeTg, dryRun = false): (Listener, State) =
  inc n
  let dir = getTempDir() / ("tn-listener-" & $n)
  removeDir(dir)
  var j = parseFile(currentSourcePath.parentDir / "keywords.json")
  j["target_chat"] = %target
  j["chats"] = %[-1'i64, -2]
  let cfg = parseConfig(j)
  let st = openState(dir / "state.sqlite3")
  var l = newListener(cfg, f.toTelegram, st, noFetch(), defaultOcr(), dryRun)
  l.log = proc (line: string) {.closure, gcsafe.} = discard
  (l, st)


suite "listener":
  test "backlog: oldest first, publish / review / reject, then marked read":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 10, good), msg(-1, 11, unclear), msg(-1, 12, bad)]
    let (l, st) = setup(f)
    waitFor l.bootstrap()
    check f.forwarded == @[(-1'i64, 10'i64)]
    check st.pendingReviews().len == 1 and st.pendingReviews()[0].msgId == 11
    check st.pendingReviews()[0].link == "https://t.me/c/1/11"
    check f.read == @[(-1'i64, 10'i64), (-1'i64, 11'i64), (-1'i64, 12'i64)]
    check st.seen(-1, 12)

  test "the backlog reports its progress (#11)":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 10, good), msg(-1, 11, unclear)]
    f.chats[-2] = @[msg(-2, 5, bad)]
    let (l, _) = setup(f)
    l.progressEvery = 2
    var lines: seq[string]
    l.log = proc (line: string) {.closure, gcsafe.} =
      {.cast(gcsafe).}: lines.add line
    waitFor l.bootstrap()
    var progress: seq[string]
    for x in lines:
      if x.startsWith("backlog: "): progress.add x
    check progress == @["backlog: 2/3 checked", "backlog: 3/3 checked"]

  test "a chat whose backlog couldn't be read is tried again (#5)":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 10, good)]
    f.chats[-2] = @[msg(-2, 5, "We are hiring a junior network technician in Basra. Fresh graduates welcome.")]
    f.unreadFails.incl -2
    let (l, _) = setup(f)
    waitFor l.bootstrap()
    check f.forwarded == @[(-1'i64, 10'i64)]
    waitFor l.retryFailedChats()          # still offline: kept for later
    waitFor l.processQueue()
    check f.forwarded.len == 1
    f.unreadFails.clear
    waitFor l.retryFailedChats()
    waitFor l.processQueue()
    check f.forwarded == @[(-1'i64, 10'i64), (-2'i64, 5'i64)]
    waitFor l.retryFailedChats()          # nothing left to retry
    waitFor l.processQueue()
    check f.forwarded.len == 2

  test "a live message doesn't hide the backlog of a chat waiting for a retry":
    let f = FakeTg(readMarker: true)
    f.chats[-2] = @[msg(-2, 5, "We are hiring a junior network technician in Basra. Fresh graduates welcome.")]
    f.unreadFails.incl -2
    let (l, _) = setup(f)
    waitFor l.bootstrap()
    let live = msg(-2, 9, bad)                      # arrives while -2 is still failing
    f.chats[-2].add live
    l.enqueue live
    waitFor l.processQueue()
    f.unreadFails.clear
    waitFor l.retryFailedChats()
    waitFor l.processQueue()
    check f.forwarded == @[(-2'i64, 5'i64)]
    check (-2'i64, 9'i64) in f.read                 # marked once the retry worked

  test "a retried chat whose backlog was cut short is reported":
    let f = FakeTg()
    f.chats[-2] = @[msg(-2, 5, bad)]
    f.unreadFails.incl -2
    f.cutChats.incl -2
    let (l, _) = setup(f)
    waitFor l.bootstrap()
    f.unreadFails.clear
    waitFor l.retryFailedChats()
    check f.texts.len == 1 and "only the newest were checked" in f.texts[0][1]

  test "a chat that forbids forwarding gets the role and a link":
    let f = FakeTg()
    f.noForward.incl -2
    f.chats[-2] = @[msg(-2, 5, good)]
    let (l, _) = setup(f)
    waitFor l.bootstrap()
    check f.forwarded.len == 0
    check f.texts.len == 1 and f.texts[0][0] == target
    check "post link: https://t.me/c/2/5" in f.texts[0][1]

  test "the same ad cross-posted in two channels is published once":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 1, good & " 🔥")]
    f.chats[-2] = @[msg(-2, 1, good)]
    let (l, st) = setup(f)
    waitFor l.bootstrap()
    check f.forwarded.len == 1
    check st.stats(1).len == 2

  test "a message seen before is skipped (restart, backlog vs live)":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 1, good)]
    let (l, _) = setup(f)
    waitFor l.bootstrap()
    l.enqueue msg(-1, 1, good)
    waitFor l.processQueue()
    check f.forwarded.len == 1

  test "each post's own Telegram date is recorded; backlog posts are counted apart from live ones (#74)":
    let f = FakeTg()
    let posted = getTime().toUnix - 120
    var a = msg(-1, 1, good)             # in the start-up backlog: caught up
    a.date = posted
    var r = msg(-1, 2, bad)
    r.date = posted - 5
    f.chats[-1] = @[a, r]
    let (l, st) = setup(f)
    waitFor l.bootstrap()
    var live = msg(-2, 3, "We are hiring a junior network technician in Basra. Fresh graduates welcome.")  # live
    live.date = getTime().toUnix - 2
    l.enqueue live
    waitFor l.processQueue()
    check f.forwarded == @[(-1'i64, 1'i64), (-2'i64, 3'i64)]
    check st.postedAt(-1, 1) == posted and st.postedAt(-1, 2) == posted - 5
    check st.caughtUp(-1, 1) and st.caughtUp(-1, 2) and not st.caughtUp(-2, 3)
    let lat = st.latency(1)
    check lat.caughtUp.count == 1 and lat.caughtUp.median in 120'i64 .. 130
    check lat.live.count == 1 and lat.live.median in 2'i64 .. 12
    check lat.reviewed.count == 0

  test "live messages held while the backlog is read are caught up, not live (#74)":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 10, good)]
    let (l, st) = setup(f)
    var fired = false
    f.duringUnread = proc () {.closure, gcsafe.} =
      {.cast(gcsafe).}:
        if not fired:
          fired = true
          l.enqueue msg(-2, 11, "We are hiring a junior network technician in Basra. Fresh graduates welcome.")
    waitFor l.bootstrap()
    check f.forwarded == @[(-1'i64, 10'i64), (-2'i64, 11'i64)]
    check st.caughtUp(-2, 11)

  test "messages that arrive while the connection is down or catching up are caught up, not live (#74)":
    let f = FakeTg()
    let (l, st) = setup(f)
    waitFor l.bootstrap()
    const tech = "We are hiring a junior network technician in Basra. Fresh graduates welcome."
    l.onConnectionState("connectionStateConnecting")          # lost
    l.enqueue msg(-2, 1, tech)
    l.onConnectionState("connectionStateUpdating")            # back: TDLib replays what was missed
    l.enqueue msg(-2, 2, tech & " Zubair")
    l.onConnectionState("connectionStateReady")               # caught up
    l.enqueue msg(-2, 3, tech & " Umm Qasr")
    waitFor l.processQueue()
    check st.seen(-2, 1) and st.seen(-2, 2) and st.seen(-2, 3)
    check st.caughtUp(-2, 1) and st.caughtUp(-2, 2) and not st.caughtUp(-2, 3)

  test "a chat's backlog read on retry is caught up too (#74)":
    let f = FakeTg()
    f.chats[-2] = @[msg(-2, 5, "We are hiring a junior network technician in Basra. Fresh graduates welcome.")]
    f.unreadFails.incl -2
    let (l, st) = setup(f)
    waitFor l.bootstrap()
    f.unreadFails.clear
    waitFor l.retryFailedChats()
    waitFor l.processQueue()
    check f.forwarded == @[(-2'i64, 5'i64)]
    check st.caughtUp(-2, 5)

  test "an edited post judged again is timed from its edit (#74)":
    let f = FakeTg()
    var teaser = msg(-1, 30, "اعلان وظائف قريبا")
    teaser.date = getTime().toUnix - 6 * 3600     # the teaser went up 6 hours ago
    f.chats[-1] = @[teaser]
    let (l, st) = setup(f)
    waitFor l.bootstrap()
    check st.previous(-1, 30).action == "rejected"
    var real = msg(-1, 30, good & " حقل الرميلة")
    real.edited = true
    real.date = teaser.date
    real.editDate = getTime().toUnix - 30          # edited into the real ad 30 s ago
    l.enqueue real
    waitFor l.processQueue()
    check st.previous(-1, 30).action == "forwarded"
    check st.postedAt(-1, 30) == real.editDate and not st.caughtUp(-1, 30)
    check st.latency(1).live.median in 30'i64 .. 40
    var noDate = msg(-1, 31, "اعلان وظائف قريبا")  # an edit without its time: left out
    f.chats[-1].add noDate
    let (l2, st2) = setup(f)
    waitFor l2.bootstrap()
    noDate.text = good
    noDate.edited = true
    noDate.date = 1_700_000_000
    l2.enqueue noDate
    waitFor l2.processQueue()
    check st2.previous(-1, 31).action == "forwarded" and st2.postedAt(-1, 31) == 0

  test "dry run: judged and recorded, nothing forwarded or marked read":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 1, good), msg(-1, 2, unclear)]
    let (l, st) = setup(f, dryRun = true)
    waitFor l.bootstrap()
    check f.forwarded.len == 0 and f.read.len == 0 and f.texts.len == 0
    check st.seen(-1, 1) and st.pendingReviews().len == 1

  test "a failing message doesn't stop the others; its photo failure is noted":
    let f = FakeTg()
    f.brokenPhotos.incl 7
    f.chats[-1] = @[msg(-1, 1, "", photo = 7), msg(-1, 2, good)]
    let (l, st) = setup(f)
    waitFor l.bootstrap()
    check st.seen(-1, 1)
    check f.forwarded == @[(-1'i64, 2'i64)]

  test "text in a photo is read (OCR) and can publish":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 3, "", photo = 1)]
    let (l, _) = setup(f)
    waitFor l.bootstrap()
    check f.forwarded == @[(-1'i64, 3'i64)]

  test "downtime alerts: at start-up, and when the connection comes back":
    let f = FakeTg()
    let (l, st) = setup(f)
    st.setMeta("last_alive", $(getTime().toUnix - 3 * 3600))
    waitFor l.checkDowntimeAtStart()
    check f.texts.len == 1 and f.texts[0][0] == 42 and "was not running for 3h" in f.texts[0][1]
    var lines: seq[string]
    l.log = proc (line: string) {.closure, gcsafe.} =
      {.cast(gcsafe).}: lines.add line
    l.onConnectionState("connectionStateUpdating")   # connected, catching up: fine
    check lines.len == 0
    l.onConnectionState("connectionStateWaitingForNetwork")
    l.onConnectionState("connectionStateReady")      # back at once: only logged
    waitFor sleepAsync(50)
    check f.texts.len == 1

  test "live messages that arrive during the backlog wait until it's done":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 10, good)]
    let (l, _) = setup(f)
    var fired = false
    f.duringUnread = proc () {.closure, gcsafe.} =
      {.cast(gcsafe).}:
        if not fired:
          fired = true
          l.enqueue msg(-1, 11, good & " Rumaila")
    waitFor l.bootstrap()
    check f.read == @[(-1'i64, 10'i64), (-1'i64, 11'i64)]   # never marked past the backlog

  test "a stop request is honoured after the current message":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 1, good), msg(-1, 2, good & " Rumaila")]
    let (l, st) = setup(f)
    f.afterForward = proc () {.closure, gcsafe.} =
      {.cast(gcsafe).}: l.stop[] = true
    waitFor l.bootstrap()
    check f.forwarded == @[(-1'i64, 1'i64)]
    check not st.seen(-1, 2) and f.read == @[(-1'i64, 1'i64)]   # handled next time

  test "a post that fails to forward is not lost: it goes to the review queue":
    let f = FakeTg()
    f.forwardFails = true
    f.chats[-1] = @[msg(-1, 1, good)]
    let (l, st) = setup(f)
    waitFor l.bootstrap()
    let r = st.pendingReviews()
    check r.len == 1 and r[0].msgId == 1 and "FLOOD_WAIT" in r[0].reasons

  test "posts approved in review are sent by the running bot":
    let f = FakeTg()
    f.noForward.incl -2
    let (l, st) = setup(f)
    st.addReview(-1, 7, "chat1", unclear, "no level", "مهندس كهرباء", "")
    st.addReview(-2, 8, "chat2", unclear, "no level", "مهندس كهرباء", "")
    for r in st.pendingReviews(): check st.decideReview(r.id, "to_send")
    waitFor l.sendApproved(afterWindow())
    check f.forwarded == @[(-1'i64, 7'i64)]
    check f.texts.len == 1 and "post link: https://t.me/c/2/8" in f.texts[0][1]   # link fetched now
    check st.approvedReviews().len == 0 and st.pendingReviews().len == 0
    check duplicateOf(unclear, st.recentPublished()) != ""

  test "an approved post waits out its 30 s undo window before it is sent (#62)":
    let f = FakeTg()
    let (l, st) = setup(f)
    st.addReview(-1, 7, "chat1", unclear, "no level", "مهندس كهرباء", "")
    let t0 = getTime().toUnix
    check st.decideReview(st.pendingReviews()[0].id, "to_send")
    let r = st.approvedReviews()
    check r.len == 1 and r[0].sendAfter >= t0 + undoWindowSeconds
    waitFor l.sendApproved(r[0].sendAfter - 1)        # the window is still open
    check f.forwarded.len == 0 and st.approvedReviews().len == 1
    waitFor l.sendApproved(r[0].sendAfter)            # it's over
    check f.forwarded == @[(-1'i64, 7'i64)] and st.approvedReviews().len == 0

  test "undo racing the send: the post being sent stays sent, one undone in time is never sent (#62, #76)":
    let f = FakeTg()
    let (l, st) = setup(f)
    let path = statePathOf(n)
    st.addReview(-1, 7, "chat1", unclear, "no level", "role", "")
    st.addReview(-1, 8, "chat1", unclear & " 2", "no level", "role", "")
    var ids: seq[int64]
    for r in st.pendingReviews():
      check st.decideReview(r.id, "to_send")
      ids.add r.id
    # the terminal screen, another process, presses u on both while the bot
    # is forwarding the first: its undo is one UPDATE on 'to_send' (store.py)
    var undone = new seq[string]
    f.afterForward = proc () {.closure, gcsafe.} =
      {.cast(gcsafe).}:
        if undone[].len > 0: return
        let screen = open(path, "", "", "")
        defer: screen.close()
        for id in ids:
          screen.exec(sql"""UPDATE reviews SET status = 'pending', decided_at = NULL, send_after = NULL
                            WHERE id = ? AND status = 'to_send'""", id)
          undone[].add screen.getValue(sql"SELECT changes()")
    waitFor l.sendApproved(afterWindow())
    check undone[] == @["0", "1"]                      # the first was claimed; the second wasn't yet
    check f.forwarded == @[(-1'i64, 7'i64)]            # the undone one never went out
    let db = open(path, "", "", "")
    defer: db.close()
    check db.getValue(sql"SELECT status FROM reviews WHERE id = ?", ids[0]) == "sent"
    check db.getValue(sql"SELECT status FROM reviews WHERE id = ?", ids[1]) == "pending"

  test "an approved post that can't be sent goes back to the queue with the reason":
    let f = FakeTg()
    f.noForward.incl -2
    f.linkFails = true
    let (l, st) = setup(f)
    st.addReview(-2, 8, "chat2", unclear, "no level", "role", "")
    check st.decideReview(st.pendingReviews()[0].id, "to_send")
    waitFor l.sendApproved(afterWindow())
    let r = st.pendingReviews()
    check r.len == 1 and "could not send: no link" in r[0].reasons

  test "messages arriving before the backlog starts also wait for it":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 10, good), msg(-1, 11, unclear)]
    let (l, _) = setup(f)
    l.enqueue msg(-1, 12, bad)            # arrived while chats were being opened
    f.afterForward = proc () {.closure, gcsafe.} =
      {.cast(gcsafe).}: l.stop[] = true
    waitFor l.bootstrap()
    check f.read == @[(-1'i64, 10'i64)]   # nothing marked read past 11

  test "an image-only post whose picture can't be read goes to review, not reject":
    let f = FakeTg()
    f.brokenPhotos.incl 9
    f.chats[-1] = @[msg(-1, 1, "", photo = 9)]
    let (l, st) = setup(f)
    waitFor l.bootstrap()
    let r = st.pendingReviews()
    check r.len == 1 and "could not be read" in r[0].reasons

  test "a short post whose only substance is an unreadable Instagram link goes to review (#114)":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 1, "التفاصيل في المنشور https://www.instagram.com/p/DAbc123/"),
                    msg(-1, 2, bad & "، دوام كامل في شركة تجارية كبرى، يشترط اجادة برامج المحاسبة " &
                               "واللغة الانكليزية، ارسال السيرة الذاتية https://www.instagram.com/p/DAbc124/")]
    let (l, st) = setup(f)
    l.instagram = kittygramReader("http://127.0.0.1:8430", noFetch(), noFetch(), defaultOcr())
    waitFor l.bootstrap()
    let r = st.pendingReviews()
    check r.len == 1 and r[0].msgId == 1   # a long post speaks for itself
    check "the linked Instagram post could not be read, so this is a guess" in r[0].reasons
    check "front end failed: offline test" in r[0].reasons

  test "a photo that can't be read and an unreadable Instagram link: the reason names both":
    let f = FakeTg()
    f.brokenPhotos.incl 9
    f.chats[-1] = @[msg(-1, 1, "التفاصيل https://www.instagram.com/p/DAbc125/", photo = 9)]
    let (l, st) = setup(f)
    l.instagram = kittygramReader("http://127.0.0.1:8430", noFetch(), noFetch(), defaultOcr())
    waitFor l.bootstrap()
    let r = st.pendingReviews()
    check r.len == 1
    check "the picture could not be read and the linked Instagram post could not be read, so this is a guess" in
      r[0].reasons

  test "a backlog too big to read in full is reported":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 1, bad)]
    f.cutChats.incl -1
    let (l, _) = setup(f)
    waitFor l.bootstrap()
    check f.texts.len == 1 and "only the newest were checked" in f.texts[0][1]

  test "an album is judged and forwarded as one post":
    let f = FakeTg()
    var parts = @[msg(-1, 20, "صور الاعلان"), msg(-1, 21, "", photo = 1), msg(-1, 22, "", photo = 1)]
    for p in parts.mitems: p.albumId = 777
    f.chats[-1] = parts & @[msg(-1, 23, bad)]
    let (l, st) = setup(f)
    waitFor l.bootstrap()
    check f.forwarded == @[(-1'i64, 20'i64), (-1'i64, 21'i64), (-1'i64, 22'i64)]
    check st.seen(-1, 21) and st.seen(-1, 22) and st.seen(-1, 23)
    check f.read.len == 4
    check st.recentPublished().len == 1

  test "album pictures arriving a moment apart are still grouped":
    let f = FakeTg()
    let (l, _) = setup(f)
    waitFor l.bootstrap()                      # nothing unread; now live
    l.albumWaitMs = 400
    var a = msg(-1, 40, good)
    a.albumId = 9
    var b = msg(-1, 41, "", photo = 1)
    b.albumId = 9
    l.enqueue a
    let p = l.processQueue()
    waitFor sleepAsync(100)
    l.enqueue b
    waitFor p
    check f.forwarded == @[(-1'i64, 40'i64), (-1'i64, 41'i64)]

  test "a rejected post edited into a job ad is judged again; others are not":
    let f = FakeTg()
    const other = "Hiring a junior Electrical Engineer in Basra, fresh graduates welcome"
    f.chats[-1] = @[msg(-1, 30, "اعلان وظائف قريبا"), msg(-1, 31, other)]
    let (l, st) = setup(f)
    waitFor l.bootstrap()
    check f.forwarded == @[(-1'i64, 31'i64)]
    var same = msg(-1, 30, "اعلان وظائف قريبا")     # e.g. a link preview loaded: same text
    same.edited = true
    l.enqueue same
    var again = msg(-1, 31, other & " (updated)")  # a published post edited: left alone
    again.edited = true
    l.enqueue again
    waitFor l.processQueue()
    check f.forwarded.len == 1
    var real = msg(-1, 30, good & " حقل الرميلة")
    real.edited = true
    l.enqueue real
    waitFor l.processQueue()
    check f.forwarded == @[(-1'i64, 31'i64), (-1'i64, 30'i64)]
    check st.previous(-1, 30).action == "forwarded"

  test "an edit of a message never seen is ignored (it would forward old posts)":
    let f = FakeTg()
    let (l, _) = setup(f)
    waitFor l.bootstrap()
    var m = msg(-2, 50, good)
    m.edited = true
    l.enqueue m
    waitFor l.processQueue()
    check f.forwarded.len == 0

  test "dry run: the Python bot's posts in the channel are noted, with where they came from":
    let f = FakeTg()
    let (l, st) = setup(f, dryRun = true)
    var fwd = msg(target, 900, good)
    (fwd.originChat, fwd.originMsg) = (-1'i64, 10'i64)
    waitFor l.noteOtherBot(fwd)
    waitFor l.noteOtherBot(msg(target, 901, "match: electrical engineer\npost link: https://t.me/basrajobs/77"))
    waitFor l.noteOtherBot(msg(target, 902, "match: x\npost link: https://t.me/unknown/1"))
    let notes = st.otherBotSince(0)
    check notes.len == 3
    check (notes[0].originChat, notes[0].originMsg) == (-1'i64, 10'i64)
    check (notes[1].originChat, notes[1].originMsg) == (-5'i64, 77'i64)
    check notes[2].originChat == 0 and notes[2].originMsg == 1   # chat unknown, id kept

  test "album parts arriving after their first picture was handled are not judged again":
    let f = FakeTg()
    var parts = @[msg(-1, 60, good), msg(-1, 61, "", photo = 1), msg(-1, 62, "", photo = 1)]
    for p in parts.mitems: p.albumId = 5
    let (l, st) = setup(f)
    waitFor l.bootstrap()
    waitFor l.handle(parts[0])               # the backlog had only the first picture
    l.enqueue parts[0]
    l.enqueue parts[1]
    l.enqueue parts[2]
    waitFor l.processQueue()
    check f.forwarded == @[(-1'i64, 60'i64)]
    check st.seen(-1, 61) and st.seen(-1, 62)

  test "an edit doesn't join an album, and the same picture twice counts once":
    let f = FakeTg()
    var parts = @[msg(-1, 71, "", photo = 1), msg(-1, 70, good)]   # out of order
    for p in parts.mitems: p.albumId = 6
    var edited = msg(-1, 70, good & " (edited)")
    edited.albumId = 6
    edited.edited = true
    let (l, _) = setup(f)
    waitFor l.bootstrap()
    l.enqueue parts[0]
    l.enqueue parts[1]
    l.enqueue parts[1]
    l.enqueue edited
    waitFor l.processQueue()
    check f.forwarded == @[(-1'i64, 70'i64), (-1'i64, 71'i64)]    # once, in order

  test "approving a reviewed album forwards the whole album":
    let f = FakeTg()
    var parts = @[msg(-1, 80, unclear), msg(-1, 81, "", photo = 7)]
    f.brokenPhotos.incl 7
    for p in parts.mitems: p.albumId = 8
    f.chats[-1] = parts
    let (l, st) = setup(f)
    waitFor l.bootstrap()
    let r = st.pendingReviews()
    check r.len == 1 and r[0].albumIds == @[80'i64, 81]
    check st.decideReview(r[0].id, "to_send")
    waitFor l.sendApproved(afterWindow())
    check f.forwarded == @[(-1'i64, 80'i64), (-1'i64, 81'i64)]

  test "a message rejected before fingerprints existed is not judged again on an edit":
    let f = FakeTg()
    let (l, st) = setup(f)
    waitFor l.bootstrap()
    st.markProcessed(-1, 90, "chat1", "reject", "", "", "rejected")   # a stage 3 row
    var m = msg(-1, 90, good)
    m.edited = true
    l.enqueue m
    waitFor l.processQueue()
    check f.forwarded.len == 0

  test "a late or split-off album picture counts as part of the handled post":
    let f = FakeTg()
    var a = msg(-1, 100, "Hiring a junior Electrical Engineer in Basra, fresh graduates welcome")
    var b = msg(-1, 101, "", photo = 1)                  # its own picture text would publish
    a.albumId = 42
    b.albumId = 42
    let (l, st) = setup(f)
    waitFor l.bootstrap()
    l.albumWaitMs = 10
    l.enqueue a
    l.enqueue msg(-2, 5, bad)                             # another chat in between
    l.enqueue b
    waitFor l.processQueue()
    check f.forwarded == @[(-1'i64, 100'i64)]
    check st.previous(-1, 101).found
    for row in st.stats(1):
      if row.chat == "chat1": check row.forwarded == 1   # one post, not two pictures

suite "watched chats you can't read any more (#16)":
  proc capture(l: Listener): ref seq[string] =
    let lines = new seq[string]
    l.log = proc (line: string) {.closure, gcsafe.} =
      {.cast(gcsafe).}: lines[].add line
    lines

  test "a ban is reported once; the same update again, or for an unwatched chat, is not":
    let f = FakeTg()
    let (l, _) = setup(f)
    waitFor l.onChatAccess(-1, ChatAccess(title: "Basra Jobs", status: "banned"))
    check f.texts.len == 1 and f.texts[0][0] == 42
    check "banned from Basra Jobs" in f.texts[0][1] and "config.json" in f.texts[0][1]
    waitFor l.onChatAccess(-1, ChatAccess(title: "Basra Jobs", status: "banned"))   # TDLib repeats it
    check f.texts.len == 1
    waitFor l.onChatAccess(-7, ChatAccess(title: "not watched", status: "banned"))
    waitFor l.onChatAccess(-2, ChatAccess(title: "x", status: ""))     # unknown status
    check f.texts.len == 1

  test "leaving, and a group upgraded to a supergroup, are reported too":
    let f = FakeTg()
    let (l, _) = setup(f)
    waitFor l.onChatAccess(-1, ChatAccess(status: "left"))             # no title: from l.titles
    check f.texts.len == 1 and "no longer a member of chat -1" in f.texts[0][1]
    l.titles[-2] = "Old Group"
    waitFor l.onChatAccess(-2, ChatAccess(status: "upgraded", newChat: -1000000000009))
    check f.texts.len == 2 and "Old Group" in f.texts[1][1]
    check "Replace -2 with -1000000000009" in f.texts[1][1]

  test "access back clears the state; losing it again is reported again":
    let f = FakeTg()
    let (l, st) = setup(f)
    let lines = l.capture
    waitFor l.onChatAccess(-1, ChatAccess(title: "Jobs", status: "banned"))
    waitFor l.onChatAccess(-1, ChatAccess(title: "Jobs", status: "ok"))
    check st.getMeta("chat_access:-1") == ""
    check "✅ can read Jobs again" in lines[]
    check f.texts.len == 1                          # a log line, no alert
    waitFor l.onChatAccess(-1, ChatAccess(title: "Jobs", status: "ok"))
    check lines[].countIt(it == "✅ can read Jobs again") == 1
    waitFor l.onChatAccess(-1, ChatAccess(title: "Jobs", status: "banned"))
    check f.texts.len == 2

  test "a restart (same state database) doesn't report it again":
    let f = FakeTg()
    f.access[-1] = ChatAccess(title: "Jobs", status: "banned")
    let (l, st) = setup(f)
    waitFor l.checkWatchedChats()
    check f.texts.len == 1 and "banned from Jobs" in f.texts[0][1]
    let l2 = newListener(l.cfg, f.toTelegram, st, noFetch(), defaultOcr(), false)
    l2.log = proc (line: string) {.closure, gcsafe.} = discard
    waitFor l2.checkWatchedChats()
    waitFor l2.onChatAccess(-1, ChatAccess(title: "Jobs", status: "banned"))   # TDLib's replay
    check f.texts.len == 1

  test "start-up check: a chat that can't be opened is reported; a timeout only logged":
    let f = FakeTg()
    f.accessError[-1] = "Chat not found (400)"
    f.accessError[-2] = "no answer from TDLib after 300 s"
    let (l, st) = setup(f)
    let lines = l.capture
    waitFor l.checkWatchedChats()
    check f.texts.len == 1 and "chat -1 can't be read (Chat not found (400))" in f.texts[0][1]
    check st.getMeta("chat_access:-1") == "unavailable" and st.getMeta("chat_access:-2") == ""
    check lines[].anyIt("could not check access to chat -2" in it)
    # its backlog fails the same way: still one alert
    f.unreadError[-1] = "Chat not found (400)"
    waitFor l.bootstrap()
    check f.texts.len == 1
    # a member again (status ok) isn't enough: the chat must be readable
    f.accessError.clear
    waitFor l.checkWatchedChats()
    check st.getMeta("chat_access:-1") == "unavailable"

  test "a backlog that can't be read for lack of access is reported; readable again clears it":
    let f = FakeTg()
    f.chats[-2] = @[msg(-2, 5, good)]
    f.unreadError[-2] = "CHANNEL_PRIVATE (400)"
    f.unreadFails.incl -1                          # offline: only logged, retried
    let (l, st) = setup(f)
    let lines = l.capture
    waitFor l.bootstrap()
    check f.texts.len == 1 and "chat -2 can't be read (CHANNEL_PRIVATE (400))" in f.texts[0][1]
    waitFor l.retryFailedChats()                   # still failing: not reported again
    check f.texts.len == 1
    f.unreadError.clear
    f.unreadFails.clear
    waitFor l.retryFailedChats()
    waitFor l.processQueue()
    check st.getMeta("chat_access:-2") == ""
    check "✅ can read chat2 again" in lines[]
    check f.forwarded == @[(-2'i64, 5'i64)]

  test "a ban plus a failing backlog is one alert, also after restarts (review)":
    let f = FakeTg()
    f.access[-1] = ChatAccess(title: "Jobs", status: "banned")
    f.unreadError[-1] = "CHANNEL_PRIVATE (400)"
    let (l, st) = setup(f)
    waitFor l.checkWatchedChats()
    waitFor l.bootstrap()
    check f.texts.len == 1 and st.getMeta("chat_access:-1") == "banned"
    let l2 = newListener(l.cfg, f.toTelegram, st, noFetch(), defaultOcr(), false)
    l2.log = proc (line: string) {.closure, gcsafe.} = discard
    waitFor l2.checkWatchedChats()
    waitFor l2.bootstrap()
    check f.texts.len == 1

  test "no unread messages doesn't prove a chat readable (review)":
    let f = FakeTg()
    f.unreadError[-2] = "CHANNEL_PRIVATE (400)"
    let (l, st) = setup(f)
    waitFor l.bootstrap()
    check st.getMeta("chat_access:-2") == "unavailable"
    f.unreadError.clear                       # read works, but there's nothing unread
    waitFor l.retryFailedChats()
    check st.getMeta("chat_access:-2") == "unavailable"
    f.chats[-2] = @[msg(-2, 5, good)]         # next start, a real read clears it
    let l2 = newListener(l.cfg, f.toTelegram, st, noFetch(), defaultOcr(), false)
    l2.log = proc (line: string) {.closure, gcsafe.} = discard
    waitFor l2.bootstrap()
    check st.getMeta("chat_access:-2") == ""

  test "a public channel never joined is not reported at start-up; leaving live is (review)":
    let f = FakeTg()
    f.access[-1] = ChatAccess(title: "Public", status: "left", public: true)
    let (l, st) = setup(f)
    waitFor l.checkWatchedChats()
    check f.texts.len == 0 and st.getMeta("chat_access:-1") == "left-public"
    waitFor l.onChatAccess(-2, ChatAccess(title: "Group", status: "left"))   # live update
    check f.texts.len == 1 and "no longer a member of Group" in f.texts[0][1]

  test "a dry run's alerts say so (review)":
    let f = FakeTg()
    let (l, _) = setup(f, dryRun = true)
    waitFor l.alert("something")
    check f.texts.len == 1 and f.texts[0][1].startsWith("⚠️ tele-notify [dry run]: something")

  test "a post that arrives proves the chat readable again (adversarial review)":
    let f = FakeTg()
    f.unreadError[-1] = "CHANNEL_PRIVATE (400)"
    let (l, st) = setup(f)
    waitFor l.bootstrap()
    check st.getMeta("chat_access:-1") == "unavailable"
    let live = msg(-1, 20, bad)              # you rejoined; posts arrive live
    l.enqueue live
    waitFor l.processQueue()
    check st.getMeta("chat_access:-1") == ""

  test "removed from a private group while the bot was down is reported (adversarial review)":
    let f = FakeTg()
    f.access[-1] = ChatAccess(title: "Private Jobs", status: "left")     # no username
    let (l, st) = setup(f)
    waitFor l.checkWatchedChats()
    check f.texts.len == 1 and "no longer a member of Private Jobs" in f.texts[0][1]
    check st.getMeta("chat_access:-1") == "left"

  test "a public channel never joined that goes private is reported (adversarial review)":
    let f = FakeTg()
    f.access[-1] = ChatAccess(title: "Public", status: "left", public: true)
    let (l, st) = setup(f)
    waitFor l.checkWatchedChats()
    check f.texts.len == 0
    waitFor l.onChatAccess(-1, ChatAccess(status: "left", public: false), live = true)   # username gone
    check f.texts.len == 1 and "no longer public" in f.texts[0][1]
    check st.getMeta("chat_access:-1") == "unavailable"

  test "a live update during the start-up check waits for the check (adversarial review)":
    let f = FakeTg()
    f.access[-1] = ChatAccess(title: "Public", status: "left", public: true)
    let (l, st) = setup(f)
    var raced = false
    f.duringAccess = proc () {.closure, gcsafe.} =
      {.cast(gcsafe).}:
        if not raced:
          raced = true
          # TDLib's refresh of chat -2 arrives while chat -1 is being checked
          waitFor l.onChatAccess(-2, ChatAccess(status: "left"), live = true)
    waitFor l.checkWatchedChats()
    check f.texts.len == 0 and st.getMeta("chat_access:-2") == ""

suite "what the terminal screen reads":
  test "alerts are recorded; the chat list's status follows access changes":
    let f = FakeTg()
    let (l, st) = setup(f)
    st.saveChats(@[ChatRow(id: -1, title: "Basra Jobs", kind: "channel")], @[-1'i64, -2])
    waitFor l.onChatAccess(-1, ChatAccess(title: "Basra Jobs", status: "banned"))
    check st.chatStatus(-1) == "banned"
    check "banned from Basra Jobs" in st.alertTexts(1)[0]
    waitFor l.onChatAccess(-1, ChatAccess(title: "Basra Jobs", status: "ok"))
    check st.chatStatus(-1) == "ok"

  test "the post text is kept for search, in the real run too":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 10, good)]
    let (l, st) = setup(f)               # not a dry run
    waitFor l.bootstrap()
    check st.rawTextOf(-1, 10) == good

  test "the connection state is published":
    let (l, st) = setup(FakeTg())
    l.onConnectionState("connectionStateConnecting")
    check st.getMeta("connection") == "connecting"
    l.onConnectionState("connectionStateReady")
    check st.getMeta("connection") == "ready"

  test "old post texts are cleared in real runs, kept in dry runs (review)":
    for dry in [false, true]:
      let (l, st) = setup(FakeTg(), dryRun = dry)
      st.markProcessed(-1, 1, "Jobs", "reject", "", "r", "rejected", rawText = "old")
      st.ageProcessed(-1, 200)
      l.forgetOldTexts()
      check st.rawTextOf(-1, 1) == (if dry: "old" else: "")

const tech = "We are hiring a junior network technician in Basra. Fresh graduates welcome."

proc recorder(sent: ref seq[string], err = "", delayMs = 0): Notifier =
  result = proc (text: string): Future[string] {.async, closure, gcsafe.} =
    if delayMs > 0: await sleepAsync(delayMs)
    {.cast(gcsafe).}: sent[].add text
    return err

proc live(l: Listener, ms: varargs[TgMessage]) =
  ## Messages that arrive after the backlog, handled at once.
  for m in ms: l.enqueue m
  waitFor l.processQueue()
  waitFor l.flushNotifications()

suite "notifications through your own bot (#122)":
  test "live: a published post and a post for review each notify; a reject doesn't":
    let f = FakeTg()
    let (l, _) = setup(f)
    let sent = new seq[string]
    l.notify = recorder(sent)
    waitFor l.bootstrap()
    l.live(msg(-1, 10, good), msg(-1, 11, unclear), msg(-1, 12, bad))
    check sent[].len == 2
    check sent[0].startsWith("✅ Published") and "chat1" in sent[0] and "https://t.me/c/1/10" in sent[0]
    check sent[1].startsWith("🔎 For review") and "chat1" in sent[1] and "https://t.me/c/1/11" in sent[1]
    # the role isolated from the English around it; what to do next last (review run 2)
    check sent[0] == "✅ Published: \u2068مهندس كهرباء\u2069\nchat1\nhttps://t.me/c/1/10"
    check sent[1] == "🔎 For review: \u2068مهندس كهرباء\u2069\nchat1\nhttps://t.me/c/1/11\n" & reviewHint
    check reviewHint == "Review: telenotify review, or the screen's Review tab (2)"

  test "the backlog after downtime sends one summary, then live posts notify one by one (review run 1)":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 10, good), msg(-1, 11, unclear), msg(-1, 12, bad), msg(-1, 13, unclear & " 2")]
    let (l, _) = setup(f)
    let sent = new seq[string]
    l.notify = recorder(sent)
    waitFor l.bootstrap()
    waitFor l.flushNotifications()
    check sent[] == @["📥 Backlog read: 1 published, 2 for review\n" & reviewHint]
    l.live(msg(-1, 20, unclear & " 3"))
    check sent[].len == 2 and sent[1].startsWith("🔎 For review")

  test "a backlog with nothing to tell sends nothing (review run 1)":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 12, bad)]
    let (l, _) = setup(f)
    let sent = new seq[string]
    l.notify = recorder(sent)
    waitFor l.bootstrap()
    waitFor l.flushNotifications()
    check sent[].len == 0

  test "a slow bot never slows the posts or the alerts (review run 1)":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 10, good), msg(-1, 11, unclear), msg(-1, 12, bad)]
    let (l, st) = setup(f)
    let sent = new seq[string]
    l.notify = recorder(sent, delayMs = 1500)
    var t0 = epochTime()
    waitFor l.alert("first")
    check epochTime() - t0 < 0.5
    check st.alertTexts() == @["first"]                     # recorded at once
    check f.texts.len == 1                                   # and in Saved Messages
    t0 = epochTime()
    waitFor l.bootstrap()
    l.enqueue msg(-2, 20, tech)
    l.enqueue msg(-1, 21, unclear & " 3")
    waitFor l.processQueue()
    check epochTime() - t0 < 1.0
    check f.forwarded.len == 2
    waitFor l.flushNotifications()
    check sent[].len == 4                                    # alert, summary, two live posts

  test "alerts go through the bot too, and a failing bot doesn't stop anything":
    let f = FakeTg()
    let (l, _) = setup(f)
    var lines: seq[string]
    l.log = proc (line: string) {.closure, gcsafe.} =
      {.cast(gcsafe).}: lines.add line
    let sent = new seq[string]
    l.notify = recorder(sent, err = "HTTP 401: Unauthorized")
    waitFor l.alert("something")
    waitFor l.bootstrap()
    l.live(msg(-1, 10, good))
    check f.forwarded == @[(-1'i64, 10'i64)]          # published anyway
    check sent[].len == 2 and sent[0].startsWith("⚠️ tele-notify: something")
    check lines.anyIt("could not notify" in it and "HTTP 401" in it)

  test "\"alerts\": false silences the bot's alerts too, not the posts (review run 1)":
    let f = FakeTg()
    let (l, st) = setup(f)
    l.cfg.alerts = false
    let sent = new seq[string]
    l.notify = recorder(sent)
    waitFor l.alert("something")
    waitFor l.flushNotifications()
    check sent[].len == 0 and f.texts.len == 0 and st.alertTexts() == @["something"]
    waitFor l.bootstrap()
    l.live(msg(-1, 10, good))
    check sent[].len == 1 and sent[0].startsWith("✅ Published")

  test "a dry run sends no per-post notifications; its alerts say so (review run 1)":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 10, good), msg(-1, 11, unclear)]
    let (l, _) = setup(f, dryRun = true)
    let sent = new seq[string]
    l.notify = recorder(sent)
    waitFor l.bootstrap()
    l.live(msg(-2, 20, tech), msg(-1, 21, unclear & " 3"))
    check sent[].len == 0
    waitFor l.alert("something")
    waitFor l.flushNotifications()
    check sent[] == @["⚠️ tele-notify [dry run]: something"]

  test "an album notifies once":
    let f = FakeTg()
    let (l, _) = setup(f)
    let sent = new seq[string]
    l.notify = recorder(sent)
    waitFor l.bootstrap()
    var parts: seq[TgMessage]
    for id in 30'i64 .. 32:
      var m = msg(-1, id, if id == 30: good else: "")
      m.albumId = 777
      parts.add m
    l.live(parts)
    check f.forwarded.len == 3
    check sent[].len == 1 and sent[0].startsWith("✅ Published")

  test "a post that couldn't be queued for review isn't announced for review (review run 1)":
    let f = FakeTg()
    let (l, _) = setup(f)
    let sent = new seq[string]
    l.notify = recorder(sent)
    waitFor l.bootstrap()
    let db = open(getTempDir() / ("tn-listener-" & $n) / "state.sqlite3", "", "", "")
    db.exec(sql"DROP TABLE reviews")
    db.close()
    f.forwardFails = true                    # the error path, then addReview fails too
    l.live(msg(-1, 10, good))
    check sent[].allIt("For review" notin it)

  test "a review post asks for its link once (review run 1)":
    let f = FakeTg()
    let (l, _) = setup(f)
    let sent = new seq[string]
    l.notify = recorder(sent)
    waitFor l.bootstrap()
    l.live(msg(-1, 11, unclear))
    check f.linkCalls == 1
    check sent[].len == 1 and "https://t.me/c/1/11" in sent[0]

  test "a post you approved, once sent to your channel, notifies too":
    let f = FakeTg()
    let (l, st) = setup(f)
    let sent = new seq[string]
    l.notify = recorder(sent)
    st.addReview(-1, 7, "chat1", unclear, "no level", "مهندس كهرباء", "https://t.me/c/1/7")
    for r in st.pendingReviews(): check st.decideReview(r.id, "to_send")
    waitFor l.sendApproved(afterWindow())
    waitFor l.flushNotifications()
    check sent[].len == 1 and sent[0].startsWith("✅ Published after your review") and "https://t.me/c/1/7" in sent[0]

  test "after review, the notification has the link the channel got, even if none was stored (review run 1)":
    let f = FakeTg()
    f.noForward.incl -1
    let (l, st) = setup(f)
    let sent = new seq[string]
    l.notify = recorder(sent)
    st.addReview(-1, 8, "chat1", unclear, "no level", "مهندس كهرباء", "")
    for r in st.pendingReviews(): check st.decideReview(r.id, "to_send")
    waitFor l.sendApproved(afterWindow())
    waitFor l.flushNotifications()
    check f.texts.len == 1 and "https://t.me/c/1/8" in f.texts[0][1]
    check sent[].len == 1 and "https://t.me/c/1/8" in sent[0]

  test "a chat read on retry is one summary too, not a ping per post (review run 2)":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 10, good)]
    f.chats[-2] = @[msg(-2, 5, tech), msg(-2, 6, unclear), msg(-2, 7, unclear & " 2"), msg(-2, 8, bad)]
    f.unreadFails.incl -2
    let (l, _) = setup(f)
    let sent = new seq[string]
    l.notify = recorder(sent)
    waitFor l.bootstrap()
    waitFor l.flushNotifications()
    check sent[] == @["📥 Backlog read: 1 published, 0 for review"]
    f.unreadFails.clear
    waitFor l.retryFailedChats()
    waitFor l.processQueue()
    waitFor l.flushNotifications()
    check sent[].len == 2 and sent[1] == "📥 Backlog read: 1 published, 2 for review\n" & reviewHint
    check (-2'i64, 8'i64) in f.read                     # marked read once handled
    l.live(msg(-1, 20, unclear & " 3"))                 # live again: one by one
    check sent[].len == 3 and sent[2].startsWith("🔎 For review")

  test "notify_published and notify_review turn each kind off (review run 2)":
    let f = FakeTg()
    f.chats[-1] = @[msg(-1, 10, good), msg(-1, 11, unclear)]
    let (l, st) = setup(f)
    l.cfg.notifyPublished = false
    let sent = new seq[string]
    l.notify = recorder(sent)
    waitFor l.bootstrap()
    l.live(msg(-2, 20, tech), msg(-1, 21, unclear & " 3"))
    st.addReview(-1, 7, "chat1", unclear, "no level", "role", "https://t.me/c/1/7")
    for r in st.pendingReviews():
      if r.msgId == 7: check st.decideReview(r.id, "to_send")
    waitFor l.sendApproved(afterWindow())
    waitFor l.flushNotifications()
    check sent[] == @["📥 Backlog read: 1 for review\n" & reviewHint,
                      "🔎 For review: \u2068مهندس كهرباء\u2069\nchat1\nhttps://t.me/c/1/21\n" & reviewHint]
    waitFor l.alert("still on")                         # alerts follow "alerts"
    waitFor l.flushNotifications()
    check sent[].len == 3 and sent[2].startsWith("⚠️ tele-notify: still on")
    let f2 = FakeTg()
    let (l2, _) = setup(f2)
    l2.cfg.notifyReview = false
    let sent2 = new seq[string]
    l2.notify = recorder(sent2)
    waitFor l2.bootstrap()
    l2.live(msg(-1, 30, good), msg(-1, 31, unclear))
    check sent2[].len == 1 and sent2[0].startsWith("✅ Published")

  test "a published post's link lookup that hangs gives up after 5 s (review run 2)":
    let f = FakeTg()
    let (l, _) = setup(f)
    let sent = new seq[string]
    l.notify = recorder(sent)
    waitFor l.bootstrap()
    f.linkHangs = true
    let t0 = epochTime()
    l.enqueue msg(-1, 10, good)
    waitFor l.processQueue()
    check epochTime() - t0 < 1.0                         # the post didn't wait
    waitFor l.flushNotifications()
    check sent[] == @["✅ Published: \u2068مهندس كهرباء\u2069\nchat1"]
    check epochTime() - t0 >= 4.5 and epochTime() - t0 < 7
