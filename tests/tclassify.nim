## One test per problem from docs/problems-report.md, using invented posts
## (your real Saved Messages stay private and are used by `telenotify eval`).

import std/[unittest, os, json, times, strutils]
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

  test "requirements after a look-alike job line still count (review of #29 fix)":
    check verdict("""مطلوب للعمل في شركة نفطية في البصرة
1- مهندس كهرباء
2- تحمل المسؤولية والعمل تحت الضغط
3- خبرة لا تقل عن 10 سنوات""") == vReject
    check verdict("""شركة نفطية في البصرة بحاجة الى مهندس حديث التخرج
الشروط:
1- خريج معهد فني او بكالوريوس هندسة كهرباء
2- القدرة على تحمل المسؤولية
3- خبرة لا تقل عن 8 سنوات في المشاريع النفطية""") == vReject
    check verdict("""شركة في البصرة تعلن عن فرص عمل للخريجين الجدد
1- مهندس كهرباء
الشروط: بكالوريوس هندسة كهرباء
2- محاسب
خبرة سنتين
━━━━━━
شروط عامة لجميع الوظائف: خبرة لا تقل عن 6 سنوات""") == vReject
    check verdict("""We are hiring in Basra - fresh graduates welcome
1. Electrical Engineer
2. Must work closely with site managers and technicians
3. Minimum 10 years of experience""") == vReject

  test "adversarial review of the #29 fix":
    check verdict("""تعلن شركة النور في البصرة عن حاجتها الى:
1- مهندس كهرباء حديث التخرج
2- محاسبة خبرة 5 سنوات""") == vPublish
    check verdict("""تعلن شركة النور في البصرة عن حاجتها الى:
1- مهندس كهرباء حديث التخرج
2- مندوبة مبيعات خبرة 5 سنوات
3- مشرفين مواقع خبرة 10 سنوات""") == vPublish
    check verdict("""تعلن شركة النور في البصرة عن حاجتها الى:
1- مهندس كهرباء حديث التخرج
2- مبرمجة خبرة 5 سنوات
3- مساعدة مدير خبرة 4 سنوات""") == vPublish
    check verdict("""تعلن شركة النور في البصرة عن حاجتها الى:
🔸 1 محاسب خبرة 5 سنوات
🔸 2 مهندس كهرباء حديث التخرج""") == vPublish
    check verdict("""We are hiring - Location: Basra
Requirements:
1. Engineer with B.Sc. in Electrical Engineering
2. Minimum 5 years experience as a site engineer in oil and gas
3. Fresh graduates with internship experience are welcome to apply for the trainee track""") == vReject
    check verdict("""مطلوب مهندس للعمل في شركة نفطية
موقع العمل: البصرة
الشروط:
1- مهندس حاصل على بكالوريوس هندسة كهرباء
2- خبرة لا تقل عن 7 سنوات كمهندس موقع""") == vReject

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

