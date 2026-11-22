"""The Settings screen, driven through the app like a user would."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import BAD, GOOD
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    OptionList,
    Static,
    TabbedContent,
)

from telenotify_tui import bot, rtl
from telenotify_tui.app import TeleNotifyApp
from telenotify_tui.screens import settings_data as sd
from telenotify_tui.screens.settings import (
    ConfirmModal,
    EvalModal,
    KeywordEditor,
    SettingsPane,
)


@pytest.fixture
def cfg_path(config: Path) -> Path:
    """The conftest config plus secrets and keys the screen doesn't know, in a set order."""
    cfg = json.loads(config.read_text(encoding="utf-8"))
    out = {"api_id": 4242, "api_hash": "do-not-show", "zz_unknown": {"keep": True}}
    out.update(cfg)
    out["recent_messages"] = ["x"]
    config.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return config


@pytest.fixture
def services(monkeypatch):
    calls = []

    async def fake(mode, action):
        calls.append((mode, action))
        return True, ""

    async def running(mode):
        return "active"

    monkeypatch.setattr(bot, "service_action", fake)
    monkeypatch.setattr(bot, "service_state", running)
    return calls


async def save(pilot) -> None:
    """ctrl+s, then wait for the restart question (it asks systemd first)."""
    await pilot.press("ctrl+s")
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


async def open_settings(pilot) -> SettingsPane:
    await pilot.press("4")
    await pilot.pause()
    pane = pilot.app.query_one(SettingsPane)
    assert pane.original is not None, pane.load_error
    return pane


def backups(cfg: Path) -> list[Path]:
    return sorted(cfg.parent.glob("config.json.bak-*"))


async def add_keyword(pilot, pane: SettingsPane, section: str, key: str, word: str) -> None:
    pane.query_one("#sections", TabbedContent).active = f"sec-{section}"
    await pilot.pause()
    picker = pane.query_one(f"#picker-{section}", OptionList)
    picker.highlighted = [picker.get_option_at_index(i).id for i in range(picker.option_count)].index(key)
    await pilot.pause()
    entry = pane.query_one(f"#editor-{section} #kw-entry", Input)
    entry.focus()
    entry.value = word
    await pilot.press("enter")
    await pilot.pause()


async def test_secrets_are_never_shown(paths, cfg_path):
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        shown = " ".join(str(w.render()) for w in pane.query(Static))
        shown += " ".join(w.value for w in pane.query(Input))
        assert "do-not-show" not in shown and "4242" not in shown
        assert "api_hash: set" in str(pane.query_one("#secrets", Static).render())


async def test_edit_save_round_trip_backup_and_restart_cancel(paths, cfg_path, services):
    before = cfg_path.read_text(encoding="utf-8")
    order = list(json.loads(before))
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        await add_keyword(pilot, pane, "places", "location_keywords_ar", "أبو الخصيب")
        assert pane.dirty
        assert str(app.query_one(TabbedContent).get_tab("settings").label).endswith("*")
        assert json.loads(cfg_path.read_text(encoding="utf-8")) == json.loads(before)   # not saved yet
        await save(pilot)
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("n")
        await pilot.pause()
        assert services == []                                  # cancel does nothing
        assert not pane.dirty
        assert not str(app.query_one(TabbedContent).get_tab("settings").label).endswith("*")
    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert list(saved) == order
    assert saved["api_id"] == 4242 and saved["api_hash"] == "do-not-show"
    assert saved["zz_unknown"] == {"keep": True} and saved["recent_messages"] == ["x"]
    assert saved["location_keywords_ar"][-1] == "أبو الخصيب"
    assert "أبو الخصيب" in cfg_path.read_text(encoding="utf-8")
    [bak] = backups(cfg_path)
    assert bak.read_text(encoding="utf-8") == before


async def test_restart_confirmed_uses_the_dry_run_service(paths, cfg_path, services):
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        await add_keyword(pilot, pane, "filters", "extra_seniority", "lead")
        await save(pilot)
        await pilot.press("y")
        await app.workers.wait_for_complete()
        await pilot.pause()
    assert services == [("dry-run", "try-restart")]


async def test_stopped_service_is_not_started_by_a_save(paths, cfg_path, services, monkeypatch):
    async def stopped(mode):
        return "inactive"

    monkeypatch.setattr(bot, "service_state", stopped)
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        await add_keyword(pilot, pane, "filters", "extra_seniority", "lead")
        await save(pilot)
        assert not isinstance(app.screen, ConfirmModal)          # no restart offered
        assert any("isn't running" in n.message and "wasn't restarted" in n.message
                   for n in app._notifications)
    assert services == []
    assert json.loads(cfg_path.read_text(encoding="utf-8"))["extra_seniority"] == ["lead"]


async def test_conflict_can_take_the_files_value_and_keep_other_edits(paths, cfg_path, services):
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        await add_keyword(pilot, pane, "filters", "extra_seniority", "lead")
        pane._put("chats", [-1, -2, -3, -8])
        on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
        on_disk["chats"] = [-1, -9]                              # changed elsewhere meanwhile
        on_disk["recent_messages"] = ["bot wrote this"]
        cfg_path.write_text(json.dumps(on_disk, ensure_ascii=False, indent=2), encoding="utf-8")
        await save(pilot)
        assert isinstance(app.screen, ConfirmModal)
        assert "chats" in str(app.screen.query_one("#confirm-text", Static).render())
        yes = app.screen.query_one("#yes", Button)                # it drops the user's own edits
        assert yes.variant == "error" and "Use the file's values" in str(yes.label)
        await pilot.press("y")                                   # use the file's chats
        await pilot.pause()
        assert pane.edited["chats"] == [-1, -9] and pane.edited["extra_seniority"] == ["lead"]
        assert pane.dirty
        await save(pilot)                                        # saves now, then offers the restart
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("n")
        await pilot.pause()
    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert saved["chats"] == [-1, -9] and saved["extra_seniority"] == ["lead"]
    assert saved["recent_messages"] == ["bot wrote this"] and saved["api_hash"] == "do-not-show"


async def test_conflict_declined_saves_nothing(paths, cfg_path, services):
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        pane._put("chats", [-1, -2, -3, -8])
        on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
        on_disk["chats"] = [-1, -9]
        cfg_path.write_text(json.dumps(on_disk, ensure_ascii=False, indent=2), encoding="utf-8")
        await save(pilot)
        await pilot.press("n")
        await pilot.pause()
        assert pane.edited["chats"] == [-1, -2, -3, -8]
    assert json.loads(cfg_path.read_text(encoding="utf-8"))["chats"] == [-1, -9] and backups(cfg_path) == []


async def test_chat_id_with_markup_is_shown_literally(paths, cfg_path):
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        pane.query_one("#sections", TabbedContent).active = "sec-chats"
        await pilot.pause()
        entry = pane.query_one("#chat-add", Input)
        entry.focus()
        entry.value = "x[/]"
        await pilot.press("enter")
        await pilot.pause()
        assert app.is_running
        assert any("x[/]" in n.message and not n.markup for n in app._notifications)


