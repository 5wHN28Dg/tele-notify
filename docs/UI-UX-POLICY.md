# UI and UX policy: the terminal screen

Status: draft (2026-11-18, the owner asked for it). Reviewed blind, then adversarially; adopted when the owner
approves it.

This policy says what the terminal screen (`tui/`, the Textual app `telenotify-tui`) must do, and how a change to it is
checked. It is modelled on the owner's kks-explorer policy, cut down to a one-person terminal app: the method
(checkable rules, evidence, two audit passes, a results table, merge blockers) is the same; touch, phones, several
platforms and screen readers are left out because they don't apply.

The bot itself (Telegram messages, `telenotify` command output) is not covered, except where the screen shows it.

## 1. Who uses it, and where

- **One user, the owner:** a programmer and fresh graduate who runs the bot on their own laptop and opens the screen
  now and then to check it, work through the review queue, search and tune keywords. They shouldn't need to remember
  keys: the screen shows them or shows where to find them.
- **Terminal:** Kitty 0.47 on Ubuntu GNOME is the main one; Ptyxis / GNOME Terminal (VTE) and Zed's built-in terminal
  are possible. All are truecolour. Window sizes vary; 80 × 24 is the smallest supported, 120 × 40 is typical.
- **Themes:** Textual's default `textual-dark`; `textual-light` is supported but not the owner's usual one.
- **Keyboard and mouse** are both first-class.
- **Text:** the UI is English. Posts, chat titles and keywords are Arabic, English or mixed, with emoji, numbers,
  links, emails and phone numbers, and come from strangers: they are untrusted.
- **Data is private:** the owner's chat list, state files, review queue, config and the labelled corpus
  (`~/.local/share/tele-notify-corpus/`) never go into screenshots, issues, PRs or the wiki (both are public).
- **The screen sits beside a running bot:** it must never block the bot, corrupt its state file or config, or make
  the bot do something the owner didn't ask for.
- **The review queue is read one post at a time on purpose** (each decision is also a corpus label), so there is no
  bulk-decision rule.

## 2. The rules

Each rule is a check with a pass or a fail. **Block** means a fail stops a merge (section 4); **Warn** means it is
recorded and gets an issue, but doesn't block. Each rule ends with how it is checked, using the harness in 3.3.

**Main tasks** (used by R1 and R2): Dashboard: pick a period, start / stop / restart the bot, read alerts; Review:
read a post, send, discard, keep (`k`), undo; Search: search, open a result; Settings: pick a list, add, edit, delete
a keyword, watch / unwatch a chat, save, revert.

### R1 Every action by keyboard and by mouse (Block for main tasks, Warn for the rest)

1. Every action can be done with the keyboard alone. Every main task can also be done with the mouse alone (typing
   text excepted), with a button, a click on a footer key, or the command palette (`ctrl+p`). Checked at 120 × 40.
2. Focus is always visible, and `tab` / `shift+tab` follow the visible layout (no jumps back and forth across the
   screen); focus never lands in a hidden tab or behind a dialog.
3. A key shown in the footer works where it is shown; a key that can't act right now is dimmed or hidden, never shown
   as working.
4. Printable keys, `delete` and `backspace` never trigger actions while the user types in a text box; they edit the
   text.
5. `esc` closes the top layer (a dialog, an open drop-down list, an edit); there is no focus trap. Number keys `1`–`4`
   switch tabs from anywhere outside a text box, a dialog or an open drop-down list. `ctrl+q` quits from anywhere
   (asking first per R5.1).
