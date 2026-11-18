## Job-board links and extractors, tested on real pages saved on
## 2026-11-17 (tests/fixtures/, scripts and styles removed). Workable and
## mselect pages were saved on 2026-11-18 (mselect_job.html keeps only the
## page's title and JSON-LD block: the real page is 1.4 MB).

import std/[unittest, os, strutils]
import telenotify/[boards, config, classify]

const fx = currentSourcePath.parentDir / "fixtures"
proc page(name: string): string = readFile(fx / name)

suite "finding job links":
  test "every supported board, in the forms channels actually post":
    let links = findJobLinks("""
      https://khana.jobs/jobs/9915
      khana.jobs/dashboard/jobs/426
      https://taeen.iq/ar/jobs/6c8a084d-1363-457e-bd73-5f03a2400423?utm_source=telegram
      https://www.linkedin.com/jobs/view/4490426414/
      https://www.linkedin.com/jobs/view/electrical-engineer-at-acme-4490415622?trk=x
      https://www.linkedin.com/posts/husain-a-jassem_share-7466376950174011392-7b04/?utm_source=x
      https://lnkd.in/dGPh986F⁠
      https://yadanoo.com/job/4505e77354fb9161
      https://shift-iraq.com/jobs/251f8967-5969-46b0-8184-ab4e1e0ffa24?utm_source=whatsapp
      https://jobs.workable.com/view/rBwyYa9DZC2rUGyCY5Ldqk/hybrid-marketing-lead-in-baghdad-at-digital-zone
      https://apply.workable.com/digital-zone/j/87323D1E12/
      https://www.mselect.com/job/drilling-fluids-engineer-5748""")
    var urls: seq[string]
    for l in links: urls.add $l.kind & " " & l.url
    check urls == @[
      "Khana https://prodclient.up.railway.app/api/v1/jobs/9915",
      "Khana https://prodclient.up.railway.app/api/v1/jobs/426",
      "Taeen https://api.taeen.iq/jobs/6c8a084d-1363-457e-bd73-5f03a2400423",
      "LinkedIn job https://www.linkedin.com/jobs/view/4490426414/",
      "LinkedIn job https://www.linkedin.com/jobs/view/4490415622/",
      "LinkedIn post https://www.linkedin.com/posts/husain-a-jassem_share-7466376950174011392-7b04/",
      "lnkd.in short link https://lnkd.in/dGPh986F",
      "job page (JSON-LD) https://jobs.workable.com/view/rBwyYa9DZC2rUGyCY5Ldqk",
      "Workable https://apply.workable.com/api/v2/accounts/digital-zone/jobs/87323D1E12",
      "job page (JSON-LD) https://yadanoo.com/job/4505e77354fb9161",
      "job page (JSON-LD) https://shift-iraq.com/jobs/251f8967-5969-46b0-8184-ab4e1e0ffa24",
      "job page (JSON-LD) https://www.mselect.com/job/drilling-fluids-engineer-5748"]

  test "unsupported links are ignored, extra JSON-LD sites can be added":
    check findJobLinks("https://forms.gle/abc https://t.me/x/1 https://iqjscout.com/jobs/x/").len == 0
    check findJobLinks("https://careers.example.iq/job/7", @["example.iq"]).len == 1
    check findJobLinks("taeen.iq/ar/jobs?categoryId=970e5241").len == 0 # a search page, not a job

  test "Workable links: bare apply links and partial IDs are skipped":
    check findJobLinks("https://apply.workable.com/j/87323D1E12").len == 0   # no company: no API
    check findJobLinks("https://apply.workable.com/digital-zone/").len == 0
    check findJobLinks("https://apply.workable.com/digital-zone/j/87323D1E12FF").len == 0
    check findJobLinks("https://jobs.workable.com/view/rBwyYa9DZC").len == 0 # too short
    check findJobLinks("https://jobs.workable.com/search?q=basra").len == 0
    let l = findJobLinks("Apply: apply.workable.com/Digital-Zone/j/87323d1e12")
    check l.len == 1 and l[0].url == "https://apply.workable.com/api/v2/accounts/digital-zone/jobs/87323D1E12"

  test "mselect links keep www. (mselect.com alone has no DNS record)":
    let l = findJobLinks("mselect.com/job/drilling-fluids-engineer-5748")
    check l.len == 1 and l[0].url == "https://www.mselect.com/job/drilling-fluids-engineer-5748"

