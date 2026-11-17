## Keyword lists and how they are searched.
##
## The old Python code compiled every list into one big regex. Here a list is
## searched keyword by keyword instead. It is plenty fast for a few hundred
## keywords and it tells us exactly *which* keyword matched, which is what the
## verdict reasons (and your debugging) need.
##
## Matching rules (applied to `flatten(normalizeText(text))`):
## * The left edge must be a word start. For Arabic, the common one-letter
##   prefixes و ف ب ل ك and the article ال (and combos like وال، بال، لل) are
##   allowed in front, so "بالبصره" still contains "البصره"/"بصره".
## * English keywords must also end at a word end ("intern" does not match
##   "internal"). Arabic keywords may be followed by more letters, so
##   "مبرمج" also matches "مبرمجين" / "مبرمجه". (Note that this only helps at
##   the *end*: "مهندس كهرباء" does not match "مهندس كهربائي"; add both.)

import std/[unicode, strutils]
import normalize

type
  Keyword* = object
    raw*: string       ## as written in the config, for display
    norm*: string      ## normalised + flattened form that is searched for
    latin: bool        ## true when the keyword has no Arabic letters
    loose: bool        ## only check the left edge (OCR glues words: "CCNAor")
    strict: bool       ## Arabic too must end at a word end ("اول" not in "الاولمبيه")

  KeywordList* = seq[Keyword]

  Hit* = object
    keyword*: string   ## the keyword as written in the config
    start*, stop*: int ## byte range of the hit in the searched string

proc hasArabic(s: string): bool =
  for r in s.runes:
    if int(r) in 0x0600..0x06FF: return true

proc toKeywords*(words: openArray[string], loose = false, strict = false): KeywordList =
  for w in words:
    let n = w.normalizeText.flatten.strip
    if n.len == 0: continue
    result.add Keyword(raw: w.strip, norm: n, latin: not n.hasArabic, loose: loose,
                       strict: strict)

proc runeBefore(s: string, i: int): Rune =
  if i <= 0: return Rune(' ')
  var j = i - 1
  while j > 0 and (s[j].uint8 and 0xC0) == 0x80: dec j
  s.runeAt(j)

const arabicPrefixes = ["وال", "بال", "فال", "كال", "لل", "ال", "و", "ف", "ب", "ل", "ك"]

proc arabicWordStart(s: string, i: int): bool =
  ## True when position `i` begins a word, possibly after a short prefix.
  if not s.runeBefore(i).isWordRune: return true
  for p in arabicPrefixes:
    let j = i - p.len
    if j >= 0 and s[j ..< i] == p and not s.runeBefore(j).isWordRune:
      return true
  false

proc matchAt(s: string, i: int, k: Keyword): bool =
  if k.latin:
    if s.runeBefore(i).isWordRune: return false
    if k.loose: return true
    let e = i + k.norm.len
    e >= s.len or not s.runeAt(e).isWordRune
  else:
    if not s.arabicWordStart(i): return false
    if not k.strict: return true
    let e = i + k.norm.len
    e >= s.len or not s.runeAt(e).isWordRune

iterator hits*(s: string, list: KeywordList): Hit =
  ## Every place a keyword of `list` occurs in `s` (`s` must already be
  ## normalised and flattened).
  for k in list:
    var i = s.find(k.norm)
    while i >= 0:
      if s.matchAt(i, k):
        yield Hit(keyword: k.raw, start: i, stop: i + k.norm.len)
      i = s.find(k.norm, i + 1)

proc firstHit*(s: string, list: KeywordList): (bool, Hit) =
  for h in s.hits(list): return (true, h)

proc contains*(s: string, list: KeywordList): bool = s.firstHit(list)[0]

proc precededBy*(s: string, h: Hit, words: KeywordList): bool =
  ## True if one of `words` sits right before the hit, e.g. "شارع" in
  ## "شارع البصره" (a street in another city, not Basra itself).
  var cut = h.start
  # Step over an Arabic prefix glued to the hit ("ال" in "البصره" when the
  # keyword was "بصره"), so the word before the *whole* word is checked.
  while cut > 0 and s.runeBefore(cut).isWordRune:
    cut -= s.runeBefore(cut).size
  let before = s[0 ..< cut].strip(leading = false)
  for w in words:
    if before.endsWith(w.norm):
      let j = before.len - w.norm.len
      if not before.runeBefore(j).isWordRune: return true
  false

proc followedBy*(s: string, h: Hit, words: KeywordList): bool =
  ## True if one of `words` comes right after the hit ("basra street").
  let after = s[h.stop .. ^1].strip(trailing = false)
  for w in words:
    if after.startsWith(w.norm):
      let e = w.norm.len
      if e >= after.len or not after.runeAt(e).isWordRune: return true
  false
