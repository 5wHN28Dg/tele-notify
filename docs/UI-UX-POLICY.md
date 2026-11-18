# UI and UX policy: the terminal screen

Status: draft (2026-11-18, the owner asked for it). Adopted when the owner approves it after a blind and an
adversarial review.

This policy says what the terminal screen (`tui/`, the Textual app `telenotify-tui`) must do, and how a change to it is
checked. It is modelled on the owner's kks-explorer policy, cut down to a one-person terminal app: the method
(checkable rules, evidence, two audit passes, a results table, merge blockers) is the same; touch, phones, several
platforms and screen readers are left out because they don't apply.

The bot itself (Telegram messages, `telenotify` command output) is not covered, except where the screen shows it.

## 1. Who uses it, and where

- **One user, the owner:** a fresh graduate who runs the bot on their own laptop and opens the screen now and then to
  check it, work through the review queue, search and tune keywords. They know the project but shouldn't need to
  remember keys: the screen must show them or show where to find them.
- **Terminal:** Kitty 0.47 on Ubuntu GNOME is the main one (default font); Ptyxis / GNOME Terminal (VTE) and Zed's
  built-in terminal are possible. All are truecolour. Window sizes vary; 80 × 24 is the smallest supported.
- **Themes:** Textual's default `textual-dark`; `textual-light` is supported but not the owner's usual one.
- **Keyboard and mouse** are both first-class.
- **Text:** the UI is English. Posts, chat titles and keywords are Arabic, English or mixed, with emoji, numbers,
  links, emails and phone numbers, and come from strangers: they are untrusted.
- **Data is private:** the owner's chat list, state files, review queue, config and the labelled corpus
  (`~/.local/share/tele-notify-corpus/`) never go into screenshots, issues, PRs or the wiki (both repositories are
  public).
- **The screen sits beside a running bot:** it must never block the bot, corrupt its state file or config, or make
  the bot do something the owner didn't ask for.
- **The review queue is read one post at a time on purpose** (each decision is also a corpus label), so there is no
  bulk-decision rule.

## 2. The rules

Each rule is a check with a pass or a fail. **Block** means a fail stops a merge (section 4); **Warn** means it is
recorded and gets an issue, but doesn't block. Each rule ends with how it is checked; the evidence is described in
3.3.

### R1 Every action by keyboard and by mouse (Block)

1. Every action on every tab can be done with the keyboard alone, and with the mouse alone (typing text excepted;
   clicking a key in the footer counts as the mouse).
2. Focus is always visible, and `tab` / `shift+tab` move top to bottom, then left to right within each area; focus
   never lands in a hidden tab or behind a dialog.
3. A key shown in the footer works where it is shown; a key that can't act right now is dimmed or hidden, never shown
   as working.
4. Keys never fire while the user types in a text box (letters, digits, `delete`, `backspace` edit the text).
5. `esc` closes the top dialog or cancels the current edit, and there is no focus trap: from anywhere, `esc` (once
   or more) gets back to the tab. Number keys `1`–`4` switch tabs from anywhere outside a text box and a dialog;
   `ctrl+q` quits from anywhere (asking first per R5.1).
6. Every key either shows in the footer or is listed in the keys panel, which a key shown in the footer opens.

Check: a pilot run per tab that focuses each widget in turn and records the footer and `app.active_bindings`, then
presses each listed key and confirms it acted; tab order from the focus chain; typing every bound key into each text
box changes only its text; a mouse-only script (clicks only) completes each tab's main tasks.

### R2 Fits the terminal (Block at 80 × 24, Warn above)

1. At 80 × 24 every button and every tab's main content is fully visible (a list may scroll; a button may not be cut
   off or disappear). The footer may be cut off at the right; R1.6 makes every key reachable anyway.
2. At 120 × 40, no text that was cut off at 80 × 24 is still cut off while its row has room. (Warn)
3. Resizing while the screen is open re-lays everything out without losing the selection, the scroll position or
   unsaved edits. (Block)

Check: screenshots at 80 × 24, 120 × 40 and 200 × 50; a pilot that resizes from 120 × 40 to 80 × 24 and back with a
selection, a scrolled list and an unsaved edit.

### R3 Arabic and mixed text read correctly (Block)

Applies once the bidi work (PR #48) is merged.

1. Arabic paragraphs read right to left and are right-aligned; mixed Arabic, English, numbers, links, emails,
   phone numbers, brackets and emoji come out in reading order, in the mode chosen for the terminal (`words` in
   Kitty, `full` elsewhere; see `tui/README.md`).
2. Wrapping never loses, repeats or swaps characters or lines.
3. Text selected with the screen's own selection (drag, then copy) gives the text as stored: logical order, no
   joined letter forms, blank lines kept. (The terminal's own shift+drag selection copies screen cells; that is
   outside the app and N/A.)
4. A post can't change the layout of anything outside its own area (bidi control characters, very long words,
   combining marks).

