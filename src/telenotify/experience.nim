## Reading "how many years of experience does this job want?"
##
## The old code had one giant regex that answered yes/no. This module
## instead *extracts a number*: the minimum years each requirement asks
## for. The classifier then compares that number with a threshold (more than
## 1 year means it's not for a fresh graduate).
##
## How it works, per line of the post:
## 1. The line must mention experience (experience / exp / خبره / خبرات).
##    A line that is just a heading like "Experience:" is joined with the
##    line after it, because posts often write the number on the next line.
## 2. Find every quantity of years on that line: "3 years", "3+ yrs",
##    "3-5 years", "من 5 الى 12 سنه", "ثلاث سنوات", "two years", "سنتين".
## 3. Ignore quantities that are clearly not experience: ages, contract or
##    program durations, and quantities far away from the experience word.
## 4. A quantity after "less than / up to / لا تتجاوز / اقل من" is an
##    upper bound, so it counts as entry-friendly, not as a requirement.
## 5. If the line says the requirement is optional ("preferred", "يفضل"),
##    the quantity is recorded as preferred instead of required.

import std/[strutils, unicode]
import regex
import normalize, defaults, keywords

type
  Experience* = object
    requiredYears*: int    ## highest minimum among *required* quantities; -1 = none found
    preferredYears*: int   ## same for optional quantities; -1 = none
    entryFriendly*: bool   ## "0-1 years", "less than a year", "no experience"
    evidence*: seq[string] ## the phrases that produced the numbers

const
  unit = r"(?:years?|yrs?|سنوات|سنين|سنه|اعوام|عام)"
  numWordsEn = ["zero", "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten"]
  numWordsAr = [("واحده", 1), ("واحد", 1), ("ثلاثه", 3), ("ثلاث", 3),
                ("اربعه", 4), ("اربع", 4), ("خمسه", 5), ("خمس", 5),
                ("سته", 6), ("ست", 6), ("سبعه", 7), ("سبع", 7),
                ("ثمانيه", 8), ("ثمان", 8), ("تسعه", 9), ("تسع", 9),
                ("عشره", 10), ("عشر", 10)]

let
  experienceWord = re2(r"(?i)(?:experience|exp\b|خبر[هت]|خبرات|الخبر[هت])")
  # "3", "3+", "+3", "3-5", "3 to 5", "من 3 الي 5" (normalised form of الى), "3 او 4" followed by a unit
  numericQty = re2(r"(?i)(\+\s*)?(\d{1,2})(?:\.\d)?\s*(?:(?:-|–|~|to|الي|او|or)\s*(\d{1,2}))?\s*(\+)?\s*(?:\(\s*\d+\s*\)\s*)?" & unit)
  wordQtyEn = re2(r"(?i)\b(zero|one|two|three|four|five|six|seven|eight|nine|ten)\s*(?:\(\s*\d+\s*\)\s*)?(?:\+\s*)?(?:-\s*\w+\s*)?years?")
  wordQtyAr = re2(r"(واحده|واحد|ثلاثه|ثلاث|اربعه|اربع|خمسه|خمس|سته|ست|سبعه|سبع|ثمانيه|ثمان|تسعه|تسع|عشره|عشر)\s+(?:سنوات|سنين|اعوام)")
  dualYear = re2(r"(?:^|[^\p{L}])(سنتين|سنتان|عامين|عامان)")
  singleYear = re2(r"(?:خبر[هت]|عن|لمده|من|تتجاوز|تزيد|اقل من)\s+(سنه|عام)(?:\s+واحده|\s+واحد)?(?:[^\p{L}]|$)")
  upperBound = re2(r"(?i)(?:less than|up to|maximum|max\.?|not more than|no more than|under|لا تتجاوز|لا تزيد|اقل من|لغايه|حتي)\s*$")
  notExperience = re2(r"(?i)(?:old|of age|duration|contract|program|programme|العمر|عمر|مده البرنامج|مده العقد|مواليد)")
  # "سنه – خمس سنوات", "one to five years", "1 - 5 years": the quantity we
  # found is the *upper* end of a range; this finds the lower end before it.
  rangeLower = re2(r"(?i)(?:(\d{1,2})|(سنه|عام|واحد|one|a year))\s*(?:-|–|~|to|الي|او|or|\s)\s*$")
  noExperience = re2(r"(?i)(?:no (?:prior |previous )?experience|without experience|0\s*-\s*1 years?|لا يشترط (?:وجود )?(?:ال)?خبره|بدون خبره|من غير خبره|لا تشترط الخبره|بدون خبره سابقه)")

