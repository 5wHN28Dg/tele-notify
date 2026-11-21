## Instagram posts linked in job ads, read through a kittygram front end (#114).
##
## Job ads often say little and link an Instagram post whose pictures hold the
## details. Instagram itself answers automated requests with a login wall, so
## the bot asks a kittygram instance (codeberg.org/irelephant/kittygram, an
## open-source Instagram front end) for the post instead:
##   <instagram_frontend>/p/<code>/?json=true
## gives one entry per picture: its `image_url` and Instagram's own
## description of it (`alt_text`). The pictures are downloaded and read with
## the same OCR as photos in Telegram posts; the descriptions are kept too.
##
## Off unless config.json has `instagram_frontend` (e.g. a kittygram on the
## same server: "http://127.0.0.1:8430"; its `allow_json` must be on).
## Every failure is a note for the verdict's reasons, never an error.
##
## The listener judges one post at a time, so reading is kept short:
## * at most 2 linked posts per Telegram post and 3 pictures per linked post;
## * about 45 s in all per Telegram post: when it's spent, no further
##   picture or post is started (one already started finishes; a download
##   gives up after 20 s), with a note;
## * a picture whose description already holds Instagram's own reading of
##   its text ("... text that says ...") isn't downloaded or OCR'd: the
##   description is kept instead;
## * a post read in full is remembered by its code (200 posts, 6 h), so an
##   ad cross-posted to several channels is read once.
##
## Share links (instagram.com/share/p/<token>/) carry a token, not the
## post's code, and only Instagram can turn one into a post: they're
## skipped, with a note.

import std/[asyncdispatch, json, strutils, tables, monotimes]
import fetch, ocr

const
  maxPosts* = 2          ## linked posts read per Telegram post
  maxPictures* = 3       ## pictures downloaded and OCR'd per linked post
  budgetSeconds* = 45.0  ## time for all the Instagram reading of one Telegram post
  cacheSize* = 200       ## linked posts remembered
  cacheSeconds* = 6 * 3600.0
  postKinds = ["p", "reel", "reels", "tv"]
  notCodes = ["audio", "share", "explore", "tags", "locations", "stories", "accounts",
              "p", "reel", "reels", "tv"]
    ## path words that can follow a post kind but aren't post codes

type
  ReadOcr* = proc (image: string): Future[OcrResult] {.closure, gcsafe.}
  Clock* = proc (): float {.closure, gcsafe.}
    ## Seconds from any fixed start (tests pass a fake one).
  InstagramRead* = object
    text*: string
    notes*: seq[string]
    unread*: bool   ## a linked post gave nothing (front end failed, no pictures
                    ## read, out of time, or a share link)
  InstagramLinks* = object
    codes*: seq[string]    ## post shortcodes, in order, once each
    shares*: seq[string]   ## share links, as "share/<kind>/<token>"
  Cached = object
    at: float
    read: InstagramRead
  Instagram* = ref object
    ## Reads linked Instagram posts through a kittygram front end.
    base: string
    frontendFetcher, imageFetcher: Fetcher
    ocr: ReadOcr
    clock*: Clock
    budget*: float             ## seconds per Telegram post
    cache: Table[string, Cached]

proc monoSeconds*(): float =
  float(getMonoTime().ticks) / 1e9

proc isCodeChar(c: char): bool = c in {'A'..'Z', 'a'..'z', '0'..'9', '_', '-'}

proc instagramLinks*(text: string): InstagramLinks =
  ## The Instagram posts, reels and share links in `text`. A link counts when
  ## `instagram.com` starts the text or follows `/`, `.` or whitespace (so
  ## not `notinstagram.com`), and its path is `<kind>/<code>` or
  ## `<user>/<kind>/<code>` with kind p, reel, reels or tv.
  let lower = text.toLowerAscii
  const host = "instagram.com/"
  var i = 0
  while true:
    let at = lower.find(host, i)
    if at < 0: break
    i = at + host.len
    if at > 0 and lower[at - 1] notin {'/', '.', ' ', '\t', '\n', '\r', '\v', '\f'}: continue
    # up to three path segments; a segment ends at '/', anything else ends the path
    var segs: seq[string]
    var origs: seq[string]
    var k = i
    while segs.len < 3:
      var e = k
      while e < text.len and (text[e].isCodeChar or text[e] == '.'): inc e
      segs.add lower[k ..< e]
      origs.add text[k ..< e]
      if e >= text.len or text[e] != '/': break
      k = e + 1
    if segs.len >= 2 and segs[0] == "share":
      if segs[1] in postKinds and segs.len >= 3 and segs[2] != "":
        let s = "share/" & segs[1] & "/" & origs[2]
        if s notin result.shares: result.shares.add s
      continue
    var c = -1
    if segs.len >= 2 and segs[0] in postKinds: c = 1
    elif segs.len >= 3 and segs[1] in postKinds: c = 2
    if c < 0: continue
    let code = origs[c]
    var plain = code != "" and segs[c] notin notCodes
    for ch in code:
      if not ch.isCodeChar: plain = false
    if plain and code notin result.codes: result.codes.add code

proc instagramCodes*(text: string): seq[string] =
  ## Shortcodes of the Instagram posts and reels linked in `text`, in order, once each.
  instagramLinks(text).codes

