import asyncio
import os

from telenotify_tui import bot
from telenotify_tui.bot import (
    service_action as real_service_action,  # before the conftest fakes it
)


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


async def test_cancelled_run_kills_the_program(tmp_path):
    pidfile = tmp_path / "pid"
    task = asyncio.create_task(bot.run("sh", "-c", f'echo $$ > "{pidfile}"; exec sleep 37', timeout=100))
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        await asyncio.sleep(0.05)
    pid = int(pidfile.read_text())
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("the cancellation was swallowed")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return                     # gone, and reaped
    os.kill(pid, 9)
    raise AssertionError(f"process {pid} was still running")


async def test_try_restart_is_an_allowed_action(monkeypatch):
    calls = []

    async def fake_run(*args, timeout=120):
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr(bot, "run", fake_run)
    assert await real_service_action("dry-run", "try-restart") == (True, "")
    assert calls == [("systemctl", "--user", "try-restart", "tele-notify-dryrun")]
