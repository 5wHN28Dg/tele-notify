## Job boards: which links we can follow, and how to read each one.
##
## Some posts are nothing but "#Basrah" and a link. The filter can't judge
## those, so for a link to a known job board we fetch the job and append its
## text to the post. This module is *pure*: it finds links in text and turns
## a downloaded page/API answer into text. The downloading is in fetch.nim,
## so everything here is tested offline against saved pages
## (tests/fixtures/).
##
## Supported boards (checked against the 3,834 latest posts of your
## channels on 2026-11-17):
##
## | Board | Link | How it is read |
## |---|---|---|
## | Khana | khana.jobs/jobs/9915 | JSON API (title, location, description) |
## | Taeen | taeen.iq/ar/jobs/<uuid> | JSON API (title, city, experience years, description) |
## | LinkedIn job | linkedin.com/jobs/view/<id> | page `<title>` + `description__text` block |
## | LinkedIn post | linkedin.com/posts/<slug> | the post's own text (+ the reshared post's), never comments |
## | lnkd.in | lnkd.in/<code> | resolved to its target, which is then read if supported |
## | Sites with schema.org JobPosting | yadanoo.com/job/<id>, shift-iraq.com/jobs/<id>, mselect.com/job/<slug>-<id>, + `extra_job_sites` | the page's JSON-LD `JobPosting` block only |
## | Workable job board | jobs.workable.com/view/<id>/<slug> | the page's JSON-LD `JobPosting` block (+ its `requirementsSection` when the description lacks it; `jobLocationType` ignored) |
## | Workable company page | apply.workable.com/<company>/j/<shortcode>, <company>.workable.com/j/<shortcode> | JSON API (title, locations, description, requirements, benefits) |
##
## Bare apply.workable.com/j/<shortcode> links (no company) are skipped:
## the API needs the company's name.
##
## Workable's job board marks hybrid jobs "TELECOMMUTE" too (a hybrid
## Baghdad job said so on 2026-11-18 while its API said remote: false), so
## on Workable pages that field is not read: "remote" would make any job
## look acceptable.
##
## Not supported any more: Iraq Jobs Scout (iqjscout.com now redirects to
## Yadanoo's home page).
##
## Why JSON-LD only for those sites: a Yadanoo page also contains a city
## picker listing *every* city, Basra included. Reading the whole page
## would make every job look like a Basra job.

import std/[json, strutils, uri]
import regex
import html

type
  BoardKind* = enum
    bkKhana = "Khana"
    bkTaeen = "Taeen"
    bkLinkedInJob = "LinkedIn job"
    bkLinkedInPost = "LinkedIn post"
    bkShortLink = "lnkd.in short link"
    bkJobPosting = "job page (JSON-LD)"
    bkWorkableBoard = "Workable job board"
    bkWorkableApply = "Workable"

  JobLink* = object
    kind*: BoardKind
    url*: string        ## what to download (an API URL or a clean page URL)

const defaultJobPostingSites* = ["yadanoo.com", "shift-iraq.com", "mselect.com"]

const wwwOnlyHosts = ["mselect.com"]
  ## Sites whose bare name has no DNS record: links are read without
  ## "www.", so it is put back before downloading.

