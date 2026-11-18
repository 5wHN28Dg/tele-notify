"""The settings screen's data helpers: load, validate, merge, save."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from telenotify_tui import bot
from telenotify_tui.screens import settings_data as sd
from telenotify_tui.store import Chat


def base_cfg() -> dict:
    # unknown keys and secrets mixed between known ones, in a deliberate order
    return {
        "api_id": 12345,
        "zz_custom": {"nested": [1, 2]},
        "api_hash": "secret-hash",
        "location_keywords_ar": ["البصرة"],
        "mid_level_general_role_en": ["electrical engineer"],
        "recent_messages": ["a", "b"],
        "chats": [-1, -2],
        "target_chat": -999,
    }


def write(path: Path, cfg: dict) -> Path:
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------- loading

def test_load_errors(tmp_path):
    with pytest.raises(sd.ConfigError, match="cannot read"):
        sd.load(tmp_path / "missing.json")
    (tmp_path / "bad.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(sd.ConfigError, match="not valid JSON"):
        sd.load(tmp_path / "bad.json")
    (tmp_path / "list.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(sd.ConfigError, match="object"):
        sd.load(tmp_path / "list.json")


def test_secret_status():
    assert sd.secret_status(base_cfg()) == {"api_id": "set", "api_hash": "set"}
    assert sd.secret_status({"api_id": 0, "api_hash": ""}) == {"api_id": "missing", "api_hash": "missing"}
    assert sd.secret_status({"api_id": "123"})["api_id"] == "missing"   # the bot reads it as 0


def test_parse_int():
    assert sd.parse_int(" -100123 ") == -100123
    assert sd.parse_int("abc") == "abc"


# ------------------------------------------------------------- validation

def keys_of(problems, error=None):
    return {p.key for p in problems if error is None or p.error == error}


def test_valid_config_has_no_errors():
    assert not sd.errors(sd.validate(base_cfg()))


def test_required_lists():
    cfg = base_cfg()
    cfg["mid_level_general_role_en"] = []
    cfg["location_keywords_ar"] = ["  "]
    errs = keys_of(sd.validate(cfg), error=True)
    assert set(sd.ROLE_KEYS) <= errs and set(sd.LOCATION_KEYS) <= errs
    cfg["field_keywords_ar"] = ["علوم حاسبات"]   # any role list will do
    assert not set(sd.ROLE_KEYS) & keys_of(sd.validate(cfg), error=True)


@pytest.mark.parametrize("key,value", [("max_experience_years", -1), ("max_experience_years", "2"),
                                       ("alert_after_minutes", 1.5), ("alert_after_minutes", True),
                                       ("dry_run", 1), ("data_dir", 5), ("target_chat", "x"),
                                       ("chats", [-1, "abc"]), ("chats", "-1"),
                                       ("certifications", "CCNA"), ("certifications", [1])])
def test_wrong_types_are_errors(key, value):
    cfg = base_cfg()
    cfg[key] = value
    assert key in keys_of(sd.validate(cfg), error=True)


def test_warnings():
    cfg = base_cfg()
    cfg["level_keywords_en"] = ["Intern", "intern ", "junior", ""]
    cfg["target_chat"] = 0
    probs = sd.validate(cfg)
    warn = [p for p in probs if not p.error]
    assert any(p.key == "level_keywords_en" and "Intern" in p.message for p in warn)
    assert any(p.key == "level_keywords_en" and "empty" in p.message for p in warn)
    assert "target_chat" in keys_of(probs, error=False)
    assert not sd.errors(probs)                       # warnings never block


def test_duplicates_ignore_case_and_spacing():
    assert sd.duplicates(["Help  Desk", "help desk", "x", "X", "y"]) == ["Help  Desk", "x"]


# --------------------------------------------------------- diff and merge

def test_absent_key_set_to_default_is_not_a_change():
    orig = base_cfg()
    ed = dict(orig, dry_run=False, alerts=True, extra_seniority=[])
    assert sd.changed_keys(orig, ed) == []
    ed["alerts"] = False
    assert sd.changed_keys(orig, ed) == ["alerts"]


def test_apply_edits_keeps_unknown_keys_order_and_secrets():
    orig = base_cfg()
    ed = json.loads(json.dumps(orig))
    ed["location_keywords_ar"].append("الزبير")
    ed["max_experience_years"] = 2
    ed["api_hash"] = "tampered"          # never edited by the screen; must not leak through
    out, conflicts = sd.apply_edits(orig, orig, ed)
    assert not conflicts
    assert list(out) == list(orig) + ["max_experience_years"]
    assert out["api_hash"] == "secret-hash" and out["zz_custom"] == {"nested": [1, 2]}
    assert out["location_keywords_ar"] == ["البصرة", "الزبير"]


def test_apply_edits_on_a_file_changed_elsewhere():
    orig = base_cfg()
    disk = dict(orig, recent_messages=["c"], chats=[-5])
    ed = dict(orig, target_chat=-1, chats=[-1])
    out, conflicts = sd.apply_edits(disk, orig, ed)
    assert out["recent_messages"] == ["c"] and out["target_chat"] == -1
    assert conflicts == ["chats"]


def test_removed_key():
    orig = dict(base_cfg(), tdlib_path="/x")
    ed = dict(orig)
    del ed["tdlib_path"]
    out, _ = sd.apply_edits(orig, orig, ed)
    assert "tdlib_path" not in out


# ----------------------------------------------------------------- saving

def test_save_round_trip_backup_and_format(tmp_path):
    path = write(tmp_path / "config.json", base_cfg())
    os.chmod(path, 0o640)
    old = path.read_text(encoding="utf-8")
    new = dict(base_cfg(), level_keywords_ar=["حديث التخرج"])
    bak = sd.save(path, new, now=0)
    assert bak is not None and bak.read_text(encoding="utf-8") == old
    assert bak.name.startswith("config.json.bak-") and len(bak.name) == len("config.json.bak-YYYYmmdd-HHMMSS")
    text = path.read_text(encoding="utf-8")
    assert "حديث التخرج" in text and "\\u" not in text      # ensure_ascii=False
    assert text.startswith('{\n  "api_id": 12345,')          # indent 2, order kept
    assert json.loads(text) == new
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    bak2 = sd.save(path, base_cfg(), now=0)                  # same second: a second backup
    assert bak2 != bak and bak2.exists() and bak.exists()


def test_atomic_write_leaves_old_file_on_failure(tmp_path, monkeypatch):
    path = write(tmp_path / "config.json", base_cfg())
    old = path.read_text(encoding="utf-8")

    def boom(*_a):
        raise OSError("disk full")

    monkeypatch.setattr(sd.os, "replace", boom)
    with pytest.raises(OSError):
        sd.write_atomic(path, "{}")
    assert path.read_text(encoding="utf-8") == old
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json"]   # no temp file left


def test_write_temp_is_private(tmp_path):
    p = sd.write_temp(base_cfg())
    try:
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
        assert json.loads(p.read_text(encoding="utf-8")) == base_cfg()
    finally:
        p.unlink()


def test_corpus_path(monkeypatch, tmp_path):
    monkeypatch.setenv("TELENOTIFY_CORPUS", str(tmp_path / "c.jsonl"))
    assert sd.corpus_path() == tmp_path / "c.jsonl"
    monkeypatch.delenv("TELENOTIFY_CORPUS")
    assert sd.corpus_path() == bot.default_corpus()


# ------------------------------------------------------------ corpus test

def test_compare_eval_marks_what_got_worse():
    before = bot.EvalResult(rows={"TRUE": (5, 2, 0), "UNSURE": (1, 1, 1), "FALSE": (0, 3, 10)})
    after = bot.EvalResult(rows={"TRUE": (4, 2, 1), "UNSURE": (2, 1, 0), "FALSE": (2, 3, 8)})
    rows = sd.compare_eval(before, after)
    cell = {(c.label, c.verdict): c for row in rows for c in row}
    assert cell["FALSE", "publish"].mood == "worse" and cell["TRUE", "reject"].mood == "worse"
    assert cell["TRUE", "publish"].mood == "worse"     # fewer true matches published
    assert cell["FALSE", "reject"].mood == "worse"
    assert cell["UNSURE", "publish"].mood == "" and cell["TRUE", "review"].mood == ""
    better = {(c.label, c.verdict): c for row in sd.compare_eval(after, before) for c in row}
    assert better["FALSE", "publish"].mood == "better"


# ------------------------------------------------------------------ chats

def test_chat_rows_toggle_and_target():
    chats = [Chat(-1, "Basra Jobs", "channel", True, "ok", 0), Chat(-4, "Family", "group", False, "ok", 0)]
    cfg = {"chats": [-1, -77], "target_chat": -999}
    rows = {r.id: r for r in sd.chat_rows(chats, cfg)}
    assert rows[-1].watched and not rows[-4].watched
    assert rows[-77].title == "unknown chat -77" and not rows[-77].known and rows[-77].watched
    assert rows[-999].target and not rows[-999].watched
    assert sd.toggle_watched(cfg, -4) is True and cfg["chats"] == [-1, -77, -4]
    assert sd.toggle_watched(cfg, -1) is False and cfg["chats"] == [-77, -4]
    sd.set_target(cfg, -4)
    assert cfg["target_chat"] == -4
