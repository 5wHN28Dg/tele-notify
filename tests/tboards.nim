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
      # the board link to the same job is dropped for the API one
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

  test "old-style <company>.workable.com/j/<code> links use the same API":
    let l = findJobLinks("Apply: https://digital-zone.workable.com/j/87323D1E12")
    check l.len == 1 and l[0].kind == bkWorkableApply and
      l[0].url == "https://apply.workable.com/api/v2/accounts/digital-zone/jobs/87323D1E12"
    # the same job by both links is one link
    check findJobLinks("digital-zone.workable.com/j/87323d1e12 apply.workable.com/digital-zone/j/87323D1E12").len == 1
    for host in ["jobs", "apply", "www", "WWW"]:
      check findJobLinks("https://" & host & ".workable.com/j/87323D1E12").len == 0
    check findJobLinks("https://digital-zone.workable.com/j/87323D1E12FF").len == 0

  test "Workable links followed by a query, a bracket or Arabic text; http links":
    let a = findJobLinks("(https://apply.workable.com/digital-zone/j/87323D1E12?utm_source=tg)للتقديم")
    check a.len == 1 and a[0].url == "https://apply.workable.com/api/v2/accounts/digital-zone/jobs/87323D1E12"
    let b = findJobLinks("رابط: https://jobs.workable.com/view/rBwyYa9DZC2rUGyCY5Ldqk/hybrid-x?utm=1)للتقديم")
    check b.len == 1 and b[0].url == "https://jobs.workable.com/view/rBwyYa9DZC2rUGyCY5Ldqk"
    let c = findJobLinks("(digital-zone.workable.com/j/87323D1E12?x=1)قدم الآن")
    check c.len == 1 and c[0].url == "https://apply.workable.com/api/v2/accounts/digital-zone/jobs/87323D1E12"
    let d = findJobLinks("http://apply.workable.com/digital-zone/j/87323D1E12 http://jobs.workable.com/view/rBwyYa9DZC2rUGyCY5Ldqk")
    check d.len == 1 and d[0].kind == bkWorkableApply   # one job: the API link is kept
    let e = findJobLinks("http://jobs.workable.com/view/rBwyYa9DZC2rUGyCY5Ldqk")
    check e.len == 1 and e[0].url == "https://jobs.workable.com/view/rBwyYa9DZC2rUGyCY5Ldqk"

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
    check "Location: Maysan\n" in t   # was "Muḩāfaz̧at Maysān" twice
    check "5–7 years of relevant experience" in t

  test "Workable job board (JSON-LD): hybrid Baghdad job, requirements once":
    let t = extract(bkWorkableBoard, page("workable_job.html"))
    check "Job title: Marketing Lead" in t
    var loc = ""
    for l in t.splitLines:
      if l.startsWith("Location: "): loc = l
    # The page says TELECOMMUTE, but the job is hybrid (the API copy says
    # workplace "hybrid", remote false): no "remote" for the filter.
    check loc == "Location: Baghdad Baghdad Governorate"
    check "remote" notin loc.toLowerAscii
    # The description already holds the requirements: not added twice.
    check t.count("8–12 years in marketing") == 1

  test "the hybrid Baghdad Workable job is rejected":
    let cfg = loadConfig(currentSourcePath.parentDir / "keywords.json")
    check classify(extract(bkWorkableBoard, page("workable_job.html")), cfg).verdict == vReject

  test "other JSON-LD sites still read TELECOMMUTE as remote":
    let ld = """<script type="application/ld+json">{"@type": "JobPosting", "title": "X",
      "jobLocation": {"address": {"addressLocality": "Erbil"}}, "jobLocationType": "TELECOMMUTE"}</script>"""
    check "Location: Erbil, remote\n" in extract(bkJobPosting, ld)
    check "remote" notin extract(bkWorkableBoard, ld)

  test "only requirementsSection is appended to a JSON-LD job, when the description lacks it":
    let ld = """<script type="application/ld+json">{"@type": "JobPosting", "title": "X",
      "description": "<p>About us</p>", "requirementsSection": "<p>3 years</p>",
      "benefitsSection": "<p>Free lunch</p>", "responsibilitiesSection": "<p>Sell</p>"}</script>"""
    let t = extract(bkJobPosting, ld)
    check "About us" in t and t.count("3 years") == 1
    check "Free lunch" notin t and "Sell" notin t
    # Already in the description (other HTML, other spacing): not repeated.
    let dup = """<script type="application/ld+json">{"@type": "JobPosting", "title": "X",
      "description": "<p>About us</p><p><strong>Must</strong></p><p>– 3   years\n of Python</p>",
      "requirementsSection": "<ul><li>Must</li><li>– 3 years of Python</li></ul>"}</script>"""
    check extract(bkWorkableBoard, dup).count("of Python") == 1

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

  test "Workable API: every place in `locations`, without repeats":
    let j = """{"title": "Engineer",
      "location": {"city": "Baghdad", "region": "Baghdad Governorate", "country": "Iraq"},
      "locations": [{"city": "Baghdad", "region": "Baghdad Governorate", "country": "Iraq"},
                    {"city": "Basra", "region": "Basra Governorate", "country": "Iraq"}]}"""
    check extract(bkWorkableApply, j) ==
      "Job title: Engineer\nLocation: Baghdad, Baghdad Governorate, Iraq, Basra, Basra Governorate\n"
    # "locations" alone is enough
    check extract(bkWorkableApply, """{"title": "E", "locations": [{"city": "Basra"}, null, 3]}""") ==
      "Job title: E\nLocation: Basra\n"

  test "Workable API: null location, remote as a string, numeric title":
    check extract(bkWorkableApply, """{"title": 1234, "location": null, "remote": "yes"}""") ==
      "Job title: 1234\nLocation: remote\n"
    check extract(bkWorkableApply, """{"title": "X", "location": null, "remote": "no"}""") == "Job title: X\n"
    check extract(bkWorkableApply, """{"title": "X", "locations": null, "remote": null}""") == "Job title: X\n"

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

