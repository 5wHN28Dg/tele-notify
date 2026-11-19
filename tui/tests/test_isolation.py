"""Tests never reach the owner's real files: the private corpus, the state, the config."""

from __future__ import annotations

import os
import pwd
from pathlib import Path

from telenotify_tui import bot, paths
from telenotify_tui.screens import review_data, settings_data

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


PRIVATE = [REAL_HOME / ".local" / "share" / "tele-notify", REAL_HOME / ".local" / "share" / "tele-notify-corpus",
           REAL_HOME / ".local" / "share" / "tele-notify-dryrun", REAL_HOME / ".config" / "tele-notify",
           REAL_HOME / "Projects" / "bqtechjobs"]


def outside_real_home(p: Path) -> bool:
    """Not in one of the owner's private folders (the temp folder may be under the real home)."""
    p = Path(p).expanduser().resolve()
    return not any(p == d or d in p.parents for d in PRIVATE)


def test_labels_and_corpus_are_not_the_real_ones():
    # a test that presses s / d / k without the labels fixture used to append
    # its synthetic post to the real review-labels.jsonl
    assert outside_real_home(review_data.labels_path())
    assert outside_real_home(bot.default_corpus())


def test_config_and_state_lookups_are_not_the_real_ones():
    assert outside_real_home(paths.default_config())
    assert outside_real_home(paths.default_data_dir())
    assert all(outside_real_home(Path(p)) for p in paths.config_candidates() if Path(p).is_absolute())


def test_settings_corpus_is_not_the_real_one():
    assert outside_real_home(settings_data.corpus_path())
