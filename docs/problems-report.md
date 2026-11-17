# tele-notify: problems report

Written 2026-11-17, against the `testing` branch at commit `6d49bcc`.

> **Status (updated as the rewrite progresses).** Sections 3 and 4.1–4.5 are fixed in the Nim rewrite (stages 1–2), as are 5.1 (OCR) and most of 7: tests, CI, and a `requirements.txt` that installs again (including the missing `beautifulsoup4`, `lxml` and `urlextract`). Sections 4.6–4.13, 5.2–5.6 and 6 belong to stage 3, the Telegram listener. Progress is tracked in [`rewrite-guide.md`](rewrite-guide.md#roadmap).

Each problem below says what is wrong, why it matters, and how to fix it. Each one is also marked by how I know:

- **[tested]**: I reproduced it by running the code.
- **[read]**: I found it by reading the code but did not trigger it.

---

## 1. Short version

1. **Most of what gets published is wrong, and almost all of it comes from stage 2.** I replayed your Saved Messages through the current `main.py` (details in section 2). Of 115 distinct posts it would publish, 96 are false positives. 80 of those 96 come from stage-2 inference: "a role word was found and no experience requirement was detected, so it must be entry level." That logic treats *missing evidence* as *evidence*, so a student car-share ad that mentions "علوم حاسبات" plus a Basra district gets published.
2. **Some messages crash the handler and are lost.** Examples are a LinkedIn page with unexpected HTML, a non-URL button, or a Workable link without a trailing slash. The exception escapes, the message is never marked as handled, and it is never retried.
3. **The bot overwrites your config.** Recent-message state is stored inside `config.json`, and the bot rewrites the whole file every time it forwards something. Any keyword edit you make while it is running is silently thrown away.
4. **A fresh install is impossible.** `requirements.txt` asks for `regex>=20225.11.3` (year 20225), which pip cannot resolve. `bs4`, `lxml` and `urlextract` are also missing from it.
5. **There is no test suite, so every regex change is a gamble.** This is the root of issue #9 ("a never stopping process"). The corpus I built during this review is the start of a fix.

---

## 2. False-positive test against your Saved Messages

### What I did

- **Export.** I read Saved Messages from 2026-11-17 back to 2025-06-05, using a *copy* of the deployed session (the bot was not running). That gave 333 messages, 56 of them with photos.
- **Link stubs.** 105 messages were only "match: … / post link: …". I fetched the original posts; 59 still exist and 46 have been deleted by their channels.
- **Classifier run.** I ran every job-like item (229 of them) through the real `check_for_a_match()` from `main.py`, with your deployed keywords, real OCR and real scraping, and with Telegram stubbed out. I skipped your personal notes.
- **Labelling.** Two independent reviewers (fresh agents) labelled each would-be-published post against a written rubric: real job, Basra or remote, IT/electrical/control role, and reachable for a fresh graduate. I spot-checked a sample by hand and agreed with the labels.

The corpus and results live outside the repo, in `~/.local/share/tele-notify-corpus/` (mode 700). They contain your private messages and must never be committed.

### Results

| Outcome of current code | Count |
|---|---|
| Would publish to channel | 119 (115 distinct) |
| Would send to you for review | 86 |
| Rejected | 21 |
| **Crashed** | 3 |

Of the 115 distinct would-be-published posts:

| Label | Stage 1 (level keyword found) | Stage 2 (inference) | Total |
|---|---|---|---|
| Correct | 3 | 6 | 9 |
| Unsure | 1 | 9 | 10 |
| **False positive** | 16 | **80** | 96 |

Why they were false:

| Reason | Stage 1 | Stage 2 |
|---|---|---|
| Not a job at all (courses, students, "network of experts", school notices) | 5 | 24 |
| Wrong role ("degree in Engineering" pulls in HSE, accounting, civil…) | 1 | 20 |
| Experience required but not detected | 3 | 15 |
| Senior/lead/supervisor title not detected | 0 | 11 |
| Wrong location ("شارع البصرة" in Baghdad, Maysan project by a Basra company) | 4 | 8 |
| Mandatory certification | 2 | 1 |
| Multi-job post mix-up | 1 | 1 |

**Important bias.** Saved Messages is not a random sample. It is mostly posts you saved *because* something looked wrong, plus the bot's review forwards. So "83% false" does **not** mean 83% of your channel is wrong. It does show *which kinds* of mistakes exist and how common each kind is relative to the others. The corpus is a ready-made regression suite.

The stage-1 false positives were triggered by: `junior` (4), `متدرب` (2), `خريج جديد` (2), `apprenticeship` (2), `فرصة تدريب` (2), and one each for the others.

---

## 3. Filtering accuracy problems

### 3.1 Stage 2 treats "no experience found" as "entry level" [tested]

**What.** For mid-level role words, a post is published when the experience, certification and responsibility patterns all fail to match.

**Why it's a problem.** These patterns can only recognise a small set of phrasings, so *any* post that phrases its requirements differently, or is not a job at all, passes. This one rule produces 80 of the 96 false positives.

**Fix.** Turn the decision around. Collect positive evidence of entry level (fresh graduate, 0–1 years, "graduated within the last 12 months", training program, graduate program) and negative evidence (years ≥ 2, senior/lead/supervisor/manager, mandatory certs). Then decide with a small score or rule table:

- Positive evidence and no negative evidence: **publish**.
- Negative evidence: **reject**.
- Neither: **review**, not publish.

Stage 2 then stops being "publish by default."

### 3.2 Stage 1 never checks experience [tested]

**What.** If a level keyword (`graduate`, `junior`, `متدرب`…) appears anywhere, the post is published immediately.

**Why it's a problem.** For example, "Electrical engineering graduate with 5 years of experience" is published. Words like "graduate" also appear in degree requirements ("graduate of an accredited university").

**Fix.** Treat level keywords as positive evidence in the same scoring step (3.1), so a hard requirement such as "5 years" still overrides them.

### 3.3 The "senior/lead" guard only protects English keywords [tested]

**What.** `build_hybrid_regex(..., mid_level=True)` puts `(?<!(lead|senior|Sr).{0,30})` in front of `en|ar|special` without wrapping the alternation in a group. A regex `|` binds loosest, so the guard applies only to the first (English) alternative.

**Why it's a problem.** "Senior مهندس كهرباء" and "Senior … control engineer" are not blocked.

**Fix.** Wrap the alternatives: `guard + "(?:" + "|".join(parts) + ")"`. Add Arabic seniority words (أول، رئيس، مدير، مشرف، خبير) and English ones (supervisor, manager, principal, head, specialist with years).

### 3.4 Certification "are a plus" escape is broken [tested]

**What.** In `build_certifications_regex`, the pattern `relevant certifications.{0,20}{cert}?(?!.{0,30}are a plus)` has an optional cert. The `.{0,20}` can also back off until the negative lookahead no longer sees "are a plus".

**Why it's a problem.** "Relevant certifications such as CCNA are a plus" and "Relevant certifications preferred" both count as *mandatory* certs. That produces false *negatives* (good jobs sent to review).

**Fix.** Check for "plus / preferred / advantage / يفضل / ميزة" in the same sentence and treat that as optional. Don't rely on a lookahead after a variable-length gap.

### 3.5 The experience regex misses common phrasings [tested]

These are missed:

- `Experience: 5+ years`
- `5 yrs experience`
- `two years of experience`
- `3+ years in accounting`
- `خبرة عالية شرط لا تقل عن 10 سنوات`
- `من 5 إلى 12 سنة`

**Why it's a problem.** It is one giant hand-written regex, which is very hard to extend safely.

**Fix.** Split it into small parts:

1. Find a number of years: digits (Latin and Arabic-Indic), number words (one…ten, سنة/سنتين/ثلاث…), ranges and `+`.
2. Look within a window of about 60 characters for "experience / خبرة / خبره".
3. Extract the *minimum years* as a number, then compare it with a threshold (≤ 1 is fine for you).

This also handles issue #25 properly. "Apply anyway" language only matters when the minimum is 1 year or less; it never overrides a 5-year requirement.

### 3.6 Location matches anywhere in the text [tested via corpus]

**What.** "شارع البصرة" (Basra Street in Baghdad), "Basra Gas Company" in a Baghdad posting, and "a Basra company, project in Maysan" all count as Basra.

**Fix.**
- Add a negative list (شارع البصرة, Basra street…).
- Detect *other* governorates; when another governorate is named as the work location ("موقع العمل", "Location:", "📍"), it should win over a mere company-name mention.
- Prefer location found on a `Location:`/`📍`/`موقع العمل` line.

### 3.7 Role keywords are too broad [tested via corpus]

**What.** `degree in Engineering`, `electrical engineering` and `علوم حاسبات` appear in the *requirements* of unrelated jobs (HSE officer, cost engineer, school teacher).

**Fix.** Separate *job-title* matches (strong) from *degree/field* matches (weak). Look for the title on title-like lines: the first lines, `Position:`, `المسمى الوظيفي`, numbered list items. A degree-only match should go to review, not be published.

### 3.8 Multi-job posts (#29) [tested via corpus]

**What.** The whole post is one blob, so the role from job 1, the level from job 3 and the location from the header get combined.

**Fix.** Your line-expansion idea in #29 is right. A cheaper first step is to split the post into *job blocks*. Split on numbered items (`1-`, `1.`, `1️⃣`), separator lines (`━━━`, `———`), and repeated `Position:`/`المسمى الوظيفي` headers. Then evaluate each block separately, letting each block inherit the header's location if it has none of its own. Publish if any block passes.

### 3.9 "Not a job" filters are a few hard-coded phrases [tested via corpus]

**What.** The job-seeker, tuition and trivial patterns cover only a handful of phrasings, and 29 of the false positives were not jobs at all.

**Fix.**
- Require at least one *job-ad signal*: مطلوب، تعلن، فرصة عمل، hiring، vacancy، apply، CV، السيرة الذاتية, a Position line, etc.
- Move the blacklist phrases to config so you can add them without editing code.

### 3.10 Keywords are not escaped in two places [read]

`build_responsibilities_regex` joins `responsibility_keywords` without `regex.escape`. A keyword like `C++` or `.NET` would break the pattern or silently change its meaning. `certifications` is escaped, but the responsibility keywords are not.

---

## 4. Correctness bugs (crashes, lost or duplicated messages)

### 4.1 Unexpected scraped HTML crashes and loses the message [tested: 3 of 229 crashed]

**What.** `soup_alpha.find(...).get_text()` raises `AttributeError` when LinkedIn serves a login wall, a different layout, or an expired job. `retry_transient` doesn't retry `AttributeError`.

The exception escapes the live handler, so the message is never marked read and never reconsidered. In the unread bootstrap it aborts the whole chat, and `send_read_acknowledge` is skipped.

**Fix.** Make every scraper return "no text" on failure and log a warning. Scraping is an *enhancement*; it must never take down the evaluation of a message. When scraping was needed but failed, send the post to review.

### 4.2 Workable URL is built wrong [tested]

**What.** It builds `…/accounts/acme-1/jobsDAF36R174B` (missing `/`). Links without a trailing slash crash with `None.group()`.

**Fix.** Use `f".../accounts/{account}/jobs/{job_id}"` and parse the link with a single regex that has an optional trailing slash.

### 4.3 Non-URL buttons crash message processing [read]

**What.** `msg.buttons[0][0].url` is `None` for callback buttons, and `msg + " " + None` raises `TypeError`.

**Fix.** Collect the URLs of *all* buttons that have one.

### 4.4 Posts with several links never get scraped [read]

**What.** In `scrape_full_job`, the "is the rest ≤ 10 characters" check runs *inside* the loop that removes links one at a time. With two links, the second link is still in `remaining` when the first is checked, so the check fails.

**Fix.** Remove all links (and filler) first, then decide once.

### 4.5 Photo-only posts never get scraped [read]

**What.** `message_processor` only scrapes when `msg.raw_text` is non-empty. A photo post with a URL button is never scraped.

**Fix.** Scrape based on the URLs found anywhere (text, buttons, OCR), not on the presence of text.

### 4.6 Unread messages can be skipped [read]

**What.** `last_read_id = dialog.message.id - dialog.unread_count` assumes message IDs are consecutive. Channels delete posts all the time (46 of your linked posts are gone), so this lands too high and silently skips real unread posts.

**Fix.** Use the dialog's actual `read_inbox_max_id` (`dialog.dialog.read_inbox_max_id` in Telethon).

### 4.7 The backlog is processed newest first [read]

**What.** `iter_messages` defaults to newest first, so the channel receives the backlog in reverse order.

**Fix.** Use `reverse=True` (oldest first).

### 4.8 Duplicates slip through when posts arrive together [read]

**What.** Telethon runs handlers concurrently by default (`sequential_updates=False`). Two channels cross-posting the same ad at the same moment both run `check_for_duplicates` *before* either one calls `add_to_recent`. The lock only protects the write, not check-then-add. This matches your "exact duplicate!!!" notes.

**Fix.** Do check + forward + record under one lock, or process messages through a single queue.

### 4.9 The bootstrap and the live handler can process the same message twice [read]

**What.** The `NewMessage` handler is registered before the unread bootstrap runs, so a message arriving during the bootstrap can be handled by both.

**Fix.** Use a processed-ID set per chat (persisted, see 5.2).

### 4.10 Retries can forward twice [read]

**What.** `new_message_handler` *and* `message_forwarder` are both wrapped in `retry_transient`, so up to 5 × 5 attempts. If the forward succeeded but `mark_read` hit a network error, the whole handler re-runs and the post is evaluated and forwarded again. Only the duplicate check saves you, and that is racy (4.8).

**Fix.** Retry only the single network call that failed, never a whole handler with side effects.

### 4.11 Review forwards happen inside the matching function and before the duplicate check [read]

**What.** `check_for_a_match` both decides *and* forwards to Saved Messages.

**Why it's a problem.** You get duplicate review forwards, and the function can't be tested without Telegram.

**Fix.** Make matching a pure function that returns a verdict (publish / review / reject) plus reasons. A separate step acts on the verdict.

### 4.12 Link fallback is broken for private channels [read]

**What.** It builds `https://t.me/{username}/{id}`, and `username` is `None` for private channels.

**Fix.** Use `https://t.me/c/{internal_id}/{msg_id}` when there is no username.

### 4.13 The download progress callback divides by `total` [read]

**What.** It prints to stdout and divides by `total`, which can be `0` or `None`.

**Fix.** Remove it; the progress is noise in the logs.

---

## 5. Reliability and operations

### 5.1 OCR blocks the event loop (#30) [read]

**What.** `pytesseract.image_to_string` is synchronous, so nothing else runs while OCR does (around 1–3 s per image).

**Fix.** `await asyncio.to_thread(...)`, or run `tesseract` as an async subprocess.

### 5.2 State lives inside `config.json`, and the bot overwrites the whole file [read]

**What.** Every forward rewrites `config.json` from the copy held in memory.

**Why it's a problem.** Edits you make while the bot runs are lost, and the bot needs write access to your secrets file.

**Fix.** Keep config read-only. Put state (recent messages, processed IDs, stats) in a separate SQLite file or `state.json`. SQLite also gives you the stats store that #12–#15 need.

### 5.3 Retry policy (#5) [read]

**Problems.**
- `aiohttp` HTTP errors other than `OSError` are not retried.
- There is no HTTP timeout, so the default is 5 minutes per request.
- `FloodWaitError` longer than 60 s is raised, not waited out.
- On startup with no network, Telethon gives up after 5 connection retries and the script exits.

**Fix.**
- Set an explicit timeout (about 20 s) on HTTP.
- Use one retry helper for network calls only.
- Retry forever, with capped backoff, for the Telegram connection at startup.
- Let systemd restart the process (`Restart=always`) as the outer safety net.

### 5.4 No crash or downtime alerts (#3, #16) [read]

**Fix.**
- Track the time of the last successful connection.
- On reconnect after a gap longer than N minutes, send a message to Saved Messages, or to a bot chat, saying how long it was down.
- On an unhandled exception, send the traceback summary.

### 5.5 Autostart (#6) is undocumented [read]

The wiki page "Autostart-setup" ends in "TBC". You already use a systemd unit; it should be in the repo as `deploy/tele-notify.service` and documented on that page.

### 5.6 Logging (#7) [read]

**What.** The logs say ✅/❌ but not *why*.

**Fix.** Log the verdict reasons the pure matcher returns (stage, matched role, location, years found, cert found). This is also what makes #14 (stage breakdown) trivial.

---

## 6. Code structure (#17)

- **One 500-line file with everything at import time.** Config is loaded, the client is created and patterns are compiled when the module is imported, so nothing can be imported for testing without a real `config.json` and a Telegram client. (I had to cut the bottom of the file off to test it.)
- **Mixed responsibilities.** `scrape_full_job` mixes "should I scrape?", URL rewriting, HTTP and HTML parsing for 7 sites in one function, with `match.group(5)`-style magic numbers. Each site should be its own small function that takes a URL and returns text (or nothing), registered in a table.
- **Copy-paste between versions.** `main_simple.py` duplicates about 60% of `main.py`, including the bare `except:`, and is drifting out of date.
- **Global mutable state.** `recent_messages` and `data` are module globals rebound with `global`.
- **TF-IDF duplicate detection is fitted on only 11 documents.** The numbers are unstable, and it pulls in scikit-learn (about 100 MB) for something that is 30 lines of code. A URL-based plus normalised-text-hash plus small cosine check is simpler and more predictable.

---

## 7. Packaging, repo hygiene, docs

| Problem | Why | Fix |
|---|---|---|
| `regex>=20225.11.3` [tested] | `pip install -r requirements.txt` fails | `regex>=2025.11.3` |
| `beautifulsoup4`, `lxml`, `urlextract` missing from requirements [read] | Fresh install crashes on import | Add them |
| `myenv/` points at Python 3.14 but its packages are for 3.13 [tested] | The repo's venv can't import telethon | Delete and recreate it; add `myenv/`, `.venv/`, `.ropeproject/`, `.ruff_cache/`, `__pycache__/` to `.gitignore` |
| `crash_alert.py` is an empty tracked file [read] | Confusing | Remove it, or implement 5.4 |
| `tools/retreive_specifc_msg.py` has `if dialog.id == :` [read] | Syntax error | Take the ID as an argument |
| `tools/web_scraping.py` is a scratch file with a hard-coded URL [read] | Not a tool | Turn it into `scrape <url>` (prints what each scraper extracts) |
| Template `config.json` is not valid JSON (`chat_id` placeholders) [read] | Users copy it and get a parse error | Ship `config.example.json` with valid example values; validate on startup with clear errors |
| Session name hard-coded to `"test"` [read] | Confusing; collides between versions | Make it a config option |
| README says Python 3.8+ and lists old deps [read] | Wrong | Update |
| README to-do list duplicates the issues [read] | Two sources of truth | Link to the issues instead |
| No tests, no CI [read] | Every keyword or regex change can silently break other cases | Corpus-based regression tests plus unit tests per pattern, run in CI |
| Wiki: Autostart page unfinished; Job-Boards says Khana/Workable are "testing only" [read] | Outdated | Update alongside the code |

**Outside the repo (no action taken).** `~/Projects/bqtechjobs/get_chat_ids.py` contains your API hash in plain text. It isn't in any git history (I checked this repo's full history: no secrets). Still, consider reading it from `config.json` like `main.py` does.

