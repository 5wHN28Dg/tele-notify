# UI and UX policy: the terminal screen

Status: adopted 2026-11-18 by the owner, after a blind and an adversarial review. Revised 2026-11-18 in PR #50
(owner's critique: owner sign-off, tab table, R13, R14, scope and loophole fixes), approved by the owner.

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

**Main tasks and what must be visible** come from the **tab table** in `tui/README.md`: one row per tab with its
main tasks, what must be visible at 80 × 24 with no scrolling or focus change, and the key and the click that do the
main action. R1, R2 and R13 use it. Auditors use the table as it is on main; a change to the table needs the owner's
OK in that PR (3.2, "owner's words"), is reviewed as a UI change (both passes look at what it drops), and takes effect
for later PRs. Until the owner approves the first table, the draft in `tui/README.md` is used. A PR that adds a tab or
a main task adds its row; until the row is approved, the new tab's actions are audited as main tasks.

**Visible** means the whole widget is drawn: its clipped region (`screen.find_widget(w)`) equals its region, every
ancestor has `display` on, and its label appears in the screenshot text. A region inside the screen is not enough.
Each must-be-visible item is checked in the state its row names (by default as the tab first opens; "after a
decision" or "in Chats" right after the shortest key path that reaches it), on the evidence pack's synthetic data with
its longest values, in both the dry run and the real mode.

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
   switch tabs (one per tab) from anywhere outside a text box, a dialog or an open drop-down list. `ctrl+q` quits from anywhere
   (asking first per R5.1).
6. Every key either shows in the footer or is listed in the keys panel, which is reachable from the footer (a key
   such as `?`, or the command palette's "Keys" entry).

Check: a pilot per tab that walks `screen.focus_chain`, records the footer and `app.active_bindings` for each focused
widget, and presses each listed key (in the isolated harness) to confirm it acted; typing every printable bound key,
`delete` and `backspace` into each text box changes only its text; a clicks-only pilot that does each main task.

### R2 Fits the terminal (Block at 80 × 24, Warn at 120 × 40)

1. At 80 × 24 everything in the tab table's "must be visible" column is visible with no scrolling or focus change;
   anything else may sit in a panel that scrolls, if it shows a scrollbar and every control in it scrolls into view
   when focused; nothing overlaps; no button is cut off. The footer may be cut
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
   decorative borders. A stock widget a change adds counts as that change's own fail; a stock widget already on main
   is a pre-existing fail only with the evidence and the issue in this repository that section 4 asks for (noted in the audit file, not filed
   upstream without the owner's approval).
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
5. Verdicts, statuses and problems use the same style (colour variable, bold, symbol) on every tab.

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

### R12 (folded into 3.3)

The rules for private, side-effect-free evidence are in 3.3, since they bind the auditors, not the screen.

### R13 Task cost (Warn)

Applies to the Search and Settings tabs (reviewing is slow on purpose), for PRs that touch them. The harness keeps one
keyboard script and one mouse script per main task of those tabs in the tab table (`tui/audit/tasks/`); a PR that
changes a task's path updates its script. Each script ends by asserting the task's end state (the config saved with
the keyword, the search result opened); a script that doesn't reach it is a fail. The keys pressed and the
clicks are counted on main and on the branch, on synthetic data; typed text counts as one key, and waits such as
`DECIDE_DELAY` don't count. A rise in either count needs a reason in the PR's results table; a rise with no reason
is a fail.

Check: the harness's task-cost table from main and from the branch.

### R14 Visual consistency (Warn)

1. Colours in `tui/` CSS and in styles or markup in code are theme variables (`$primary`, `$accent`, `$success`,
   `$warning`, `$error`, `$text-muted`, …) or values read from `app.theme_variables`, never literal colours
   (`dim`, `bold`, `reverse` aren't colours).
2. Padding, margin and `grid-gutter` use only 0, 1 or 2 cells.
3. Each tab view has at most one `variant="primary"` button or `$primary`-coloured heading (borders and border
   titles don't count), and if there is one it is the tab table's main action. A tab view is what a tab shows without
   opening a dialog; a mode the tab switches to (Search's paste mode, a Settings section) is its own view.

Check: the harness's style scan of `tui/`, and a DOM query per tab view for primary buttons and headings.

Whether the layout looks right (hierarchy, spacing, what stands out) is not an auditor rule: a subagent judging it
would file issues about taste that isn't the owner's. It is the owner's sign-off (3.2).

## 3. The audit

### 3.1 When

- **For every PR that changes `tui/telenotify_tui/`** (code or CSS), and for bot PRs that change the state file's
  tables or the `meta` keys the screen reads. Filter changes that only change reason wording need none.
- A **wording-only** change still gets both passes, but its evidence is only 80 × 24 dark-theme screenshots of the
  changed screens.
- **A full audit** (every rule, every tab) once now that the policy is adopted (results in
  `docs/ui-audit/2026-11.md`), again before the switch from the Python bot (PR #39), and after that when the owner
  asks, at least once a year. A PR that reworks a tab gets the normal PR audit for the rules and tabs it touches,
  plus the owner's sign-off, not a full audit.
- No calendar schedule: with one user and one platform, audits follow changes.

### 3.2 Who

Two fresh subagents, one after the other, neither told what the author thinks. For a PR they are the same two review
passes the project already uses (blind, then adversarial), given this policy and the evidence pack as well as the
diff.

1. **Blind audit:** records a result for every rule the change touches.
2. **Adversarial audit:** given pass 1's table too, tries to make the change fail: 80 × 24, the light theme, keyboard
   only, mouse only, the longest synthetic Arabic post, the large state file, a locked state file, a stopped bot, a
   broken config, hostile text (R9), all simulated in the harness. It tries these only for the rules and tabs the
   evidence pack covers (3.3).
3. **After fixes:** a fresh subagent re-checks the rules that failed and the rules the fix's screens touch, on new
   evidence. A fail stands until such a check says it's gone. After two rounds of fixes that don't clear a fail, the
   owner is asked.
4. **The owner's sign-off** for a PR whose diff adds, removes or moves a widget or dialog, shows or hides one
   conditionally, changes key bindings, changes Textual CSS anywhere (`app.tcss`, `CSS` / `DEFAULT_CSS` strings,
   `styles.*` in code), or changes colours (in CSS or in Rich styles), button variants or the theme: the owner runs the branch in Kitty for a few minutes with the harness's interactive mode,
   with every isolation of 3.3 (never the live bot's data or services), checks the PR's Not-checked items in the same
   session, and records in the PR "Owner: OK" or what feels wrong. A PR whose diff has none of these changes doesn't
   need it (wording-only changes and most bug fixes). What the SVGs can't show (looks, how Kitty draws it) is judged
   here.

**The owner's words.** Only the owner writes "Owner: OK", "Owner accepts: …", the tab-table approval or a waiver. An
agent records one only by quoting the owner's own words from the session, with the date, and never on another
agent's say-so. The merge check looks for that quote.

Auditors don't change the repository; they run the harness and may write throw-away tests outside it, following the
machine's memory rules.

### 3.3 The harness and the evidence pack

Evidence is collected only in the **isolated harness** (`tui/audit/`, added with the first full audit; until it
exists, its steps below are followed by hand as a checklist). Every evidence run:

- computes the real home with `pwd.getpwuid(os.getuid()).pw_dir` **before** changing anything, and refuses to start if
  any path it will use (symlinks followed) is under `~/.local/share/tele-notify*`, the corpus folder,
  `~/tele-notify` or `~/Projects/bqtechjobs`;
- runs with the run folder's temporary `HOME` and config (no `api_id` / `api_hash`, `data_dir` inside the run
  folder); `TELENOTIFY_LABELS` and
  `TELENOTIFY_CORPUS` inside it; `TELENOTIFY_BIN` set to the repository's build or a fake;
- unsets `DISPLAY`, `WAYLAND_DISPLAY`, `DBUS_SESSION_BUS_ADDRESS` and `XDG_RUNTIME_DIR`, sets `BROWSER` to a fake that
  records the URL and exits 0, and puts fakes for `systemctl`, `xdg-open` and `gio` first on `PATH`; the fake
  `systemctl` answers active, inactive, failed or missing per service as the script asks, and logs every call;
- does not use the test suite's autouse fixtures (they switch off `DECIDE_DELAY` and always report the bot inactive);
- builds the large master state file once, under `~/.cache/agent-tmp/tn-audit/` (not `/tmp`, which is memory), and
  never opens it for writing (rebuilt when the schema changes); each run copies or links what it needs into a fresh
  run folder `/tmp/tn-audit/<run>/` holding its temporary home, config and `data_dir` (so paths on screen show no
  user name); interactive and evidence runs never share a run folder;
- sets `PATH` to the fakes folder plus `/usr/bin:/bin` only, so "the `telenotify` program is missing" can't silently
  find an installed one.

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
| Task cost | the R13 scripts' counts, on main and on the branch |
| Style scan | the R14 scan and DOM query |

The harness also has an **interactive mode** (`cd tui && python -m audit interactive`) that sets up the same isolated
environment and starts the screen in the current terminal, for the owner's sign-off. It uses the plain synthetic
data, never the R9 hostile fixtures; copying in it writes the real clipboard (synthetic text only), which is how the
OSC 52 copy is checked. Until the harness exists, a
sign-off waits for it, or the owner accepts the PR on screenshots.

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
- a PR that needs the owner's sign-off (3.2) has "Owner: OK";
- a fail already on main doesn't block if there is evidence from main, it has an open issue or a waiver, and the
  change doesn't make it worse. "Worse" means: for R6, the gap to the limit grows; for R7, main and the branch are
  measured in the same session, interleaved (main, branch, three times each), and the branch's median is over main's
  slowest run by more than 10 % and at least 50 ms; for the other rules, the change adds a failing place (a tab, dialog, control or key) or makes an
  existing one fail in a new way.

Exception, so redesigns aren't blocked by the floor: a change that adds one Block fail of R1, R2, R4.1, R4.4, R6, R7
or R11 while removing more **Block** fails of those rules (places on main, with evidence from main) may merge if the PR
names the new fail and the owner writes "Owner accepts: <the fail>" in the PR; the new fail gets an issue. Never for
R3, R4.2, R4.3, R5, R9 or R10. Removing a feature doesn't count as removing its fails unless the tab table drops that
task with the owner's OK.

**Warn** fails get an issue and don't block. An audit that finds a fail with no issue files one. Before the switch
(PR #39), the Block fails of R3, R5, R9 and R10 found by the first full audit are fixed; the others stay as issues.

**Getting better, not only not worse:** each full audit ends with a short list, agreed with the owner, of the open
issues (Warn included) to fix next. It doesn't block the switch unless the owner says so.

## 5. Changing this policy

The owner decides. A change to a rule is reviewed the same way as the first version (blind, then adversarial); a rule
that gets stricter applies to new changes only, and existing fails get issues from the next full audit.

Waivers are the owner's call, need no review, and are listed here with the date, the rule, the case and the reason:

- none yet.