suite "reading saved pages":
  test "Khana API":
    let t = extract(bkKhana, page("khana_job.json"))
    check "Job title: General Ledger Accounting Supervisor" in t
    check "Location: البصرة - Basra" in t
    check extract(bkKhana, page("khana_missing.json")) == ""

  test "Taeen API":
    let t = extract(bkTaeen, page("taeen_job.json"))
    check "Location: دهوك - Dohuk" in t
    check "Job title:" in t

  test "Yadanoo reads only the JobPosting, never the city picker":
    let t = extract(bkJobPosting, page("yadanoo_job.html"))
    check "Job title: Service Advisor" in t
    check "Duhok" in t
    check "Basrah" notin t   # the page's city dropdown lists Basrah

  test "Shift (JSON-LD)":
    check "Job title:" in extract(bkJobPosting, page("shift_job.html"))

  test "mselect (JSON-LD)":
    let t = extract(bkJobPosting, page("mselect_job.html"))
    check "Job title: Drilling Fluids Engineer" in t
    check "Maysān" in t
    check "5–7 years of relevant experience" in t

  test "Workable job board (JSON-LD) adds the separate requirements section":
    let t = extract(bkJobPosting, page("workable_job.html"))
    check "Job title: Marketing Lead" in t
    check "Location: Baghdad" in t
    check "8–12 years in marketing" in t   # only in requirementsSection

  test "only requirementsSection is appended to a JSON-LD job":
    let ld = """<script type="application/ld+json">{"@type": "JobPosting", "title": "X",
      "description": "<p>About us</p>", "requirementsSection": "<p>3 years</p>",
      "benefitsSection": "<p>Free lunch</p>", "responsibilitiesSection": "<p>Sell</p>"}</script>"""
    let t = extract(bkJobPosting, ld)
    check "About us" in t and "3 years" in t
    check "Free lunch" notin t and "Sell" notin t

  test "Workable company page (API)":
    let t = extract(bkWorkableApply, page("workable_apply.json"))
    check "Job title: Marketing Lead" in t
    check "Location: Baghdad, Baghdad Governorate, Iraq\n" in t   # hybrid, not remote
    check "8–12 years in marketing" in t
    check "compensation packages" in t
    check "<p>" notin t
    check extract(bkWorkableApply, """{"title": "X", "remote": true, "location": {"city": "Basra"}}""").startsWith(
      "Job title: X\nLocation: Basra, remote\n")
    check extract(bkWorkableApply, """{"title": "X", "workplace": "remote"}""") == "Job title: X\nLocation: remote\n"

  test "LinkedIn job: title line with the location, and the description":
    let t = extract(bkLinkedInJob, page("linkedin_job.html"))
    check t.startsWith("Job title: ")
    check t.splitLines.len > 5
    check "Job SummaryA" notin t # paragraphs stay on separate lines

  test "LinkedIn post from issue #34 is read, including the reshared ad":
    let t = extract(bkLinkedInPost, page("linkedin_post_issue34.html"))
    check t.startsWith("#فرصة_عمل")   # the author's own words
    check "محاسبة" in t and "بغداد" in t # the reshared job: an accountant in Baghdad
    check "forms.gle" notin t           # the only comment is not read

  test "issue #34's post is rejected now that it is read (Baghdad accountant)":
    let cfg = loadConfig(currentSourcePath.parentDir / "keywords.json")
    check classify(extract(bkLinkedInPost, page("linkedin_post_issue34.html")), cfg).verdict == vReject

  test "garbage and login walls give empty text, not a crash":
    for k in BoardKind:
      check extract(k, "<html><body>Sign in to continue</body></html>") == ""
      check extract(k, "{not json") == ""

  test "lnkd.in: redirect target or the outside link on LinkedIn's warning page":
    check shortLinkTarget("", "https://www.linkedin.com/jobs/view/1/") == "https://www.linkedin.com/jobs/view/1/"
    check shortLinkTarget(page("lnkd_interstitial.html"), "https://lnkd.in/d_8Yy3WM").startsWith(
      "https://ap-1.fountain.com/al-majal-energy/apply/instrumentation-control-engineer-basra")

suite "scraped text helps the filter":
  test "a '#Basrah + link' post whose job is really in Duhok is rejected":
    let cfg = loadConfig(currentSourcePath.parentDir / "keywords.json")
    let post = "#Basrah\nمطلوب IT support\nhttps://yadanoo.com/job/4505e77354fb9161\n\n" &
               extract(bkJobPosting, page("yadanoo_job.html"))
    check classify(post, cfg).verdict == vReject

suite "review pass 1 (stage 2) findings":
  test "JSON-LD blocks that are not objects don't crash":
    for ld in ["\"hello\"", "42", "[1, \"x\", null]", "null", "{\"@graph\": 5}"]:
      check extract(bkJobPosting, "<script type=\"application/ld+json\">" & ld & "</script>") == ""

  test "absurdly nested HTML is refused instead of overflowing the stack":
    var deep = ""
    for i in 0 ..< 50_000: deep.add "<div>"
    check extract(bkLinkedInPost, deep) == ""
    check extract(bkLinkedInJob, "<title>x</title>" & deep) == ""

  test "uppercase links are found":
    check findJobLinks("https://Yadanoo.com/job/5ab").len == 1
    check findJobLinks("TAEEN.IQ/ar/jobs/6C8A084D-1363-457E-BD73-5F03A2400423").len == 1
    check findJobLinks("https://WWW.LinkedIn.com/jobs/view/4490426414/").len == 1

suite "review pass 2 (stage 2) findings":
  test "a LinkedIn comment can't replace the post's text":
    let orig = page("linkedin_post_issue34.html")
    let post = extract(bkLinkedInPost, orig)
    var fake = ""
    for i in 0 ..< 60: fake.add "Job title: IT Support, fresh graduates, Location: Basra. "
    let hijacked = orig.replace("comment__text", "comment__text\">" & fake & "<span class=\"x")
    check extract(bkLinkedInPost, hijacked) == post
    check "IT Support, fresh graduates" notin post

  test "home pages, list pages and e-mail addresses are not job links":
    check findJobLinks("Apply: https://khana.jobs/jobs/9915 / More jobs: https://yadanoo.com/jobs").len == 1
    check findJobLinks("CV to hr@shift-iraq.com/careers").len == 0
    check findJobLinks("https://yadanoo.com/").len == 0

  test "Arabic punctuation and text end a link":
    let l = findJobLinks("yadanoo.com/job/55ab،للتقديم")
    check l.len == 1 and l[0].url == "https://yadanoo.com/job/55ab"

  test "a trailing slash doesn't make two links":
    check findJobLinks("yadanoo.com/job/123ab and yadanoo.com/job/123ab/").len == 1
