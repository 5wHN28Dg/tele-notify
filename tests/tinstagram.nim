## Instagram posts linked in job ads, read through a kittygram front end (#114).

import std/[unittest, asyncdispatch, asynchttpserver, os, strutils, json, sequtils]
import telenotify/[instagram, gather, fetch, ocr, config]

let picture = readFile(currentSourcePath.parentDir / "fixtures" / "ocr_arabic.png")

proc kittygramAnswer(code: string): string =
  # the shape kittygram's /p/<code>/?json=true gives: one entry per picture
  $(%*[
    {"image_url": "https://cdn.example/" & code & "-1.webp", "shortcode": code,
     "alt_text": "Photo by Basra Jobs. May be an image of text that says 'مطلوب مهندس'",
     "user": {"username": "basrajobs"}},
    {"image_url": "https://cdn.example/" & code & "-2.webp", "shortcode": code, "alt_text": ""}])

proc fakeFrontend(seen: ref seq[string], answer = ""): Fetcher =
  result = proc (url: string): Future[Fetched] {.async, closure, gcsafe.} =
    {.cast(gcsafe).}:
      seen[].add url
      if "cdn.example" in url: return Fetched(ok: true, status: 200, body: picture)
      let code = url.split("/p/")[1].split("/")[0]
      return Fetched(ok: true, status: 200, body: (if answer != "": answer else: kittygramAnswer(code)))

proc fakeOcr(): Ocr =
  result = proc (image: string): Future[OcrResult] {.async, closure, gcsafe.} =
    return OcrResult(ok: true, text: "مطلوب مهندس كهرباء حديث التخرج")

suite "instagram links":
  test "post and reel links are found, once each, with their codes":
    check instagramCodes("see https://www.instagram.com/p/Df665JXhgxw/?hvrq=x and instagram.com/reel/AbC_d-1") ==
      @["Df665JXhgxw", "AbC_d-1"]
    check instagramCodes("https://instagram.com/p/X1/ https://www.instagram.com/p/X1/?a=b") == @["X1"]
    check instagramCodes("https://www.instagram.com/basrajobs/") == newSeq[string]()   # a profile, not a post

  test "a linked post's pictures are read and its descriptions kept":
    let seen = new seq[string]
    let ig = kittygramReader("http://127.0.0.1:8430", fakeFrontend(seen), fakeFrontend(seen), fakeOcr())
    let r = waitFor ig.readPost("Df665JXhgxw")
    check seen[][0] == "http://127.0.0.1:8430/p/Df665JXhgxw/?json=true"
    check r.text.count("مطلوب مهندس كهرباء حديث التخرج") == 2           # both pictures
    check "May be an image of text that says" in r.text
    check r.notes.anyIt("instagram" in it.toLowerAscii)

  test "gather reads the linked Instagram post with the Telegram post":
    let seen = new seq[string]
    let ig = kittygramReader("http://127.0.0.1:8430/", fakeFrontend(seen), fakeFrontend(seen), fakeOcr())
    let g = waitFor gather(Post(text: "وظيفة جديدة https://www.instagram.com/p/Df665JXhgxw/"),
                           fakeFrontend(seen), fakeOcr(), instagram = ig)
    check "مطلوب مهندس كهرباء حديث التخرج" in g.text
    check seen[][0] == "http://127.0.0.1:8430/p/Df665JXhgxw/?json=true"   # no double slash

  test "without a front end, Instagram links are left alone":
    let seen = new seq[string]
    let g = waitFor gather(Post(text: "https://www.instagram.com/p/Df665JXhgxw/"), fakeFrontend(seen), fakeOcr())
    check seen[].len == 0 and "instagram" notin g.notes.join(" ").toLowerAscii

  test "a front end that fails, or answers nonsense, is a note, never an error":
    let seen = new seq[string]
    let down: Fetcher = proc (url: string): Future[Fetched] {.async, closure, gcsafe.} =
      return Fetched(ok: false, error: "connection refused")
    let ig1 = kittygramReader("http://127.0.0.1:8430", down, down, fakeOcr())
    let r1 = waitFor ig1.readPost("X1")
    check r1.text == "" and r1.notes.anyIt("connection refused" in it)
    let ig2 = kittygramReader("http://127.0.0.1:8430", fakeFrontend(seen, "<html>oops</html>"), fakeFrontend(seen), fakeOcr())
    let r2 = waitFor ig2.readPost("X2")
    check r2.text == "" and r2.notes.len > 0

  test "at most two linked posts and three pictures each are read":
    let seen = new seq[string]
    var many = newJArray()
    for i in 1 .. 9: many.add %*{"image_url": "https://cdn.example/p" & $i & ".webp", "alt_text": ""}
    let ig = kittygramReader("http://k", fakeFrontend(seen, $many), fakeFrontend(seen), fakeOcr())
    discard waitFor gather(Post(text: "instagram.com/p/A1/ instagram.com/p/A2/ instagram.com/p/A3/"),
                           fakeFrontend(seen), fakeOcr(), instagram = ig)
    check seen[].countIt("/?json=true" in it) == 2
    check seen[].countIt("cdn.example" in it) == 6

  test "config: instagram_frontend is read, empty by default; empty means no reader":
    var j = parseFile(currentSourcePath.parentDir / "keywords.json")
    check parseConfig(j).instagramFrontend == ""
    j["instagram_frontend"] = %" http://127.0.0.1:8430 "
    check parseConfig(j).instagramFrontend == "http://127.0.0.1:8430"
    check instagramFor("", fakeOcr()) == nil
    check instagramFor("http://127.0.0.1:8430", fakeOcr()) != nil

