# tele-notify terminal screen

A live screen for the bot, built with [Textual](https://github.com/Textualize/textual): open it any time from a terminal and close it again; the bot keeps running either way. Keyboard and mouse both work.

| Tab | Key | What it does |
|---|---|---|
| Dashboard | `1` | Is the bot running and connected, dry run or real; start / stop / restart it; posts judged today and this week; a 14-day chart; numbers per channel; a live feed of what it just judged; alerts; channels that blocked you or went silent |
| Review | `2` | The review queue: read each unsure post and send (`s`) or discard (`d`) it; `o` opens the link, `r` shows what the filter read from links. Every choice is also saved as a test label (`~/.local/share/tele-notify-corpus/review-labels.jsonl`) |
| Search | `3` | Search everything the bot judged and see why; re-judge a post with the current settings (`j`, or `J` to follow links); paste any post to try it (`p`) |
| Settings | `4` | Edit keywords, places, watched chats (by name), the target chat and the bot options; test a change on the corpus before saving (`t`); save (`ctrl+s`) with a backup, then restart the bot to apply it (offered only while it runs) |

`ctrl+r` refreshes the tab, `ctrl+q` quits (it asks first if Settings has unsaved changes).

## Install and run

```bash
cd tui
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/telenotify-tui --config:/path/to/config.json            # the real bot
.venv/bin/telenotify-tui --config:/path/to/config.json --dry-run  # the dry run
```

It finds the `telenotify` program next to the config, in `~/tele-notify`, on `$PATH`, or at `$TELENOTIFY_BIN`. Start and stop use the systemd user services `tele-notify` and `tele-notify-dryrun` (see `deploy/`).

## How it fits with the bot

The Nim bot does all the work. This screen only reads and writes what the bot already keeps on disk:

- the bot's SQLite state (`state.sqlite3`, or `state-dryrun.sqlite3`): what was judged, the review queue, alerts, your chats, status values;
- `config.json`;
- the `telenotify` program itself, for anything that needs the filter (explain, re-judge, corpus test), so the screen and the bot can't disagree about a verdict.

Approving a post here does what `telenotify review` does: it marks the post, and the running bot sends it within a few seconds.

## Tests

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/ruff check --target-version py311 .
```

Tests never touch the real services, config or data: they use a temporary state file and a fake `systemctl`. Tests that run the real filter need `telenotify` built in the repository root (`nimble build -d:release`) and are skipped otherwise.
