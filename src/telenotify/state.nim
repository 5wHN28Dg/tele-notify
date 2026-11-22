## Everything the bot remembers, in one SQLite file.
##
## The old bot kept its memory (the last 10 forwarded posts) *inside*
## config.json and rewrote that file on every forward, which threw away any
## keyword edits made while it ran (problems report 5.2). Now config.json is
## only read, and state lives in `<data_dir>/state.sqlite3`:
##
## | table | what |
## |---|---|
## | `processed` | every message judged: chat, id, verdict, role, reasons, when, and the post's own Telegram date (`posted_at`, for forwarding latency, #74). Lets the bot skip messages it has already seen (restarts, the startup backlog overlapping live updates) and gives the numbers for `telenotify stats` (#12–#15) |
## | `published` | the text of what was published, for duplicate detection |
## | `reviews` | the review queue: unclear posts waiting for you to send or discard them (`telenotify review`). Your decisions double as labels for the test corpus. An approved post waits `undoWindowSeconds` (`send_after`) before the bot sends it, so it can be taken back (#62) |
## | `meta` | small values: the last heartbeat (downtime alerts), the watched chats you lost access to (`chat_access:<chat>`), your user id for your own bot (`notify_chat_id`) |
##
## You can open the file with any SQLite tool (`sqlite3 state.sqlite3`).
##
## The file's schema version is SQLite's `user_version`. Opening a file
## older than `schemaVersion` upgrades it, but first keeps a fresh copy of
## it as `state.sqlite3.bak-<its old version>` beside it (#74), one process
## at a time (`state.sqlite3.lock`).

import std/[times, strutils, os, tables, algorithm]
from std/posix import nil
import db_connector/db_sqlite

type
  State* = ref object
    db: DbConn

  Review* = object
    id*: int64
    chatId*, msgId*: int64
    chatTitle*, text*, reasons*, role*, link*, status*: string
    at*: int64
    sendAfter*: int64       ## approved posts: not sent before this time (Unix seconds); 0 = at once
    albumIds*: seq[int64]   ## the messages to forward: the album, or just msgId

proc now(): int64 = getTime().toUnix

const schemaVersion* = 2
  ## SQLite's `user_version` of a file this build has set up. Files from
  ## before versions existed read as 0.
  ##   1  processed.posted_at: the post's own Telegram date (#74)
  ##   2  reviews.send_after: an approved post's undo window (#62)

const undoWindowSeconds* = 30
  ## How long an approved review waits before the bot sends it, so you can
  ## undo it in the terminal screen's Review tab (#62; the owner's choice,
  ## 2026-11-21). The default of config.json's `undo_seconds`, which the
  ## screen (tui/telenotify_tui/store.py) reads too.

proc backupPath*(path: string, version: int): string =
  ## Where the copy of a version-`version` state file is kept before it's upgraded.
  path & ".bak-" & $version

proc flock(fd, operation: cint): cint {.importc, header: "<sys/file.h>".}
const lockEx = 2.cint
const lockNb = 4.cint

var upgradeLockWaitMs* = 20_000
  ## How long opening an old file waits for another telenotify (the bot, or
  ## a command) that is upgrading it: the copy takes well under a second for
  ## a state file of a few MB. Tests shorten it.

var upgradeTestHook*: proc (stage: string) {.nimcall.} = nil
  ## Tests only: called during an upgrade at "locked" (just after taking
  ## the lock), "renamed" (the copy has its name, before the folder is
  ## flushed) and "columns" (inside the upgrade's transaction, after the
  ## columns, before the version is written), to fail or race there.

proc testHook(stage: string) =
  if upgradeTestHook != nil: upgradeTestHook(stage)

proc lockForUpgrade(path: string): cint =
  ## An exclusive lock on `<path>.lock`, so only one process at a time
  ## copies and upgrades the file. Released when the descriptor is closed.
  let lockPath = path & ".lock"
  result = posix.open(lockPath.cstring, posix.O_RDWR or posix.O_CREAT or posix.O_CLOEXEC, 0o600)
  if result < 0: raiseOSError(osLastError(), lockPath)
  var waited = 0
  while flock(result, lockEx or lockNb) != 0:
    let err = osLastError()
    if err.int32 notin [posix.EWOULDBLOCK, posix.EINTR]:
      discard posix.close(result)
      raiseOSError(err, lockPath)
    if waited >= upgradeLockWaitMs:
      discard posix.close(result)
      raise newException(IOError, "another telenotify (the bot or a command) is still upgrading " &
        path & ". Try again in a minute.")
    sleep(100)
    waited += 100

proc syncPath(path: string) =
  ## Make sure what was written to `path` (a file, or a folder's list of
  ## names) is on the disk, not only in memory, before going on.
  let fd = posix.open(path.cstring, posix.O_RDONLY or posix.O_CLOEXEC)
  if fd < 0: raiseOSError(osLastError(), path)
  let failed = posix.fsync(fd) != 0
  let err = osLastError()
  discard posix.close(fd)
  if failed: raiseOSError(err, path)

proc removeStaleCopies(path: string) =
  ## Half-made copies left by a process that died while copying. Only
  ## called with the upgrade lock held, so no other process is making one.
  for f in walkFiles(path & ".bak-*.tmp-*"):
    try: removeFile(f)
    except OSError: discard

