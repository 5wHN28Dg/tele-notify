import std/unittest
import telenotify/[normalize, keywords]

suite "normalizeText":
  test "folds Arabic letter variants, digits and case":
    check normalizeText("أحمد إبراهيم آمنة") == "احمد ابراهيم امنه"
    check normalizeText("خبرة ٣ سنوات") == "خبره 3 سنوات"
    check normalizeText("Senior ENGINEER") == "senior engineer"
    check normalizeText("مستوى") == "مستوي"

  test "drops diacritics, tatweel and invisible characters":
    check normalizeText("مُهَنْدِس") == "مهندس"
    check normalizeText("مهنـــدس") == "مهندس"
    check normalizeText("linkedin.com/jobs/1‏") == "linkedin.com/jobs/1"

  test "squeezes spaces but keeps newlines":
    check normalizeText("a  \t b\nc") == "a b\nc"

  test "flatten turns URL and hashtag separators into spaces":
    check flatten("jobs/electrical-engineer-34/") == "jobs electrical engineer 34 "
    check flatten("#مهندس_كهرباء") == " مهندس كهرباء"

  test "runeSafe never cuts a character in half":
    let s = "مهندس"
    check runeSafe(s, 1, 3) == "مه"

suite "keyword matching":
  let en = toKeywords(["intern", "electrical engineer"])
  let ar = toKeywords(["البصرة", "مبرمج"])

  test "English keywords need whole words":
    check "summer intern wanted".flatten.contains(en)
    check not "internal audit".contains(en)
    check "jobs electrical engineer 34".contains(en)

  test "Arabic keywords allow prefixes and suffixes":
    check normalizeText("للعمل بالبصرة").flatten.contains(ar)
    check normalizeText("مطلوب مبرمجين").flatten.contains(ar)
    check not normalizeText("شركتهالبصره").contains(ar)

  test "strict Arabic keywords must end at a word end":
    let strict = toKeywords(["اول"], strict = true)
    check normalizeText("مهندس اول").contains(strict)
    check not normalizeText("شارع اللجنة الاولمبية").contains(strict)

  test "loose keywords tolerate OCR-glued words":
    let certs = toKeywords(["CCNA"], loose = true)
    check normalizeText("CCNAor MTCNA required").contains(certs)