proc answerFetcher(seen: ref seq[string], answer: string, badPictures: seq[string] = @[]): Fetcher =
  ## A front end giving `answer` for every post; pictures in `badPictures` fail.
  result = proc (url: string): Future[Fetched] {.async, closure, gcsafe.} =
    {.cast(gcsafe).}:
      seen[].add url
      if url in badPictures: return Fetched(ok: false, error: "HTTP 404")
      if "cdn.example" in url: return Fetched(ok: true, status: 200, body: picture)
      return Fetched(ok: true, status: 200, body: answer)

suite "instagram review fixes (PR #126)":
  test "share links aren't read: a note, and 'share' is never a user name":
    check instagramCodes("https://www.instagram.com/share/p/BAm9xY2kz/") == newSeq[string]()
    check instagramCodes("https://www.instagram.com/share/reel/BAm9xY2kz/") == newSeq[string]()
    let seen = new seq[string]
    let g = waitFor gather(Post(text: "https://www.instagram.com/share/p/BAm9xY2kz/"), fakeFrontend(seen), fakeOcr(),
                           instagram = kittygramReader("http://k", fakeFrontend(seen), fakeFrontend(seen), fakeOcr()))
    check seen[].len == 0
    check g.notes.anyIt("share links aren't read" in it)

  test "path words after reels aren't post codes; the host match is anchored":
    check instagramCodes("https://www.instagram.com/reels/audio/1234567890/") == newSeq[string]()
    check instagramCodes("https://notinstagram.com/p/Abc123/") == newSeq[string]()
    check instagramCodes("xinstagram.com/p/Abc123/") == newSeq[string]()
    check instagramCodes("instagram.com/p/Abc123/") == @["Abc123"]               # at the start
    check instagramCodes("see\ninstagram.com/p/Abc124/") == @["Abc124"]         # after whitespace
    check instagramCodes("https://m.instagram.com/p/Abc125/") == @["Abc125"]     # after a dot
    check instagramCodes("https://instagram.com/reels/Abc126/") == @["Abc126"]   # a reel code still counts

  test "the user-name link form instagram.com/<user>/reel/<code>":
    check instagramCodes("https://www.instagram.com/basra.jobs_1/reel/DAbc-12_x/?igsh=x") == @["DAbc-12_x"]
    check instagramCodes("https://www.instagram.com/basrajobs/p/DAbc-13/") == @["DAbc-13"]

  test "an empty image_url falls back to thumbnail; a missing one too":
    let seen = new seq[string]
    let answer = $(%*[{"image_url": "", "thumbnail": "https://cdn.example/t1.webp"},
                      {"thumbnail": "https://cdn.example/t2.webp"}])
    let ig = kittygramReader("http://k", answerFetcher(seen, answer), answerFetcher(seen, answer), fakeOcr())
    discard waitFor ig.readPost("T1")
    check "https://cdn.example/t1.webp" in seen[]
    check "https://cdn.example/t2.webp" in seen[]

  test "the {json: {post: {media: [...]}}} shape and a caption field":
    let seen = new seq[string]
    let answer = $(%*{"json": {"post": {"media": [
      {"image_url": "https://cdn.example/m1.webp", "caption": "Hiring in Basra: junior electrical engineer"}]}}})
    let ig = kittygramReader("http://k", answerFetcher(seen, answer), answerFetcher(seen, answer), fakeOcr())
    let r = waitFor ig.readPost("M1")
    check "Hiring in Basra: junior electrical engineer" in r.text
    check "مطلوب مهندس كهرباء حديث التخرج" in r.text
    check "https://cdn.example/m1.webp" in seen[]

  test "one picture failing and the next succeeding: both handled":
    let seen = new seq[string]
    let answer = $(%*[{"image_url": "https://cdn.example/bad.webp"}, {"image_url": "https://cdn.example/good.webp"}])
    let ig = kittygramReader("http://k", answerFetcher(seen, answer),
                             answerFetcher(seen, answer, @["https://cdn.example/bad.webp"]), fakeOcr())
    let r = waitFor ig.readPost("F1")
    check r.notes.anyIt("picture failed: HTTP 404" in it)
    check r.text.count("مطلوب مهندس كهرباء حديث التخرج") == 1
    check r.notes.anyIt("read instagram post F1" in it)

  test "an OCR failure is a note":
    let seen = new seq[string]
    let answer = $(%*[{"image_url": "https://cdn.example/o1.webp"}])
    let broken: Ocr = proc (image: string): Future[OcrResult] {.async, closure, gcsafe.} =
      return OcrResult(ok: false, error: "tesseract crashed")
    let ig = kittygramReader("http://k", answerFetcher(seen, answer), answerFetcher(seen, answer), broken)
    let r = waitFor ig.readPost("O1")
    check r.notes.anyIt(it == "instagram O1: OCR failed: tesseract crashed")

  test "no pictures read says so, not 'read ... (0 pictures)'":
    let seen = new seq[string]
    let answer = $(%*[{"image_url": "https://cdn.example/bad.webp"}])
    let ig = kittygramReader("http://k", answerFetcher(seen, answer),
                             answerFetcher(seen, answer, @["https://cdn.example/bad.webp"]), fakeOcr())
    let r = waitFor ig.readPost("Z1")
    check "instagram Z1: no pictures read" in r.notes
    check not r.notes.anyIt("read instagram post" in it)

  test "gather with scrape=false calls nothing, Instagram included":
    let seen = new seq[string]
    let ig = kittygramReader("http://k", fakeFrontend(seen), fakeFrontend(seen), fakeOcr())
    let g = waitFor gather(Post(text: "https://www.instagram.com/p/Df665JXhgxw/"), fakeFrontend(seen), fakeOcr(),
                           scrape = false, instagram = ig)
    check seen[].len == 0 and "instagram" notin g.notes.join(" ").toLowerAscii

  test "a job URL only in Instagram's text doesn't change which job link is followed":
    let seen = new seq[string]
    let answer = $(%*[{"image_url": "", "alt_text": "apply at https://khana.jobs/jobs/1234"}])
    let ig = kittygramReader("http://k", answerFetcher(seen, answer), answerFetcher(seen, answer), fakeOcr())
    let calls = new seq[string]
    let jobFetcher: Fetcher = proc (url: string): Future[Fetched] {.async, closure, gcsafe.} =
      {.cast(gcsafe).}: calls[].add url
      return Fetched(ok: false, error: "offline test")
    let g = waitFor gather(Post(text: "وظيفة https://khana.jobs/jobs/9915 https://www.instagram.com/p/J1/"),
                           jobFetcher, fakeOcr(), instagram = ig)
    check calls[] == @["https://prodclient.up.railway.app/api/v1/jobs/9915"]   # the post's own link, followed
    check "apply at https://khana.jobs/jobs/1234" in g.text                    # still read by the filter
    check not g.notes.anyIt("job links: none followed" in it)

  test "config: instagram_frontend must be an http(s) address":
    var j = parseFile(currentSourcePath.parentDir / "keywords.json")
    for bad in ["127.0.0.1:8430", "ftp://k", "kittygram"]:
      j["instagram_frontend"] = %bad
      expect ConfigError: discard parseConfig(j)
      try: discard parseConfig(j)
      except ConfigError as e: check "instagram_frontend" in e.msg and "http://" in e.msg
    j["instagram_frontend"] = %"HTTPS://kitty.example"
    check parseConfig(j).instagramFrontend == "HTTPS://kitty.example"

