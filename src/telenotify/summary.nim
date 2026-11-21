## The bot's own summary of a matched post, for when Telegram won't let it
## forward the post (#80, `"forward_fallback": "summary"`).
##
## The old fallback was "match: <role>" and "post link: <link>": for a
## private source, the channel's readers can't open the link. The summary
## says what the job is, where, how to apply and where it came from, so it
## helps even then. Owner's decision, 2026-11-21 (#135): "Post its own
## summary".
##
##   Job match: electrical engineer
##   Place: Basra
##   Apply: on LinkedIn, by e-mail
##   From: Basra Jobs
##   Post link: https://t.me/basrajobs/123      (public chats only)
##
## Rules:
## * **Plain text.** The bot sends it with no entities and no markup.
##   Telegram still turns some plain text into links by itself (@names,
##   #tags, $TAGS, /commands, web and e-mail addresses), so text that
##   comes from outside (the chat's title, and the config's words to be
##   safe) is defused (`plainField`): control, invisible and direction
##   characters are dropped, line breaks become spaces, and a space goes
##   after "@", "#", "$", "/" and a dot that's glued to the next word. The
##   only link is the post's own, and only for a public chat.
## * **Nothing is copied from the post itself.** The role and the place
##   are the config's keywords the filter matched (`classify`), as written
##   there (Arabic stays Arabic). How to apply is named, never quoted:
##   "by e-mail", not the address (an address would be a link).
## * **Mixed text.** Each value is wrapped in Unicode isolation marks (as
##   the notifications do), so an Arabic role or title can't reorder the
##   English label around it.
## * **Short.** Each value is cut to a few dozen characters; the whole
##   message stays under `maxSummaryRunes`.
## * The link line starts with "Post link: ", like the old text's "post
##   link: ", so `noteOtherBot` (the dry run noting the real bot's posts
##   for `telenotify compare`) reads both.

import std/[strutils, unicode]
import regex
import normalize, keywords, boards

const
  maxSummaryRunes* = 400
  maxRoleRunes = 60
  maxPlaceRunes = 40
  maxTitleRunes = 60
  linkLabel* = "Post link: "   ## the last line, when there is a link (noteOtherBot reads it)

proc isolate(s: string): string =
  ## First strong isolate .. pop directional isolate: the value keeps its own
  ## direction without touching the line around it.
  "⁨" & s & "⁩"

proc dropped(c: int): bool =
  ## Characters a value never keeps: invisible and direction controls (they
  ## could end the isolation early or reorder the line), C0/C1 controls,
  ## tag characters and interlinear annotation marks.
  c in 0x200B..0x200F or c in 0x202A..0x202E or c in 0x2060..0x206F or
    c == 0xFEFF or c == 0x00AD or c == 0x061C or c == 0x180E or
    c in 0xFFF9..0xFFFB or c in 0xE0000..0xE007F

proc breaksLine(c: int): bool =
  c < 0x20 or c in 0x7F..0x9F or c == 0x2028 or c == 0x2029

proc plainField*(s: string, maxRunes: int): string =
  ## `s` as one short line of inert plain text: see the module's rules.
  ## Cut to `maxRunes` (with "…").
  const sigils = ["@", "#", "$", "/", ".", "。", "．", "｡"]   # dots: 。 ． ｡
  var rs: seq[Rune]
  var space = true                 # leading spaces go
  for r in s.runes:
    let c = int(r)
    if c.dropped: continue
    if c.breaksLine or r.isWhiteSpace:
      if not space: rs.add Rune(' ')
      space = true
      continue
    rs.add r
    space = false
  while rs.len > 0 and rs[^1] == Rune(' '): rs.setLen rs.len - 1
  # defuse what Telegram would make a link by itself
  var defused: seq[Rune]
  for i, r in rs:
    defused.add r
    if $r in sigils and i + 1 < rs.len and rs[i + 1].isWordRune: defused.add Rune(' ')
  # long runs of digits: Telegram can mark 13 to 19 digits as a bank card
  var digits = 0
  var cut: seq[Rune]
  for r in defused:
    if int(r) < 128 and char(int(r)) in Digits:
      if digits > 0 and digits mod 4 == 0: cut.add Rune(' ')
      inc digits
    else: digits = 0
    cut.add r
  if cut.len > maxRunes:
    cut.setLen maxRunes - 1
    while cut.len > 0 and cut[^1] == Rune(' '): cut.setLen cut.len - 1
    cut.add Rune(0x2026)           # …
  $cut

# ------------------------------------------------------------- how to apply

let
  emailRe = re2(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
  formRe = re2(r"(?i)forms\.gle/|docs\.google\.com/forms|forms\.office\.com|form\.jotform\.com")
  whatsAppRe = re2(r"(?i)wa\.me/|whatsapp")
  # an Iraqi mobile number (07xx xxx xxxx, +964 7xx ...), spaces or dashes allowed
  phoneRe = re2(r"(?:\+|00)?(?:964[\s-]?|0)7\d{2}[\s-]?\d{3}[\s-]?\d{4}")
  whatsAppWords = toKeywords(["واتس", "وتساب"])     # Arabic keywords match word starts: واتساب too
  formWords = toKeywords(["google form", "استمارة", "فورم"])

proc boardName(k: BoardKind): string =
  case k
  of bkKhana: "on Khana"
  of bkTaeen: "on Taeen"
  of bkLinkedInJob, bkLinkedInPost, bkShortLink: "on LinkedIn"
  of bkWorkableBoard, bkWorkableApply: "on Workable"
  of bkJobPosting: "on a job site"

proc applyMethods*(text: string, jobSites: openArray[string] = defaultJobPostingSites): seq[string] =
  ## How the post says to apply, named and never quoted: "on LinkedIn" (a
  ## job board the filter knows, boards.nim), "through a form", "by
  ## e-mail", "on WhatsApp", "by phone". At most three, in that order.
  for l in findJobLinks(text, jobSites):
    let n = boardName(l.kind)
    if n notin result: result.add n
  let flat = text.normalizeText.flatten
  if text.contains(formRe) or flat.contains(formWords): result.add "through a form"
  if text.contains(emailRe): result.add "by e-mail"
  if text.contains(whatsAppRe) or flat.contains(whatsAppWords): result.add "on WhatsApp"
  # a number next to WhatsApp is usually its number: then it says WhatsApp only
  # (normalizeText turns Arabic-Indic digits into 0-9)
  if "on WhatsApp" notin result and text.normalizeText.contains(phoneRe): result.add "by phone"
  if result.len > 3: result.setLen 3

# ------------------------------------------------------------------ the text

proc summaryText*(role, place: string, apply: seq[string], title, link: string): string =
  ## The summary as sent. `link`: the post's link, only for a public chat
  ## ("" for a private one: its link only opens for members).
  let r = plainField(role, maxRoleRunes)
  var lines = @[if r == "": "Job match (no role named)" else: "Job match: " & isolate(r)]
  let p = plainField(place, maxPlaceRunes)
  if p != "": lines.add "Place: " & isolate(p)
  if apply.len > 0: lines.add "Apply: " & apply.join(", ")
  elif link != "": lines.add "Apply: see the post"
  let t = plainField(title, maxTitleRunes)
  let source = if t == "": "a chat" else: isolate(t)
  if link != "":
    lines.add "From: " & source
    lines.add linkLabel & link
  else:
    lines.add "From: " & source & " (a private chat, so no link)"
  result = lines.join("\n")
  if result.runeLen > maxSummaryRunes:     # only a very long link could do this
    result = $result.toRunes[0 ..< maxSummaryRunes - 1] & "…"
