# UX profile: tele-notify

Stage: **live** (v4.0 is released, and the README invites others to install it) · Policy: the owner's UI and UX
policy v4.13 (`global/UI-UX-POLICY.md` in the owner's prompts repo) · Written 2026-11-19 at adoption (phase 1).
Reviewed: pending (both passes on this file's PR).

This profile covers the whole product: the bot and its command line (`telenotify`), the messages it sends, and the
terminal screen (`telenotify-tui`). The older project policy, `docs/UI-UX-POLICY.md`, keeps its rules as
**TN-R1 … TN-R14**. Where it is stricter, it wins. Where it is looser, it stays until the next full audit or
2027-05-18, whichever comes first (policy 0.2). The mapping is at the end.

Items marked **(owner)** are decisions only the owner makes. Until the owner sets them, the proposed value is used
and the item is on the owner-check list.

## Purpose (and what it must never do)

tele-notify reads the Telegram job channels and groups its user picks, and forwards only the posts that fit them
(an entry-level IT, control or computer engineering job in Basra, or remote) to a channel of their own. Unclear posts
wait for the user to decide. The terminal screen lets them see the bot, decide unclear posts, find out why a post got
its verdict, and tune the keywords.

It must never:
- send a post to the channel that the user didn't approve and the filter didn't pass;
- send the same post twice;
- lose the user's review decisions or labels;
- leak the user's chats, account or private sources into the public channel;
- make the running bot do something the user didn't ask for.

## Product kinds (7.9)

**Alerts and monitoring** (ALERT1 to ALERT3). Not a reader, viewer, sharing tool, training product, field tool or
family filter.

## Core objects and verbs

- **Objects:** post (a message in a watched chat; an album counts as one), verdict (publish, review, reject) with
  its reasons, chat (watched or the target), keyword list, review queue, alert, period (for the numbers), the bot
  (real or dry run).
- **Verbs:** watch / unwatch a chat; judge (the filter); forward; review a post (send, discard, match no send, undo);
  search; re-judge; test settings on the corpus; save settings; start / stop / restart the bot.

## Experience intent and reference products

| Part | Intent | Reference product |
|---|---|---|
| Glancing at the bot | "Is it fine?" answered in one look, calm, no noise | `htop` / `btop` (state at a glance); GNOME's system monitor |
| Reviewing a post | Careful but quick: read, decide, next; never decide unread | Newsboat (reading an item, one key to act) |
| Finding why a post got its verdict | Certain: the reason is on screen | `lazygit` (select an item, the details show beside it) |
| Tuning keywords | Safe: see the effect before saving, undo by not saving | GNOME Settings' lists (add, edit, remove in place) |
| The command line | Plain, scriptable, honest | `git`, `gh` (clig.dev) |
| Messages to the user | Rare and worth reading | Signal's system notices |
| What the channel's readers get | Only real matches, the original post untouched | a curated job-alert channel |

Tie-breakers for UI choices this profile doesn't settle (1.3): Textual's own widgets and defaults; then clig.dev for
the command line; then the reference product named above for that part.

## Core interactions (with feel specs)

- **Deciding a review post** (`s` / `k` / `d`, or the buttons). The decision shows on the post within 100 ms, and the
  next post appears. Keys wait `DECIDE_DELAY` after a post appears, and the wait is shown, so a double tap can't decide
  an unread post. `u` undoes until the bot has sent the post. Success: a one-line note saying what happened. Failure
  (state locked, already decided elsewhere): a plain-words note, and the post stays. Reference: Newsboat's
  mark-and-next.
- **Picking a search result.** Moving the selection shows that post's verdict and reasons beside the list within
  100 ms (local data). Reference: `lazygit`'s file list and diff pane.

## Users

- **The owner:** the only user of the screen and the command line. A programmer and fresh graduate who runs the bot
  on their own laptop, opens the screen now and then, works through the review queue, searches, and tunes keywords.
  Reads Arabic and English. They fear:
  - the bot silently stopping and missing jobs;
  - a wrong or duplicate post in their public channel;
  - breaking the config or the running bot;
  - losing their review labels.
  Evidence: the owner's own requests in sessions (2026-11-17 to 19) and their use since the switch.
- **Recipients** (never run it): the readers of the owner's public channel (job seekers in Basra). They see only the
  forwarded original posts. They fear wrong, stale or duplicate posts, and noise. Evidence: assumption (marked;
  first thing the next round checks).
- **Others affected:** the authors and admins of the watched chats, whose posts are republished (T13). Evidence:
  assumption.
- **Where and how:** at a desk, in a terminal beside the running service, for a few minutes at a time, often
  interrupted.
- **Abilities:** the owner's needs today: none recorded. The floor still applies (A1 to A5, A10, A18 to A23). Colour
  vision deficiency, low vision (large terminal fonts), one-handed and keyboard-only use are served by the floor
  rules. Blindness: the screen draws in a terminal grid that screen readers read poorly (Kitty has no AT-SPI). The
  non-visual path for the main tasks is the command line (`telenotify review`, `telenotify stats`, editing
  `config.json`), which prints plain lines that Orca reads in GNOME Terminal. Checking it with Orca is Not checked
  (accepted reason below).
- **Age:** adults only; no children use it. T9 is N/A.
- **Languages:** the UI is English. Content (posts, chat titles, keywords) is Arabic, English and mixed, with emoji,
  numbers, links, emails and phone numbers, from strangers: untrusted.

## Platforms

- **OS:** Linux with a systemd user session (Ubuntu GNOME on the owner's laptop). Nothing else is supported.
- **Oldest and smallest device:** the owner's laptop is both the typical and the oldest supported device.
- **Terminals:** Kitty 0.47 (main); Ptyxis and GNOME Terminal (VTE); Zed's terminal. All truecolour.
- **Sizes:** 80 × 24 smallest; 120 × 40 typical; 200 × 50 largest checked.
- **Themes:** `textual-dark` (default) and `textual-light`.
- **Inputs:** the keyboard and the mouse are both first-class in the screen; the keyboard alone in the command line.
- **Assistive technology:** the terminal's font size (the screen never needs a smaller one); GNOME's High Contrast
  and Large Text only through the terminal; Orca through the command line in VTE.
- **Network:** always online in normal use. Telegram outages and the laptop being off are normal states.

## Main tasks

Targets set 2026-11-19 (actions counted by the harness's keyboard-only and mouse-only scripts; waits such as
`DECIDE_DELAY` excluded).

| Task (users' words) | Who | How often | Done means | Targets |
|---|---|---|---|---|
| See whether the bot is fine | owner | daily | service state, connection, dry run or real, pending reviews and alerts read on the Dashboard | 0 actions after start; first useful screen within 2 s warm |
| Start, stop or restart the bot | owner | weekly | the service is in the asked state, and the screen says so | 2 actions (button, confirm) |
| Review an unsure post | owner | daily | the post is sent, discarded or kept, labelled, with undo offered | 1 action per post after reading |
| Find why a post got its verdict | owner | weekly | the post's verdict and reasons are on screen | type the words + 1 action |
| Read the numbers for a period | owner | weekly | the period's counts and chart are on screen | 2 actions (`p`, pick) |
| Add, edit or delete a keyword, then save | owner | weekly | the change is in `config.json`, a backup kept, the bot restarted if asked | 4 actions + typing |
| Watch or unwatch a chat | owner | monthly | the chat is (un)watched in the saved config | 4 actions |
| Test settings on the corpus before saving | owner | monthly | the corpus result is shown next to the saved settings' result | 1 action |
| Remove a wrong post from the channel (MSG6) | owner | rare | the post is gone from the channel | not built yet (tracked) |
| Undo a review decision | owner | weekly | the post is back in the queue, its label removed | 1 action |
| Install, log in and start the service (first setup) | owner, others following the README | once | the bot runs from `~/.local/bin`, logged in, as a user service | README steps only; each warning explained beforehand |
| Update after `git pull` | owner | monthly | `deploy/install.sh` rebuilt and restarted it, or said why not | 1 command |
| Log in again after Telegram logs the account out (recovering access) | owner | rare | `telenotify login` works and the service restarts | README steps only |
| Change the Arabic display mode or theme (the accessibility settings) | owner | rare | the mode is in effect | 1 flag or variable; documented in the screen's README |

First run budget: 3 screens before the first main task (default).

## Screen table

The screen's tabs keep their rows in the tab table in `tui/README.md` (approved by the owner on 2026-11-18). This
table adds the intent order and the rest of the product.

| Screen or group | Job | Must be visible at smallest size | Primary action | Input per method | Intent order |
|---|---|---|---|---|---|
| Dashboard tab | bot health, start / stop, numbers, feed, alerts | as the tab table | none (state screen); Period | `p` / click the picker | 1 service and connection state; 2 dry run or real, pending reviews, alerts; 3 the period's numbers |
| Review tab | decide unsure posts | as the tab table | Send | `s` / click Send | 1 the post (chat, text); 2 its reasons; 3 the decision buttons |
| Search tab | find a post and why | as the tab table | none (typing searches) | type / click a row | 1 results with verdicts; 2 the chosen post's reasons; 3 the search box |
| Settings tab: list sections (keywords, places, roles, certificates, job sites) | edit a list | as the tab table | Save | `ctrl+s` / click Save | 1 the chosen list; 2 saved or unsaved and problems; 3 Edit / Delete / Add |
| Settings tab: Chats | watch, unwatch, pick the target | as the tab table | Save | `ctrl+s` / click Save | 1 the chat table with watched marks; 2 the target chat; 3 saved or unsaved |
| Settings tab: options | bot options | the options and the saved marker | Save | `ctrl+s` / click Save | 1 the options; 2 saved or unsaved |
| Confirm dialogs (service start / stop / restart; quit with unsaved changes; save and restart) | a decision now | the question, the object, both buttons | the safe choice has focus | `esc` / `n` cancels; the named button | 1 the question; 2 the consequence; 3 the buttons |
| Dates form (custom period) | type a range | both fields, the error, OK and Cancel | Show | enter / click | 1 the fields; 2 the error; 3 the buttons |
| Filter-read view (review `r`) and corpus test view (settings `t`) | show what the filter read, or the test result | the text and Close | Close | `esc` / click Close | 1 the result; 2 Close |
| Paste a post (search `p`) | try a post | the paste box and its verdict | Judge | type, then the key / click | 1 the box; 2 the verdict and reasons |
| Command line: each command's output (`check`, `eval`, `login`, `run`, `review`, `stats`, `compare`, usage and errors) | as its help says | readable at 80 columns | n/a | keyboard | 1 the result or error; 2 the next step |
| Messages: alerts in the owner's Saved Messages (bot back after downtime, no connection, a watched chat lost, a backlog cut) | tell the owner something needs them | the first line says why | n/a | Telegram | 1 why; 2 what to do |
| Messages: forwards to the public channel | the original post, unchanged | n/a (Telegram's UI) | n/a | n/a | n/a |

## Budgets (with tolerance) and the benchmark walk

Tolerance: 5 % or the measurement's noise, median of 10 runs (policy 1.5). Baselines are measured in the first
evidence run (phase 2) and written here then.

- Screen start-up to the first useful Dashboard: 2 s warm, 5 s cold (default; the harness measured about 4.5 s for the
  first tab on the large state file, tracked).
- Local interactions (tab switch, selection, typing): 100 ms (P7).
- No event-loop block over 1 s (P1), from asyncio's slow-callback log.
- **ALERT1, latency:** from a source post to the forward in the channel, 95 % under 60 s while the bot is connected.
  Measured from the state file's post and judge times, in aggregate only.
- The bot's own memory: under 200 MB resident (proposed; measured in phase 2).

**Benchmark walk:** open the screen on the harness's synthetic state file with 1 million posts (about 10 times the
real one), visit every tab, scroll the feed, open the Activity box for all time, search a common word, pick a
result, open Settings and the Chats table, decide 3 review posts.

## Real-data evaluation (1.5, Z1, ALERT2)

- **What:** `telenotify eval` on the private corpus (`~/.local/share/tele-notify-corpus/eval.jsonl`), run locally;
  only aggregate counts go into PRs.
- **Combined score (owner):** proposed **F0.5** over posts labelled TRUE and FALSE, where a publish is a positive.
  F0.5 weighs precision twice as much as recall, because a wrong publish reaches every reader of the channel,
  while a missed one costs only the owner. Review counts as not published.
- **Snapshot and held-out part (owner):** only the owner cuts a fixed, versioned snapshot and its held-out part,
  stored encrypted outside every repository. Until the owner does, the ratchet uses the visible corpus, and the
  held-out run is an open owner duty.
- **Held-out tolerance:** one item, or the bootstrap 90 % interval, whichever is wider.

## Owner budget (owner)

Proposed: 30 minutes a month. Owner-check items carry an estimate. When the queue for the next release is over budget,
the agent proposes a batch, highest risk first.

## Declarations

None.

## Modules

7.6 TUI (the screen), 7.7 CLI (`telenotify`, `deploy/install.sh`, `deploy/build-tdlib.sh`), 7.8 MSG (alerts and
forwards). Not 7.1 to 7.5.

## Design system

The platform's: Textual's widgets, its `textual-dark` and `textual-light` themes and their variables, plus
`tui/telenotify_tui/app.tcss`. Additions:
- **Verdict styles:** publish uses `$success`, review `$warning`, reject `$error`, and each always has its word (A5).
- **Spacing:** 0, 1 or 2 cells.
- **Glossary:** post, verdict (publish, review, reject), review queue, send, discard, match no send (`k`), chat,
  watched, target chat, dry run, real bot, period, corpus.
- **Voice:** plain and short; say what happened and what to do; no exclamation marks.
- Exceptions to V3 (literal colours): none.

## Evidence tools and synthetic data

- **Screen:** the isolated harness `tui/audit/` (branch `ui-audit-harness`, to merge in phase 2). It uses a temporary
  `HOME`, fake `systemctl`, `xdg-open`, `gio` and `BROWSER`, and refuses the real data folders. Textual pilot
  screenshots (SVG, and PNG through `rsvg-convert` for eyes) at 80 × 24, 120 × 40 and 200 × 50, both themes. It also
  collects keys, contrast, timing, hostile text, failures, task scripts, styles, and an interactive mode for the owner.
- **Command line:** golden-output tests in `tests/` (to add in phase 2: help, normal, error, summary).
- **Messages:** unit tests that render each alert from synthetic input.
- **Synthetic data:** invented posts, chats and keywords in the harness (`tui/audit/data.py`), at `example.com`.
  Its statistics (posts per day, longest Arabic and English chat titles, the mix of scripts) are re-measured from
  the real state at each full audit, as counts only.
- **Evidence store:** the PR (comments and CI artifacts) and `docs/ui-audit/YYYY-MM.md` for full audits.

## Applicability list (N/A rules, with reasons)

| Rules | Why |
|---|---|
| WEB, GNOME, AND, WIN, GM, READER, VIEW, SHARE, TRAIN, FIELD2, FILTER, HAND1 | not those platforms or kinds |
| A3 (sizes) | a terminal: the user's font; the screen never needs a smaller one |
| A4 (text scales) | the terminal's own font size scales everything; checked as 80 × 24 (TUI1) |
| A6, A10's video part, A13 to A16, A29, A30 | no pictures behind text, no video, sound, generated documents or haptics |
| A19 | no touch; terminal cells are the only pointer target |
| A20 | no gestures, drags or device motion are needed (A22 applies: `ctrl+s`, `ctrl+r` and `ctrl+q` are chords, checked in the first audit) |
| N5 | no URLs or incoming links |
| T2, T4, T6's sign-in parts beyond `telenotify login` | no accounts, OS permissions or pairing |
| T9 | no children use it |
| ST3, ST5 | no offline mode or shared editing (Telegram outages are covered by F4 and ST4) |
| O1, O4 | no system notifications or badges |
| L1, L4, L6 | the UI is English only; content isn't translated |
| D7 | no updater of its own (`deploy/install.sh` is run by hand) |
| GM10, MSG4's command menu | no multiplayer; it acts as a user account, not a bot (MSG4 says controls live in the screen) |
| A12 | not N/A: the non-visual path is the command line (see Users) |

## Accepted Not-checked reasons (expire 2027-05-19 unless renewed)

- How it really looks and feels in Kitty and VTE, and whether the terminal puts an OSC 52 copy on the clipboard:
  owner-check items (8.5).
- Orca reading the command line's output in GNOME Terminal: owner-check item.
- A real Telegram ban or log-out, and starting while offline: need the live account. Checked with fakes in tests.

## Third-party surfaces

- Telegram's apps around the forwarded posts and the alerts (how a forward looks in the channel). A change on their
  side is noticed by the owner reading the channel.
- TDLib's login prompts are worded by the bot itself (`telenotify login`).

## Review brief

`docs/ux-review-brief.md`, version 1.

## Waivers

None.

## Mapping of the project policy (TN-R) to this policy

| TN rule | Policy rules | Stricter |
|---|---|---|
| TN-R1 keyboard and mouse | U1, A18, TUI2, I3 | both apply |
| TN-R2 fits the terminal | TUI1, V8 | the policy (Block at every size) |
| TN-R3 Arabic and mixed text | L2, TUI5 | both apply |
| TN-R4 feedback | F1, F2, F4, W3, S7 | the policy |
| TN-R5 safe changes | S1, S2, S6, TUI6 | both apply |
| TN-R6 readable colours | A1, A2, A5, A7, TUI3 | the policy (Block in both themes) |
| TN-R7 fast enough | P1, P3, P7 | both apply (TN-R7's interleaved measuring is kept) |
| TN-R8 consistent | C2, V6, W1 | the policy (Block) |
| TN-R9 untrusted text | T7, TUI4 | both apply |
| TN-R10 degrades gracefully | ST4, U5 | the policy |
| TN-R11 copying | I5 | both apply |
| TN-R13 task cost | U3 | the same |
| TN-R14 visual consistency | V2, V3, V4, V6 | the policy (Block) |
| TN 3.2.4 owner sign-off | 8.5 | both apply: the TN sign-off gates the merge, 8.5 gates the release |
