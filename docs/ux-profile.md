# UX profile: tele-notify

Stage: **live** (v4.0 is released, and the README invites others to install it) · Policy: the owner's UI and UX
policy v4.13 (`global/UI-UX-POLICY.md` in the owner's prompts repo) · Written 2026-11-19 at adoption (phase 1).
Reviewed: blind and adversarial passes on PR #53.

**State of the adoption.** The profile and review brief exist. The hook-up lines (policy Appendix G) are **not yet**
in AGENTS.md or this project's CLAUDE.md. Until they are, AGENTS.md wins every conflict, and agents start no tier-2
or tier-3 UI change on this product (policy, Quick start, phase 1). Whether the terminal screen is inside the
delegation in CLAUDE.md is also unanswered (policy 0.2), so until the owner answers, agents make **no UI changes to
`tui/`** and only propose them. Changes to tests and to the bot's own code inside the delegation go ahead.

**Phase-1 check** (the never-waivable set, U1, A1 and A5, plus T13 and MSG6) was done by hand on 2026-11-19. Its fails
are tracked as `ux` issues #55 and #62 to #73. Earlier harness finds are #54 to #60, and finds from this profile's
reviews are #74 to #86 (all: `gh issue list --label ux`). A10 and T1 passed, T9 is N/A, and T0 waits for the owner's
checklist below. Z1 failed: TUI tests wrote two synthetic labels into the owner's real `review-labels.jsonl`. The
cause is fixed in PR #61 (merged 2026-11-19). The two lines are still in the file until the owner removes
them, and they count in every `telenotify eval` until then.

This profile covers the whole product: the bot and its command line (`telenotify`, `deploy/install.sh`,
`deploy/build-tdlib.sh`), the messages it sends, and the terminal screen (`telenotify-tui`). The older project
policy, `docs/UI-UX-POLICY.md`, keeps its rules as **TN-R1 … TN-R14**. Where it is stricter, it wins. Where it is
looser, it stays until the next full audit or 2027-05-18, whichever comes first (policy 0.2). The mapping is at the
end.

Items marked **(owner)** are decisions only the owner makes. Until the owner sets them, they are proposals, listed in
"Waiting for the owner" at the end, and nothing that depends on them counts as settled.

## Purpose (and what it must never do)

tele-notify reads the Telegram job channels and groups its user picks, and forwards only the posts that fit them to
a channel of their own. Unclear posts wait for the user to decide. The terminal screen lets them see the bot, decide
unclear posts, find out why a post got its verdict, and tune the keywords.

It must never:
- send a post to the channel that the user didn't approve and the filter didn't pass;
- send the same post twice;
- lose the user's review decisions or labels;
- republish a private source's posts, or leak the user's chats or account, into the public channel;
- make the running bot do something the user didn't ask for.

## Content policy (owner)

What counts as a match is the owner's decision, written in their config's keywords. Proposed wording of today's
choice: entry-level IT, control, electrical and computer engineering jobs in Basra or remote. The filter must
enforce it. Whether it should be different isn't something a reviewer judges.

## Product kinds (7.9)

**Alerts and monitoring** (ALERT1 to ALERT3), and FIELD1 (report a problem), because people other than the owner
may install it. Not a reader, viewer, sharing tool, training product, family filter or hand-over setup.

## Core objects and verbs

- **Objects:** post (a message in a watched chat; an album counts as one), verdict (publish, review, reject) with
  its reasons, chat (watched or the target), keyword list, review queue, alert, period (for the numbers), the bot
  (real or dry run).
- **Not the original post:** when forwarding is blocked, the bot posts its own short "match / post link" text in the
  channel (#80).
- **Verbs:** watch / unwatch a chat; judge (the filter); forward; review a post (send, discard, match no send, undo);
  search; re-judge; test settings on the corpus; save settings; start / stop / restart the bot.

## Experience intent and reference products (owner)

Proposed; the agent's draft until the owner confirms it.

| Part | Intent | Reference product |
|---|---|---|
| Glancing at the bot | "Is it fine?" answered in one look, calm, no noise | `btop` (state at a glance) |
| Reviewing a post | Careful but quick: read, decide, next; never decide unread | Newsboat (read an item, one key to act) |
| Finding why a post got its verdict | Certain: the reason is on screen | `lazygit` (select an item, the details show beside it) |
| Tuning keywords | Safe: see the effect before saving, undo by not saving | GNOME Settings' lists |
| The command line | Plain, scriptable, honest | `gh` (clig.dev) |
| Messages to the user | Rare and worth reading | Signal's system notices |
| What the channel's readers get | Only real matches, the original post untouched (today a short fallback text when forwarding is blocked, #80) | a curated job-alert channel |

Tie-breakers for UI choices this profile doesn't settle (1.3), **(owner)**, proposed: the reference product named
above for that part; then Textual's widgets for the screen and clig.dev for the command line. Until the owner
confirms them, they settle nothing (0.2).

The intent orders in the screen table, the main-task targets and the budgets are also the agent's proposals,
**(owner)**, until the owner confirms them.

## Core interactions (with feel specs)

- **Deciding a review post** (`s` / `k` / `d`, or the buttons). The spec: the decision shows within 100 ms and the
  next post appears; keys wait `DECIDE_DELAY` after a post appears, and the wait is visible, so a double tap can't
  decide an unread post; any decision the bot hasn't acted on can be undone; success is a one-line note saying what
  happened; on failure (state locked, decided elsewhere) a plain-words note explains it. Reference: Newsboat's
  mark-and-next. **Today** the wait isn't shown, `u` undoes only the last decision of this run, and a post decided
  elsewhere drops from the list (#76).
- **Picking a search result.** Moving the selection shows that post's verdict and reasons beside the list within
  100 ms. Reference: `lazygit`'s list and detail pane.

## Users

- **The owner:** the main user of everything. A programmer and fresh graduate who runs the bot on their own laptop,
  opens the screen now and then, works through the review queue, searches, and tunes keywords. Reads Arabic and
  English. They fear:
  - the bot silently stopping and missing jobs;
  - a wrong or duplicate post in their public channel;
  - breaking the config or the running bot;
  - losing their review labels.

  Evidence: the owner's own requests in sessions (2026-11-17 to 19) and their use since the switch. Their own access
  needs: not yet asked (owner question).
- **Other installers** (assumption: none known yet): people who follow the README to run it for their own job hunt.
  They know Linux and a terminal, but not this code. They fear a long build, a Telegram ban, and breaking their
  account. This is why FIELD1 and T11 apply.
- **Recipients** (never run it): the readers of the owner's public channel, job seekers in Basra. They see the
  forwarded original posts, and the bot's short fallback text when forwarding is blocked (#80). They fear wrong, stale or duplicate posts, and noise. Evidence: assumption.
- **Others affected:** the authors and admins of the watched chats, whose posts are republished (T13). Evidence:
  assumption.
- **Where and how:** at a desk, in a terminal beside the running service, a few minutes at a time, often interrupted;
  sometimes over `ssh` or inside `tmux` (the screen's README covers both).
- **Abilities:** the floor applies (A1 to A5, A10, A18 to A23, met by the means below, never N/A).
  - Colour vision deficiency: A5, every state has a word.
  - Low vision: the terminal's own font size; checked as the cell grid a 200 % font gives on the typical screen
    (A4, about 80 × 24 on a 1920 × 1080 laptop).
  - Motor: keyboard only or mouse only (A18). Chords (`ctrl+s`, `ctrl+r`, `ctrl+q`) need an alternative (A22).
    Copying a post needs a mouse drag today (A20, #78).
  - Tremor: the review double-tap guard (`DECIDE_DELAY`) and the safe focus in dialogs (S2, #55).
  - One hand: every main-task key is a single key or has a button; chords are the gap (A22).
  - Cognitive: plain words (A24), one decision per dialog, nothing to remember (the footer shows the keys).
  - Photosensitivity: nothing flashes (A10 passed). The Dashboard refreshes every few seconds by itself; it must be
    possible to pause it (A11; not possible today, #82).
  - Deafness: the product makes no sound.
  - **Blindness: the non-visual path is incomplete (tracked in #70; the fix's reviewer rates it, at least severity 1 for "find why", 9.5).** The screen draws in a terminal grid that
    Kitty doesn't expose to screen readers. The command line is the non-visual path where it exists:

    | Main task | Non-visual path today |
    |---|---|
    | See whether the bot is fine | partly: `systemctl --user status tele-notify`, `telenotify stats` (no connection or alerts) |
    | Review a post | partly: `telenotify review` (send, discard, next; no undo, no match-no-send, 40 lines of the post) |
    | Find why a post got its verdict | none (`check` only re-judges a file you give it) |
    | Read the numbers for a period | partly: `telenotify stats --days:N` |
    | Edit keywords, watch chats | editing `config.json` in a text editor (chats by ID only) |
    | Start, stop, restart | `systemctl --user` (outside the product) |
    | Undo a review decision | none |
    | Match, don't send (`k`) | none |
    | Remove duplicate keywords, revert | editing `config.json` |
    | Switch dry run and real, the target chat, publish-when-unsure | editing `config.json` and restarting with `systemctl --user` |
    | Test settings on the corpus | partly: `telenotify eval` (no before / after) |
    | Remove a wrong post from the channel | none (not built, #69) |
    | Install, log in, update | the README steps and command output |

    The gaps are tracked in #70; none is recorded as N/A. "Find why a post got its verdict" has no path at all, so
    that gap shuts a blind user out of a main task (severity 1 by 9.5). Orca
    reading the screen itself in GNOME Terminal (VTE exposes its text) is an owner-check item below.
- **Age:** adults only; no children use it.
- **Languages:** the UI is English. Content (posts, chat titles, keywords) is Arabic, English and mixed, with emoji,
  numbers, links, emails and phone numbers, from strangers: untrusted.

## Platforms

- **OS:** Linux with a systemd user session. Nothing else is supported.
- **Typical device:** the owner's laptop (Ubuntu GNOME, 37.5 GiB RAM, a 1920 × 1080 class screen).
- **Oldest and smallest supported device (owner):** proposed: a 2-core machine with 4 GB of RAM, simulated by
  running the benchmark walk under a 2-CPU, 4 GB memjob limit. On it, TDLib builds with `JOBS=1` (about 4 GB per
  parallel job, `deploy/build-tdlib.sh`), which takes about an hour.
- **Terminals:** Kitty 0.47 (main); Ptyxis and GNOME Terminal (VTE); Zed's terminal; inside `tmux` and over `ssh`.
  All truecolour.
- **Sizes:** 80 × 24 smallest (80 × 23 inside `tmux` with its status line, also checked); 120 × 40 typical; 200 × 50 largest checked.
- **Themes:** `textual-dark` (default) and `textual-light`, switched in Textual's command palette (`ctrl+p`), not
  saved between runs (#73). The palette also offers 19 other themes that aren't supported (#81).
- **Inputs:** the keyboard and the mouse are both first-class in the screen; the keyboard alone in the command line.
- **Assistive technology:** the terminal's font size; GNOME's High Contrast and Large Text through the terminal;
  GNOME's Reduced Motion and animations off (Textual's own animations must follow them, A11; Textual has
  `TEXTUAL_ANIMATIONS=none`); Orca through the command line in VTE.
- **Network:** always online in normal use. Telegram outages and the laptop being off are normal states: the bot
  catches up afterwards.

## Main tasks

Targets proposed 2026-11-19, **(owner)**. Actions are counted by the harness's keyboard-only (K) and mouse-only (M)
scripts, from the start state named in brackets, with each key, click and arrow press as one action; waits such as
`DECIDE_DELAY` are excluded. Where a target says "K/M", both scripts must meet it.

| Task (users' words) | Who | How often | Done means | Targets |
|---|---|---|---|---|
| Matching jobs reach my channel on their own | owner, recipients | continuous | each match is forwarded once | ALERT1 latency below; ALERT2 score |
| See whether the bot is fine | owner | daily | service state, connection, dry run or real, pending reviews and alerts read on the Dashboard | 0 actions (screen just opened); first useful screen within 2 s warm on the 1M-post state file |
| Start, stop or restart the bot | owner | weekly | the service is in the asked state, and the screen says so | M 2 (Dashboard open); K: no keys exist today (#83), so Tab presses count |
| Review an unsure post (sends to the public channel) | owner | daily | the post is sent, discarded or kept, labelled, with undo offered | K/M 1 per post (Review open, post read); from Dashboard +1 |
| Undo a review decision | owner | weekly | any decision the bot hasn't acted on is back in the queue, its label removed | K/M 1 for the last one; K/M 3 for an older one from History (today only the last of this run, #76) |
| Find why a post got its verdict | owner | weekly | the post's verdict and reasons are on screen | K: `3` + the words + 1; M: 1 + the words + 1 (Dashboard open) |
| Read the numbers for a period | owner | weekly | the period's counts and chart are on screen | K: `p` + arrows + enter, at most 6; M 2 (Dashboard open) |
| Add, edit or delete a keyword, then save (deletes data) | owner | weekly | the change is in `config.json`, a backup kept, the bot restarted if asked | 4 actions + typing |
| Remove duplicate keywords (deletes data) | owner | rare | each list keeps one spelling, then saved | K/M 1 + save (Settings open) |
| Revert unsaved settings (throws away edits) | owner | rare | the screen shows `config.json` again | 2 actions (Revert, confirm) |
| Watch or unwatch a chat, seeing who can read it (T13) | owner | monthly | the chat is (un)watched in the saved config | K/M 4 (Settings open); the source's audience shown (not built yet, #68) |
| Change the target channel (sends to others) | owner | rare | matches go to the new chat after save and restart | 4 actions + confirm |
| Switch between the dry run and the real bot (live system, starts public forwarding) | owner | rare | the mode is saved, the bot restarted in it, and the screen shows which | K/M 4 + confirm (fails today: needs a reopen, #77) |
| Turn "publish when unsure" on or off (sends to others) | owner | rare | the option is saved and in effect after restart | 4 actions |
| Test settings on the corpus before saving | owner | monthly | the corpus result is shown next to the saved settings' result | 1 action |
| Remove a wrong post from the channel (MSG6) | owner | rare | the post is gone from the channel | not built yet (#69) |
| Report a problem (FIELD1) | other installers | rare | a report with diagnostics, private data removed, reaches the owner | not built yet (#72) |
| Change how long post text is kept (`keep_text_days`, deletes data) | owner | rare | the setting is saved; the screen says what will be cleared | not in Settings yet (config only, #84) |
| Open the screen the first time (no state file, no chat list yet) | owner, other installers | once | the screen says what's missing and the next step | 0 actions; within 60 s to the first main task |
| Install, log in and start the service (first setup) | owner, other installers | once | the bot runs from `~/.local/bin`, logged in, as a user service | README steps only, each warning explained beforehand; TDLib build time measured and stated in the README (about an hour on one core today); the rest within 10 min |
| Update after `git pull` (live system) | owner, other installers | monthly | `deploy/install.sh` rebuilt and restarted it, or said why not | 1 command; PASS/FAIL and rollback (#64) |
| Log in again after Telegram logs the account out (recovering access) | owner | rare | `telenotify login` works and the service restarts | the screen says what to run (#65); within 3 min |
| Log out and uninstall (leaving) | owner, other installers | rare | the session, service, programs and data are gone, or kept where the README says | not documented yet (#72) |
| Change the Arabic display mode or the theme (the accessibility settings) | owner | rare | the mode is in effect | 1 flag or variable for Arabic; theme through the command palette, not saved (#73) |

First run budget: 3 screens before the first main task (default).

## Screen table

The screen's tabs keep their rows in the tab table in `tui/README.md` (approved by the owner on 2026-11-18). This
table adds the intent order and the rest of the product. One row per layout; groups list every member.

| Screen or group | Job | Must be visible at smallest size | Primary action | Input per method | Intent order |
|---|---|---|---|---|---|
| Dashboard tab, Feed view (`f`, default) | bot health, start / stop, numbers, live feed | as the tab table | none (state screen) | `p` / click the picker | 1 service and connection state; 2 dry run or real, pending reviews, alerts; 3 the period's numbers; 4 the live feed |
| Dashboard tab, Chats view (`c`) | numbers per chat for the period | the state line and the chat table's first rows | none | `c` / click | 1 the chat table; 2 the period |
| Dashboard tab, Alerts view (`a`) | read alerts; see each watched chat's health | the state line, the newest alerts, the chat-health table's first rows | none | `a` / click | 1 the newest alert; 2 its time; 3 chats that can't be read |
| Review tab (post view) | decide unsure posts | as the tab table | Send | `s` / click Send | 1 dry run or real (what Send will do); 2 the post (chat, text); 3 its reasons; 4 the decision buttons |
| Review tab, History view (`h`) | see and undo recent decisions | the recent decisions and Undo | Undo | `u` / click | 1 the last decision; 2 Undo |
| Search tab | find a post and why | as the tab table | none (typing searches) | type / click a row | 1 results with verdicts; 2 the chosen post's reasons; 3 the search box |
| Search tab, paste a post (`p`) | try a post | the paste box and its verdict | Judge | type, then the key / click | 1 the box; 2 the verdict and reasons |
| Settings tab, list sections (Roles & levels, Places, Other filters) | edit a list | as the tab table | Save | `ctrl+s` / click Save | 1 the chosen list; 2 saved or unsaved and problems; 3 Edit / Delete / Add |
| Settings tab, Chats (chat table) | watch, unwatch, pick the target | as the tab table | Save | `ctrl+s` / click Save | 1 the chat table with watched marks; 2 the target chat; 3 saved or unsaved |
| Settings tab, Chats (chat-ID editor, before the bot has saved a chat list) | type chat IDs and the target | the ID list, the target field, Save | Save | `ctrl+s` / click Save | 1 why it's IDs; 2 the fields; 3 saved or unsaved |
| Settings tab, Bot | dry run, alerts, paths | the options and the saved marker | Save | `ctrl+s` / click Save | 1 the options; 2 saved or unsaved |
| Dashboard confirm dialog (`ConfirmScreen`: start, stop, restart the service) | a decision now | the question, the service and mode, both buttons | the safe choice has focus | `esc` / `n` cancels | 1 the question; 2 the consequence; 3 the buttons |
| Settings confirm dialogs (`ConfirmModal`: revert unsaved changes; use the file's value after an outside change; save and restart; quit with unsaved changes) | a decision now | the question, the object, both buttons | the safe choice has focus | `esc` / `n` cancels | 1 the question; 2 the consequence; 3 the buttons |
| Dates form (`RangeScreen`, custom period) | type a range | both fields, the error, the buttons | Show | enter / click | 1 the fields; 2 the error; 3 the buttons |
| Filter-read view (`CheckModal`, review `r`) | show what the filter read from links | the text and Close | Close | `esc` / click Close | 1 the text; 2 Close |
| Corpus test view (`EvalModal`, settings `t`) | before / after numbers on the corpus | the before / after table and Close | Close | `esc` / click Close | 1 the changed counts; 2 Close |
| Command palette (Textual's, `ctrl+p`) | switch the theme, run commands | the search field and the matches | the chosen command | `ctrl+p` / click the header's icon | 1 the field; 2 the matches |
| Toasts (`app.notify`: save, error and restart results) | say what happened | the whole message while shown | none | n/a | 1 the result |
| Command line: `check`, `eval`, `stats`, `compare`, `run` output, usage and errors | as its help says | readable at 80 columns | n/a | keyboard | 1 the result or error; 2 the next step |
| Command line: `telenotify review` (sends to the public channel) | decide unsure posts | the post, its reasons, the prompt | n/a | keyboard | 1 the post; 2 the reasons; 3 the prompt and what each key does |
| Command line: `telenotify-tui --help`, its flags and start-up warnings | start the screen | readable at 80 columns | n/a | keyboard | 1 the usage; 2 the warning |
| Command line: `login` prompts | log in to Telegram | each prompt says what it wants | n/a | keyboard | 1 the question; 2 what it's for |
| Command line: `deploy/install.sh` and `deploy/build-tdlib.sh` output | install, update, build TDLib | each step and the final summary | n/a | keyboard | 1 PASS / FAIL per part; 2 what to do |
| Messages: alerts in the owner's Saved Messages (back after downtime, no connection, a watched chat lost, a backlog cut, a send failed) | tell the owner something needs them | the first line says why | n/a | Telegram | 1 why; 2 what to do |
| Messages: forwards to the public channel | the original post, unchanged | n/a (Telegram's UI) | n/a | n/a | n/a |
| Messages: the fallback "match / post link" text in the public channel (when forwarding is blocked) | point readers to the post | the role and a link readers can open | n/a | Telegram | 1 what the job is; 2 the link (#80) |

## Budgets (with tolerance) and the benchmark walk

Tolerance: 5 % or the measurement's noise, median of 10 runs (policy 1.5). Baselines are measured in the first
evidence run (phase 2) and written here then.

All screen budgets are measured on the 1-million-post synthetic state file, under the minimum-device limits above.

- Screen start-up to the first useful Dashboard: 2 s warm, 5 s cold (default). The harness measured about 4.5 s for
  the first tab on the large state file (#56).
- Local interactions (tab switch, selection, typing): 100 ms (P7).
- No event-loop block over 1 s (P1), from asyncio's slow-callback log.
- **ALERT1, latency (owner):** proposed: from a source post's Telegram date to its forward in the channel, 95 % under
  60 s while connected, with the catch-up after downtime reported separately (its own median and 95th percentile).
  **Not checked** until the bot records each post's Telegram date (#74); then also a timed run with a fake Telegram
  in the benchmark walk.
- The bot's own memory: under 200 MB resident (proposed; measured in phase 2).

**Benchmark walk:** open the screen on the harness's synthetic state file with 1 million posts (about 10 times the
real one), visit every tab and view, open the Activity box for all time, search a common word, pick a result, open
Settings and the Chats table, decide 3 review posts.

## Real-data evaluation (1.5, Z1, ALERT2)

- **What:** `telenotify eval` on the private corpus (`~/.local/share/tele-notify-corpus/eval.jsonl`), run locally;
  only aggregate counts go into PRs (this project's CLAUDE.md already requires them for filter changes).
- **Combined score (owner):** proposed **F0.5** over posts labelled TRUE and FALSE, where a publish is a positive.
  F0.5 weighs precision twice as much as recall: a wrong publish reaches every reader of the channel, while a missed
  one costs only the owner. Review counts as not published.
- **Review guard (owner):** F0.5 can be raised by sending more posts to review, which costs the owner time. So every
  filter PR also reports the TRUE→review and all→review counts, and neither may rise without the owner's OK.
- **Same corpus, same run:** the corpus grows as the owner reviews. Every filter PR runs main and the branch in the
  same session on the same corpus file and reports its line count and sha256 (no text).
- **Snapshot and held-out part (owner):** only the owner cuts the fixed snapshot and its held-out part (stored
  encrypted outside every repository). Until then there is no held-out score, so nothing ratchets yet. Whether the
  visible corpus's score may stand in meanwhile is an owner question. PRs still report it and may not lower it
  without the owner's OK.
- **Held-out tolerance:** one item, or the bootstrap 90 % interval, whichever is wider.

## Owner budget (owner)

Proposed: 30 minutes a month.

## Declarations

None.

## Modules

7.6 TUI (the screen), 7.7 CLI (`telenotify`, `deploy/install.sh`, `deploy/build-tdlib.sh`), 7.8 MSG (alerts and
forwards). Not 7.1 to 7.5.

## Design system

The platform's: Textual's widgets, its `textual-dark` and `textual-light` themes and their variables, plus
`tui/telenotify_tui/app.tcss`. Additions:
- **Verdict styles (target):** publish uses `$success`, review `$warning`, reject `$error`, and each always has its
  word (A5). **Today** the code uses literal Rich colours (`bold green/yellow/red`, `dim`) in Search and the Dashboard
  (#75).
- **Spacing:** 0, 1 or 2 cells.
- **Glossary:** post, verdict (publish, review, reject), review queue, send, discard, match no send (`k`), chat,
  watched, target chat, dry run, real bot, period, corpus.
- **Voice:** plain and short; say what happened and what to do; no exclamation marks.
- Exceptions to V3 (literal colours): none; the literals today are fails (#75).

## Evidence tools and synthetic data

- **Screen:** the isolated harness `tui/audit/` (branch `ui-audit-harness`, to merge in phase 2). It uses a temporary
  `HOME`, fake `systemctl`, `xdg-open`, `gio` and `BROWSER`, and refuses the real data folders. Textual pilot
  screenshots (SVG, and PNG through `rsvg-convert` for eyes) at 80 × 24, 120 × 40 and 200 × 50, both themes. It also
  collects keys, contrast, timing, hostile text, failures, task scripts, styles, and an interactive mode for the
  owner. Until it is merged, reviewers run the same steps by hand from its branch, or from the checklist in
  `docs/UI-UX-POLICY.md` 3.3.
- **Command line:** golden-output tests (to add in phase 2: help, normal, error, summary).
- **Messages:** unit tests that render each alert from synthetic input.
- **Synthetic data:** invented posts, chats and keywords in the harness (`tui/audit/data.py`), at `example.com`.
  Its statistics (posts per day, longest Arabic and English chat titles, the mix of scripts) are re-measured from
  the real state at each full audit, as counts only.
- **Evidence store:** the PR (comments and CI artifacts) and `docs/ui-audit/YYYY-MM.md` for full audits.

## Applicability list (N/A rules, with reasons)

| Rules | Why |
|---|---|
| WEB, GNOME, AND, WIN, GM, READER, VIEW, SHARE, TRAIN, FIELD2, FILTER, HAND1 | not those platforms or kinds |
| A6, A13 to A16, A29, A30 | no pictures behind text, no video, no sound, no generated documents, no haptics |
| N5 | no URLs or incoming links |
| T9 | no children use it |
| L1, L6, and L4's UI-locale part | the UI is English only (L4's search part applies: Arabic letter forms and diacritics) |
| D7 | no updater of its own (`deploy/install.sh` is run by hand) |
| MSG4's command menu | it acts as a user account, not a bot; its controls live in the screen (MSG4 says so) |

Not N/A, so they apply: A20 (copying needs a drag, #78), O1 to O4 (alerts reach the phone through Telegram; the
Dashboard's alert count, #79), A3 and A4 (met through the terminal's font, checked as above), A12 (see Users), A19 (pointer
targets: terminal cells, and a destructive button spaced from others; Delete sits beside Edit and Add, and Discard
beside Send: checked in the first full audit), A22, T2 and T4 (a Telegram session; the bot
fetches job pages and runs OCR on images), ST3 (Telegram outages and catch-up), ST5 (the screen, `telenotify review`
and the bot edit the same state and config).

## Not-checked reasons (owner)

Proposed; none is accepted until the owner writes so. Checks on the owner's laptop, Kitty, VTE and the mouse are
**not** Not-checked reasons (the typical device and a first-class input, policy 1.6). They are owner-check items at
each release (8.5) unless agent evidence covers them:
- how it really looks and feels in Kitty and VTE, and whether an OSC 52 copy reaches the clipboard (about 5 min);
- Orca reading the command line's output in GNOME Terminal (about 10 min);
- whether alerts in Saved Messages notify the owner's phone (about 2 min);
- Orca reading the screen itself in GNOME Terminal (about 10 min).

Proposed reasons, each a severity-2 tracked item due by the next release, expiring 2027-05-19:
- a real Telegram ban or log-out of the account (needs the live account; covered with fakes in tests; #85);
- ALERT1, alert latency, until #74 records each post's Telegram date, and its last leg to the phone (#86).

Where the project policy (`docs/UI-UX-POLICY.md` 3.3) accepts "how it looks in Kitty or VTE" as Not checked, this
profile is stricter and wins: those are owner-check items (policy 1.6).

## Third-party surfaces

- Telegram's apps around the forwarded posts and the alerts (how a forward looks in the channel). The alerts are
  sent from the owner's own account to their own Saved Messages, and Telegram may not notify for those (to check).
- TDLib's login flow is worded by `telenotify login`.

## Law and platform duties, T0 (owner)

The owner keeps this checklist. To start it (agent's draft, owner to complete):
- Telegram's API Terms of Service for an automated user account (rate limits, no spam, no misuse of others' data).
- Republishing others' posts: only from public sources, or with the admins' agreement (T13).
- Iraq's and the owner's local rules on republishing job adverts: owner question.

## Review brief

`docs/ux-review-brief.md`, version 1.

## Waivers

None.

## Waiting for the owner

1. Hook-up lines: add policy Appendix G to AGENTS.md or this project's CLAUDE.md, and say whether the terminal screen
   is inside the delegation (policy 0.2).
2. Content policy wording; experience intent and reference products.
3. The combined score (F0.5 proposed), the snapshot and held-out part, and whether the visible score stands in until
   then.
4. The owner budget (30 min/month proposed).
5. The Not-checked reasons above, and your own access needs.
6. The T0 checklist.
7. The public channel's description (MSG6): the agent drafts it, the owner changes it.
8. The tie-breakers, every screen's intent order, the main-task targets, the budgets (start-up, ALERT1's 60 s,
   memory), the minimum device and the review guard's limit: all proposals until confirmed.
9. Remove the two synthetic test labels (ids `-2:7` and `-1:11`) from `~/.local/share/tele-notify-corpus/review-labels.jsonl`.

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
