import std/unittest
import telenotify/[normalize, experience]

proc years(s: string): int = parseExperience(normalizeText(s)).requiredYears
proc preferred(s: string): int = parseExperience(normalizeText(s)).preferredYears
proc friendly(s: string): bool = parseExperience(normalizeText(s)).entryFriendly

suite "experience: phrasings the old regex missed (report 3.5)":
  test "English":
    check years("Experience: 5+ years") == 5
    check years("5 yrs experience in networking") == 5
    check years("two years of experience") == 2
    check years("3+ years in accounting experience") == 3
    check years("Minimum of 3 years of experience") == 3
    check years("At least 1.5 years of experience in IT") == 2

  test "heading on its own line":
    check years("Experience:\n3+ years") == 3

  test "Arabic":
    check years("ذو كفائه وخبرة عالية شرط لا تقل عن 10 سنوات") == 10
    check years("خبرة عملية مثبتة من 5 إلى 12 سنة") == 5
    check years("خبرة لا تقل عن ٣ سنوات") == 3
    check years("خبرة سنتين في المجال") == 2
    check years("خبرة خمس سنوات") == 5

suite "experience: ranges take the lower end":
  test "range lower bound":
    check years("Minimum 1-3 years of experience required") == 1
    check years("الخبرة العملية: سنة – خمس سنوات") == 1
    check years("الخبرة العملية: سنة خمس سنوات") == 1 # OCR lost the dash

suite "experience: entry friendly and optional":
  test "upper bounds and zero":
    check friendly("خريج جديد أو خبرة لا تتجاوز سنة واحدة")
    check friendly("0-1 years of experience")
    check friendly("No experience required")

  test "optional requirements are not required":
    check years("3 years of experience is preferred") == -1
    check preferred("3 years of experience is preferred") == 3
    check years("يفضل خبرة 3 سنوات") == -1

  test "required wins over preferably on the same line":
    check years("3-5 years of experience in IT required, preferably with ITIL") == 3

  test "'preferably' after the years qualifies what follows, not the years":
    # the Eni "Clinic Administration Analyst" post that reached review
    check years("- Minimum 7 years’ relevant experience, preferably in the oil and gas industry and a multinational environment.") == 7
    check years("5 years of experience, preferably in banking") == 5
    check years("خبرة لا تقل عن 3 سنوات ويفضل في الشركات النفطية") == 3
    check years("خبرة 4 سنوات ويفضل في مجال النفط") == 4

  test "review of the fix: a wrapped sentence and other forward qualifiers":
    check years("- Minimum 7 years' relevant experience,\npreferably in the oil and gas industry.") == 7
    check years("خبرة لا تقل عن 3 سنوات\nويفضل في الشركات النفطية") == 3
    check years("خبرة 3 سنوات مع أفضلية لمن عمل في الشركات النفطية") == 3
    check years("5 years experience, preferred in oil companies") == 5

  test "adversarial review: these stay optional (main got them right)":
    for s in ["Minimum 3 years of experience preferred",
              "At least 2 years of experience is preferred",
              "A minimum of 2 years experience is a plus",
              "خبرة لا تقل عن سنتين تعتبر ميزة",
              "خبرة لا تقل عن سنتين (يفضل)",
              "We need an accountant with 2 years of experience preferably. Send your CV to hr@x.com",
              "2 years experience preferably, fresh graduates are welcome",
              "خبرة سنتين يفضل ويمكن قبول الخريجين الجدد",
              "3 years experience will be a big plus",
              "3+ years experience would be an added plus",
              "Experience 2 years is plus",
              "3 years experience plus",
              "2 years of experience is preferred for this position",
              "2 years of experience is preferred in this role",
              "3 years of experience would be an advantage in this role",
              "Preferred: Master's degree, 3 years experience",
              "Nice to have: Docker, Kubernetes, 2+ years of experience in DevOps",
              "Preferably M.Sc. holder with 3 years of experience",
              "Experience (preferred): 3 years",
              "experience 3 years\n- preferred",
              "Preferably in oil companies, 3 years experience",
              "Linux admin 3 years experience is a plus"]:
      checkpoint s
      check years(s) == -1

  test "optional markers that still make the years optional":
    check years("Preferably 3 years of experience") == -1
    check preferred("Preferably 3 years of experience") == 3
    check years("3 years of experience, preferably.") == -1
    check years("3 years of experience in sales, ideally in retail, is a plus") == -1
    check years("خبرة 3 سنوات مفضلة") == -1
    check years("Preferably a minimum of 3 years of experience") == -1

  test "durations that are not experience":
    check years("Program Duration: 3.5 Years\nGraduated within the last 12 months") == -1
    check years("Age: 25 years old, experience in sales") == -1

suite "review pass 1 findings":
  test "a list number is not the low end of a range":
    check years("Requirements:\n1- 5+ years of experience in substations") == 5
    check years("1. 3 years of experience") == 3

  test "'not experience' words need whole words":
    check years("5 years of programming experience") == 5
    check years("BSc holder with 6 years experience") == 6
    check years("experience with contractors for 4 years") == 4

  test "عامل (worker) is not عام (year)":
    check years("خبرة في ادارة 20 عامل") == -1

  test "less than N years is entry friendly, not a requirement":
    check friendly("less than 2 years of experience")
    check years("less than 2 years of experience") == -1
