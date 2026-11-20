"""python -m audit <job> --out DIR  (run in tui/; see audit/README.md)"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path

# The real home is read before anything else happens (guard.real_home uses the password database).
from . import appsrc, guard

REAL_HOME = guard.real_home()

JOBS = ("isolation", "screenshots", "visible", "keys", "timing", "colours", "hostile", "failures", "tasks", "styles",
        "leaks", "big-state", "interactive", "terminal")


def _list(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m audit", description="Isolated evidence for docs/UI-UX-POLICY.md")
    p.add_argument("job", choices=JOBS)
    p.add_argument("--out", type=Path, help="where the evidence goes (required except for interactive)")
    p.add_argument("--sizes", type=_list, default=["80x24", "120x40"], help="80x24,120x40")
    p.add_argument("--bidi", type=_list, default=None,
                   help="full,words (default both; interactive: the screen's own choice for this terminal)")
    p.add_argument("--themes", type=_list, default=["dark"], help="dark,light (light only for R6)")
    p.add_argument("--only", type=_list, default=None, help="screenshots: shot name prefixes; "
                   "failures: condition names; tasks: task names; terminal: scene names")
    p.add_argument("--runs", type=int, default=3, help="timing: runs per lock kind")
    p.add_argument("--locks", type=_list, default=["none", "write", "exclusive"], help="timing: none,write,exclusive")
    p.add_argument("--posts", type=int, default=None, help="timing / big-state: judged posts (default 1 000 000)")
    p.add_argument("--rebuild", action="store_true", help="big-state: build again even if the manifest matches")
    p.add_argument("--real", action="store_true", help="show the real bot's state instead of the dry run's")
    p.add_argument("--modes", type=_list, default=["dry-run", "real"],
                   help="screenshots: dry-run,real (the screen in each mode)")
    p.add_argument("--app", type=Path, default=None,
                   help="the tui/ folder of the checkout whose screen is audited (default: this one)")
    p.add_argument("--bin", choices=("fake", "repo"), default="fake",
                   help="telenotify: the fake (default) or the repository's build")
    p.add_argument("--big", action="store_true", help="interactive: use the large state file")
    p.add_argument("--size-check", action="store_true", help="interactive: say whether the terminal is at "
                   "least 80x24 and refuse to start if it is smaller")
    p.add_argument("--terminals", type=_list, default=["kitty", "ptyxis"],
                   help="terminal: kitty,ptyxis (each must be installed)")
    p.add_argument("--kitty-config", type=Path, default=None,
                   help="terminal: draw kitty with this kitty.conf instead of its defaults (font fidelity)")
    p.add_argument("--headless-check", action="store_true", help=argparse.SUPPRESS)   # tests only
    p.add_argument("--base", type=Path, default=None, help=argparse.SUPPRESS)   # tests only
    p.add_argument("--compare", type=Path, default=None,
                   help="timing: a second checkout's tui/ folder; its runs are interleaved with --app's")
    return p


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    from . import evidence
    from . import sandbox as sbx

    if a.job not in ("interactive", "terminal") and a.bidi is None:
        a.bidi = ["full", "words"]
    if a.job != "interactive" and a.out is None:
        print("--out DIR is required", file=sys.stderr)
        return 2
    out = a.out or (REAL_HOME / ".cache" / "agent-tmp" / "tn-audit" / "interactive")   # nothing is written there
    if a.kitty_config is not None:
        a.kitty_config = Path(os.path.abspath(a.kitty_config))
        why = guard.why_forbidden(a.kitty_config, REAL_HOME)
        if why:
            print(f"refusing to run: --kitty-config {why}", file=sys.stderr)
            return 2
        if not a.kitty_config.is_file():
            print(f"--kitty-config {a.kitty_config}: not a file", file=sys.stderr)
            return 2
    if a.job == "terminal":
        from . import terminal
        try:
            terminal.plan_shots(a.terminals, a.sizes, a.themes, a.modes, a.bidi, set(a.only) if a.only else None)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
    sb = sbx.plan(out, base=a.base or sbx.BASE, job=a.job, dry_run=not a.real, use_repo_bin=a.bin == "repo",
                  real_home=REAL_HOME)
    try:
        sbx.check(sb)
    except guard.Refused as e:
        print(e, file=sys.stderr)
        return 2
    if a.job == "leaks":
        clean = evidence.scan(sb.out)
        print("no leaks" if clean else f"LEAKS: see {sb.out / 'leaks.json'}")
        return 0 if clean else 3
    if a.job == "big-state":
        from . import data

        _path, man, built = data.big_state(sb.cache, posts=a.posts or data.BIG_POSTS, rebuild=a.rebuild)
        print(json.dumps({"built_now": built, **man}))
        return 0
    try:
        if a.job != "interactive":
            sbx.fresh_out(sb.out)
        sbx.create(sb)
    except (guard.Refused, OSError) as e:
        print(e, file=sys.stderr)
        return 2
    # a closed terminal or a kill still cleans up the run folder (finally below)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _exit_on_signal)
    try:
        if a.job == "interactive":
            from . import interactive

            appsrc.use(a.app)
            if a.headless_check:
                print(json.dumps(interactive.headless_check(sb, a.bidi[0] if a.bidi else None)))
                return 0
            return interactive.run(sb, a.bidi[0] if a.bidi else None, big=a.big, size_check=a.size_check)
        app_dir = appsrc.use(a.app)
        info = {"job": a.job, "argv": [str(x) for x in (argv if argv is not None else sys.argv[1:])],
                "app": appsrc.describe(app_dir), "machine": evidence.machine()}
        sb.out.mkdir(parents=True, exist_ok=True)
        sbx.prepare(sb)
        sbx.apply(sb)
        import telenotify_tui  # noqa: F401  (after apply; verify_loaded checks where it came from)
        appsrc.verify_loaded()
        result = run_job(a, sb)
        if sb.use_repo_bin:
            info["telenotify"] = "the repository's build, run with --no-scrape"
        evidence.write_json(sb.out / f"run-{a.job}.json", {**info, "result": result})
        print(json.dumps(result, ensure_ascii=False))
    finally:
        sbx.remove(sb)
    clean = evidence.scan(sb.out)
    if not clean:
        print(f"LEAKS: see {sb.out / 'leaks.json'}", file=sys.stderr)
        return 3
    if isinstance(result, dict) and result.get("errors"):
        print(f"{result['errors']} state(s) couldn't be measured: see the summary", file=sys.stderr)
        return 4
    return 0


def _exit_on_signal(signum, _frame) -> None:
    raise SystemExit(128 + signum)


def run_job(a: argparse.Namespace, sb) -> dict:
    """The job itself; the sandbox's environment is already in place."""
    if a.job == "isolation":
        from . import isolation
        return isolation.collect(sb)
    if a.job == "screenshots":
        from . import shots
        return shots.screenshots(sb, a.sizes, a.bidi, a.themes, set(a.only) if a.only else None, a.modes)
    if a.job == "visible":
        from . import visible
        return visible.collect(sb, a.sizes, a.modes)
    if a.job == "colours":
        from . import shots
        return shots.colours(sb, a.themes)
    if a.job == "keys":
        from . import keys
        return keys.collect(sb, a.sizes)
    if a.job == "failures":
        from . import failures
        return failures.collect(sb, a.only, "120x40" if "120x40" in a.sizes else a.sizes[0])
    if a.job == "hostile":
        from . import hostile
        return hostile.collect(sb, a.bidi)
    if a.job == "timing":
        from . import data, timing
        return timing.collect(sb, a.runs, a.locks, a.posts or data.BIG_POSTS, compare=a.compare)
    if a.job == "tasks":
        from . import tasks
        return tasks.collect(sb, a.only)
    if a.job == "styles":
        from . import styles
        return styles.collect(sb)
    if a.job == "terminal":
        from . import terminal
        return terminal.collect(sb, a.terminals, a.sizes, a.themes, a.modes, a.bidi,
                                set(a.only) if a.only else None, kitty_config=a.kitty_config)
    raise SystemExit(f"unknown job {a.job}")


if __name__ == "__main__":
    sys.exit(main())
