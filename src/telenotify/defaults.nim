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
    # CV section headings ("about me" isn't one: adverts ask for an
    # "About me" paragraph)
    "who am i", "career objective",
  ]

  ## Wording that limits a post to people who are still studying ("ان يكون
  ## احد طلبة المرحلة الثالثة"). Such a post is held for review unless it
  ## also mentions graduates (`graduateWords`).
  studentsOnlyPhrases* = [
    "احد طلبه", "احد طلاب", "طلبه المرحله", "طلاب المرحله", "طلبه المراحل",
    "لطلاب البكالوريوس", "لطلبه البكالوريوس", "ان يكون طالبا", "ان يكون طالب",
    "ان تكون طالبه", "طالب في المرحله", "طالبه في المرحله", "طلبه الجامعات",
    "الطلبه الحاليين", "الطلاب الحاليين", "current students", "currently enrolled",
    "enrolled students", "students only", "only for students", "university students",
    "final year students", "undergraduate students", "undergraduates",
  ]

  ## Graduate schemes, often named like a senior title ("LEAD",
  ## "Future Leaders"). Only looked for near the top of the post: "we offer a
  ## development program" further down doesn't make a senior job junior.
  programmeWords* = [
    "program", "programme", "scheme", "برنامج",
  ]

  ## Wording that means people who have already graduated. Not "graduate"
  ## or "التخرج" alone: "expected to graduate", "مشروع التخرج" are students.
  graduateWords* = [
    "خريج", "خريجه", "خريجين", "خريجي", "حديث التخرج", "حديثي التخرج",
    "حديثه التخرج", "graduates", "fresh graduate", "recent graduate",
    "new graduate", "alumni",
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
