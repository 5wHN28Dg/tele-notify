## "Have I already published this job?"
##
## Two checks, like the old bot (issue #28), but without scikit-learn:
## 1. **Same link.** Both posts contain the same job-specific link (query
##    and trailing slash ignored). Links that many unrelated posts share
##    don't count: a channel's own t.me link, WhatsApp, Instagram,
##    Facebook... Nor do bare domains ("apply at https://company.com"),
##    which many different jobs from one company end with.
## 2. **Same words.** The cosine similarity of the two posts' word counts is
##    0.8 or more (the old threshold, which you found "spot on"). Words are
##    normalised first (normalize.nim), and links are left out.
##
## Compared with the old version: posts are compared against everything
## published in the last 14 days (stored in SQLite), not just the last 10.

import std/[tables, math, strutils, uri, unicode]
import regex
import normalize

const
  similarityThreshold* = 0.8
  sharedHosts = ["t.me", "telegram.me", "wa.me", "whatsapp.com", "chat.whatsapp.com",
                 "instagram.com", "facebook.com", "fb.com", "youtube.com", "youtu.be",
                 "x.com", "twitter.com", "tiktok.com"]

let urlRe = re2(r"(?i)https?://[!#-;=?-Z^-~]+")

proc jobUrls*(text: string): seq[string] =
  ## Normalised links that identify a job.
  for m in text.findAll(urlRe):
    let u = parseUri(text[m.boundaries])
    var host = u.hostname.toLowerAscii
    if host.startsWith("www."): host = host[4 .. ^1]
    var shared = false
    for h in sharedHosts:
      if host == h or host.endsWith("." & h): shared = true
    let path = u.path.strip(leading = false, chars = {'/'})
    # a bare domain (a company's careers page, a job board's home page) is
    # shared by many different jobs, so it says nothing
    if shared or host == "" or path == "": continue
    result.add host & path

proc wordCounts(text: string): CountTable[string] =
  let t = text.replace(urlRe, " ").normalizeText
  var word = ""
  for r in t.runes:
    if r.isWordRune: word.add r
    elif word.len > 0:
      result.inc word
      word = ""
  if word.len > 0: result.inc word

proc similarity*(a, b: string): float =
  ## Cosine similarity of word counts, 0.0 (nothing shared) .. 1.0 (same).
  let (x, y) = (wordCounts(a), wordCounts(b))
  var dot, nx, ny = 0.0
  for w, c in x:
    nx += float(c * c)
    if w in y: dot += float(c * y[w])
  for _, c in y: ny += float(c * c)
  if nx == 0 or ny == 0: return 0.0
  dot / (sqrt(nx) * sqrt(ny))

proc duplicateOf*(text: string, published: seq[string]): string =
  ## Why `text` repeats one of `published`, or "" if it doesn't.
  let mine = jobUrls(text)
  for old in published:
    for u in jobUrls(old):
      if u in mine: return "same link as an earlier post (" & u & ")"
    let s = similarity(text, old)
    if s >= similarityThreshold:
      return "same text as an earlier post (" & $int(round(s * 100)) & "% similar)"
  ""