suite "review pass 2 (PR #42) findings":
  test "a remote Workable job outside Iraq is not marked remote":
    let us = """{"title": "Engineer", "remote": true, "workplace": "remote",
      "location": {"city": "Austin", "region": "Texas", "country": "United States", "countryCode": "US"}}"""
    check "remote" notin extract(bkWorkableApply, us).toLowerAscii
    let usCode = """{"title": "E", "remote": true, "locations": [{"city": "Austin", "countryCode": "US"}]}"""
    check "remote" notin extract(bkWorkableApply, usCode).toLowerAscii
    let iq = """{"title": "Engineer", "remote": true,
      "location": {"city": "Basra", "country": "Iraq", "countryCode": "IQ"}}"""
    check "Location: Basra, Iraq, remote\n" in extract(bkWorkableApply, iq)
    let iqCode = """{"title": "E", "workplace": "remote", "locations": [{"city": "Austin", "countryCode": "US"}, {"countryCode": "iq"}]}"""
    check extract(bkWorkableApply, iqCode).contains(", remote\n")

  test "a remote US Workable job is not accepted by the filter":
    let cfg = loadConfig(currentSourcePath.parentDir / "keywords.json")
    let us = """{"title": "IT Support Engineer", "remote": true,
      "location": {"city": "Austin", "region": "Texas", "country": "United States", "countryCode": "US"}}"""
    check classify(extract(bkWorkableApply, us), cfg).verdict != vPublish

  test "JSON-LD place names lose their accents and the Muhafazat prefix":
    check stripLatinAccents("Muḩāfaz̧at Maysān Sulaymānīyah Al Başrah Çaḑāţ") ==
      "Muhafazat Maysan Sulaymaniyah Al Basrah Cadat"
    check stripLatinAccents("البصرة Basra") == "البصرة Basra"   # Arabic untouched
    let ld = """<script type="application/ld+json">{"@type": "JobPosting", "title": "X",
      "jobLocation": {"address": {"addressLocality": "Al Başrah", "addressRegion": "Muḩāfaz̧at al Başrah"}}}</script>"""
    check "Location: Al Basrah\n" in extract(bkJobPosting, ld)
    let region = """<script type="application/ld+json">{"@type": "JobPosting", "title": "X",
      "jobLocation": {"address": {"addressRegion": "Al Başrah"}}}</script>"""
    check "Location: Al Basrah\n" in extract(bkJobPosting, region)

  test "requirements behind a non-breaking space are not added twice":
    let ld = """<script type="application/ld+json">{"@type": "JobPosting", "title": "X",
      "description": "<p>About us</p><p>Requirements:&nbsp;3 years of Python</p>",
      "requirementsSection": "<p>Requirements: 3 years of Python</p>"}</script>"""
    check extract(bkJobPosting, ld).count("of Python") == 1
    let u3000 = """<script type="application/ld+json">{"@type": "JobPosting", "title": "X",
      "description": "Requirements:\u20033 years of Python", "requirementsSection": "Requirements: 3 years of Python"}</script>"""
    check extract(bkJobPosting, u3000).count("of Python") == 1

  test "the same job by two links is one link":
    check findJobLinks("mselect.com/job/Drilling-Fluids-Engineer-5748 https://www.mselect.com/job/drilling-fluids-engineer-5748").len == 1
    let l = findJobLinks("https://jobs.workable.com/view/rBwyYa9DZC2rUGyCY5Ldqk/hybrid-marketing-lead " &
      "Apply: https://apply.workable.com/digital-zone/j/87323D1E12/apply")
    check l.len == 1 and l[0].kind == bkWorkableApply and
      l[0].url == "https://apply.workable.com/api/v2/accounts/digital-zone/jobs/87323D1E12"
    # two board links stay two (different jobs)
    check findJobLinks("jobs.workable.com/view/rBwyYa9DZC2rUGyCY5Ldqk jobs.workable.com/view/aBwyYa9DZC2rUGyCY5Ldqk").len == 2

  test "Workable hosts inside another host name or URL are not links":
    check findJobLinks("https://notjobs.workable.com/view/rBwyYa9DZC2rUGyCY5Ldqk").len == 0
    check findJobLinks("https://notapply.workable.com/digital-zone/j/87323D1E12").len == 0
    check findJobLinks("https://evil.com/?x=acme.workable.com/j/0123456789").len == 0
    check findJobLinks("https://evil.com/acme.workable.com/j/0123456789").len == 0
    # still found at the start of the text, after a space, a bracket, "//"
    check findJobLinks("acme.workable.com/j/0123456789").len == 1
    check findJobLinks("(acme.workable.com/j/0123456789)").len == 1
    check findJobLinks("قدم acme.workable.com/j/0123456789").len == 1
    check findJobLinks("jobs.workable.com/view/rBwyYa9DZC2rUGyCY5Ldqk").len == 1
    check findJobLinks("https://evil.com/?r=https://acme.workable.com/j/0123456789").len == 1
