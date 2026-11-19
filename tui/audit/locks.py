"""Holding a lock on a state file from another process, the way the bot would.

* "write": `BEGIN IMMEDIATE` (the bot's write transaction; in WAL mode
  readers go on reading);
* "exclusive": `PRAGMA locking_mode=EXCLUSIVE` and a write transaction, so
  other connections can't even read ("database is locked").

The helper only takes the lock and rolls back: the file is never changed.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

HELPER = r"""
import sqlite3, sys
path, kind = sys.argv[1], sys.argv[2]
db = sqlite3.connect(path, isolation_level=None, timeout=30)
if kind == "exclusive":
    db.execute("PRAGMA locking_mode=EXCLUSIVE")
    db.execute("BEGIN EXCLUSIVE")
    db.execute("SELECT count(*) FROM sqlite_master").fetchall()
else:
    db.execute("BEGIN IMMEDIATE")
print("locked", flush=True)
sys.stdin.read()
db.execute("ROLLBACK")
db.close()
"""


@contextmanager
def hold(path: Path, kind: str) -> Iterator[None]:
    if kind not in ("write", "exclusive"):
        raise ValueError(kind)
    proc = subprocess.Popen([sys.executable, "-I", "-c", HELPER, str(path), kind], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    try:
        line = proc.stdout.readline() if proc.stdout else ""
        if line.strip() != "locked":
            raise RuntimeError(f"couldn't lock {path.name} ({kind})")
        yield
    finally:
        if proc.stdin:
            proc.stdin.close()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
