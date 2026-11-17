## One test per problem from docs/problems-report.md, using invented posts
## (your real Saved Messages stay private and are used by `telenotify eval`).

import std/[unittest, os, json]
import telenotify/[config, classify]

let cfg = loadConfig(currentSourcePath.parentDir / "keywords.json")

proc verdict(post: string): Verdict = classify(post, cfg).verdict

suite "positive evidence publishes":
  test "fresh graduate electrical engineer in Basra":
    check verdict("""مطلوب مهندس كهرباء حديث التخرج
للعمل في شركة نفطية في البصرة
ارسال السيرة الذاتية على الواتساب""") == vPublish

  test "graduate program in Basra with a degree requirement only":
    check verdict("""Graduate Development Program
📍 Location: Basra, Iraq
- Bachelor's degree in Engineering in any discipline.
- Graduated within the last 12 months.
Apply now""") == vPublish

  test "help desk is entry level by nature":
    check verdict("""We are hiring: Help Desk Technician
Location: Basra
Send your CV""") == vPublish

suite "absence of evidence goes to review, not publish (3.1)":
  test "title + location but nothing about level":
    check verdict("""شركة في البصرة تعلن عن حاجتها الى مهندس كهرباء
ارسال السيرة الذاتية""") == vReview

  test "publish_when_unsure restores the old behaviour":
    var j = parseFile(currentSourcePath.parentDir / "keywords.json")
    j["publish_when_unsure"] = %true
    let c2 = parseConfig(j)
    check classify("""شركة في البصرة تعلن عن حاجتها الى مهندس كهرباء
ارسال السيرة الذاتية""", c2).verdict == vPublish

suite "negative evidence rejects":
  test "a level word no longer bypasses years of experience (3.2)":
    check verdict("""Hiring: Electrical engineering graduate
Location: Basra
Requirements: at least 5 years of experience""") == vReject

  test "Arabic senior title is caught (3.3)":
    check verdict("""مطلوب مهندس كهرباء اول للعمل في البصرة
حديث التخرج""") == vReject

  test "senior word in the title line (3.3)":
    check verdict("""KBR is Hiring an IT Support Lead in Basrah
Responsibilities: coordinate IT support activities
Junior staff welcome""") != vPublish

  test "mandatory certification rejects (3.4)":
    check verdict("""Junior Network Technician wanted in Basra
CCNA certification is required""") == vReject

  test "certification that is a plus does not reject (3.4)":
    check verdict("""Junior Network Technician wanted in Basra
Relevant certifications such as CCNA are a plus""") == vPublish

  test "apply-anyway only excuses a certification (#25)":
    check verdict("""Junior Network Technician wanted in Basra
CCNA is required.
Even if you don't meet every requirement, we encourage you to apply""") == vReview
    check verdict("""Junior Network Technician wanted in Basra
5 years of experience required.
Even if you don't meet every requirement, we encourage you to apply""") == vReject

suite "location (3.6)":
  test "Basra Street in Baghdad is not Basra":
    check verdict("""مطلوب مهندس كهرباء حديث التخرج
موقع الشركة بغداد - شارع البصرة""") == vReject

  test "a Basra company hiring for another governorate":
    check verdict("""تعلن شركة البصرة للمقاولات عن حاجتها الى مهندس كهرباء حديث التخرج
موقع العمل: محافظة ميسان""") == vReject

  test "Basra and Baghdad with no location line is ambiguous":
    check verdict("""مطلوب مهندس كهرباء حديث التخرج لفروع البصرة وبغداد""") == vReview

suite "roles (3.7)":
  test "cost control engineer is not a control engineer":
    check verdict("""Hiring: Cost Control Engineer, fresh graduate
Location: Basra""") == vReject

  test "no role at all":
    check verdict("""مطلوب محاسب حديث التخرج للعمل في البصرة""") == vReject

suite "multi-job posts (3.8, #29)":
  test "experience of another job does not leak into ours":
    check verdict("""شركة برمجية تعلن عن توفر فرص عمل
1- مصور
2- إعلامي ذو خبرة لا تقل عن 3 سنين
3- مبرمج تطبيقات حديث التخرج
عنوان الشركة البصرة""") == vPublish

  test "our job's own requirement still counts":
    check verdict("""شركة تعلن عن توفر فرص عمل في البصرة
1- مصور حديث التخرج
2- مبرمج تطبيقات خبرة لا تقل عن 5 سنوات""") == vReject

  test "a requirements list stays with its single job":
    check verdict("""مطلوب مهندس كهرباء للعمل في البصرة
المتطلبات:
1- بكالوريوس هندسة كهرباء
2- خبرة لا تقل عن 5 سنوات""") == vReject

suite "not a job (3.9)":
  test "job seeker":
    check verdict("""أبحث حاليًا عن فرصة عمل في البصرة، خريج هندسة كهرباء حديث التخرج""") == vReject

  test "no job-advert wording at all":
    check verdict("""محتاجة خط سيارة من القبلة لجامعة البصرة علوم حاسبات""") == vReject

  test "training course advert":
    check verdict("""تعلن الشركة عن إطلاق البرنامج: الدورة الدولية IT Support للمبتدئين في البصرة""") == vReject

suite "reasons are always given":
  test "every verdict explains itself":
    let d = classify("مطلوب مهندس كهرباء حديث التخرج في البصرة", cfg)
    check d.reasons.len > 0
    check d.role == "مهندس كهرباء"
