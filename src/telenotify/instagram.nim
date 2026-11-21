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

import std/[asyncdispatch, json, strutils]
import fetch, ocr

const
  maxPosts* = 2      ## linked posts read per Telegram post
  maxPictures* = 5   ## pictures read per linked post

type
  ReadOcr* = proc (image: string): Future[OcrResult] {.closure, gcsafe.}
  InstagramRead* = object
    text*: string
    notes*: seq[string]
  Instagram* = proc (code: string): Future[InstagramRead] {.closure, gcsafe.}
    ## Read one post by its shortcode.

proc isCodeChar(c: char): bool = c in {'A'..'Z', 'a'..'z', '0'..'9', '_', '-'}

proc instagramCodes*(text: string): seq[string] =
  ## Shortcodes of the Instagram posts and reels linked in `text`, in order, once each.
  var i = 0
  let lower = text.toLowerAscii
  while true:
    let at = lower.find("instagram.com/", i)
    if at < 0: break
    var j = at + "instagram.com/".len
    i = j
    # an optional user name before p/ or reel/: instagram.com/<user>/p/<code>
    for kind in ["p/", "reel/", "reels/", "tv/"]:
      var k = j
      if not lower.continuesWith(kind, k):
        # skip one path segment (a user name), then look again
        var s = k
        while s < lower.len and lower[s] notin {'/', ' ', '\n', '?'}: inc s
        if s < lower.len and lower[s] == '/' and lower.continuesWith(kind, s + 1): k = s + 1
        else: continue
      k += kind.len
      var e = k
      while e < text.len and text[e].isCodeChar: inc e
      if e > k:
        let code = text[k ..< e]
        if code notin result: result.add code
      break

proc kittygramReader*(frontend: string, frontendFetcher, imageFetcher: Fetcher, ocr: ReadOcr): Instagram =
  ## `frontendFetcher` may reach a local address (the front end usually runs on
  ## the same machine); `imageFetcher` is the ordinary one for Instagram's pictures.
  let base = frontend.strip(leading = false, chars = {'/'})
  result = proc (code: string): Future[InstagramRead] {.async, closure, gcsafe.} =
    var r: InstagramRead
    let page = await frontendFetcher(base & "/p/" & code & "/?json=true")
    if not page.ok:
      r.notes.add "instagram " & code & ": front end failed: " & page.error
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
      return r
    var parts: seq[string]
    var read = 0
    for item in items:
      if item.kind != JObject: continue
      let alt = item{"alt_text"}.getStr("").strip
      if alt != "" and alt notin parts: parts.add alt
      let caption = item{"caption"}.getStr("").strip
      if caption != "" and caption notin parts: parts.add caption
      if read >= maxPictures: continue
      let url = item{"image_url"}.getStr(item{"thumbnail"}.getStr(""))
      if not url.startsWith("https://"): continue
      inc read
      let img = await imageFetcher(url)
      if not img.ok:
        r.notes.add "instagram " & code & ": picture failed: " & img.error
        continue
      let o = await ocr(img.body)
      if o.ok:
        if o.text.strip != "": parts.add o.text
      else:
        r.notes.add "instagram " & code & ": OCR failed: " & o.error
    r.text = parts.join("\n")
    r.notes.add "read instagram post " & code & " (" & $read & " pictures)"
    return r

proc instagramFor*(frontend: string, ocr: ReadOcr): Instagram =
  ## The reader for config's `instagram_frontend`, or nil when it's empty. The
  ## front end is usually on this machine, so its fetcher may reach a local address.
  if frontend == "": return nil
  let local: Fetcher = proc (url: string): Future[Fetched] {.closure, gcsafe.} =
    fetch(url, allowPrivate = true)
  kittygramReader(frontend, local, defaultFetcher(), ocr)