6. Every key either shows in the footer or is listed in the keys panel, which is reachable from the footer (a key
   such as `?`, or the command palette's "Keys" entry).

Check: a pilot per tab that walks `screen.focus_chain`, records the footer and `app.active_bindings` for each focused
widget, and presses each listed key (in the isolated harness) to confirm it acted; typing every printable bound key,
`delete` and `backspace` into each text box changes only its text; a clicks-only pilot that does each main task.

### R2 Fits the terminal (Block at 80 × 24, Warn at 120 × 40)

1. At 80 × 24 every button and every tab's main content can be seen: a panel may scroll if it shows a scrollbar and
   every control in it scrolls into view when focused; nothing overlaps; no button is cut off. The footer may be cut
   off at the right (R1.6 still reaches every key).
2. At 120 × 40, no text that was cut off at 80 × 24 is still cut off while its row has room. (Warn)
3. Resizing while the screen is open keeps the focused widget, the selected item (kept in view) and unsaved text.

Check: screenshots at 80 × 24 and 120 × 40; a pilot that resizes from 120 × 40 to 80 × 24 and back
(`pilot.resize_terminal`) with a selection and an unsaved edit.

### R3 Arabic and mixed text read correctly (Block)

1. Arabic paragraphs read right to left and are right-aligned; mixed Arabic, English, numbers, links, emails, phone
   numbers, brackets and emoji come out in reading order, in each bidi mode (`full`, `words`; see `tui/README.md`).
2. Wrapping never loses, repeats or swaps characters or lines.
3. Text selected with the screen's own selection gives the text as stored: logical order, no joined letter forms,
   blank lines kept. (The terminal's own shift+drag selection copies screen cells; that is outside the app and N/A.)
4. A post can't change the layout of anything outside its own area (bidi control characters, very long words,
   combining marks).

Check: `tui/tests/test_rtl.py` and the evidence pack's synthetic posts, run with `TELENOTIFY_BIDI=full` and again with
`words` (never left to auto-detection, so every auditor gets the same result); `screen.get_selected_text()` after a
`triple_click` or a set `screen.selections`. How it really looks in Kitty or VTE is an accepted Not checked (3.3).

### R4 Feedback in plain words (Block for 1–4, Warn for 5)

1. Every action that changes saved data or the bot, or starts background work, says what happened, in plain words
   without a status code or stack trace.
2. An error says what went wrong and what to do next; nothing fails silently (no `except` that turns a failure into
   an empty list or a blank panel without saying so).
3. Whether the screen shows the **dry run** or the **real** bot is always visible as a word ("DRY RUN" / "REAL"), and
   in the dry run every action that would send something says that nothing is sent.
4. An error message stays until dismissed or is also shown in place; a message never covers the focused control.
5. Anything that takes over a second shows that it is working and the rest of the screen keeps responding; work over
   10 s shows progress where it can be known and can be cancelled or left running.

Check: a pilot that does every action on every tab and records each notification and status line; the R10 failures
for every error path; toast timeout and position at 80 × 24.

### R5 Safe changes (Block)

1. Nothing is written to `config.json` until the user saves; saving keeps a backup; unsaved changes are shown, and
   quitting or reverting with them asks first.
2. A choice that can't be taken back (stop or restart the bot, quit with unsaved edits, discard a review the bot may
   already have acted on) asks first or can be undone, and the undo is shown.
