## The filter: post text in, verdict plus reasons out.
##
## This is a *pure* function. It does not touch Telegram, the network or any
## file, so it can be tested with plain strings (see tests/) and run over
## your whole Saved Messages corpus in a second (`telenotify eval`).
##
## The decision, in order. The first rule that fires wins:
##
## 1. Not a job advert at all (job seeker, course ad, ...) -> reject.
## 2. No job-ad wording anywhere (مطلوب، تعلن، hiring, vacancy...) -> reject.
## 3. Split into job blocks (blocks.nim). For each block:
##    a. Find your roles. Ignore a role whose line also says senior / lead /
##       manager / مشرف ... near it. No role left -> reject that block.
##    b. Location (see `locate`). Not Basra -> reject the block. Basra and
##       another city both mentioned with no clear "Location:" line -> review.
##    c. Experience (experience.nim). More than `max_experience_years`
##       required -> reject.
##    d. A mandatory certification -> reject, or review when the post also
##       says "apply even if you don't meet everything" (issue #25).
##    e. Is there *positive* evidence that a fresh graduate is wanted? (a
##       level word like "intern"/"حديث التخرج" that is not about mentoring
##       juniors, "0-1 years", or a role that is entry-level by nature such
##       as help desk). Yes -> publish.
##    f. No evidence either way: a real job title -> review (you decide);
##       only a degree/field mention -> reject.
## 4. The post gets the best verdict of its blocks (publish > review > reject).
##
## The old version published in case (f). That single rule produced 80 of
## the 96 false positives found in the Saved Messages test.

import std/[strutils, unicode]
import regex
import normalize, keywords, config, experience, blocks

type
  Verdict* = enum
    vReject = "reject"
    vReview = "review"
    vPublish = "publish"

  Decision* = object
    verdict*: Verdict
    role*: string           ## the role that matched (shown in link-only forwards)
    reasons*: seq[string]   ## human-readable explanation, for logs and `explain`

proc around(line: string, h: Hit, before, after: int): string =
  line.runeSafe(h.start - before, h.stop + after)

proc short(s: string, n = 70): string =
  let r = s.strip.toRunes
  if r.len <= n: $r else: $r[0 ..< n] & "…"

# ---------------------------------------------------------------- roles

type
  RoleKind = enum rkField, rkTitle, rkEntry
  RoleHit = object
    kind: RoleKind
    keyword: string
    senior: bool
    titleLine: bool   ## found on a line that names the job (see isTitleLine)
    context: string

proc titleLines(lines: seq[string], cfg: Config): seq[bool] =
  ## Which lines name the job: the first two non-empty lines, and lines
  ## like "Position: ..." / "المسمى الوظيفي: ...".
  var seen = 0
  for line in lines:
    var t = false
    if line.strip.len > 0:
      inc seen
      t = seen <= 2 or line.flatten.contains(cfg.titleMarkers)
    result.add t

proc findRoles(lines: seq[string], cfg: Config): seq[RoleHit] =
  let isTitle = titleLines(lines, cfg)
  for li, line in lines:
    let flat = line.flatten
    var excluded: seq[Hit]
    for x in flat.hits(cfg.roleExclusions): excluded.add x
    for (kind, list) in [(rkEntry, cfg.entryRoles), (rkTitle, cfg.titles), (rkField, cfg.fields)]:
      for h in flat.hits(list):
        var inside = false
        for x in excluded:
          if h.start >= x.start and h.stop <= x.stop: inside = true
        if inside: continue # "cost control engineer" is not a control engineer
        # Seniority must be close to the role: up to a few words before it
        # ("Senior Electrical Engineer", "Lead – ICSS Engineer") or right
        # after it ("مهندس كهرباء اول", "IT Support Lead"). Further away it is
        # usually about someone else ("reports to the site manager") or an
        # alternative ("fresh graduates or experienced").
        let near = flat.runeSafe(h.start - 45, h.start) & " " & flat.runeSafe(h.stop, h.stop + 20)
        let senior = near.contains(cfg.seniority)
        result.add RoleHit(kind: kind, keyword: h.keyword, senior: senior,
                           titleLine: isTitle[li], context: flat.around(h, 30, 30))

# ------------------------------------------------------------- location

type
  Place = enum plNone, plBasra, plElsewhere, plMixed

proc basraHits(flat: string, cfg: Config): seq[Hit] =
  for h in flat.hits(cfg.locations):
    if flat.precededBy(h, cfg.streetBefore) or flat.followedBy(h, cfg.streetAfter):
      continue # "شارع البصره" in Baghdad is a street, not the city
    if flat.followedBy(h, cfg.notAPlaceAfter):
      continue # "remote terminal units", "المدينه المنوره"
    result.add h

