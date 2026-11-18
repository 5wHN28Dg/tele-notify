"""The Settings screen, driven through the app like a user would."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from textual.widgets import DataTable, Input, OptionList, Static, TabbedContent

from telenotify_tui import bot
from telenotify_tui.app import TeleNotifyApp
from telenotify_tui.screens import settings_data as sd
from telenotify_tui.screens.settings import ConfirmModal, EvalModal, KeywordEditor, SettingsPane

from conftest import BAD, GOOD


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

    monkeypatch.setattr(bot, "service_action", fake)
    return calls


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
        await pilot.press("ctrl+s")
        await pilot.pause()
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
        await pilot.press("ctrl+s")
        await pilot.pause()
        await pilot.press("y")
        await app.workers.wait_for_complete()
        await pilot.pause()
    assert services == [("dry-run", "restart")]


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
        await pilot.press("ctrl+s")
        await pilot.pause()
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
    assert seen["edited"]["api_hash"] == "do-not-show"           # the temp copy is a working config
    assert not seen["path"].exists()                             # temp copy (with secrets) removed


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
