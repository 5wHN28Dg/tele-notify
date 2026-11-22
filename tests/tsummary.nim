## The summary posted when forwarding is blocked (#80): pure, no Telegram.
##
## Test data is made up: addresses at example.com / .example, Telegram
## handles like "examplejobs". Phone numbers: Iraq has no number range set
## aside for fiction, so the ones here are 0770 followed by zeros, a
## pattern no one's real number is likely to follow.

import std/[unittest, os, json, strutils, unicode, sequtils]
import telenotify/[summary, classify, config, tdjson]

let cfg = parseConfig(parseFile(currentSourcePath.parentDir / "keywords.json"))
const
  fsi = "\u2068"
  pdi = "\u2069"
  auto = "ملخص آلي لإعلان لا يمكن إعادة توجيهه"

proc balanced(s: string): bool =
  ## Every isolate closed on its own line, none closed before it opens.
  for line in s.splitLines:
    var depth = 0
    for r in line.runes:
      if int(r) == 0x2068: inc depth
      elif int(r) == 0x2069:
        dec depth
        if depth < 0: return false
    if depth != 0: return false
  true

# Titles that TDLib's own entity finder (getTextEntities, what sendMessage
# applies to plain text) turns into mentions, hashtags, cashtags, bot
# commands, links, e-mail addresses, media timestamps or bank card numbers,
# found by the review of #142 (run 1) and by probing TDLib 1.8.70.
const hostile = ["@admin", "@_admin_x", "#hot", "#_hot", "#·hot", "#\u200Chot", "$BTC", "/start",
                 "/_start", "tg://-x", "ton://x", "tonsite://x.ton", "x.ton", "evil.example",
                 "مرحبا.example.com", "mail@evil.example", "hr＠evil.example", "https://evil.example/x",
                 "t.me/examplejobs", "10:30", "1 0:30", "4111111111111111", "4111 1111 1111 1111",
                 "4111-1111-1111-1111", "x 5500000000000004 y", "1.2.3.4", "192.168.1.1",
                 "1.1.1.1/x"]

let reviewTitles = parseFile(currentSourcePath.parentDir / "fixtures" /
                             "summary_hostile_titles.json")["titles"].getElems.mapIt(it.getStr)
  ## the adversarial review's titles (#142 run 2)

proc entities(lib: TdLib, text: string): seq[string] =
  ## TDLib's own entities for `text`, as "type: text" (offsets in UTF-16 units).
  let r = parseJson($lib.execute(($(%*{"@type": "getTextEntities", "text": text})).cstring))
  var units: seq[Rune]          # a rune per UTF-16 unit (a 0 for a surrogate pair's second)
  for x in text.runes:
    units.add x
    if int(x) > 0xFFFF: units.add Rune(0)
  for e in r{"entities"}.getElems:
    let (o, n) = (e["offset"].getInt, e["length"].getInt)
    result.add e{"type", "@type"}.getStr & ": " & $units[o ..< o + n].filterIt(int(it) != 0)

