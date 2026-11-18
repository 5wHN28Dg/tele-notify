"""Data helpers for the Review screen: decision history and corpus labels.

Every send / discard made on the Review screen is also a label for the
test corpus: "TRUE" (this should reach the channel) or "FALSE" (it
shouldn't). The labels go to one JSON line per post, in the same shape as
the eval corpus ({"id", "text", "label"}), so `telenotify eval` can run on
the file directly.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from pathlib import Path

from ..store import Review, Store

DECIDED = ("to_send", "sent", "discarded")
LABELS = {"to_send": "TRUE", "discarded": "FALSE"}


def labels_path() -> Path:
    """$TELENOTIFY_LABELS, or review-labels.jsonl in the private corpus folder."""
    env = os.environ.get("TELENOTIFY_LABELS")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".local" / "share" / "tele-notify-corpus" / "review-labels.jsonl"


def label_id(review: Review) -> str:
    return f"{review.chat_id}:{review.msg_id}"


def _line_id(line: bytes) -> str | None:
    try:
        obj = json.loads(line)   # a line that isn't UTF-8 / JSON has no id and is kept as is
    except ValueError:
        return None
    return str(obj.get("id")) if isinstance(obj, dict) and "id" in obj else None


def write_label(review: Review, decision: str, path: Path | None = None,
                now: int | None = None) -> Path:
    """Record `decision` ("to_send" / "discarded") for `review` as a corpus label.

    A post already in the file gets its line replaced (the later decision
    wins); every other line is kept byte for byte, even one that isn't valid
    UTF-8. The folder is created private (0700) if missing; the file is
    written whole and swapped in, so a crash never leaves half a file. A lock
    on a sidecar file (<name>.lock) keeps two open screens from losing each
    other's labels.
    """
    path = path or labels_path()
    entry = {"id": label_id(review), "text": review.text, "label": LABELS[decision],
             "source": "review", "at": int(time.time()) if now is None else now}
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_fd = os.open(path.with_name(path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        lines: list[bytes] = []
        if path.exists():
            lines = [l for l in path.read_bytes().splitlines()
                     if l.strip() and _line_id(l) != entry["id"]]
        lines.append(json.dumps(entry, ensure_ascii=False).encode("utf-8"))
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".review-labels.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(b"\n".join(lines) + b"\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    finally:
        os.close(lock_fd)   # releases the lock
    return path


def history(store: Store, limit: int = 50) -> list[Review]:
    """Recently decided reviews (approved, sent, discarded), newest decision first."""
    rows = [r for status in DECIDED for r in store.reviews(status)]
    rows.sort(key=lambda r: (r.decided_at or r.at, r.id), reverse=True)
    return rows[:limit]
