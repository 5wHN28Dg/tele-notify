# The old Python bot (v3)

This is the Python version of tele-notify that ran until the Nim rewrite replaced it. It's kept for reference and gets no fixes; use the Nim version in the repository root.

| File | What |
|---|---|
| `main.py` | The v3 bot (Telethon) |
| `main_simple.py` | The original version: a post matches when it has one keyword from each of three lists. Suits any topic |
| `get_chat_ids.py` | Prints your chats and their IDs (`telenotify login` does this now) |
| `crash_alert.py` | Crash notifications (the Nim bot's downtime alerts replace it) |
| `tools/` | One-off helpers |
| `config_simple.json` | Example config for `main_simple.py` |

To run it anyway, from this folder:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python main.py          # reads config.json from the current folder
```

Why it was replaced: [docs/problems-report.md](../docs/problems-report.md).
