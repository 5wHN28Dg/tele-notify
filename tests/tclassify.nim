## One test per problem from docs/problems-report.md, using invented posts
## (your real Saved Messages stay private and are used by `telenotify eval`).

import std/[unittest, os, json, times]
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

  test "a listed job that matches only by its degree is judged alone (#29 sample 2)":
    check verdict("""وظائف جديدة – البصرة
1. Coordinators & Project Managers – جنوب العراق
📍 Basra
• 5+ سنوات خبرة في ادارة المشاريع.

2. Bureau Veritas – Commissioning Engineer
📍 Basra – On-site
Requirements:
• بكالوريوس هندسة كهرباء.
• حديث التخرج.

3. Technical Sales Engineer
📍 Basrah
• 5+ سنوات في Technical Sales.""") == vPublish

  test "another job named in the plural doesn't share its requirements":
    check verdict("""شركة في البصرة تعلن عن فرص عمل
1. Project Managers
• 5+ سنوات خبرة.
2. مهندس كهرباء حديث التخرج""") == vPublish

  test "a job word inside a numbered requirement doesn't make it another job":
    check verdict("""شركة في البصرة تعلن عن فرص عمل
1. Commissioning Engineer
• بكالوريوس هندسة كهرباء.
• 5+ سنوات خبرة، وخبرة مع EPC/Developer.""") == vReject

  test "a bullet with a number before a list mark is still a list item":
    check verdict("""شركة في البصرة تعلن عن فرص عمل
🔹 1- محاسب خبرة 5 سنوات
🔹 2- مهندس كهرباء حديث التخرج""") == vPublish

  test "a numbered degree requirement is not a job of its own":
    check verdict("""مطلوب مهندس كهرباء للعمل في البصرة
1- بكالوريوس هندسة كهرباء
2- حديث التخرج""") == vPublish

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

suite "review pass 1 findings":
  test "place names do not match inside other words (واسط/بواسطة, مصر/مصرف)":
    # (Not tested: "بقطر" really is ambiguous, "in Qatar" or "with diameter",
    # so review is a fair answer for it.)
    for tail in ["ارسال السيرة الذاتية بواسطة البريد", "راتب عبر مصرف الرافدين"]:
      check verdict("مطلوب مهندس كهرباء حديث التخرج للعمل في البصرة\n" & tail) == vPublish

  test "a later numbered job without our role is not glued onto ours":
    check verdict("شركة في البصرة تعلن عن حاجتها الى:\n1- مهندس كهرباء حديث التخرج\n2- محاسب خبرة 5 سنوات") == vPublish

  test "employers opening with 'looking for a job?' are still jobs":
    check verdict("Looking for a job? We are hiring a fresh graduate electrical engineer in Basra!") == vPublish
    check verdict("هل انت باحث عن عمل؟ تعلن شركة في البصرة عن حاجتها الى مهندس كهرباء حديث التخرج") == vPublish

  test "'experienced' or 'manager' elsewhere on the title line is not seniority":
    check verdict("Hiring Electrical Engineer in Basra (fresh graduates or experienced)") == vPublish
    check verdict("Hiring a junior Electrical Engineer in Basra who reports to the site manager") == vPublish

  test "mandatory certification in the header applies to every job":
    check verdict("شركة في البصرة تعلن عن حاجتها الى، يشترط CCNA لجميع الوظائف:\n1- فني شبكات حديث التخرج\n2- مهندس كهرباء حديث التخرج") == vReject

  test "times and decimals are not list items":
    check verdict("شركة في البصرة تعلن عن حاجتها الى مهندس كهرباء حديث التخرج\n8:00 - 4:00 دوام\n2.5 مليون راتب") == vPublish

  test "requirements after a compact title list apply to every job":
    check verdict("تعلن شركة في البصرة عن حاجتها الى\n1- مهندس كهرباء\n2- مهندس ميكانيك\n\nالمتطلبات:\nخبرة لا تقل عن 5 سنوات") == vReject
    check verdict("تعلن شركة في البصرة عن حاجتها الى\n1 - مهندس كهرباء\n2- مهندس LOTO\nبخبرة لاتقل عن 4 سنوات") == vReject

  test "years in a field count as experience":
    check verdict("Hiring: Cybersecurity Specialist, fresh graduates welcome\nLocation: Basra\n3–7 years in cybersecurity") == vReject

