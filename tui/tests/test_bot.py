from telenotify_tui import bot


def test_parse_check():
    v = bot.parse_check("note: x\nverdict: review\n  - role (rkTitle): it support\n  - no sign of level\n")
    assert v.verdict == "review" and v.reasons == ["role (rkTitle): it support", "no sign of level"]


def test_parse_check_with_text():
    out = "----- text the filter reads -----\nhello\nworld\n---------------------------------\nverdict: reject\n  - no job\n"
    v = bot.parse_check(out)
    assert v.text == "hello\nworld" and v.verdict == "reject"


def test_parse_eval():
    out = """196 posts
  TRUE    publish:   5  review:   9  reject:   0
  UNSURE  publish:   2  review:   7  reject:   8
  FALSE   publish:   0  review:  43  reject: 122
published: 7, of which wrong: 0 (precision on labelled: 100%)
true matches published: 5/14, sent to review: 9, lost: 0
"""
    r = bot.parse_eval(out)
    assert r.rows["FALSE"] == (0, 43, 122) and r.wrong_published == 0 and r.true_lost == 0
    assert len(r.summary) == 2


async def test_check_with_the_real_filter(binary, config):
    v = await bot.check(binary, config, "مطلوب مهندس كهرباء حديث التخرج للعمل في البصرة")
    assert v.verdict == "publish", v


async def test_missing_program_is_an_error_not_a_crash(tmp_path, config):
    v = await bot.check(tmp_path / "missing", config, "x")
    assert v.verdict == "" and v.error
