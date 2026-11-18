# UI and UX policy: the terminal screen

Status: draft (2026-11-18, the owner asked for it). Adopted once a blind and an adversarial review have both checked it.

This policy says what the terminal screen (`tui/`, the Textual app `telenotify-tui`) must do, and how a change to it is
checked. It is modelled on the owner's kks-explorer policy, cut down to what a one-person terminal app needs: the
method (checkable rules, evidence, two audit passes, a results table, merge blockers) is the same; touch, phones,
several platforms and screen readers are left out because they don't apply.

The bot itself (Telegram messages, `telenotify` command output) is not covered, except where the screen shows it.

## 1. Who uses it, and where

- **One user, the owner:** a fresh graduate who runs the bot on their own laptop and opens the screen now and then to
  check it, work through the review queue, search and tune keywords. They know the project but shouldn't need to
  remember keys: the screen must show them.
- **Terminal:** Kitty 0.47 on Ubuntu GNOME is the main one (default font, DejaVu Sans Mono); Ptyxis / GNOME Terminal
  (VTE) and Zed's built-in terminal are possible. Window sizes vary; 80 × 24 is the smallest supported.
- **Keyboard and mouse** are both first-class.
- **Text:** the UI is English. Posts, chat titles and keywords are Arabic, English or mixed, with emoji, numbers,
  links and phone numbers.
- **Data is private:** the owner's chat list, the review queue and the labelled corpus (`~/.local/share/tele-notify-corpus/`)
  never go into screenshots, issues, PRs or the wiki.
- **The screen sits beside a running bot:** it must never block the bot, corrupt its state file or config, or make
  the bot do something the owner didn't ask for.

## 2. The rules

Each rule is a check with a pass or a fail. **Block** means a fail stops a merge (section 4); **Warn** means it is
recorded and gets an issue, but doesn't block.

### R1 Every action by keyboard and by mouse (Block)

1. Every action on every tab can be done with the keyboard alone, and with the mouse alone (typing text excepted).
2. Focus is always visible, and `tab` / `shift+tab` reach every control in a sensible order.
3. The footer shows the keys that work where the focus is, and only those: a key shown must work, a key that works
   in a list must not be shown as working while a text box has focus.
4. Keys never fire while the user types in a text box (letters, digits, `delete`, `backspace` edit the text).
5. Number keys `1`–`4` switch tabs from anywhere outside a text box; `ctrl+q` quits from anywhere.

### R2 Fits the terminal (Block at 80 × 24, Warn above)

1. At 80 × 24 every tab shows its main content and every button and key hint, with nothing cut off or overlapping
   (a list may scroll; a button may not disappear).
2. At 120 × 40 and 200 × 50 the extra space is used (wider tables, longer text), not left blank.
3. Resizing while the screen is open re-lays everything out without a restart and without losing the selection,
   the scroll position or unsaved edits.

### R3 Arabic and mixed text read correctly (Block)

1. Arabic paragraphs read right to left and are right-aligned; mixed Arabic, English, numbers, links, emails,
   phone numbers, brackets and emoji come out in reading order, in the mode chosen for the terminal (`words` in
   Kitty, `full` elsewhere, see `tui/README.md`).
2. Wrapping never loses, repeats or swaps characters or lines.
3. Copying text with the mouse gives the text as stored (logical order, no presentation forms), blank lines kept.
4. A post can't change the layout of anything outside its own area (bidi control characters, very long words,
   combining marks).

### R4 Feedback in plain words (Block for 1–3, Warn for 4)

1. Every action says what happened, in words a non-programmer understands, without a status code or stack trace.
2. An error says what went wrong and what to do next; nothing fails silently.
3. Whether the bot shown is the **dry run** or the **real** bot is always visible, and in the dry run every action
   that would send something says that nothing is sent.
4. Anything that takes over a second shows that it is working, and the rest of the screen keeps responding.

### R5 Safe changes (Block)

1. Nothing is written to `config.json` until the user saves; saving keeps a backup; unsaved changes are shown and
   quitting with them asks first.
2. A choice that can't be taken back (stop the bot, discard a review the bot may already have acted on, overwrite a
   file) asks first or can be undone; the undo is shown.
3. A one-key bulk change (for example remove duplicates) only does what its label says, names what it changed, and
   is not saved until the user saves.
4. The screen never edits the bot's state file except through the same steps the bot's own commands use (deciding
   a review), and never sends anything to Telegram itself.