Check: `tui/tests/test_rtl.py`'s cases plus the synthetic posts of the evidence pack; Kitty's own renderer
(`kitty +runpy`, `kitty.fonts.render.render_string`) fed the rendered screen lines from a pilot run, which checks
how Kitty draws those lines, not the layout; `screen.get_selected_text()` after a pilot selection.

### R4 Feedback in plain words (Block for 1–4, Warn for 5)

1. Every action that changes something or starts work says what happened, in words a non-programmer understands,
   without a status code or stack trace.
2. An error says what went wrong and what to do next; nothing fails silently (no `except` that turns a failure into
   an empty list without saying so).
3. Whether the bot shown is the **dry run** or the **real** bot is always visible, and in the dry run every action
   that would send something says that nothing is sent.
4. An error message stays until dismissed or is also shown in place; a message never covers the focused control.
5. Anything that takes over a second shows that it is working and the rest of the screen keeps responding; work
   over 10 s shows progress where it can be known and can be cancelled or left running in the background.

Check: a pilot script that does every action on every tab and records each notification and status line; forced
failures (see R10) for every error path; a toast's timeout and position at 80 × 24.

### R5 Safe changes (Block)

1. Nothing is written to `config.json` until the user saves; saving keeps a backup; unsaved changes are shown and
   quitting or reverting with them asks first.
2. A choice that can't be taken back (stop or restart the bot, quit with unsaved edits, discard a review the bot may
   already have acted on) asks first or can be undone, and the undo is shown.
3. A confirmation names the action on its button ("Stop the bot", not "Yes"), starts with focus on the safe choice,
   and can't be confirmed by an accidental `enter` or a double tap; a decision key waits a moment after a new item
   appears (as the review queue's `DECIDE_DELAY` does) so a double tap can't decide the next item unread.
4. A one-key bulk change (for example remove duplicates) only does what its label says, names what it changed, and
   is not saved until the user saves.
5. The screen never edits the bot's state file except through the same steps the bot's own commands use (deciding
   a review), and never sends anything to Telegram itself.
6. A field error appears at the field, names it, and keeps what was typed.

Check: a pilot per dialog that opens it and presses `enter` straight away (must not do the action); scripts for
quit/revert with unsaved edits, undo after each decision, dedupe, and invalid input in every form; diff of the
config and state file before and after.

### R6 Readable colours (Block in `textual-dark`, Warn in `textual-light`)

1. All text, bold included, has a contrast of at least 4.5 : 1 against what is behind it; borders, focus marks and
   the selected-row cursor at least 3 : 1. A stock Textual widget the project hasn't restyled carries its open
   fails as pre-existing (issue filed upstream or noted), not as this project's fail.
2. Colour is never the only signal: a verdict, problem, warning or selected row also has a word, a symbol, bold,
   underline or reverse video. A background colour alone doesn't count.

Check: the foreground and background colours of every style, read from the screenshot SVGs, and the contrast ratio
computed; a greyscale copy of each screenshot in which every state can still be told apart.

### R7 Fast enough (Block for 1, Warn for 2 and 3)

1. No event-loop stall over 1 s while typing, moving the selection, switching tabs or refreshing.
2. No stall over 100 ms in the same interactions.
3. The first tab shows within 2 s of starting; a refresh that would take longer keeps showing the old numbers until
   the new ones are ready.

Measured with asyncio debug mode (`loop.slow_callback_duration`) during a scripted pilot interaction, median of 3
runs on an otherwise idle machine, with a synthetic state file of at least 10 times the real one's row count (and
at least 100 000 rows).

### R8 Consistent (Warn)

1. The same action has the same key and the same word on every tab (`enter` opens, `esc` closes, `ctrl+r`
   refreshes, `o` opens a link).
2. The words for verdicts, actions and states match the bot's (`publish`, `review`, `reject`, `forwarded`) and the
   README.
3. Times are `YYYY-MM-DD HH:MM` (or `HH:MM` for today), 24-hour, local time; day names and short dates are used the
   same way everywhere.
4. Counts are grammatical ("1 problem", "2 problems"); an empty list says why and what to do.

Check: a table of every binding per tab from the pilot; a text scan of the screenshots and of the strings in
`tui/telenotify_tui/`.

### R9 Untrusted text stays text (Block)

Post text, chat titles, keywords, config values and the output of `telenotify` and `systemctl` are never read as
Textual or Rich markup and never reach the terminal as escape sequences (colours, cursor moves, OSC 8 links, OSC 52
clipboard writes, title changes).

Check: a synthetic fixture whose posts, titles, keywords and subprocess output contain `[b]x[/b]`, `[link=…]`,
`[@click=…]`, unbalanced `[`, ANSI CSI and OSC 8 / 52 sequences, shown on every tab and in every notification; the
screen's output stream is scanned for escapes the app didn't write itself.

### R10 Degrades gracefully (Block)

Each tab shows a plain-words state, never a crash, a blank tab or an endless spinner, when: the bot is stopped or
was never started; the state file is missing, has a missing table, or is locked by the bot; `config.json` is
missing or invalid; the `telenotify` program or `systemctl --user` isn't available. The Dashboard shows how old its
numbers are and says so when the bot has stopped updating its status (Warn).