async def test_validation_errors_block_save(paths, cfg_path, services):
    before = cfg_path.read_text(encoding="utf-8")
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        for k in sd.LOCATION_KEYS:
            pane._put(k, [])
        pane.query_one("#sections", TabbedContent).active = "sec-filters"
        await pilot.pause()
        box = pane.query_one("#f-max_experience_years", Input)
        box.focus()
        box.value = "-2"
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmModal)
        assert "no location keywords" in str(pane.query_one("#problems-places", Static).render())
        assert "whole number" in str(pane.query_one("#problems-filters", Static).render())
        assert "problem" in str(pane.query_one("#settings-status", Static).render())
    assert cfg_path.read_text(encoding="utf-8") == before and backups(cfg_path) == []
    assert services == []


async def test_chat_toggles_and_target(paths, cfg_path, services):
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        pane.query_one("#sections", TabbedContent).active = "sec-chats"
        await pilot.pause()
        table = pane.query_one("#chat-table", DataTable)
        titles = [str(table.get_row_at(i)[2]) for i in range(table.row_count)]
        assert "Family" in titles and "unknown chat -999" in titles    # target not in the bot's list
        table.focus()
        table.move_cursor(row=titles.index("Family"))
        await pilot.press("space")                    # watch Family (-4)
        table.move_cursor(row=titles.index("Basra Jobs"))
        await pilot.press("space")                    # stop watching Basra Jobs (-1)
        table.move_cursor(row=titles.index("Family"))
        await pilot.press("m")                        # send matches to Family
        await pilot.pause()
        assert pane.edited["chats"] == [-2, -3, -4] and pane.edited["target_chat"] == -4
        await save(pilot)
        await pilot.press("n")
        await pilot.pause()
    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert saved["chats"] == [-2, -3, -4] and saved["target_chat"] == -4
    assert services == []


async def test_chats_without_a_chat_list_edit_ids(paths, cfg_path, tmp_path):
    state = tmp_path / "bare.sqlite3"
    sqlite3.connect(state).executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);").close()
    app = TeleNotifyApp(replace(paths, config=cfg_path, state=state), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        pane.query_one("#sections", TabbedContent).active = "sec-chats"
        await pilot.pause()
        assert pane.query_one("#chat-named").has_class("hidden")
        assert "Names appear" in str(pane.query_one("#chat-note", Static).render())
        editor = pane.query_one("#editor-chats", KeywordEditor)
        entry = editor.query_one("#kw-entry", Input)
        entry.focus()
        entry.value = "-100500"
        await pilot.press("enter")
        await pilot.pause()
        assert pane.edited["chats"] == [-1, -2, -3, -100500]
        entry.value = "abc"
        await pilot.press("enter")
        await pilot.pause()
        assert "not chat IDs" in str(pane.query_one("#problems-chats", Static).render())


async def test_reload_keeps_unsaved_edits_and_revert_asks(paths, cfg_path):
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        await add_keyword(pilot, pane, "roles", "level_keywords_en", "new grad")
        await pilot.press("ctrl+r")                 # asks before throwing edits away
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("n")
        await pilot.pause()
        assert pane.dirty and "new grad" in pane.edited["level_keywords_en"]
        pane.reload()                               # tab shown again: keeps edits, no question
        await pilot.pause()
        assert pane.dirty and not isinstance(app.screen, ConfirmModal)
        pane.action_revert()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("n")
        await pilot.pause()
        assert pane.dirty
        pane.action_revert()
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert not pane.dirty and "new grad" not in pane.edited["level_keywords_en"]


async def test_keyword_edit_delete_and_filter(paths, cfg_path):
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        await add_keyword(pilot, pane, "roles", "level_keywords_en", "zz first")
        editor = pane.query_one("#editor-roles", KeywordEditor)
        editor.query_one("#kw-filter", Input).value = "zz"
        await pilot.pause()
        items = editor.query_one("#kw-items", OptionList)
        assert items.option_count == 1
        items.focus()
        items.highlighted = 0
        await pilot.press("enter")                     # edit it
        await pilot.pause()
        entry = editor.query_one("#kw-entry", Input)
        assert entry.has_focus and entry.value == "zz first"
        entry.value = "zz second"
        await pilot.press("enter")
        await pilot.pause()
        assert pane.edited["level_keywords_en"][-1] == "zz second"
        items.focus()
        items.highlighted = 0
        await pilot.press("delete")
        await pilot.pause()
        assert not any(w.startswith("zz") for w in pane.edited["level_keywords_en"])
        assert not pane.dirty


def eval_result(wrong: int, lost: int) -> bot.EvalResult:
    return bot.EvalResult(rows={"TRUE": (5, 2, lost), "UNSURE": (1, 1, 1), "FALSE": (wrong, 3, 10)},
                          summary=[f"published: {6 + wrong}, of which wrong: {wrong}"])


async def test_corpus_test_shows_before_and_after(paths, cfg_path, tmp_path, monkeypatch):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("TELENOTIFY_CORPUS", str(corpus))
    seen = {}

    async def fake_evaluate(binary, config, corp):
        assert corp == corpus
        cfg = json.loads(Path(config).read_text(encoding="utf-8"))
        if Path(config) == cfg_path:
            return eval_result(0, 0)
        seen["edited"], seen["path"] = cfg, Path(config)
        return eval_result(2, 1)

    monkeypatch.setattr(bot, "evaluate", fake_evaluate)
    app = TeleNotifyApp(replace(paths, config=cfg_path, binary=Path("/fake/telenotify")), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        await add_keyword(pilot, pane, "filters", "extra_not_a_job", "course")
        pane.query_one("#picker-filters", OptionList).focus()
        await pilot.press("t")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, EvalModal)
        table = app.screen.query_one(DataTable)
        false_row = [str(c) for c in table.get_row("FALSE")]
        true_row = [str(c) for c in table.get_row("TRUE")]
        assert false_row[1].startswith("0 -> 2") and "!" in false_row[1]
        assert true_row[3].startswith("0 -> 1") and "!" in true_row[3]
        summary = str(app.screen.query_one("#eval-summary", Static).render())
        assert "wrong publishes (FALSE published): 0 -> 2 (worse)" in summary
        assert "true matches lost (TRUE rejected): 0 -> 1 (worse)" in summary
        await pilot.press("escape")
    assert seen["edited"]["extra_not_a_job"] == ["course"]
    assert "api_hash" not in seen["edited"] and "api_id" not in seen["edited"]   # judging needs no secrets
    assert not seen["path"].exists()                             # temp copy removed


async def test_corpus_test_without_corpus(paths, cfg_path, tmp_path, monkeypatch):
    monkeypatch.setenv("TELENOTIFY_CORPUS", str(tmp_path / "missing.jsonl"))
    called = []

    async def fake_evaluate(*a):
        called.append(a)
        return eval_result(0, 0)

    monkeypatch.setattr(bot, "evaluate", fake_evaluate)
    app = TeleNotifyApp(replace(paths, config=cfg_path, binary=Path("/fake/telenotify")), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        pane.query_one("#picker-roles", OptionList).focus()
        await pilot.press("t")
        await pilot.pause()
        assert not isinstance(app.screen, EvalModal) and called == []
        assert any("No corpus" in n.message for n in app._notifications)


async def test_corpus_test_with_the_real_filter(binary, paths, cfg_path, tmp_path, monkeypatch):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [
        {"id": 1, "text": GOOD, "label": "TRUE"},
        {"id": 2, "text": BAD, "label": "FALSE"},
    ]) + "\n", encoding="utf-8")
    monkeypatch.setenv("TELENOTIFY_CORPUS", str(corpus))
    app = TeleNotifyApp(replace(paths, config=cfg_path, binary=binary), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        await add_keyword(pilot, pane, "filters", "extra_not_a_job", "مهندس")   # rejects the good post
        pane.query_one("#picker-filters", OptionList).focus()
        await pilot.press("t")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, EvalModal), [n.message for n in app._notifications]
        modal = app.screen
        assert not modal.before.error and not modal.after.error, (modal.before.error, modal.after.error)
        assert modal.before.rows["TRUE"] == (1, 0, 0) and modal.before.rows["FALSE"][0] == 0
        assert modal.after.true_lost == 1
        assert "true matches lost (TRUE rejected): 0 -> 1 (worse)" in str(
            modal.query_one("#eval-summary", Static).render())


