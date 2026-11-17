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

  test "durations that are not experience":
    check years("Program Duration: 3.5 Years\nGraduated within the last 12 months") == -1
    check years("Age: 25 years old, experience in sales") == -1