3. A confirmation names the action on its button ("Stop the bot", not "Yes"), starts with focus on the safe choice,
   and an `enter` pressed straight away never does the action; a decision key waits a moment after a new item
   appears (the review queue's `DECIDE_DELAY`) so a double tap can't decide the next item unread.
4. A one-key bulk change (for example remove duplicates) only does what its label says, names what it changed, and
   is not saved until the user saves.
5. The screen writes to the bot's state only to decide reviews, and never sends anything to Telegram itself.
6. A field error appears at the field, names it, and keeps what was typed.

Check: per dialog, a pilot that opens it and presses `enter` at once (the action must not happen), with the fake
`systemctl` answering "active" so the stop / restart dialogs appear and the real `DECIDE_DELAY` in force; scripts for
quit / revert with unsaved edits, undo after each decision, dedupe, invalid input in every form; a `sqlite3 .dump`
diff of the state before and after a decision shows only that review row's status and time changed; the config file
is unchanged until save.

### R6 Readable colours (Block in `textual-dark`, Warn in `textual-light`)

Checked on every change to `app.tcss`, a widget's CSS, the theme or a new kind of widget; otherwise N/A for a PR.

1. Text has a contrast of at least 4.5 : 1 against what is behind it; focus marks, the selected-row cursor and
   the borders of focused controls at least 3 : 1. Exempt: disabled or dimmed controls, placeholders, and unfocused
   decorative borders. A stock Textual style the project hasn't changed carries its fails as pre-existing, noted in
   the audit file (not filed upstream without the owner's approval).
2. Colour is never the only signal: a verdict, problem, warning or selected row also has a word, a symbol, bold,
   underline or reverse video. A background colour alone doesn't count.

Check: colours read from screenshot SVGs taken with the cursor blink off, in these states: focused, unfocused
selection, error, warning, zebra rows; the contrast ratio computed with Textual's `Color`; a greyscale copy in which
every state can still be told apart.

### R7 Fast enough (Block for 1, Warn for 2 and 3)

1. No event-loop stall over 1 s while typing, moving the selection, switching tabs or refreshing, including while
   the bot holds a write lock on the state file.
2. No stall over 100 ms in the same interactions.
3. The first tab shows within 2 s of starting; a refresh that would take longer keeps showing the old numbers until
   the new ones are ready.

Measured with asyncio debug mode (`loop.slow_callback_duration`) during the harness's scripted interaction (switch
through the tabs, scroll the review queue and the feed, run three searches, pick each period) on the harness's fixed
synthetic state file (1 000 000 judged posts, 2 000 reviews, 300 chats); the result is the median, over 3 runs, of
each run's longest stall. The load of the machine is recorded with the result; the owner's services and jobs are
never stopped to get a quieter machine.

### R8 Consistent (Warn)

1. The same action has the same key and the same word on every tab (`enter` opens, `esc` closes, `ctrl+r`
   refreshes, `o` opens a link).
2. The words for verdicts, actions and states match the bot's (`publish`, `review`, `reject`, `forwarded`) and the
   README.
3. Times are `YYYY-MM-DD HH:MM` (or `HH:MM` for today), 24-hour, local time.
4. Counts are grammatical ("1 problem", "2 problems"); an empty list says why and what to do.

Check: a table of every binding per tab from the R1 pilot; a text scan of the screenshots and of the strings in
`tui/telenotify_tui/`.

### R9 Untrusted text stays text (Block)

Post text, chat titles, keywords, config values and the output of `telenotify` and `systemctl` are never read as
Textual or Rich markup, and never reach the terminal as their own escape sequences (colours, cursor moves, OSC 8
links, OSC 52 clipboard writes, title changes). This covers every place text is shown: tabs, dialogs (confirmations,
the corpus test result), drop-down lists, the keys panel, tooltips, notifications and the header.

Check: the method of `tui/tests/test_untrusted_text.py`: payloads carrying unique markers (`[b]x[/b]`, `[link=…]`,
`[@click=…]`, an unbalanced `[`, ANSI CSI, C1 controls, OSC 8 and OSC 52) in posts, titles, keywords, config values
and fake subprocess output, shown in each of those places in a pty run; the output stream must have no payload ESC or
C1 byte next to a marker, and the markers must show as plain text.

### R10 Degrades gracefully (Block)

Each tab shows a plain-words state, never a crash, a blank tab or an endless spinner, when: the bot is stopped or was
never started; the state file is missing, has a missing table, or is locked; `config.json` is missing or invalid; the
`telenotify` program or `systemctl --user` isn't available. The Dashboard shows how old its numbers are and says so
when the bot has stopped updating its status (Warn).

Check: a pilot per tab for each condition, simulated in the harness with temporary files and the fake commands, never
with the owner's services or files.

### R11 Values can be copied (Block)

Links, emails and phone numbers in posts, chat IDs and keywords can be copied exactly, by the screen's selection or a
copy key, and the screen confirms what was copied. Where the copy can't reach the system clipboard (a terminal that
ignores OSC 52), the confirmation says so or a fallback (`wl-copy`) is used. Textual's stock `ctrl+c` copy, which gives
no confirmation, is a pre-existing fail.

Check: a pilot selection or copy key on each kind of value; `app.clipboard` compared with the stored value; the
confirmation text.

## 3. The audit

### 3.1 When

- **For every PR that changes `tui/telenotify_tui/`** (code or CSS), and for bot PRs that change the state file's
  tables or the `meta` keys the screen reads. Filter changes that only change reason wording need none.
