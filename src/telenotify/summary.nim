## The bot's own summary of a matched post, for when Telegram won't let it
## forward the post (#80, `"forward_fallback": "summary"`).
##
## The old fallback was "match: <role>" and "post link: <link>": for a
## private source, the channel's readers can't open the link. The summary
## says what the job is, where, how to apply and where it came from, so it
## helps even then. Owner's decisions (#135): "Post its own summary"
## (2026-11-21); "Arabic" labels and "Public only; private to review
## (Recommended)" (2026-11-22): only a public channel's posts are summarized;
## a blocked post from a private or members-only chat goes to the review
## queue instead (listener.nim), and nothing is posted.
##
##   ملخص آلي لإعلان لا يمكن إعادة توجيهه
##   الوظيفة: electrical engineer
##   المكان: البصرة
##   التقديم: عبر LinkedIn، بالبريد الإلكتروني
##   المصدر: Example Jobs
##   رابط المنشور: https://t.me/examplejobs/123
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
##   dropped, and so are letters that look blank (ZWNJ and ZWJ are kept:
##   Persian and Kurdish spelling, emoji); line breaks become spaces; a
##   ".", "/" or ":" between two digits (any script) gets a word joiner
##   (U+2060) after it ("10:30", "1.500.000", "22/11/2026" look the same,
##   read in order in Arabic, and are no timestamp, link or IP address);
##   elsewhere a space goes after "@ # $ / :" and after a dot that isn't
##   followed by a space or another dot; and in a run TDLib would take as a
##   card number (13 to 19 digits with the card checksum, any spaces and
##   dashes between groups, not right after "+") each separator gets a "·",
##   each number kept as it was ("4111 · 1111 · …"; one group is split in
##   fours). Other numbers are untouched.
##   The only link is the post's own.
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
import unicodedb/properties
import normalize, keywords, boards

const
  maxSummaryRunes* = 400
  maxRoleRunes = 60
  maxPlaceRunes = 40
  maxTitleRunes = 60
  automaticLine* = "ملخص آلي لإعلان لا يمكن إعادة توجيهه"
    ## the first line: an automatic summary of an advert that can't be forwarded
  linkLabel* = "رابط المنشور: "   ## the last line, when there is a link (noteOtherBot reads it)

proc isolate(s: string): string =
  ## First strong isolate .. pop directional isolate: the value keeps its own
  ## direction without touching the line around it.
  "\u2068" & s & "\u2069"

proc dropped(c: int): bool =
  ## Characters a value never keeps: invisible and direction controls (they
  ## could end the isolation early or reorder the line), tag characters,
  ## interlinear annotation marks, and letters that look blank (Hangul
  ## fillers, the Braille blank), which could make a name look empty. ZWNJ
  ## (U+200C) and ZWJ (U+200D) stay: Persian and Kurdish words and emoji
  ## sequences need them.
  c == 0x200B or c in 0x200E..0x200F or c in 0x202A..0x202E or c in 0x2060..0x206F or
    c == 0xFEFF or c == 0x00AD or c == 0x061C or c == 0x180E or
    c in 0xFFF9..0xFFFB or c in 0xE0000..0xE007F or
    c in [0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800, 0x17B4, 0x17B5]

proc breaksLine(c: int): bool =
  c < 0x20 or c in 0x7F..0x9F or c == 0x2028 or c == 0x2029

proc isDigit(r: Rune): bool = int(r) < 128 and char(int(r)) in Digits

proc isAnyDigit(r: Rune): bool =
  ## Any decimal digit: 0-9, Arabic-Indic, Persian ...
  r.unicodeCategory == ctgNd

proc separator(r: Rune): bool =
  ## What TDLib accepts between the digits of a bank card number.
  r == Rune(' ') or r == Rune('-')

const middleDot = Rune(0x00B7)   ## "·": TDLib never takes it as part of a card number

proc luhn(digits: seq[Rune]): bool =
  ## The card number checksum.
  var sum = 0
  for i in 0 ..< digits.len:
    var d = int(digits[digits.len - 1 - i]) - ord('0')
    if i mod 2 == 1:
      d *= 2
      if d > 9: d -= 9
    sum += d
  sum mod 10 == 0

