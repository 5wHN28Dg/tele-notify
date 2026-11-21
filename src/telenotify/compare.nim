## Stage 4: the Nim bot (dry run) next to the Python bot.
##
## While `telenotify run --dry-run` runs, it also notes every post the
## Python bot sends to your channel (they show up as your own outgoing
## messages there: forwards, or "match: ... post link: ..." texts; the real
## Nim bot's summaries, #80, are read the same way through their "Post link:"
## line, and a summary of a private chat has none). Then
## `telenotify compare` puts the two side by side:
##
## * **both**: published by both bots (or Nim skipped it as a copy of an
##   ad it had already published from another channel);
## * **Python only**: with Nim's verdict and reasons, so you can see who
##   was right;
##
## Posts are matched by where they came from, or else by the message text
## (kept in `processed.raw_text`). A forward of a forward
## points at the first channel, and links to channels without a username
## can't be resolved, so text matching catches those too.
## * **Nim only**: what Nim would have published and Python didn't.
##
## It only reads the database, so it works while the bot runs.

import std/[sets, strutils]
import state, dedup

type
  Compared* = object
    other*: OtherBotPost   ## the Python bot's post (empty for Nim only)
    nim*: ProcessedRow     ## what Nim did with the same post
    nimSaw*: bool          ## false: Nim never saw it (e.g. before the dry run started)

  Comparison* = object
    both*, pythonOnly*, nimOnly*: seq[Compared]

const publishedActions = ["would publish", "forwarded", "linked", "summarized"]

proc byText(text: string, rows: seq[ProcessedRow]): (bool, ProcessedRow) =
  ## The message Nim saw with (nearly) the same text. Compares the raw
  ## message text on both sides: a Python forward carries only the caption,
  ## while Nim's published text also has the picture and job-page text.
  if text.strip == "": return
  var best = similarityThreshold
  for r in rows:
    if r.rawText.strip == "": continue
    let s = similarity(text, r.rawText)
    if s >= best:
      best = s
      result = (true, r)

proc compareRuns*(s: State, since: int64): Comparison =
  let seen = s.processedSince(since)
  var nimPublished: seq[ProcessedRow]
  for r in seen:
    if r.action in publishedActions and s.publishedText(r.chatId, r.msgId) != "":
      nimPublished.add r            # (album parts after the first have no text: skipped)
  # The Python bot forwards an album as several messages: one post.
  var python: seq[OtherBotPost]
  var albums = initHashSet[int64]()
  for p in s.otherBotSince(since):
    if p.albumId != 0:
      if p.albumId in albums:
        if python[^1].text.strip == "": python[^1].text = p.text   # the caption may be on a later part
        continue
      albums.incl p.albumId
    python.add p
  var matched = initHashSet[(int64, int64)]()
  for p in python:
    var found = false
    var row: ProcessedRow
    if p.originChat != 0:
      (found, row) = s.processedRow(p.originChat, p.originMsg)
    elif p.originMsg != 0:
      # a link without the chat (t.me/None/<id>): the watched message with
      # that id; if several chats have one, the one Nim published
      let candidates = s.processedByMsgId(p.originMsg)
      var pick = -1
      for i, (chat, action) in candidates:
        if action in publishedActions: pick = i
      if pick < 0 and candidates.len == 1: pick = 0
      if pick >= 0: (found, row) = s.processedRow(candidates[pick][0], p.originMsg)
    if not found:
      # no origin (forwarded from a group), a link that couldn't be resolved,
      # or a forward of a forward (its origin is the first channel)
      (found, row) = byText(p.text, seen)
    if found and (row.action in publishedActions or row.action == "duplicate"):
      result.both.add Compared(other: p, nim: row, nimSaw: true)
      matched.incl (row.chatId, row.msgId)
    else:
      result.pythonOnly.add Compared(other: p, nim: row, nimSaw: found)
  for n in nimPublished:
    if (n.chatId, n.msgId) in matched: continue
    var copy = false          # Python published the same ad from another channel
    for p in python:
      if p.text.strip != "" and n.rawText.strip != "" and
          similarity(p.text, n.rawText) >= similarityThreshold: copy = true
    if not copy:
      result.nimOnly.add Compared(nim: n, nimSaw: true)
