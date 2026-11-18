"""The settings screen's data side: load, validate, merge and save config.json.

Plain functions with no Textual in them, so they can be tested on their own.

The screen keeps two dicts: `original` (config.json as it was loaded) and
`edited` (a deep copy the widgets change). Saving never writes `edited`
as is: `apply_edits` replays only the keys the user changed on top of the
file as it is on disk at that moment, so every key this screen doesn't
know about, the secrets (api_id / api_hash) and the key order stay exactly
as they were.

Every key and default here mirrors src/telenotify/config.nim.
"""

from __future__ import annotations

import copy
import json
import math
import os
import shutil
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DUPLICATE_FIX = " (press D or \"Remove duplicates\" to keep one of each)"

MISSING: Any = object()   # "key not in the file" (the bot then uses its default)

# Keyword lists: key -> what it means (shown under the list).
LIST_KEYS: dict[str, str] = {
    "level_keywords_en": "Words that mean \"for beginners\": intern, fresh graduate...",
    "level_keywords_ar": "Words that mean \"for beginners\" (Arabic): متدرب، حديث التخرج...",
    "entry_level_role_en": "Roles that are entry-level by nature: help desk...",
    "entry_level_role_ar": "Roles that are entry-level by nature (Arabic): فني دعم...",
    "mid_level_general_role_en": "Your job titles and fields: electrical engineer...",
    "mid_level_general_role_ar": "Your job titles and fields (Arabic): مهندس كهرباء...",
    "field_keywords_en": "Role keywords that name a degree or field, not a job title",
    "field_keywords_ar": "Role keywords that name a degree or field (Arabic): علوم حاسبات...",
    "location_keywords_en": "Places that count: Basra and its districts, oil fields, remote...",
    "location_keywords_ar": "Places that count (Arabic): البصرة...",
    "extra_other_places": "More places that are NOT yours (added to the built-in list)",
    "extra_location_markers": "More words that introduce a place (\"location:\", \"في\"...)",
    "certifications": "Certificates that disqualify a post when they are required",
    "extra_seniority": "More words that mean senior (added to the built-in list)",
    "extra_role_exclusions": "More roles that never count, even if a keyword matches",
    "extra_job_signals": "More words that show a post is a job advert",
    "extra_not_a_job": "More words that show a post is not a job (courses, ads...)",
    "extra_job_sites": "More career sites whose job pages carry a schema.org JobPosting",
}
INT_KEYS: dict[str, int] = {"max_experience_years": 1, "alert_after_minutes": 10}
BOOL_KEYS: dict[str, bool] = {"publish_when_unsure": False, "dry_run": False, "alerts": True}
STR_KEYS: dict[str, str] = {"data_dir": "", "tdlib_path": ""}
CHAT_KEYS = ("chats", "target_chat")
SECRET_KEYS = ("api_id", "api_hash")

ROLE_KEYS = ("entry_level_role_en", "entry_level_role_ar", "mid_level_general_role_en",
             "mid_level_general_role_ar", "field_keywords_en", "field_keywords_ar")
LOCATION_KEYS = ("location_keywords_en", "location_keywords_ar")

SECTIONS: list[tuple[str, str, list[str]]] = [
    ("roles", "Roles & levels", ["level_keywords_en", "level_keywords_ar", *ROLE_KEYS]),
    ("places", "Places", [*LOCATION_KEYS, "extra_other_places", "extra_location_markers"]),
    ("filters", "Other filters", ["certifications", "extra_seniority", "extra_role_exclusions",
                                  "extra_job_signals", "extra_not_a_job", "extra_job_sites",
                                  "max_experience_years", "publish_when_unsure"]),
    ("chats", "Chats", list(CHAT_KEYS)),
    ("bot", "Bot", ["dry_run", "alerts", "alert_after_minutes", "data_dir", "tdlib_path"]),
]

DEFAULTS: dict[str, Any] = {**{k: [] for k in LIST_KEYS}, **INT_KEYS, **BOOL_KEYS, **STR_KEYS,
                            "chats": [], "target_chat": 0}
KNOWN_KEYS = tuple(DEFAULTS)   # the keys this screen edits (secrets are never edited)


class ConfigError(Exception):
    pass


@dataclass
class Problem:
    key: str        # config key ("" for the whole file)
    message: str
    error: bool     # True blocks saving; False is only a warning


# ----------------------------------------------------------------- loading

