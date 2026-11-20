## Everything the bot remembers, in one SQLite file.
##
## The old bot kept its memory (the last 10 forwarded posts) *inside*
## config.json and rewrote that file on every forward, which threw away any
## keyword edits made while it ran (problems report 5.2). Now config.json is
## only read, and state lives in `<data_dir>/state.sqlite3`:
##
## | table | what |
## |---|---|
## | `processed` | every message judged: chat, id, verdict, role, reasons, when. Lets the bot skip messages it has already seen (restarts, the startup backlog overlapping live updates) and gives the numbers for `telenotify stats` (#12–#15) |
## | `published` | the text of what was published, for duplicate detection |
## | `reviews` | the review queue: unclear posts waiting for you to send or discard them (`telenotify review`). Your decisions double as labels for the test corpus |
## | `meta` | small values: the last heartbeat (downtime alerts), the watched chats you lost access to (`chat_access:<chat>`) |
##
## You can open the file with any SQLite tool (`sqlite3 state.sqlite3`).

import std/[times, strutils, os, tables]
import db_connector/db_sqlite

type
  State* = ref object
    db: DbConn

  Review* = object
    id*: int64
    chatId*, msgId*: int64
    chatTitle*, text*, reasons*, role*, link*, status*: string
    at*: int64
    albumIds*: seq[int64]   ## the messages to forward: the album, or just msgId

proc now(): int64 = getTime().toUnix

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
  # (Declare the type: values are bound as text, and only an INTEGER
  # column turns "0" back into the number 0.)
  for (table, column) in [("processed", "text_hash TEXT"), ("processed", "raw_text TEXT"),
                          ("processed", "album_id INTEGER"),
                          ("reviews", "album_ids TEXT"), ("other_bot", "album_id INTEGER")]:
    var has = false
    for row in db.fastRows(sql("PRAGMA table_info(" & table & ")")):
      if row[1] == column.split(' ')[0]: has = true
    if not has:
      # `review` and `run` may open an old file at the same moment
      try: db.exec(sql("ALTER TABLE " & table & " ADD COLUMN " & column))
      except DbError as e:
        if "duplicate column" notin e.msg: raise
  # the dashboard, `stats` and the terminal screen read the last N days,
  # overall and per chat: without these every refresh scans every post ever judged
  db.exec(sql"CREATE INDEX IF NOT EXISTS processed_at ON processed (at)")
  db.exec(sql"CREATE INDEX IF NOT EXISTS processed_chat_at ON processed (chat_id, at)")

proc close*(s: State) = s.db.close()

proc seen*(s: State, chat, msg: int64): bool =
  s.db.getValue(sql"SELECT 1 FROM processed WHERE chat_id = ? AND msg_id = ?", chat, msg) != ""

proc markProcessed*(s: State, chat, msg: int64, title, verdict, role, reasons, action: string,
                    textHash = "", rawText = "", albumId = 0'i64) =
  s.db.exec(sql"""INSERT OR REPLACE INTO processed
    (chat_id, msg_id, chat_title, verdict, role, reasons, action, at, text_hash, raw_text, album_id)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
    chat, msg, title, verdict, role, reasons, action, now(), textHash, rawText, albumId)

proc albumHandled*(s: State, chat, albumId: int64): tuple[found: bool, action, textHash: string] =
  ## Was any picture of this album handled already?
  if albumId == 0: return
  let row = s.db.getRow(sql"""SELECT 1, action, COALESCE(text_hash, '') FROM processed
                              WHERE chat_id = ? AND album_id = ? LIMIT 1""", chat, albumId)
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

proc reviewsWithStatus(s: State, status: string): seq[Review] =
  for r in s.db.fastRows(sql"""SELECT id, chat_id, msg_id, chat_title, text, reasons, role, link,
                               status, at, COALESCE(album_ids, '') FROM reviews
                               WHERE status = ? ORDER BY at""", status):
    var rv = Review(id: parseBiggestInt(r[0]), chatId: parseBiggestInt(r[1]),
                    msgId: parseBiggestInt(r[2]), chatTitle: r[3], text: r[4], reasons: r[5],
                    role: r[6], link: r[7], status: r[8], at: parseBiggestInt(r[9]))
    for i in r[10].split(','):
      if i != "": rv.albumIds.add parseBiggestInt(i)
    if rv.albumIds.len == 0: rv.albumIds = @[rv.msgId]
    result.add rv

proc pendingReviews*(s: State): seq[Review] =
  ## Posts waiting for your decision.
  s.reviewsWithStatus("pending")

proc approvedReviews*(s: State): seq[Review] =
  ## Posts you chose to send; the running bot sends them (`sendApproved`).
  s.reviewsWithStatus("to_send")

proc decideReview*(s: State, id: int64, status: string, fromStatus = "pending"): bool =
  ## Move a review from `fromStatus` to `status`: "to_send" (you approved
  ## it; the bot will send it), "sent" (published), "discarded" or "kept"
  ## (a match you don't want sent, e.g. the old bot already forwarded it;
  ## set by the terminal screen, never sent or counted as forwarded). Does
  ## nothing and returns false if it's no longer in `fromStatus` (e.g.
  ## another `review` window already decided it), so a post is never
  ## approved, and sent, twice.
  s.db.exec(sql"UPDATE reviews SET status = ?, decided_at = ? WHERE id = ? AND status = ?",
            status, now(), id, fromStatus)
  s.db.getValue(sql"SELECT changes()") == "1"

proc reopenReview*(s: State, id: int64, problem: string) =
  ## Sending failed: put it back in the queue, with the reason.
  s.db.exec(sql"""UPDATE reviews SET status = 'pending', reasons = reasons || '; could not send: ' || ?
                   WHERE id = ? AND status = 'to_send'""", problem, id)

proc getMeta*(s: State, key: string): string =
  s.db.getValue(sql"SELECT value FROM meta WHERE key = ?", key)

proc setMeta*(s: State, key, value: string) =
  s.db.exec(sql"INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", key, value)

type StatRow* = object
  chat*: string
  verdicts*: array[3, int]   ## publish, review, reject
  forwarded*: int

proc stats*(s: State, days: int): seq[StatRow] =
  ## Per chat over the last `days` days: how many messages got each verdict
  ## and how many were actually forwarded (#12, #13, #15), including posts
  ## you approved in `telenotify review` once they've been sent.
  # An album is one post, however many pictures: count its first row only.
  for r in s.db.fastRows(sql"""SELECT chat_title,
      SUM(verdict = 'publish'), SUM(verdict = 'review'), SUM(verdict = 'reject'),
      SUM(action = 'forwarded' OR action = 'linked' OR EXISTS (SELECT 1 FROM reviews r
        WHERE r.chat_id = p.chat_id AND r.msg_id = p.msg_id AND r.status = 'sent'))
      FROM processed p WHERE at > ? AND (COALESCE(album_id, 0) = 0 OR msg_id =
        (SELECT MIN(msg_id) FROM processed q WHERE q.chat_id = p.chat_id AND q.album_id = p.album_id))
      GROUP BY chat_id ORDER BY COUNT(*) DESC""", now() - days * 86400):
    result.add StatRow(chat: r[0], verdicts: [parseInt(r[1]), parseInt(r[2]), parseInt(r[3])],
                       forwarded: parseInt(r[4]))

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

proc processedSince*(s: State, since: int64): seq[ProcessedRow] =
  for r in s.db.fastRows(sql"""SELECT chat_id, msg_id, at, chat_title, verdict, role, reasons, action,
                               COALESCE(raw_text, '') FROM processed WHERE at > ? ORDER BY at""", since):
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