suite "review pass 2 (adversarial) findings":
  test "'not less than' is a minimum, not an upper bound":
    check verdict("Hiring: Electrical Engineer, Basra\nExperience not less than 5 years in power distribution") == vReject
    check verdict("مطلوب مهندس كهرباء في البصرة\nالخبرة: ليس اقل من 5 سنوات") == vReject
    check verdict("مطلوب مهندس كهرباء في البصرة\nخبرة من 3 حتى 5 سنوات") == vReject

  test "more experience phrasings":
    check verdict("مطلوب مهندس كهرباء في البصرة\nخبرة لا تقل عن خمس (5) سنوات") == vReject
    check verdict("Hiring: Network Engineer, Basra\nYears of Experience: 5+") == vReject
    check verdict("مطلوب مهندس شبكات في البصرة\nعدد سنوات الخبرة: 6") == vReject
    check verdict("Hiring IT support in Basra\n5+ yrs in enterprise IT support") == vReject
    check verdict("Hiring: Network Engineer, Basra\nMinimum 36 months of hands-on experience") == vReject
    check verdict("مطلوب مهندس كهرباء في البصرة\nممارسة فعلية في المجال لا تقل عن 4 سنوات") == vReject
    check verdict("مطلوب مهندس كهرباء في البصرة\nخبرة عملية واسعة في مجال تصميم وتنفيذ وصيانة أنظمة التوزيع والمحطات الكهربائية لا تقل عن سبع سنوات") == vReject

  test "certifications listed under a 'required' heading are mandatory":
    check verdict("Hiring: Junior Network Technician, Basra\nRequired certifications:\n- CCNA\n- CCNP") == vReject

  test "numbered requirement lines are not other jobs":
    check verdict("📢 فرصة عمل في البصرة\n1- فني دعم تقني\n2- خبرة لا تقل عن 4 سنوات\n3- اجادة الانكليزي") == vReject

  test "a header naming the role still lets each listed job be judged alone":
    check verdict("تعلن شركة في البصرة عن حاجتها الى مبرمجين:\n1- مبرمج جافا خبرة 5 سنوات\n2- مبرمج تطبيقات حديث التخرج") == vPublish

  test "job seekers in other words":
    check verdict("Fresh graduate computer engineer from Basra 🎓\nSeeking a position as IT Support\nCV available") == vReject
    check verdict("اني مهندس شبكات حديث التخرج ساكن بالبصرة\nاريد اشتغل باي شركة، اذا اكو شاغر راسلوني") == vReject

  test "intensive course adverts":
    check verdict("📢 اعلان دورة IT Support المكثفة\n🎓 للخريجين الجدد\n📍 البصرة\n💰 رسوم التسجيل: 150 الف دينار") == vReject

  test "places that only look like Basra":
    check verdict("Hiring: junior Control Engineer\nLocation: Zakho\nWork on remote terminal units") == vReject
    check verdict("مطلوب مهندس كهرباء حديث التخرج للعمل في المدينة المنورة") == vReject
    check verdict("مطلوب مهندس كهرباء حديث التخرج للعمل في كربلا، الشركة فرعها الرئيسي في البصرة") != vPublish

  test "more seniority wording":
    check verdict("مطلوب مهندس كهرباء ذو خبرة للعمل في البصرة مع فترة تجربة") != vPublish
    check verdict("مطلوب مهندس شبكات أقدم للعمل في البصرة مع فترة تجربة") != vPublish

  test "Unicode bold and Arabic presentation forms are normal text":
    check verdict("𝗦𝗲𝗻𝗶𝗼𝗿 𝗡𝗲𝘁𝘄𝗼𝗿𝗸 𝗘𝗻𝗴𝗶𝗻𝗲𝗲𝗿 𝟴+ 𝘆𝗲𝗮𝗿𝘀 𝗼𝗳 𝗲𝘅𝗽𝗲𝗿𝗶𝗲𝗻𝗰𝗲\nBachelor graduate\nBasra\nSend CV") == vReject
    check verdict("We are hiring: 𝗜𝗧 𝗦𝘂𝗽𝗽𝗼𝗿𝘁 (𝗙𝗿𝗲𝘀𝗵 𝗚𝗿𝗮𝗱𝘂𝗮𝘁𝗲) 📍 𝗕𝗮𝘀𝗿𝗮") == vPublish
    check verdict("ﻣﻄﻠﻮﺏ ﻣﻬﻨﺪﺱ ﻛﻬﺮﺑﺎﺀ ﺣﺪﻳﺚ ﺍﻟﺘﺨﺮﺝ\nﻣﻮﻗﻊ ﺍﻟﻌﻤﻞ: ﺍﻟﺒﺼﺮﺓ") == vPublish

  test "short certificates do not match longer words":
    check verdict("We are hiring: IT Support (fresh graduates), Basra\nMust be capable of working in shifts") == vPublish

  test "fresh-graduate wording is not years of experience":
    check verdict("We are hiring: IT Support in Basra\nOpen to fresh graduates (graduated within the last 2 years), no experience required.") == vPublish
    check verdict("تعلن شركة في البصرة عن برنامج تدريبي لمدة عامين لاكتساب الخبرة\nمطلوب مهندس كهرباء حديث التخرج") == vPublish

  test "large posts stay fast":
    var post = "مطلوب:\n"
    for i in 0 ..< 3500: post.add $(i mod 99 + 1) & "- it support\n"
    var line = ""
    for i in 0 ..< 2800: line.add "خبرة 1 سنة "
    let t0 = epochTime()
    discard verdict(post)
    discard verdict("مطلوب it support في البصرة\n" & line)
    # was 68 s before the fix; ~3 s in a debug build, ~0.2 s in release
    check epochTime() - t0 < 10.0