suite "paid course adverts go to review (false positive 2026-11-23)":
  # Invented posts, written like the advert the bot published: a training
  # centre opening registration for a paid course, with a discount for new
  # graduates, in Basra. No job, salary or hiring anywhere. The owner chose
  # on 2026-11-23 that such a post goes to review, never to reject.
  const courseAdvert = """شركة النخيل الهندسية – مركز التدريب والتطوير
يعلن المركز عن فتح باب التسجيل في البرنامج التدريبي
برنامج تدريبي متخصص: "أساسيات أنظمة التحكم الصناعي PLC"
محاور البرنامج التدريبي:
1- مقدمة في أنظمة التحكم
2- البرمجة بلغة السلم
3- تطبيقات عملية
مدة البرنامج: 5 أيام تدريبية
🎓 خصم خاص للخريجين الجدد!
الفئة المستهدفة: خريجو التخصصات الهندسية وطلبة المراحل المنتهية
📍 الموقع: البصرة – العشار
للتسجيل والاستفسار: 07700000000
انستغرام: @example_training
استثمر في مهاراتك اليوم"""

  proc courseReview(post: string): bool =
    let d = classify(post, cfg)
    d.verdict == vReview and d.reasons[^1].startsWith("but it looks like a course or training you pay for")

  test "a training centre's paid course goes to review":
    check courseReview(courseAdvert)

  test "the same advert in English":
    check courseReview("""Example Engineering – Training & Development Center
Registration is open for our training course: Industrial Automation Basics
Course outline: PLC, SCADA, HMI
Duration: 5 days, 30 training hours, accredited certificate
Special discount for fresh graduates!
Target audience: electrical engineering graduates
Don't miss this opportunity! Location: Basra. Register now: 07700000000""")

  test "it only holds back a publish: a rejected course stays rejected":
    let d = classify("""شركة النخيل الهندسية – مركز التدريب والتطوير
يعلن المركز عن فتح باب التسجيل في برنامج تدريبي متخصص
محاور البرنامج: أنظمة التحكم
🎓 خصم خاص للخريجين الجدد!
الفئة المستهدفة: خريجو التخصصات الهندسية
📍 الموقع: بغداد – الكرادة""", cfg)
    check d.verdict == vReject
    check not d.reasons[^1].startsWith("but it looks like a course")

  test "it only holds back a publish: a review stays a review for its own reason":
    let d = classify("""شركة في البصرة تعلن عن فتح باب التسجيل في البرنامج التدريبي لحديثي التخرج
هندسة كهرباء، مدة البرنامج شهر، مجاناً وبدون رسوم""", cfg)
    check d.verdict == vReview
    check d.reasons[^1] == "no sign of level either way"

  test "one or two signs are not enough":
    check verdict("""مطلوب مهندس كهرباء حديث التخرج للعمل في البصرة
سيخضع المقبولون لدورة تدريبية في الشركة
ارسال السيرة الذاتية""") == vPublish
    check verdict("""شركة في البصرة تعلن عن فتح باب التسجيل لمهندس كهرباء حديث التخرج
للتسجيل ارسال السيرة الذاتية""") == vPublish
    check verdict("""مطلوب مهندس كهرباء حديث التخرج للعمل في أكاديمية في البصرة لتقديم الدورات""") == vPublish

  test "without a fee a training centre is not a paid course":
    check verdict("""مركز التدريب في شركة الطاقة يعلن عن فتح باب التسجيل في برنامج تدريبي لخريجي هندسة كهرباء حديثي التخرج في البصرة
محاور البرنامج: السلامة والتشغيل""") == vPublish

  test "an unrelated 'بدون خبرة' before a fee doesn't make it free":
    check courseReview("""تعلن شركة النخيل عن فتح باب التسجيل في البرنامج التدريبي لخريجي هندسة كهرباء في البصرة
بدون خبرة، رسوم البرنامج 150 ألف دينار""")

  test "an unrelated 'No experience.' before a fee doesn't make it free":
    check courseReview("""Registration is open for our training course for electrical engineering fresh graduates in Basra. Don't miss this opportunity.
No experience needed. Fee: 100$""")

  test "a free phrase in the fee's sentence makes the post free":
    check verdict("""تعلن وزارة الاتصالات عن فتح باب التسجيل في برنامج تدريبي لخريجي هندسة حاسبات حديثي التخرج في البصرة
ملاحظة: التقديم مجاني ولا توجد اي رسوم""") == vPublish
    check verdict("""تعلن اكاديمية النور في البصرة عن فتح باب التسجيل في دورة مهندس شبكات لحديثي التخرج
الرسوم مجانا للأوائل و 100 الف للبقية""") == vPublish

  test "engineering drawings and price quotations are not fees":
    check verdict("""تعلن شركة الرافدين للمقاولات في البصرة عن توفر فرصة لمهندس كهرباء حديث التخرج
المهام: اعداد الرسوم الهندسية والمخططات
خريج دورة AutoCAD وحاصل على شهادة معتمدة""") == vPublish
    check verdict("""Electrical Engineer (Sales) in Basra, fresh graduates welcome.
Duties: prepare price quotations for clients. Completed an AutoCAD course; accredited certificate preferred.
A great opportunity.""") == vPublish

  test "a staff discount is a benefit, not a fee":
    check verdict("""تعلن أكاديمية الخليج في البصرة عن توفر فرصة لمهندس شبكات حديث التخرج لإدارة شبكة الأكاديمية
الامتيازات: خصم 50% على دورات الأكاديمية""") == vPublish
    check verdict("""Software Engineer in Basra, fresh graduates welcome. A great opportunity.
Benefits: staff discount on all academy courses.""") == vPublish

  test "main's job-advert wording keeps a job (Job Title)":
    check verdict("""Job Title: Network Engineer at Basra Training Center. Fresh graduates welcome.
Duties: deliver CCNA courses; trainees get a 10% discount.""") == vPublish

  test "a hiring hashtag (#مطلوب) keeps a job":
    check verdict("""#مطلوب
مهندس شبكات حديث التخرج في أكاديمية النور – البصرة
يقدم دورات الشبكات للمتدربين مع خصم 20% للطلبة""") == vPublish

  test "hiring words match word starts (تعيينهم)":
    check verdict("""تعلن شركة الطاقة عن فتح باب التسجيل في برنامج تدريبي لخريجي هندسة كهرباء حديثي التخرج في البصرة، رسوم الاستمارة 10 الاف
وسيتم تعيينهم في الشركة بعد اجتياز البرنامج""") == vPublish

  test "'للعمل' right after a job keeps a job":
    check verdict("""أكاديمية النور في البصرة تفتح باب التسجيل لمهندس شبكات حديث التخرج للعمل مدرباً في دوراتها
مع خصم 50% على دوراتها لعائلته""") == vPublish

  test "'المطلوبة' (required documents) is not 'wanted'":
    check courseReview("""يعلن مركز التدريب الهندسي في البصرة عن فتح باب التسجيل في دورة مهندس شبكات لخريجي هندسة حاسبات حديثي التخرج
الرسوم 150 الف
الوثائق المطلوبة: صورة الهوية""")

  test "'مسارك الوظيفي' is not a job":
    check courseReview("""تعلن اكاديمية النور في البصرة عن فتح باب التسجيل في دورة مهندس شبكات لحديثي التخرج
ابدأ مسارك الوظيفي، الرسوم 100 الف مع خصم للخريجين""")

  test "'تؤهلك للعمل' is a course's promise, not a job":
    check courseReview("""اكاديمية النور في البصرة تفتح باب التسجيل في دورة مهندس شبكات لحديثي التخرج تؤهلك للعمل في الشركات
الرسوم 100 الف""")

  test "hashtags are not course signs":
    check verdict("""تعلن شركة النخيل في البصرة عن فتح باب التسجيل لخريجي هندسة كهرباء حديثي التخرج
رسوم الاستمارة 10 الاف
#دورات #كورسات""") == vPublish

  test "a channel signature is not a course sign":
    check verdict("""تعلن شركة النخيل في البصرة عن فتح باب التسجيل لخريجي هندسة كهرباء حديثي التخرج، رسوم الاستمارة 10 الاف
تابعونا على قناة دورات البصرة""") == vPublish

  test "only the signature is left out, not the rest of its line":
    check courseReview("""تعلن اكاديمية النور في البصرة عن دورة لخريجي هندسة كهرباء حديثي التخرج، الرسوم 100 الف، تابعونا على قناة اكاديمية النور""")