# A local "kittygram" for instagramFor's real fetchers: its pictures point at
# this machine too, which the ordinary fetcher must refuse (before connecting).
let server = newAsyncHttpServer()
server.listen(Port(0))
let port = server.getPort.uint16
let local = "http://127.0.0.1:" & $port
proc handler(req: Request) {.async, gcsafe.} =
  if req.url.path.startsWith("/p/"):
    await req.respond(Http200, $(%*[{"image_url": "https://127.0.0.1:" & $port & "/pic.webp", "alt_text": ""}]))
  else: await req.respond(Http404, "")
asyncCheck (proc () {.async.} =
  while true:
    if server.shouldAcceptRequest: await server.acceptRequest(handler)
    else: await sleepAsync(10))()

proc fakeClock(now: ref float): Clock =
  result = proc (): float {.closure, gcsafe.} =
    {.cast(gcsafe).}: now[]

proc slowOcr(now: ref float, seconds: float, calls: ref int): Ocr =
  ## OCR that takes `seconds` of the fake clock.
  result = proc (image: string): Future[OcrResult] {.async, closure, gcsafe.} =
    {.cast(gcsafe).}:
      now[] += seconds
      inc calls[]
    return OcrResult(ok: true, text: "مطلوب مهندس كهرباء حديث التخرج")