proc cardLike(digits: seq[Rune]): bool =
  ## What TDLib marks as a bank card number: 13 to 19 digits (in one run,
  ## with any spaces and dashes between groups) that pass the checksum.
  digits.len in 13..19 and luhn(digits)

proc defuseDigits(rs: seq[Rune]): seq[Rune] =
  ## A run of digits TDLib would mark as a bank card number (`cardLike`),
  ## also with any runs of spaces and dashes between groups ("4111 - 1111
  ## -- 1111 1111"), is kept, each number as it was, but its separators get
  ## a "·" ("4111 · 1111 · …"); a single group is split in fours by "·".
  ## Any other number (a year, a phone number, separate numbers) is left
  ## as it is.
  var i = 0
  while i < rs.len:
    if not rs[i].isDigit:
      result.add rs[i]
      inc i
      continue
    # groups of digits joined by runs of spaces and dashes
    var groups: seq[seq[Rune]]
    var seps: seq[seq[Rune]]
    var j = i
    while true:
      var g: seq[Rune]
      while j < rs.len and rs[j].isDigit:
        g.add rs[j]
        inc j
      groups.add g
      var k = j
      while k < rs.len and rs[k].separator: inc k
      if k > j and k < rs.len and rs[k].isDigit:
        seps.add rs[j ..< k]
        j = k
      else: break
    var all: seq[Rune]
    for g in groups: all.add g
    # TDLib takes no card from a run right after "+" (a phone number: "+964 …")
    let card = all.cardLike and not (i > 0 and rs[i - 1] == Rune('+'))
    for n, g in groups:
      if n > 0:
        if card:
          let sep = seps[n - 1]
          if Rune(' ') in sep: result.add @[Rune(' '), middleDot, Rune(' ')]
          else: result.add middleDot
        else: result.add seps[n - 1]
      if card and groups.len == 1:
        for k, d in g:
          if k > 0 and k mod 4 == 0: result.add middleDot
          result.add d
      else: result.add g
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
  rs = defuseDigits(rs)
  # defuse what Telegram would make an entity by itself: a space after a
  # sigil, and after a dot that isn't followed by a space or another dot.
  # Between two digits (any script) a ".", "/" or ":" ("1.500.000",
  # "22/11/2026", "10:30" or "٩:٣٠", "192.168.1.1") keeps its place and
  # gets a word joiner (U+2060) after it instead: no timestamp, link or IP
  # address to TDLib, and the number still reads in order inside Arabic
  # text, which a space wouldn't keep.
  var cut: seq[Rune]
  for i, r in rs:
    cut.add r
    if i + 1 >= rs.len or rs[i + 1] == Rune(' '): continue
    let betweenDigits = i > 0 and rs[i - 1].isAnyDigit and rs[i + 1].isAnyDigit
    if betweenDigits and ($r in [":", "/"] or $r in dots): cut.add Rune(0x2060)
    elif $r in sigils or ($r in dots and $rs[i + 1] notin dots): cut.add Rune(' ')
  if cut.len > maxRunes:
    cut.setLen maxRunes - 1
    while cut.len > 0 and cut[^1] == Rune(' '): cut.setLen cut.len - 1
    cut.add Rune(0x2026)           # …
    cut = defuseDigits(cut)        # a long number cut short may now be card-like
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
  ## The summary as sent, for a public channel only: `title` is its name and
  ## `link` the post's link (the listener never summarizes a private or
  ## members-only chat's post: it goes to review instead, #135).
  let r = plainField(role, maxRoleRunes)
  var lines = @[automaticLine,
                "الوظيفة: " & (if r == "": "غير محددة" else: isolate(r))]   # "not named"
  let p = plainField(place, maxPlaceRunes)
  if p != "": lines.add "المكان: " & isolate(p)
  if apply.len > 0: lines.add "التقديم: " & apply.join("، ")
  else: lines.add "التقديم: التفاصيل في المنشور"               # details in the post
  let t = plainField(title, maxTitleRunes)
  lines.add "المصدر: " & (if t == "": "قناة عامة" else: isolate(t))   # "a public channel"
  if link != "": lines.add linkLabel & link
  result = lines.join("\n")
  if result.runeLen > maxSummaryRunes:     # only a very long link could do this
    result = $result.toRunes[0 ..< maxSummaryRunes - 1] & "…"
