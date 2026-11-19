"""Tests never reach the owner's real files: the private corpus, the state, the config."""

from __future__ import annotations

import dataclasses
import os
import pwd
from pathlib import Path

from test_review import open_review

from telenotify_tui import bot, paths
from telenotify_tui.app import TeleNotifyApp
from telenotify_tui.screens import review_data, settings_data

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


PRIVATE = [REAL_HOME / ".local" / "share" / "tele-notify", REAL_HOME / ".local" / "share" / "tele-notify-corpus",
           REAL_HOME / ".local" / "share" / "tele-notify-dryrun", REAL_HOME / ".config" / "tele-notify",
           REAL_HOME / "Projects" / "bqtechjobs"]


def outside_private(p: Path) -> bool:
    """Not in one of the owner's private folders (the temp folder may be under the real home)."""
    p = Path(p).expanduser().resolve()
    return not any(p == d or d in p.parents for d in PRIVATE)


def test_labels_and_corpus_are_not_the_real_ones():
    # a test that presses s / d / k without the labels fixture used to append
    # its synthetic post to the real review-labels.jsonl
    assert outside_private(review_data.labels_path())
    assert outside_private(bot.default_corpus())


def test_config_and_state_lookups_are_not_the_real_ones():
    assert outside_private(paths.default_config())
    assert outside_private(paths.default_data_dir())
    assert all(outside_private(Path(p)) for p in paths.config_candidates() if Path(p).is_absolute())


def test_settings_corpus_is_not_the_real_one():
    assert outside_private(settings_data.corpus_path())


async def test_deciding_a_post_writes_its_label_under_the_test_home(paths, state):
    # the original leak, end to end: no labels fixture, press s on Review
    home = Path(os.environ["HOME"])
    app = TeleNotifyApp(dataclasses.replace(paths, dry_run=False), refresh_seconds=0)
    async with app.run_test() as pilot:
        await open_review(pilot)
        await pilot.press("s")
        await pilot.pause()
    written = home / ".local" / "share" / "tele-notify-corpus" / "review-labels.jsonl"
    assert written.exists()
    assert outside_private(review_data.labels_path())
