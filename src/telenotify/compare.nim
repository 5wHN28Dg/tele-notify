## Stage 4: the Nim bot (dry run) next to the Python bot.
##
## While `telenotify run --dry-run` runs, it also notes every post the
## Python bot sends to your channel (they show up as your own outgoing
## messages there: forwards, or "match: ... post link: ..." texts). Then
## `telenotify compare` puts the two side by side:
##
## * **both**: published by both bots (or Nim skipped it as a copy of an
##   ad it had already published from another channel);
## * **Python only**: with Nim's verdict and reasons, so you can see who
##   was right;
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

const publishedActions = ["would publish", "forwarded", "linked"]

proc compareRuns*(s: State, since: int64): Comparison =
  var nimPublished: seq[ProcessedRow]
  for r in s.processedSince(since):
    if r.action in publishedActions and s.publishedText(r.chatId, r.msgId) != "":
      nimPublished.add r            # (album parts after the first have no text: skipped)
  var matched = initHashSet[(int64, int64)]()
  var pythonTexts: seq[string]
  for p in s.otherBotSince(since):
    pythonTexts.add p.text
    var found = false
    var row: ProcessedRow
    if p.originChat != 0:
      (found, row) = s.processedRow(p.originChat, p.originMsg)
    if not found and p.text.strip != "":
      # no origin (forwarded from a group), or Nim judged another copy
      for n in nimPublished:
        if similarity(p.text, s.publishedText(n.chatId, n.msgId)) >= similarityThreshold:
          (found, row) = (true, n)
          break
    if found and (row.action in publishedActions or row.action == "duplicate"):
      result.both.add Compared(other: p, nim: row, nimSaw: true)
      matched.incl (row.chatId, row.msgId)
    else:
      result.pythonOnly.add Compared(other: p, nim: row, nimSaw: found)
  for n in nimPublished:
    if (n.chatId, n.msgId) in matched: continue
    let text = s.publishedText(n.chatId, n.msgId)
    var copy = false
    for t in pythonTexts:
      if similarity(t, text) >= similarityThreshold: copy = true
    if not copy:
      result.nimOnly.add Compared(nim: n, nimSaw: true)