proc locate(lines: seq[string], cfg: Config, why: var string): Place =
  ## Where is the job?
  ## * A line that says where the job is or where you must live
  ##   ("📍", "Location:", "موقع العمل", "من سكنه ...") decides first.
  ## * Otherwise: Basra mentioned and no other city -> Basra; both -> mixed.
  var markedBasra, markedOther, anyBasra, anyOther = ""
  for line in lines:
    let flat = line.flatten
    let b = flat.basraHits(cfg)
    let (o, oh) = flat.firstHit(cfg.otherPlaces)
    if b.len > 0 and anyBasra == "": anyBasra = b[0].keyword
    if o and anyOther == "": anyOther = oh.keyword
    if line.contains("📍") or flat.contains(cfg.locationMarkers):
      if b.len > 0 and o:
        why = "location line names Basra and elsewhere: '" & line.short & "'"
        return plMixed
      if b.len > 0 and markedBasra == "": markedBasra = line.short
      elif o and markedOther == "": markedOther = line.short
  if markedBasra != "":
    why = "location line: '" & markedBasra & "'"
    return plBasra
  if markedOther != "":
    why = "location line says elsewhere: '" & markedOther & "'"
    return plElsewhere
  if anyBasra != "" and anyOther == "":
    why = "location: " & anyBasra
    return plBasra
  if anyBasra != "":
    why = "mentions both " & anyBasra & " and " & anyOther
    return plMixed
  if anyOther != "":
    why = "location: " & anyOther
    return plElsewhere
  why = "no Basra/remote location"
  plNone

# ---------------------------------------------------- level & certificates

# Experience asked for without a number: "extensive experience", "خبرة
# واسعة". Not a hard no (no years to compare), but it outweighs a generic
# "fresh graduates and experienced" that some channels put on every post
# (found on the first real run).
let strongExperience = re2(r"(?:(?:ال)?خبر(?:ه|ات)\s+(?:(?:ال)?(?:عمليه|ميدانيه|مهنيه|فنيه|عملي|عمل)\s+)?(?:ال)?(?:واسعه|طويله|قويه|كبيره|عاليه|عميقه|متميزه|ممتازه)|(?:^|[^a-z])(?:extensive|proven|strong|solid|significant|substantial|considerable|broad)\s+(?:(?:hands\s+on|practical|professional|relevant|industry|industrial|field|work|working|technical|engineering|commercial|international|previous|prior)\s+)?experience(?:[^a-z]|$))")
# ...unless it's denied ("no extensive experience required", "لا يشترط خبرة
# واسعة") or offered ("you will gain solid practical experience").
let notAsked = re2(r"(?:^|[^a-z])(?:no|not|without|gain|gaining|acquire|build|develop|get|offers?|provides?)(?:[^a-z]|$)|(?:لا|بدون|غير|اكتساب|لاكتساب|تكتسب|يكتسب|نوفر|توفر)(?:\s|$)")

proc strongExperienceAsked(flatLower: string): string =
  ## The first "extensive experience"-like requirement, or "".
  for m in flatLower.findAll(strongExperience):
    let before = flatLower.runeSafe(m.boundaries.a - 30, m.boundaries.a)
    if before.contains(notAsked): continue
    return flatLower[m.boundaries].strip(chars = {' ', '\n', '.', ',', '(', ')'})
  ""

proc levelEvidence(lines: seq[string], cfg: Config): string =
  ## First entry-level word that is not about managing juniors.
  for line in lines:
    let flat = line.flatten
    for h in flat.hits(cfg.levels):
      let before = flat.runeSafe(h.start - 25, h.start)
      if before.contains(cfg.mentoring): continue
      return h.keyword
  ""

proc mandatoryCert(lines: seq[string], cfg: Config): string =
  ## A certification on a line that says it is required, or listed under a
  ## heading that does ("Required certifications:" / "الشهادات المطلوبه:")
  ## until the next empty line.
  var underRequiredHeading = false
  for line in lines:
    let flat = line.flatten
    if line.strip.len == 0:
      underRequiredHeading = false
      continue
    let required = flat.contains(cfg.requiredMarkers) and not flat.contains(cfg.optionalMarkers)
    let (found, h) = flat.firstHit(cfg.certifications)
    if found and (required or underRequiredHeading):
      return h.keyword
    if not found and line.strip.endsWith(":"):
      # a new heading: is it a "required certifications" heading?
      underRequiredHeading = required and
        (flat.contains("certif") or flat.contains("شهاد") or flat.contains("licen"))
  ""

proc anySeniorityOnTitleLines(lines: seq[string], cfg: Config): bool =
  ## Stricter check used only by `publish_when_unsure`: with no positive
  ## evidence at all, any seniority word on a title line ("Project Lead",
  ## "Cybersecurity Specialist – experienced") is enough to hold the post back.
  let isTitle = titleLines(lines, cfg)
  for i, line in lines:
    if isTitle[i] and line.flatten.contains(cfg.seniority): return true
  false

# --------------------------------------------------------------- blocks

type WholePostPlace = object
  ## Location of the whole post, computed at most once per post (computing
  ## it once per job made 3,500-job posts take a minute).
  lines: seq[string]
  done: bool
  place: Place
  why: string