async def test_layout_fits_small_terminal(paths, cfg_path, tmp_path):
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(100, 30)) as pilot:
        pane = await open_settings(pilot)
        for sec, *_ in sd.SECTIONS:
            pane.query_one("#sections", TabbedContent).active = f"sec-{sec}"
            await pilot.pause()
        pane.query_one("#sections", TabbedContent).active = "sec-roles"
        await pilot.pause()
        items = pane.query_one("#editor-roles #kw-items", OptionList)
        assert items.size.height >= 5 and items.size.width >= 40
        for wid in ("#kw-keys", "#kw-delete", "#kw-add", "#kw-entry"):   # the delete / add controls show
            w = pane.query_one(f"#editor-roles {wid}")
            assert w.region.height >= 1 and w.region.width >= 5, wid
        assert pane.query_one("#editor-roles #kw-delete").region.right <= 100


async def test_quit_with_unsaved_settings_asks_first(paths, cfg_path):
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        await add_keyword(pilot, pane, "roles", "level_keywords_en", "new grad")
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal) and app.is_running
        await pilot.press("n")
        await pilot.pause()
        assert app.is_running and pane.dirty
        await pilot.press("ctrl+q")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert not app.is_running


def messages(app) -> str:
    return "\n".join(n.message for n in app._notifications)


async def test_infinity_in_an_unknown_key_refuses_to_save(paths, cfg_path, services):
    cfg_path.write_text(cfg_path.read_text(encoding="utf-8").replace('"keep": true', '"keep": 1e999'),
                        encoding="utf-8")
    before = cfg_path.read_text(encoding="utf-8")
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        await add_keyword(pilot, pane, "filters", "extra_seniority", "lead")
        await save(pilot)
        assert app.is_running and not isinstance(app.screen, ConfirmModal)
        assert "Not saved" in messages(app) and "zz_unknown" in messages(app)
        assert "NaN or Infinity" in messages(app)
    assert cfg_path.read_text(encoding="utf-8") == before and backups(cfg_path) == []
    assert services == []


async def test_infinity_added_on_disk_meanwhile_refuses_to_save(paths, cfg_path, services):
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        await add_keyword(pilot, pane, "filters", "extra_seniority", "lead")
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg_path.write_text(json.dumps(cfg | {"zz_new": 1}).replace('"zz_new": 1', '"zz_new": 1e999'),
                            encoding="utf-8")
        before = cfg_path.read_text(encoding="utf-8")
        await save(pilot)
        assert app.is_running
        assert "Not saved" in messages(app) and "zz_new" in messages(app)
    assert cfg_path.read_text(encoding="utf-8") == before and backups(cfg_path) == []


@pytest.mark.parametrize("key", ["data_dir", "dry_run"])
async def test_changing_where_the_state_lives_says_to_reopen(paths, cfg_path, services, tmp_path, key):
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        pane._put(key, str(tmp_path / "elsewhere") if key == "data_dir" else True)
        pane.query_one("#editor-roles #kw-entry", Input).focus()   # ctrl+s needs focus inside Settings
        await save(pilot)
        assert "Saved" in messages(app)
        assert "reopen this screen" in messages(app) and key in messages(app)


async def test_ordinary_save_doesnt_say_reopen(paths, cfg_path, services):
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        await add_keyword(pilot, pane, "filters", "extra_seniority", "lead")
        await save(pilot)
        assert "Saved" in messages(app) and "reopen" not in messages(app)


async def test_config_values_are_shown_without_control_characters(paths, cfg_path):
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["extra_seniority"] = ["le\x1b]0;PWNED\x07ad"]
    cfg["data_dir"] = cfg["data_dir"] + "\x1b[2J"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        pane.query_one("#sections", TabbedContent).active = "sec-filters"
        await pilot.pause()
        picker = pane.query_one("#picker-filters", OptionList)
        picker.highlighted = [picker.get_option_at_index(i).id
                              for i in range(picker.option_count)].index("extra_seniority")
        await pilot.pause()
        items = pane.query_one("#editor-filters #kw-items", OptionList)
        assert str(items.get_option_at_index(0).prompt) == "le]0;PWNEDad"
        assert "\x1b" not in pane.query_one("#f-data_dir", Input).value
        assert not pane.dirty                      # showing a cleaned value isn't an edit


# ------------------------------------------- deleting, adding, duplicates

EN = ["basra", "abu al khasib", "zubair", "Abu al Khasib"]
AR = ["البصرة", "التنومة", "الزبير", "التنومة"]


@pytest.fixture
def dup_cfg(cfg_path: Path) -> Path:
    """A config with one place listed twice in each language (as in the owner's)."""
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["location_keywords_en"], cfg["location_keywords_ar"] = list(EN), list(AR)
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg_path


async def open_places(pilot, key: str = "location_keywords_en") -> tuple[SettingsPane, KeywordEditor]:
    pane = await open_settings(pilot)
    pane.query_one("#sections", TabbedContent).active = "sec-places"
    await pilot.pause()
    picker = pane.query_one("#picker-places", OptionList)
    picker.highlighted = [picker.get_option_at_index(i).id for i in range(picker.option_count)].index(key)
    await pilot.pause()
    return pane, pane.query_one("#editor-places", KeywordEditor)