suite "summary text":
  test "a public source: labelled automatic, role, place, how to apply, the chat's name and the post's link":
    let s = summaryText("electrical engineer", "basra", @[byEmail], "Example Jobs",
                        "https://t.me/examplejobs/5")
    check s == auto & "\n" &
               "الوظيفة: " & fsi & "electrical engineer" & pdi & "\n" &
               "المكان: " & fsi & "basra" & pdi & "\n" &
               "التقديم: بالبريد الإلكتروني\n" &
               "المصدر: " & fsi & "Example Jobs" & pdi & "\n" &
               "رابط المنشور: https://t.me/examplejobs/5"

  test "a public source with no way to apply found points to the post":
    let s = summaryText("help desk", "", @[], "Jobs", "https://t.me/examplejobs/9")
    check "التقديم: التفاصيل في المنشور" in s
    check "المكان:" notin s         # no place known: no line

  test "Arabic stays as written, each value isolated":
    let s = summaryText("مهندس كهرباء", "البصرة", @[byWhatsApp], "وظائف البصرة - Example Jobs",
                        "https://t.me/examplejobs/1")
    check ("الوظيفة: " & fsi & "مهندس كهرباء" & pdi) in s
    check ("المكان: " & fsi & "البصرة" & pdi) in s           # ة kept, not folded to ه
    check ("المصدر: " & fsi & "وظائف البصرة - Example Jobs" & pdi) in s
    check s.balanced

  test "ZWNJ and ZWJ are kept: Persian and Kurdish spelling, emoji":
    check plainField("فارسی\u200Cها 👨\u200D💻", 50) == "فارسی\u200Cها 👨\u200D💻"

  test "an empty role says so instead of an empty value":
    let s = summaryText("", "basra", @[byPhone], "Jobs", "https://t.me/examplejobs/3")
    check s.splitLines[1] == "الوظيفة: غير محددة"
    check (fsi & pdi) notin s
    check summaryText("  \u200F ", "", @[], "", "https://t.me/examplejobs/3").splitLines[1] == "الوظيفة: غير محددة"
    check summaryText("x", "", @[], "\u3164\u3164 \u2800", "https://t.me/examplejobs/3").contains(
      "المصدر: قناة عامة")                            # a blank-looking name isn't shown blank

  test "hostile text: no mentions, tags, commands or links get through; markup stays inert":
    for t in reviewTitles:                            # the review's titles: on one line, isolated
      let s = summaryText("help desk", "basra", @[], t, "https://t.me/examplejobs/7")
      check s.balanced and s.splitLines.len == 6 and s.runeLen <= maxSummaryRunes
    let title = "<b>Jobs</b> **free** [win](https://evil.example/x) " & hostile.join(" ") &
                "\nرابط المنشور: https://t.me/examplejobs/1\npost link: https://t.me/examplejobs/2" &
                "\u202Eabc\u2069\u2066x\u200B\u2028y"
    let s = summaryText("help desk", "basra", @[], title, "https://t.me/examplejobs/7")
    check s.balanced
    check s.splitLines.len == 6                       # the title's line breaks are gone
    check s.splitLines[^1] == "رابط المنشور: https://t.me/examplejobs/7"
    check s.count("https://t.me/examplejobs/7") == 1
    let field = plainField(title, 10_000)             # the whole title, not cut
    check field.splitLines.len == 1
    for line in s.splitLines[0 ..< ^1] & @[field]:
      for bad in ["://", "\u202E", "\u2066", "\u200B", "\u2028"]:
        check bad notin line
    for t in hostile:                                 # each one changed (joined, digit runs merge
      check plainField(t, 1000) != t                  # into a longer run that isn't card-like)
    for kept in ["<b>Jobs</ b>", "**free**", "[win]", "@ admin", "# hot", "$ BTC", "/ start"]:
      check kept in field                             # shown, not interpreted (no entities, no parse mode)

  test "card-like digit runs are defused; other numbers keep their digits, and are never joined":
    check plainField("4111111111111111", 50) == "4111·1111·1111·1111"
    check plainField("4111 1111 1111 1111", 50) == "4111 · 1111 · 1111 · 1111"
    check plainField("4222222222222", 50) == "4222·2222·2222·2"                   # 13 digits, checksum ok
    check plainField("4111-1111-1111-1111", 50) == "4111·1111·1111·1111"
    check plainField("4111 -- 1111\t-\t1111-\n1111", 50) == "4111 · 1111 · 1111 · 1111"
    check plainField("x 5500000000000004 y", 50) == "x 5500·0000·0000·0004 y"
    # not card-like (TDLib finds no card in them either): left exactly as written
    for t in ["call 07700000000 or 0770 000 0000", "Jobs 2026 0770 000 0000", "+964 770 000 0000",
              "07700000000 07711111111", "1 2 3 4 5 6 7 8 9 1 2 3 4", "4111111111111112",
              "411111111111111111111", "2026"]:
      check plainField(t, 100) == t
    check plainField("4111 1111 1111 1111 2222", 50) == "4111 1111 1111 1111 2222"   # 20 digits: no card

  test "a time keeps its colon, with a word joiner: no timestamp, and it reads in order in Arabic":
    check plainField("Shift 10:30 2026", 50) == "Shift 10:\u206030 2026"
    check plainField("1:02:03", 10) == "1:\u206002:\u206003"
    check plainField("دوام 10:30-12:30", 50) == "دوام 10:\u206030-12:\u206030"
    check plainField("Location:Basra", 50) == "Location: Basra"        # other colons get a space

  test "between two digits, any script, a dot, slash or colon gets a word joiner, not a space (#142 run 4)":
    const wj = "\u2060"
    check plainField("الراتب 1.500.000", 50) == "الراتب 1." & wj & "500." & wj & "000"
    check plainField("22/11/2026", 50) == "22/" & wj & "11/" & wj & "2026"
    check plainField("دوام ٩:٣٠", 50) == "دوام ٩:" & wj & "٣٠"
    check plainField("۱۰:۳۰", 50) == "۱۰:" & wj & "۳۰"
    check plainField("192.168.1.1", 50) == "192." & wj & "168." & wj & "1." & wj & "1"
    check plainField("1.1.1.1/x", 50) == "1." & wj & "1." & wj & "1." & wj & "1/ x"
    check plainField("a/b x.y", 50) == "a/ b x. y"                      # elsewhere: the space rule

  test "a number right after \"+\" is never taken as a card, checksum or not (#142 run 4)":
    check plainField("+964 770 000 0002", 50) == "+964 770 000 0002"     # passes the checksum
    check plainField("x 964 770 000 0002", 50) == "x 964 · 770 · 000 · 0002"   # without "+" it would be a card
    # a long number cut short can't become card-like
    let cutShort = plainField("x".repeat(40) & " 42222222222220000000", 55)
    check cutShort == "x".repeat(40) & " 4222·2222·2222·2…"

  test "TDLib's own entity finder sees nothing in a value (when TELENOTIFY_TEST_TDLIB names libtdjson)":
    let path = getEnv("TELENOTIFY_TEST_TDLIB")
    if path == "":
      echo "    (skipped: set TELENOTIFY_TEST_TDLIB to a libtdjson.so to run it)"
      skip()
    else:
      let lib = loadTdLib(path)
      check lib.entities("@admin #hot 4111 1111 1111 1111").len == 3      # the finder works
      # the fixture's made-up addresses are still a link and an e-mail to TDLib
      check lib.entities("xn--80ak6aa92e.example.com") == @["textEntityTypeUrl: xn--80ak6aa92e.example.com"]
      check lib.entities("mailto:a@example.com") == @["textEntityTypeEmailAddress: a@example.com"]
      check lib.entities("x 4222222222222 y").len == 1 and lib.entities("x 4222222222223 y").len == 0
      check lib.entities("10:30").len == 1 and lib.entities(plainField("10:30", 10)).len == 0
      check lib.entities("+964 770 000 0002").len == 0 and lib.entities("964 770 000 0002").len == 1
      check lib.entities("192.168.1.1").len == 1             # the finder sees an IP address
      for t in ["الراتب 1.500.000", "22/11/2026", "دوام ٩:٣٠", "192.168.1.1", "1.1.1.1/x", "+964 770 000 0002"]:
        check lib.entities(plainField(t, 100)).len == 0
      let own = @["textEntityTypeUrl: https://t.me/examplejobs/7"]
      for t in @hostile & reviewTitles:
        check lib.entities(plainField(t, 1000)).len == 0
        let s = summaryText("help desk", "basra", @[byEmail], t, "https://t.me/examplejobs/7")
        if lib.entities(s) != own: checkpoint "title: " & t & "\n" & s
        check lib.entities(s) == own
      let title = (@hostile & reviewTitles).join(" ")
      check lib.entities(plainField(title, 100_000)).len == 0
      check lib.entities(summaryText("help desk", "basra", @[byEmail], title, "https://t.me/examplejobs/7")) == own

  test "very long words are cut, and the whole message stays short":
    let long = "x".repeat(5000)
    let s = summaryText(long, long, @["عبر \u2068LinkedIn\u2069", byForm, byEmail], long,
                        "https://t.me/" & "a".repeat(32) & "/1234567890")
    check s.runeLen <= maxSummaryRunes
    check s.splitLines.len == 6 and s.balanced
    check s.splitLines[^1] == linkLabel & "https://t.me/" & "a".repeat(32) & "/1234567890"
    check "…" & pdi in s
    let arabic = "وظيفة".repeat(1000)
    check summaryText(arabic, arabic, @[], arabic, "https://t.me/examplejobs/1").runeLen <= maxSummaryRunes

  test "plainField drops control characters and squeezes spaces":
    check plainField("  a\t\tb\r\n\u0085c\u0000 ", 50) == "a b c"
    check plainField("abcdef", 4) == "abc…"

