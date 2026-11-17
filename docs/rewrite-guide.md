# The Nim rewrite: a guide for future you

This document explains what was built, why it looks the way it does, and how to keep working on it, so you don't have to reverse-engineer anything. Read it top to bottom once; after that, use the headings as a reference.

- **Problems report** (what was wrong with the Python version and why): [`problems-report.md`](problems-report.md)
- **Where we are**: stages 1 and 2 of 4 are done: the filter core, plus OCR and job-board reading (see [section 7](#7-stage-2-gathering-the-text-ocr-and-job-boards)). See [Roadmap](#roadmap).

---

## 1. The big picture

The old `main.py` did everything in one file: Telegram, OCR, scraping, filtering and state. The rewrite splits the job into layers and builds them bottom-up:

```
             ┌────────────────────────────────────────────┐
 stage 3     │ listener: Telegram via TDLib, state in      │  (not built yet)
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
  fixtures/                saved job pages (scripts/styles removed) and OCR images
  config.nims              lets the tests import from src/
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
- **Several links: none followed.** A post with several job links is almost always several jobs, and reading all their pages would mix them together.
- **Downloads have rules** (`fetch.nim`):
  - 20-second time limit, after which the connection is *closed* (an abandoned connection leaked a socket);
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
| LinkedIn post | `linkedin.com/posts/<slug>` | the longest post-text block |
| `lnkd.in` | `lnkd.in/<code>` | resolved first, then read if the target is supported |
| JSON-LD job pages | `yadanoo.com/job/<id>`, `shift-iraq.com/jobs/<id>`, + `extra_job_sites` in config | **only** the page's schema.org `JobPosting` block |

The scraped text starts with `Job title: …` and `Location: …` lines, which the filter's title and location rules understand.

**Gone:**
- **Iraq Jobs Scout**: `iqjscout.com` now redirects to Yadanoo's home page, so old IJS links are dead; new posts link to Yadanoo directly.
- **Workable and mselect**: zero links in the 3,834 recent posts, so there was no real page to test against. Adding a board back means a `BoardKind`, a pattern in `findJobLinks`, a branch in `extract` and a saved page in `tests/fixtures/`.

**Why "JSON-LD only" for Yadanoo-style sites.** A Yadanoo job page also contains a city picker listing *every* city, Basra included. Reading the whole page would make every job look like a Basra job. A test checks that a Duhok job's text never contains "Basrah".

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
| 3 | Listener: TDLib (Telegram's official library, via its C JSON interface), unread bootstrap using the real read marker, one processing queue (fixes the duplicates race), state and stats in SQLite (unblocks #10–#15), downtime alerts (#3, #16), systemd unit (#6), `telenotify export` for the corpus | next. **Needs you once:** a fresh Telegram login with the phone code |
| 3b | **Review queue**, as you described: `review` posts are stored by tele-notify (not sent to Saved Messages), and the CLI dashboard (#10) shows them with *send* / *discard*. Each decision is also saved as a label, so the corpus grows on its own while you work | with/after 3 |
| 4 | Run it next to the Python bot (Nim only logs what it *would* do), compare a week of verdicts, then switch, and move the Python files to `legacy/` | after 3 |