def load(path: Path) -> dict:
    """config.json as a dict (key order kept). Raises ConfigError with a readable message."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e.strerror or e}") from e
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ConfigError(f"{path} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must hold a JSON object")
    return data


def secret_status(cfg: dict) -> dict[str, str]:
    """'set' or 'missing' for api_id / api_hash. The values themselves are never shown."""
    out = {}
    for k in SECRET_KEYS:
        v = cfg.get(k)
        ok = bool(v) and not isinstance(v, bool) and (k != "api_id" or isinstance(v, int))
        out[k] = "set" if ok else "missing"
    return out


def value(cfg: dict, key: str) -> Any:
    """The value the bot would use: the file's, or the default."""
    return cfg.get(key, copy.deepcopy(DEFAULTS[key]))


def parse_int(text: str) -> int | str:
    """A whole number from an input box, or the text unchanged (validation reports it)."""
    t = text.strip()
    try:
        return int(t)
    except ValueError:
        return text


# -------------------------------------------------------------- validation

def norm_keyword(k: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", k).casefold().split())


def duplicates(items: list) -> list[str]:
    """Items that appear more than once (ignoring case and spacing), first spelling of each."""
    seen: dict[str, str] = {}
    dups: list[str] = []
    for x in items:
        if not isinstance(x, str):
            continue
        n = norm_keyword(x)
        if n in seen and seen[n] not in dups:
            dups.append(seen[n])
        seen.setdefault(n, x)
    return dups


def _dedupe_key(x: Any) -> Any:
    """What counts as "the same" entry: words ignoring case and spacing, other values as they are."""
    if isinstance(x, str):
        return ("str", norm_keyword(x))
    if isinstance(x, bool):
        return ("bool", x)
    return (type(x).__name__, x) if isinstance(x, (int, float)) else None


def dedupe(items: list) -> tuple[list, list]:
    """(the list with each entry once, keeping its first spelling and the
    order; the entries taken out). Entries that can't be compared stay."""
    seen: set = set()
    kept: list = []
    removed: list = []
    for x in items:
        k = _dedupe_key(x)
        if k is not None and k in seen:
            removed.append(x)
            continue
        if k is not None:
            seen.add(k)
        kept.append(x)
    return kept, removed


DEDUPE_KEYS = (*LIST_KEYS, "chats")


def dedupe_all(cfg: dict) -> dict[str, list]:
    """Take repeated entries out of every list in `cfg` (in place): the first
    spelling of each stays. Returns {key: the entries taken out}."""
    out: dict[str, list] = {}
    for key in DEDUPE_KEYS:
        v = cfg.get(key)
        if isinstance(v, list):
            kept, removed = dedupe(v)
            if removed:
                cfg[key] = kept
                out[key] = removed
    return out


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1


def _is_int64(v: Any) -> bool:
    """A whole number the bot can hold (Nim's int / int64): chat IDs and counts."""
    return _is_int(v) and INT64_MIN <= v <= INT64_MAX


def _non_finite(v: Any) -> bool:
    """NaN or Infinity anywhere in `v` (json.loads reads NaN, Infinity and 1e999)."""
    if isinstance(v, float):
        return not math.isfinite(v)
    if isinstance(v, dict):
        return any(_non_finite(x) for x in v.values())
    if isinstance(v, list):
        return any(_non_finite(x) for x in v)
    return False


def non_finite_keys(cfg: dict) -> list[str]:
    """Top-level keys holding NaN / Infinity: standard JSON can't store them, so
    the file can't be saved while they are there."""
    return [k for k, v in cfg.items() if _non_finite(v)]


def validate(cfg: dict) -> list[Problem]:
    """Everything wrong with `cfg`. Errors block saving; warnings don't."""
    p: list[Problem] = []
    for key in LIST_KEYS:
        v = cfg.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            p.append(Problem(key, "must be a list of words (text)", True))
            continue
        if any(not x.strip() for x in v):
            p.append(Problem(key, "has an empty entry", False))
        dups = duplicates(v)
        if dups:
            p.append(Problem(key, "listed twice: " + ", ".join(f'"{d}"' for d in dups)
                             + DUPLICATE_FIX, False))

    def has_words(keys: tuple[str, ...]) -> bool:
        return any(isinstance(cfg.get(k), list) and any(isinstance(x, str) and x.strip() for x in cfg[k])
                   for k in keys)

    if not has_words(ROLE_KEYS):
        for k in ROLE_KEYS:
            p.append(Problem(k, "no role keywords at all: add some entry-level roles, job titles or fields", True))
    if not has_words(LOCATION_KEYS):
        for k in LOCATION_KEYS:
            p.append(Problem(k, "no location keywords at all: add the places that count", True))

    for key in INT_KEYS:
        v = cfg.get(key, MISSING)
        if v is not MISSING and (not _is_int64(v) or v < 0):
            p.append(Problem(key, "must be a whole number, 0 or more", True))
    for key in BOOL_KEYS:
        v = cfg.get(key, MISSING)
        if v is not MISSING and not isinstance(v, bool):
            p.append(Problem(key, "must be true or false", True))
    for key in STR_KEYS:
        v = cfg.get(key, MISSING)
        if v is not MISSING and not isinstance(v, str):
            p.append(Problem(key, "must be text", True))
    tl = cfg.get("tdlib_path")
    if isinstance(tl, str) and tl and not Path(tl).expanduser().exists():
        p.append(Problem("tdlib_path", f"{tl} doesn't exist", False))

    chats = cfg.get("chats", [])
    if not isinstance(chats, list):
        p.append(Problem("chats", "must be a list of chat IDs", True))
    else:
        bad = [str(c) for c in chats if not _is_int64(c)]
        if bad:
            p.append(Problem("chats", "not chat IDs (whole numbers): " + ", ".join(bad), True))
        elif len(set(chats)) != len(chats):
            p.append(Problem("chats", "a chat is listed twice" + DUPLICATE_FIX, False))
        if not chats:
            p.append(Problem("chats", "no chats are watched: the bot will read nothing", False))
    t = cfg.get("target_chat", MISSING)
    if t is not MISSING and not _is_int64(t):
        p.append(Problem("target_chat", "must be a chat ID (a whole number)", True))
    elif t in (MISSING, 0):
        p.append(Problem("target_chat", "no target chat: matches have nowhere to go", False))
    elif isinstance(chats, list) and t in chats:
        p.append(Problem("target_chat", "the target chat is also watched", False))
    for key in non_finite_keys(cfg):
        p.append(Problem(key, "holds NaN or Infinity (a number too big, like 1e999?), which can't be "
                              "saved as JSON: fix it in config.json by hand", True))
    return p


def errors(problems: list[Problem]) -> list[Problem]:
    return [x for x in problems if x.error]


# ----------------------------------------------------------- diff and merge

def changed_keys(original: dict, edited: dict) -> list[str]:
    """Known keys whose value differs. Setting an absent key to its default is no change."""
    return [k for k in KNOWN_KEYS if _key_changed(k, original, edited)]


def _key_changed(k: str, original: dict, edited: dict) -> bool:
    a, b = original.get(k, MISSING), edited.get(k, MISSING)
    if a is MISSING and b is MISSING:
        return False
    if a is MISSING and b == DEFAULTS[k] or b is MISSING and a == DEFAULTS[k]:
        return False
    return a != b or type(a) is not type(b)


def is_dirty(original: dict, edited: dict) -> bool:
    return bool(changed_keys(original, edited))


def apply_edits(base: dict, original: dict, edited: dict) -> tuple[dict, list[str]]:
    """Replay the user's edits (`original` -> `edited`) on top of `base`, the
    file as it is now. Returns (new config, conflicts): keys that changed
    both on disk and in the screen to different values (the screen's value
    wins in the result). The same change made in both places is no conflict.

    Unknown keys, secrets and the order of existing keys come from `base`;
    new keys go at the end."""
    out = dict(base)          # keeps base's key order
    conflicts = []
    for k in changed_keys(original, edited):
        if _key_changed(k, original, base) and _key_changed(k, base, edited):
            conflicts.append(k)
        new = edited.get(k, MISSING)
        if new is MISSING:
            out.pop(k, None)
        else:
            out[k] = copy.deepcopy(new)
    return out, conflicts


def take_disk(base: dict, original: dict, edited: dict, keys: list[str]) -> dict:
    """The way out of a save conflict: the edits replayed on `base` (the file
    as it is now), except that `keys` take the file's value. The result is
    the new edited copy; use `base` as the new original."""
    out, _ = apply_edits(base, original, edited)
    for k in keys:
        if k in base:
            out[k] = copy.deepcopy(base[k])
        else:
            out.pop(k, None)
    return out


# ------------------------------------------------------------------ saving

def dumps(cfg: dict) -> str:
    """config.json's text. Raises ValueError for NaN / Infinity (non_finite_keys):
    Python would write them, but they aren't JSON and the bot couldn't read the file."""
    return json.dumps(cfg, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def backup(path: Path, now: float | None = None) -> Path:
    """Copy config.json to config.json.bak-YYYYmmdd-HHMMSS (permissions too)."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now if now is not None else time.time()))
    dest = path.with_name(f"{path.name}.bak-{stamp}")
    n = 1
    while dest.exists():                      # two saves in the same second
        dest = path.with_name(f"{path.name}.bak-{stamp}-{n}")
        n += 1
    shutil.copy2(path, dest)
    return dest


def write_atomic(path: Path, text: str) -> None:
    """Write via a temp file in the same folder + rename, so the bot never reads a half file.
    Keeps the old file's permission bits (config.json holds secrets)."""
    mode = path.stat().st_mode & 0o7777 if path.exists() else 0o600
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save(path: Path, cfg: dict, now: float | None = None) -> Path | None:
    """Back up the current file, then write `cfg` atomically. Returns the backup's path.
    Raises ValueError (before touching anything) when `cfg` holds NaN / Infinity."""
    path = Path(path)
    text = dumps(cfg)
    bak = backup(path, now) if path.exists() else None
    write_atomic(path, text)
    return bak


def write_temp(cfg: dict) -> Path:
    """`cfg` in a private temp file for the corpus test; the caller deletes it.
    The secrets (api_id / api_hash) are left out: judging posts doesn't need them."""
    clean = {k: v for k, v in cfg.items() if k not in SECRET_KEYS}
    fd, tmp = tempfile.mkstemp(prefix="telenotify-config-", suffix=".json")   # mode 0600
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(dumps(clean))
    return Path(tmp)


def corpus_path() -> Path:
    from .. import bot
    env = os.environ.get("TELENOTIFY_CORPUS")
    return Path(env).expanduser() if env else bot.default_corpus()


# ------------------------------------------------------------- corpus test

LABELS = ("TRUE", "UNSURE", "FALSE")
VERDICTS = ("publish", "review", "reject")


@dataclass
class EvalCell:
    label: str
    verdict: str
    before: int
    after: int
    mood: str       # "better", "worse" or ""


def _mood(label: str, verdict: str, delta: int) -> str:
    if delta == 0 or verdict == "review" or label == "UNSURE":
        return ""
    good_up = (label, verdict) in (("TRUE", "publish"), ("FALSE", "reject"))
    return "better" if (delta > 0) == good_up else "worse"


def compare_eval(before, after) -> list[list[EvalCell]]:
    """Rows TRUE / UNSURE / FALSE of cells publish / review / reject, before -> after.
    Wrong publishes (FALSE -> publish) going up or true matches lost (TRUE -> reject)
    going up are "worse"."""
    rows = []
    for label in LABELS:
        b = before.rows.get(label, (0, 0, 0))
        a = after.rows.get(label, (0, 0, 0))
        rows.append([EvalCell(label, v, b[i], a[i], _mood(label, v, a[i] - b[i]))
                     for i, v in enumerate(VERDICTS)])
    return rows


# ------------------------------------------------------------------- chats

@dataclass
class ChatRow:
    id: int
    title: str
    kind: str
    status: str
    known: bool      # the bot's chat list has it
    watched: bool    # in config `chats`
    target: bool     # config `target_chat`


def chat_rows(store_chats: list, cfg: dict) -> list[ChatRow]:
    """The account's chats (from the bot's state) plus any chat in the config
    that the state doesn't know ("unknown chat <id>"). Watched/target come
    from the config being edited, not from the state."""
    watched = cfg.get("chats", [])
    watched = watched if isinstance(watched, list) else []
    target = cfg.get("target_chat", 0)
    rows = [ChatRow(c.id, c.title, c.kind, c.status, True, c.id in watched, c.id == target)
            for c in store_chats]
    known = {c.id for c in store_chats}
    extra = [c for c in watched if _is_int(c) and c not in known]
    if _is_int(target) and target and target not in known and target not in extra:
        extra.append(target)
    for cid in dict.fromkeys(extra):
        rows.append(ChatRow(cid, f"unknown chat {cid}", "", "unknown", False, cid in watched, cid == target))
    return rows


def toggle_watched(cfg: dict, chat_id: int) -> bool:
    """Add or remove `chat_id` in cfg['chats'] (order kept). Returns the new state."""
    chats = cfg.get("chats")
    chats = list(chats) if isinstance(chats, list) else []
    if chat_id in chats:
        chats = [c for c in chats if c != chat_id]
        now = False
    else:
        chats.append(chat_id)
        now = True
    cfg["chats"] = chats
    return now


def set_target(cfg: dict, chat_id: int) -> None:
    cfg["target_chat"] = chat_id
