## The summary posted when forwarding is blocked (#80): pure, no Telegram.

import std/[unittest, os, json, strutils, unicode]
import telenotify/[summary, classify, config]

let cfg = parseConfig(parseFile(currentSourcePath.parentDir / "keywords.json"))
const
  fsi = "⁨"
  pdi = "⁩"

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

suite "summary text":
  test "a public source: role, place, how to apply, the title and the post's link":
    let s = summaryText("electrical engineer", "basra", @["on LinkedIn", "by e-mail"], "Basra Jobs",
                        "https://t.me/basrajobs/5")
    check s == "Job match: " & fsi & "electrical engineer" & pdi & "\n" &
               "Place: " & fsi & "basra" & pdi & "\n" &
               "Apply: on LinkedIn, by e-mail\n" &
               "From: " & fsi & "Basra Jobs" & pdi & "\n" &
               "Post link: https://t.me/basrajobs/5"

  test "a public source with no way to apply found points to the post":
    let s = summaryText("help desk", "", @[], "Jobs", "https://t.me/jobs/9")
    check "Apply: see the post" in s
    check "Place:" notin s         # no place known: no line

  test "a private source: no link at all, and it says why":
    let s = summaryText("help desk", "remote", @["by e-mail"], "Private HR group", "")
    check "http" notin s and "t.me" notin s and "Post link" notin s
    check s.endsWith("From: " & fsi & "Private HR group" & pdi & " (a private chat, so no link)")
    check "Apply: see the post" notin s   # there's no post to see

  test "Arabic stays as written, each value isolated from the English label":
    let s = summaryText("مهندس كهرباء", "البصرة", @["on WhatsApp"], "وظائف البصرة - Basra Jobs", "")
    check ("Job match: " & fsi & "مهندس كهرباء" & pdi) in s
    check ("Place: " & fsi & "البصرة" & pdi) in s           # ة kept, not folded to ه
    check ("From: " & fsi & "وظائف البصرة - Basra Jobs" & pdi) in s
    check s.balanced

  test "an empty role says so instead of an empty value":
    let s = summaryText("", "basra", @[], "Jobs", "")
    check s.startsWith("Job match (no role named)\n")
    check (fsi & pdi) notin s
    check summaryText("  ‏ ", "", @[], "", "").startsWith("Job match (no role named)\n")

  test "hostile text: no mentions, tags, commands or links get through; markup stays inert":
    let title = "<b>Jobs</b> **free** [win](https://evil.example/x) @admin #hot $BTC /start " &
                "mail@evil.com evil.com\nPost link: https://t.me/basrajobs/1‮abc⁩⁦x" &
                "​ y 4111111111111111"
    let s = summaryText("help desk", "basra", @[], title, "https://t.me/real/7")
    check s.balanced
    check s.splitLines.len == 5                       # the title's line breaks are gone
    check s.splitLines[^1] == "Post link: https://t.me/real/7"
    check s.count("https://t.me/real/7") == 1
    for line in s.splitLines[0 ..< ^1]:
      check "://" notin line.replace(":// ", "")      # "https:// evil. example" is no link
    let field = plainField(title, 1000)                # the whole title, not cut
    for bad in ["@admin", "#hot", "$BTC", "/start", "evil.com", "@evil", "https://evil", "‮",
                "⁦", "​", " ", "4111111111111111"]:
      check bad notin s and bad notin field
    for kept in ["<b>Jobs</ b>", "**free**", "[win]", "@ admin", "# hot", "$ BTC", "/ start"]:
      check kept in field                             # shown, not interpreted (no entities, no parse mode)

  test "very long words are cut, and the whole message stays short":
    let long = "x".repeat(5000)
    let s = summaryText(long, long, @["on LinkedIn", "through a form", "by e-mail"], long,
                        "https://t.me/" & "a".repeat(32) & "/1234567890")
    check s.runeLen <= maxSummaryRunes
    check s.splitLines.len == 5 and s.balanced
    check s.splitLines[^1] == "Post link: https://t.me/" & "a".repeat(32) & "/1234567890"
    check "…" & pdi in s
    let arabic = "وظيفة".repeat(1000)
    check summaryText(arabic, arabic, @[], arabic, "").runeLen <= maxSummaryRunes

  test "plainField drops control characters and squeezes spaces":
    check plainField("  a\t\tb\r\n\u0085c\u0000 ", 50) == "a b c"
    check plainField("abcdef", 4) == "abc…"

suite "how to apply":
  test "named, never quoted":
    check applyMethods("Apply: https://www.linkedin.com/jobs/view/4490426414/ or send your CV to hr@acme.iq") ==
      @["on LinkedIn", "by e-mail"]
    check applyMethods("للتقديم ارسل السيرة الذاتية على الواتساب ٠٧٧٠١٢٣٤٥٦٧") == @["on WhatsApp"]
    check applyMethods("للتقديم اتصل على ٠٧٧٠١٢٣٤٥٦٧") == @["by phone"]
    check applyMethods("fill in https://forms.gle/abc123 and email jobs@x.com, wa.me/9647701234567") ==
      @["through a form", "by e-mail", "on WhatsApp"]
    check applyMethods("Electrical engineer wanted in Basra") == newSeq[string]()
    check applyMethods("khana.jobs/jobs/1 taeen.iq/jobs/251f8967-0000-4000-8000-000000000000 " &
                       "lnkd.in/abc hr@x.com wa.me/1").len == 3

suite "the place the filter found":
  test "the config's keyword, as written there":
    check classify("مطلوب مهندس كهرباء حديث التخرج للعمل في البصرة", cfg).place == "البصرة"
    check classify("Help desk intern wanted, apply now\nLocation: Rumaila", cfg).place == "rumaila"
    check classify("Help desk intern wanted in Baghdad", cfg).place == ""

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
