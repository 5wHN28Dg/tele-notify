import std/[unittest, strutils]
import telenotify/dedup

suite "duplicates":
  test "the same job link, however it's written":
    check jobUrls("apply https://www.linkedin.com/jobs/view/123/?utm=x") == @["linkedin.com/jobs/view/123"]
    check duplicateOf("Basra job https://linkedin.com/jobs/view/123", @["other words https://www.linkedin.com/jobs/view/123/"]) != ""

  test "shared links (channels, WhatsApp...) don't make posts duplicates":
    check jobUrls("join https://t.me/jobs_basra and https://wa.me/964770") == newSeq[string]()
    check duplicateOf("مطلوب مهندس كهرباء https://t.me/jobs", @["مطلوب محاسب للعمل في بغداد https://t.me/jobs"]) == ""

  test "almost the same text":
    let a = "تعلن شركة نفط البصرة عن حاجتها الى مهندسين كهرباء حديثي التخرج للعمل في حقل الرميلة"
    let b = "تعلن شركة نفط البصرة عن حاجتها إلى مهندسين كهرباء حديثي التخرج للعمل في حقل الرميلة 🔥"
    check similarity(a, b) > 0.95
    check duplicateOf(b, @[a]).startsWith("same text")

  test "different jobs are not duplicates":
    check similarity("Electrical engineer in Basra, fresh graduates", "Accountant in Baghdad, 5 years") < 0.3
    check duplicateOf("Electrical engineer in Basra", @[]) == ""

  test "a bare company or job-board domain is not a job link":
    check jobUrls("apply at https://careers.company.com/ or https://bayt.com") == newSeq[string]()
    check duplicateOf("Electrical engineer, Basra. Apply: https://careers.company.com",
                      @["Accountant, Basra office. Apply: https://careers.company.com"]) == ""
