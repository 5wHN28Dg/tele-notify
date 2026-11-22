# The Nim rewrite: a guide for future you

This document explains what was built, why it looks the way it does, and how to keep working on it, so you don't have to reverse-engineer anything. Read it top to bottom once; after that, use the headings as a reference.

- **Problems report** (what was wrong with the Python version and why): [`problems-report.md`](problems-report.md)
- **Where we are**: stages 1–3 are done: the filter, text gathering (OCR, job boards; [section 7](#7-stage-2-gathering-the-text-ocr-and-job-boards)) and the Telegram listener ([section 8](#8-stage-3-the-telegram-listener)). Stage 4 is done: after a week next to the Python bot ([section 9](#9-stage-4-side-by-side-with-the-python-bot)), the Nim bot took over on 2026-11-18 and the Python bot is retired in `legacy/`. Stage 5, the terminal screen, is done too ([section 10](#10-the-terminal-screen-tui-issue-10)). See [Roadmap](#roadmap).

---

## 1. The big picture

The old `main.py` did everything in one file: Telegram, OCR, scraping, filtering and state. The rewrite splits the job into layers and builds them bottom-up:

```
             ┌────────────────────────────────────────────┐
 stage 3     │ LISTENER: Telegram via TDLib, state in      │  ✅ done
             │ SQLite, review queue, alerts                │
             ├────────────────────────────────────────────┤
 stage 2     │ TEXT GATHERING: OCR (tesseract), job pages  │  ✅ done
             │ from Khana / Taeen / LinkedIn / Yadanoo ... │
             ├────────────────────────────────────────────┤
 stage 1     │ FILTER CORE: text -> verdict + reasons      │  ✅ done
             └────────────────────────────────────────────┘
```

The filter core is a **pure function**:

```nim
proc classify*(text: string, cfg: Config): Decision
```

It gets the full text of a post and returns one of `publish`, `review` or `reject`, plus a list of human-readable reasons. It never touches Telegram, the network or files. That is what makes it testable with plain strings, and lets you run it over hundreds of saved posts in a fraction of a second.

### The three verdicts

| Verdict | Meaning | What the listener will do (stage 3) |
|---|---|---|
| `publish` | Positive evidence it's an entry-level job in your field in Basra | Forward to your channel |
| `review` | Role and place fit, but the level is unclear | Keep it in tele-notify's own review queue; you send or discard it from the CLI (see Roadmap) |
| `reject` | Not for you, with a reason | Nothing; it's logged |

The old code had no "review" for unclear posts: it **published** them (stage 2 inference). On your Saved Messages that rule caused 80 of 96 false positives. If you ever want the old behaviour back, set `"publish_when_unsure": true` in config.json.

---

## 2. Files

```
telenotify.nimble          package definition (name, version, dependencies)
config.nims                build settings (-d:ssl for HTTPS)
src/
  telenotify.nim           the command-line tool: `check` and `eval`
  telenotify/
    gather.nim             stage 2: post text + OCR + one job page -> text to judge
    ocr.nim                stage 2: tesseract, without blocking
    fetch.nim              stage 2: downloading with time limits, never raising
    boards.nim             stage 2: which job links we follow and how to read them
    html.nim               stage 2: HTML -> text with line breaks
    tdjson.nim             stage 3: TDLib's C functions, loaded at run time
    td.nim                 stage 3: async requests/answers/updates with TDLib
    telegram.nim           stage 3: what the bot does on Telegram (+ parseMessage)
    session.nim            stage 3: starting TDLib, login
    listener.nim           stage 3: what happens to each message
    state.nim              stage 3: SQLite state
    dedup.nim              stage 3: duplicate detection
    commands.nim           stage 3: login, run, review, stats
    normalize.nim          text folding (Arabic variants, digits, invisible chars)
    keywords.nim           keyword lists and how they are searched
    defaults.nim           built-in word lists (seniority, job signals, other cities…)
    config.nim             reading config.json into a Config object
    experience.nim         "how many years of experience are required?"
    blocks.nim             splitting multi-job posts into one block per job
    classify.nim           the decision itself
tests/
  keywords.json            your keyword lists (no credentials) used by the tests
  tnormalize.nim           tests for normalize + keywords
  texperience.nim          tests for experience extraction
  tclassify.nim            one test per problem in the report (and per review finding)
  tboards.nim              job links and page readers, on real saved pages
  tgather.nim              gather() with fake downloads and fake OCR
  tocr.nim                 real tesseract on two small rendered images
  tfetch.nim               fetch() against a local test web server
  ttd.nim                  TdClient against a fake libtdjson
  ttelegram.nim            parseMessage on TDLib JSON
  tlistener.nim            the whole bot against a fake Telegram
  tstate.nim, tdedup.nim   SQLite state, duplicates
  fixtures/                saved job pages (scripts/styles removed) and OCR images
  config.nims              lets the tests import from src/
deploy/build-tdlib.sh      builds lib/libtdjson.so (shell: drives cmake)
deploy/tele-notify.service systemd user unit
.github/workflows/nim.yml  CI: style check, build, tests, CLI smoke test
```

The Python files (`main.py`, `main_simple.py`, `tools/`) were left untouched and kept running in production until the switch in stage 4; they're now in `legacy/`.

---

## 3. How a post flows through `classify`

Take this post (invented, but typical):

```
شركة برمجية تعلن عن توفر فرص عمل
1- مصور
2- إعلامي ذو خبرة لا تقل عن 3 سنين
3- مبرمج تطبيقات حديث التخرج
عنوان الشركة البصرة
```

### Step 0: normalise (`normalize.nim`)

The text becomes lower-case, with letter variants folded (أ/إ/آ→ا, ة→ه, ى→ي, گ→ك), Arabic-Indic digits turned into 0–9, diacritics and invisible characters removed.

Styled copies of normal characters are turned back into the normal ones: 𝗕𝗼𝗹𝗱 and 𝘪𝘵𝘢𝘭𝘪𝘤 letters (popular on Telegram), Arabic presentation forms (ﻣﻬﻨﺪﺱ, text copied from PDFs) and full-width letters. The hamza after an alef is dropped, so "كربلاء" and "كربلا", or "كهرباء" and "كهربا", look the same. Keywords from your config go through the **same** function, so the two sides always agree. That's why your config can say "البصرة" while the code internally compares "البصره".

There is also `flatten`, used only for keyword search. It turns `-`, `_`, `/`, `#`, `:` and similar into spaces, so `#مهندس_كهرباء` and `iqjscout.com/jobs/electrical-engineer-34` contain normal words.

### Step 1: is it a job advert at all?

- If it matches a phrase from `notAJob` in `defaults.nim` (school fees, course ads…): **reject**.
- If it sounds like someone *looking for* a job ("ابحث عن عمل", "محتاج وظيفه", "looking for a job"), **and** has no employer phrase (hiring, تعلن، حاجتها الى، مطلوب…): **reject**. The employer check keeps adverts that open with "Looking for a job? We are hiring…".
- If it has no job-advert wording at all (`jobSignals`: مطلوب، تعلن، hiring, vacancy, CV…): **reject**.

Our example has "تعلن" and "فرص عمل", so it continues.

### Step 2: split into jobs (`blocks.nim`)

Lines that start a list item ("1-", "2.", "3)", "1️⃣") or are separators ("━━━━") cut the post into pieces. The first piece is the *header*.

- If no list item names one of your job titles, it's a single-job post (the list is its requirements), so the whole post stays one block.
- Otherwise each list item that names one of your job titles becomes a job. A numbered item without one of your titles is one of two things:
  - *another job*, if it names some job ("2- محاسب": see `jobNouns` in `defaults.nim`), and is dropped;
  - a *requirement* ("2- خبرة لا تقل عن 4 سنوات"), and is glued onto the job above it.
- An un-numbered piece (cut off by a "━━━" separator) is glued onto the job above it.
- **Compact lists.** If every list item is a single line ("1- مهندس كهرباء / 2- مهندس ميكانيك / 3- …"), whatever follows the last item is a *shared footer* ("المتطلبات: خبرة 5 سنوات", the address). It joins the header and applies to every job.
- If a job has no location of its own or in the header, the whole post is searched.

In the example every item is one line, so it's a compact list. Piece 3 ("مبرمج تطبيقات") is our job, and the footer "عنوان الشركة البصرة" is shared. Piece 2, with its "3 years", belongs to another job and is dropped. That fixes the multi-job mix-up from issue #29.

Each job block is then judged with the header as context (`judgeBlock`). The post gets the best verdict of its blocks.

### Step 3: judge one block (`judgeBlock` in `classify.nim`)

1. **Role.** Search the block for three kinds of role keyword:
   - `entry`: roles that are entry-level by nature (help desk, فني دعم);
   - `title`: real job titles (electrical engineer, مهندس كهرباء);
   - `field`: degrees and fields (electrical engineering, علوم حاسبات).

   A role is ignored if a seniority word (senior, lead, مشرف، اول…) is in the few words *before* it, or *right after* it ("مهندس كهرباء اول", "IT Support Lead"). It is also ignored if it is part of an exclusion like "cost control engineer". Seniority further away on the line is usually about someone else ("reports to the site manager") or an alternative ("fresh graduates or experienced"), so it doesn't count. No role left: **reject**.
2. **Location** (`locate`).
   - A line that says where the job is or where you must live ("📍", "Location:", "موقع العمل", "من سكنه…") decides first.
   - Otherwise: Basra mentioned and no other city means Basra; Basra and another city with no location line means *mixed* (**review**).
   - "شارع البصرة" (a street) doesn't count as Basra; neither does a place keyword followed by a word that makes it something else ("remote terminal units", "المدينة المنورة", "المدينة الصناعية": see `notAPlaceAfter`).
3. **Experience** (`experience.nim`). The *number* of years required is extracted (see section 4). More than `max_experience_years` (default 1): **reject**.
4. **Certification** (job block and header). One of your `certifications` on a line with "required/must/مطلوب/يجب" (and not "a plus/preferred/يفضل"), or listed under a heading like "Required certifications:", is mandatory: **reject**. If the post also says "apply even if you don't meet every requirement", it's **review** instead (issue #25: that phrase can excuse a cert, never years).
5. **Positive evidence**: one of your level keywords (not in a "mentor junior engineers" sentence), "0–1 years"/"no experience", or an `entry` role.
   - Found, and the title lines don't look senior: **publish**.
   - Found, but the role on a title line (first two lines, or a "Position:"/"المسمى الوظيفي" line) is marked senior: **review**.
   - Nothing either way: **review**. With `publish_when_unsure` it's **publish**, unless the role is only a field or *any* seniority word appears on a title line.

Every step appends to `reasons`, so you can always see *why*:

```
$ ./telenotify check --config:config.json post.txt
verdict: publish
  - job 1 of 1
  - role (rkTitle): مبرمج تطبيقات
  - location line: 'عنوان الشركه البصره'
  - entry level: حديث التخرج
```

---

## 4. Experience extraction in detail (`experience.nim`)

The old code had one giant regex returning yes/no. The new one returns numbers:

```nim
type Experience = object
  requiredYears: int    # -1 = none found
  preferredYears: int
  entryFriendly: bool   # "0-1 years", "لا تتجاوز سنة", "no experience"
  evidence: seq[string]
```

For each line that mentions experience (experience/exp/خبره/خبرات/ممارسه, "N years/yrs in X", "سنوات في", or "لا تقل عن", which in job ads is almost always about experience; an age uses "لا يقل"):

1. **Find quantities.**
   - Digits with a unit ("3 years", "3+ yrs", "3-5 years", "من 5 الي 12 سنه").
   - English number words ("two years").
   - Arabic number words ("خمس سنوات").
   - Duals ("سنتين").
   - The number after the words ("Years of Experience: 5+", "عدد سنوات الخبرة: 6").
   - Months ("36 months" → 3 years, rounded up).
   - "خمس (5) سنوات" is fine too.
   - A lone "سنه" after خبره/عن/من/تتجاوز.
2. **Keep only quantities that are about experience.** They must overlap the experience phrase or be within 160 bytes of it: about 25 English words, but only about 12 Arabic ones, since Arabic letters take 2 bytes each. They must not be an age, contract length, training-program length or graduation date ("Program Duration: 3.5 Years", "برنامج تدريبي لمدة عامين", "graduated within the last 2 years"; English words are matched as whole words, so "programming" doesn't count). A list number is not a range: in "1- 5+ years" the answer is 5.
3. **Ranges use the lower end.** "1-3 years" and "سنة – خمس سنوات" both mean 1.
4. **Decimals round up.** "1.5 years" counts as 2.
5. **Upper bounds** ("less than", "لا تتجاوز") mark the post entry-friendly instead. "*Not* less than" / "ليس اقل من" is a minimum, though, and the end of a range ("من 3 حتى 5") is never an upper bound.
6. **Optional or required.** "preferred / يفضل / a plus" on the line (or on the next short line when the sentence wraps) makes it optional. If "required" is also on the line, required wins.

Things the regexes must remember: the text is **already normalised**, so they say `الي` not `الى`, `حتي` not `حتى`, `خبره` not `خبرة`. Forgetting this was a real bug during development.

---

## 5. Changing the filter without breaking it

### Adding words

- **Your own keywords** stay in config.json, under the same keys as before.
- **Built-in language** (seniority words, other cities, course-ad phrases…) lives in `defaults.nim`. You can *add* to most of these lists without touching code, using these config keys:

| Key | Adds to |
|---|---|
| `extra_seniority` | words that make a role senior |
| `extra_job_signals` | words that show a post is a job ad |
| `extra_not_a_job` | phrases that make a post "not a job" |
| `extra_other_places` | places that are *not* Basra |
| `extra_location_markers` | words that mark a "where is the job" line |
| `extra_role_exclusions` | phrases like "cost control engineer" |
| `field_keywords_en/_ar` | force a role keyword to count as a *field*, not a title |
| `max_experience_years` | default 1 |
| `publish_when_unsure` | default false |

**Title vs field.** Your `mid_level_general_role_*` keywords are sorted automatically. Those that start with "degree"/"bachelor"/"هندسه"/"علوم"/"تخصص", or end with "engineering"/"science", are *fields*; everything else is a *title* (`isFieldPhrase` in `config.nim`).

**Keyword matching rules** (`keywords.nim`):
- English keywords must be whole words ("intern" ≠ "internal").
- Certifications must also be whole words ("CAP" doesn't match "capable"), except that OCR-glued "CCNAor"/"CCNAand" is accepted.
- Arabic keywords may have a prefix in front (و ف ب ل ك ال، وال، بال، لل) and anything after. So "مبرمج" matches "مبرمجين", but "مهندس كهرباء" does **not** match "مهندس كهربائي": add both forms if you want both.

### The safety net: tests and the corpus

1. **`nimble test`** runs the public tests. CI runs them on every push and PR. Each fixed problem from the report has a test named after it. When you fix a new false positive, first add an invented post that reproduces it to `tests/tclassify.nim` and watch it fail.
2. **`telenotify eval`** runs the filter over your *private* labelled corpus:

   ```
   ./telenotify eval --config:/home/finn/Projects/bqtechjobs/config.json \
       ~/.local/share/tele-notify-corpus/eval.jsonl
   ```

   Options:
   - `--show:fp`: false positives (published but labelled FALSE)
   - `--show:fn`: true matches that weren't published
   - `--show:wrong`: published-false plus rejected-true
   - `--show:all`

   **Never commit the corpus**: it is your Saved Messages.

### About the corpus

`~/.local/share/tele-notify-corpus/` (only readable by you) contains:

| File | What |
|---|---|
| `saved.json` | 333 Saved Messages, 2025-06-05 → 2026-11-17 |
| `resolved.json` | originals of "match: … / post link: …" stubs (59 found, 46 deleted by their channels) |
| `media/` | photos, used for OCR |
| `results_testing.json` | what the old Python code decided for each post, including the OCR and scraped text (`full`) |
| `labels_[abcd].json` | TRUE/FALSE/UNSURE label + reason for each of 196 distinct posts |
| `eval.jsonl` | what `eval` reads: one `{"id","text","label","reason","note","old"}` per line |
| `labels-review.xlsx` | **the readable version of the labels**: one row per post, label column to edit, filters, a summary sheet |
| `channels_recent.json(l)` | the latest 100 posts of every watched channel (3,834 posts, read 2026-11-17), unlabelled. Used to find today's job-board links and to sanity-check the filter |
| `scripts/` | the Python scripts used to export, replay and build the spreadsheet. Temporary: stage 3 replaces them with `telenotify` commands, and the review queue will produce labels as you work |

**The labels were made by AI reviewers, not by you.** The rubric was: real job, Basra or remote, IT/electrical/control role, reachable for a fresh graduate. I spot-checked a sample and agreed, but you know the market better. Some labels are debatable: e.g. summer training for *current students* is labelled FALSE because the rubric said "fresh graduate", but your channel's audience may want it. To fix labels, open `labels-review.xlsx`, change the yellow *Label* cells, save, and copy them back into `eval.jsonl`:

```bash
python3 -m venv /tmp/xl && /tmp/xl/bin/pip install openpyxl
/tmp/xl/bin/python ~/.local/share/tele-notify-corpus/scripts/labels_from_xlsx.py ~/.local/share/tele-notify-corpus
```

Then re-run `eval`. (This is the one Python step left in the workflow; it's private tooling, not part of tele-notify.)

### Current numbers

Same 196 labelled posts, your deployed keywords:

| | Old Python | Nim, default | Nim, `publish_when_unsure` |
|---|---|---|---|
| Published, labelled FALSE | **96** | **4** | 6 |
| Published, labelled TRUE | 9 | 5 | 9 |
| TRUE sent to review | 5 | 9 | 5 |
| TRUE lost (rejected) | 0 | 0 | 0 |
| Total sent to review | 57 | 56 | 47 |

The 4 wrong publishes in the default mode:
- 2 summer trainings for *current* students. Arguably fine for your channel; it's a label question.
- A teenager's CV that offers volunteering.
- An "HSE Lead" post whose only role match is "degree in Engineering".

The rules were tuned while looking at this same corpus, so expect real-life numbers to be somewhat worse. The way to keep them honest is to keep adding newly saved posts to the corpus.

### A sanity check on fresh posts

The filter was also run over the 2,872 posts the watched channels published from 2026-10-01 to 2026-11-17 (text only), and its 11 publications were read one by one:
- 7 good: IT Specialist (Basra, cross-posted twice), Baker Hughes graduate programme, KBR fresh-graduates list, two EPCM oil-field graduate calls, and "engineers of all specialties, fresh graduates".
- 3 borderline: a Data Entry Officer with a CS degree and 0–1 years (cross-posted twice), and a hashtag-only teaser.
- 1 wrong: a *female* training-centre manager. Seniority words were masculine only; now fixed and tested, so 10 remain.

---

## 6. Nim things used here, in case you're rusty

- **`iterator hits*(...)`**: a function that `yield`s many results lazily; you loop over it with `for h in s.hits(list)`.
- **`let pat = re2(r"...")`** at module level: compiled once at program start. `re2` is from [nim-regex](https://github.com/nitely/nim-regex) (pure Nim). It supports the variable-length look-behind the old Python code needed. Use `findAll`, `contains`, `find(s, re, m)`. Groups are **0-based for capture groups** (`m.group(0)` is the *first* `( )`), and `m.boundaries` is the whole match.
- **Byte offsets vs characters**: Nim strings are UTF-8 bytes, and Arabic letters are 2 bytes. Never slice `s[a ..< b]` with computed offsets; use `runeSafe(s, a, b)` from `normalize.nim`, which never cuts a letter in half.
- **`j{"key"}.getInt(1)`**: the `{}` accessor returns nil instead of raising when the key is missing, and `getInt(default)` handles nil.
- **`enum` with strings**: `vPublish = "publish"`, so `$verdict` prints `publish`. Enums are ordered, so `vPublish > vReview > vReject` is how "best verdict" is computed.
- **`nimble test`** compiles and runs every `tests/t*.nim`; `tests/config.nims` adds `src/` to the import path.
- **`{.async.}` and `await`** (stage 2, `std/asyncdispatch`): an async proc returns a `Future`; `await` pauses *that* proc until the result is ready, and other async work runs meanwhile. In a test or the CLI, `waitFor` runs the event loop until one future finishes. Async procs **can't take `var` parameters**, which is why `gather.nim`'s `follow` returns its notes in an object instead.
- **Recursion and deep trees**: the HTML parser nests unclosed tags inside each other, so a real page can be thousands of levels deep. The walks in `html.nim` use an explicit stack (`while stack.len > 0: let x = stack.pop ...`) instead of calling themselves.
- **Overloading pitfall**: a proc named `add` that calls `list.add` inside will call *itself* if the types match. `boards.nim` uses `addUnique` for that reason (this exact bug happened).

---

## 7. Stage 2: gathering the text (OCR and job boards)

Before the filter runs, `gather` (`gather.nim`) builds the text it will read:

```
post text
  + text of each attached picture (OCR)            ocr.nim
  + button URLs
  + the job page, if exactly one supported job link is found
    in the text, the buttons or the pictures       boards.nim + fetch.nim
```

It returns that text plus `notes` saying what happened ("read Khana (2952 characters)", "OCR failed: …", "2 job links: none followed"). Nothing in it can crash: every failure becomes a note. `telenotify check` prints the notes, and `--show-text` prints the whole text.

### Why it's built this way

- **OCR doesn't freeze the bot (#30).** `ocr.nim` starts the `tesseract` program and *awaits* it, checking every 50 ms, so other work continues meanwhile.
  - tesseract writes its result to a file and its messages to `/dev/null`, so nothing piles up in a pipe that nobody reads.
  - Every run gets a fresh private temp folder, so two OCRs of the same picture at once can't collide.
  - On timeout, the process is killed *and waited for*, so no zombie process is left behind. The test `issue #30: the bot keeps working while OCR runs` proves a ticker keeps ticking during OCR.
- **A single job link is always followed (#34).** The old code only scraped when almost nothing else was in the post, and OCR text from a picture counted as "something else". That's why the LinkedIn post in #34 was never read. Now any post with exactly one supported link gets its job page read; the page usually has the experience requirement the post leaves out.
  - Epilogue for #34: the post was someone writing "#فرصة_عمل" and *resharing* an ad for an accountant in Baghdad. Now that it's read, it's rejected, which is the right answer. The old ❌ was right by luck.
- **Several links: none followed.** A post with several job links is almost always several jobs, and reading all their pages would mix them together.
- **Downloads use our own small HTTP client** (`fetch.nim`), not Nim's `std/httpclient`. Review showed the standard client hides the socket. It resolves names itself, so `127.1`, `0x7f000001` or a name pointing at 127.0.0.1 slipped past the "no local addresses" check. It can't cancel a connection that's still opening. And it read a redirect's whole body (200 MB once). Ours resolves the name, refuses if *any* address is local or private (including IPv4-in-IPv6 forms), connects to the checked address, and verifies the HTTPS certificate belongs to the host (Nim's async TLS doesn't). It sends one `GET` with `Connection: close`, and never reads a redirect's body. Its rules:
  - 20-second time limit, after which our socket is closed, cancelling a connect or read in progress;
  - pages capped at 3 MB, checked against the announced size and while reading;
  - only `http`/`https`, and never local or private addresses (127.0.0.1, 10.x, 192.168.x…), so a short link in a post can't make the bot poke at your home network;
  - redirects followed by hand, so the final URL is known (needed for `lnkd.in`);
  - a polite User-Agent naming the project;
  - errors returned, never raised.

### Supported boards (`boards.nim`)

These were chosen by counting links in the 3,834 latest posts of your channels on 2026-11-17:

| Board | Example link | Read from |
|---|---|---|
| Khana | `khana.jobs/jobs/9915` | its JSON API: title, location, description |
| Taeen | `taeen.iq/ar/jobs/<uuid>` | its JSON API: title, city, experience years, description |
| LinkedIn job | `linkedin.com/jobs/view/<id>` | page title (it includes the location) + the `description__text` block |
| LinkedIn post | `linkedin.com/posts/<slug>` | the post's own text plus, for a reshare, the reshared post. **Never comments**: anyone can write a comment, and a long one used to replace the post |
| `lnkd.in` | `lnkd.in/<code>` | resolved first, then read if the target is supported |
| JSON-LD job pages | `yadanoo.com/job/<id>`, `shift-iraq.com/jobs/<id>`, `mselect.com/job/<slug>-<id>`, + `extra_job_sites` in config | **only** the page's schema.org `JobPosting` block. Place names lose their Latin accents and a leading "Muhafazat" (mselect writes "Muḩāfaz̧at Maysān"; the filter knows "Maysan"). mselect paths are lowercased so case variants are one link |
| Workable job board | `jobs.workable.com/view/<id>/<slug>` | the page's `JobPosting` block, plus its separate `requirementsSection` only when the description doesn't already contain it. Its `jobLocationType` is ignored: Workable says `TELECOMMUTE` even for hybrid jobs, and a "remote" in the Location line would let a Baghdad job through |
| Workable company page | `apply.workable.com/<company>/j/<shortcode>`, old-style `<company>.workable.com/j/<shortcode>` | Workable's JSON API: title, every place in `location` and `locations` (+ "remote", but only when the job's country is Iraq or not given: a remote job in Texas is not for us), description, requirements, benefits. Links without the company (`apply.workable.com/j/<code>`) are skipped: the API needs it. When a post has one `jobs.workable.com` link and one `apply.workable.com` link, only the API one is kept (almost always the same job, and a post's link is read only when it has just one) |

Workable patterns need a link boundary before the host (start of text, a space or other non-URL character, or `//`), so `notjobs.workable.com/...` or `evil.com/?x=acme.workable.com/j/...` are not links.

The scraped text starts with `Job title: …` and `Location: …` lines, which the filter's title and location rules understand.

**Gone:**
- **Iraq Jobs Scout**: `iqjscout.com` now redirects to Yadanoo's home page, so old IJS links are dead; new posts link to Yadanoo directly.

**Workable and mselect** came back on 2026-11-18 (issue #31) with pages saved that day. Adding a board means a `BoardKind` (or a site in the JSON-LD list), a pattern in `findJobLinks`, a branch in `extract` and a saved page in `tests/fixtures/`. `mselect.com` without `www.` has no DNS record, so mselect links are always downloaded from `www.mselect.com`.

**Why "JSON-LD only" for Yadanoo-style sites.** A Yadanoo job page also contains a city picker listing *every* city, Basra included. Reading the whole page would make every job look like a Basra job. A test checks that a Duhok job's text never contains "Basrah".

**Job links must look like a job.** On the JSON-LD sites the path needs at least two parts, the last with a digit (`/job/55ab`, not `/jobs` or `/careers`). E-mail addresses (`hr@shift-iraq.com/...`) don't count, and a link ends at the first non-ASCII character, so `yadanoo.com/job/55،للتقديم` is read as `/job/55`.

**Pictures are checked before OCR.** Only PNG, JPEG, WebP and GIF, up to 12,000 pixels a side, read from the file header without decoding. Given text instead of a picture, tesseract reads it as a list of files to open; and a tiny PNG claiming 30,000×30,000 pixels made it use 1.8 GB.

**Hostile pages.** Pages with more than 15,000 tags are refused before parsing. The HTML parser is recursive, and a page of 50,000 nested tags crashed the whole process; real job pages have about 3,000 tags.

**When a site changes its layout**, the reader returns empty text and `gather` notes "page had no job text (expired, login wall or new layout)". To fix it:
1. save the new page into `tests/fixtures/`;
2. adjust the reader in `boards.nim`;
3. run `nimble test`.

LinkedIn is the most likely to change: it shows logged-out visitors a simplified page, and sometimes a login wall.

### Things found while building stage 2

- Khana's old links in your corpus (jobs 425, 426, 7641) now return 404: job pages expire. That's why the tests use saved fixtures instead of live sites.
- `lnkd.in` sometimes redirects straight to the job, and sometimes shows a "you're leaving LinkedIn" page; both are handled. In the corpus, one led to Al-Majal Energy's graduate programme on **fountain.com**, which isn't supported yet. It's a candidate for the JSON-LD list if its pages carry a `JobPosting` block.

---

## Roadmap

| Stage | What | Status |
|---|---|---|
| 1 | Filter core + tests + eval against the corpus | ✅ done |
| 2 | Text gathering: async OCR (fixes #30), job-board readers with saved pages as tests (fixes #34, report 4.1–4.5) | ✅ done |
| 3 | Listener on TDLib: unread bootstrap with the real read marker, one processing queue, state and stats in SQLite, downtime alerts (#3, #16), systemd unit (#6), review queue with `telenotify review` (send / discard) | ✅ done |
| 4 | Albums and edits; `telenotify compare`; run `telenotify run --dry-run` next to the Python bot for about a week and compare; then switch: stop the Python bot, start the Nim service, move the Python files to `legacy/` | ✅ done (switched 2026-11-18) |
| 5 | The nice CLI dashboard (#10–#15): live counts, review queue with keys, highlighted events, on top of the SQLite state that now exists. Review decisions become corpus labels | ✅ done (the terminal screen, `tui/`) |


---

## 8. Stage 3: the Telegram listener

### The pieces

```
telenotify run
  session.nim    start TDLib, check the saved login
  td.nim         talk to TDLib: requests ↔ answers, updates
  telegram.nim   the few things the bot does on Telegram (an interface)
  listener.nim   what happens to each message
  state.nim      what the bot remembers (SQLite)
  dedup.nim      "did I already publish this?"
```

**TDLib** is Telegram's own client library, written in C++. It handles the hard parts the old bot struggled with: reconnecting, missed updates, read markers and flood limits. Its whole interface is four C functions that exchange JSON (`tdjson.nim`). The library is loaded when `run` starts, not at build time, so building and testing never need it.

**`td.nim`** turns that into async Nim. Every request gets an `@extra` number; TDLib copies it into its answer, so `call` can return a Future that completes with the right answer. Anything without our `@extra` is an *update* (a new message, a connection change) and goes to `onUpdate`. `pump` polls TDLib every 20 ms without blocking.

**`telegram.nim`** is the seam that makes the bot testable. The listener only uses a `Telegram` object with a few operations: `unread`, `markRead`, `downloadPhoto`, `forward`, `link`, `sendText`, `selfChat`. `tdTelegram` implements them with TDLib, and `tests/tlistener.nim` implements them with tables in memory. The whole bot is therefore tested without an account. `parseMessage` reads the text *and the links hidden behind words*, which the old bot never saw.

### What happens to a message (`listener.nim`)

1. Already in `processed`? Skip it. This covers restarts and the startup backlog overlapping live updates.
2. `gather` reads the text, the photo (OCR) and the job page, then `classify` decides.
3. Then:
   - **publish**: if it repeats something published in the last 14 days (same job link, or ≥ 80% the same words), skip it as a duplicate. A bare domain like `https://company.com` doesn't count as a job link: many different jobs from one company end with it. Otherwise forward it to `target_chat`. Where the channel forbids forwarding, what goes out depends on `forward_fallback` (#80, see "When forwarding is blocked" below): "match: role / post link" (the default, "link"), using Telegram's real link, which only opens for a private chat's members; or the bot's own short summary ("summary").
   - **review**: put it in the review queue in `state.sqlite3`, not in Saved Messages.
   - **reject**: nothing.
4. Record the verdict, reasons and action in `processed`, then mark the message read.

There is one queue, and messages are handled **one at a time**. First the startup backlog (every watched chat's unread messages, oldest first, found with the chat's real read marker; at most 10,000 per chat, and you get an alert if a chat had more), then live messages. Live messages that arrive before the backlog is done (including while the chats are still being opened at start-up) wait for it. Otherwise a new message could be marked read first, and since Telegram's read marker is a single position per chat, the backlog behind it would count as read: if the bot then died, those messages would never be handled. One queue alone removes the old duplicate race and the "deleted posts make us skip unread messages" bug.

A failure (a download, OCR, a Telegram error such as a flood limit) affects only its own message, and the message isn't lost: it goes to the review queue with the error, so you decide. The same goes for a picture that couldn't be read: if the post would otherwise be rejected, it goes to review instead, because the picture is often the whole ad. A TDLib request that gets no answer for 5 minutes fails instead of stalling the bot. If Telegram refuses a forward after TDLib accepted it (you lost the right to post, slow mode...), you get an alert.

Chats in your Archive are loaded too; job channels are often archived.

A stop request (Ctrl-C, `systemctl stop`) is honoured after the current message, not after the whole backlog. Whatever is left stays unread and is handled at the next start.

If the Telegram session ends while the bot runs (you logged it out from your phone, for example), the bot stops with an error (a normal stop doesn't count), so systemd restarts it and the log says why. Before, it would have hung while looking alive. Any request still waiting for TDLib then fails instead of waiting forever (`td.nim`).

Photos are deleted from TDLib's file folder once read, so a bot that runs for months doesn't fill the disk.

### Commands

| Command | What |
|---|---|
| `telenotify login` | Once: phone number, the code Telegram sends, your 2FA password. Prints your chats with their IDs (it replaces `get_chat_ids.py`). The login is saved in `data_dir/tdlib` |
| `telenotify run [--dry-run]` | The bot. `--dry-run` (or `"dry_run": true`) judges and records everything but forwards nothing and marks nothing read |
| `telenotify review [--dry-run]` | Goes through the review queue: shows the post, its link and why it's unclear; `s` approves it, `d` discards it, `n` skips it, `q` quits |
| `telenotify stats [--days:N] [--dry-run]` | Per chat: how many messages got each verdict, how many were forwarded, and how many reviews are waiting. Then the forwarding time (#74): how long forwarded posts took from when they went up to when they reached your channel, as "typically within X (half are faster), 95 of 100 within Y" (the median and the 95th percentile). It's split three ways: **live** posts (live updates forwarded as soon as they were judged: what the profile's alert-latency target ALERT1, 95% under 60 s while connected, is about), posts **caught up after downtime** (read from the start-up backlog or from a chat read again after its read failed, live messages held while that backlog is read, and messages that arrive while the connection is down, connecting or catching up, which is when TDLib replays what was missed), and posts sent **after your review** (your wait included). An edited post judged again and forwarded is timed from its edit (TDLib's `edit_date`), since that's when the ad appeared; an edit without that time is left out. Posts judged before the date was kept are left out too. In a dry run it says "would have reached your channel". `--days` is a whole number from 1 to 3650 |

**Why `review` doesn't send anything itself.** TDLib locks its database, so only one program can use your login at a time. If `review` opened Telegram, you'd have to stop the bot first. So `review` only writes your decision (status `to_send`) to SQLite, and the running bot sends approved posts within about five seconds (`sendApproved`). If one can't be sent, it goes back to the queue with the reason. A post can only be approved while it's pending, so two `review` windows can't send it twice. Text is shown without control characters, so a post or a scraped job page can't clear or fake your terminal screen. `login` still needs the bot stopped, but you only run it once.

**Dry runs have their own state** (`state-dryrun.sqlite3`). Otherwise every message the dry run judged would be skipped as "already seen" once you switch to the real bot. Add `--dry-run` to `review` and `stats` to look at it.

### State (`state.sqlite3` in `data_dir`)

| Table | Holds |
|---|---|
| `processed` | every judged message with verdict, role, reasons and action, `posted_at`: the post's own Telegram date in Unix seconds, or its edit's for an edited post judged again (#74), empty for posts judged before it was kept; and `caught_up`: 1 when the post was read from a backlog (start-up, or a retried chat), 0 for a live update |
| `published` | what was published, for duplicate checks |
| `reviews` | the queue and your decisions (`pending` → `to_send` → `sent`, or `discarded`, or `kept`: a match the terminal screen marked "don't send", because it already reached the channel; the bot never sends it or counts it as forwarded). Those decisions are labels for the corpus: stage 5 adds an export |
| `meta` | the heartbeat, and which watched chats you lost access to (`chat_access:<chat id>`) |

`config.json` is now only *read*. The old bot rewrote it on every forward, which could throw away your edits.

**Schema versions and the copy kept before an upgrade (#74).** The file's schema version is SQLite's `user_version` (`PRAGMA user_version`); files from before versions existed read as 0, and `schemaVersion` in `state.nim` is the current one (1: `processed.posted_at` and `processed.caught_up`). When the bot, or any command, opens a file older than that, it takes an exclusive lock on `state.sqlite3.lock` beside it (so the bot and, say, `stats` can't upgrade at the same moment; a second one waits up to 20 seconds, then stops with "try again in a minute"; the copy of a state file of a few MB takes well under a second), looks at the version again (the other one may have just upgraded it), and removes half-made copies (`state.sqlite3.bak-*.tmp-*`) a process that died while copying left behind. Then, for an existing file, it makes a fresh copy at `state.sqlite3.bak-<old version>` (so `state.sqlite3.bak-0` for the first upgrade), private to you like the file itself, and only then adds the columns and sets the version, in one transaction (`BEGIN IMMEDIATE` … `COMMIT`), so the file is either fully upgraded or not at all, before letting go of the lock. A copy already at that name is replaced, not trusted: it may be from an earlier upgrade followed by a rollback to an older bot that kept writing, or broken by a power cut. The copy is made with SQLite's `VACUUM INTO`, not a file copy: while the bot runs, recent writes are still in the write-ahead log (`state.sqlite3-wal`), which a plain copy of `state.sqlite3` would miss. It's written under a temporary name, flushed to the disk (`fsync`; `VACUUM INTO` doesn't do that itself), renamed, and the folder flushed too, so a half-written copy never takes the backup's name. If the copy fails (a full disk, say), the file is left unchanged and the command stops, saying to free some disk space and check the folder's permissions; the next start tries again. If the copy was made but flushing the folder fails, it says the copy exists but may not be on the disk yet, and the file was not upgraded. Any failed upgrade is one line (`error: …` in the journal), and the bot then stops; systemd starts it again, so a passing problem fixes itself. The tests reach into the upgrade's steps through `upgradeTestHook`. A new, empty file needs no copy. To undo an upgrade, stop the bot and put the copy back in place of `state.sqlite3` (removing `state.sqlite3-wal` and `state.sqlite3-shm`). Adding a column later: add it to the list in `createTables`, raise `schemaVersion`, and the copy comes with it.

Open it with `sqlite3 ~/.local/share/tele-notify/state.sqlite3`. For example, `SELECT chat_title, verdict, action, reasons FROM processed ORDER BY at DESC LIMIT 20;`

### When forwarding is blocked (#80, `forward_fallback`)

Some chats don't allow forwarding. The old answer was a text of two lines, `match: <role>` and `post link: <link>`, and for a private chat that link only opens for its members, so the channel's readers got nothing they could use. The owner's decisions on #135: "Post its own summary" (2026-11-21); "Arabic" labels and "Public only; private to review (Recommended)" (2026-11-22). So a blocked post from a **public channel** gets the bot's own summary, and one from anywhere else (a private or members-only chat, or a group) goes to the review queue, with nothing posted: you decide by hand. It is a tier-2 change (a message in a live public channel), so it sits behind a switch in config.json until the owner has checked it:

- `"forward_fallback": "link"` (the default, also when the key is missing or empty): the old two lines, for every chat. The state records `linked`.
- `"forward_fallback": "summary"`: the summary below. The state records `summarized`, which counts as forwarded everywhere (`telenotify stats`, `compare`, the screen's Dashboard; History and Search show "summarized").
- Anything else stops the bot at start-up with a config error naming the key.

The summary (`summary.nim`), 5 or 6 lines and at most 400 characters (made-up example):

```
ملخص آلي لإعلان لا يمكن إعادة توجيهه
الوظيفة: electrical engineer
المكان: البصرة
التقديم: عبر LinkedIn، بالبريد الإلكتروني
المصدر: Example Jobs
رابط المنشور: https://t.me/examplejobs/123
```

- **First line**: says it's an automatic summary of an advert that can't be forwarded.
- **الوظيفة** (the job): the role keyword the filter matched, as written in the config; "غير محددة" (not named) when there is none.
- **المكان** (the place): the place keyword that counted for the filter, as written in the config (`Decision.place`, new); left out when none did, and when the filter found the place mixed (Basra and somewhere else), since no single place is right then.
- **التقديم** (how to apply): named and never quoted: "عبر Khana/Taeen/LinkedIn/Workable" or "عبر موقع توظيف" (a job site; the job links `boards.nim` knows), "عبر استمارة" (a form), "بالبريد الإلكتروني" (e-mail), "عبر واتساب" (WhatsApp), "بالهاتف" (phone: an Iraqi mobile number), at most three. Addresses and numbers are never copied, since Telegram would turn them into links. With none found: "التفاصيل في المنشور" (details in the post).
- **المصدر** (the source): the public channel's title ("قناة عامة", a public channel, if it has none that shows), then `رابط المنشور: <link>`, the post's link, asked from TDLib again at sending time (a link stored with a review may be from before the chat went public, or an old username).

Only a **public channel** gets a summary: TDLib says it's a channel (`is_channel`, not a group) with a username, and its link isn't a `t.me/c/` one. The owner's T0 answer was "public channels only", so a public group is held back too. Nothing is sent, and the post goes to the review queue, when:
- it isn't from a public channel: "not posted: it isn't from a public channel (it's a private or members-only chat, or a group), and only public channels' posts are republished in your public channel (#80). Review's Send can't post it; if its admins agree, post it yourself in Telegram. Otherwise discard it." This isn't an error: the post is "queued for review".
- the summary would say nothing (no role and no way to apply found, which can happen for a post you approved in review): "not posted: the summary would say nothing (…). Review's Send can't post it; if you want it in your channel, post it yourself in Telegram. Otherwise discard it."
- the access check fails, or Telegram gives no public link: an error, with "Approve it again to retry".

A post you approve in review takes the same path, and goes back to the queue with the reason ("could not send: …"). A reason the post already has, as a whole part (`withProblem` in `state.nim`: "no link" doesn't match "no link to the post"), isn't added again however often it bounces back; it gets a count instead, "(3 tries)".

Safety: it is plain text, sent with no entities and no parse mode. TDLib still finds entities in plain text by itself (`getTextEntities`, which `sendMessage` applies): mentions, hashtags, cashtags, bot commands, web, `ton://` and e-mail addresses, media timestamps like `10:30`, bank card numbers. So every value is defused:
- control, invisible and direction characters (U+200B, U+200E, U+200F, U+202A–U+202E, U+2060–U+206F, U+061C and others) are dropped, and so are letters that look blank (Hangul fillers U+115F, U+1160, U+3164, U+FFA0, the Braille blank U+2800); ZWNJ and ZWJ (U+200C, U+200D) stay, for Persian and Kurdish spelling and emoji;
- line breaks and tabs become spaces;
- between two digits of any script, a `.`, `/` or `:` keeps its place and gets a word joiner (U+2060) after it: `10:30`, `٩:٣٠`, `1.500.000`, `22/11/2026` look the same and read in order inside Arabic text (checked with fribidi), and TDLib finds no media timestamp, link or IP address (`192.168.1.1`) in them. A space there would make Arabic text show the number reversed, and a ratio sign "∶" in place of the colon would too;
- elsewhere a space goes after `@`, `#`, `$`, `/` and `:` (whatever follows: `#_x` and `/_x` are entities too), and after a dot not followed by a space or another dot;
- digits: TDLib reads a run of 13 to 19 digits that passes the card checksum (Luhn) as a card number, even with any runs of spaces and dashes between groups (`4111 -- 1111 - 1111 1111`). A run right after `+` (a phone number, `+964 770 000 0002`) is never a card to TDLib, checksum or not, so it's left alone. Only such a run is touched: each separator gets a `·` (`4111 · 1111 · 1111 · 1111`), each number keeping its digits, and a single group is split in fours by `·`. Every other number, like a year, a phone number or separate numbers (`2026`, `+964 770 000 0000`, `07700000000 07711111111`), is left exactly as written. A value cut short is checked again, so a long number cut to 13 digits can't become one.

`tests/tsummary.nim` checks these rules, including every title the adversarial review of #142 tried (`tests/fixtures/summary_hostile_titles.json`), and, with `TELENOTIFY_TEST_TDLIB=/path/to/libtdjson.so`, runs TDLib's own finder on each summary: the only entity must be the post's link (skipped otherwise, as in CI, which has no TDLib). Each value is cut (role and title 60 characters, place 40) and wrapped in Unicode isolation marks, as in the notifications, so Arabic and Latin text don't reorder each other. Nothing else from the post is copied. A dry run sends nothing, as before. The `رابط المنشور: ` line is the last one and starts a line, so the dry run's note of the real bot's posts (`noteOtherBot`, for `compare`) reads it, and the old English `post link: ` too, and a title can't stand in for it.

### Downtime alerts (#3, #16)

Every minute the bot saves a heartbeat. At start-up, a heartbeat older than `alert_after_minutes` (default 10) means it was down. When TDLib reports the connection back after a long gap, that counts too. Either way you get a message in Saved Messages, e.g. "⚠️ tele-notify: was not running for 2h 6m (since 2026-11-18 02:17). Back now." Set `"alerts": false` to only log them.

Messages in Saved Messages never notify you (you wrote them yourself). For real notifications, make a bot of your own with @BotFather, press Start in its chat, and put its token in config.json as `notify_bot_token`. The bot then tells you through it, as a normal Telegram notification on your phone and laptop: when a post goes to the review queue ("🔎 For review", with "Review: telenotify review, or the screen's Review tab (2)" as its last line), when one is published ("✅ Published", also after your review), and for every alert, including the log-out alert below (the Bot API doesn't need your Telegram login). At the first start that reaches you, and again after another account logs in, it says "🔔 tele-notify notifications are on" (not when `notify_published`, `notify_review` and `alerts` are all off, as nothing would follow) (kept as `notify_on_for` in the meta table, so it isn't repeated). A backlog doesn't ping once per post: the one read at start-up, and each chat read later on retry, ends with one summary, e.g. "📥 Backlog read: 3 published, 5 for review" (nothing if both are 0); live posts notify one by one. The role is wrapped in Unicode isolation marks, so an Arabic role can't reorder the English around it. `notify_published` and `notify_review` (both default true) turn each kind off, in config.json or in the screen's Settings → Bot (which says when they can't send anything: no or a malformed `notify_bot_token`, or a dry run); alerts follow `alerts`, so `"alerts": false` keeps them out of the bot as well. A dry run sends no per-post notifications, only its alerts, marked "[dry run]". It sends to your own user id through the Bot API's `sendMessage`. That id is asked from TDLib once and kept in the state database (`notify_chat_id`), so the bot can still reach you once logged out. Sending never holds the bot up: messages wait in a queue of at most 50 (the oldest dropped, with one log line per burst) that a background loop sends, each send limited to 5 s, and api.telegram.org's address is looked up at most every 10 minutes (that lookup can't be interrupted, so a slow resolver now blocks once per period, not once per message; a lookup that fails is remembered for a minute, and an address found before is kept in use for that minute instead; a kept address that can't be connected to, or whose send times out, is marked expired, so the next send looks the name up again, and is still the fallback if that lookup fails, as after a network outage). Messages go out at most one a second (Telegram's Bot FAQ asks that per chat; timed by the monotonic clock, so setting the clock back can't hold them up), so a start-up burst doesn't run into its limit. A 429 (too many messages) is tried once more after Telegram's `retry_after`, at most 30 s; a timeout, a lost connection, a failed lookup or a 5xx is tried once more after 2 s (#127); a timeout or a 502/504 can come after Telegram took the message, and the Bot API can't mark a repeat, so then the message arrives twice, which beats not at all. A 401 or 403 (a wrong token, or you blocked the bot), or a 400 "chat not found" (Start never pressed), is one alert on the terminal screen, "Your notification bot can't reach you: press Start in its chat, or check notify_bot_token", repeated only after a send has worked again. Each message is at most 1000 UTF-16 units (Telegram's way of counting; its own limit is 4096). Every failed send is logged with Telegram's reason (without the token) and never stops anything. A token that doesn't look like one (`digits:letters`) turns notifications off, with one line in the log at start-up. At a stop, what's still queued is sent for up to 18 s (one message with its link wait, the pace, and tried twice; time that passed, by the monotonic clock), or after a log-out for up to 70 s, long enough for a message waiting for its link and out a 429, then the alert tried twice (systemd waits 90 s; an alert that itself meets a 429 may not make it); what's left then is logged as not sent. The terminal screen keeps the token when it saves config.json and leaves it out of the corpus test.

Instagram posts linked in an ad (#114): with `instagram_frontend` in config.json (the address of a kittygram, an open-source Instagram front end, e.g. `"http://127.0.0.1:8430"` on the same server, with `allow_json` on; it must start with `http://` or `https://`, or the config is refused), the bot reads up to two linked Instagram posts per ad: it asks kittygram for `/p/<code>/?json=true`, downloads up to three of the post's pictures from Instagram (through the ordinary downloader, which refuses local addresses; only kittygram may be local) and reads them with the same OCR as Telegram photos, and adds Instagram's own picture descriptions and captions (every picture is OCR'd anyway: a description's "… text that says …" quotes little and reads Arabic badly). The listener judges one post at a time, so all the Instagram reading for one Telegram post gets a budget of about 45 seconds: no picture or post is started with less than 25 seconds of it left (a download may take 20), and a note says so. A step already running still finishes within its own limits, so the real bound is a little higher: up to about 20 seconds of download plus 60 seconds of OCR past that point. A post read in full is remembered by its code for 6 hours (up to 200 posts), so an ad cross-posted to several channels is read once; a post that failed isn't remembered. Links are only taken when `instagram.com` starts the text or follows `/`, `.`, a space, `(`, `:`, a quote or any non-ASCII character such as an Arabic letter or an emoji (never an ASCII letter or digit, so not `notinstagram.com`), in the forms `/p/<code>`, `/reel/<code>`, `/reels/<code>`, `/tv/<code>` and `/<user>/<kind>/<code>`; words like `/reels/audio/…` aren't codes. Share links (`instagram.com/share/p/<token>/`, `instagram.com/share/<token>`) carry a token only Instagram can resolve, so they're skipped with a note. A job-board URL found only in Instagram's text doesn't count when deciding which job link to follow; the text is still read by the filter. Every failure is a note in the verdict's reasons, never an error; a post with no picture read says "instagram <code>: no pictures read". When a short post's (100 characters or fewer, links, hashtags and @mentions left out) only substance is an Instagram link that couldn't be read (kittygram down, no pictures read, out of time, or a share link), a reject becomes a review, as for a photo whose OCR failed (when both happen, the reason names both). `telenotify check` uses it too.

Installing kittygram is optional and separate from the bot: `sudo sh deploy/install-kittygram.sh [PORT]` (port 8430 if not given) on a Debian or Ubuntu server. It installs the packages it needs (a compiler, lua5.1, git, and valkey for kittygram's cache, or redis on releases without valkey such as Debian 12 and Ubuntu 22.04), adds a system user `kittygram` whose home `/opt/kittygram` holds the rest, builds OpenResty 1.27.1.2 and LuaRocks 3.11.1 there from downloads checked against fixed SHA-256 sums, fetches kittygram at a fixed, tested commit, installs its Lua modules at the versions pinned in `deploy/kittygram-luarocks.lock` (the ones running with the bot; LuaRocks checks versions, not checksums), and installs `kittygram.service` with its start script in `/usr/local/libexec/kittygram-start` (a system service: listening on 127.0.0.1 only, at most 300 MB, its files read-only apart from its own folder). Of kittygram's settings it changes only the port, one worker, JSON answers on (what the bot reads), a random secret, and nginx's listen line (127.0.0.1); running it again keeps the settings and the secret and rebuilds only what's missing or out of date, so a new kittygram commit in the script is an update. Root never runs, writes or changes the permissions of anything inside `/opt/kittygram`: kittygram's own code (and its modules' build scripts) runs there and could plant a link or replace a program, so all of that is done as the kittygram user, and the start script lives where only root can change it. Valkey and redis never both run: a redis already installed is used as it is. The Lua modules are installed from scratch whenever the commit or the pins change, since LuaRocks would keep loading a newer version already there. Before starting kittygram it checks that nothing else answers on the port, and it ends by checking that the service is running and that its front page is kittygram's (a failure stops the service rather than leaving it restarting every 10 s), and then prints the `instagram_frontend` line for config.json. This is how the VPS's kittygram was set up by hand on 2026-11-21. Tests: `tui/tests/test_install_kittygram.py`, with the system faked.

If Telegram logs the account out (from your phone, or a session reset), the bot can't write to your Saved Messages any more, so it keeps the news in its state file instead (and, with `notify_bot_token`, sends it through your own bot, once per log-out): one alert that leads with the cause, then the commands to run, in plain text: "Logged out of Telegram. To log in again: systemctl --user stop tele-notify; telenotify login; systemctl --user start tele-notify" (login needs the bot stopped), and a `login_needed` flag. While the flag is set, the terminal screen's Bot box opens with "Not logged in to Telegram. Stop, run telenotify login, then Start." (right for a log-out and for a first start before logging in) (shorter in a narrow window; where the box is too short for everything, as at 80 × 24, the Service row is left out meanwhile so Mode and the pending count stay in view). systemd keeps restarting the bot meanwhile; the alert isn't repeated until you've logged in again. The flag is cleared as soon as the bot is logged in at its next start (TDLib ready), not after the chat list has loaded.

### Chats you can't read any more (#16)

A channel or group that bans you (or that you leave) simply stops sending you its posts; nothing fails, so the bot used to go quiet about it. Now, for every chat in `chats`, you get an alert when this account was banned, is no longer a member, the chat can't be opened or read at all (TDLib errors such as `CHANNEL_PRIVATE` or "Chat not found"; a timeout or a lost connection doesn't count), or a basic group became a supergroup with a new id (the alert gives the id to put in `config.json`). It's checked at start-up (`getChat`, then `getSupergroup` / `getBasicGroup` for this account's member status), on TDLib's live `updateSupergroup` / `updateBasicGroup`, and whenever a backlog read fails. TDLib repeats those updates on every start, so the last state alerted per chat is kept in the `meta` table (`chat_access:<chat id>`): one alert per change, also across restarts. When you can read the chat again (a member again, or its backlog was read), the state is cleared and the log says "can read X again".

### Building TDLib and running as a service

- `deploy/build-tdlib.sh` (shell, because it only drives cmake for a C++ library) builds `lib/libtdjson.so`. Expect about 4 GB of RAM per parallel job (`JOBS=2` by default, about 20–40 minutes; `JOBS=1` on a 4 GB machine, about an hour). It builds a fixed TDLib commit, the one this code was tested with, because TDLib renames fields from time to time. To move to a newer TDLib, change `TD_REF` in the script and run the tests and a dry run. It builds `gperf` from GNU's source if it's missing, so no system install is needed beyond a compiler, cmake, and the OpenSSL and zlib headers (already on your machine).
- `deploy/tele-notify.service` is a systemd **user** unit (no sudo) with `Restart=always`. The comments at its top say how to install it.
- `deploy/install.sh` builds and installs everything for your user, no sudo: `telenotify` and `telenotify-tui` in `~/.local/bin`, TDLib and the screen's Python environment in `~/.local/lib/tele-notify/`, the service file; then it restarts the bot if it runs. Run it again after `git pull` to update. The config is `~/.config/tele-notify/config.json` and the state and Telegram login stay in `~/.local/share/tele-notify/`; every command finds the config without `--config` (`--config:FILE` or `$TELENOTIFY_CONFIG` pick another). TDLib is found next to the program (`lib/`), in `../lib/` or `../lib/tele-notify/`, or on the system path.
- `deploy/push-to-server.sh [--allow-dirty] USER@HOST [ssh options]` does the same for a bot running on a server: it builds here (refusing uncommitted changes in `src/`, `tui/`, `deploy/` or the build files unless `--allow-dirty`), sends the bot, TDLib and the screen over ssh, and on the server first checks that the new bot starts and TDLib finds its libraries and builds the screen in a new venv; only then does it swap the bot, TDLib, the screen and the unit together, keeping what was installed as `*.prev` (a re-run that changes nothing keeps them; the script's header has the one-line manual rollback). A running service is restarted and, if it isn't still up 10 s later, rolled back to the `*.prev` set automatically, and the script fails. The server's config, state and login, and the service's drop-ins, are left alone. The bot and TDLib need glibc, libssl and libsqlite3 on the server no older than the ones they were built against.
- **Self-update (#123).** Each install from `push-to-server.sh` also installs `deploy/self-update.sh` and a systemd user timer (`tele-notify-update.timer`, every night at 04:00 Baghdad time, give or take 20 minutes; a missed night runs at the next start). The script asks GitHub for the latest release and installs it only if it's newer than what's installed: a push records `git describe` (for example `v4.1.0-3-gabc1234`, three commits after v4.1.0), so a pushed build is never replaced by the release it came after. It downloads the release's `tele-notify-<tag>.tar.gz`, checks it against its `.sha256`, and runs the package's own `install-staged.sh`, the same installer a push runs (checks first, swaps, keeps `*.prev`, rolls back if the bot doesn't stay up). TDLib isn't in a release; the server's is kept. The timer is turned on at its first install only, so `systemctl --user disable --now tele-notify-update.timer` stays off across later installs. A failure becomes an alert on the terminal screen and in your notification bot (`telenotify alert TEXT`, which keeps the alert and sends it through `notify_bot_token` to the user id the bot kept). Logs: `journalctl --user -u tele-notify-update`. Releases come from `.github/workflows/release.yml`: pushing a `v*` tag builds the bot on Ubuntu 22.04 (an older glibc, so it runs on more servers), packages it with `deploy/make-package.sh` (the same set a push sends) and attaches the package and its checksum to a GitHub release. The agent tags a release after each deploy (the owner's choice, 2026-11-21): the tag goes on the commit first and the push follows, so the server records the tag and the nightly run doesn't reinstall the same code. If GitHub can't be reached, the script tries three times a minute apart (after a reboot the network may not be up yet). A release that doesn't work on this server (its bot doesn't start here, or doesn't stay up: `install-staged.sh` exits 3) is remembered (`~/.local/lib/tele-notify/failed-version`) and not tried again; the next release is. Other failures (another push running, pip or the network) are tried again the next night. A latest release that isn't newer needn't have a package (the old hand-made v4.0 has none), so it's simply up to date. The update service may run at most 45 minutes, and a shutdown waits up to 5 minutes for a swap in progress. The release workflow's Nim setup action is pinned to a commit, as what it builds reaches the server unattended; the Nim packages themselves come from nimble's version ranges (there is no lock file yet), so GitHub and nimble are what an update trusts. The checksum catches a broken download, not a forged release.

### First run on your account (2026-11-18, dry run)

187 unread messages in 18 of the 40 watched chats, judged in under 2 minutes: 2 would have been published, 4 went to review, 181 were rejected, no errors, and the stop was clean. Things it showed, all fixed before merging: TDLib's "connection Updating" state was logged as a lost connection, TDLib printed harmless internal errors (now silenced), and `stats` cut Arabic channel names in the middle of a letter.

One of the 2 publishes was wrong: an expatriate Electrical QA/QC job asking for "extensive experience" (خبرة واسعة). The channel starts every post with "#حديثي_التخرج واصحاب الخبرة" (fresh graduates *and* experienced), and the filter took that as an entry-level sign. That's a filter change, so it goes into stage 4 with a corpus check.

### Not done yet

- **The nice dashboard (#10).** `review` and `stats` are plain text for now. The data the dashboard needs is all in SQLite already.
- **`telenotify export`.** It will export review decisions and Saved Messages as corpus labels, replacing the Python scripts in the corpus folder.
- **Albums and edits** were done in stage 4 ([section 9](#9-stage-4-side-by-side-with-the-python-bot)).
- **Duplicates after a crash.** If the bot dies between forwarding a post and recording it, the post is forwarded again at the next start. That window is a few milliseconds.

---

## 9. Stage 4: side by side with the Python bot

The plan: run `telenotify run --dry-run` next to the Python bot for about a week, look at where they disagree, fix what's wrong, then switch.

### `telenotify compare` (`compare.nim`)

The Python bot uses the same Telegram account, so what it sends to your channel shows up as *your own outgoing messages* there. During a dry run, the Nim bot notes each one (table `other_bot` in `state-dryrun.sqlite3`) with where it came from:
- a forward from a channel carries its origin (channel and message id);
- a "match: … post link: https://t.me/…" message, or a summary's last line "رابط المنشور: https://t.me/…" (#80), is resolved with TDLib's `getMessageLinkInfo` (only a line that starts with it, the last one);
- anything else (a forward from a group) is matched by text later.

`telenotify compare --days:7` then prints three lists:

| List | Meaning |
|---|---|
| both | Both published it. Also counts when Nim skipped it as a copy of an ad it had already published from another channel |
| Python only | With Nim's verdict and reasons. Either Nim missed a good post, or (much more often, on the corpus) Python published a wrong one |
| Nim only | What Nim would have published and Python didn't |

Posts are matched by where they came from, or else by the message text: the dry run keeps each message's own text (`processed.raw_text`). A "post link" to a channel without a username (`t.me/None/<id>`) is matched by the message id. Text matching also catches a forward of a forward (its origin is the first channel, not the one Nim watched) An album the Python bot forwarded counts once.

Limits: your own posts in the channel during the dry run are counted as the Python bot's. And the Python bot marks what it reads as read, so after a restart of the dry run its start-up backlog is nearly empty: posts from while it was down show as "Nim never saw it".

It only reads the database, so it works while the dry run is running.

### Albums

The pictures of an album arrive as separate messages (one has the caption, the others only pictures). They're now judged together: the caption, plus the text in up to 6 pictures, as one post, and forwarded together so your channel gets the whole album. In the backlog the parts are next to each other; live, the parts arrive a moment apart, so when the first part is the last thing in the queue, the bot waits 1.5 s for the rest.

Approving a reviewed album forwards the whole album. If Telegram refuses some pictures of an album, the rest still counts as forwarded, so no extra link is sent. If part of an album was already handled (the backlog had only its first picture, a picture arrived late, or another chat's message got in between), the other parts are recorded with it, never judged or forwarded on their own: `processed.album_id` remembers which album a message belongs to. `stats` counts an album as one post.

### Edits

Some channels post "job ad coming soon" and edit the real ad in later. When a message from a watched chat is edited (TDLib's `updateMessageContent`), the bot fetches it again. It's judged again only if it was **rejected** before and its text really changed (a fingerprint of the text is kept in `processed.text_hash`; link previews loading also count as edits, but don't change the text). Published or reviewed posts are left alone, so an edit never causes a second forward. Ignored: edits of messages older than 2 days, edits of album pictures (one picture isn't the post), messages judged before stage 4 (they have no fingerprint, so a change can't be told apart), and edits of messages the bot never saw (it wasn't running then; judging the edit as new would forward old posts).

### A filter change from the first real run

"Extensive experience" (خبرة واسعة/قوية/طويلة…, also "الخبرة الواسعة", "خبرات واسعة", "خبرة عملية واسعة"; "extensive/proven/strong/solid … experience", optionally with "hands-on", "field", "practical"…) now turns a publish into a review: it says "not a beginner" even when a channel adds "#حديثي_التخرج واصحاب الخبرة" to every post. It doesn't apply when the post gives a number of years or says no experience is needed, or when, in the same sentence, the phrase is:
- optional or denied, before or after it: "preferably", "يفضل", "no extensive experience required", "extensive experience is not required / a plus", "الخبرة الواسعة غير مطلوبة";
- offered: "you will gain solid experience", "ستحصل على خبرة عملية واسعة";
- about the company or its staff: "a company with extensive experience", "شركة ذات خبرة واسعة";
- about a tool or skill: "strong Excel experience", "strong experience with MS Office".

It reads the text with its punctuation, so "Contract No. 12. Strong experience required" isn't cancelled by the "No" of another sentence. It's a heuristic: wordings it doesn't know ("extensive oil and gas experience", "خبرة كافية") still publish, as before. The corpus numbers didn't change (it was a case the corpus didn't have); the post from the first run now goes to review.

### Four false publishes from the corpus

The corpus still had four posts the filter published wrongly. Each became a rule, with a test of an invented post like it:

- **Training only for current students** ("ان يكون احد طلبة المرحلة الثانية او الثالثة"). You've graduated, so a post limited to students goes to review, unless it also mentions graduates ("للطلبة والخريجين" still publishes). Only wording for people who *have* graduated counts: "expected to graduate in 2027" and "مشروع التخرج" are about students. "Current students are not eligible", "not open to current students" and "يستثنى الطلبة الحاليين" are the opposite and don't count. Wordings that also describe the work ("help university students with their laptops") aren't in the list. The wordings are `studentsOnlyPhrases` and `graduateWords` in `defaults.nim`.
- **A CV.** A 15-year-old's CV got through because "get the job done" counted as job wording and "volunteer" as entry level. A line that is only a CV heading ("WHO AM I?", "Career objective:", "نبذه عني") now marks a job seeker, like "my name is" already did. Only as a line of its own: "Who am I looking for?" and "include a career objective in your CV" are employers.
- **"HSE Lead".** Your role only appeared as a field in the requirements ("Degree in Engineering"), so the old senior-title check, which looks for a seniority word *next to your role*, saw nothing. Now, when the match is only a field, a seniority word on the **headline** (the first line) holds the post back. Only the headline: lower lines say "support the project manager", and "الوجبة الاولى" ("first batch") isn't "senior". It doesn't apply when the post explicitly welcomes fresh graduates: then the senior word on the headline is someone else's (a "Senior Talent Acquisition Partner" posting it, "مكتب المدير العام" announcing it). Graduate schemes are exempt too, when the top three lines say "development/graduate programme" or the headline itself says "Program": Baker Hughes calls its scheme "LEAD". "Part of the expansion programme" doesn't count.

Corpus: wrong publishes 4 → 0; true matches published 5 → 5, none lost.

### Multi-job posts, backlog progress, retries, stats (#29, #11, #5, #13)

- **Multi-job posts (#29).** The second sample in the issue (nine numbered jobs from "Iraq Jobs Foundation") wasn't split at all: your field ("هندسة كهرباء") only appeared as job 3's degree requirement, and only *title* keywords started a job block. Now a numbered item whose first line names some job ("3. Commissioning Engineer") and that mentions one of your fields is a job block too. Two smaller fixes came out of it: "• 5+ years" is a quantity, not a list number (only "🔹 1-" with a list mark counts), and the requirements of a job that isn't yours are dropped with it instead of becoming shared by every job. Job nouns also match in the plural ("Project Managers"). The sample is now rejected for the right reason: job 3 itself asks for 5+ years.
- **Backlog progress (#11).** A progress bar doesn't suit a service whose output goes to the journal, so the bot logs `backlog: 25/187 checked` every 25 messages and once at the end.
- **Retry chats that failed at startup (#5).** If a chat's unread messages couldn't be read at startup (no connection yet, a timeout), they used to wait for the next restart. Now such chats are tried again about every minute until it works; anything handled live meanwhile is skipped as already seen.
- **Approved reviews count as forwarded (#13).** `telenotify stats` counted only posts the bot forwarded itself. A post you approve in `telenotify review` now counts too, once it has been sent.

### The switch (done 2026-11-18)

The owner approved the switch after checking the terminal screen. The last 7 days of `telenotify compare` before it: no post only the Python bot published that Nim lost (its one Python-only post was in Nim's review queue), and 3 Basra oil-sector posts for fresh graduates that only Nim found. These were the steps:

1. Look at `telenotify compare` after the trial, and at `telenotify review --dry-run`.
2. Stop the Python bot (Ctrl-C in its terminal). Don't run both for real: each would forward the same posts.
3. Stop the dry run: `systemctl --user disable --now tele-notify-dryrun`. Both use the same TDLib login, and only one program can hold it at a time.
4. Install the real service: copy `deploy/tele-notify.service` to `~/.config/systemd/user/`, point its `--config:` at your config, then `systemctl --user daemon-reload && systemctl --user enable --now tele-notify`. Keep `loginctl enable-linger` on so it runs while you're logged out.
5. Watch the first minutes: `journalctl --user -u tele-notify -f`. The real bot starts with its own empty `state.sqlite3`, so it judges whatever is unread. The Python bot marked what it handled as read, so that's only what it missed (a crashed chat, for example).
6. Merge the `legacy/` move (the Python files go to `legacy/`; the READMEs say the Nim bot is the one that runs).

To go back: `systemctl --user disable --now tele-notify`, then start the Python bot from `legacy/` as before.

## 10. The terminal screen (`tui/`, issue #10)

A live screen you open from a terminal when you want it: `tui/.venv/bin/telenotify-tui --config:config.json` (add `--dry-run` for the dry run). Four tabs: **Dashboard**, **Review**, **Search** and **Settings**; `tui/README.md` lists the keys.

**Why Python and Textual, next to a Nim bot.** You picked [Textual](https://github.com/Textualize/textual): it has real widgets (tables, tabs, dialogs), mouse support and good-looking defaults, and Nim has nothing close (illwill is low-level; nimwave hasn't been updated since 2023). The cost is a second language, so the line between them is kept sharp:

- The **bot** does all the work and owns the state database.
- The **screen** only reads the database, writes your review decisions (exactly as `telenotify review` does: `pending` → `to_send` or `discarded`, and only if it's still pending, so nothing is approved twice; plus `kept`, a match it must not send because it already reached the channel), edits `config.json`, and runs the `telenotify` program for anything that needs the filter (explain, re-judge, test settings on the corpus). There is only one filter, so the screen and the bot can't disagree.

**What the bot shares for it** (all in `state.sqlite3`):

| Where | What |
|---|---|
| `alerts` table | every alert also sent to Saved Messages (newest 500) |
| `chats` table | your chats with name, kind, whether watched, and access status (#16); refreshed in the background after start-up |
| `meta` | `mode` (real / dry-run), `started_at`, `connection` (starting, ready, connecting, stopped…) and the heartbeat `last_alive` |
| `processed.raw_text` | the text of each post, for searching; cleared after `keep_text_days` (default 90; never in a dry run, `compare` needs it). The file is private to you (0600) |

**Things the reviews caught** (each is a test now):

- Text from Telegram is written by strangers. In a table cell it was read as formatting (`[/]` crashed the app), and terminal escape codes in it reached your real terminal. Everything from the database is cleaned before it's shown, and table cells are plain text.
- The "what did the filter read" view parsed the *first* `verdict:` line, so a post containing "verdict: publish" could fake its own verdict. It now reads the last one.
- Pressing `s` twice, or holding it, approved the next post unread. Decisions are ignored for a moment after a new post appears, and `u` undoes the last one if the bot hasn't sent it yet.
- The screen and the bot have to agree on which database: `dry_run` in config.json and `data_dir` (`~`, relative paths, which count from config.json's folder) follow the same rules in both.
- Arabic posts showed with their words in the wrong order: Textual draws every line left to right in stored order. The screen now shapes and reorders right-to-left text itself (`tui/telenotify_tui/rtl.py`, with `python-bidi` and `arabic-reshaper`), one wrapped line at a time so a long post's lines stay in order, and switches VTE terminals to explicit bidi mode while it runs so they don't reverse it again. kitty draws each Arabic word right to left itself but keeps words in stored order, so in kitty only the word order is changed (the `words` mode, picked by itself). `TELENOTIFY_BIDI=full|words|off` or `--bidi=` chooses; `--no-bidi` turns it off. Copying a post gives the stored text. Bidi control characters in a post are dropped, so a post can't reorder anything but its own words.
- Saving settings makes a timestamped backup, writes atomically, keeps keys it doesn't know and your API keys, refuses values the bot couldn't read back, and only offers a restart if the bot is running.