proc wordValue(w: string): int =
  let w = w.toLowerAscii
  for i, x in numWordsEn:
    if x == w: return i
  for (x, v) in numWordsAr:
    if x == w: return v
  -1

proc joinedLines(text: string): seq[string] =
  ## Lines of the post, where a short "Experience:" heading is merged with
  ## the following line so "Experience:\n3+ years" is seen as one line.
  let lines = text.splitLines
  var i = 0
  while i < lines.len:
    var line = lines[i].strip
    if line.len > 0 and line.len < 40 and line.endsWith(":") and
        line.contains(experienceWord) and i + 1 < lines.len:
      line.add " " & lines[i + 1].strip
      inc i
    result.add line
    inc i

proc nearExperienceWord(line: string, a, b: int): bool =
  ## Is the quantity at line[a..<b] close (about 10 words) to an
  ## experience word?
  for m in line.findAll(experienceWord):
    let (s, e) = (m.boundaries.a, m.boundaries.b + 1)
    if (s >= b and s - b <= 90) or (a >= e and a - e <= 90): return true
  false

proc isUpperBound(line: string, a: int): bool =
  let before = line.runeSafe(a - 30, a)
  before.contains(upperBound)

proc isOtherDuration(line: string, a, b: int): bool =
  ## "3.5 years program", "aged 25 years old", "عمر 30 سنه": not experience.
  let ctx = line.runeSafe(a - 25, b + 15)
  ctx.contains(notExperience)

proc parseExperience*(normText: string): Experience =
  ## `normText` must already be normalised (see normalize.nim).
  result = Experience(requiredYears: -1, preferredYears: -1)
  if normText.contains(noExperience):
    result.entryFriendly = true
    result.evidence.add "no experience needed"
  let optional = toKeywords(optionalMarkers)
  let required = toKeywords(requiredMarkers)
  let lines = normText.joinedLines
  for li, line in lines:
    if not line.contains(experienceWord) and not line.contains(dualYear):
      continue
    var qtys: seq[(int, int, int)] # (years, start, stop)
    for m in line.findAll(numericQty):
      var lo = parseInt(line[m.group(1)])
      if line[m.boundaries].contains(re2(r"^\+?\s*\d{1,2}\.[1-9]")):
        inc lo # "1.5 years" is more than 1 year
      qtys.add (lo, m.boundaries.a, m.boundaries.b + 1)
    for m in line.findAll(wordQtyEn):
      qtys.add (wordValue(line[m.group(0)]), m.boundaries.a, m.boundaries.b + 1)
    for m in line.findAll(wordQtyAr):
      qtys.add (wordValue(line[m.group(0)]), m.boundaries.a, m.boundaries.b + 1)
    for m in line.findAll(dualYear):
      qtys.add (2, m.group(0).a, m.group(0).b + 1)
    for m in line.findAll(singleYear):
      qtys.add (1, m.group(0).a, m.group(0).b + 1)
    # "preferably" often lands on the next line when a sentence wraps.
    # "required" on the same line wins over "preferably".
    var isOptional = line.flatten.contains(optional)
    if not isOptional and li + 1 < lines.len and lines[li + 1].len < 60 and
        not (line.strip.len > 0 and line.strip[^1] in {'.', ':', ';', '!'}):
      isOptional = lines[li + 1].flatten.contains(optional)
    if isOptional and line.flatten.contains(required):
      isOptional = false
    for (q, a, b) in qtys:
      var years = q
      if years < 0 or years > 30: continue
      if not line.nearExperienceWord(a, b): continue
      if line.isOtherDuration(a, b): continue
      var m: RegexMatch2
      let before = line.runeSafe(a - 20, a)
      if before.find(rangeLower, m):
        years = if m.group(0).a >= 0: parseInt(before[m.group(0)]) else: 1
      let phrase = line.runeSafe(a - 20, b + 10).strip
      if line.isUpperBound(a) or years == 0:
        result.entryFriendly = true
        result.evidence.add "upper bound: '" & phrase & "'"
        continue
      if isOptional:
        result.preferredYears = max(result.preferredYears, years)
        result.evidence.add "preferred: '" & phrase & "'"
      else:
        result.requiredYears = max(result.requiredYears, years)
        result.evidence.add "required: '" & phrase & "'"