suite "instagram load and wiring (PR #126)":
  test "instagramFor: the front end may be local, Instagram's pictures may not":
    let ig = instagramFor(local, fakeOcr())
    let r = waitFor ig.readPost("L1")
    check not r.notes.anyIt("front end failed" in it)                     # reached 127.0.0.1
    check r.notes.anyIt("picture failed: refused a private/local address" in it)
    check "instagram L1: no pictures read" in r.notes

  test "the time budget: no picture is started once it's spent, with a note":
    let now = new float
    let calls = new int
    var many = newJArray()
    for i in 1 .. 3: many.add %*{"image_url": "https://cdn.example/b" & $i & ".webp"}
    let seen = new seq[string]
    let ig = kittygramReader("http://k", answerFetcher(seen, $many), answerFetcher(seen, $many),
                             slowOcr(now, 30, calls), fakeClock(now))
    let r = waitFor ig.readLinked("instagram.com/p/B1/ instagram.com/p/B2/")
    check calls[] == 1                                  # at 30 s only 15 s are left: no second picture
    check seen[].countIt("/?json=true" in it) == 1      # and B2 isn't asked for
    check r.notes.anyIt("instagram B1: time for Instagram spent" in it)
    check r.notes.anyIt("instagram B2: time for Instagram spent, not read" in it)
    check not r.unread                                  # B1 was read

  test "the time budget is per Telegram post, across its linked posts":
    let now = new float
    let calls = new int
    let seen = new seq[string]
    let one = $(%*[{"image_url": "https://cdn.example/c.webp"}])
    let ig = kittygramReader("http://k", answerFetcher(seen, one), answerFetcher(seen, one),
                             slowOcr(now, 50, calls), fakeClock(now))
    discard waitFor ig.readLinked("instagram.com/p/C1/ instagram.com/p/C2/")
    check calls[] == 1
    discard waitFor ig.readLinked("instagram.com/p/C3/")   # the next Telegram post has its own 45 s
    check calls[] == 2

  test "a post read in full is remembered by its code for 6 h":
    let now = new float
    let calls = new int
    let seen = new seq[string]
    let one = $(%*[{"image_url": "https://cdn.example/d.webp"}])
    let ig = kittygramReader("http://k", answerFetcher(seen, one), answerFetcher(seen, one),
                             slowOcr(now, 1, calls), fakeClock(now))
    let r1 = waitFor ig.readLinked("instagram.com/p/D1/")
    let r2 = waitFor ig.readLinked("cross-posted: https://www.instagram.com/p/D1/")
    check calls[] == 1 and seen[].countIt("/?json=true" in it) == 1
    check r2.text == r1.text
    check r2.notes.anyIt("instagram D1: read earlier" in it)
    now[] += 6 * 3600.0 + 1
    discard waitFor ig.readLinked("instagram.com/p/D1/")
    check calls[] == 2                                  # expired: read again

  test "a post that couldn't be read isn't remembered":
    let calls = new int
    let now = new float
    let seen = new seq[string]
    var up = false
    let flaky: Fetcher = proc (url: string): Future[Fetched] {.async, closure, gcsafe.} =
      {.cast(gcsafe).}:
        seen[].add url
        if not up: return Fetched(ok: false, error: "connection refused")
        return Fetched(ok: true, status: 200, body: $(%*[{"image_url": "https://cdn.example/e.webp"}]))
    let ig = kittygramReader("http://k", flaky, fakeFrontend(seen), slowOcr(now, 1, calls), fakeClock(now))
    check (waitFor ig.readLinked("instagram.com/p/E1/")).unread
    up = true
    check not (waitFor ig.readLinked("instagram.com/p/E1/")).unread
    check calls[] == 1
    # nor one whose pictures all failed
    var picturesUp = false
    let pictures: Fetcher = proc (url: string): Future[Fetched] {.async, closure, gcsafe.} =
      {.cast(gcsafe).}:
        if not picturesUp: return Fetched(ok: false, error: "HTTP 503")
        return Fetched(ok: true, status: 200, body: picture)
    let ig2 = kittygramReader("http://k", flaky, pictures, slowOcr(now, 1, calls), fakeClock(now))
    check (waitFor ig2.readLinked("instagram.com/p/E2/")).unread
    picturesUp = true
    check not (waitFor ig2.readLinked("instagram.com/p/E2/")).unread
    check calls[] == 2

  test "the memory holds 200 posts: the oldest is forgotten first":
    let now = new float
    let calls = new int
    let seen = new seq[string]
    let one = $(%*[{"image_url": "https://cdn.example/f.webp"}])
    let ig = kittygramReader("http://k", answerFetcher(seen, one), answerFetcher(seen, one),
                             slowOcr(now, 1, calls), fakeClock(now))
    for i in 0 .. cacheSize: discard waitFor ig.readPost("G" & $i)   # 201 posts
    check calls[] == cacheSize + 1
    discard waitFor ig.readPost("G" & $cacheSize)    # the newest: remembered
    check calls[] == cacheSize + 1
    discard waitFor ig.readPost("G0")                # the oldest: forgotten
    check calls[] == cacheSize + 2

  test "a picture Instagram already read (alt text 'text that says') is still OCR'd; its alt text is kept":
    let now = new float
    let calls = new int
    let seen = new seq[string]
    let answer = $(%*[{"image_url": "https://cdn.example/h1.webp",
                       "alt_text": "May be an image of text that says 'Hiring: junior engineer, Basra'"}])
    let ig = kittygramReader("http://k", answerFetcher(seen, answer), answerFetcher(seen, answer),
                             slowOcr(now, 1, calls), fakeClock(now))
    let r = waitFor ig.readPost("H1")
    check calls[] == 1 and "https://cdn.example/h1.webp" in seen[]
    check "Hiring: junior engineer, Basra" in r.text
    check "مطلوب مهندس كهرباء حديث التخرج" in r.text
    check not r.unread
    check "read instagram post H1 (1 pictures)" in r.notes

  test "a short post whose Instagram link couldn't be read is marked unreadable":
    let down: Fetcher = proc (url: string): Future[Fetched] {.async, closure, gcsafe.} =
      return Fetched(ok: false, error: "connection refused")
    let ig = kittygramReader("http://k", down, down, fakeOcr())
    let g = waitFor gather(Post(text: "وظيفة جديدة في البصرة https://www.instagram.com/p/U1/"), down, fakeOcr(),
                           instagram = ig)
    check g.unreadable != ""
    # a share link can't be read either
    let g2 = waitFor gather(Post(text: "https://www.instagram.com/share/p/BAm9x/"), down, fakeOcr(), instagram = ig)
    check g2.unreadable != ""
    # a long post speaks for itself
    let long = "مطلوب مهندس كهرباء حديث التخرج للعمل في شركة نفطية في البصرة، دوام كامل، " &
               "رواتب مجزية، ارسال السيرة الذاتية على البريد المذكور في الصورة مع ذكر الاختصاص"
    let g3 = waitFor gather(Post(text: long & " https://www.instagram.com/p/U1/"), down, fakeOcr(), instagram = ig)
    check g3.unreadable == ""
    # and one that was read is fine
    let seen = new seq[string]
    let ok = kittygramReader("http://k", fakeFrontend(seen), fakeFrontend(seen), fakeOcr())
    let g4 = waitFor gather(Post(text: "https://www.instagram.com/p/U2/"), down, fakeOcr(), instagram = ok)
    check g4.unreadable == ""

