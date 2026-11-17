## Text normalisation.
##
## Everything the filter compares (post text *and* keywords) goes through
## `normalizeText` first, so the two sides always agree. This is what lets a
## keyword like "مهندس أتمتة" match a post that wrote "مهندس اتمتة", or
## "electrical engineer" match "Electrical-Engineer" inside a URL.

import std/[unicode, strutils]

const
  arabicIndicZero = 0x0660   ## ٠
  persianZero = 0x06F0       ## ۰ (some keyboards produce these instead)

proc isInvisible(r: Rune): bool =
  ## Zero-width and direction-control characters. Telegram posts are full of
  ## these (e.g. a U+200F right-to-left mark glued to the end of a URL) and
  ## they silently break substring matching.
  let c = int(r)
  c in 0x200B..0x200F or c in 0x202A..0x202E or c in 0x2066..0x2069 or
    c == 0xFEFF or c == 0x00AD

proc isArabicDiacritic(r: Rune): bool =
  ## Harakat (fatha, damma, shadda, ...) and the tatweel stretch character.
  let c = int(r)
  c in 0x064B..0x065F or c == 0x0670 or c == 0x0640

proc foldArabic(r: Rune): Rune =
  ## Collapse letter variants that people use interchangeably.
  case int(r)
  of 0x0623, 0x0625, 0x0622, 0x0671: Rune(0x0627)  # أ إ آ ٱ -> ا
  of 0x0649: Rune(0x064A)                           # ى -> ي
  of 0x0629: Rune(0x0647)                           # ة -> ه
  of 0x06AF: Rune(0x0643)                           # گ -> ك (Iraqi "gaf")
  of 0x06A4: Rune(0x0641)                           # ڤ -> ف
  of 0x06CC: Rune(0x064A)                           # Persian ی -> ي
  of 0x06A9: Rune(0x0643)                           # Persian ک -> ك
  else: r

proc foldDigit(r: Rune): Rune =
  let c = int(r)
  if c in arabicIndicZero..arabicIndicZero + 9:
    Rune(ord('0') + c - arabicIndicZero)
  elif c in persianZero..persianZero + 9:
    Rune(ord('0') + c - persianZero)
  else: r

proc normalizeText*(s: string): string =
  ## Lower-case, fold Arabic letter variants and digits, drop diacritics and
  ## invisible characters, and squeeze runs of spaces/tabs into one space.
  ## Newlines are kept: the filter works line by line.
  result = newStringOfCap(s.len)
  var lastSpace = false
  for r in s.runes:
    if r.isInvisible or r.isArabicDiacritic: continue
    var x = r.foldDigit.foldArabic.toLower
    if x == Rune('\r'): continue
    if x == Rune('\t') or x == Rune(0x00A0) or x == Rune(0x2009) or x == Rune(0x202F):
      x = Rune(' ')
    if x == Rune(' '):
      if lastSpace: continue
      lastSpace = true
    else:
      lastSpace = x == Rune('\n')
    result.add x

proc flatten*(s: string): string =
  ## A second, looser form used only for keyword search: word separators
  ## that appear inside URLs and hashtags ("-", "_", "/", "#") become spaces.
  ## So "#مهندس_كهرباء" and "jobs/electrical-engineer-34" contain the words.
  result = newStringOfCap(s.len)
  var lastSpace = false
  for ch in s:
    let c = if ch in {'-', '_', '/', '#', '|', '(', ')', ',', ':', '.'}: ' ' else: ch
    if c == ' ':
      if lastSpace: continue
      lastSpace = true
    else:
      lastSpace = false
    result.add c

proc isWordRune*(r: Rune): bool =
  r.isAlpha or (int(r) < 128 and char(int(r)) in Digits)

proc runeSafe*(s: string, a, b: int): string =
  ## s[a ..< b] widened/narrowed so it never cuts a UTF-8 character in half.
  var a = clamp(a, 0, s.len)
  var b = clamp(b, 0, s.len)
  if a >= b: return ""
  while a > 0 and (s[a].uint8 and 0xC0) == 0x80: dec a
  while b < s.len and (s[b].uint8 and 0xC0) == 0x80: inc b
  s[a ..< b]