suite "found on recent channel posts":
  test "a female manager role and 'dealing with trainees' are not entry level":
    check verdict("تعلن شركة هندسية في محافظة البصرة عن حاجتها إلى موظفة للعمل بمنصب مديرة مركز تدريب\nشهادة جامعية ويفضل في التخصصات الهندسية\nمهارات عالية في التواصل والتعامل مع المتدربين\nموقع العمل: محافظة البصرة") != vPublish

suite "found on the first real run (stage 4)":
  test "'extensive experience' outweighs a channel's 'fresh graduates and experienced' tag":
    let post = "#وظائف_نفطية #البصرة التقديم يضم #حديثي_التخرج واصحاب الخبرة\nفرصة عمل\nمهندس كهرباء - ضمان ومراقبة الجودة (QA/QC)\nموقع العمل: البصرة\nخبرة واسعة في مجال ضمان ومراقبة الجودة"
    check verdict(post) == vReview
    check verdict("Hiring electrical engineer in Basra, fresh graduates welcome. Proven field experience required") == vReview

  test "a real fresh-graduate post still publishes":
    check verdict("مطلوب مهندس كهرباء حديث التخرج للعمل في البصرة") == vPublish
    check verdict("#البصرة مطلوب مهندس كهرباء #حديثي_التخرج\nموقع العمل: البصرة") == vPublish
    check verdict("Hiring Electrical Engineer in Basra (fresh graduates or experienced), strong communication skills") == vPublish

  test "'strong experience' only counts when it is asked for, as a level":
    const fg = "Hiring electrical engineer in Basra, fresh graduates welcome. "
    for t in ["No extensive experience required", "Deep learning experience is a plus",
              "This internship lets you gain solid practical experience",
              "Studied abroad experience is a plus", "We want strong, experienced communicators",
              "Strong Excel experience", "Up to 2 years of experience. Strong experience with AutoCAD is a plus"]:
      check verdict(fg & t) == vPublish
    check verdict("مطلوب مهندس كهرباء حديث التخرج للعمل في البصرة لا يشترط خبرة واسعة") == vPublish
    for t in ["Extensive hands-on experience required", "Strong field experience in oil and gas"]:
      check verdict(fg & t) == vReview
    for t in ["الخبرة الواسعة في المجال", "خبرات واسعة", "خبرة عملية واسعة"]:
      check verdict("مطلوب مهندس كهرباء حديث التخرج للعمل في البصرة\n" & t) == vReview

  test "'strong experience': review pass 2 cases":
    const fg = "Hiring electrical engineer in Basra, fresh graduates welcome. "
    const ar = "مطلوب مهندس كهرباء حديث التخرج للعمل في البصرة\n"
    for t in ["We are a company with extensive experience in oil and gas",
              "Interns will work alongside staff with extensive industry experience",
              "You will earn broad industry experience", "Extensive experience is not required",
              "Extensive experience is a plus", "Strong experience with MS Office",
              "strong experience of working in teams"]:
      check verdict(fg & t) == vPublish
    for t in ["شركة نفطية ذات خبرة واسعة", "ستحصل على خبرة عملية واسعة", "الخبرة الواسعة غير مطلوبة",
              "يفضل من لديه خبرة واسعة", "خبرة ممتازة في الاوتوكاد"]:
      check verdict(ar & t) == vPublish
    for t in ["We do not offer accommodation. Extensive experience in substations required",
              "Contract No. 12. Strong field experience required",
              "Get in touch: extensive experience in substations required"]:
      check verdict(fg & t) == vReview
    for t in ["اولا: خبرة واسعة", "مثلا خبرة واسعة في المجال", "مشروع صغير\nخبرة واسعة", "خبرة سابقة واسعة"]:
      check verdict(ar & t) == vReview