async def type_filter(pilot, word: str) -> Input:
    await pilot.click("#editor-places #kw-filter")
    await pilot.press(*word)
    await pilot.pause()
    return pilot.app.query_one("#editor-places #kw-filter", Input)


async def test_delete_in_the_filter_box_edits_the_filter_then_down_enters_the_list(paths, dup_cfg):
    app = TeleNotifyApp(replace(paths, config=dup_cfg), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane, editor = await open_places(pilot)
        flt = await type_filter(pilot, "khasibx")
        await pilot.press("left", "delete")                 # delete edits the filter text...
        await pilot.pause()
        assert flt.value == "khasib"
        assert pane.edited["location_keywords_en"] == EN    # ...and deletes no keyword
        items = editor.query_one("#kw-items", OptionList)
        assert items.option_count == 2
        await pilot.press("down")                           # into the list
        await pilot.pause()
        assert app.focused is items and items.highlighted == 0
        await pilot.press("down", "delete")                 # the second spelling
        await pilot.pause()
        assert pane.edited["location_keywords_en"] == ["basra", "abu al khasib", "zubair"]
        assert pane.dirty


async def test_enter_in_the_filter_box_enters_the_list(paths, dup_cfg):
    app = TeleNotifyApp(replace(paths, config=dup_cfg), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane, editor = await open_places(pilot)
        await type_filter(pilot, "zubair")
        await pilot.press("enter")
        await pilot.pause()
        assert app.focused is editor.query_one("#kw-items", OptionList)
        await pilot.press("x")
        await pilot.pause()
        assert pane.edited["location_keywords_en"] == ["basra", "abu al khasib", "Abu al Khasib"]


async def test_click_an_item_then_press_delete(paths, dup_cfg):
    app = TeleNotifyApp(replace(paths, config=dup_cfg), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane, editor = await open_places(pilot)
        await type_filter(pilot, "khasib")
        await pilot.click("#editor-places #kw-items", offset=(2, 1))   # the second match
        await pilot.pause()
        items = editor.query_one("#kw-items", OptionList)
        assert app.focused is items and editor.editing is None         # a click picks, it doesn't edit
        await pilot.press("delete")
        await pilot.pause()
        assert pane.edited["location_keywords_en"] == ["basra", "abu al khasib", "zubair"]


async def test_mouse_only_delete_and_double_click_edit(paths, dup_cfg):
    app = TeleNotifyApp(replace(paths, config=dup_cfg), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane, editor = await open_places(pilot, "location_keywords_ar")
        await pilot.click("#editor-places #kw-items", offset=(2, 3))   # the second التنومة
        await pilot.pause()
        await pilot.click("#editor-places #kw-delete")
        await pilot.pause()
        assert pane.edited["location_keywords_ar"] == ["البصرة", "التنومة", "الزبير"]
        await pilot.click("#editor-places #kw-items", offset=(2, 0), times=2)   # double click edits
        await pilot.pause()
        entry = editor.query_one("#kw-entry", Input)
        assert editor.editing == 0 and entry.value == "البصرة" and entry.has_focus


async def test_edit_button_and_hint_line(paths, dup_cfg):
    app = TeleNotifyApp(replace(paths, config=dup_cfg), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        _pane, editor = await open_places(pilot)
        hint = str(editor.query_one("#kw-keys", Static).render())
        assert "del / x: delete" in hint and "a: add" in hint and "edit" in hint
        await pilot.click("#editor-places #kw-items", offset=(2, 2))
        await pilot.pause()
        await pilot.click("#editor-places #kw-edit")
        await pilot.pause()
        assert editor.editing == 2 and editor.query_one("#kw-entry", Input).value == "zubair"


async def test_add_by_key_and_by_button(paths, dup_cfg):
    app = TeleNotifyApp(replace(paths, config=dup_cfg), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane, editor = await open_places(pilot)
        await pilot.click("#editor-places #kw-items", offset=(2, 0))
        await pilot.press("a")
        await pilot.pause()
        entry = editor.query_one("#kw-entry", Input)
        assert entry.has_focus
        await pilot.press(*"fao", "enter")
        await pilot.pause()
        assert pane.edited["location_keywords_en"][-1] == "fao"
        await pilot.click("#editor-places #kw-add")
        await pilot.pause()
        assert entry.has_focus
        await pilot.press(*"siba", "enter")
        await pilot.pause()
        assert pane.edited["location_keywords_en"][-2:] == ["fao", "siba"]


async def test_duplicate_warning_names_list_and_words_and_offers_the_fix(paths, dup_cfg):
    app = TeleNotifyApp(replace(paths, config=dup_cfg), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane, _editor = await open_places(pilot)
        problems = str(pane.query_one("#problems-places", Static).render())
        assert 'location_keywords_en: listed twice: "abu al khasib"' in problems
        # drawn in display order (the screen reorders right-to-left words)
        assert 'location_keywords_ar: listed twice: "' in problems and rtl.visual("التنومة") in problems
        assert "Remove duplicates" in problems
        button = pane.query_one("#dedupe", Button)
        assert not button.has_class("hidden")
        await pilot.click("#dedupe")
        await pilot.pause()
        assert pane.edited["location_keywords_en"] == ["basra", "abu al khasib", "zubair"]   # first spelling kept
        assert pane.edited["location_keywords_ar"] == ["البصرة", "التنومة", "الزبير"]
        assert button.has_class("hidden")
        assert "listed twice" not in str(pane.query_one("#problems-places", Static).render())
        assert pane.dirty


async def test_remove_duplicates_by_key(paths, dup_cfg):
    app = TeleNotifyApp(replace(paths, config=dup_cfg), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane, _editor = await open_places(pilot)
        await pilot.click("#editor-places #kw-items", offset=(2, 0))   # focus in the list, not a text box
        await pilot.press("D")
        await pilot.pause()
        assert pane.edited["location_keywords_en"] == ["basra", "abu al khasib", "zubair"]
        assert pane.edited["location_keywords_ar"] == ["البصرة", "التنومة", "الزبير"]
        await pilot.click("#editor-places #kw-filter")
        await pilot.press("D")                                         # in a text box D is just a letter
        await pilot.pause()
        assert pane.query_one("#editor-places #kw-filter", Input).value == "D"


# ------------------------------------------ the list changing under an open edit

@pytest.fixture
def abc_cfg(cfg_path: Path) -> Path:
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["location_keywords_en"] = ["a", "A", "b", "c"]
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg_path


async def retype(pilot, editor: KeywordEditor, text: str) -> None:
    entry = editor.query_one("#kw-entry", Input)
    entry.value = text
    entry.focus()
    await pilot.press("enter")
    await pilot.pause()


def notes(app) -> str:
    return "\n".join(str(n.message) for n in app._notifications)


async def test_remove_duplicates_during_an_edit_keeps_editing_the_same_item(paths, abc_cfg):
    app = TeleNotifyApp(replace(paths, config=abc_cfg), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane, editor = await open_places(pilot)
        await pilot.click("#editor-places #kw-items", offset=(2, 2), times=2)   # edit "b"
        await pilot.pause()
        assert editor.editing == 2
        await pilot.click("#dedupe")
        await pilot.pause()
        assert pane.edited["location_keywords_en"] == ["a", "b", "c"]
        await retype(pilot, editor, "bb")
        assert pane.edited["location_keywords_en"] == ["a", "bb", "c"]


async def test_remove_duplicates_cancels_an_edit_of_a_removed_repeat(paths, abc_cfg):
    app = TeleNotifyApp(replace(paths, config=abc_cfg), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane, editor = await open_places(pilot)
        await pilot.click("#editor-places #kw-items", offset=(2, 1), times=2)   # edit "A", the repeat
        await pilot.pause()
        await pilot.click("#dedupe")
        await pilot.pause()
        assert editor.editing is None and editor.query_one("#kw-entry", Input).value == ""
        assert "edit was cancelled" in notes(app)
        await retype(pilot, editor, "zz")                       # now a plain add
        assert pane.edited["location_keywords_en"] == ["a", "b", "c", "zz"]


async def test_deleting_another_item_keeps_the_open_edit(paths, abc_cfg):
    app = TeleNotifyApp(replace(paths, config=abc_cfg), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane, editor = await open_places(pilot)
        await pilot.click("#editor-places #kw-items", offset=(2, 3), times=2)   # edit "c"
        await pilot.pause()
        entry = editor.query_one("#kw-entry", Input)
        entry.value = "c2"                                       # half typed
        await pilot.click("#editor-places #kw-items", offset=(2, 0))            # pick "a"
        await pilot.press("delete")
        await pilot.pause()
        assert pane.edited["location_keywords_en"] == ["A", "b", "c"]
        assert editor.editing == 2 and entry.value == "c2"      # still open, on "c"
        await retype(pilot, editor, "c2")
        assert pane.edited["location_keywords_en"] == ["A", "b", "c2"]


async def test_deleting_the_item_being_edited_ends_the_edit_and_says_so(paths, abc_cfg):
    app = TeleNotifyApp(replace(paths, config=abc_cfg), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane, editor = await open_places(pilot)
        await pilot.click("#editor-places #kw-items", offset=(2, 2), times=2)   # edit "b"
        await pilot.pause()
        await pilot.click("#editor-places #kw-items", offset=(2, 2))
        await pilot.press("delete")
        await pilot.pause()
        assert pane.edited["location_keywords_en"] == ["a", "A", "c"]
        assert editor.editing is None and editor.query_one("#kw-entry", Input).value == ""
        assert "edit you had open on it was cancelled" in notes(app)


async def test_add_during_an_edit_clears_the_box(paths, abc_cfg):
    app = TeleNotifyApp(replace(paths, config=abc_cfg), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane, editor = await open_places(pilot)
        await pilot.click("#editor-places #kw-items", offset=(2, 2), times=2)   # edit "b"
        await pilot.pause()
        await pilot.click("#editor-places #kw-add")
        await pilot.pause()
        entry = editor.query_one("#kw-entry", Input)
        assert editor.editing is None and entry.value == "" and entry.has_focus
        await pilot.press("enter")                               # nothing typed: nothing added
        await pilot.pause()
        assert pane.edited["location_keywords_en"] == ["a", "A", "b", "c"]


async def test_delete_and_edit_buttons_need_a_picked_item(paths, abc_cfg):
    app = TeleNotifyApp(replace(paths, config=abc_cfg), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane, editor = await open_places(pilot)
        await pilot.click("#editor-places #kw-filter")           # focus elsewhere, nothing picked
        await pilot.click("#editor-places #kw-delete")
        await pilot.pause()
        assert pane.edited["location_keywords_en"] == ["a", "A", "b", "c"]
        await pilot.click("#editor-places #kw-edit")
        await pilot.pause()
        assert editor.editing is None
        assert "Pick an item in the list first" in notes(app)


async def test_revert_during_an_edit_of_an_added_item_cancels_it(paths, abc_cfg):
    app = TeleNotifyApp(replace(paths, config=abc_cfg), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane, editor = await open_places(pilot)
        await retype(pilot, editor, "new")                       # added, not saved
        await pilot.click("#editor-places #kw-items", offset=(2, 4), times=2)   # edit "new"
        await pilot.pause()
        assert editor.editing == 4
        pane.action_revert()
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert pane.edited["location_keywords_en"] == ["a", "A", "b", "c"]
        assert editor.editing is None                            # "new" is gone: no stale index
        await retype(pilot, editor, "x")
        assert pane.edited["location_keywords_en"] == ["a", "A", "b", "c", "x"]


async def test_each_kind_of_bot_notification_can_be_switched_off(paths, cfg_path, services):
    """#127 (O1/MSG5): notify_published and notify_review, on by default, as the bot reads them."""
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        pane.query_one("#sections", TabbedContent).active = "sec-bot"
        await pilot.pause()
        published = pane.query_one("#f-notify_published", Checkbox)
        review = pane.query_one("#f-notify_review", Checkbox)
        assert published.value and review.value                      # the bot's defaults
        review.scroll_visible()
        await pilot.pause()
        await pilot.click(review)
        await pilot.pause()
        assert not review.value and pane.dirty
        assert str(review.label) == "off" and str(published.label) == "on"   # in words, not colour alone
        await save(pilot)
        await pilot.press("n")                                          # don't restart the bot
        await pilot.pause()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert cfg["notify_review"] is False
    assert "notify_published" not in cfg                               # unchanged keys aren't written



@pytest.mark.parametrize(("extra", "note"), [
    ({}, "Sends nothing now: no notify_bot_token in config.json"),
    ({"notify_bot_token": "  "}, "Sends nothing now: no notify_bot_token in config.json"),
    ({"notify_bot_token": "garbage"}, "Sends nothing now: notify_bot_token isn't a bot token"),
    ({"notify_bot_token": "123:ab cd"}, "Sends nothing now: notify_bot_token isn't a bot token"),
    ({"notify_bot_token": 12345}, "Sends nothing now: notify_bot_token isn't a bot token"),
    ({"notify_bot_token": "1:abc", "dry_run": True}, "Sends nothing now: a dry run"),
    ({"notify_bot_token": " 1:abc_-Z "}, ""),
    ({"notify_bot_token": "123:abc\u00a0"}, "Sends nothing now: notify_bot_token isn't a bot token"),   # the bot's
    ({"notify_bot_token": "\u2003123:abc"}, "Sends nothing now: notify_bot_token isn't a bot token"),   # strip: ASCII
])
async def test_the_notification_switches_say_when_they_cant_do_anything(paths, cfg_path, extra, note):
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.pop("notify_bot_token", None)
    cfg_path.write_text(json.dumps(cfg | extra), encoding="utf-8")
    app = TeleNotifyApp(replace(paths, config=cfg_path, dry_run=False, dry_run_flag=False),
                        refresh_seconds=0)   # the config decides
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        for key in ("notify_published", "notify_review"):
            text = str(pane.query_one(f"#help-{key}", Static).render())
            assert "(default on)" in text
            assert (note in text) if note else ("Sends nothing" not in text)
        if extra.get("dry_run"):                       # switching the dry run off updates it at once
            pane.query_one("#sections", TabbedContent).active = "sec-bot"
            await pilot.pause()
            await pilot.click(pane.query_one("#f-dry_run", Checkbox))
            await pilot.pause()
            assert "Sends nothing" not in str(pane.query_one("#help-notify_review", Static).render())
            assert str(pane.query_one("#f-dry_run", Checkbox).label) == "off"



async def test_a_focused_switch_still_shows_on_or_off(paths, cfg_path):
    """Run 2 of #131: Textual's focus border filled the one-row switch and hid its state. What is
    drawn is checked (the screen's text, as the audit harness reads it), not the label property."""
    from audit import svg

    def row(app, key: str) -> str:
        w, h = app.size
        lines = svg.text(app.export_screenshot(), w, h).splitlines()
        return next(line for line in lines if line.lstrip().startswith(key))

    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        pane = await open_settings(pilot)
        pane.query_one("#sections", TabbedContent).active = "sec-bot"
        await pilot.pause()
        box = pane.query_one("#f-dry_run", Checkbox)
        box.focus()
        await pilot.pause()
        before = row(app, "dry_run")
        await pilot.press("space")
        await pilot.pause()
        after = row(app, "dry_run")
        assert app.focused is box
        assert {"on", "off"} == {before.split()[2], after.split()[2]}, (before, after)



@pytest.mark.parametrize("theme", ["textual-dark", "textual-light"])
async def test_a_focused_switch_is_readable(paths, cfg_path, theme):
    """Run 3 of #131: with the focus border gone, Textual's focus pair on the label was 3.78:1 in
    textual-dark. The drawn cells of the focused "on"/"off" must reach 4.5:1 (text contrast, A1)."""
    from audit import svg

    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        app.theme = theme
        pane = await open_settings(pilot)
        pane.query_one("#sections", TabbedContent).active = "sec-bot"
        await pilot.pause()
        pane.query_one("#f-dry_run", Checkbox).focus()
        await pilot.pause()
        w, h = app.size
        grid = svg.parse(app.export_screenshot(), w, h)
        row = next(r for r, line in enumerate(grid.text()) if line.lstrip().startswith("dry_run"))
        line = grid.text()[row]
        start = line.index("\u258c") + 1                    # after the box's ▌
        word = [c for c in grid.cells[row][start:start + 5] if c.ch.strip()]
        assert "".join(c.ch for c in word) in ("on", "off")
        for c in word:
            assert svg.contrast(c.fg, c.bg) >= 4.5, (theme, c)



@pytest.mark.parametrize("flag", [False, True])
async def test_the_dry_run_note_follows_the_saved_config_and_the_flag(paths, cfg_path, services, flag):
    """#131 run 4: after saving dry_run off, the note said "a dry run" (the flag was guessed from the
    config as opened). Opened with --dry-run, the flag keeps it a dry run whatever the config says."""
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg_path.write_text(json.dumps(cfg | {"notify_bot_token": "1:abc", "dry_run": True}), encoding="utf-8")
    app = TeleNotifyApp(replace(paths, config=cfg_path, dry_run=True, dry_run_flag=flag), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        note = lambda: str(pane.query_one("#help-notify_review", Static).render())
        assert "a dry run" in note()
        pane.query_one("#sections", TabbedContent).active = "sec-bot"
        await pilot.pause()
        await pilot.click(pane.query_one("#f-dry_run", Checkbox))
        await pilot.pause()
        assert ("dry run" in note()) == flag
        await save(pilot)
        await pilot.press("n")
        await pilot.pause()
        assert json.loads(cfg_path.read_text(encoding="utf-8"))["dry_run"] is False
        assert ("dry run" in note()) == flag


async def test_holding_ctrl_q_asks_once(paths, cfg_path):
    """#92: holding ctrl+q with unsaved edits stacked one quit question per repeat."""
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        await add_keyword(pilot, pane, "roles", "level_keywords_en", "new grad")
        await pilot.press("ctrl+q", "ctrl+q", "ctrl+q", "ctrl+q")
        await pilot.pause()
        assert sum(isinstance(s, ConfirmModal) for s in app.screen_stack) == 1
        await pilot.press("n")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmModal) and app.is_running and pane.dirty


async def test_ctrl_q_over_another_question_still_asks_to_quit(paths, cfg_path):
    """#92 review run 1: only the quit question itself stops a second one; over the revert
    question (or any other), ctrl+q opens the quit question as before."""
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        await add_keyword(pilot, pane, "roles", "level_keywords_en", "new grad")
        await pilot.press("ctrl+r")                       # "Discard your unsaved settings changes…?"
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        depth = len(app.screen_stack)
        await pilot.press("ctrl+q", "ctrl+q")
        await pilot.pause()
        assert len(app.screen_stack) == depth + 1
        assert isinstance(app.screen, ConfirmModal) and app.screen.yes == "Quit"   # the quit question, once


async def test_an_unreadable_state_file_is_said_once_in_plain_words(paths, cfg_path):
    """Review run 1 (C2, W3): Settings said "Couldn't read the chat list: file is not a database",
    several times for one opening of the tab. Now once, in the Dashboard's words."""
    paths.state.write_bytes(b"this is not a database" * 100)
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await open_settings(pilot)
        await pilot.pause()
        said = [n.message for n in app._notifications if "chat list" in n.message]
        assert said == [("The chat list wasn't read. Can't read the state file. "
                         "Check the bot's log: journalctl --user -u tele-notify-dryrun")]
        await pilot.press("1")                            # another opening says it again
        await pilot.pause()
        await open_settings(pilot)
        await pilot.pause()
        assert len([n for n in app._notifications if "chat list" in n.message]) == 2


# A real lock held by another process, with the real timeouts (nothing patched). The bounds
# are generous for a slow CI machine: what they catch is a 10 s wait on the event loop.
LOOP_BLOCK_LIMIT = 3.0
HOLD = ("import sqlite3, sys, time; c = sqlite3.connect(sys.argv[1], isolation_level=None); "
        "c.execute('BEGIN EXCLUSIVE'); print('held', flush=True); time.sleep(float(sys.argv[2]))")


def hold_lock(path: Path, seconds: float = 120):
    import subprocess
    import sys
    p = subprocess.Popen([sys.executable, "-I", "-c", HOLD, str(path), str(seconds)],
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    return p


class LoopWatch:
    """The longest the event loop was blocked while inside the `with`."""

    def __enter__(self):
        import asyncio
        import time

        async def tick():
            while True:
                t = time.monotonic()
                await asyncio.sleep(0.02)
                self.worst = max(self.worst, time.monotonic() - t - 0.02)

        self.worst = 0.0
        self._task = asyncio.get_running_loop().create_task(tick())
        return self

    def __exit__(self, *exc):
        self._task.cancel()


def chat_toasts(app) -> list[str]:
    return [n.message for n in app._notifications if "chat list" in n.message]


async def test_a_real_lock_never_freezes_settings_and_is_said_where_it_stays(paths, cfg_path):
    """Review run 2: under another process's exclusive lock, opening Settings read the chat list
    twice on the UI thread, 10 s each; the only toast expired inside the freeze, the lasting
    note said "No chat list from the bot yet", and ctrl+r said nothing."""
    from telenotify_tui.store import BUSY

    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        holder = hold_lock(paths.state)
        try:
            with LoopWatch() as watch:
                pane = await open_settings(pilot)
                await pilot.pause()
            assert watch.worst < LOOP_BLOCK_LIMIT, f"event loop blocked {watch.worst:.1f} s"
            note = str(pane.query_one("#chat-note", Static).render())
            assert BUSY in note and "ctrl+r retries" in note
            assert "not yet" not in note and "once the bot has run" not in note
            assert chat_toasts(app) == [pane.chats_problem]          # one opening, one toast
            with LoopWatch() as watch:
                await pilot.press("ctrl+r")                          # a retry gets its own answer
                await pilot.pause()
            assert watch.worst < LOOP_BLOCK_LIMIT, f"event loop blocked {watch.worst:.1f} s"
            assert len(chat_toasts(app)) == 2
        finally:
            holder.kill()
            holder.wait()
        await pilot.press("ctrl+r")                                  # the lock is gone
        await pilot.pause()
        assert pane.chats_problem == "" and pane.has_chat_table
        assert BUSY not in str(pane.query_one("#chat-note", Static).render())
        assert len(chat_toasts(app)) == 2


async def test_an_unreadable_chat_list_stays_said_in_the_chat_note(paths, cfg_path):
    """Review run 2: the lasting note said "No chat list from the bot yet" while the read failed."""
    paths.state.write_bytes(b"this is not a database" * 100)
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        await pilot.pause()
        note = str(pane.query_one("#chat-note", Static).render())
        assert "Can't read the state file." in note and "journalctl --user -u tele-notify-dryrun" in note
        assert "not yet" not in note
        await add_keyword(pilot, pane, "roles", "level_keywords_en", "new grad")   # an edit redraws it
        assert "Can't read the state file." in str(pane.query_one("#chat-note", Static).render())
        n = len(chat_toasts(app))
        app.action_show("dashboard")                                 # (a "1" would go into the entry)
        await pilot.pause()
        app.action_show("settings")                                  # with unsaved edits: one toast
        await pilot.pause()
        await pilot.pause()
        said = [m.message for m in app._notifications][-1:]
        assert len(chat_toasts(app)) == n + 1 and "wasn't read either" in said[0], [m.message for m in app._notifications]


async def test_the_retry_words_follow_unsaved_edits(paths, cfg_path):
    """Review run 3: the note said "ctrl+r retries" after an edit, where ctrl+r offers to revert."""
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        holder = hold_lock(paths.state)
        try:
            pane = await open_settings(pilot)
            await pilot.pause()
            assert "ctrl+r retries" in str(pane.query_one("#chat-note", Static).render())
            await add_keyword(pilot, pane, "roles", "level_keywords_en", "new grad")
            assert pane.dirty
            note = str(pane.query_one("#chat-note", Static).render())
            assert "ctrl+r" not in note and "opening this tab again retries" in note
        finally:
            holder.kill()
            holder.wait()


async def test_the_log_command_ends_the_chat_note(paths, cfg_path):
    """Review run 3: "…-u tele-notify-dryrun Edit chat IDs…": a copy of the command took the advice too."""
    paths.state.write_bytes(b"this is not a database" * 100)
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(200, 50)) as pilot:
        pane = await open_settings(pilot)
        await pilot.pause()
        note = str(pane.query_one("#chat-note", Static).render())
        assert note.startswith("Edit chat IDs directly")
        assert note.endswith("journalctl --user -u tele-notify-dryrun")


@pytest.mark.parametrize("edit", [False, True])
async def test_the_status_says_no_problems_whole_at_80x24(paths, cfg_path, edit):
    """#58: with nothing to fix the status bar said nothing about problems; it says "no problems",
    and the whole line fits the bar at 80 x 24 (saved or with an unsaved change)."""
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    sd.dedupe_all(cfg)                            # the shared config lists a few words twice (warnings)
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        pane = await open_settings(pilot)
        if edit:
            await add_keyword(pilot, pane, "filters", "extra_seniority", "lead")
        await pilot.pause()
        assert pane.problems == []
        status = pane.query_one("#settings-status", Static)
        shown = status.render()
        assert "no problems" in str(shown)
        assert ("unsaved change" in str(shown)) == edit
        assert shown.cell_length <= status.content_region.width, (str(shown), status.content_region.width)


@pytest.mark.parametrize("drop, what", [(("api_id", "api_hash"), "api_id and api_hash are"),
                                        (("api_hash",), "api_hash is")])
async def test_missing_api_keys_are_a_problem_listed_under_bot(paths, cfg_path, drop, what):
    """Review run 2: "no problems · API keys missing" told a first-time user the opposite of the truth
    (the bot can't log in without them). They count as a problem in the bar and are listed under Bot."""
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    sd.dedupe_all(cfg)
    for k in drop:
        del cfg[k]
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        pane = await open_settings(pilot)
        await pilot.pause()
        shown = str(pane.query_one("#settings-status", Static).render())
        assert shown == "saved · 1 problem (Bot)"   # where to look: a first-time user starts on Roles
        listed = str(pane.query_one("#problems-bot", Static).render())
        assert f"{what} missing from config.json" in listed


COUNTS = [0, 1, 12]


async def test_the_status_is_whole_at_80x24_in_every_combination(paths, cfg_path):
    """Review run 3: "1 problem: API keys missing" pushed the warning count off the 50-column bar.
    The bar holds counts only; every mix of unsaved changes, errors, warnings and missing keys (both,
    one, none) is whole at 80 x 24, as the screen draws it."""
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    sd.dedupe_all(cfg)
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        pane = await open_settings(pilot)
        status = pane.query_one("#settings-status", Static)
        base_original, base_edited = pane.original, dict(pane.edited)
        lists = list(sd.LIST_KEYS)[:12]
        assert len(lists) == 12
        for changes in COUNTS:
            for errs in COUNTS:
                for warns in COUNTS:
                    for drop in ((), ("api_hash",), ("api_id", "api_hash")):
                        pane.original = {k: v for k, v in base_original.items() if k not in drop}
                        pane.edited = dict(base_edited)
                        for k in lists[:changes]:
                            pane.edited[k] = [*sd.value(base_edited, k), "zz-new"]
                        pane.problems = ([sd.Problem("max_experience_years", "bad", True)] * errs
                                         + [sd.Problem("certifications", "twice", False)] * warns)
                        pane.update_status()
                        await pilot.pause()
                        full = str(status.render())
                        drawn = status.render_line(0).text.rstrip()
                        case = (changes, errs, warns, drop)
                        assert len(sd.changed_keys(pane.original, pane.edited)) == changes, case
                        assert drawn == full, (case, full, drawn)
                        problems = errs + (1 if drop else 0)
                        assert ("no problems" in full) == (problems == 0 and warns == 0), case
                        if problems:
                            assert f"{problems} problem" in full, case
                        if warns:
                            assert f"{warns} warning" in full, case


def drawn(app) -> str:
    """The screen as drawn (from its SVG, as the audit reads it)."""
    from audit.svg import text
    return text(app.export_screenshot(), *app.size)


def first_time_config(cfg_path: Path, **extra) -> None:
    """The conftest config as a first-time user has it: no API keys, no duplicate words."""
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    sd.dedupe_all(cfg)
    cfg.pop("api_id", None)
    cfg.pop("api_hash", None)
    cfg.update(extra)
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


async def test_a_first_time_user_sees_where_the_problem_is_at_80x24(paths, cfg_path):
    """Review run 4: with no API keys, Settings at 80 x 24 said "saved · 1 problem" on the Roles tab and
    nothing said where. The bar names the section and the Bot tab is marked, as drawn on screen."""
    first_time_config(cfg_path)
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        pane = await open_settings(pilot)
        await pilot.pause()
        status = pane.query_one("#settings-status", Static)
        assert status.render_line(0).text.rstrip() == "saved · 1 problem (Bot)"
        sections = pane.query_one("#sections", TabbedContent)
        assert sections.active == "sec-roles"
        assert str(sections.get_tab("sec-bot").label) == "Bot !"
        assert str(sections.get_tab("sec-roles").label) == "Roles & levels"
        screen = drawn(app)
        assert "Bot !" in screen and "1 problem (Bot)" in screen


async def test_problems_in_two_sections_mark_both_tabs(paths, cfg_path):
    first_time_config(cfg_path, max_experience_years=-2)
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        pane = await open_settings(pilot)
        await pilot.pause()
        status = pane.query_one("#settings-status", Static).render_line(0).text.rstrip()
        assert status == "saved · 2 problems"   # two places: the marked tabs say which
        sections = pane.query_one("#sections", TabbedContent)
        assert str(sections.get_tab("sec-bot").label) == "Bot !"
        assert str(sections.get_tab("sec-filters").label) == "Other filters !"


async def test_the_bot_problems_are_never_cut_at_80x24(paths, cfg_path):
    """Review run 4: the problems list had max-height 5 and no scrollbar, so keys missing, 4 Bot errors
    and a warning lost the warning silently. It shows every line, or a scrollbar and the focus can
    reach it to scroll to the last one."""
    first_time_config(cfg_path, dry_run="x", notify_review="x", alert_after_minutes="x", data_dir=5,
                      tdlib_path="/nonexistent/" + "lib" * 12 + "/libtdjson.so")
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        pane = await open_settings(pilot)
        pane.query_one("#sections", TabbedContent).active = "sec-bot"
        await pilot.pause()
        await pilot.pause()
        listed = str(pane.query_one("#problems-bot", Static).render())
        for want in ("api_id and api_hash are missing", "error: dry_run, notify_review", "error: alert_after_minutes",
                     "error: data_dir", "warning: tdlib_path"):
            assert want in listed, want
        box = pane.query_one("#problems-bot").parent
        assert box.region.height > 0
        if box.max_scroll_y > 0:   # doesn't fit: a scrollbar shows, and the keys can scroll it
            assert box.show_vertical_scrollbar and box.allow_focus()
            box.focus()
            await pilot.press("end")
            await pilot.wait_for_scheduled_animations()   # "end" scrolls with an animation: a loaded run drew it half-way
            await pilot.pause()
        assert "libtdjson.so doesn't exist" in " ".join(drawn(app).split())   # whole, wherever it wraps


async def test_many_problems_scroll_with_a_scrollbar(paths, cfg_path):
    """More problems than the list's room: a scrollbar shows and the end can be reached."""
    first_time_config(cfg_path, dry_run="x", notify_published="x", notify_review="x", alert_after_minutes="x",
                      data_dir=5, alerts="x", tdlib_path="/nonexistent/" + "d" * 150 + "/libtdjson.so")
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(80, 24)) as pilot:
        pane = await open_settings(pilot)
        pane.query_one("#sections", TabbedContent).active = "sec-bot"
        await pilot.pause()
        await pilot.pause()
        box = pane.query_one("#problems-bot").parent
        assert box.max_scroll_y > 0
        assert box.show_vertical_scrollbar and box.allow_focus()
        box.focus()
        await pilot.press("end")
        await pilot.wait_for_scheduled_animations()   # "end" scrolls with an animation: a loaded run drew it half-way
        await pilot.pause()
        assert "libtdjson.so doesn't exist" in " ".join(drawn(app).split())   # whole, wherever it wraps


@pytest.mark.parametrize("api_id, api_hash", [(4242, 123), ("4242", "abc"), (4242, ["abc"]), (True, "abc")])
def test_secrets_the_bot_cant_read_are_missing(api_id, api_hash):
    """The bot reads api_id with getInt(0) and api_hash with getStr(""): a number as api_hash, or text
    as api_id, is missing to it, so the screen doesn't call it set (review run 4)."""
    assert "missing" in sd.secret_status({"api_id": api_id, "api_hash": api_hash}).values()
    assert sd.secret_status({"api_id": 4242, "api_hash": "abc"}) == {"api_id": "set", "api_hash": "set"}


async def test_the_undo_window_is_a_field_in_settings_bot(paths, cfg_path, services):
    """#62, review run 1 (A21): undo_seconds can be changed, with its help text."""
    app = TeleNotifyApp(replace(paths, config=cfg_path), refresh_seconds=0)
    async with app.run_test(size=(120, 40)) as pilot:
        pane = await open_settings(pilot)
        pane.query_one("#sections", TabbedContent).active = "sec-bot"
        await pilot.pause()
        box = pane.query_one("#f-undo_seconds", Input)
        assert box.placeholder == "30"
        assert "0 = at once" in str(pane.query_one("#help-undo_seconds", Static).render())
        box.focus()
        box.value = "601"
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert "600 seconds or fewer" in str(pane.query_one("#problems-bot", Static).render())
        box.value = "0"
        await pilot.pause()
        await save(pilot)
    assert json.loads(cfg_path.read_text(encoding="utf-8"))["undo_seconds"] == 0
