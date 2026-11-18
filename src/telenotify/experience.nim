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
  unit = r"(?:years?|yrs?|سنوات|سنين|سنه|اعوام|عام)(?![\p{L}])"
  numWordsEn = ["zero", "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten"]
  numWordsAr = [("واحده", 1), ("واحد", 1), ("ثلاثه", 3), ("ثلاث", 3),
                ("اربعه", 4), ("اربع", 4), ("خمسه", 5), ("خمس", 5),
                ("سته", 6), ("ست", 6), ("سبعه", 7), ("سبع", 7),
                ("ثمانيه", 8), ("ثمان", 8), ("تسعه", 9), ("تسع", 9),
                ("عشره", 10), ("عشر", 10)]

let
  experienceWord = re2(r"(?i)(?:experience|exp\b|خبر[هت]|خبرات|الخبر[هت]|\b(?:years?|yrs?) (?:in|of|working)\b|سنوات في|سنوات بمجال|ممارسه|لا ?تقل عن)")
  # "3", "3+", "+3", "3-5", "3 to 5", "من 3 الي 5" (normalised form of الى), "3 او 4" followed by a unit
  numericQty = re2(r"(?i)(\+\s*)?\(?(\d{1,2})(?:\.\d)?\)?\s*(?:(?:-|–|~|to|الي|او|or|حتي)\s*(\d{1,2}))?\s*(\+)?\s*(?:\(\s*\d+\s*\)\s*)?" & unit)
  # "Years of Experience: 5+", "عدد سنوات الخبره: 6": the number comes last.
  numberAfter = re2(r"(?i)(?:years of experience|experience years|سنوات الخبره|سنوات خبره)\s*:?\s*\+?\s*(\d{1,2})(?!\d)")
  # "36 months of experience" -> 3 years (rounded up)
  monthsQty = re2(r"(?i)(\d{1,3})\s*(?:months?|اشهر|شهرا|شهر)(?![\p{L}])")
  wordQtyEn = re2(r"(?i)\b(zero|one|two|three|four|five|six|seven|eight|nine|ten)\s*(?:\(\s*\d+\s*\)\s*)?(?:\+\s*)?(?:-\s*\w+\s*)?years?")
  wordQtyAr = re2(r"(واحده|واحد|ثلاثه|ثلاث|اربعه|اربع|خمسه|خمس|سته|ست|سبعه|سبع|ثمانيه|ثمان|تسعه|تسع|عشره|عشر)\s+(?:\(\s*\d+\s*\)\s*)?(?:سنوات|سنين|اعوام)")
  dualYear = re2(r"(?:^|[^\p{L}])(سنتين|سنتان|عامين|عامان)")
  singleYear = re2(r"(?:خبر[هت]|عن|لمده|من|تتجاوز|تزيد|اقل من)\s+(سنه|عام)(?:\s+واحده|\s+واحد)?(?:[^\p{L}]|$)")
  upperBound = re2(r"(?i)(?:less than|up to|maximum|max\.?|not more than|no more than|under|لا تتجاوز|لا تزيد|اقل من|لغايه|حتي)\s*$")
  notExperience = re2(r"(?i)(?:\b(?:old|of age|duration|contract|program|programme|graduated|graduation)\b|العمر|عمر|مده البرنامج|مده العقد|مده التدريب|برنامج تدريبي|البرنامج التدريبي|مواليد|تخرج|التخرج)")
  # "not less than 5 years" is a *minimum*, unlike "less than 2 years".
  negatedBound = re2(r"(?i)(?:not|ليس|لا|غير)\s+(?:less than|اقل من|under)\s*$")
  # "سنه – خمس سنوات", "one to five years", "1 - 5 years": the quantity we
  # found is the *upper* end of a range; this finds the lower end before it.
  listNumber = re2(r"^\s*\d{1,2}\s*(?:[.)]|-\s)")
  rangeLower = re2(r"(?i)(?:(\d{1,2})|(سنه|عام|واحد|one|a year))\s*(?:-|–|~|to|الي|او|or|حتي|\s)\s*$")
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

const maxGap = 160
  ## How far (in bytes) a quantity may be from the experience word: about 25
  ## English words or 12 Arabic ones (Arabic letters are 2 bytes each).

proc nearExperienceWord(spans: seq[(int, int)], a, b: int): bool =
  ## Is the quantity at line[a..<b] close to one of the experience-word
  ## `spans` (found once per line, not once per quantity: that was slow on
  ## huge posts)?
  for (s, e) in spans:
    if s < b and a < e: return true # overlapping: "3-7 years in cybersecurity"
    if (s >= b and s - b <= maxGap) or (a >= e and a - e <= maxGap): return true
  false

proc isUpperBound(line: string, a: int): bool =
  let before = line.runeSafe(a - 30, a)
  before.contains(upperBound) and not before.contains(negatedBound)

proc isOtherDuration(line: string, a, b: int): bool =
  ## "3.5 years program", "aged 25 years old", "عمر 30 سنه": not experience.
  let ctx = line.runeSafe(a - 40, b + 15)
  ctx.contains(notExperience)

const leadingOptional = ["preferably", "يفضل", "ويفضل"]
  ## Optional markers that point *forward*: after the years ("5 years,
  ## preferably in banking", "3 سنوات ويفضل في النفط") they make what follows
  ## optional, not the years.

