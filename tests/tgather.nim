## gather(): OCR + one followed job link, with fake downloads and OCR.

import std/[unittest, asyncdispatch, os, strutils, tables]
import telenotify/[gather, fetch, ocr]

const fx = currentSourcePath.parentDir / "fixtures"

proc fakeFetcher(pages: Table[string, string], calls: ref seq[string]): Fetcher =
  result = proc (url: string): Future[Fetched] {.closure, gcsafe.} =
    {.cast(gcsafe).}:
      calls[].add url
      var f = newFuture[Fetched]("fake")
      if url in pages:
        f.complete Fetched(ok: true, status: 200, finalUrl: url, body: readFile(fx / pages[url]))
      else:
        f.complete Fetched(ok: false, status: 404, finalUrl: url, error: "HTTP 404")
      result = f

proc fakeOcr(text: string): Ocr =
  result = proc (image: string): Future[OcrResult] {.closure, gcsafe.} =
    var f = newFuture[OcrResult]("ocr")
    f.complete OcrResult(ok: true, text: text)
    f

let pages = {"https://prodclient.up.railway.app/api/v1/jobs/9915": "khana_job.json",
             "https://www.linkedin.com/posts/husain-a-jassem-7a0b25100_aepaezaedaerabraehaetaes-share-7466376950174011392-7b04/": "linkedin_post_issue34.html",
             "https://lnkd.in/d_8Yy3WM": "lnkd_interstitial.html"}.toTable

suite "gather":
  test "a single job link is followed and its text appended":
    let calls = new seq[string]
    let g = waitFor gather(Post(text: "وظيفة جديدة\nhttps://khana.jobs/jobs/9915"), fakeFetcher(pages, calls), fakeOcr(""))
    check calls[] == @["https://prodclient.up.railway.app/api/v1/jobs/9915"]
    check "General Ledger Accounting Supervisor" in g.text
    check g.notes.len == 1 and g.notes[0].startsWith("read Khana")

  test "issue #34: a picture does not stop the link from being followed":
    let calls = new seq[string]
    let post = Post(text: "https://www.linkedin.com/posts/husain-a-jassem-7a0b25100_aepaezaedaerabraehaetaes-share-7466376950174011392-7b04/?utm_source=social_share_send",
                    images: @["<image bytes>"])
    let g = waitFor gather(post, fakeFetcher(pages, calls), fakeOcr("فرصة عمل في البصرة وصف طويل من الصورة"))
    check calls[].len == 1
    check "فرصة عمل في البصرة" in g.text           # OCR text kept
    check g.text.len > 200                          # and the post was read

  test "links inside the picture or a button count too":
    let calls = new seq[string]
    discard waitFor gather(Post(text: "التفاصيل في الرابط", buttons: @["https://khana.jobs/jobs/9915"]),
                           fakeFetcher(pages, calls), fakeOcr(""))
    check calls[].len == 1

  test "several job links: none followed":
    let calls = new seq[string]
    let g = waitFor gather(Post(text: "1- https://khana.jobs/jobs/1\n2- https://khana.jobs/jobs/2"),
                           fakeFetcher(pages, calls), fakeOcr(""))
    check calls[].len == 0
    check "2 job links" in g.notes[0]

  test "a failed download is a note, not a crash":
    let calls = new seq[string]
    let g = waitFor gather(Post(text: "https://khana.jobs/jobs/77"), fakeFetcher(pages, calls), fakeOcr(""))
    check "download failed" in g.notes[0]
    check g.text.startsWith("https://khana.jobs/jobs/77")

  test "short link to an unsupported site is reported":
    let calls = new seq[string]
    let g = waitFor gather(Post(text: "https://lnkd.in/d_8Yy3WM"), fakeFetcher(pages, calls), fakeOcr(""))
    check calls[] == @["https://lnkd.in/d_8Yy3WM"]
    check "unsupported site (ap-1.fountain.com)" in g.notes[0]

  test "scraping can be turned off":
    let calls = new seq[string]
    discard waitFor gather(Post(text: "https://khana.jobs/jobs/9915"), fakeFetcher(pages, calls), fakeOcr(""), scrape = false)
    check calls[].len == 0
