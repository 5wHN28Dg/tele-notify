## Loading config.json.
##
## The new version reads the *same* keys as the old Python `main.py`, so your
## existing config works unchanged. A few optional keys are new (all default
## to empty / sensible values):
##
## * `max_experience_years` (default 1): more than this many years required
##   means "not for me".
## * `publish_when_unsure` (default false): what to do with a job that has
##   your role and location but says nothing about level or experience.
##   false = send it to you for review; true = publish it (the old stage-2
##   behaviour, which caused most false positives).
## * `field_keywords_en` / `field_keywords_ar`: role keywords that describe a
##   *degree or field* ("electrical engineering", "علوم حاسبات") rather than a
##   job title. Without them, fields are guessed from the wording (see
##   `isFieldPhrase`).
## * `extra_seniority`, `extra_role_exclusions`, `extra_job_signals`, `extra_not_a_job`,
##   `extra_other_places`, `extra_location_markers`: words added to the
##   built-in lists in defaults.nim.
##
## `responsibility_keywords` is accepted but no longer used: the new
## classifier does not guess level from responsibilities (that guess was the
## biggest source of false positives, see docs/problems-report.md).

import std/[json, strutils, sets]
import keywords, normalize, defaults

type
  Config* = object
    levels*: KeywordList        ## explicit entry-level words ("intern", "حديث التخرج")
    entryRoles*: KeywordList    ## roles that are entry-level by nature ("help desk")
    titles*: KeywordList        ## job titles in your field ("electrical engineer")
    fields*: KeywordList        ## degrees/fields ("electrical engineering")
    locations*: KeywordList     ## places that count as Basra (or remote)
    certifications*: KeywordList
    titleMarkers*, seniority*, mentoring*, jobSignals*, otherPlaces*, roleExclusions*: KeywordList
    streetBefore*, streetAfter*, locationMarkers*: KeywordList
    requiredMarkers*, optionalMarkers*, applyAnyway*, notAJob*: KeywordList
    maxExperienceYears*: int
    publishWhenUnsure*: bool    ## old behaviour: no level evidence either way -> publish
    # Telegram side, used by the listener (not by the filter itself)
    apiId*: int
    apiHash*: string
    chats*: seq[int64]
    targetChat*: int64

  ConfigError* = object of CatchableError

proc strings(j: JsonNode, key: string): seq[string] =
  if j.hasKey(key):
    if j[key].kind != JArray:
      raise newException(ConfigError, "config key '" & key & "' must be a list of strings")
    for x in j[key]:
      if x.kind != JString:
        raise newException(ConfigError, "config key '" & key & "' must only contain strings, found: " & $x)
      result.add x.getStr

proc both(j: JsonNode, key: string): seq[string] =
  j.strings(key & "_en") & j.strings(key & "_ar")

proc isFieldPhrase*(keyword: string): bool =
  ## Guess whether a role keyword names a degree/field instead of a job
  ## title. A field mentioned in a post ("degree in electrical engineering")
  ## is weak evidence: it shows up in the requirements of unrelated jobs.
  let k = keyword.normalizeText.flatten.strip
  for p in ["degree", "bachelor", "bsc", "بكالوريوس", "هندسه ", "علوم ", "تخصص", "التخصصات"]:
    if k.startsWith(p): return true
  for p in ["engineering", "science", "sciences"]:
    if k.endsWith(p): return true
  k.contains("التخصصات")

proc splitRoles(words: seq[string], explicitFields: HashSet[string]): (seq[string], seq[string]) =
  for w in words:
    if w.strip.len == 0: continue
    if w.normalizeText.strip in explicitFields or w.isFieldPhrase: result[1].add w
    else: result[0].add w

proc parseConfig*(j: JsonNode): Config =
  var explicitFields: HashSet[string]
  for f in j.both("field_keywords"): explicitFields.incl f.normalizeText.strip
  let (entryTitles, entryFields) = splitRoles(j.both("entry_level_role"), explicitFields)
  let (midTitles, midFields) = splitRoles(j.both("mid_level_general_role"), explicitFields)

  result.levels = toKeywords(j.both("level_keywords") & @entryPhrases)
  result.entryRoles = toKeywords(entryTitles)
  result.titles = toKeywords(midTitles & @builtinTitles)
  result.fields = toKeywords(entryFields & midFields & j.both("field_keywords"))
  result.locations = toKeywords(j.both("location_keywords"))
  result.certifications = toKeywords(j.strings("certifications"), loose = true)
  result.seniority = toKeywords(@seniorityWords & j.strings("extra_seniority"), strict = true)
  result.mentoring = toKeywords(@mentoringWords)
  result.titleMarkers = toKeywords(titleMarkers)
  result.roleExclusions = toKeywords(@roleExclusions & j.strings("extra_role_exclusions"))
  result.jobSignals = toKeywords(@jobSignals & j.strings("extra_job_signals"))
  result.otherPlaces = toKeywords(@otherPlaces & j.strings("extra_other_places"))
  result.streetBefore = toKeywords(streetBefore)
  result.streetAfter = toKeywords(streetAfter)
  result.locationMarkers = toKeywords(@locationMarkers & j.strings("extra_location_markers"))
  result.requiredMarkers = toKeywords(requiredMarkers)
  result.optionalMarkers = toKeywords(optionalMarkers)
  result.applyAnyway = toKeywords(applyAnyway)
  result.notAJob = toKeywords(@notAJob & j.strings("extra_not_a_job"))
  result.maxExperienceYears = j{"max_experience_years"}.getInt(1)
  result.publishWhenUnsure = j{"publish_when_unsure"}.getBool(false)

  result.apiId = j{"api_id"}.getInt(0)
  result.apiHash = j{"api_hash"}.getStr("")
  result.targetChat = j{"target_chat"}.getBiggestInt(0)
  if j.hasKey("chats") and j["chats"].kind == JArray:
    for c in j["chats"]: result.chats.add c.getBiggestInt

  if result.titles.len + result.entryRoles.len + result.fields.len == 0:
    raise newException(ConfigError, "config has no role keywords (entry_level_role_* / mid_level_general_role_*)")
  if result.locations.len == 0:
    raise newException(ConfigError, "config has no location_keywords_*")

proc loadConfig*(path: string): Config =
  let text = try: readFile(path)
             except IOError as e: raise newException(ConfigError, "cannot read " & path & ": " & e.msg)
  let j = try: parseJson(text)
          except JsonParsingError as e: raise newException(ConfigError, path & " is not valid JSON: " & e.msg)
  parseConfig(j)