let
  minimumBefore = re2(r"(?i)(?:(?:^|[^\p{L}])(?:minimum|min\.?|at least|not less than|no less than)|لا ?تقل عن|لا ?يقل عن|علي الاقل|كحد ادني)(?:\s+(?:of|a|an))*\s*:?\s*$")
  minimumAfter = re2(r"(?i)^\s*(?:(?:of\s+)?(?:relevant\s+|work\s+)?experience\s+)?(?:minimum|at least)(?:[^\p{L}]|$)|^\s*(?:(?:من\s+)?(?:ال)?خبره\s+)?(?:علي الاقل|كحد ادني)(?:\s|$)")
  # A marker followed by a preposition points forward: "preferred in oil
  # companies", "مع افضليه لمن عمل", "يفضل في".
  forwardAfter = re2(r"^\s*(?:in|for|with|from|at|لمن|لل|ل|في|من|ممن)(?:\s|$)")
  # A new list item: what it says doesn't qualify the line before.
  bulletStart = re2(r"^\s*(?:[-•*●▪◦–]|\d{1,2}\s*[.)-])")
  wordAfter = re2(r"[\p{L}\p{N}]")
  clauseEnd = {',', ';', ')', '.'}
  window = 200

proc markerOptional(after: string, optional: KeywordList): bool =
  ## Does an optional marker in `after` (normalised text that follows the
  ## years) make the years optional? "is preferred", "a plus" do;
  ## "preferably in banking", "preferred in oil companies", "plus strong SQL"
  ## (a conjunction) point at what follows instead.
  let after = after.flatten
  for h in after.hits(optional):
    let rest = after[h.stop .. ^1]
    if h.keyword == "plus" and not after[0 ..< h.start].strip.endsWith(" a") and
        after[0 ..< h.start].strip != "a":
      continue
    if rest.contains(forwardAfter): continue
    if h.keyword in leadingOptional and rest.contains(wordAfter): continue
    return true
  false

proc optionalFor(line: string, a, b: int, next: string, optional: KeywordList): bool =
  ## Is the quantity at line[a..<b] optional? Yes if an optional marker
  ## comes before it in the same clause ("preferably 3 years", "يفضل خبرة 3
  ## سنوات"), or after it ("3 years is preferred", "... is a plus", or that
  ## on the next line when the sentence wraps), unless an explicit minimum
  ## comes right before or after the years ("Minimum 7 years",
  ## "3 سنوات علي الاقل").
  # Look only near the years (`window` bytes each side): a far-away marker
  # belongs to another sentence, and huge one-line posts stay fast.
  var clause = line.runeSafe(a - window, a)
  var cut = -1
  for i in countdown(clause.high, 0):
    if clause[i] in clauseEnd: cut = i; break
  if cut >= 0: clause = clause[cut + 1 .. ^1]
  if clause.flatten.contains(optional): return true
  let after = line.runeSafe(b, b + window)
  let atEnd = b + window >= line.len
  if line.runeSafe(a - 25, a).contains(minimumBefore) or after.contains(minimumAfter):
    return false
  if after.markerOptional(optional): return true
  atEnd and next.len > 0 and not next.contains(bulletStart) and (after & " " & next).markerOptional(optional)

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
    var spans: seq[(int, int)]
    for m in line.findAll(experienceWord): spans.add (m.boundaries.a, m.boundaries.b + 1)
    var qtys: seq[(int, int, int)] # (years, start, stop)
    for m in line.findAll(numberAfter):
      qtys.add (parseInt(line[m.group(0)]), m.group(0).a, m.group(0).b + 1)
    for m in line.findAll(monthsQty):
      qtys.add ((parseInt(line[m.group(0)]) + 11) div 12, m.boundaries.a, m.boundaries.b + 1)
    for m in line.findAll(numericQty):
      var lo = parseInt(line[m.group(1)])
      # "1- 5+ years" at the start of a line is list item 1, not "1 to 5".
      if m.group(2).a >= 0 and m.boundaries.a <= 8 and line.runeSafe(0, m.boundaries.a).strip.len == 0 and
          line[m.boundaries].contains(listNumber):
        lo = parseInt(line[m.group(2)])
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
    var next = ""
    if li + 1 < lines.len and lines[li + 1].len < 60 and
        not (line.strip.len > 0 and line.strip[^1] in {'.', ':', ';', '!'}):
      next = lines[li + 1]
    let lineRequired = line.flatten.contains(required)
    for (q, a, b) in qtys:
      let isOptional = not lineRequired and line.optionalFor(a, b, next, optional)
      var years = q
      if years < 0 or years > 30: continue
      if not spans.nearExperienceWord(a, b): continue
      if line.isOtherDuration(a, b): continue
      var m: RegexMatch2
      let before = line.runeSafe(a - 20, a)
      let isListNumber = a <= 8 and line.runeSafe(0, a).contains(listNumber)
      var isRange = false
      if not isListNumber and before.find(rangeLower, m):
        years = if m.group(0).a >= 0: parseInt(before[m.group(0)]) else: 1
        isRange = true
      let phrase = line.runeSafe(a - 20, b + 10).strip
      if years == 0 or (not isRange and line.isUpperBound(a)):
        result.entryFriendly = true
        result.evidence.add "upper bound: '" & phrase & "'"
        continue
      if isOptional:
        result.preferredYears = max(result.preferredYears, years)
        result.evidence.add "preferred: '" & phrase & "'"
      else:
        result.requiredYears = max(result.requiredYears, years)
        result.evidence.add "required: '" & phrase & "'"
