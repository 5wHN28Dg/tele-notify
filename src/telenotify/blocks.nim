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
## 2. If no list piece names one of your job titles, the post is one block
##    (a single job, the list being its requirements).
## 3. Otherwise every list piece that names one of your titles starts a job
##    block. A numbered piece without one of your titles is either
##    * another job, if it names *some* job ("2- محاسب", see `jobNouns`):
##      dropped, or
##    * a requirement ("2- خبرة 4 سنوات"): glued onto the job above it.
##    An un-numbered piece after a job (cut off by a separator line) is glued
##    onto that job too. If a job block has no location of its own or in the
##    header, the whole post is searched (the address may be in a footer
##    that ended up attached to a dropped job).
## 4. When the list is compact (every item one line), whatever follows the
##    last item is a shared footer ("المتطلبات: خبرة 5 سنوات", the address)
##    and is treated like the header: it applies to every job.
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
  listItem = re2(r"^\s*(?:\(?\d{1,2}\s*[-.)ـ:](?:\s|$)|\d\x{FE0F}?\x{20E3}|[\x{2776}-\x{277F}\x{2460}-\x{2473}]|[•▪◾◽🔹🔸]\s*\d)")
  separator = re2(r"^\s*[━─—_=\-*~•.·]{3,}\s*$")

proc mentionsTitle(lines: seq[string], titles: KeywordList): bool =
  for l in lines:
    if l.flatten.contains(titles): return true

proc splitJobs*(normText: string, titles, jobNouns: KeywordList): seq[Block] =
  ## Returns either one block (the whole post, `isHeader = true`) or a
  ## header block followed by one block per job.
  let lines = normText.splitLines
  var pieces: seq[seq[string]] = @[@[]]
  var numbered = @[false]   # does piece i start with a list number?
  for l in lines:
    if l.contains(separator):
      pieces.add @[]
      numbered.add false
      continue
    if l.contains(listItem) and pieces[^1].len > 0:
      pieces.add @[]
      numbered.add true
    pieces[^1].add l

  let whole = @[Block(lines: lines, isHeader: true)]
  var titledPieces = 0
  for i in 1 ..< pieces.len:
    if numbered[i] and pieces[i].mentionsTitle(titles): inc titledPieces
  if titledPieces == 0:
    return whole

  # A compact title list ("1- مهندس كهرباء\n2- مهندس ميكانيك\n3- ...") is
  # usually followed by requirements that apply to *every* job. If every
  # numbered item but the last is a single line, whatever follows the last
  # item's first line is such a shared footer: it joins the header.
  var footer: seq[string]
  var lastNumbered = -1
  for i in 1 ..< pieces.len:
    if numbered[i]: lastNumbered = i
  var compact = lastNumbered > 0
  for i in 1 ..< lastNumbered:
    if numbered[i] and pieces[i].len != 1: compact = false
  if compact:
    footer = pieces[lastNumbered][1 .. ^1]
    pieces[lastNumbered] = pieces[lastNumbered][0 .. 0]
    for i in lastNumbered + 1 ..< pieces.len: footer.add pieces[i]
    pieces.setLen(lastNumbered + 1)

  result.add Block(lines: pieces[0] & footer, isHeader: true)
  for i in 1 ..< pieces.len:
    let p = pieces[i]
    if p.mentionsTitle(titles):
      result.add Block(lines: p)
    elif numbered[i] and p.mentionsTitle(jobNouns):
      discard                  # another job that isn't yours
    elif result.len > 1:
      result[^1].lines.add p   # a requirement, footer or continuation
    else:
      result[0].lines.add p    # requirements before any job: shared
  if result.len == 1:
    return whole