proc backupBeforeUpgrade(db: DbConn, path: string, version: int) =
  ## Copy the state file to `backupPath(path, version)`, replacing any
  ## older copy there (it may be from before a rollback). `VACUUM INTO`
  ## copies through SQLite, so the copy has what the write-ahead log (WAL)
  ## holds too, which a plain file copy can miss. It writes to a temporary
  ## name, flushes it to the disk, and only then renames it, so a
  ## half-written copy never takes the backup's name, even after a power
  ## cut. Raises if it fails. Call it with the upgrade lock held.
  let dest = backupPath(path, version)
  let tmp = dest & ".tmp-" & $getCurrentProcessId()
  try:
    removeFile(tmp)
    # an empty file, private before anything is in it (VACUUM INTO accepts an
    # empty file): the copy holds the text of every post judged
    writeFile(tmp, "")
    setFilePermissions(tmp, {fpUserRead, fpUserWrite})
    db.exec(sql"VACUUM INTO ?", tmp)
    syncPath(tmp)                  # VACUUM INTO doesn't flush its file itself
    moveFile(tmp, dest)
  except CatchableError as e:
    try: removeFile(tmp)
    except CatchableError: discard
    raise newException(IOError, "could not keep a copy of " & path & " as " & dest &
      " before upgrading it, so it was left as it was. Free some disk space and check that you " &
      "can write to " & path.parentDir & ", then start telenotify again. (" & e.msg & ")")
  try:
    testHook("renamed")
    syncPath(dest.parentDir)       # the rename itself
  except CatchableError as e:
    raise newException(IOError, "kept a copy of " & path & " as " & dest & ", but could not " &
      "make sure it is on the disk yet, so " & path & " was not upgraded and is unchanged. Check " &
      "the disk, then start telenotify again. (" & e.msg & ")")

proc userVersion(db: DbConn): int = parseInt(db.getValue(sql"PRAGMA user_version"))