suite "instagram review fixes, round 2 (PR #126)":
  test "a picture isn't started with less than 25 s of the budget left":
    let now = new float
    let calls = new int
    var many = newJArray()
    for i in 1 .. 3: many.add %*{"image_url": "https://cdn.example/r" & $i & ".webp"}
    let seen = new seq[string]
    let ig = kittygramReader("http://k", answerFetcher(seen, $many), answerFetcher(seen, $many),
                             slowOcr(now, 21, calls), fakeClock(now))
    let r = waitFor ig.readLinked("instagram.com/p/R1/")
    check calls[] == 1                    # at 21 s, 24 s are left: not enough for another
    check seen[].countIt("cdn.example" in it) == 1
    check r.notes.anyIt("instagram R1: time for Instagram spent" in it)
    now[] = 0
    calls[] = 0
    let ig2 = kittygramReader("http://k", answerFetcher(seen, $many), answerFetcher(seen, $many),
                              slowOcr(now, 19, calls), fakeClock(now))
    discard waitFor ig2.readLinked("instagram.com/p/R2/")
    check calls[] == 2                    # at 19 s, 26 s are left: one more; at 38 s, no third

  test "a post cut short by the budget isn't remembered":
    let now = new float
    let calls = new int
    var many = newJArray()
    for i in 1 .. 3: many.add %*{"image_url": "https://cdn.example/s" & $i & ".webp"}
    let seen = new seq[string]
    let ig = kittygramReader("http://k", answerFetcher(seen, $many), answerFetcher(seen, $many),
                             slowOcr(now, 30, calls), fakeClock(now))
    let r1 = waitFor ig.readLinked("instagram.com/p/S1/")
    check not r1.unread and r1.notes.anyIt("time for Instagram spent" in it)
    discard waitFor ig.readLinked("instagram.com/p/S1/")
    check seen[].countIt("/?json=true" in it) == 2      # fetched again

  test "a post with one failed picture among good ones isn't remembered":
    let now = new float
    let calls = new int
    let seen = new seq[string]
    let answer = $(%*[{"image_url": "https://cdn.example/bad.webp"}, {"image_url": "https://cdn.example/ok.webp"}])
    let ig = kittygramReader("http://k", answerFetcher(seen, answer),
                             answerFetcher(seen, answer, @["https://cdn.example/bad.webp"]),
                             slowOcr(now, 1, calls), fakeClock(now))
    let r1 = waitFor ig.readLinked("instagram.com/p/V1/")
    check not r1.unread
    discard waitFor ig.readLinked("instagram.com/p/V1/")
    check seen[].countIt("/?json=true" in it) == 2      # fetched again
    check calls[] == 2

  test "share links without a kind aren't read either, and count as unread":
    let links = instagramLinks("https://www.instagram.com/share/BAm9xY2kz/")
    check links.codes.len == 0 and links.shares == @["share/BAm9xY2kz"]
    let seen = new seq[string]
    let ig = kittygramReader("http://k", fakeFrontend(seen), fakeFrontend(seen), fakeOcr())
    let r = waitFor ig.readLinked("https://www.instagram.com/share/BAm9xY2kz/")
    check seen[].len == 0 and r.unread
    check "instagram share/BAm9xY2kz: share links aren't read" in r.notes

  test "the host may follow punctuation, a non-breaking space or Arabic, never an ASCII letter":
    check instagramCodes("رابط:instagram.com/p/X1/") == @["X1"]
    check instagramCodes("(instagram.com/p/X2)") == @["X2"]
    check instagramCodes("\"instagram.com/p/X3\" 'instagram.com/p/X4'") == @["X3", "X4"]
    check instagramCodes("رابطinstagram.com/p/X5/") == @["X5"]                    # glued to an Arabic letter
    check instagramCodes("see\u00A0instagram.com/p/X6/ \u200Finstagram.com/p/X7/") == @["X6", "X7"]
    check instagramCodes("👉instagram.com/p/X8/") == @["X8"]
    check instagramCodes("notinstagram.com/p/Y1/ 9instagram.com/p/Y2/") == newSeq[string]()

  test "a short post is at most 100 characters of its own, links, hashtags and @mentions left out":
    let down: Fetcher = proc (url: string): Future[Fetched] {.async, closure, gcsafe.} =
      return Fetched(ok: false, error: "connection refused")
    let ig = kittygramReader("http://k", down, down, fakeOcr())
    proc unreadable(text: string): bool =
      (waitFor gather(Post(text: text), down, fakeOcr(), instagram = ig)).unreadable != ""
    check unreadable("ب".repeat(100) & " https://www.instagram.com/p/W1/")
    check not unreadable("ب".repeat(101) & " https://www.instagram.com/p/W1/")
    check unreadable("ب".repeat(100) & " instagram.com/p/W1/")                    # a bare link isn't counted
    check unreadable("ب".repeat(90) & " #وظائف_البصرة #وظائف @basrajobs https://www.instagram.com/p/W1/")