### R6 Readable colours (Block for 1–2, Warn for 3)

1. Text is readable in Textual's default dark theme and in its light theme (contrast at least 4.5 : 1 for body text,
   3 : 1 for large or bold text and for focus and borders, measured on the screenshot's colours).
2. Colour is never the only signal: a verdict, a problem, a warning or a selected row also has a word, symbol or
   style.
3. On a 16-colour terminal (`TERM=xterm`, `COLORTERM` unset) everything is still readable.

### R7 Fast enough (Block for 1, Warn for 2)

1. Typing, moving the selection and switching tabs never freeze the screen for more than 100 ms, also with a state
   file of a million judged posts.
2. Opening the screen shows the first tab within 2 s; a refresh that would take longer shows the old numbers until
   the new ones are ready.

### R8 Consistent (Warn)

1. The same action has the same key and the same word on every tab (for example `enter` opens, `esc` closes or
   cancels, `ctrl+r` refreshes, `o` opens a link).
2. The words for verdicts, actions and states match the bot's (`publish`, `review`, `reject`, `forwarded`) and the
   README.
3. Dates and times use one format and the local time zone.

### R9 Private evidence (Block)

Screenshots and recordings made for an audit, a PR, an issue or the wiki use synthetic data only (made-up posts,
chats and keywords) and a throw-away config with no `api_id` / `api_hash`. Never the owner's state file, config,
chat list or corpus.

## 3. The audit

### 3.1 When

- **For every PR that changes what the screen shows or how it is used:** `tui/telenotify_tui/` and its CSS, and bot
  changes that change text or data the screen shows. Changes to tests and docs only need none.
- **A full audit** (every rule, every tab) once now that the policy is adopted (results in
  `docs/ui-audit/2026-11.md`), and again before the switch from the Python bot (PR #39), and after any change that
  reworks a whole tab.
- No calendar schedule: with one user and one platform, audits follow changes.

### 3.2 Who

Two fresh subagents, one after the other, neither told what the author thinks. For a PR they are the same two review
passes the project already uses (blind, then adversarial), given this policy and the evidence pack as well as the
diff.

1. **Blind audit:** records a result for every rule the change touches.
2. **Adversarial audit:** given pass 1's table too, tries to make the change fail: 80 × 24, light theme, 16
   colours, keyboard only, mouse only, the longest synthetic Arabic post, a huge state file, a slow or stopped bot.
3. **After fixes:** a fresh subagent re-checks the rules that failed, on new evidence. A fail stands until such a
   check says it's gone.

Auditors don't change the repository; they may run the evidence script and write throw-away tests outside it,
following the machine's memory rules.

### 3.3 The evidence pack

Made from synthetic data only (R9), before and after the change, for the tabs the change touches:

| What | How |
|---|---|
| Screenshots | Textual pilot `app.save_screenshot()` (SVG) at 80 × 24, 120 × 40 and 200 × 50, dark and light theme; one at 80 × 24 with `TERM=xterm` colours |
| Arabic in Kitty | `kitty +runpy` with `kitty.fonts.render.render_string` (never a window on the owner's desktop), as `tui/tests/test_rtl.py` does |
| Keys | the footer and `app.active_bindings` per focused widget, from a pilot run |
| Timing | pilot timings on a synthetic state file of 1 000 000 judged posts |
| Colours | foreground and background of each style, read from the screenshot SVG |

What can't be checked headless (how it really looks in Kitty or VTE, a real mouse drag) is recorded as "not checked:
needs the owner's terminal", and listed in the PR so the owner can look.

### 3.4 How results are recorded

For a PR: a table in a PR comment from each pass, one row per rule that applies, each cell one of **Pass**, **Fail**
(where and what), **Pre-existing fail** (already on main, issue linked), **N/A** (why), **Not checked** (why).

For a full audit: the same table for every rule and tab in `docs/ui-audit/YYYY-MM.md`, with the screenshots
(synthetic data only) beside it in `docs/ui-audit/YYYY-MM/`, and one issue per group of fails.

## 4. What blocks a merge

A PR that changes the screen merges only when both audit passes are done and no **Block** rule fails because of the
change. A fail already on main doesn't block if it has an open issue and the change doesn't make it worse. **Warn**
fails get an issue and don't block.

## 5. Changing this policy

The owner decides. A change is reviewed the same way as the first version (blind, then adversarial), and a rule that
gets stricter applies to new changes only; existing fails get issues from the next full audit.
