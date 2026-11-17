<div align="center">

[🇮🇶 العربية](README.ar.md)

# tele-notify

**Watches Telegram job channels and forwards only the posts that fit you.**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Nim](https://img.shields.io/badge/Nim-2.2-yellow)](https://nim-lang.org)
[![CI](https://github.com/5wHN28Dg/tele-notify/actions/workflows/nim.yml/badge.svg)](https://github.com/5wHN28Dg/tele-notify/actions/workflows/nim.yml)

</div>

In Iraq, most jobs are posted on Telegram, in hundreds of channels and hundreds of posts a day. Finding the few entry-level IT, control and computer engineering jobs in Basra in that stream is a needle in a haystack. tele-notify reads the channels for you and forwards only the matches, to a channel of your own ([this one](https://t.me/BQTechJobs), for example). The story behind it: [*A Needle in a Haystack*](https://medium.com/@TogataMirio/a-needle-in-a-haystack-and-the-deadly-pursuit-to-prove-your-worth-88c34f1df79f).

It is built around that one need, but the keywords, places and channels are yours to set.

[![asciicast](https://asciinema.org/a/h8qYjhK6LsRqMchP6L93N1qu5.svg)](https://asciinema.org/a/h8qYjhK6LsRqMchP6L93N1qu5)

## Status: being rewritten in Nim

| | What it is | State |
|---|---|---|
| **v3, Python** (`main.py`) | The bot that runs today | Works, frozen: no new features |
| **v4, Nim** (`src/`) | The rewrite, built in stages | Stages 1–2 done: the filter, OCR and job-board reading. The Telegram part comes next |

The rewrite exists because the Python filter published far too many wrong posts: on a test with 196 real posts, 96 of its 115 publications were wrong, against 4 for the new filter. The [problems report](docs/problems-report.md) explains what was wrong; the [rewrite guide](docs/rewrite-guide.md) explains how the new code works.

## How the new filter decides

Every post gets one of three verdicts, **with the reasons**:

| Verdict | When |
|---|---|
| ✅ `publish` | Your role, in Basra (or remote), *and* a sign it's for beginners: "fresh graduate", "intern", "0–1 years", help desk… |
| 🤔 `review` | Role and place fit, but nothing says the level. You decide |
| ❌ `reject` | Not a job advert, wrong city, needs years of experience, senior title, mandatory certificate… |

Before deciding, it gathers everything there is to read:
- the post text;
- text in its pictures (OCR, Arabic and English);
- the job page when the post links to a known job board: Khana, Taeen, Yadanoo, LinkedIn, Shift.

Posts listing several jobs are split, and each job is judged on its own.

```
$ ./telenotify check --config:config.json post.txt
note: read Khana (2952 characters)
verdict: publish
  - role (rkTitle): مهندس كهرباء
  - location line: 'location: البصره - basra'
  - entry level: حديث التخرج
```

## Try the new filter

You need [Nim](https://nim-lang.org/install.html) 2.2 and tesseract with Arabic:

```bash
sudo apt install tesseract-ocr tesseract-ocr-ara tesseract-ocr-eng   # Ubuntu/Debian
git clone https://github.com/5wHN28Dg/tele-notify.git && cd tele-notify
nimble build -d:release
```

Then:

```bash
./telenotify check --config:config.json post.txt                  # one post
./telenotify check --config:config.json --image:ad.jpg post.txt   # with a picture
./telenotify check --config:config.json --show-text post.txt      # see everything it read
./telenotify eval --config:config.json posts.jsonl                # a labelled collection
```

`nimble test` runs the tests.

## Run the bot (Python, until v4 is finished)

1. Get Telegram API credentials: an API ID and API hash from [my.telegram.org](https://my.telegram.org) ([how](https://docs.telethon.dev/en/stable/basic/signing-in.html#signing-in)).
2. Install:
   ```bash
   python3 -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   ```
   You also need tesseract, as above. On Android, see the [wiki](https://github.com/5wHN28Dg/tele-notify/wiki/Android-Instructions).
3. Find your chat IDs by running this once, with your API ID and hash filled in:
   ```python
   from telethon import TelegramClient
   client = TelegramClient("test", API_ID, "API_HASH")
   async def main():
       async for d in client.iter_dialogs():
           print(f"{d.id:>15}: {d.title}")
   with client:
       client.loop.run_until_complete(main())
   ```
4. Fill in `config.json` (see below), then run `python main.py`. The first time, it asks for your phone number and the login code.

The bot first goes through the unread posts, then watches for new ones. A match is forwarded to `target_chat`, unless it repeats one of the last 10 forwarded posts. Unclear posts go to your Saved Messages for review.

`main_simple.py` is the original, simpler version: a post matches when it has one keyword from each of three lists. It suits any topic, not just jobs.

## Configuration

Both versions read the same `config.json`:

| Key | What |
|---|---|
| `api_id`, `api_hash` | Your Telegram API credentials. **Keep them private** |
| `chats` | IDs of the channels/groups to watch |
| `target_chat` | ID of the chat matches are forwarded to |
| `level_keywords_en/_ar` | Words that mean "for beginners": intern, حديث التخرج… |
| `entry_level_role_en/_ar` | Roles that are entry-level by nature: help desk, فني دعم… |
| `mid_level_general_role_en/_ar` | Your job titles and fields: electrical engineer, هندسة حاسبات… |
| `location_keywords_en/_ar` | Places that count: Basra and its districts, oil fields, "remote"… |
| `certifications` | Certificates that disqualify a post when they are *required* |
| `recent_messages` | Used by the Python bot to spot duplicates; leave it alone |

The Nim version adds optional keys, such as `publish_when_unsure` and `max_experience_years`. They are listed in the [rewrite guide](docs/rewrite-guide.md#5-changing-the-filter-without-breaking-it). My own keywords are in [`example_keywords.txt`](example_keywords.txt).

## More

- [Wiki](https://github.com/5wHN28Dg/tele-notify/wiki): FAQ, Android, the new filter, supported job boards
- [Issues](https://github.com/5wHN28Dg/tele-notify/issues): plans and known problems

**Disclaimer:** this is a personal tool, built for one person's job hunt. It isn't meant for high-volume use or anything that would approach Telegram's limits. Use your own account responsibly.

## License

[AGPL-3.0](LICENSE)
