"""Talking to the bot: its systemd service and its `telenotify` program.

Anything that needs the filter (explain a post, re-judge it, test settings
on the corpus) runs the real `telenotify` binary, so the screen and the
bot can never disagree about a verdict.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .clean import clean

SERVICES = {"real": "tele-notify", "dry-run": "tele-notify-dryrun"}


@dataclass
class Verdict:
    verdict: str
    reasons: list[str] = field(default_factory=list)
    text: str = ""          # what the filter read (with --show-text)
    error: str = ""


@dataclass
class EvalResult:
    rows: dict[str, tuple[int, int, int]] = field(default_factory=dict)  # label -> publish, review, reject
    summary: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def wrong_published(self) -> int:
        return self.rows.get("FALSE", (0, 0, 0))[0]

    @property
    def true_lost(self) -> int:
        return self.rows.get("TRUE", (0, 0, 0))[2]


async def run(*args: str, timeout: float = 120) -> tuple[int, str, str]:
    """Run a program; (exit code, stdout, stderr). Never raises for a failing program.
    The output comes back without control characters (clean.clean): it is shown on screen
    and can carry text strangers wrote (a post, a scraped job page)."""
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
    except OSError as e:
        return 127, "", clean(e)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        _kill(proc)
        await proc.wait()
        return 124, "", f"timed out after {timeout:.0f} s"
    except BaseException:
        # cancelled (the screen closed, a newer run replaced this one): don't leave the child running
        _kill(proc)
        await asyncio.shield(proc.wait())
        raise
    return proc.returncode or 0, clean(out.decode(errors="replace")), clean(err.decode(errors="replace"))


def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:   # it ended on its own just now
            pass


# ------------------------------------------------------------- service

async def service_state(mode: str) -> str:
    """'active', 'inactive', 'failed', ... for the real or dry-run service."""
    code, out, _ = await run("systemctl", "--user", "is-active", SERVICES[mode], timeout=10)
    return out.strip() or ("unknown" if code else "active")


async def service_action(mode: str, action: str) -> tuple[bool, str]:
    """start / stop / restart / try-restart (restart only if running) a service. (ok, message)."""
    if action not in ("start", "stop", "restart", "try-restart"):
        raise ValueError(action)
    code, out, err = await run("systemctl", "--user", action, SERVICES[mode], timeout=60)
    return code == 0, (err or out).strip()


# -------------------------------------------------------------- filter

_VERDICT = re.compile(r"^verdict:\s*(\w+)\s*$")
_TEXT_START, _TEXT_END = "----- text the filter reads -----", "---------------------------------"


def parse_check(out: str) -> Verdict:
    """Parse `telenotify check` output: a 'verdict: X' line followed by '  - reason' lines.

    With --show-text the output first prints the text the filter read between
    _TEXT_START and _TEXT_END, and strangers write that text: it may hold its
    own "verdict: publish" and "  - " lines, or a dashed line. So the real
    verdict is the LAST verdict line, its reasons are the "  - " lines after
    it, and the shown text runs from the first opening marker to the last
    closing marker before that verdict line."""
    lines = out.splitlines()
    at = next((i for i in range(len(lines) - 1, -1, -1) if _VERDICT.match(lines[i])), None)
    if at is None:
        return Verdict("", error=out.strip() or "no verdict in the output")
    verdict = _VERDICT.match(lines[at]).group(1)  # type: ignore[union-attr]
    reasons = [line[4:] for line in lines[at + 1:] if line.startswith("  - ")]
    text = ""
    head = lines[:at]
    if _TEXT_START in head:
        start = head.index(_TEXT_START)
        end = max((i for i in range(start + 1, at) if head[i] == _TEXT_END), default=None)
        if end is not None:
            text = "\n".join(head[start + 1:end]).strip("\n")
    return Verdict(verdict, reasons, text)


async def check(binary: Path, config: Path, text: str, scrape: bool = False,
                show_text: bool = False) -> Verdict:
    """Judge `text` with the real filter. Without `scrape`, links aren't followed (fast, offline)."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
        post = f.name
    try:
        args = [str(binary), "check", f"--config:{config}"]
        if not scrape:
            args.append("--no-scrape")
        if show_text:
            args.append("--show-text")
        _code, out, err = await run(*args, post, timeout=90)
    finally:
        Path(post).unlink(missing_ok=True)
    v = parse_check(out)
    if not v.verdict and err:
        v.error = err.strip()
    return v


_EVAL_ROW = re.compile(r"^\s*(TRUE|FALSE|UNSURE)\s+publish:\s*(\d+)\s+review:\s*(\d+)\s+reject:\s*(\d+)", re.MULTILINE)


def parse_eval(out: str) -> EvalResult:
    r = EvalResult()
    for m in _EVAL_ROW.finditer(out):
        r.rows[m.group(1)] = (int(m.group(2)), int(m.group(3)), int(m.group(4)))
    r.summary = [l for l in out.splitlines() if l.startswith(("published:", "true matches"))]
    if not r.rows:
        r.error = out.strip() or "no results"
    return r


async def evaluate(binary: Path, config: Path, corpus: Path) -> EvalResult:
    _code, out, err = await run(str(binary), "eval", f"--config:{config}", str(corpus), timeout=300)
    r = parse_eval(out)
    if not r.rows and err:
        r.error = err.strip()
    return r


def default_corpus() -> Path:
    return Path.home() / ".local" / "share" / "tele-notify-corpus" / "eval.jsonl"