- A **wording-only** change needs one audit pass with 80 × 24 dark-theme screenshots of the changed screens.
- **A full audit** (every rule, every tab) once now that the policy is adopted (results in
  `docs/ui-audit/2026-11.md`), again before the switch from the Python bot (PR #39), and after any change that
  reworks a whole tab.
- No calendar schedule: with one user and one platform, audits follow changes.

### 3.2 Who

Two fresh subagents, one after the other, neither told what the author thinks. For a PR they are the same two review
passes the project already uses (blind, then adversarial), given this policy and the evidence pack as well as the
diff.

1. **Blind audit:** records a result for every rule the change touches.
2. **Adversarial audit:** given pass 1's table too, tries to make the change fail: 80 × 24, the light theme, keyboard
   only, mouse only, the longest synthetic Arabic post, the large state file, a locked state file, a stopped bot, a
   broken config, hostile text (R9), all simulated in the harness.
3. **After fixes:** a fresh subagent re-checks the rules that failed and the rules the fix's screens touch, on new
   evidence. A fail stands until such a check says it's gone. After two rounds of fixes that don't clear a fail, the
   owner is asked.

Auditors don't change the repository; they run the harness and may write throw-away tests outside it, following the
machine's memory rules.

### 3.3 The harness and the evidence pack

Evidence is collected only in the **isolated harness** (`tui/audit/`, added with the first full audit; until it
exists, its steps below are followed by hand as a checklist). Every evidence run:

- computes the real home with `pwd.getpwuid(os.getuid()).pw_dir` **before** changing anything, and refuses to start if
  any path it will use (symlinks followed) is under `~/.local/share/tele-notify*`, the corpus folder,
  `~/tele-notify` or `~/Projects/bqtechjobs`;
- runs with a temporary `HOME`; the config at the fixed path `/tmp/tn-audit/config.json` (so screenshots show no
  user name) with no `api_id` / `api_hash` and its `data_dir` inside the temporary home; `TELENOTIFY_LABELS` and
  `TELENOTIFY_CORPUS` inside it; `TELENOTIFY_BIN` set to the repository's build or a fake;
- unsets `DISPLAY`, `WAYLAND_DISPLAY`, `DBUS_SESSION_BUS_ADDRESS` and `XDG_RUNTIME_DIR`, sets `BROWSER` to a fake that
  records the URL and exits 0, and puts fakes for `systemctl`, `xdg-open` and `gio` first on `PATH`; the fake
  `systemctl` answers active, inactive, failed or missing per service as the script asks, and logs every call;
- does not use the test suite's autouse fixtures (they switch off `DECIDE_DELAY` and always report the bot inactive);
- builds the large state file once, under `~/.cache/agent-tmp/tn-audit/` (not `/tmp`, which is memory), and reuses it.

Data is synthetic only: invented posts (not edited copies of real ones), chats and keywords; links and emails at
`example.com`; invented phone numbers. A problem first seen on real data is reproduced on synthetic data before it is
filed. Before anything is published, every SVG and text file is searched for the login name and the real home path.

The pack, only for the rules and tabs the change touches:

| What | How |
|---|---|
| Screenshots | `app.save_screenshot()` (SVG) at 80 × 24 and 120 × 40 in `textual-dark`; `textual-light` only for R6 |
| Keys | the footer and `app.active_bindings` per focused widget (R1) |
| Timing | the R7 interaction's slow-callback log |
| Colours | the R6 states, read from the SVGs |
| Hostile text and failures | the R9 and R10 fixtures |

**Accepted Not checked reasons:** how it really looks in Kitty or VTE, a real mouse drag, and whether the terminal
puts an OSC 52 copy on the clipboard. These are listed in the PR for the owner to look at in their own terminal.

### 3.4 How results are recorded

For a PR: a table in a PR comment from each pass, one row per rule that applies, each cell one of **Pass**, **Fail**
(where and what), **Pre-existing fail** (already on main, with evidence from main, and an open issue or a waiver),
**N/A** (why), **Not checked** (why).

For a full audit: the same table for every rule and tab in `docs/ui-audit/YYYY-MM.md`, with the screenshots beside
it in `docs/ui-audit/YYYY-MM/`, and one issue per group of fails.

## 4. What blocks a merge

A PR that changes the screen merges only when its audit passes are done and:

- no **Block** rule fails because of the change;
- no **Block** rule is **Not checked**, unless the reason is an accepted one (3.3) or the owner accepts it for that
  change;
- a fail already on main doesn't block if there is evidence from main, it has an open issue or a waiver, and the
  change doesn't make it worse ("worse" means the gap to the limit grows, or the change itself adds a failing
  control, not counting a stock Textual widget used unchanged).

**Warn** fails get an issue and don't block. An audit that finds a fail with no issue files one. Before the switch
(PR #39), the Block fails of R3, R5, R9 and R10 found by the first full audit are fixed; the others stay as issues.

## 5. Changing this policy

The owner decides. A change to a rule is reviewed the same way as the first version (blind, then adversarial); a rule
that gets stricter applies to new changes only, and existing fails get issues from the next full audit.

Waivers are the owner's call, need no review, and are listed here with the date, the rule, the case and the reason:

- none yet.
