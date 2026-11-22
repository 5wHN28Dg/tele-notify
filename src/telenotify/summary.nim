## The bot's own summary of a matched post, for when Telegram won't let it
## forward the post (#80, `"forward_fallback": "summary"`).
##
## The old fallback was "match: <role>" and "post link: <link>": for a
## private source, the channel's readers can't open the link. The summary
## says what the job is, where, how to apply and where it came from, so it
## helps even then. Owner's decisions: "Post its own summary" (#135,
## 2026-11-21); "Summary for all, no chat name" and "Arabic" labels (#135,
## 2026-11-22).
##
##   ملخص آلي لإعلان لا يمكن إعادة توجيهه
##   الوظيفة: electrical engineer
##   المكان: البصرة
##   التقديم: عبر LinkedIn، بالبريد الإلكتروني
##   المصدر: Example Jobs
##   رابط المنشور: https://t.me/examplejobs/123     (public chats only)
##
## Rules:
## * **Labelled automatic.** The first line says it's an automatic summary
##   of an advert that can't be forwarded.
## * **Plain text.** The bot sends it with no entities and no markup.
##   Telegram (TDLib's `getTextEntities`, which `sendMessage` applies) still
##   turns some plain text into entities by itself: @names, #tags, $TAGS,
##   /commands, web and e-mail addresses, `ton://` links, 10:30 media
##   timestamps, bank card numbers. So every value is defused
##   (`plainField`): control, invisible and direction characters are
##   dropped (ZWNJ and ZWJ are kept: Persian and Kurdish spelling, emoji),
##   line breaks become spaces, a space goes after "@ # $ / :" and after a
##   dot that isn't followed by a space or another dot, and a run of 12 or
##   more digits (spaces and dashes between them included) is regrouped
##   in fours joined by "·". The only link is the post's own, and only for
##   a public chat.
## * **A private chat is never named**: "محادثة خاصة" (a private chat),
##   and no link.
## * **Nothing is copied from the post itself.** The role and the place
##   are the config's keywords the filter matched (`classify`), as written
##   there (Arabic stays Arabic). A place the filter found mixed (Basra and
##   somewhere else) isn't shown. How to apply is named, never quoted: "by
##   e-mail", not the address (an address would be a link).
## * **Mixed text.** Each value is wrapped in Unicode isolation marks (as
##   the notifications do), so Latin and Arabic don't reorder each other.
## * **Short.** Each value is cut to a few dozen characters; the whole
##   message stays under `maxSummaryRunes`.
## * The link line starts with "رابط المنشور: " (`linkLabel`); `noteOtherBot`
##   (the dry run noting the real bot's posts for `telenotify compare`)
##   reads it, and the old English "post link: " too.

import std/[strutils, unicode]
import regex
import normalize, keywords, boards

const
  maxSummaryRunes* = 400
  maxRoleRunes = 60
  maxPlaceRunes = 40
  maxTitleRunes = 60
  automaticLine* = "ملخص آلي لإعلان لا يمكن إعادة توجيهه"
    ## the first line: an automatic summary of an advert that can't be forwarded
  linkLabel* = "رابط المنشور: "   ## the last line, when there is a link (noteOtherBot reads it)
  privateSource* = "محادثة خاصة"   ## "a private chat": never its name

proc isolate(s: string): string =
  ## First strong isolate .. pop directional isolate: the value keeps its own
  ## direction without touching the line around it.
  "\u2068" & s & "\u2069"

proc dropped(c: int): bool =
  ## Characters a value never keeps: invisible and direction controls (they
  ## could end the isolation early or reorder the line), tag characters and
  ## interlinear annotation marks. ZWNJ (U+200C) and ZWJ (U+200D) stay:
  ## Persian and Kurdish words and emoji sequences need them.
  c == 0x200B or c in 0x200E..0x200F or c in 0x202A..0x202E or c in 0x2060..0x206F or
    c == 0xFEFF or c == 0x00AD or c == 0x061C or c == 0x180E or
    c in 0xFFF9..0xFFFB or c in 0xE0000..0xE007F

proc breaksLine(c: int): bool =
  c < 0x20 or c in 0x7F..0x9F or c == 0x2028 or c == 0x2029

proc isDigit(r: Rune): bool = int(r) < 128 and char(int(r)) in Digits

proc spacer(r: Rune): bool = r == Rune(' ') or r == Rune('-')