proc kittygramReader*(frontend: string, frontendFetcher, imageFetcher: Fetcher, ocr: ReadOcr,
                      clock: Clock = nil): Instagram =
  ## `frontendFetcher` may reach a local address (the front end usually runs on
  ## the same machine); `imageFetcher` is the ordinary one for Instagram's pictures.
  Instagram(base: frontend.strip(leading = false, chars = {'/'}),
            frontendFetcher: frontendFetcher, imageFetcher: imageFetcher, ocr: ocr,
            clock: (if clock == nil: Clock(monoSeconds) else: clock),
            budget: budgetSeconds)

proc remember(ig: Instagram, code: string, r: InstagramRead) =
  if ig.cache.len >= cacheSize and code notin ig.cache:
    var oldest = ""
    var oldestAt = Inf
    for k, v in ig.cache:
      if v.at < oldestAt: (oldest, oldestAt) = (k, v.at)
    ig.cache.del oldest
  ig.cache[code] = Cached(at: ig.clock(), read: r)

proc readPost*(ig: Instagram, code: string, deadline = Inf): Future[InstagramRead] {.async.} =
  ## Read one post by its shortcode; stop starting pictures at `deadline`
  ## (a time of `ig.clock`).
  var r: InstagramRead
  if code in ig.cache:
    let c = ig.cache[code]
    if ig.clock() - c.at < cacheSeconds:
      r = c.read
      r.notes = @["instagram " & code & ": read earlier, reused"] & r.notes
      return r
    ig.cache.del code
  let page = await ig.frontendFetcher(ig.base & "/p/" & code & "/?json=true")
  if not page.ok:
    r.notes.add "instagram " & code & ": front end failed: " & page.error
    r.unread = true
    return r
  var items: JsonNode
  try:
    let j = parseJson(page.body)
    items = if j.kind == JArray: j
            elif j.kind == JObject and j{"json", "post", "media"} != nil: j{"json", "post", "media"}
            else: nil
  except CatchableError:
    items = nil
  if items == nil or items.kind != JArray:
    r.notes.add "instagram " & code & ": the front end's answer isn't a post (is allow_json on?)"
    r.unread = true
    return r
  var parts: seq[string]
  var tried, read, fromText, failed = 0
  var outOfTime = false
  for item in items:
    if item.kind != JObject: continue
    let alt = item{"alt_text"}.getStr("").strip
    if alt != "" and alt notin parts: parts.add alt
    let caption = item{"caption"}.getStr("").strip
    if caption != "" and caption notin parts: parts.add caption
    if "text that says" in alt.toLowerAscii:
      inc fromText      # Instagram already read this picture's text: kept above
      continue
    var url = item{"image_url"}.getStr("").strip
    if url == "": url = item{"thumbnail"}.getStr("").strip
    if not url.startsWith("https://"): continue
    if tried >= maxPictures: continue
    if ig.clock() >= deadline:
      outOfTime = true
      continue
    inc tried
    let img = await ig.imageFetcher(url)
    if not img.ok:
      inc failed
      r.notes.add "instagram " & code & ": picture failed: " & img.error
      continue
    let o = await ig.ocr(img.body)
    if o.ok:
      inc read
      if o.text.strip != "": parts.add o.text
    else:
      inc failed
      r.notes.add "instagram " & code & ": OCR failed: " & o.error
  r.text = parts.join("\n")
  if outOfTime:
    r.notes.add "instagram " & code & ": time for Instagram spent, pictures left unread"
  if read + fromText == 0:
    r.notes.add "instagram " & code & ": no pictures read"
    r.unread = true
  else:
    var n = "read instagram post " & code & " (" & $(read + fromText) & " pictures"
    if fromText > 0: n.add ", " & $fromText & " by Instagram's own text reading"
    r.notes.add n & ")"
  if not r.unread and not outOfTime and failed == 0: ig.remember(code, r)
  return r

proc readLinked*(ig: Instagram, text: string): Future[InstagramRead] {.async.} =
  ## Read the Instagram posts linked in `text` (up to `maxPosts`, within
  ## `ig.budget` seconds). `unread` is set when there were links and none
  ## gave anything.
  var r: InstagramRead
  let links = instagramLinks(text)
  for s in links.shares:
    r.notes.add "instagram " & s & ": share links aren't read"
  let deadline = ig.clock() + ig.budget
  var gotSome = false
  var n = 0
  for code in links.codes:
    if n >= maxPosts: break
    inc n
    if ig.clock() >= deadline:
      r.notes.add "instagram " & code & ": time for Instagram spent, not read"
      continue
    let p = await ig.readPost(code, deadline)
    r.notes.add p.notes
    if p.text.len > 0:
      r.text.add (if r.text.len > 0: "\n" else: "") & p.text
    if not p.unread: gotSome = true
  r.unread = (links.codes.len + links.shares.len > 0) and not gotSome
  return r

proc localFetcher*(): Fetcher =
  ## The fetcher for the front end: it may reach a local address.
  result = proc (url: string): Future[Fetched] {.closure, gcsafe.} =
    fetch(url, allowPrivate = true)

proc instagramFor*(frontend: string, ocr: ReadOcr): Instagram =
  ## The reader for config's `instagram_frontend`, or nil when it's empty. The
  ## front end is usually on this machine, so its fetcher may reach a local
  ## address; Instagram's pictures go through the ordinary fetcher, which doesn't.
  if frontend == "": return nil
  kittygramReader(frontend, localFetcher(), defaultFetcher(), ocr)