Check: a pilot per tab for each of these conditions, made with temporary files and fake commands.

### R11 Values can be copied (Block)

Links, emails and phone numbers in posts, chat IDs and keywords can be copied exactly (the screen's own selection,
or a copy key), and the screen confirms what was copied.

Check: a pilot selection or copy key on each kind of value; the copied text compared with the stored value.

### R12 Private and side-effect-free evidence (Block)

1. Screenshots and recordings made for an audit, a PR, an issue or the wiki use synthetic data only: invented posts
   (not edited copies of real ones), chats and keywords; phone numbers, emails and links from reserved ranges
   (`example.com`, `+964 555 …`); a throw-away config with no `api_id` / `api_hash`. Never the owner's state file,
   config, chat list or corpus.
2. Every pilot or evidence run uses a temporary `HOME`, a throw-away config whose `data_dir` is inside it,
   `TELENOTIFY_LABELS` and `TELENOTIFY_CORPUS` pointing inside it, and a fake `systemctl` and `xdg-open` first on
   `PATH` (as `tui/tests/conftest.py` does). The evidence script refuses to run if any path it resolves is under the
   real data folder, the corpus folder or the deployed bot's folder.
3. Before anything is published, its edges are checked too: the header's config path, the terminal title, user
   names in paths, notification text.
4. A problem first seen on real data is reproduced on synthetic data before it is filed.

## 3. The audit

### 3.1 When

- **For every PR that changes what the screen shows or how it is used:** `tui/telenotify_tui/` and its CSS, and bot
  changes that change text or data the screen shows. Changes to tests and docs only need none.
- **A full audit** (every rule, every tab) once now that the policy is adopted (results in
  `docs/ui-audit/2026-11.md`), again before the switch from the Python bot (PR #39), and after any change that
  reworks a whole tab.
- No calendar schedule: with one user and one platform, audits follow changes.

### 3.2 Who

Two fresh subagents, one after the other, neither told what the author thinks. For a PR they are the same two review
passes the project already uses (blind, then adversarial), given this policy and the evidence pack as well as the
diff.

1. **Blind audit:** records a result for every rule the change touches.
2. **Adversarial audit:** given pass 1's table too, tries to make the change fail: 80 × 24, the light theme,
   keyboard only, mouse only, the longest synthetic Arabic post, a large state file, a stopped bot, a broken config,
   hostile text (R9). Always under R12.
3. **After fixes:** a fresh subagent re-checks the rules that failed, and the rules the fix's screens touch, on new
   evidence. A fail stands until such a check says it's gone.

Auditors don't change the repository; they may run the evidence script and write throw-away tests outside it,
following the machine's memory rules.

### 3.3 The evidence pack

Made under R12, before and after the change, **only for the rules and tabs the change touches** (a wording-only
change needs 80 × 24 dark-theme screenshots of the changed screens only):

| What | How |
|---|---|
| Screenshots | Textual pilot `app.save_screenshot()` (SVG) at 80 × 24, 120 × 40 and 200 × 50, `textual-dark` and `textual-light` |
| Arabic in Kitty | `kitty +runpy` with `kitty.fonts.render.render_string` on the rendered lines (never a window on the owner's desktop) |
| Keys | the footer and `app.active_bindings` per focused widget, from a pilot run |
| Timing | asyncio slow-callback log of a scripted pilot interaction on a large synthetic state file |
| Colours | foreground and background of each style, read from the screenshot SVGs |
| Hostile text and failures | the R9 and R10 fixtures |

What can't be checked headless is recorded as **Not checked** with the reason. The accepted reasons (section 4) are:
how it really looks in Kitty or VTE, a real mouse drag, and the terminal's clipboard (OSC 52).

### 3.4 How results are recorded

For a PR: a table in a PR comment from each pass, one row per rule that applies, each cell one of **Pass**, **Fail**
(where and what), **Pre-existing fail** (already on main, with evidence from main and an issue linked), **N/A**
(why), **Not checked** (why).

For a full audit: the same table for every rule and tab in `docs/ui-audit/YYYY-MM.md`, with the screenshots
(synthetic data only) beside it in `docs/ui-audit/YYYY-MM/`, and one issue per group of fails.

## 4. What blocks a merge

A PR that changes the screen merges only when both audit passes are done and:

- no **Block** rule fails because of the change;
- no **Block** rule is **Not checked**, unless its reason is one of the accepted ones in 3.3; those are listed in
  the PR for the owner to look at in their own terminal;
- a fail already on main doesn't block if it has an open issue, there is evidence from main, and the change doesn't
  make it worse ("worse" means more tabs or items fail, or the gap to the limit grows).

**Warn** fails get an issue and don't block. An audit that finds a fail with no issue files one. Block fails found by
the first full audit are fixed before the switch (PR #39).

## 5. Changing this policy

The owner decides. A change is reviewed the same way as the first version (blind, then adversarial); a rule that
gets stricter applies to new changes only, and existing fails get issues from the next full audit.

Waivers (a rule not applied to a case, with the date and the reason):

- none yet.