suite "course adverts: every post of the #152 reviews":
  # main publishes all of these except where noted; the course rule may
  # hold one for review but never rejects it
  test "review 1/A2_trainer_job_ar (main: publish)":
    check verdict("""مطلوب مهندس كهرباء حديث التخرج للعمل مدرباً في مركز تدريب في البصرة لتقديم الدورات
للتقديم ارسال السيرة الذاتية""") == vPublish

  test "review 1/A3_trainer_job_en (main: publish)":
    check verdict("""We are looking for an electrical engineer (fresh graduate) to teach PLC courses at our Training Center in Basra.
Send your CV.""") == vPublish

  test "review 1/A_trainer_job_ar (main: publish)":
    check verdict("""أكاديمية النور للتدريب في البصرة بحاجة الى مهندس كهرباء حديث التخرج للعمل كمدرب في دورات PLC
للتسجيل ارسال السيرة الذاتية على الواتساب""") == vPublish

  test "review 1/B2_paid_trainee_ar (main: reject)":
    check verdict("""مركز التدريب في شركة الطاقة يعلن عن فتح باب التسجيل في برنامج تدريب بأجر لخريجي هندسة كهرباء حديثي التخرج في البصرة""") == vReject

  test "review 1/B_paid_trainee_ar (main: publish)":
    check verdict("""أكاديمية شركة نفط البصرة تعلن عن فتح باب التسجيل في برنامج تدريبي مدفوع لخريجي هندسة كهرباء حديثي التخرج
مع منحة شهرية للمتدربين، الموقع: البصرة""") == vPublish

  test "review 1/C_summer_ar (main: publish)":
    check verdict("""تعلن شركة نفط البصرة – مركز التدريب والتطوير عن فتح باب التسجيل للتدريب الصيفي لطلبة هندسة كهرباء والخريجين الجدد
مدة البرنامج: 6 أسابيع. الموقع: البصرة""") == vPublish

  test "review 1/D2_summer_en (main: reject)":
    check verdict("""Basra Oil Company Training Center announces its summer training program for electrical engineering students and fresh graduates.
Registration is open now. Location: Basra""") == vReject

  test "review 1/D_summer_en (main: publish)":
    check verdict("""Training center of a Basra oil company: summer training for electrical engineering students and fresh graduates
Registration is open, course outline attached, 20 training days. Location: Basra. Send your CV to apply.""") == vPublish

  test "review 1/E0_pr_advert (main: publish)":
    check verdict("""شركة النخيل الهندسية – مركز التدريب والتطوير
يعلن المركز عن فتح باب التسجيل في البرنامج التدريبي
برنامج تدريبي متخصص: "أساسيات أنظمة التحكم الصناعي PLC"
محاور البرنامج التدريبي:
1- مقدمة في أنظمة التحكم
مدة البرنامج: 5 أيام تدريبية
🎓 خصم خاص للخريجين الجدد!
الفئة المستهدفة: خريجو التخصصات الهندسية وطلبة المراحل المنتهية
📍 الموقع: البصرة – العشار
للتسجيل والاستفسار: 07700000000""") == vReview

  test "review 1/E1_footer_hashtag (main: publish)":
    check verdict("""شركة النخيل الهندسية – مركز التدريب والتطوير
يعلن المركز عن فتح باب التسجيل في البرنامج التدريبي
برنامج تدريبي متخصص: "أساسيات أنظمة التحكم الصناعي PLC"
محاور البرنامج التدريبي:
1- مقدمة في أنظمة التحكم
مدة البرنامج: 5 أيام تدريبية
🎓 خصم خاص للخريجين الجدد!
الفئة المستهدفة: خريجو التخصصات الهندسية وطلبة المراحل المنتهية
📍 الموقع: البصرة – العشار
للتسجيل والاستفسار: 07700000000
#وظائف_البصرة""") == vPublish

  test "review 1/E2_footer_channel (main: publish)":
    check verdict("""شركة النخيل الهندسية – مركز التدريب والتطوير
يعلن المركز عن فتح باب التسجيل في البرنامج التدريبي
برنامج تدريبي متخصص: "أساسيات أنظمة التحكم الصناعي PLC"
محاور البرنامج التدريبي:
1- مقدمة في أنظمة التحكم
مدة البرنامج: 5 أيام تدريبية
🎓 خصم خاص للخريجين الجدد!
الفئة المستهدفة: خريجو التخصصات الهندسية وطلبة المراحل المنتهية
📍 الموقع: البصرة – العشار
للتسجيل والاستفسار: 07700000000
تابعونا على قناة وظائف البصرة""") == vPublish

  test "review 1/E3_qualifies_for_job (main: publish)":
    check verdict("""شركة النخيل الهندسية – مركز التدريب والتطوير
يعلن المركز عن فتح باب التسجيل في البرنامج التدريبي
برنامج تدريبي متخصص: "أساسيات أنظمة التحكم الصناعي PLC"
محاور البرنامج التدريبي:
1- مقدمة في أنظمة التحكم
مدة البرنامج: 5 أيام تدريبية
🎓 خصم خاص للخريجين الجدد!
الفئة المستهدفة: خريجو التخصصات الهندسية وطلبة المراحل المنتهية
📍 الموقع: البصرة – العشار
للتسجيل والاستفسار: 07700000000
البرنامج يؤهلك للحصول على وظيفة في سوق العمل""") == vPublish

  test "review 1/E4_payroll_topic (main: publish)":
    check verdict("""شركة النخيل الهندسية – مركز التدريب والتطوير
يعلن المركز عن فتح باب التسجيل في البرنامج التدريبي
برنامج تدريبي متخصص: "أساسيات أنظمة التحكم الصناعي PLC"
محاور البرنامج التدريبي:
1- مقدمة في أنظمة التحكم
مدة البرنامج: 5 أيام تدريبية
🎓 خصم خاص للخريجين الجدد!
الفئة المستهدفة: خريجو التخصصات الهندسية وطلبة المراحل المنتهية
📍 الموقع: البصرة – العشار
للتسجيل والاستفسار: 07700000000
2- إعداد الرواتب والأجور""") == vPublish

  test "review 1/E5_internship_mention (main: publish)":
    check verdict("""شركة النخيل الهندسية – مركز التدريب والتطوير
يعلن المركز عن فتح باب التسجيل في البرنامج التدريبي
برنامج تدريبي متخصص: "أساسيات أنظمة التحكم الصناعي PLC"
محاور البرنامج التدريبي:
1- مقدمة في أنظمة التحكم
مدة البرنامج: 5 أيام تدريبية
🎓 خصم خاص للخريجين الجدد!
الفئة المستهدفة: خريجو التخصصات الهندسية وطلبة المراحل المنتهية
📍 الموقع: البصرة – العشار
للتسجيل والاستفسار: 07700000000
Course includes an internship opportunity""") == vPublish

  test "review 1/F1_neg_bidun_khibra (main: publish)":
    check verdict("""تعلن شركة النخيل عن فتح باب التسجيل في البرنامج التدريبي لخريجي هندسة كهرباء في البصرة
بدون خبرة، رسوم البرنامج 150 ألف دينار""") == vReview

  test "review 1/F2_neg_la_tafut (main: publish)":
    check verdict("""تعلن شركة النخيل عن فتح باب التسجيل في البرنامج التدريبي لخريجي هندسة كهرباء في البصرة
لا تفوت الفرصة! خصم 30% للخريجين الجدد""") == vReview

  test "review 1/F3_neg_en_free (main: publish)":
    check verdict("""Registration is open for our training course for electrical engineering fresh graduates in Basra. Apply now.
Free parking, fee: 100$ per trainee""") == vPublish

  test "review 1/F4_fee_positive_control (main: review)":
    check verdict("""تعلن شركة النخيل عن فتح باب التسجيل في البرنامج التدريبي لخريجي هندسة كهرباء في البصرة
رسوم البرنامج 150 ألف دينار""") == vReview

  test "review 1/F5_la_short (main: publish)":
    check verdict("""تعلن شركة النخيل عن فتح باب التسجيل في البرنامج التدريبي لخريجي هندسة كهرباء في البصرة
لا تفوت! خصم 30% للخريجين الجدد""") == vReview

  test "review 1/F6_no_en (main: publish)":
    check verdict("""Registration is open for our training course for electrical engineering fresh graduates in Basra. Apply now.
No experience needed. Fee: 100$""") == vPublish

  test "review 1/G1_morph (main: publish)":
    check verdict("""تعلن شركة النخيل عن دورتها التدريبية لخريجي هندسة كهرباء في البصرة
سجلوا الآن! أسعار خاصة للخريجين الجدد
محاور الدورة: PLC و SCADA""") == vPublish

  test "review 1/G2_morph_fee_suffix (main: reject)":
    check verdict("""تعلن شركة النخيل عن فتح باب التسجيل في البرنامج التدريبي لخريجي هندسة كهرباء في البصرة
رسومه مخفضة للخريجين الجدد، اجور الدورة مناسبة""") == vReject

  test "review 1/H1_iraqi_vocab (main: reject)":
    check verdict("""تعلن شركة النخيل عن انطلاق دورة PLC لخريجي هندسة كهرباء في البصرة
الكلفة 150 ألف دينار فقط، احجز مقعدك الآن، المقاعد محدودة
تمنح شهادة معتمدة في نهاية الدورة""") == vReject

  test "review 1/I1_free_hire_ar (main: publish)":
    check verdict("""تعلن شركة الطاقة – مركز التدريب عن فتح باب التسجيل في برنامج تدريبي مجاني لخريجي هندسة كهرباء حديثي التخرج في البصرة
والمتميزون سيتم ضمهم للعمل في الشركة""") == vPublish

  test "review 1/I2_free_hire_en (main: publish)":
    check verdict("""Basra Energy Company Training Center: registration is open for a free training program for electrical engineering fresh graduates in Basra.
Top trainees will join our engineering team. Apply now.""") == vPublish

  test "review 1/I3_fee_free_after (main: publish)":
    check verdict("""تعلن شركة الطاقة عن فتح باب التسجيل في برنامج تدريبي لخريجي هندسة كهرباء حديثي التخرج في البصرة
الرسوم: مجاناً بالكامل""") == vPublish

  test "review 1/J1_hire_pronoun (main: publish)":
    check verdict("""تعلن شركة الطاقة – مركز التدريب عن فتح باب التسجيل في برنامج تدريبي مجاني لخريجي هندسة كهرباء حديثي التخرج في البصرة
وسيتم تعيينهم في الشركة بعد اجتياز البرنامج""") == vPublish

  test "review 1/J2_salary_pronoun (main: publish)":
    check verdict("""أكاديمية شركة نفط البصرة تعلن عن فتح باب التسجيل في برنامج تدريبي لخريجي هندسة كهرباء حديثي التخرج
يتقاضى المتدرب راتبا شهريا خلال فترة البرنامج. الموقع: البصرة""") == vPublish

  test "review 1/K1_devcenter_job_en (main: publish)":
    check verdict("""Our Software Development Center in Basra is looking for a fresh graduate python developer.
Free courses for new staff. Register now through the link with your CV.""") == vPublish

  test "review 1/K2_academy_job_ar (main: publish)":
    check verdict("""مطلوب مهندس كهرباء حديث التخرج لأكاديمية النور في البصرة للعمل في قسم الدورات
رابط التسجيل في الوصف""") == vPublish

  test "review 1/smoke (main: publish)":
    check verdict("""مطلوب مهندس كهرباء حديث التخرج للعمل في البصرة
ارسال السيرة الذاتية""") == vPublish

  test "review 2/C01_wathaiq_matlooba (main: publish)":
    check verdict("""يعلن مركز التدريب الهندسي في البصرة عن فتح باب التسجيل في دورة مهندس شبكات لخريجي هندسة حاسبات حديثي التخرج
الرسوم 150 الف
الوثائق المطلوبة: صورة الهوية""") == vReview

  test "review 2/C02_masar_wadhifi (main: publish)":
    check verdict("""تعلن اكاديمية النور في البصرة عن فتح باب التسجيل في دورة مهندس شبكات لحديثي التخرج
ابدأ مسارك الوظيفي، الرسوم 100 الف مع خصم للخريجين""") == vReview

  test "review 2/C03_souq_amal (main: publish)":
    check verdict("""اكاديمية النور في البصرة تفتح باب التسجيل في دورة مهندس شبكات لحديثي التخرج تؤهلك للعمل في الشركات
الرسوم 100 الف""") == vReview

  test "review 2/C04_fee_after_free_word (main: reject)":
    check verdict("""اكاديمية النور في البصرة: باب التسجيل مفتوح لدورة مهندس شبكات لحديثي التخرج
السعر: 100 الف بدل 150، الحجز مجانا""") == vReject

  test "review 2/C05_free_word_far (main: publish)":
    check verdict("""تعلن اكاديمية النور في البصرة عن فتح باب التسجيل في دورة مهندس شبكات لحديثي التخرج
الرسوم مجانا للأوائل و 100 الف للبقية""") == vPublish

  test "review 2/R01_taaqud_academy (main: publish)":
    check verdict("""تعلن أكاديمية البصرة للتدريب عن رغبتها بالتعاقد مع مهندس شبكات حديث التخرج
لتقديم دورات الشبكات للطلبة
التسجيل عبر الرابط: فتح باب التسجيل حتى نهاية الشهر""") == vPublish

  test "review 2/R02_hashtag_matloob (main: publish)":
    check verdict("""#مطلوب
مهندس شبكات حديث التخرج للتدريس في أكاديمية النور - البصرة
لتقديم دورات الشبكات
للتسجيل ارسل السيرة الذاتية على الواتساب""") == vPublish

  test "review 2/R03_gov_train_hire (main: publish)":
    check verdict("""تعلن شركة نفط البصرة / مركز التدريب عن فتح باب التسجيل في الدورة التدريبية لخريجي هندسة حاسبات حديثي التخرج
وسيتم التعاقد مع الناجحين بعد اجتياز الدورة
مدة الدورة 3 اشهر""") == vPublish

  test "review 2/R04_scam_warning (main: publish)":
    check verdict("""تعلن وزارة الاتصالات عن فتح باب التسجيل في برنامج تدريبي لخريجي هندسة حاسبات حديثي التخرج في البصرة
والتعاقد مع المتميزين
ملاحظة: التقديم مجاني ولا توجد اي رسوم""") == vPublish

  test "review 2/R05_oneline_signature (main: publish)":
    check verdict("""مطلوب مهندس شبكات حديث التخرج للعمل مدرب في أكاديمية النور في البصرة لتقديم دورات الشبكات، للتسجيل ارسل سيرتك، تابعونا على قناة وظائف البصرة""") == vPublish

  test "review 2/R06_en_position (main: publish)":
    check verdict("""Position: Network Engineer (Instructor)
Company: Basra Training Center
Location: Basra
Fresh graduates are welcome
Duties: deliver CCNA courses and prepare the course content
Requirements: CCNA certificate
Apply: send CV to jobs@btc.iq""") == vPublish

  test "review 2/R07_staff_discount (main: publish)":
    check verdict("""Job Title: Software Engineer
Location: Basra
Fresh graduates welcome
Benefits: staff discount on all academy courses, health insurance
Register through the link: registration is open until 1 December
Apply by sending CV to hr@x.iq""") == vPublish

  test "review 2/R08_scam_warning_en (main: reject)":
    check verdict("""Kurdistan-Basra Telecom announces its Graduate Development Program for fresh graduates in Basra
Open to computer engineering graduates
Registration is open now through the link
The program includes courses in networking
Note: we never charge any fees""") == vReject

  test "review 2/R09_intern_en (main: publish)":
    check verdict("""Intern - Network Engineer
Basra Academy of Technology, Basra
Fresh graduates
Course duration and training hours: 3 months, then trainees may receive an offer
Apply by sending CV""") == vPublish

  test "review 2/R10_rusoom_draftsman (main: publish)":
    check verdict("""تعلن شركة الرافدين للمقاولات في البصرة عن توفر فرصة لمهندس كهرباء حديث التخرج
المهام: اعداد الرسوم الهندسية والمخططات
الشروط: خريج دورة AutoCAD وحاصل على شهادة معتمدة""") == vPublish

  test "review 2/R11_price_sales_en (main: publish)":
    check verdict("""Job Title: Electrical Engineer (Sales)
Location: Basra
Fresh graduates welcome
Duties: prepare price quotations for clients
Requirements: completed an AutoCAD course; accredited certificate preferred
Apply by sending CV""") == vPublish

  test "review 2/R12_shift_dawra (main: publish)":
    check verdict("""تعلن أكاديمية الخليج في البصرة عن توفر فرصة لمهندس شبكات حديث التخرج
لإدارة شبكة الأكاديمية
الدوام: دورة صباحية
الامتيازات: خصم 50% على دورات الأكاديمية""") == vPublish

  test "review 2/R13_university (main: reject)":
    check verdict("""تعلن جامعة البصرة / مركز الحاسبة الالكترونية عن قبول مهندسين حاسبات حديثي التخرج كمتدربين لمدة سنة ثم التعيين على الملاك
شروط التقديم: خريج دورة CCNA
فتح باب التسجيل الكترونياً، رسوم الاستمارة 10 الاف""") == vReject

  test "review 2/R14_hashtag_wadhaif_only (main: publish)":
    check verdict("""#وظائف_البصرة #فرص_عمل
تعلن أكاديمية التقنية في البصرة عن التعاقد مع مهندس شبكات حديث التخرج للتدريس في دورات الشبكات
التسجيل عبر الرابط او للتسجيل راسلونا""") == vPublish

  test "first version/p5 (main: publish)":
    check verdict("""أكاديمية شركة نفط البصرة تعلن عن فتح باب التسجيل في برنامج تدريبي مدفوع الأجر
لخريجي هندسة كهرباء حديثي التخرج
مدة البرنامج: 3 أشهر، مع مكافأة شهرية للمتدربين
الموقع: البصرة، للتسجيل عبر الرابط""") == vPublish

  test "first version/p6 (main: publish)":
    check verdict("""Training center of a Basra oil company: summer training for electrical engineering students and fresh graduates
Registration is open, course outline attached, 20 training days
Outstanding trainees are offered employment. Location: Basra""") == vPublish

  test "first version/p7 (main: publish)":
    check verdict("""مطلوب مهندس كهرباء حديث التخرج للعمل في البصرة
يتم توفير التدريب، دورة تدريبية لمدة 10 أيام تدريبية وشهادة معتمدة
راتب مجزي، للتقديم ارسال السيرة الذاتية""") == vPublish

  test "first version/p8 (main: publish)":
    check verdict("""مركز التدريب في شركة الطاقة يعلن عن فتح باب التسجيل في برنامج تدريبي مجاني
لخريجي هندسة كهرباء حديثي التخرج، محاور البرنامج: السلامة، التشغيل، الصيانة
البرنامج ينتهي بالتعيين في الشركة. الموقع: البصرة""") == vPublish

  test "first version/k3 (main: publish)":
    check verdict("""Python developer opportunity at our Software Development Center in Basra for fresh graduates.
Free courses for new staff. Register now through the link.""") == vPublish

  test "first version/k4 (main: publish)":
    check verdict("""أكاديمية النور في البصرة تبحث عن مهندس كهرباء حديث التخرج كمدرب في دورات PLC
للتسجيل ارسال السيرة الذاتية""") == vPublish

  test "first version/k5 (main: publish)":
    check verdict("""Summer training for electrical engineering students and fresh graduates at our Training Center in Basra.
Registration is open, course outline attached. Apply now.""") == vPublish

  test "first version/k6 (main: publish)":
    check verdict("""Our Training Center in Basra is looking for a fresh graduate electrical engineer to teach PLC courses.
Register now via the link.""") == vPublish

  test "first version/k7 (main: publish)":
    check verdict("""تعلن شركة الطاقة – مركز التدريب عن فتح باب التسجيل في برنامج تدريبي مجاني لخريجي هندسة كهرباء حديثي التخرج في البصرة
والمتميزون سيتم ضمهم الى كادر الشركة""") == vPublish

  test "first version/k8 (main: publish)":
    check verdict("""Junior python developer opportunity in Basra for fresh graduates.
Free courses at our Training Center.""") == vPublish