---

## 8. Should it be rewritten in Nim?

**My opinion: yes, it's a reasonable project.** With the caveats below, I'd do it as a staged rewrite rather than a big-bang one. I checked the risky parts rather than guessing:

| Piece | Python today | Nim option | Verdict |
|---|---|---|---|
| Regex (the heart of the filter) | `regex` module, variable-length lookbehind | `nim-regex` (pure Nim, already installed). **I tested:** variable-length lookbehind `(?<!(lead\|senior\|sr).{0,30})`, Arabic lookbehind and Unicode-aware boundaries all work | ✅ No blocker |
| Telegram client | Telethon | No mature pure-Nim MTProto client exists. Use **TDLib** (Telegram's official C++ library) through its tiny C JSON interface (`libtdjson`), about 4 functions to bind | ✅ but it is the one big **non-Nim** part: a C++ library you must build once. It also needs **one new login** (phone code), because a Telethon session can't be reused |
| OCR | pytesseract (blocks) | Run the `tesseract` binary as an async subprocess | ✅ Simpler than now, and fixes #30 for free. Non-Nim: tesseract itself, as today |
| HTTP, JSON | aiohttp, json | `std/httpclient` (async), `std/json` | ✅ |
| HTML scraping | BeautifulSoup + lxml | `std/htmlparser` + `xmltree`, or `nimquery` for CSS selectors | ⚠️ Works, but less forgiving of broken HTML. Khana, Taeen and Workable are JSON APIs anyway |
| Duplicate detection | scikit-learn TF-IDF | About 40 lines of Nim | ✅ And drops a 100 MB dependency |
| State/stats | JSON in config | SQLite (`db_connector`) | ✅ |
| CLI dashboard (#10) | none | `illwill` (terminal UI) | ✅ |

**What you gain:**
- Your favourite language, which matters a lot for a passion project you'll maintain alone.
- One compiled binary.
- TDLib is more robust than Telethon on exactly your pain points: reconnection, missed updates, unread tracking and flood waits are handled for you.
- Typed verdicts and reasons instead of tuples of match objects.

**What it costs:**
- Building TDLib once. It's heavy: tens of minutes and several GB of RAM.
- A fresh Telegram login.
- The Android/Termux instructions get harder.
- Fewer examples online.
- The risk of a rewrite silently changing behaviour.

**How to make it safe:** the corpus from section 2 becomes a regression suite.

1. **Write the filter core first**, as a pure Nim library: `text → verdict + reasons`. Write it with the new evidence-based design (3.1–3.9), plus a CLI that runs it over the corpus and prints precision numbers. This part has zero Telegram code, and it is where all the real value is.
2. **Port the scrapers** as small per-site functions, with saved HTML/JSON fixtures as tests.
3. **Add the TDLib listener** (unread bootstrap, live updates, publish/review, state in SQLite, alerts).
4. **Run it next to the Python version** for a while (Python keeps publishing; Nim only logs its verdicts), then switch.

The Python version keeps running unchanged during all of this.

**Where non-Nim code would remain:**
- TDLib (C++, used as a shared library: no reasonable alternative).
- The `tesseract` binary (as today).
- A systemd unit file.
- Possibly a tiny shell script to build TDLib.

**What I would *not* do:** improve the Python filter first and then rewrite it in Nim. The filter needs a redesign either way (section 3), and doing that twice is wasted work.

---

## 9. Open issues: where each one lands

| Issue | Covered by |
|---|---|
| #34 LinkedIn post not scraped | 4.1 / 4.4 (scraper returns nothing; the multi-link rule). Needs a fixture test |
| #31 more job boards | 6 (scraper table makes adding one trivial) |
| #30 OCR blocks the loop | 5.1 |
| #29 multi-job posts | 3.8 |
| #25 apply-anyway nuance | 3.5 (decide on minimum years, not on the phrase) |
| #17 refactor | 6 / 8 |
| #16, #3 alerts | 5.4 |
| #10–#15 CLI stats | 5.2 (state store) + 5.6 (reasons), then the UI |
| #9 false positives | 2 + 3 (corpus regression suite) |
| #7 stage logging | 5.6 |
| #6 autostart | 5.5 |
| #5 retries | 5.3, 4.10 |
