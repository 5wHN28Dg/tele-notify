# tele-notify terminal screen

A live screen for the bot, built with [Textual](https://github.com/Textualize/textual): open it any time from a terminal and close it again; the bot keeps running either way. Keyboard and mouse both work.

| Tab | Key | What it does |
|---|---|---|
| Dashboard | `1` | Is the bot running and connected, dry run or real; start / stop / restart it; how many posts were judged, published, sent for review, rejected and forwarded over a period you pick, with a chart and the numbers per channel (see below); a live feed of what it just judged; alerts; channels that blocked you or went silent |
| Review | `2` | The review queue: read each unsure post and send (`s`) or discard (`d`) it, or press `k` ("match, don't send") for a post that is a match but already reached your channel another way, for example the old bot forwarded it: it is labelled a match and the bot never sends it. For a moment after a post appears these keys wait, so a double tap can't decide the next post unread. `u` undoes the last choice unless the bot already sent the post; `o` opens the link, `r` shows what the filter read from links. Every choice is also saved as a test label (`~/.local/share/tele-notify-corpus/review-labels.jsonl`): send and `k` as a match, discard as not a match. In a dry run nothing is ever forwarded: send only records your choice, and the screen says so |
| Search | `3` | Search everything the bot judged and see why; re-judge a post with the current settings (`j`, or `J` to follow links); paste any post to try it (`p`) |
| Settings | `4` | Edit keywords, places, watched chats (by name), the target chat and the bot options; test a change on the corpus before saving (`t`); save (`ctrl+s`) with a backup, then restart the bot to apply it (offered only while it runs). Lists are explained below |

`ctrl+r` refreshes the tab, `ctrl+q` quits (it asks first if Settings has unsaved changes).

### The tab table

What each tab is for, used by the [UI and UX policy](../docs/UI-UX-POLICY.md) (rules R1, R2, R13 and R14). Draft: the owner approves it. Rows say what must be true; today's gaps are listed in the first audit.

| Tab | Main tasks | Must be visible at 80 × 24 (no scrolling, no focus change) | Main action: key / click |
|---|---|---|---|
| Dashboard | see whether the bot is healthy; start / stop / restart it; check alerts; read the numbers for a period (pick it) | service state (running / stopped) and the Start, Stop and Restart buttons; dry run or real; Telegram connection; pending reviews; alert count and age of the last one (or "no alerts"); the period name and its judged / publish / review / reject / forwarded counts | `p` picks the period / click the period picker |
| Review | read the next unsure post and decide: send, discard, match no send (`k`), undo; open its link | dry run or real; how many are waiting; the post's chat, reasons and the start of its text; the Send, Match no send and Discard buttons, and Undo after a decision | `s` sends / click Send |
| Search | find a judged post and see why it got its verdict; re-judge it | the search box; the first results with their verdicts; the chosen result's verdict and reasons | typing in the search box searches / click a result row opens it |
| Settings | add, edit or delete a keyword; watch or unwatch a chat; save or revert; test on the corpus | the saved / unsaved marker and the problem count in the status bar; Save, Test, Revert; in the list sections: the list picker, the chosen list, Edit / Delete / Add; in Chats: the chat table | `ctrl+s` saves / click Save |

### Numbers over a period

The Activity box on the Dashboard counts posts over a period: today, the last 7 days (the default), the last 30 days, this month, this year, all time (since the first post the bot judged; the date is shown under the box) or a custom range. Press `p` (or click the picker) to choose one; press `d` (or click `Dates…`, or pick "Custom range…") to type a start and end day as `YYYY-MM-DD`. Both days are included, whole, in local time. Every period is made of whole local days: "the last 7 days" is today and the 6 days before it, from midnight, so it can cover up to a day more than `telenotify stats 7`, which counts the last 7 × 24 hours. If the first post's time in the state file is unreadable, all time says "start unknown" and starts at the earliest readable one. Picking "Custom range…" again while a custom range is shown opens the dates form again. The per-channel table (`c`) follows the same period.

"Forwarded" is what reached your channel: posts published straight away plus reviews you approved once the bot has sent them (not pending, discarded or only kept ones). A dry run forwards nothing. An album counts as one post.

The chart shows one bar per day while the period fits (up to 46 days), then per week, month or year. The numbers come from the bot's state file, which keeps a row for every judged post for good (only the post text is cleared after `keep_text_days`), so they survive restarts. Over a long period on a very large state file (a million posts takes about a second) the numbers are counted again at most once a minute instead of every few seconds.

### Editing a keyword list (Settings)

Pick a list on the left, then work in the list on the right:

- **Pick an item**: click it, or use the arrow keys. In the filter box, type part of a word to narrow the list, then press down or enter to move into it (in the filter box, delete and backspace only edit the filter text).
- **Delete**: `del` or `x` on the picked item, or the Delete button.
- **Edit**: `enter` or a double click on the item, or the Edit button; change the text in the bottom box and press enter (`esc` cancels).
- **Add**: `a` or the Add button (or click the bottom box), type the word and press enter.
- **Duplicates**: a list with the same word twice (ignoring case and spacing) gets a warning naming the list and the word. `D` (shift+d, outside a text box) or the Remove duplicates button keeps the first spelling of each word in every list (except the job sites list, where only the lower-case spelling works: delete the wrong one yourself).

Nothing is written until you save with `ctrl+s`.

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

Terminals draw each line left to right in the order the characters are stored, so on its own an Arabic sentence would come out with its words in the wrong order. The screen puts right-to-left text in order itself: each paragraph is wrapped first and then reordered line by line, Arabic paragraphs are right-aligned, mixed Arabic / English / numbers land in the right places, brackets are mirrored, and emoji and accents stay whole. Terminals differ in what they do with Arabic, so there are three modes:

| Mode | For | What the screen does |
|---|---|---|
| `full` | most terminals: Alacritty, xterm, GNOME Terminal, Ptyxis (VTE) | puts every line in visual order and joins the letters itself. While it is open it switches the terminal to *explicit* bidi mode (`ESC [ 8 l`, from the [Terminal-WG BiDi recommendation](https://terminal-wg.pages.freedesktop.org/bidi/)) so VTE doesn't reverse the text again, and back to the usual *implicit* mode (`ESC [ 8 h`) when it closes, also after a crash. Terminals that don't know the mode ignore it |
| `words` | kitty | kitty joins the letters and draws each Arabic word right to left by itself, but keeps the words in stored order (its `force_ltr` docs: "HELLO WORLD" in an RTL script shows as "WORLD HELLO"). So only the word order is changed, and each word is left for kitty to draw. Nothing is sent to the terminal |
| `off` | a terminal whose own bidi you prefer | text as stored, the terminal left alone |

The mode is picked by itself: `words` in kitty (`TERM=xterm-kitty`, which ssh passes on, or `$KITTY_WINDOW_ID` inside a tmux or screen started from kitty; another terminal started from a kitty shell inherits `$KITTY_WINDOW_ID` too, so on its own it doesn't count), `full` anywhere else. An unknown `TELENOTIFY_BIDI` value is reported when the screen starts, and the automatic choice is used. To choose:

```bash
TELENOTIFY_BIDI=words .venv/bin/telenotify-tui --config:/path/to/config.json   # full, words, off or auto
.venv/bin/telenotify-tui --config:/path/to/config.json --bidi=words            # the flag wins over the variable
.venv/bin/telenotify-tui --config:/path/to/config.json --no-bidi               # the same as --bidi=off
```

- With `force_ltr yes` in kitty.conf kitty draws cells as they are: use `--bidi=full` there.
- Inside tmux or screen the `ESC [ 8 l` switch doesn't reach the outer terminal, and the auto choice only sees tmux's `TERM`. In tmux inside GNOME Terminal or Ptyxis, VTE then reorders the screen's already-ordered text again: use `--bidi=off` there and let VTE do it. Elsewhere, if Arabic looks reversed, pick the mode yourself.
- kitty shapes the text between spaces and emoji as one piece in one direction, so a word that starts with Arabic and ends with Latin letters with no space between ("بايثونPython" in an Arabic sentence) can't come out right there.
- If the screen is killed hard (`kill -9`) in `full` mode and Arabic in that terminal looks wrong afterwards, `printf '\e[8h'` restores it.
- Selecting a post with the mouse and copying it gives the text as stored (reading order, plain letters), not the drawn order. Short values in tables and headers copy as drawn.
- Fields you type into (keyword entry, the paste box) are not reordered: they show text in stored order so the cursor stays where you type.

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
