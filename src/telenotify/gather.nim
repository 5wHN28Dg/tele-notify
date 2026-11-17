## Putting together everything the filter should read about one post.
##
##   post text + button links + text read from images (OCR)
##     + the text of the job page, if the post links to one supported board
##
## The old version only scraped a link when *almost nothing else* was in the
## post (≤ 10 characters left after removing keywords and links). Any OCR
## text from an attached picture made it skip scraping, which is why the
## post in issue #34 was never scraped. Now a single supported link is
## always followed: the job page usually holds the experience requirement
## that the short Telegram post leaves out.
##
## Posts with *several* job links are usually several jobs (each line a job
## and its link), so none of them is followed: their pages would mix
## different jobs together. The post text has to speak for itself.

import std/[asyncdispatch, strutils, uri]
import boards, fetch, ocr

type
  Post* = object
    text*: string
    buttons*: seq[string]  ## URLs of the post's inline buttons
    images*: seq[string]   ## raw image bytes (photos attached to the post)

  Gathered* = object
    text*: string          ## what the filter will read
    notes*: seq[string]    ## what happened, for logs and verdict reasons

  Ocr* = proc (image: string): Future[OcrResult] {.closure, gcsafe.}

proc defaultOcr*(): Ocr =
  result = proc (image: string): Future[OcrResult] {.closure, gcsafe.} = ocrBytes(image)

type Followed = object
  text: string
  notes: seq[string]

proc follow(link: JobLink, fetcher: Fetcher, jobSites: seq[string]): Future[Followed] {.async.} =
  ## Download and read one job link (resolving lnkd.in first).
  var link = link
  var notes: seq[string]
  if link.kind == bkShortLink:
    let page = await fetcher(link.url)
    if not page.ok:
      notes.add "short link " & link.url & " failed: " & page.error
      return Followed(notes: notes)
    let target = shortLinkTarget(page.body, page.finalUrl)
    let found = findJobLinks(target, jobSites)
    if found.len == 0 or found[0].kind == bkShortLink:
      let host = if target == "": "nowhere" else: parseUri(target).hostname
      notes.add "short link leads to an unsupported site (" & host & ")"
      return Followed(notes: notes)
    link = found[0]
    if link.url == page.finalUrl and link.kind != bkKhana and link.kind != bkTaeen:
      # the redirect already landed on the page itself: no second download
      let t = extract(link.kind, page.body)
      if t == "": notes.add $link.kind & " page had no job text"
      else: notes.add "read " & $link.kind & " via short link"
      return Followed(text: t, notes: notes)
  let page = await fetcher(link.url)
  if not page.ok:
    notes.add $link.kind & " download failed: " & page.error
    return Followed(notes: notes)
  let t = extract(link.kind, page.body)
  if t == "": notes.add $link.kind & " page had no job text (expired, login wall or new layout)"
  else: notes.add "read " & $link.kind & " (" & $t.len & " characters)"
  return Followed(text: t, notes: notes)

proc gather*(post: Post, fetcher: Fetcher, ocr: Ocr,
             jobSites: seq[string] = @defaultJobPostingSites,
             scrape = true): Future[Gathered] {.async.} =
  ## Never raises: OCR and download problems end up in `notes`.
  var parts = @[post.text]
  var notes: seq[string]
  for img in post.images:
    let r = await ocr(img)
    if r.ok:
      if r.text.len > 0: parts.add r.text
      notes.add "read " & $r.text.len & " characters from an image"
    else:
      notes.add "OCR failed: " & r.error
  let all = (parts & post.buttons).join("\n")
  let links = findJobLinks(all, jobSites)
  if scrape and links.len == 1:
    let f = await follow(links[0], fetcher, jobSites)
    notes.add f.notes
    if f.text.len > 0: parts.add f.text
  elif scrape and links.len > 1:
    notes.add $links.len & " job links: none followed (probably several jobs)"
  if post.buttons.len > 0: parts.add post.buttons.join("\n")
  result = Gathered(text: parts.join("\n\n"), notes: notes)