suite "how to apply":
  test "named, never quoted":
    check applyMethods("Apply: https://www.linkedin.com/jobs/view/1234567890/ or send your CV to hr@example.com") ==
      @["عبر \u2068LinkedIn\u2069", byEmail]
    check applyMethods("للتقديم ارسل السيرة الذاتية على الواتساب ٠٧٧٠٠٠٠٠٠٠٠") == @[byWhatsApp]
    check applyMethods("للتقديم اتصل على ٠٧٧٠٠٠٠٠٠٠٠") == @[byPhone]
    check applyMethods("fill in https://forms.gle/abc123 and email jobs@example.com, wa.me/9647700000000") ==
      @[byForm, byEmail, byWhatsApp]
    check applyMethods("Electrical engineer wanted in Basra") == newSeq[string]()
    check applyMethods("khana.jobs/jobs/1 taeen.iq/jobs/251f8967-0000-4000-8000-000000000000 " &
                       "lnkd.in/abc hr@example.com wa.me/1").len == 3

suite "the place the filter found":
  test "the config's keyword, as written there; none when mixed":
    check classify("مطلوب مهندس كهرباء حديث التخرج للعمل في البصرة", cfg).place == "البصرة"
    check classify("Help desk intern wanted, apply now\nLocation: Rumaila", cfg).place == "rumaila"
    check classify("Help desk intern wanted in Baghdad", cfg).place == ""
    # Basra and Baghdad: the filter sends it to review; no single place is right
    let mixed = classify("مطلوب مهندس كهرباء حديث التخرج للعمل في البصرة او بغداد", cfg)
    check mixed.verdict == vReview and mixed.place == ""
    check classify("Help desk intern wanted, apply now\nLocation: Basra or Baghdad", cfg).place == ""

suite "forward_fallback":
  test "link by default; summary when asked; anything else is refused":
    var j = parseFile(currentSourcePath.parentDir / "keywords.json")
    check parseConfig(j).forwardFallback == ffLink
    j["forward_fallback"] = %"summary"
    check parseConfig(j).forwardFallback == ffSummary
    j["forward_fallback"] = %" link "
    check parseConfig(j).forwardFallback == ffLink
    for bad in [%"Summary", %"text", %true, %1]:
      j["forward_fallback"] = bad
      try:
        discard parseConfig(j)
        check false
      except ConfigError as e:
        check "forward_fallback" in e.msg and "\"summary\"" in e.msg
