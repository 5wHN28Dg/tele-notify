# tele-notify terminal screen

A live screen for the bot, built with [Textual](https://github.com/Textualize/textual): open it any time from a terminal and close it again; the bot keeps running either way. Keyboard and mouse both work.

| Tab | Key | What it does |
|---|---|---|
| Dashboard | `1` | Is the bot running and connected, dry run or real; start / stop / restart it; posts judged today and this week; a 14-day chart; numbers per channel; a live feed of what it just judged; alerts; channels that blocked you or went silent |
| Review | `2` | The review queue: read each unsure post and send (`s`) or discard (`d`) it (for a moment after a post appears these keys wait, so a double tap can't decide the next post unread); `u` undoes the last choice unless the bot already sent the post; `o` opens the link, `r` shows what the filter read from links. Every choice is also saved as a test label (`~/.local/share/tele-notify-corpus/review-labels.jsonl`) |
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

`"dry_run": true` in config.json also means the dry run, as it does for the bot. The state file is worked out once at start from `data_dir` (blank means `~/.local/share/tele-notify`; a relative path is relative to the config file's folder), so after changing `data_dir` or `dry_run` in Settings, reopen the screen.

### Arabic and other right-to-left text

Terminals draw each line left to right in the order the characters are stored, so on its own an Arabic sentence would come out with its words in the wrong order. The screen puts right-to-left text in order itself: letters joined, each line reordered after it is wrapped, Arabic paragraphs right-aligned, mixed Arabic / English / numbers in the right places, brackets mirrored. That works the same in terminals that do no bidi of their own (Alacritty, kitty, xterm).

Terminals that do reorder text themselves (VTE: GNOME Terminal, Ptyxis) would undo that, so while the screen is open it switches the terminal to *explicit* bidi mode (`ESC [ 8 l`, from the [Terminal-WG BiDi recommendation](https://terminal-wg.pages.freedesktop.org/bidi/)) and back to the usual *implicit* mode (`ESC [ 8 h`) when it closes, also after a crash. Terminals that don't know the mode ignore both. If the screen is killed hard (`kill -9`) and Arabic in that terminal looks wrong afterwards, `printf '\e[8h'` restores it.

If your terminal does its own bidi well and you'd rather it did the work, turn the screen's off:

```bash
TELENOTIFY_BIDI=off .venv/bin/telenotify-tui --config:/path/to/config.json
.venv/bin/telenotify-tui --config:/path/to/config.json --no-bidi    # the same
```

Fields you type into (keyword entry, the paste box) are not reordered: they show text in stored order so the cursor stays where you type.

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
