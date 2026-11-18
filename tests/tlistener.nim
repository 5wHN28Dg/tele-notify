## The whole bot against a fake Telegram: no account, no network.

import std/[unittest, asyncdispatch, os, tables, sets, strutils, times, json]
import telenotify/[listener, telegram, state, config, gather, fetch, ocr, dedup]

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

proc toTelegram(f: FakeTg): Telegram =
  result = Telegram()
  result.unread = proc (chat: int64): Future[Unread] {.async, closure, gcsafe.} =
    {.cast(gcsafe).}:
      if f.duringUnread != nil: f.duringUnread()
      return Unread(messages: f.chats.getOrDefault(chat), cut: chat in f.cutChats)
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

var n = 0
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
    waitFor l.sendApproved()
    check f.forwarded == @[(-1'i64, 7'i64)]
    check f.texts.len == 1 and "post link: https://t.me/c/2/8" in f.texts[0][1]   # link fetched now
    check st.approvedReviews().len == 0 and st.pendingReviews().len == 0
    check duplicateOf(unclear, st.recentPublished()) != ""

  test "an approved post that can't be sent goes back to the queue with the reason":
    let f = FakeTg()
    f.noForward.incl -2
    f.linkFails = true
    let (l, st) = setup(f)
    st.addReview(-2, 8, "chat2", unclear, "no level", "role", "")
    check st.decideReview(st.pendingReviews()[0].id, "to_send")
    waitFor l.sendApproved()
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

  test "an edit of a message never seen is handled like a new one":
    let f = FakeTg()
    let (l, _) = setup(f)
    waitFor l.bootstrap()
    var m = msg(-2, 50, good)
    m.edited = true
    l.enqueue m
    waitFor l.processQueue()
    check f.forwarded == @[(-2'i64, 50'i64)]

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
    check notes[2].originChat == 0          # couldn't resolve: kept, matched by text later
