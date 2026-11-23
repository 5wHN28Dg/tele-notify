## Built-in word lists the filter needs besides your own keywords.
##
## Every list here can be *extended* from config.json (see `config.nim`, keys
## starting with `extra_`), so you never have to edit code to add a word.
## They are kept in code because they describe the job-ad language itself,
## not your personal preferences.

const
  seniorityWords* = [
    # English: titles that put a role out of reach for a fresh graduate
    "senior", "sr", "lead", "principal", "head of", "chief", "manager",
    "supervisor", "superintendent", "director", "expert", "experienced",
    "team leader", "foreman",
    # Arabic
    "اول", "اقدم", "رئيس", "مدير", "مشرف", "خبير", "ذو خبره", "ذوي الخبره",
    # feminine forms (seniority words must match whole words)
    "اولي", "رئيسه", "مديره", "مشرفه", "خبيره", "ذات خبره", "ذات الخبره",
    "خبره عاليه", "خبره طويله", "مسؤول قسم",
  ]

  ## Words that, right before a level keyword, mean the post is about
  ## *managing* juniors, not hiring one ("mentor junior engineers").
  mentoringWords* = [
    "mentor", "mentoring", "supervise", "supervising", "train", "training of",
    "guide", "guiding", "coach", "coaching", "lead", "leading", "manage",
    "managing", "oversee", "overseeing", "develop",
    "تدريب", "اشراف على", "الاشراف على", "توجيه", "قياده", "التعامل مع",
    "تدريب وتوجيه", "متابعه",
  ]

  ## Phrases that show a post is a job/internship advert at all.
  jobSignals* = [
    "hiring", "wanted", "vacancy", "vacancies", "job", "jobs", "position", "positions",
    "opening", "apply", "applicants", "recruit", "recruiting", "recruitment",
    "career", "careers", "looking for", "is looking", "seeking", "join our",
    "join the team", "cv", "resume", "internship", "intern", "trainee",
    "we are hiring", "job title", "employment", "opportunity", "opportunities",
    "required", "requirements", "qualifications",
    "مطلوب", "مطلوبه", "تعلن", "اعلان", "فرصه عمل", "فرص عمل", "فرصه تدريب",
    "فرصه وظيفيه", "فرص وظيفيه", "وظيفه", "وظائف", "شاغر", "شواغر", "توظيف",
    "للتقديم", "التقديم", "قدم الان", "السيره الذاتيه", "سيره ذاتيه", "سي في",
    "المسمى الوظيفي", "نبحث عن", "تبحث عن", "يبحث عن", "بحاجه الى", "حاجتها الى",
    "حاجتنا الى", "للعمل", "للانضمام", "المتطلبات", "المؤهلات", "الشروط",
    "برنامج تدريبي", "تدريب صيفي",
  ]

  ## Iraqi governorates, cities and neighbouring countries that are *not*
  ## Basra. Used to tell "a Basra company hiring for Maysan" from a Basra job.
  otherPlaces* = [
    "baghdad", "erbil", "irbil", "karbala", "kerbala", "najaf", "maysan",
    "missan", "amarah", "amara", "nasiriyah", "nasiriya", "dhi qar", "thi qar",
    "samawah", "samawa", "muthanna", "kut", "wasit", "diwaniyah", "diwaniya",
    "qadisiyah", "hillah", "hilla", "babylon", "babil", "kirkuk", "mosul",
    "nineveh", "ninawa", "sulaymaniyah", "sulaimaniyah", "sulaymaniyya",
    "slemani", "duhok", "dohuk", "anbar", "ramadi", "fallujah", "diyala",
    "baqubah", "zakho", "soran", "halabja", "koya", "ranya", "akre", "karkh", "rusafa", "mansour", "karrada", "salahuddin", "salah al din", "tikrit", "kurdistan",
    "dubai", "abu dhabi", "uae", "saudi", "riyadh", "jeddah", "qatar", "doha",
    "kuwait", "oman", "muscat", "bahrain", "jordan", "amman", "turkey",
    "istanbul", "iran", "egypt", "cairo", "lebanon", "beirut",
    "بغداد", "اربيل", "كربلاء", "النجف", "نجف", "ميسان", "العماره", "الناصريه",
    "ذي قار", "السماوه", "المثنى", "الكوت", "واسط", "الديوانيه", "القادسيه",
    "الحله", "بابل", "كركوك", "الموصل", "نينوى", "السليمانيه", "دهوك",
    "الانبار", "الرمادي", "الفلوجه", "ديالى", "بعقوبه", "صلاح الدين", "تكريت",
    "كردستان", "زاخو", "سوران", "حلبجه", "كويه", "رانيه", "عقره", "المدينه المنوره", "علي الغربي", "الكرخ", "الرصافه", "عويريج", "المنصور", "الكراده",
    "الاعظميه", "الكاظميه", "زيونه", "دبي", "الامارات", "السعوديه", "الرياض", "جده",
    "قطر", "الدوحه", "الكويت", "عمان", "مسقط", "البحرين", "الاردن", "تركيا",
    "اسطنبول", "ايران", "مصر", "القاهره", "لبنان", "بيروت",
  ]

  ## A place name right after one of these is a street, not a city.
  streetBefore* = ["شارع", "طريق", "ساحه", "street", "st", "road", "rd"]
  streetAfter* = ["street", "st", "road", "rd", "avenue", "highway"]

  ## A place keyword followed by one of these is not a place at all:
  ## "remote terminal units", "remote monitoring", "المدينه المنوره" (Saudi
  ## Arabia, not the Basra district), "المدينه الصناعيه" (an industrial zone).
  notAPlaceAfter* = [
    "terminal", "terminals", "monitoring", "control", "controlled", "access",
    "sensing", "area", "areas", "site", "sites", "locations", "operations",
    "المنوره", "الصناعيه",
  ]

  ## Lines starting with (or containing) these say where the job *is*, or
  ## where the applicant must *live*. A place on such a line beats a place
  ## mentioned anywhere else in the post.
  locationMarkers* = [
    "📍", "location", "job location", "work location", "based in", "site",
    "resident", "residents", "must live", "living in",
    "الموقع", "موقع العمل", "مكان العمل", "مقر العمل", "العنوان", "عنوان الشركه",
    "السكن", "سكنه", "سكان", "يسكن", "من اهالي", "حصرا من", "المحافظه",
  ]

  ## Something on the same line as one of these is optional, not required.
  optionalMarkers* = [
    "plus", "preferred", "preferably", "advantage", "advantageous",
    "desirable", "nice to have", "is an asset", "bonus", "beneficial",
    "يفضل", "ويفضل", "مفضل", "ميزه", "افضليه", "يعتبر ميزه", "يكون ميزه",
  ]

  ## A certification on the same line as one of these is mandatory.
  requiredMarkers* = [
    "required", "must", "mandatory", "essential", "necessary", "need to hold",
    "must hold", "must have", "requires",
    "مطلوب", "يجب", "يشترط", "شرط", "الزامي", "ضروري", "حاصل على", "حاصلا على",
  ]

  ## Phrases from big companies telling people to apply even if they do not
  ## tick every box. Per issue #25 these may only excuse a missing
  ## certification, never years of experience.
  applyAnyway* = [
    "even if you don't meet", "even if you do not meet", "even if you don't match",
    "even if you do not match", "don't meet every", "don't meet all",
    "don't tick every", "don't check every", "no candidate will meet",
    "encourage you to apply", "we encourage applicants", "still encourage you",
  ]

  ## Posts that are clearly not job adverts, even when they mention roles
  ## and places. (Kept from the old version's three blacklist patterns.)
  notAJob* = [
    # schools/universities advertising themselves, sales, misc
    "القسط السنوي", "الكادر التدريسي", "تطوير مهارات الحاسوب",
    "تطوير مهارات الكمبيوتر", "عروض نهايه السنه", "تخفيضات حصريه",
    "اسئله اختبار شركه", "network of experts", "شبكه الخبراء",
    "محتاجه خط", "محتاج خط", "سجل الان في الدوره", "سجل الان في الكورس",
    "رسوم الدوره", "رسوم الكورس", "اجور الدوره", "سعر الدوره", "course fee",
    "انطلاق الدوره", "موعد الدوره", "مقاعد محدوده",
    # training *courses* and learning platforms (you pay or study, nobody hires)
    "الدوره الدوليه", "عن اطلاق البرنامج", "منصه تعليميه", "شهاده انجاز",
    "شهاده مشاركه", "اعلان دوره", "دوره مكثفه", "الدوره المكثفه",
    "رسوم التسجيل", "registration fee", "course fees", "job simulation", "job simulations", "virtual experience program",
  ]

  ## Signs of a course or training the reader registers on or pays for
  ## (false positive 2026-11-23: a training centre's paid course "for
  ## engineering graduates, discount for new graduates" was published).
  ## One sign proves nothing ("سيخضع لدورة تدريبية" is a job that trains
  ## you); `courseAdvert` in classify.nim needs three of the five kinds,
  ## one of them a fee or a training centre, and no hiring or pay
  ## (`courseHiring`). These five lists and `courseFree*` are whole words
  ## ("رسوم" is not "رسومات"); an Arabic prefix (و ف ب ل ك ال...) is allowed.
  ## Phrases already in `notAJob` ("اجور الدوره", "شهاده مشاركه") aren't
  ## repeated here: that check runs first.
  courseRegistration* = [
    "فتح باب التسجيل", "باب التسجيل", "للتسجيل", "سجل الان", "سارع بالتسجيل",
    "التسجيل في الدوره", "التسجيل في البرنامج", "التسجيل في الكورس",
    "رابط التسجيل", "التسجيل مفتوح", "register now", "registration is open",
    "registration open", "registrations are open", "registrations open",
    "enroll now", "enrol now", "sign up now",
  ]
  ## Not "مجاناً"/"free": a free course has no fee.
  courseFees* = [
    "خصم", "خصومات", "رسوم", "سعر", "اجور الكورس",
    "اجور الاشتراك", "اجور التسجيل", "قسط الدوره", "تكلفه الدوره",
    "discount", "fee", "fees", "price", "tuition",
  ]
  ## The word right before a fee word that makes it no fee ("بدون رسوم",
  ## "no fees"): that fee word doesn't count.
  courseFreeBefore* = [
    "بدون", "بلا", "دون", "لا", "مجانا", "مجاني", "مجانيه", "no", "without",
    "free", "zero",
  ]
  ## The word right after a fee word that makes the whole post free
  ## ("الرسوم: مجاناً", "fee: free"): no fee word counts.
  courseFreeAfter* = [
    "مجانا", "مجاني", "مجانيه", "بالمجان", "free", "none", "zero", "لا يوجد",
    "لا توجد",
  ]
  courseStructure* = [
    "محاور", "مده الدوره", "مده البرنامج", "مده الكورس", "ايام تدريبيه",
    "يوم تدريبي", "ساعات تدريبيه", "ساعه تدريبيه",
    "شهاده معتمده", "شهاده حضور", "الفئه المستهدفه", "course outline",
    "course content", "course contents", "course duration", "training hours",
    "training days", "certificate of completion", "certificate of attendance",
    "accredited certificate", "target audience",
  ]
  ## Only centres that say "training": a "Software Development Center" is
  ## an employer.
  courseProviders* = [
    "مركز تدريب", "مركز التدريب", "مركز للتدريب", "معهد تدريب", "اكاديميه",
    "training center", "training centre", "training institute", "academy",
  ]
  courseWords* = [
    "دوره", "دورات", "كورس", "كورسات", "ورشه عمل", "ورش عمل", "ورشه تدريبيه",
    "برنامج تدريبي", "البرنامج التدريبي", "course", "courses",
    "training workshop", "bootcamp", "boot camp",
  ]
  ## Hiring, pay or an employer's wording: with one of these, the training
  ## is part of a job or a paid or hiring traineeship, not a course advert.
  ## Unlike the lists above, an Arabic entry here matches the start of a
  ## word, so it covers the forms built on it: "راتب" also matches
  ## "راتباً"/"راتبه", "تعيين" "تعيينهم", "وظيف" "وظيفتك"/"الوظيفه". English
  ## entries are whole words. Not a bare "paid"/"مدفوعه": that is also "a
  ## paid course". Hashtags and channel signature lines are left out
  ## before this list is searched (`courseSignature`): channels tag courses
  ## "#وظائف" too.
  courseHiring* = [
    # pay
    "راتب", "رواتب", "مكافا", "منحه", "منح شهريه", "مخصصات", "اجر شهري",
    "مدفوع الاجر", "مدفوعه الاجر", "تدريب مدفوع", "تدريبي مدفوع",
    "تدريب باجر", "تدريبي باجر",
    # hiring
    "تعيين", "توظيف", "وظيف", "وظائف", "فرصه عمل", "فرص عمل", "شاغر", "شواغر",
    "عقد عمل", "ضمهم", "ضمه", "سيتم ضم", "للعمل",
    "تدريب صيفي", "تدريب الصيفي",
    # an employer's wording
    "مطلوب", "حاجه الى", "حاجتها الى", "حاجته الى", "حاجتنا الى",
    "حاجتهم الى", "نبحث عن", "تبحث عن", "يبحث عن", "يعلن عن حاجته",
    "تعلن عن حاجتها",
    "salary", "salaries", "stipend", "stipends", "allowance", "paid internship",
    "paid training", "paid traineeship", "hiring", "we are hiring", "hire",
    "hired", "employment", "employed", "job offer", "job opening", "vacancy",
    "vacancies", "internship", "internships", "summer training",
    "summer internship", "recruitment", "looking for", "join our team",
    "join the team", "join our", "wanted", "your cv",
  ]
  ## A line with one of these is the channel's signature ("تابعونا على قناة
  ## وظائف البصرة"), not the advert: left out when looking for hiring words.
  courseSignature* = [
    "تابعونا", "تابعوا", "تابعنا", "اشتركوا", "اشترك في قناه", "انضموا الى قناه",
    "قناتنا", "follow us", "follow our", "join our channel", "subscribe",
  ]

  ## Entry-level phrases added to your `level_keywords_*`. These are
  ## phrasings of "we want fresh graduates" that are not single keywords.
  entryPhrases* = [
    "graduated within", "recent graduate", "recent graduates",
    "graduate program", "graduate programme", "graduate development",
    "graduate trainee", "graduate engineer", "fresh grad", "fresh grads",
    "freshers", "no experience", "no prior experience", "entry level",
    "حديثي التخرج", "حديث التخرج", "الخريجين الجدد", "خريجين جدد",
    "خريج حديث", "خريجه حديثه", "بدون خبره", "لا يشترط الخبره",
    "لا يشترط وجود خبره",
  ]

  ## Job titles that were hard-coded in the old Python version
  ## (`mid_level_special_pattern`), kept so behaviour does not regress.
  builtinTitles* = ["control engineer"]

  ## A role match that is part of one of these phrases is a different job.
  roleExclusions* = [
    "cost control engineer", "estimation and cost control engineer",
    "document control", "quality control", "project control",
    "production control", "inventory control", "access control",
  ]

  ## Lines that name the job. A seniority word here ("IT Support Lead",
  ## "Senior ...") makes the whole job senior even if your role keyword
  ## also appears, unseniored, further down in the responsibilities.
  titleMarkers* = [
    "position", "job title", "role", "vacancy", "we are hiring", "is hiring",
    "hiring", "المسمى الوظيفي", "الوظيفه", "العنوان الوظيفي", "مطلوب",
  ]

  ## Phrases of someone looking for a job. They only count when the post
  ## does *not* also sound like an employer ("Looking for a job? We are
  ## hiring..." is an advert).
  seekerPhrases* = [
    "محتاج عمل", "محتاجه عمل", "محتاج وظيفه", "محتاجه وظيفه", "ابحث عن عمل",
    "ابحث عن وظيفه", "ابحث عن فرصه عمل", "باحث عن عمل", "باحث عن وظيفه",
    "ادور على شغل", "ادور شغل", "ادور عمل", "looking for a job",
    "seeking employment", "i am looking for", "seeking a position",
    "seeking a job", "seeking an opportunity", "looking for an opportunity",
    "looking for a position", "open to work", "cv available", "my cv",
    "اريد اشتغل", "اريد اعمل", "اريد عمل", "ابحث عن فرصه", "راسلوني",
    "اني مهندس", "انا مهندس", "اني خريج", "انا خريج", "انا خريجه",
    "i am a fresh graduate", "i'm a fresh graduate", "my name is", "اسمي",
  ]

  ## CV section headings. Only count as a line of their own ("WHO AM I?"):
  ## inside a sentence they're an employer's ("Who am I looking for?",
  ## "include a short career objective in your CV").
  cvHeadings* = [
    "who am i", "about me", "career objective", "personal information",
    "نبذه عني", "من انا", "المعلومات الشخصيه",
  ]

  ## Wording that limits a post to people who are still studying ("ان يكون
  ## احد طلبة المرحلة الثالثة"). Such a post is held for review unless it
  ## also mentions graduates (`graduateWords`).
  studentsOnlyPhrases* = [
    "احد طلبه", "احد طلاب", "طلبه المرحله", "طلاب المرحله", "طلبه المراحل",
    "لطلاب البكالوريوس", "لطلبه البكالوريوس", "ان يكون طالبا", "ان يكون طالب",
    "ان تكون طالبه", "طالب في المرحله", "طالبه في المرحله", "للطلبه فقط",
    "للطلاب فقط", "الطلبه الحاليين", "الطلاب الحاليين", "current students",
    "currently enrolled", "students only", "only for students", "open only to students",
    "final year students", "students in their final year",
    # not "university students" / "undergraduates": they also describe the
    # work ("help university students with their laptops")
  ]

  ## Graduate schemes, often named like a senior title ("LEAD",
  ## "Future Leaders"). Only looked for near the top of the post: "we offer a
  ## development program" further down doesn't make a senior job junior.
  programmeWords* = [
    "development program", "development programme", "graduate program",
    "graduate programme", "graduate scheme", "leadership program",
    "leadership programme", "leaders program", "leaders programme",
    "trainee program", "trainee programme", "internship program",
    "early careers", "برنامج تطوير", "برنامج الخريجين", "برنامج القاده",
  ]
  ## A bare "Program" counts only on the headline itself ("LEAD Program 2027");
  ## lower down it's "part of the expansion programme".
  headlineProgrammeWords* = ["program", "programme", "scheme", "برنامج"]

  ## Wording that means people who have already graduated. Not "graduate"
  ## or "التخرج" alone: "expected to graduate", "مشروع التخرج" are students.
  graduateWords* = [
    "خريج", "خريجه", "خريجين", "خريجي", "متخرج", "متخرجه", "متخرجين",
    "حديث التخرج", "حديثي التخرج", "حديثه التخرج", "graduates", "graduated",
    "fresh graduate", "fresh grads", "recent graduate", "new graduate", "alumni",
  ]

  ## An explicit welcome for fresh graduates. With one, a senior word on the
  ## headline is someone else's ("Senior Talent Acquisition Partner at KBR"
  ## posting it, "مكتب المدير العام" announcing it, a company called
  ## "Expert Solutions").
  freshGradWelcome* = [
    "fresh graduate", "fresh graduates", "fresh grads", "recent graduates",
    "new graduates", "no experience required", "حديث التخرج", "حديثي التخرج",
    "حديثه التخرج", "خريجين جدد", "الخريجين الجدد", "خريج جديد",
    "لا يشترط خبره", "لا تشترط خبره",
  ]

  ## Words that start a requirement. In a numbered item, a job noun after
  ## one of these is part of the requirement ("2- خبرة 7 سنوات كمهندس",
  ## "2. Minimum 5 years as a site engineer"), not another job.
  requirementWords* = [
    "خبره", "سنوات", "سنه", "سنين", "سنتين", "لا تقل", "لا يقل", "حاصل", "بكالوريوس",
    "شهاده", "القدره", "تحمل", "اجاده", "معرفه", "العمل مع", "experience", "years",
    "year", "minimum", "at least", "degree", "bachelor", "b sc", "bsc", "must", "ability",
    "knowledge", "proficiency", "work with", "working with", "reporting to", "report to",
  ]

  ## Phrases only an employer writes.
  employerPhrases* = [
    "we are hiring", "is hiring", "hiring", "we are looking for", "join our team",
    "تعلن", "حاجتها الى", "حاجتنا الى", "نبحث عن", "تبحث شركه", "مطلوب",
    "نعلن", "يعلن",
  ]

  ## Nouns that name *some* job. A numbered list item with one of these but
  ## none of your roles is another job ("2- محاسب"); a numbered item without
  ## any is a requirement ("2- خبرة 4 سنوات") and stays with the job above.
  jobNouns* = [
    "engineer", "technician", "developer", "programmer", "accountant",
    "officer", "specialist", "manager", "coordinator", "assistant",
    "supervisor", "operator", "driver", "designer", "analyst", "administrator",
    "representative", "agent", "clerk", "secretary", "teacher", "nurse",
    "doctor", "pharmacist", "lawyer", "translator", "cashier", "sales",
    "مهندس", "مهندسه", "مهندسين", "فني", "فنيين", "مبرمج", "مبرمجين", "محاسب",
    "محاسبين", "موظف", "موظفه", "موظفين", "مسؤول", "منسق", "مساعد", "مشرف",
    "مدير", "سائق", "مصمم", "محلل", "مندوب", "سكرتير", "سكرتيره", "مدرس",
    "مدرسه", "معلم", "معلمه", "ممرض", "طبيب", "صيدلاني", "مترجم", "كاشير",
    "عامل", "عمال", "حارس", "مصور", "اعلامي", "اداري", "اداريه",
  ]