proc regroupDigits(rs: seq[Rune]): seq[Rune] =
  ## A run of 12 or more digits, with single spaces or dashes between them
  ## ("4111 1111 1111 1111"), as fours joined by "·": TDLib finds 13 to 19
  ## digits as a bank card even with spaces or dashes, never with "·".
  var i = 0
  while i < rs.len:
    if not rs[i].isDigit:
      result.add rs[i]
      inc i
      continue
    var j = i
    var digits: seq[Rune]
    while j < rs.len and (rs[j].isDigit or (rs[j].spacer and j + 1 < rs.len and rs[j + 1].isDigit and
                                            j > i)):
      if rs[j].isDigit: digits.add rs[j]
      inc j
    if digits.len >= 12:
      for k, d in digits:
        if k > 0 and k mod 4 == 0: result.add Rune(0x00B7)
        result.add d
    else:
      for k in i ..< j: result.add rs[k]
    i = j

proc plainField*(s: string, maxRunes: int): string =
  ## `s` as one short line of inert plain text: see the module's rules.
  ## Cut to `maxRunes` (with "…").
  const sigils = ["@", "#", "$", "/", ":"]
  const dots = [".", "\u3002", "\uFF0E", "\uFF61"]   # . 。 ． ｡
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
  rs = regroupDigits(rs)
  # defuse what Telegram would make an entity by itself: a space after a
  # sigil, and after a dot that isn't followed by a space or another dot
  var cut: seq[Rune]
  for i, r in rs:
    cut.add r
    if i + 1 >= rs.len or rs[i + 1] == Rune(' '): continue
    if $r in sigils or ($r in dots and $rs[i + 1] notin dots): cut.add Rune(' ')
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

const
  byForm* = "عبر استمارة"            ## through a form
  byEmail* = "بالبريد الإلكتروني"     ## by e-mail
  byWhatsApp* = "عبر واتساب"         ## on WhatsApp
  byPhone* = "بالهاتف"               ## by phone

proc boardName(k: BoardKind): string =
  ## "on <board>", the board's own (Latin) name isolated
  case k
  of bkKhana: "عبر \u2068Khana\u2069"
  of bkTaeen: "عبر \u2068Taeen\u2069"
  of bkLinkedInJob, bkLinkedInPost, bkShortLink: "عبر \u2068LinkedIn\u2069"
  of bkWorkableBoard, bkWorkableApply: "عبر \u2068Workable\u2069"
  of bkJobPosting: "عبر موقع توظيف"     # on a job site

proc applyMethods*(text: string, jobSites: openArray[string] = defaultJobPostingSites): seq[string] =
  ## How the post says to apply, in Arabic, named and never quoted: on a
  ## job board the filter knows (boards.nim), through a form, by e-mail, on
  ## WhatsApp, by phone. At most three, in that order.
  for l in findJobLinks(text, jobSites):
    let n = boardName(l.kind)
    if n notin result: result.add n
  let flat = text.normalizeText.flatten
  if text.contains(formRe) or flat.contains(formWords): result.add byForm
  if text.contains(emailRe): result.add byEmail
  if text.contains(whatsAppRe) or flat.contains(whatsAppWords): result.add byWhatsApp
  # a number next to WhatsApp is usually its number: then it says WhatsApp only
  # (normalizeText turns Arabic-Indic digits into 0-9)
  if byWhatsApp notin result and text.normalizeText.contains(phoneRe): result.add byPhone
  if result.len > 3: result.setLen 3

# ------------------------------------------------------------------ the text

proc summaryText*(role, place: string, apply: seq[string], title, link: string): string =
  ## The summary as sent. `link`: the post's link, only for a public chat;
  ## "" for a private one (its link only opens for members), and then
  ## `title` isn't shown either: the source is "محادثة خاصة".
  let r = plainField(role, maxRoleRunes)
  var lines = @[automaticLine,
                "الوظيفة: " & (if r == "": "غير محددة" else: isolate(r))]   # "not named"
  let p = plainField(place, maxPlaceRunes)
  if p != "": lines.add "المكان: " & isolate(p)
  if apply.len > 0: lines.add "التقديم: " & apply.join("، ")
  elif link != "": lines.add "التقديم: التفاصيل في المنشور"               # details in the post
  if link != "":
    let t = plainField(title, maxTitleRunes)
    lines.add "المصدر: " & (if t == "": "قناة عامة" else: isolate(t))   # "a public channel"
    lines.add linkLabel & link
  else:
    lines.add "المصدر: " & privateSource
  result = lines.join("\n")
  if result.runeLen > maxSummaryRunes:     # only a very long link could do this
    result = $result.toRunes[0 ..< maxSummaryRunes - 1] & "…"