proc get(w: var WholePostPlace, cfg: Config): (Place, string) =
  if not w.done:
    w.place = locate(w.lines, cfg, w.why)
    w.done = true
  (w.place, w.why)

proc judgeBlock(b: Block, header: Block, cfg: Config, whole: var WholePostPlace): Decision =
  let roles = b.lines.findRoles(cfg)
  var best: RoleHit
  var found = false
  for r in roles:
    if r.senior: continue
    if not found or r.kind > best.kind:
      best = r
      found = true
  if not found:
    if roles.len > 0:
      return Decision(verdict: vReject, reasons: @["senior role: '" & roles[0].context.short & "'"])
    return Decision(verdict: vReject, reasons: @["no matching role"])
  result.role = best.keyword
  result.reasons.add "role (" & $best.kind & "): " & best.keyword

  var why = ""
  var place = locate(b.lines & header.lines, cfg, why)
  if place == plNone and whole.lines.len > 0:
    # The address may sit in a footer that was glued to another job.
    (place, why) = whole.get(cfg)
  result.reasons.add why
  case place
  of plNone, plElsewhere:
    result.verdict = vReject
    return
  else: discard

  # The header's requirements ("all applicants need 5 years") apply to
  # every job in it, so it is read together with the job block.
  let exp = parseExperience((b.lines & header.lines).join("\n"))
  for e in exp.evidence: result.reasons.add "experience " & e
  if exp.requiredYears > cfg.maxExperienceYears:
    result.verdict = vReject
    result.reasons.add "needs " & $exp.requiredYears & " years"
    return

  let cert = (b.lines & header.lines).mandatoryCert(cfg)
  let flatAll = (b.lines & header.lines).join("\n").flatten
  if cert != "":
    result.reasons.add "mandatory certification: " & cert
    if flatAll.contains(cfg.applyAnyway):
      result.reasons.add "but says apply anyway"
      result.verdict = vReview
    else:
      result.verdict = vReject
    return

  let level = levelEvidence(b.lines & header.lines, cfg)
  if level != "": result.reasons.add "entry level: " & level
  let positive = level != "" or exp.entryFriendly or best.kind == rkEntry

  if place == plMixed:
    result.verdict = vReview
    return
  var senior = ""
  for r in roles:
    if r.senior and r.titleLine: senior = r.context.short(50)
  # A number of years (or "no experience needed") is a clearer answer than
  # a vague "strong experience", so the rule only applies without one.
  let strong = (if exp.requiredYears > 0 or exp.entryFriendly: ""
                else: flatAll.toLowerAscii.strongExperienceAsked)
  if positive:
    if strong != "":
      result.reasons.add "but asks for '" & strong & "'"
      result.verdict = vReview
    elif senior != "":
      # "KBR is hiring an IT Support Lead" + "IT support" again lower down
      result.reasons.add "but the title line looks senior: '" & senior & "'"
      result.verdict = vReview
    else:
      result.verdict = vPublish
  elif cfg.publishWhenUnsure and best.kind != rkField and senior == "" and strong == "" and
      not anySeniorityOnTitleLines(b.lines, cfg):
    result.verdict = vPublish
    result.reasons.add "no sign of level either way (publish_when_unsure)"
  else:
    result.verdict = vReview
    result.reasons.add "no sign of level either way"

# ------------------------------------------------------------------ main

let jobSeeker = re2(r"(?:محتاج|محتاجه|ابحث|باحث|باحثه|ادور|اريد|احتاج)(?:\s+حاليا)?(?:\s+عن|\s+علي)?\s+(?:فرصه\s+|فرص\s+)?(?:عمل|وظيفه|شغل|مهنه|تعيين)")

proc classify*(text: string, cfg: Config): Decision =
  let norm = text.normalizeText
  let flat = norm.flatten

  let (nj, njHit) = flat.firstHit(cfg.notAJob)
  if nj:
    return Decision(verdict: vReject, reasons: @["not a job advert: '" & njHit.keyword & "'"])
  if (norm.contains(jobSeeker) or flat.contains(cfg.seekerPhrases)) and
      not flat.contains(cfg.employerPhrases):
    return Decision(verdict: vReject, reasons: @["someone looking for a job, not offering one"])
  if not flat.contains(cfg.jobSignals):
    return Decision(verdict: vReject, reasons: @["no job-advert wording"])

  let blocks = norm.splitJobs(cfg.entryRoles & cfg.titles, cfg.jobNouns)
  var noWhole = WholePostPlace()
  if blocks.len == 1:
    return judgeBlock(blocks[0], Block(), cfg, noWhole)
  var whole = WholePostPlace(lines: norm.splitLines)

  result = Decision(verdict: vReject)
  var first = true
  for i, b in blocks:
    if b.isHeader: continue
    var d = judgeBlock(b, blocks[0], cfg, whole)
    d.reasons.insert("job " & $i & " of " & $(blocks.len - 1), 0)
    if first or d.verdict > result.verdict:
      result = d
      first = false
