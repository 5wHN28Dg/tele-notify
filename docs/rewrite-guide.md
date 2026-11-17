# The Nim rewrite: a guide for future you

This document explains what was built, why it looks the way it does, and how to keep working on it, so you don't have to reverse-engineer anything. Read it top to bottom once; after that, use the headings as a reference.

- **Problems report** (what was wrong with the Python version and why): [`problems-report.md`](problems-report.md)
- **Where we are**: stage 1 of 4 is done (the filter core). See [Roadmap](#roadmap).

---

## 1. The big picture

The old `main.py` did everything in one file: Telegram, OCR, scraping, filtering and state. The rewrite splits the job into layers and builds them bottom-up:

```
             ┌────────────────────────────────────────────┐
 stage 3     │ listener: Telegram via TDLib, state in      │  (not built yet)
             │ SQLite, forwards, alerts                    │
             ├────────────────────────────────────────────┤
 stage 2     │ text gathering: OCR (tesseract), scrapers   │  (not built yet)
             │ for LinkedIn / IJS / Khana / Taeen / ...    │
             ├────────────────────────────────────────────┤
 stage 1     │ FILTER CORE: text -> verdict + reasons      │  ✅ this PR
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
| `review` | Role and place fit, but the level is unclear | Forward to your Saved Messages (like the old "manual review") |
| `reject` | Not for you, with a reason | Nothing; it's logged |

The old code had no "review" for unclear posts: it **published** them (stage 2 inference). On your Saved Messages that rule caused 80 of 96 false positives. If you ever want the old behaviour back, set `"publish_when_unsure": true` in config.json.

---

## 2. Files

```
telenotify.nimble          package definition (name, version, dependency on nim-regex)
src/
  telenotify.nim           the command-line tool: `check` and `eval`
  telenotify/
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
  tclassify.nim            one test per problem in the report
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

The text becomes lower-case, with letter variants folded (أ/إ/آ→ا, ة→ه, ى→ي, گ→ك), Arabic-Indic digits turned into 0–9, diacritics and invisible characters removed. Keywords from your config go through the **same** function, so the two sides always agree. That's why your config can say "البصرة" while the code internally compares "البصره".

There is also `flatten`, used only for keyword search. It turns `-`, `_`, `/`, `#`, `:` and similar into spaces, so `#مهندس_كهرباء` and `iqjscout.com/jobs/electrical-engineer-34` contain normal words.

### Step 1: is it a job advert at all?

- If it matches a phrase from `notAJob` in `defaults.nim` (job seekers, school fees, course ads), or the job-seeker regex ("ابحث عن عمل", "محتاج وظيفه"…): **reject**.
- If it has no job-advert wording at all (`jobSignals`: مطلوب، تعلن، hiring, vacancy, CV…): **reject**.

Our example has "تعلن" and "فرص عمل", so it continues.

### Step 2: split into jobs (`blocks.nim`)

Lines that start a list item ("1-", "2.", "3)", "1️⃣") or are separators ("━━━━") cut the post into pieces. The first piece is the *header*.

- If the header itself names one of your job titles, it's a single-job post with a requirements list, so the whole post stays one block.
- Otherwise each list piece that names a job title becomes a job. Pieces after it are glued onto it (its requirements, the footer with the address). Pieces before the first job are *other* jobs and are dropped.

In the example: the header has no title, piece 3 ("مبرمج تطبيقات") does, and the footer "عنوان الشركة البصرة" is glued onto it. Piece 2, with its "3 years", is dropped, which fixes the multi-job mix-up from issue #29.

Each job block is then judged with the header as context (`judgeBlock`). The post gets the best verdict of its blocks.

### Step 3: judge one block (`judgeBlock` in `classify.nim`)

1. **Role.** Search the block for three kinds of role keyword:
   - `entry`: roles that are entry-level by nature (help desk, فني دعم);
   - `title`: real job titles (electrical engineer, مهندس كهرباء);
   - `field`: degrees and fields (electrical engineering, علوم حاسبات).

   A role is ignored if a seniority word (senior, lead, مشرف، اول…) is within a few words of it on the same line, or if it is part of an exclusion like "cost control engineer". No role left: **reject**.
2. **Location** (`locate`).
   - A line that says where the job is or where you must live ("📍", "Location:", "موقع العمل", "من سكنه…") decides first.
   - Otherwise: Basra mentioned and no other city means Basra; Basra and another city with no location line means *mixed* (**review**).
   - "شارع البصرة" (a street) doesn't count as Basra.
3. **Experience** (`experience.nim`). The *number* of years required is extracted (see section 4). More than `max_experience_years` (default 1): **reject**.
4. **Certification.** One of your `certifications` on a line with "required/must/مطلوب/يجب" (and not "a plus/preferred/يفضل") is mandatory: **reject**. If the post also says "apply even if you don't meet every requirement", it's **review** instead (issue #25: that phrase can excuse a cert, never years).
5. **Positive evidence**: one of your level keywords (not in a "mentor junior engineers" sentence), "0–1 years"/"no experience", or an `entry` role.
   - Found, and the title lines don't look senior: **publish**.
   - Found, but the title line says "Lead"/"Senior": **review**.
   - Nothing either way: **review** (or publish with `publish_when_unsure`).

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

For each line that mentions experience (experience/exp/خبره/خبرات):

1. **Find quantities.**
   - Digits with a unit ("3 years", "3+ yrs", "3-5 years", "من 5 الي 12 سنه").
   - English number words ("two years").
   - Arabic number words ("خمس سنوات").
   - Duals ("سنتين").
   - A lone "سنه" after خبره/عن/من/تتجاوز.
2. **Keep only quantities that are about experience.** They must be within about 10 words of the experience word, and not an age, contract length or program duration ("Program Duration: 3.5 Years").
3. **Ranges use the lower end.** "1-3 years" and "سنة – خمس سنوات" both mean 1.
4. **Decimals round up.** "1.5 years" counts as 2.
5. **Upper bounds** ("less than", "لا تتجاوز") mark the post entry-friendly instead.
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
| `scripts/` | the Python scripts used to export and replay. Temporary: stage 3 replaces them with a `telenotify export` command |

**The labels were made by AI reviewers, not by you.** The rubric was: real job, Basra or remote, IT/electrical/control role, reachable for a fresh graduate. I spot-checked a sample and agreed, but you know the market better. Some labels are debatable: e.g. summer training for *current students* is labelled FALSE because the rubric said "fresh graduate", but your channel's audience may want it. Fix any label by editing `eval.jsonl` (change `"label"`) and re-run `eval`.

### Current numbers

Same 196 labelled posts, your deployed keywords:

| | Old Python | Nim, default | Nim, `publish_when_unsure` |
|---|---|---|---|
| Published, labelled FALSE | **96** | **2** | 5 |
| Published, labelled TRUE | 9 | 4 | 8 |
| TRUE sent to review | 5 | 10 | 6 |
| TRUE lost (rejected) | 0 | 0 | 0 |
| Total sent to review | 57 | 68 | 58 |

The rules were tuned while looking at this same corpus, so expect real-life numbers to be somewhat worse. The way to keep them honest is to keep adding newly saved posts to the corpus.

---

## 6. Nim things used here, in case you're rusty

- **`iterator hits*(...)`**: a function that `yield`s many results lazily; you loop over it with `for h in s.hits(list)`.
- **`let pat = re2(r"...")`** at module level: compiled once at program start. `re2` is from [nim-regex](https://github.com/nitely/nim-regex) (pure Nim). It supports the variable-length look-behind the old Python code needed. Use `findAll`, `contains`, `find(s, re, m)`. Groups are **0-based for capture groups** (`m.group(0)` is the *first* `( )`), and `m.boundaries` is the whole match.
- **Byte offsets vs characters**: Nim strings are UTF-8 bytes, and Arabic letters are 2 bytes. Never slice `s[a ..< b]` with computed offsets; use `runeSafe(s, a, b)` from `normalize.nim`, which never cuts a letter in half.
- **`j{"key"}.getInt(1)`**: the `{}` accessor returns nil instead of raising when the key is missing, and `getInt(default)` handles nil.
- **`enum` with strings**: `vPublish = "publish"`, so `$verdict` prints `publish`. Enums are ordered, so `vPublish > vReview > vReject` is how "best verdict" is computed.
- **`nimble test`** compiles and runs every `tests/t*.nim`; `tests/config.nims` adds `src/` to the import path.

---

## Roadmap

| Stage | What | Status |
|---|---|---|
| 1 | Filter core + tests + eval against the corpus | ✅ done |
| 2 | Text gathering: async OCR (`tesseract` subprocess, fixes #30) and one small scraper per job board, with saved HTML/JSON fixtures as tests (fixes #34, report 4.1–4.5) | next |
| 3 | Listener: TDLib (Telegram's official library, via its C JSON interface), unread bootstrap using the real read marker, processing queue (fixes duplicates race), state + stats in SQLite (unblocks #10–#15), downtime alerts (#3, #16), systemd unit (#6), `telenotify export` for the corpus | after 2. **Needs you once:** a fresh Telegram login with the phone code |
| 4 | Run it next to the Python bot (Nim only logs what it *would* do), compare a week of verdicts, then switch, and move the Python files to `legacy/` | after 3 |
