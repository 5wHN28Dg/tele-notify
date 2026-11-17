# The Nim rewrite: a guide for future you

This document explains what was built, why it looks the way it does, and how to keep working on it, so you don't have to reverse-engineer anything. Read it top to bottom once; after that, use the headings as a reference.

- **Problems report** (what was wrong with the Python version and why): [`problems-report.md`](problems-report.md)
- **Where we are**: stages 1–3 are done: the filter, text gathering (OCR, job boards; [section 7](#7-stage-2-gathering-the-text-ocr-and-job-boards)) and the Telegram listener ([section 8](#8-stage-3-the-telegram-listener)). Next is stage 4, running next to the Python bot. See [Roadmap](#roadmap).

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

The Python files (`main.py`, `main_simple.py`, `tools/`) are untouched. They keep running in production until stage 4.

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
| JSON-LD job pages | `yadanoo.com/job/<id>`, `shift-iraq.com/jobs/<id>`, + `extra_job_sites` in config | **only** the page's schema.org `JobPosting` block |

The scraped text starts with `Job title: …` and `Location: …` lines, which the filter's title and location rules understand.

**Gone:**
- **Iraq Jobs Scout**: `iqjscout.com` now redirects to Yadanoo's home page, so old IJS links are dead; new posts link to Yadanoo directly.
- **Workable and mselect**: zero links in the 3,834 recent posts, so there was no real page to test against. Adding a board back means a `BoardKind`, a pattern in `findJobLinks`, a branch in `extract` and a saved page in `tests/fixtures/`.

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
| 4 | Run `telenotify run --dry-run` next to the Python bot for a week, compare (`telenotify stats`, the `processed` table), then switch: stop the Python service, start the Nim one, move the Python files to `legacy/` | next |
| 5 | The nice CLI dashboard (#10–#15): live counts, review queue with keys, highlighted events, on top of the SQLite state that now exists. Plus `telenotify export` so review decisions become corpus labels | after 4 |


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
   - **publish**: if it repeats something published in the last 14 days (same job link, or ≥ 80% the same words), skip it as a duplicate. A bare domain like `https://company.com` doesn't count as a job link: many different jobs from one company end with it. Otherwise forward it to `target_chat`. Where the channel forbids forwarding, send "match: role / post link" instead, using Telegram's real link, which also works for private channels.
   - **review**: put it in the review queue in `state.sqlite3`, not in Saved Messages.
   - **reject**: nothing.
4. Record the verdict, reasons and action in `processed`, then mark the message read.

There is one queue, and messages are handled **one at a time**. First the startup backlog (every watched chat's unread messages, oldest first, found with the chat's real read marker), then live messages. Live messages that arrive while the backlog is still being read wait until it's done. Otherwise a new message could be marked read first, and since Telegram's read marker is a single position per chat, the backlog behind it would count as read: if the bot then died, those messages would never be handled. One queue alone removes the old duplicate race and the "deleted posts make us skip unread messages" bug.

A failure (a download, OCR, a Telegram error such as a flood limit) affects only its own message, and the message isn't lost: it goes to the review queue with the error, so you decide.

A stop request (Ctrl-C, `systemctl stop`) is honoured after the current message, not after the whole backlog. Whatever is left stays unread and is handled at the next start.

If the Telegram session ends while the bot runs (you logged it out from your phone, for example), the bot stops with an error, so systemd restarts it and the log says why. Before, it would have hung while looking alive. Any request still waiting for TDLib then fails instead of waiting forever (`td.nim`).

Photos are deleted from TDLib's file folder once read, so a bot that runs for months doesn't fill the disk.

### Commands

| Command | What |
|---|---|
| `telenotify login` | Once: phone number, the code Telegram sends, your 2FA password. Prints your chats with their IDs (it replaces `get_chat_ids.py`). The login is saved in `data_dir/tdlib` |
| `telenotify run [--dry-run]` | The bot. `--dry-run` (or `"dry_run": true`) judges and records everything but forwards nothing and marks nothing read |
| `telenotify review [--dry-run]` | Goes through the review queue: shows the post, its link and why it's unclear; `s` approves it, `d` discards it, `n` skips it, `q` quits |
| `telenotify stats [--days:N] [--dry-run]` | Per chat: how many messages got each verdict, how many were forwarded, and how many reviews are waiting |

**Why `review` doesn't send anything itself.** TDLib locks its database, so only one program can use your login at a time. If `review` opened Telegram, you'd have to stop the bot first. So `review` only writes your decision (status `to_send`) to SQLite, and the running bot sends approved posts within about five seconds (`sendApproved`). If one can't be sent, it goes back to the queue with the reason. `login` still needs the bot stopped, but you only run it once.

**Dry runs have their own state** (`state-dryrun.sqlite3`). Otherwise every message the dry run judged would be skipped as "already seen" once you switch to the real bot. Add `--dry-run` to `review` and `stats` to look at it.

### State (`state.sqlite3` in `data_dir`)

| Table | Holds |
|---|---|
| `processed` | every judged message with verdict, role, reasons and action |
| `published` | what was published, for duplicate checks |
| `reviews` | the queue and your decisions (`pending` → `to_send` → `sent`, or `discarded`). Those decisions are labels for the corpus: stage 5 adds an export |
| `meta` | the heartbeat |

`config.json` is now only *read*. The old bot rewrote it on every forward, which could throw away your edits.

Open it with `sqlite3 ~/.local/share/tele-notify/state.sqlite3`. For example, `SELECT chat_title, verdict, action, reasons FROM processed ORDER BY at DESC LIMIT 20;`

### Downtime alerts (#3, #16)

Every minute the bot saves a heartbeat. At start-up, a heartbeat older than `alert_after_minutes` (default 10) means it was down. When TDLib reports the connection back after a long gap, that counts too. Either way you get a message in Saved Messages, e.g. "⚠️ tele-notify: was not running for 2h 6m (since 2026-11-18 02:17). Back now." Set `"alerts": false` to only log them.

### Building TDLib and running as a service

- `deploy/build-tdlib.sh` (shell, because it only drives cmake for a C++ library) builds `lib/libtdjson.so`. Expect about 4 GB of RAM per parallel job (`JOBS=2` by default) and 20–40 minutes. It builds a fixed TDLib commit, the one this code was tested with, because TDLib renames fields from time to time. To move to a newer TDLib, change `TD_REF` in the script and run the tests and a dry run. It builds `gperf` from GNU's source if it's missing, so no system install is needed beyond a compiler, cmake, and the OpenSSL and zlib headers (already on your machine).
- `deploy/tele-notify.service` is a systemd **user** unit (no sudo) with `Restart=always`. The comments at its top say how to install it.

### Not done yet

- **The nice dashboard (#10).** `review` and `stats` are plain text for now. The data the dashboard needs is all in SQLite already.
- **`telenotify export`.** It will export review decisions and Saved Messages as corpus labels, replacing the Python scripts in the corpus folder.
- **First run on your account.** That needs your login.