proc createTables(db: DbConn) =
  ## Every table, column and index; adds whatever an older file lacks.
  db.exec(sql"""CREATE TABLE IF NOT EXISTS processed (
    chat_id INTEGER, msg_id INTEGER, chat_title TEXT, verdict TEXT, role TEXT,
    reasons TEXT, action TEXT, at INTEGER, PRIMARY KEY (chat_id, msg_id))""")
  db.exec(sql"""CREATE TABLE IF NOT EXISTS published (
    id INTEGER PRIMARY KEY, chat_id INTEGER, msg_id INTEGER, text TEXT, at INTEGER)""")
  db.exec(sql"""CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY, chat_id INTEGER, msg_id INTEGER, chat_title TEXT, text TEXT,
    reasons TEXT, role TEXT, link TEXT, status TEXT DEFAULT 'pending', at INTEGER,
    decided_at INTEGER, UNIQUE (chat_id, msg_id))""")
  db.exec(sql"CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
  # for the terminal screen (tui/): the alerts sent to Saved Messages, and
  # this account's chats with their names and whether they can be read
  db.exec(sql"CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY, at INTEGER, text TEXT)")
  db.exec(sql"""CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY, title TEXT, kind TEXT, watched INTEGER, status TEXT, updated_at INTEGER)""")
  # stage 4: what the Python bot sent to your channel during a dry run
  db.exec(sql"""CREATE TABLE IF NOT EXISTS other_bot (
    id INTEGER PRIMARY KEY, at INTEGER, msg_id INTEGER, origin_chat INTEGER,
    origin_msg INTEGER, text TEXT, UNIQUE (msg_id))""")
  # columns added in stage 4 (older state files get them here):
  #   processed.text_hash  a fingerprint of the text judged (edits)
  #   processed.raw_text   the message text (compare; search in the terminal screen)
  #   reviews.album_ids    every message of an album, so approving sends it all
  #   other_bot.album_id   pictures of one album count as one post
  #   processed.album_id   so a late picture of an album counts as handled
  # in schema version 1:
  #   processed.posted_at  the post's own Telegram date, or the edit's for an
  #                        edited post judged again; Unix seconds (#74). NULL
  #                        for posts judged before it was kept
  #   processed.caught_up  1: read from a backlog (start-up, or a chat read
  #                        again after it failed), 0: a live update (#74)
  # and in schema version 2:
  #   reviews.send_after   an approved post isn't sent before this time (Unix
  #                        seconds): the undo window (#62). NULL (posts approved
  #                        before it was kept) means at once
  # (Declare the type: values are bound as text, and only an INTEGER
  # column turns "0" back into the number 0.)
  for (table, column) in [("processed", "text_hash TEXT"), ("processed", "raw_text TEXT"),
                          ("processed", "album_id INTEGER"), ("processed", "posted_at INTEGER"),
                          ("processed", "caught_up INTEGER"),
                          ("reviews", "album_ids TEXT"), ("reviews", "send_after INTEGER"),
                          ("other_bot", "album_id INTEGER")]:
    var has = false
    for row in db.fastRows(sql("PRAGMA table_info(" & table & ")")):
      if row[1] == column.split(' ')[0]: has = true
    if not has:
      # `review` and `run` may open an old file at the same moment
      try: db.exec(sql("ALTER TABLE " & table & " ADD COLUMN " & column))
      except DbError as e:
        if "duplicate column" notin e.msg: raise
  # Without these, reads grow with every post ever judged:
  #   processed_at          the last N days (`stats`, `compare`, the terminal screen)
  #   processed_chat_at     one chat's posts newest first (search), each chat's newest
  #   processed_chat_album  the pictures of one album (every album picture that
  #                         arrives, and counting an album once in `stats`)
  db.exec(sql"CREATE INDEX IF NOT EXISTS processed_at ON processed (at)")
  db.exec(sql"CREATE INDEX IF NOT EXISTS processed_chat_at ON processed (chat_id, at)")
  db.exec(sql"CREATE INDEX IF NOT EXISTS processed_chat_album ON processed (chat_id, album_id, msg_id)")

proc openState*(path: string): State =
  if not dirExists(path.parentDir):
    createDir(path.parentDir)
    # the same folder holds your Telegram login (session.nim)
    setFilePermissions(path.parentDir, {fpUserRead, fpUserWrite, fpUserExec})
  result = State(db: open(path, "", "", ""))
  # it now keeps the text of every post judged: yours only
  try: setFilePermissions(path, {fpUserRead, fpUserWrite})
  except OSError: discard
  let db = result.db
  db.exec(sql"PRAGMA journal_mode=WAL")
  # `telenotify review` writes while the bot runs: wait for the other
  # writer instead of failing with "database is locked"
  db.exec(sql"PRAGMA busy_timeout=10000")
  if db.userVersion >= schemaVersion:
    db.createTables()            # adds nothing to a file this build set up
    return
  # Older than this build (or new). One process at a time: take the lock,
  # look again (another one may have just upgraded it), and keep a fresh
  # copy of an existing file before changing anything. Then the columns and
  # the version in one transaction: all of the upgrade, or none of it. If
  # anything fails, stop here with the file as it was, saying so in one line.
  var lock = -1.cint
  try:
    lock = lockForUpgrade(path)
    testHook("locked")
    removeStaleCopies(path)
    let version = db.userVersion
    if version < schemaVersion:
      if db.getValue(sql"SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'processed'") != "":
        backupBeforeUpgrade(db, path, version)
      db.exec(sql"BEGIN IMMEDIATE")
      try:
        db.createTables()
        testHook("columns")
        db.exec(sql("PRAGMA user_version = " & $schemaVersion))
        db.exec(sql"COMMIT")
      except CatchableError as e:
        try: db.exec(sql"ROLLBACK")
        except CatchableError: discard        # keep the first error
        raise newException(IOError, "could not upgrade " & path & " to schema version " &
          $schemaVersion & ", so it was left as it was (" & e.msg & ")")
    else:
      db.createTables()
  except CatchableError as e:
    db.close()
    # one line in the journal, whatever went wrong (`run` prints "error: " & msg)
    raise newException(IOError, e.msg.splitLines.join(" "))
  finally:
    if lock >= 0: discard posix.close(lock)

proc close*(s: State) = s.db.close()

proc seen*(s: State, chat, msg: int64): bool =
  s.db.getValue(sql"SELECT 1 FROM processed WHERE chat_id = ? AND msg_id = ?", chat, msg) != ""

proc markProcessed*(s: State, chat, msg: int64, title, verdict, role, reasons, action: string,
                    textHash = "", rawText = "", albumId = 0'i64, postedAt = 0'i64,
                    caughtUp = false) =
  ## `postedAt`: the post's own Telegram date (Unix seconds), or its edit's
  ## when an edited post is judged again; 0 = unknown, kept as NULL.
  ## `caughtUp`: read from a backlog (start-up, or a retried chat), not live.
  s.db.exec(sql"""INSERT OR REPLACE INTO processed
    (chat_id, msg_id, chat_title, verdict, role, reasons, action, at, text_hash, raw_text, album_id,
     posted_at, caught_up)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,NULLIF(CAST(? AS INTEGER), 0),?)""",
    chat, msg, title, verdict, role, reasons, action, now(), textHash, rawText, albumId, postedAt,
    (if caughtUp: 1 else: 0))

proc caughtUp*(s: State, chat, msg: int64): bool =
  ## Was it read from a backlog rather than as a live update?
  s.db.getValue(sql"SELECT caught_up FROM processed WHERE chat_id = ? AND msg_id = ?", chat, msg) == "1"

proc postedAt*(s: State, chat, msg: int64): int64 =
  ## The post's own Telegram date as recorded, or 0 if not known.
  let v = s.db.getValue(sql"SELECT COALESCE(posted_at, 0) FROM processed WHERE chat_id = ? AND msg_id = ?",
                        chat, msg)
  if v == "": 0'i64 else: parseBiggestInt(v)

const albumHandledSql* = """SELECT 1, action, COALESCE(text_hash, '') FROM processed
                             WHERE chat_id = ? AND album_id = ? LIMIT 1"""

proc albumHandled*(s: State, chat, albumId: int64): tuple[found: bool, action, textHash: string] =
  ## Was any picture of this album handled already?
  if albumId == 0: return
  let row = s.db.getRow(sql(albumHandledSql), chat, albumId)
  (row[0] == "1", row[1], row[2])

proc processedByMsgId*(s: State, msg: int64): seq[(int64, string)] =
  ## (chat, action) of every judged message with this id, in any chat.
  for r in s.db.fastRows(sql"SELECT chat_id, action FROM processed WHERE msg_id = ?", msg):
    result.add (parseBiggestInt(r[0]), r[1])

proc previous*(s: State, chat, msg: int64): tuple[found: bool, action, textHash: string] =
  ## What happened to this message last time, if anything.
  let row = s.db.getRow(sql"SELECT 1, action, COALESCE(text_hash, '') FROM processed WHERE chat_id = ? AND msg_id = ?",
                        chat, msg)
  (row[0] == "1", row[1], row[2])

proc addPublished*(s: State, chat, msg: int64, text: string) =
  s.db.exec(sql"INSERT INTO published (chat_id, msg_id, text, at) VALUES (?,?,?,?)",
            chat, msg, text, now())

proc recentPublished*(s: State, days = 14, limit = 2000): seq[string] =
  ## Texts published in the last `days` days, newest first.
  for row in s.db.fastRows(sql"SELECT text FROM published WHERE at > ? ORDER BY at DESC LIMIT ?",
                           now() - days * 86400, limit):
    result.add row[0]

proc addReview*(s: State, chat, msg: int64, title, text, reasons, role, link: string,
                albumIds: seq[int64] = @[]) =
  var ids: seq[string]
  for i in albumIds: ids.add $i
  s.db.exec(sql"""INSERT OR IGNORE INTO reviews
    (chat_id, msg_id, chat_title, text, reasons, role, link, at, album_ids) VALUES (?,?,?,?,?,?,?,?,?)""",
    chat, msg, title, text, reasons, role, link, now(), ids.join(","))

proc reviewsWithStatus(s: State, status: string, dueBy = high(int64)): seq[Review] =
  for r in s.db.fastRows(sql"""SELECT id, chat_id, msg_id, chat_title, text, reasons, role, link,
                               status, at, COALESCE(album_ids, ''), COALESCE(send_after, 0) FROM reviews
                               WHERE status = ? AND (send_after IS NULL OR send_after <= CAST(? AS INTEGER))
                               ORDER BY at""",
                         status, dueBy):
    var rv = Review(id: parseBiggestInt(r[0]), chatId: parseBiggestInt(r[1]),
                    msgId: parseBiggestInt(r[2]), chatTitle: r[3], text: r[4], reasons: r[5],
                    role: r[6], link: r[7], status: r[8], at: parseBiggestInt(r[9]),
                    sendAfter: parseBiggestInt(r[11]))
    for i in r[10].split(','):
      if i != "": rv.albumIds.add parseBiggestInt(i)
    if rv.albumIds.len == 0: rv.albumIds = @[rv.msgId]
    result.add rv

proc pendingReviews*(s: State): seq[Review] =
  ## Posts waiting for your decision.
  s.reviewsWithStatus("pending")

proc approvedReviews*(s: State): seq[Review] =
  ## Posts you chose to send and haven't been sent yet, in their undo
  ## window or not.
  s.reviewsWithStatus("to_send")

proc dueReviews*(s: State, at = now()): seq[Review] =
  ## Approved posts whose undo window is over at `at`: the ones the running
  ## bot may send now (`sendApproved`), once it has claimed each (`claimReview`).
  s.reviewsWithStatus("to_send", dueBy = at)

proc decideReview*(s: State, id: int64, status: string, fromStatus = "pending",
                   undoSeconds = undoWindowSeconds): bool =
  ## Move a review from `fromStatus` to `status`: "to_send" (you approved
  ## it; the bot sends it once its undo window, `undoWindowSeconds`, is
  ## over; `undoSeconds`, config.json's `undo_seconds`), "sent" (published:
  ## `decided_at` stays the time you decided, so History's order doesn't
  ## move under its cursor), "discarded" or "kept" (a match you don't
  ## want sent, e.g. the old bot already forwarded it; set by the terminal
  ## screen, never sent or counted as forwarded). Does nothing and returns
  ## false if it's no longer in `fromStatus` (e.g. another `review` window
  ## already decided it), so a post is never approved, and sent, twice.
  let t = now()
  if status == "to_send":
    s.db.exec(sql"""UPDATE reviews SET status = ?, decided_at = ?, send_after = ?
                     WHERE id = ? AND status = ?""", status, t, t + max(0, undoSeconds), id, fromStatus)
  elif status in ["sent", "sending"]:
    s.db.exec(sql"UPDATE reviews SET status = ? WHERE id = ? AND status = ?", status, id, fromStatus)
  else:
    s.db.exec(sql"UPDATE reviews SET status = ?, decided_at = ? WHERE id = ? AND status = ?",
              status, t, id, fromStatus)
  s.db.getValue(sql"SELECT changes()") == "1"

proc claimReview*(s: State, id: int64, at = now()): bool =
  ## The bot takes an approved post to send it: "to_send" becomes "sending",
  ## in one statement, only if it is still approved and its undo window is
  ## over. The terminal screen's undo is the same kind of statement on
  ## "to_send" (store.py `undo`), so exactly one of the two wins: an undone
  ## post is never sent, and a post being sent can't be undone (#62, #76).
  s.db.exec(sql"""UPDATE reviews SET status = 'sending'
                   WHERE id = ? AND status = 'to_send'
                         AND (send_after IS NULL OR send_after <= CAST(? AS INTEGER))""", id, at)
  s.db.getValue(sql"SELECT changes()") == "1"

const couldNotSend = "could not send: "

const maxTries = 1_000_000   ## a stored count above this isn't trusted (a new part is added)
const unknownProblem* = "unknown error"   ## a failure with no message
const leads = ["could not send", "error", "not posted"]
  ## a reason part's lead-in ("error: …"): a count goes right after it

proc problemText*(msg: string): string =
  ## A failure's message as one reason part: its first line (a debug build
  ## adds an async traceback), with "; " (which separates the parts)
  ## written ", ", and no closing single "." or ";" (another part or a
  ## count may follow it): those and the spaces around them are stripped
  ## until none is left ("x. ." and "x;" are "x"; "..", "..." and "…" stay). No message at all
  ## is "unknown error", and so is the rest of a bare lead-in ("error:" is
  ## "error: unknown error"). So it's always as `comparable` leaves it.
  # a debug build's traceback starts on its own line, even after an empty message
  let i = msg.find("Async traceback:")
  let lines = (if i >= 0: msg[0 ..< i] else: msg).strip.splitLines
  result = (if lines.len > 0: lines[0].strip else: "").replace("; ", ", ")
  # a closing ";" too: a count after it, "x; (2 tries)", would read as a new part
  while result.endsWith(";") or (result.endsWith(".") and not result.endsWith("..")):
    result = result[0 ..< ^1].strip
  if result == "": result = unknownProblem
  for lead in leads:
    if result == lead & ":": result = lead & ": " & unknownProblem

proc parseTries(words: string): int =
  ## "3 tries" / "1 try" -> 3 / 1; -1 if it isn't a count in 0 .. maxTries.
  let w = words.split(' ')
  if w.len != 2 or w[1] notin ["try", "tries"]: return -1
  try:
    let n = parseInt(w[0])
    if n in 0 .. maxTries: return n
  except ValueError: discard
  -1

proc splitCount(part: string): tuple[base: string, tries: int] =
  ## A part with its count taken out: "error (3 tries): x" -> ("error: x",
  ## 3); "x (3 tries)" -> ("x", 3); no count -> (part, -1).
  result = (part, -1)
  for lead in leads:
    if part.startsWith(lead & " ("):
      let close = part.find("): ", lead.len)
      if close > 0:
        let n = parseTries(part[lead.len + 2 ..< close])
        if n >= 0: return (lead & ": " & part[close + 3 .. ^1], n)
  if not part.endsWith(")"): return
  let open = part.rfind(" (")
  if open < 0: return
  let n = parseTries(part[open + 2 ..< part.len - 1])
  if n >= 0: result = (part[0 ..< open], n)

proc comparable*(base: string): string =
  ## A stored part, trimmed, as it's compared: without one closing single
  ## "." (older rows have one; ".." stays), and with "unknown error" after
  ## a lead-in with nothing after it (an older bot wrote "error: " for no
  ## message). A part that's empty, or one "." (with any spaces around
  ## it), is "", which never matches. `problemText`'s output is always left
  ## as it is.
  result = base.strip
  if result.endsWith(".") and not result.endsWith(".."): result = result[0 ..< ^1].strip
  for lead in leads:
    if result == lead & ":": return lead & ": " & unknownProblem
  # an empty part, or one ".", stays "": it's no reason, and never folds

proc withCount(base: string, n: int): string =
  ## `base` with "(n tries)" where it reads naturally: after its lead-in
  ## ("error (3 tries): …"), else at the end.
  let count = "(" & $n & (if n == 1: " try)" else: " tries)")
  for lead in leads:
    if base.startsWith(lead & ": "): return lead & " " & count & ": " & base[lead.len + 2 .. ^1]
  base & " " & count

proc withProblem*(reasons, problem: string, heldBack = false): string =
  ## `reasons` ("a; b; …") after a failed send for `problem` (`heldBack`:
  ## it was held back on purpose, #80, and is added as it is, not after
  ## "could not send: "). If it already has that reason as a whole part
  ## (`problem` itself, held back live or written by the filter; "error:
  ## <problem>" from the live post; or "could not send: <problem>"), with
  ## or without a count, it isn't added again: the first such part gets
  ## the number of tries instead, "error (2 tries): …", counting each of
  ## those parts (one without a count is one try; copies an older bot
  ## wrote are folded into it). The problem is `problemText`: one line,
  ## with no "; " in it, so it's always compared as a whole.
  let p = problemText(problem)
  var parts = if reasons == "": newSeq[string]() else: reasons.split("; ")
  let forms = [p, "error: " & p, couldNotSend & p]
  var first = -1
  var total = 0
  var kept: seq[string]
  for part in parts:
    # the whole part first: the problem itself may end in something count-like
    var (base, tries) = (part.comparable, -1)
    if base notin forms:
      (base, tries) = splitCount(part)
      base = base.comparable
    if base != "" and base in forms:
      total += (if tries >= 0: tries else: 1)
      if first < 0:
        first = kept.len
        kept.add base
      continue                      # a copy: folded into the first
    kept.add part
  if first >= 0:
    kept[first] = withCount(kept[first], min(total + 1, maxTries))
    return kept.join("; ")
  parts.add (if heldBack: p else: couldNotSend & p)
  parts.join("; ")

proc reopenReview*(s: State, id: int64, problem: string, heldBack = false) =
  ## Sending failed (or was held back, `heldBack`): put it back in the
  ## queue, with the reason (`withProblem`: a reason it already has is
  ## counted, not repeated). The bot has claimed it ("sending", #62); an
  ## older caller may still pass one that is "to_send". Its undo window
  ## goes with it: approved again, it gets a new one.
  let row = s.db.getRow(sql"""SELECT COALESCE(reasons, '') FROM reviews
                              WHERE id = ? AND status IN ('to_send', 'sending')""", id)
  if row.len == 0: return
  s.db.exec(sql"""UPDATE reviews SET status = 'pending', send_after = NULL, reasons = ?
                   WHERE id = ? AND status IN ('to_send', 'sending')""", withProblem(row[0], problem, heldBack), id)

const interruptedSend* = "the bot stopped while sending it: check the channel before sending it again"
  ## The reason a post gets when the bot stopped while sending it.

proc reopenInterruptedSends*(s: State): int =
  ## At start: posts the bot was sending when it stopped (a crash, a power
  ## cut). One already in `published` did reach the channel (only the "sent"
  ## mark was lost): it is marked sent. For the others it isn't known, so they
  ## go back to the queue with a note, for you to check, rather than out a
  ## second time. Returns how many went back to the queue.
  s.db.exec(sql"""UPDATE reviews SET status = 'sent' WHERE status = 'sending' AND EXISTS
                   (SELECT 1 FROM published p WHERE p.chat_id = reviews.chat_id AND p.msg_id = reviews.msg_id)""")
  # through withProblem, as a held-back reason: a post interrupted twice gets
  # one counted part, and an empty reasons field doesn't start with "; "
  var rows: seq[(string, string)]
  for r in s.db.fastRows(sql"SELECT id, COALESCE(reasons, '') FROM reviews WHERE status = 'sending'"):
    rows.add (r[0], r[1])
  for (id, reasons) in rows:
    s.db.exec(sql"""UPDATE reviews SET status = 'pending', send_after = NULL, reasons = ?
                     WHERE id = ? AND status = 'sending'""", withProblem(reasons, interruptedSend, heldBack = true), id)
    result += parseInt(s.db.getValue(sql"SELECT changes()"))

proc getMeta*(s: State, key: string): string =
  s.db.getValue(sql"SELECT value FROM meta WHERE key = ?", key)

proc setMeta*(s: State, key, value: string) =
  s.db.exec(sql"INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", key, value)

proc botRunning*(s: State, at = now()): bool =
  ## Is a bot on this file running? Its heartbeat (`last_alive`, every
  ## minute) is under 120 s old and it didn't record a clean stop. The
  ## terminal screen asks the same (store.py `bot_running`).
  let alive = s.getMeta("last_alive")
  # a log-out: it stopped (and won't start again until you log in)
  if alive == "" or s.getMeta("connection") in ["stopped", "logged out"]: return false
  try: at - parseBiggestInt(alive) < 120
  except ValueError: false

proc loggedOut*(s: State): bool =
  ## Telegram logged this account out (or it never logged in): `telenotify
  ## login` is needed before the bot can run (the Dashboard says the same).
  s.getMeta("connection") == "logged out" or s.getMeta("login_needed") == "1"

proc botSending*(s: State, at = now()): bool =
  ## Is it also sending approved posts: its worker has started, after the
  ## start-up backlog (`sending_ready`)? (store.py `bot_sending`)
  s.botRunning(at) and s.getMeta("sending_ready") == "1"

type StatRow* = object
  chat*: string
  verdicts*: array[3, int]   ## publish, review, reject
  forwarded*: int

const statsSql* = """SELECT chat_title,
      SUM(verdict = 'publish'), SUM(verdict = 'review'), SUM(verdict = 'reject'),
      SUM(action IN ('forwarded', 'linked', 'summarized') OR EXISTS (SELECT 1 FROM reviews r
        WHERE r.chat_id = p.chat_id AND r.msg_id = p.msg_id AND r.status = 'sent')),
      MAX(at)
      FROM processed p WHERE at > ? AND (COALESCE(album_id, 0) = 0 OR msg_id =
        (SELECT MIN(msg_id) FROM processed q WHERE q.chat_id = p.chat_id AND q.album_id = p.album_id))
      GROUP BY +chat_id ORDER BY COUNT(*) DESC, chat_id"""
  # `+chat_id`: group after reading the last N days through processed_at; plain
  # `chat_id` lets SQLite walk a whole chat_id index instead, every post ever judged.
  # MAX(at): SQLite then takes chat_title from the newest row (a renamed chat).
  # `, chat_id`: equal counts in the same order every time.

proc stats*(s: State, days: int): seq[StatRow] =
  ## Per chat over the last `days` days: how many messages got each verdict
  ## and how many were actually forwarded (#12, #13, #15), including posts
  ## you approved in `telenotify review` once they've been sent.
  # An album is one post, however many pictures: count its first row only.
  for r in s.db.fastRows(sql(statsSql), now() - days * 86400):
    result.add StatRow(chat: r[0], verdicts: [parseInt(r[1]), parseInt(r[2]), parseInt(r[3])],
                       forwarded: parseInt(r[4]))

type Latency* = object
  ## Seconds from a post's own Telegram date to its publishing in your channel.
  count*: int
  median*, p95*: int64

proc percentile*(sorted: seq[int64], p: int): int64 =
  ## The nearest-rank percentile of an ascending list: the smallest value
  ## with at least `p`% of the values at or below it. 0 for an empty list.
  if sorted.len == 0: return 0
  let rank = max(1, (p * sorted.len + 99) div 100)     # ceil(p/100 * n)
  sorted[rank - 1]

const latencySql* = """SELECT MAX(0, u.at - p.posted_at),
      CASE WHEN p.action NOT IN ('forwarded', 'linked', 'summarized', 'would publish') THEN 2
           WHEN p.caught_up = 1 THEN 1 ELSE 0 END
      FROM published u JOIN processed p ON p.chat_id = u.chat_id AND p.msg_id = u.msg_id
      WHERE u.at > ? AND p.posted_at IS NOT NULL"""
  # published.at is when it reached the channel (or, in a dry run, would have).
  # Posts judged before posted_at was kept have none and are left out.

proc latency*(s: State, days: int): tuple[live, caughtUp, reviewed: Latency] =
  ## Forwarding time over the posts forwarded in the last `days` days, in
  ## three groups: `live` posts forwarded as soon as they were judged while
  ## connected (what the alert-latency target, ALERT1, is about);
  ## `caughtUp`, those read from a backlog after downtime or a failed read;
  ## and `reviewed`, those sent after you approved them (your wait included).
  var groups: array[3, seq[int64]]
  for r in s.db.fastRows(sql(latencySql), now() - days * 86400):
    groups[parseInt(r[1])].add parseBiggestInt(r[0])
  proc summary(xs: var seq[int64]): Latency =
    xs.sort()
    Latency(count: xs.len, median: percentile(xs, 50), p95: percentile(xs, 95))
  (summary(groups[0]), summary(groups[1]), summary(groups[2]))

# ------------------------------------------------- stage 4: side by side

type
  OtherBotPost* = object
    at*, originChat*, originMsg*: int64   ## origin 0 = unknown (not from a channel)
    text*: string
    albumId*: int64

  ProcessedRow* = object
    chatId*, msgId*, at*: int64
    chatTitle*, verdict*, role*, reasons*, action*: string
    rawText*: string   ## the post's text (cleared after `keep_text_days`)

proc addOtherBot*(s: State, msgId, originChat, originMsg: int64, text: string, albumId = 0'i64) =
  ## A post the other (Python) bot sent to your channel.
  s.db.exec(sql"""INSERT OR IGNORE INTO other_bot (at, msg_id, origin_chat, origin_msg, text, album_id)
                  VALUES (?,?,?,?,?,?)""", now(), msgId, originChat, originMsg, text, albumId)

proc otherBotSince*(s: State, since: int64): seq[OtherBotPost] =
  for r in s.db.fastRows(sql"""SELECT at, origin_chat, origin_msg, text,
                               CAST(COALESCE(album_id, 0) AS INTEGER)
                               FROM other_bot WHERE at > ? ORDER BY msg_id""", since):
    result.add OtherBotPost(at: parseBiggestInt(r[0]), originChat: parseBiggestInt(r[1]),
                            originMsg: parseBiggestInt(r[2]), text: r[3],
                            albumId: parseBiggestInt(r[4]))

const processedSinceSql* = """SELECT chat_id, msg_id, at, chat_title, verdict, role, reasons,
                               action, COALESCE(raw_text, '') FROM processed WHERE at > ? ORDER BY at"""

proc processedSince*(s: State, since: int64): seq[ProcessedRow] =
  for r in s.db.fastRows(sql(processedSinceSql), since):
    result.add ProcessedRow(chatId: parseBiggestInt(r[0]), msgId: parseBiggestInt(r[1]),
                            at: parseBiggestInt(r[2]), chatTitle: r[3], verdict: r[4], role: r[5],
                            reasons: r[6], action: r[7], rawText: r[8])

proc processedRow*(s: State, chat, msg: int64): (bool, ProcessedRow) =
  let r = s.db.getRow(sql"""SELECT chat_id, msg_id, at, chat_title, verdict, role, reasons, action
                            FROM processed WHERE chat_id = ? AND msg_id = ?""", chat, msg)
  if r[0] == "": return (false, ProcessedRow())
  (true, ProcessedRow(chatId: parseBiggestInt(r[0]), msgId: parseBiggestInt(r[1]),
                      at: parseBiggestInt(r[2]), chatTitle: r[3], verdict: r[4], role: r[5],
                      reasons: r[6], action: r[7]))

proc publishedText*(s: State, chat, msg: int64): string =
  s.db.getValue(sql"SELECT text FROM published WHERE chat_id = ? AND msg_id = ? LIMIT 1", chat, msg)

# ------------------------------------------- the terminal screen (tui/)

proc addAlert*(s: State, text: string) =
  ## Keep an alert so the terminal screen can list it. Only the newest 500.
  s.db.exec(sql"INSERT INTO alerts (at, text) VALUES (?, ?)", now(), text)
  s.db.exec(sql"DELETE FROM alerts WHERE id <= (SELECT MAX(id) FROM alerts) - 500")

proc markRunStarting*(s: State, dryRun: bool, at = now()) =
  ## A run has TDLib's lock: it is this file's bot now. Its mode, start time,
  ## "starting" and "not sending yet" (a crashed bot's `sending_ready` = 1
  ## mustn't promise sends during the new run's login), all or nothing.
  s.db.exec(sql"BEGIN IMMEDIATE")
  try:
    s.setMeta("mode", if dryRun: "dry-run" else: "real")
    s.setMeta("started_at", $at)
    s.setMeta("connection", "starting")
    s.setMeta("sending_ready", "0")       # until the worker starts, after the backlog
    s.db.exec(sql"COMMIT")
  except CatchableError as e:
    try: s.db.exec(sql"ROLLBACK")
    except CatchableError: discard
    raise e

proc markLoginNeeded*(s: State, alert: string): bool {.discardable.} =
  ## The account isn't logged in: the connection says so, and `alert` is kept
  ## once until `login_needed` is cleared. All or nothing, so a failure can't
  ## skip the alert or add it twice. True when the alert was added now.
  s.db.exec(sql"BEGIN IMMEDIATE")
  try:
    s.setMeta("connection", "logged out")
    if s.getMeta("login_needed") != "1":
      s.addAlert(alert)
      s.setMeta("login_needed", "1")
      result = true
    s.db.exec(sql"COMMIT")
  except CatchableError as e:
    s.db.exec(sql"ROLLBACK")
    raise e

proc alertTexts*(s: State, limit = 50): seq[string] =
  ## Newest first.
  for r in s.db.fastRows(sql"SELECT text FROM alerts ORDER BY id DESC LIMIT ?", limit):
    result.add r[0]

type ChatRow* = object
  id*: int64
  title*, kind*: string

proc chatStatusFromMeta(s: State, id: int64): string =
  ## The #16 access state as the chat list shows it: a public channel you
  ## never joined ("left-public") is readable, so "ok".
  let st = s.getMeta("chat_access:" & $id)
  if st in ["", "left-public"]: "ok" else: st

proc saveChats*(s: State, chats: seq[ChatRow], watched: seq[int64],
                titles = initTable[int64, string]()) =
  ## Replace the chat list (names for the terminal screen's settings). A
  ## chat's access status comes from the "chat_access:<id>" meta value
  ## (#16). Watched chats missing from your chat lists (a public channel
  ## you never joined isn't in them) keep their title from `titles`.
  s.db.exec(sql"BEGIN")
  try:
    s.db.exec(sql"DELETE FROM chats")
    var seenIds: seq[int64]
    for c in chats:
      s.db.exec(sql"INSERT OR REPLACE INTO chats VALUES (?, ?, ?, ?, ?, ?)", c.id, c.title, c.kind,
                (if c.id in watched: 1 else: 0), s.chatStatusFromMeta(c.id), now())
      seenIds.add c.id
    for w in watched:
      if w notin seenIds:
        let title = titles.getOrDefault(w, "")
        # a watched chat Telegram couldn't even open has no title either
        let st = if title == "" and s.getMeta("chat_access:" & $w) == "": "unavailable"
                 else: s.chatStatusFromMeta(w)
        s.db.exec(sql"INSERT OR REPLACE INTO chats VALUES (?, ?, ?, ?, ?, ?)", w, title, "", 1,
                  st, now())
    s.db.exec(sql"COMMIT")
  except CatchableError:
    try: s.db.exec(sql"ROLLBACK")
    except CatchableError: discard        # keep the first error
    raise

proc setChatStatus*(s: State, id: int64, status: string) =
  s.db.exec(sql"UPDATE chats SET status = ?, updated_at = ? WHERE id = ?",
            (if status == "": "ok" else: status), now(), id)

proc chatStatus*(s: State, id: int64): string =
  s.db.getValue(sql"SELECT status FROM chats WHERE id = ?", id)

proc rawTextOf*(s: State, chat, msg: int64): string =
  ## The text a message had when it was judged ("" if not kept).
  s.db.getValue(sql"SELECT COALESCE(raw_text, '') FROM processed WHERE chat_id = ? AND msg_id = ?", chat, msg)

proc forgetOldTexts*(s: State, days: int) =
  ## Clear the kept post text of messages judged more than `days` days ago
  ## (verdicts and reasons stay). 0 keeps everything.
  if days <= 0: return
  s.db.exec(sql"UPDATE processed SET raw_text = NULL WHERE raw_text IS NOT NULL AND at < ?",
            now() - days * 86400)

proc chatTitle*(s: State, id: int64): string =
  s.db.getValue(sql"SELECT title FROM chats WHERE id = ?", id)

proc alertCount*(s: State): int =
  parseInt(s.db.getValue(sql"SELECT COUNT(*) FROM alerts"))

proc ageProcessed*(s: State, chat: int64, days: int) =
  ## Tests: pretend a chat's messages were judged `days` days earlier.
  s.db.exec(sql"UPDATE processed SET at = at - ? WHERE chat_id = ?", days * 86400, chat)
