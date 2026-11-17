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
## | `meta` | small values, e.g. the last heartbeat (for downtime alerts) |
##
## You can open the file with any SQLite tool (`sqlite3 state.sqlite3`).

import std/[times, strutils, os]
import db_connector/db_sqlite

type
  State* = ref object
    db: DbConn

  Review* = object
    id*: int64
    chatId*, msgId*: int64
    chatTitle*, text*, reasons*, role*, link*, status*: string
    at*: int64

proc now(): int64 = getTime().toUnix

proc openState*(path: string): State =
  createDir(path.parentDir)
  result = State(db: open(path, "", "", ""))
  let db = result.db
  db.exec(sql"PRAGMA journal_mode=WAL")
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

proc close*(s: State) = s.db.close()

proc seen*(s: State, chat, msg: int64): bool =
  s.db.getValue(sql"SELECT 1 FROM processed WHERE chat_id = ? AND msg_id = ?", chat, msg) != ""

proc markProcessed*(s: State, chat, msg: int64, title, verdict, role, reasons, action: string) =
  s.db.exec(sql"""INSERT OR REPLACE INTO processed
    (chat_id, msg_id, chat_title, verdict, role, reasons, action, at) VALUES (?,?,?,?,?,?,?,?)""",
    chat, msg, title, verdict, role, reasons, action, now())

proc addPublished*(s: State, chat, msg: int64, text: string) =
  s.db.exec(sql"INSERT INTO published (chat_id, msg_id, text, at) VALUES (?,?,?,?)",
            chat, msg, text, now())

proc recentPublished*(s: State, days = 14, limit = 200): seq[string] =
  ## Texts published in the last `days` days, newest first.
  for row in s.db.fastRows(sql"SELECT text FROM published WHERE at > ? ORDER BY at DESC LIMIT ?",
                           now() - days * 86400, limit):
    result.add row[0]

proc addReview*(s: State, chat, msg: int64, title, text, reasons, role, link: string) =
  s.db.exec(sql"""INSERT OR IGNORE INTO reviews
    (chat_id, msg_id, chat_title, text, reasons, role, link, at) VALUES (?,?,?,?,?,?,?,?)""",
    chat, msg, title, text, reasons, role, link, now())

proc pendingReviews*(s: State): seq[Review] =
  for r in s.db.fastRows(sql"""SELECT id, chat_id, msg_id, chat_title, text, reasons, role, link,
                               status, at FROM reviews WHERE status = 'pending' ORDER BY at"""):
    result.add Review(id: parseBiggestInt(r[0]), chatId: parseBiggestInt(r[1]),
                      msgId: parseBiggestInt(r[2]), chatTitle: r[3], text: r[4], reasons: r[5],
                      role: r[6], link: r[7], status: r[8], at: parseBiggestInt(r[9]))

proc decideReview*(s: State, id: int64, status: string) =
  ## status: "sent" (it was good: published) or "discarded".
  s.db.exec(sql"UPDATE reviews SET status = ?, decided_at = ? WHERE id = ?", status, now(), id)

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
  ## and how many were actually forwarded (#12, #13, #15).
  for r in s.db.fastRows(sql"""SELECT chat_title,
      SUM(verdict = 'publish'), SUM(verdict = 'review'), SUM(verdict = 'reject'),
      SUM(action = 'forwarded' OR action = 'linked')
      FROM processed WHERE at > ? GROUP BY chat_id ORDER BY COUNT(*) DESC""", now() - days * 86400):
    result.add StatRow(chat: r[0], verdicts: [parseInt(r[1]), parseInt(r[2]), parseInt(r[3])],
                       forwarded: parseInt(r[4]))
