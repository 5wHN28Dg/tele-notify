## Instagram posts linked in job ads, read through a kittygram front end (#114).

import std/[unittest, asyncdispatch, os, strutils, json, sequtils]
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
    let r = waitFor ig("Df665JXhgxw")
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
    let r1 = waitFor ig1("X1")
    check r1.text == "" and r1.notes.anyIt("connection refused" in it)
    let ig2 = kittygramReader("http://127.0.0.1:8430", fakeFrontend(seen, "<html>oops</html>"), fakeFrontend(seen), fakeOcr())
    let r2 = waitFor ig2("X2")
    check r2.text == "" and r2.notes.len > 0

  test "at most two linked posts and five pictures each are read":
    let seen = new seq[string]
    var many = newJArray()
    for i in 1 .. 9: many.add %*{"image_url": "https://cdn.example/p" & $i & ".webp", "alt_text": ""}
    let ig = kittygramReader("http://k", fakeFrontend(seen, $many), fakeFrontend(seen), fakeOcr())
    discard waitFor gather(Post(text: "instagram.com/p/A1/ instagram.com/p/A2/ instagram.com/p/A3/"),
                           fakeFrontend(seen), fakeOcr(), instagram = ig)
    check seen[].countIt("/?json=true" in it) == 2
    check seen[].countIt("cdn.example" in it) == 10

  test "config: instagram_frontend is read, empty by default; empty means no reader":
    var j = parseFile(currentSourcePath.parentDir / "keywords.json")
    check parseConfig(j).instagramFrontend == ""
    j["instagram_frontend"] = %" http://127.0.0.1:8430 "
    check parseConfig(j).instagramFrontend == "http://127.0.0.1:8430"
    check instagramFor("", fakeOcr()) == nil
    check instagramFor("http://127.0.0.1:8430", fakeOcr()) != nil