suite "corpus false publishes (Nov 2026)":
  test "training only for current students is not published":
    check verdict("""الاعلان عن فرصة تدريب صيفي في شركة نفطية
شروط القبول:
- ان يكون احد طلبة الجامعة في قسم هندسة السيطرة
- ان يكون احد طلبة المرحلة الثانية او الثالثة
- من سكنة محافظة البصرة""") == vReview
    check verdict("""Summer training opportunity at an oil company in Basra
Open to current students of electrical engineering only""") == vReview

  test "training for students and graduates still publishes":
    check verdict("""فرصة تدريب صيفي في شركة نفطية في البصرة
للطلبة والخريجين من قسم هندسة السيطرة""") == vPublish

  test "a CV posted by a job seeker is rejected":
    # "get the job done" was the only job wording in the real one
    check verdict("""AHMED ALI
Basra, Iraq

WHO AM I?
I'm a cybersecurity volunteer who gets the job done.""") == vReject
    check verdict("""CAREER OBJECTIVE
Cybersecurity volunteer in Basra. I get the job done.""") == vReject

  test "an advert asking for an 'About me' paragraph is not a CV":
    check verdict("""Internship opportunity in Basra – IT Support
Fresh graduates welcome.
Send your CV with a short "About me" section to hr@example.com""") == vPublish

  test "a seniority word on the title line holds back a field-only match":
    check verdict("""RSK Iraq – HSE Lead (Basra)
We are looking for an HSE Lead to serve as our on-site authority.
Education: Degree in Electrical Engineering, graduate preferred.""") == vReview

  test "a graduate programme named like a senior title still publishes":
    # Baker Hughes calls its graduate scheme "LEAD"
    check verdict("""LEAD Field Engineering
Development Program – 2027
Location: Basra, Iraq
Position: Field Engineer I
- Bachelor's degree in Engineering in any discipline.
- Graduated within the last 12 months.""") == vPublish

  test "students-only: more wordings, and 'graduate' that means still studying":
    check verdict("""فرصة تدريب صيفي في البصرة
ان يكون طالبا في المرحلة الرابعة قسم هندسة السيطرة""") == vReview
    check verdict("""Summer training opportunity at an oil company in Basra
For final year students of electrical engineering.""") == vReview
    check verdict("""Summer training opportunity at an oil company in Basra
Open to current students of electrical engineering only, expected to graduate in 2027.""") == vReview
    check verdict("""فرصة تدريب صيفي في البصرة
ان يكون احد طلبة المرحلة الرابعة في قسم هندسة السيطرة
ارفاق عنوان مشروع التخرج""") == vReview

  test "students-only: graduates welcome, or students excluded, still publishes":
    check verdict("""Summer training opportunity at an oil company in Basra
Open to current students and fresh graduates of electrical engineering.""") == vPublish
    check verdict("""Junior position in Basra – Electrical Engineering
Bachelor's in Electrical Engineering, no experience required.
Current students are not eligible.""") == vPublish

  test "students-only holds back publish_when_unsure too":
    var j = parseFile(currentSourcePath.parentDir / "keywords.json")
    j["publish_when_unsure"] = %true
    let c2 = parseConfig(j)
    check classify("""Electrical engineer wanted in Basra
Open to current students only""", c2).verdict == vReview

  test "the headline rule ignores duty lines and 'first' (الاولى)":
    check verdict("""Graduate Trainee – Basra
Fresh graduates welcome.
Degree in Electrical Engineering.
In this role you will support the project manager on site.""") == vPublish
    check verdict("""اعلان توظيف - الوجبة الاولى
شركة في البصرة تعلن عن حاجتها الى حديثي التخرج
التخصص: هندسة السيطرة""") == vPublish

  test "the programme exemption only counts at the top":
    check verdict("""RSK Iraq – HSE Lead (Basra)
We are looking for an HSE Lead to serve as our on-site authority.
Degree in Electrical Engineering, graduate preferred.
We offer a professional development program for all staff.""") == vReview
    check verdict("""Baker Hughes LEAD Program 2027 – Basra
Graduate opportunity for fresh graduates
Location: Basra, Iraq
Bachelor's degree in Electrical Engineering.""") == vPublish

  test "adversarial review: real entry-level posts are not held back":
    check verdict("""IT support technician wanted at our Basra campus.
You will help university students and staff with laptops, Wi-Fi and printers.
Apply by email.""") == vPublish
    check verdict("""Summer internship - Electrical Engineering
Open to final year students or those who graduated in 2025.
Location: Basra. Apply now.""") == vPublish
    check verdict("""Junior IT support - Basra
Current students will not be considered; you must hold your degree already.
Apply now.""") == vPublish
    check verdict("""Who am I looking for?
A junior IT support technician in Basra, no experience required.
Apply via email.""") == vPublish
    check verdict("""Ali Hassan - Senior Talent Acquisition Partner at KBR
We are hiring a graduate engineer with a degree in Engineering (electrical).
Fresh graduates welcome. Location: Basra. Apply now.""") == vPublish
    check verdict("""اعلان صادر عن مكتب المدير العام لشركة نفط البصرة
تعلن الشركة عن حاجتها الى خريجي هندسة كهرباء من حديثي التخرج للعمل في البصرة""") == vPublish
    check verdict("""فرصة تدريب في البصرة
مطلوب متدرب هندسة حاسبات
يستثنى الطلبة الحاليين""") == vPublish
    check verdict("""فرصة تدريب صيفي في البصرة لطلبة الجامعات والمتخرجين حديثا
التخصص: هندسة حاسبات""") == vPublish
    check verdict("""Junior IT support - Basra
Applicants must have finished their degree. Not open to current students.
Apply now.""") == vPublish
    check verdict("""Help desk technician (Basra), entry level.
Please include a short career objective in your CV and apply by email.""") == vPublish
    check verdict("""Expert Solutions Iraq
We are hiring someone with a degree in Engineering. Fresh graduates welcome. Location: Basra. Apply now.""") == vPublish
    check verdict("""Junior IT support in Basra, apply now. Open to final year students as well as graduated engineers.""") == vPublish

  test "adversarial review: senior or students-only posts still held back":
    check verdict("""Internship - Basra. Apply now.
Requirements: degree in Engineering (in progress); open only to students in their final year.""") == vReview
    check verdict("""فرصة تدريب في البصرة
مطلوب متدرب هندسة حاسبات
للطلبة فقط""") == vReview
    check verdict("""Lead Engineer - Basra
Part of the Majnoon expansion programme. Apply now.
Requirements: degree in Engineering. Probation period applies.""") == vReview