let
  # URL characters: stop at spaces, quotes, brackets and the invisible
  # marks Telegram glues to links (U+200E/F, U+2060).
  # All patterns ignore case: people paste "Yadanoo.com", "WWW.LinkedIn.com".
  khana = re2(r"(?i)khana\.jobs/(?:dashboard/)?jobs/(\d+)")
  taeen = re2(r"(?i)taeen\.iq/(?:[a-z]{2}/)?jobs/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
  liJob = re2(r"(?i)linkedin\.com/(?:comm/)?jobs/view/(?:[A-Za-z0-9%_-]*-)?(\d{6,})")
  liPost = re2(r"(?i)linkedin\.com/posts/([A-Za-z0-9%_-]+)")
  shortLink = re2(r"(?i)lnkd\.in/([A-Za-z0-9_-]+)")
  # Workable: the job board (jobs.workable.com, JSON-LD in the page; the
  # path's last part is a slug without digits, so the generic JSON-LD
  # pattern can't see it) and company pages (apply.workable.com, a
  # JavaScript shell: read through the API instead).
  wkView = re2(r"(?i)jobs\.workable\.com/view/([A-Za-z0-9]{16,24})(?![A-Za-z0-9])")
  wkApply = re2(r"(?i)apply\.workable\.com/([a-z0-9][a-z0-9_-]*)/j/([0-9a-f]{10})(?![A-Za-z0-9])")
  # Old-style company links, <company>.workable.com/j/<shortcode>: they
  # redirect to apply.workable.com/<company>/j/<shortcode>.
  wkOld = re2(r"(?i)([a-z0-9][a-z0-9-]*)\.workable\.com/j/([0-9a-f]{10})(?![A-Za-z0-9])")
  # Links end at the first non-ASCII character (Arabic text or punctuation
  # glued to the link, "yadanoo.com/job/55،للتقديم") or at quotes/brackets.
  anyUrl = re2(r"(?i)(?:https?://)?(?:www\.)?((?:[a-z0-9-]+\.)+[a-z]{2,})(/[!#-&*-;=?-Z^-~]*)?")
  ldJson = re2(r"(?s)<script[^>]*application/ld\+json[^>]*>(.*?)</script>")

proc addUnique(list: var seq[JobLink], l: JobLink) =
  for x in list:
    if x.url == l.url: return
  list.add l

proc looksLikeJobPath(path: string): bool =
  ## "/job/55ab", "/jobs/251f8967-..." yes; "/", "/jobs", "/careers" no:
  ## at least two parts, and the last one has a digit (an ID).
  var parts: seq[string]
  for p in path.split('/'):
    if p.len > 0: parts.add p
  if parts.len < 2: return false
  for c in parts[^1]:
    if c in Digits: return true
  false

proc findJobLinks*(text: string, jobSites: openArray[string] = defaultJobPostingSites): seq[JobLink] =
  ## Every supported job link in `text` (post text, button URLs, OCR...),
  ## without duplicates, in order of appearance by board.
  for m in text.findAll(khana):
    result.addUnique JobLink(kind: bkKhana, url: "https://prodclient.up.railway.app/api/v1/jobs/" & text[m.group(0)])
  for m in text.findAll(taeen):
    result.addUnique JobLink(kind: bkTaeen, url: "https://api.taeen.iq/jobs/" & text[m.group(0)].toLowerAscii)
  for m in text.findAll(liJob):
    result.addUnique JobLink(kind: bkLinkedInJob, url: "https://www.linkedin.com/jobs/view/" & text[m.group(0)] & "/")
  for m in text.findAll(liPost):
    result.addUnique JobLink(kind: bkLinkedInPost, url: "https://www.linkedin.com/posts/" & text[m.group(0)] & "/")
  for m in text.findAll(shortLink):
    result.addUnique JobLink(kind: bkShortLink, url: "https://lnkd.in/" & text[m.group(0)])
  for m in text.findAll(wkView):
    # The slugless URL redirects to the full one; the ID alone is enough.
    result.addUnique JobLink(kind: bkWorkableBoard, url: "https://jobs.workable.com/view/" & text[m.group(0)])
  for m in text.findAll(wkApply):
    result.addUnique JobLink(kind: bkWorkableApply, url: "https://apply.workable.com/api/v2/accounts/" &
      text[m.group(0)].toLowerAscii & "/jobs/" & text[m.group(1)].toUpperAscii)
  for m in text.findAll(wkOld):
    let start = m.boundaries.a
    if start > 0 and text[start - 1] in {'@', '.', '-', '_'} + Letters + Digits:
      continue   # part of a longer host name or an e-mail address
    let company = text[m.group(0)].toLowerAscii
    if company in ["jobs", "apply", "www"]: continue
    result.addUnique JobLink(kind: bkWorkableApply, url: "https://apply.workable.com/api/v2/accounts/" &
      company & "/jobs/" & text[m.group(1)].toUpperAscii)
  for m in text.findAll(anyUrl):
    let start = m.boundaries.a
    if start > 0 and text[start - 1] in {'@', '.', '-', '_'} + Letters + Digits:
      continue   # part of an e-mail address or a longer host name
    var host = text[m.group(0)].toLowerAscii
    if host in wwwOnlyHosts: host = "www." & host
    for site in jobSites:
      if (host == site or host.endsWith("." & site)) and m.group(1).a >= 0:
        let path = text[m.group(1)].split({'?', '#'})[0].strip(leading = false, chars = {'/', '.', ','})
        if path.looksLikeJobPath:
          result.addUnique JobLink(kind: bkJobPosting, url: "https://" & host & path)

proc isSupported*(url: string, jobSites: openArray[string] = defaultJobPostingSites): bool =
  findJobLinks(url, jobSites).len > 0

# ------------------------------------------------------------ extractors

proc line(label, value: string): string =
  if value.strip.len == 0: "" else: label & ": " & value.strip & "\n"

proc str(j: JsonNode, keys: varargs[string]): string =
  ## j[k1][k2]... as a string, "" when anything is missing or null.
  var n = j
  for k in keys:
    if n == nil or n.kind != JObject or not n.hasKey(k): return ""
    n = n[k]
  case n.kind
  of JString: n.getStr
  of JInt: $n.getInt
  of JFloat: $n.getFloat
  else: ""

proc extractKhana(body: string): string =
  let j = parseJson(body)
  if not j{"success"}.getBool(false): return ""
  let d = j["data"]
  result = line("Job title", d.str("title")) &
           line("Location", [d.str("location"), d.str("province")].join(" - ")) &
           d.str("description").htmlFragmentToText

proc extractTaeen(body: string): string =
  let j = parseJson(body)
  var title = j.str("title")
  let jt = j.str("jobTitle", "name", "en")
  if jt != "" and jt.toLowerAscii notin title.toLowerAscii: title.add " (" & jt & ")"
  var place = [j.str("city", "name", "ar"), j.str("city", "name", "en")].join(" - ")
  if j.str("workLocationType") == "remote": place = "remote (عن بعد)"
  result = line("Job title", title) & line("Location", place)
  let years = j.str("experienceYears")
  if years != "": result.add line("Experience", years & " years")
  result.add j.str("description").htmlFragmentToText & "\n" &
             j.str("requirements").htmlFragmentToText

proc squeeze(s: string): string =
  ## Lowercase text with every run of whitespace turned into one space and
  ## lone bullets dropped (a `<li>` becomes "- ", a `<p>` doesn't).
  for w in s.splitWhitespace:
    if w in ["-", "–", "—", "•", "*", "·"]: continue
    if result.len > 0: result.add ' '
    result.add w.toLowerAscii

proc jobPostingFromLd(body: string, workable = false): string =
  ## Text of the first schema.org JobPosting in the page's JSON-LD blocks.
  ## `workable`: a jobs.workable.com page, whose jobLocationType says
  ## TELECOMMUTE even for hybrid jobs, so it is ignored there.
  for m in body.findAll(ldJson):
    var j: JsonNode
    try: j = parseJson(body[m.group(0)])
    except CatchableError: continue
    var candidates = @[j]
    if j.kind == JArray: candidates = j.getElems
    elif j.kind == JObject and j{"@graph"} != nil and j["@graph"].kind == JArray:
      candidates = j["@graph"].getElems
    for c in candidates:
      if c.kind != JObject or c.str("@type") != "JobPosting": continue
      var places: seq[string]
      let loc = c{"jobLocation"}
      for l in (if loc != nil and loc.kind == JArray: loc.getElems else: @[loc]):
        if l == nil: continue
        let p = [l.str("address", "addressLocality"), l.str("address", "addressRegion")].join(" ").strip
        if p != "": places.add p
      if not workable and c.str("jobLocationType") == "TELECOMMUTE": places.add "remote"
      var r = line("Job title", c.str("title")) & line("Location", places.join(", "))
      let exp = c.str("experienceRequirements", "monthsOfExperience")
      if exp != "": r.add line("Experience", exp & " months")
      let desc = c.str("description").htmlFragmentToText
      r.add desc
      # Some pages keep the requirements ("3+ years of ...") apart; Workable
      # repeats them in the description too, so add them only when missing.
      let req = c.str("requirementsSection").htmlFragmentToText
      if req.strip != "" and req.squeeze notin desc.squeeze: r.add "\n" & req
      return r
  ""

proc extractWorkableApply(body: string): string =
  let j = parseJson(body)
  if j.kind != JObject or j.str("title") == "": return ""
  var places: seq[string]
  # "location" is the main place; "locations" lists all of them (a job can
  # be in Baghdad and Basra).
  var locs = @[j{"location"}]
  if j{"locations"} != nil and j["locations"].kind == JArray: locs.add j["locations"].getElems
  for l in locs:
    for k in ["city", "region", "country"]:
      let p = l.str(k)
      if p != "" and p notin places: places.add p
  let remote = j{"remote"}
  let isRemote = remote != nil and (remote.getBool(false) or
    (remote.kind == JString and remote.getStr.strip.toLowerAscii in ["true", "yes", "1"]))
  if isRemote or j.str("workplace") == "remote": places.add "remote"
  result = line("Job title", j.str("title")) & line("Location", places.join(", "))
  for k in ["description", "requirements", "benefits"]:
    let t = j.str(k).htmlFragmentToText
    if t.strip != "": result.add t & "\n"

proc extractLinkedInJob(body: string): string =
  let doc = parsePage(body)
  # "<role> at <company> — Basra, Al-Basra Governorate, Iraq | LinkedIn Jobs":
  # the title even carries the location.
  let title = doc.pageTitle.replace(" | LinkedIn Jobs", "").replace(" | LinkedIn", "")
  var desc = doc.findAllByClass("description__text")
  if desc.len == 0: desc = doc.findAllByClass("show-more-less-html__markup")
  if desc.len == 0: return ""
  line("Job title", title) & desc[0].toText

proc extractLinkedInPost(body: string): string =
  ## Only the post's own text: the author's words and, for a reshare, the
  ## reshared post's words (that's where the job usually is; issue #34 was
  ## "#فرصة_عمل" resharing a job ad). Comments under the post use similar
  ## blocks and anyone can write them, so they are never read: taking "the
  ## longest block" let a long comment replace the post.
  let doc = parsePage(body)
  var parts: seq[string]
  for id in ["main-feed-activity-card__commentary", "feed-reshare-content__commentary"]:
    let n = doc.findByAttr("data-test-id", id)
    if n != nil: parts.add n.toText
  parts.join("\n\n").strip

proc extract*(kind: BoardKind, body: string): string =
  ## Text of a downloaded job, or "" when the page isn't what we expect
  ## (expired job, login wall, layout change). Never raises.
  try:
    case kind
    of bkKhana: extractKhana(body)
    of bkTaeen: extractTaeen(body)
    of bkLinkedInJob: extractLinkedInJob(body)
    of bkLinkedInPost: extractLinkedInPost(body)
    of bkJobPosting: jobPostingFromLd(body)
    of bkWorkableBoard: jobPostingFromLd(body, workable = true)
    of bkWorkableApply: extractWorkableApply(body)
    of bkShortLink: ""
  except CatchableError:
    ""

proc shortLinkTarget*(body, finalUrl: string): string =
  ## Where an lnkd.in link leads. Usually the HTTP redirect already took us
  ## there (`finalUrl`). Otherwise LinkedIn shows a "this link will take you
  ## to a page that's not on LinkedIn" page: take the first outside link.
  if "lnkd.in/" notin finalUrl: return finalUrl
  for m in body.findAll(re2("href=\"(https?://[^\"]+)\"")):
    let u = body[m.group(0)]
    let host = parseUri(u).hostname.toLowerAscii
    if host.endsWith("linkedin.com") or host.endsWith("licdn.com") or host.endsWith("lnkd.in"):
      continue
    return u
  ""
