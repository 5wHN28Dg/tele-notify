## Splitting multi-job posts into one block per job (issue #29).
##
## Many channels put several jobs in one post:
##
##     تعلن شركة ... عن حاجتها الى:          <- header (company, location)
##     1- مهندس مدني خبرة 10 سنوات          <- job block
##     2- مهندس كهرباء حديث التخرج           <- job block
##     العنوان: البصرة                         <- footer, belongs to last block
##
## If the whole post is judged as one piece, the role of job 2, the
## experience of job 1 and the location of the footer get mixed up.
##
## The splitting rule is deliberately simple:
## 1. Cut the post before every line that starts a list item ("1-", "2.",
##    "3)", "1️⃣") and at separator lines ("━━━━", "-----"). The text before
##    the first cut is the *header*.
## 2. If the header itself names one of your job titles, the post is about
##    that one job and the list is its requirements: keep the whole post as
##    one block.
## 3. Otherwise every list piece that names a job title starts a job block.
##    Pieces without a title that come *after* a job are glued onto it (they
##    are its requirements or the footer with the address). Pieces without a
##    title that come *before* the first job are other jobs: dropped.
##
## Each job block can see the header (company location, common
## requirements) but never the other jobs.

import std/strutils
import regex
import keywords, normalize

type
  Block* = object
    lines*: seq[string]   ## normalised lines of this job
    isHeader*: bool       ## true when the block is the header/whole post

let
  listItem = re2(r"^\s*(?:\(?\d{1,2}\s*[-.)ـ:]|\d\x{FE0F}?\x{20E3}|[\x{2776}-\x{277F}\x{2460}-\x{2473}]|[•▪◾◽🔹🔸]\s*\d)")
  separator = re2(r"^\s*[━─—_=\-*~•.·]{3,}\s*$")

proc mentionsTitle(lines: seq[string], titles: KeywordList): bool =
  for l in lines:
    if l.flatten.contains(titles): return true

proc splitJobs*(normText: string, titles: KeywordList): seq[Block] =
  ## Returns either one block (the whole post, `isHeader = true`) or a
  ## header block followed by one block per job.
  let lines = normText.splitLines
  var pieces: seq[seq[string]] = @[@[]]
  for l in lines:
    if l.contains(separator):
      pieces.add @[]
      continue
    if l.contains(listItem) and pieces[^1].len > 0:
      pieces.add @[]
    pieces[^1].add l

  let whole = @[Block(lines: lines, isHeader: true)]
  if pieces.len < 2 or pieces[0].mentionsTitle(titles):
    return whole

  result.add Block(lines: pieces[0], isHeader: true)
  for p in pieces[1 .. ^1]:
    if p.mentionsTitle(titles):
      result.add Block(lines: p)
    elif result.len > 1:
      result[^1].lines.add p
  if result.len == 1:
    return whole
