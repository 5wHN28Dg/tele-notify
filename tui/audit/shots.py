"""Screenshots (every tab, view and dialog) and colours (R6), in-process and headless."""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from pathlib import Path

from textual.widgets import DataTable, Input, TabbedContent

from telenotify_tui.screens.review import ReviewPane

from . import drive, evidence, fakes, svg
from . import sandbox as sbx
from .sandbox import Sandbox

QUERY = "مهندس"
Step = tuple[str | None, Callable[..., Awaitable[None]]]


async def _press(app, pilot, *keys: str) -> None:
    await pilot.press(*keys)
    await drive.settle(app, pilot)


async def _click(app, pilot, selector: str) -> None:
    await pilot.click(selector)
    await drive.settle(app, pilot)


async def _type(app, pilot, selector: str, text: str) -> None:
    app.query_one(selector).focus()
    await pilot.pause()
    await pilot.press(*list(text))
    await drive.settle(app, pilot)


async def _section(app, pilot, sec: str) -> None:
    app.set_focus(None)   # a focused widget in the old section would switch straight back
    app.query_one("#sections", TabbedContent).active = f"sec-{sec}"
    await drive.settle(app, pilot)


def steps() -> list[Step]:
    """(shot name or None, what to do first). Each step starts where the last one left off."""
    def p(*keys):
        return lambda app, pilot: _press(app, pilot, *keys)

    async def nothing(app, pilot):
        await drive.settle(app, pilot)

    async def review(app, pilot):
        await _press(app, pilot, "2")
        await asyncio.sleep(ReviewPane.DECIDE_DELAY)   # the real delay stays in force

    async def search(app, pilot):
        await _press(app, pilot, "3")
        await _type(app, pilot, "#q", QUERY)
        await _press(app, pilot, "enter")

    async def search_row(app, pilot):
        app.query_one("#results", DataTable).focus()
        await _press(app, pilot, "down")

    async def add_keyword(app, pilot):
        await _section(app, pilot, "roles")
        await _type(app, pilot, "#editor-roles #kw-entry", "audit word")
        await _press(app, pilot, "enter")
        app.query_one("#editor-roles #kw-items").focus()
        await drive.settle(app, pilot)

    async def paste(app, pilot):
        app.query_one("#results").focus()       # p would type into the search box
        await _press(app, pilot, "p")

    async def keys_panel(app, pilot):
        app.action_show_help_panel()
        await drive.settle(app, pilot)

    async def hide_keys(app, pilot):
        app.action_hide_help_panel()
        await drive.settle(app, pilot)

    async def error_toast(app, pilot):
        await _section(app, pilot, "chats")
        await _type(app, pilot, "#chat-add", "not-a-number")
        await _press(app, pilot, "enter")

    out: list[Step] = [
        ("1-dashboard-feed", nothing),
        ("1-dashboard-chats", p("c")),
        ("1-dashboard-alerts", p("a")),
        (None, p("f")),
        ("1-dashboard-period-open", p("p")),
        (None, p("escape")),
        ("1-dashboard-dates-dialog", p("d")),
        (None, p("escape")),
        ("1-dashboard-stop-confirm", lambda app, pilot: _click(app, pilot, "#svc-stop")),
        (None, p("escape")),
        ("2-review", review),
        ("2-review-filter-read", p("r")),
        (None, p("escape")),
        ("2-review-history", p("h")),
        (None, p("h")),
        ("3-search-results", search),
        ("3-search-selected", search_row),
        ("3-search-chat-dropdown", lambda app, pilot: _click(app, pilot, "#chat")),
        (None, p("escape")),
        ("3-search-paste", paste),
        (None, p("escape")),
        ("4-settings-roles", p("4")),
    ]
    out += [(f"4-settings-{sec}", functools.partial(_section, sec=sec)) for sec in ("places", "filters", "chats", "bot")]
    out += [
        ("4-settings-unsaved", add_keyword),
        ("4-settings-revert-confirm", p("ctrl+r")),
        (None, p("n")),
        ("4-settings-corpus-test", p("t")),
        (None, p("escape")),
        ("quit-confirm", p("ctrl+q")),
        (None, p("n")),
        ("keys-panel", keys_panel),
        (None, hide_keys),
        ("command-palette", p("ctrl+p")),
        (None, p("escape")),
        ("toast-error", error_toast),
    ]
    return out


async def run_steps(app, pilot, out: Path, only: set[str] | None = None) -> list[dict]:
    rows = []
    for name, act in steps():
        try:
            await act(app, pilot)
            if name and (not only or any(name.startswith(o) for o in only)):
                rows.append(await drive.shot(app, pilot, out, name))
        except Exception as e:  # noqa: BLE001 - evidence: a failing step is recorded, the rest still run
            rows.append({"name": name, "error": drive.error_text(e)})
    return rows


async def one_run(sb: Sandbox, out: Path, size: tuple[int, int], theme: str, only: set[str] | None) -> list[dict]:
    sbx.prepare(sb)
    app = drive.make_app(sb)
    async with drive.run_test(app, size) as pilot:
        app.theme = drive.THEMES[theme]
        await drive.settle(app, pilot)
        rows = await run_steps(app, pilot, out, only)
    return rows


def screenshots(sb: Sandbox, sizes: list[str], bidis: list[str], themes: list[str],
                only: set[str] | None = None, modes: tuple[str, ...] | list[str] = ("dry-run",)) -> dict:
    """Every shot per mode (dry run / real), theme, bidi mode and size."""
    index = []
    keep = sb.dry_run
    try:
        for mode in modes:
            sb.dry_run = mode != "real"
            for theme in themes:
                for bidi in bidis:
                    sbx.set_bidi(bidi)
                    for size in sizes:
                        where = sb.out / "screenshots" / mode / theme / f"bidi-{bidi}" / size
                        rows = asyncio.run(one_run(sb, where, drive.SIZES[size], theme, only))
                        index.append({"mode": mode, "theme": theme, "bidi": bidi, "size": size,
                                      "folder": str(where.relative_to(sb.out)), "shots": rows})
    finally:
        sb.dry_run = keep
    evidence.write_json(sb.out / "screenshots" / "index.json", index)
    return {"runs": len(index), "errors": sum(1 for r in index for s in r["shots"] if "error" in s)}


# ------------------------------------------------------------------ colours

def _row_region(table: DataTable, row: int) -> tuple[int, int, int, int]:
    r = table.content_region
    y = r.y + (1 if table.show_header else 0) + row - int(table.scroll_y)
    return (r.x, y, r.width, 1)


def _reg(w) -> tuple[int, int, int, int]:
    r = w.region
    return (r.x, r.y, r.width, r.height)


async def colours_run(sb: Sandbox, theme: str, out: Path) -> list[dict]:
    sbx.prepare(sb, services=fakes.services(dry_run="failed"))
    app = drive.make_app(sb)
    rows: list[dict] = []
    async with drive.run_test(app, (120, 40)) as pilot:
        app.theme = drive.THEMES[theme]
        await drive.settle(app, pilot)

        async def take(state: str, region: Callable[[], tuple[int, int, int, int]], note: str) -> None:
            try:
                await drive.settle(app, pilot)
                info = await drive.shot(app, pilot, out, state)
                data = (out / f"{state}.svg").read_text(encoding="utf-8")
                (out / f"{state}.grey.svg").write_text(svg.greyscale(data), encoding="utf-8")
                w, h = app.size
                reg = region()
                rows.append({**info, "state": state, "what": note, "region": list(reg),
                             "pairs": svg.pairs(svg.parse(data, w, h), reg)})
            except Exception as e:  # noqa: BLE001 - recorded as evidence, the other states still run
                rows.append({"state": state, "error": drive.error_text(e)})

        feed = app.query_one("#feed", DataTable)
        feed.focus()
        await take("focused-row", lambda: _row_region(feed, feed.cursor_row), "the feed's cursor row, focused")
        await take("zebra-rows", lambda: (*_row_region(feed, 1)[:3], 2), "feed rows 2 and 3 (zebra stripes)")
        app.set_focus(app.query_one("Tabs"))
        await take("unfocused-row", lambda: _row_region(feed, feed.cursor_row),
                   "the feed's cursor row with the focus on the tab bar")
        await take("focused-tabs", lambda: _reg(app.query_one("Tabs")), "the tab bar, focused")
        app.query_one("#svc-start").focus()
        await take("focused-button", lambda: _reg(app.query_one("#svc-start")), "the Start button, focused")
        await take("error-status", lambda: _reg(app.query_one("#status")),
                   "the bot box with the service failed (red)")
        await take("muted-text", lambda: _reg(app.query_one("#svc-name")), "muted text (the service name)")
        await take("header", lambda: _reg(app.query_one("Header")), "the header")
        await take("footer", lambda: _reg(app.query_one("Footer")), "the footer")
        await pilot.press("2")
        await drive.settle(app, pilot)
        await take("warning-text", lambda: _reg(app.query_one("#review-dryrun")), "the dry-run warning (.warn)")
        app.query_one("#review-list").focus()
        await take("focused-list", lambda: _reg(app.query_one("#review-list")),
                   "the review list with its highlighted post, focused")
        await pilot.press("3")
        await drive.settle(app, pilot)
        app.query_one("#q", Input).focus()
        await take("focused-input", lambda: _reg(app.query_one("#q")), "the search box, focused")
        await pilot.press(*list(QUERY), "enter")
        await drive.settle(app, pilot)
        res = app.query_one("#results", DataTable)
        await take("zebra-results", lambda: (*_row_region(res, 1)[:3], 2),
                   "search results rows 2 and 3, unfocused (zebra)")
        await pilot.press("4")
        await drive.settle(app, pilot)
        await pilot.click("#editor-roles #kw-delete")
        await take("toast-warning", lambda: _reg(app.screen.query("Toast").last()),
                   "a warning notification (delete with nothing picked)")
        app.clear_notifications()
        app.query_one("#sections", TabbedContent).active = "sec-chats"
        await drive.settle(app, pilot)
        app.query_one("#chat-add", Input).focus()
        await pilot.press(*list("x1"), "enter")
        await take("toast-error", lambda: _reg(app.screen.query("Toast").last()),
                   "an error notification (not a chat ID)")
    return rows


def colours(sb: Sandbox, themes: list[str]) -> dict:
    summary = []
    for theme in themes:
        sbx.set_bidi("full")
        out = sb.out / "colours" / theme
        rows = asyncio.run(colours_run(sb, theme, out))
        evidence.write_json(out / "states.json", rows)
        for r in rows:
            pairs = r.get("pairs", [])
            text = [p["contrast"] for p in pairs if p["kind"] == "text"]
            border = [p["contrast"] for p in pairs if p["kind"] == "border"]
            summary.append({"theme": theme, "state": r.get("state"),
                            # no pairs means the SVG wasn't read (a format change), not a pass
                            "error": "error" in r or not text,
                            "lowest_text": min(text, default=None), "lowest_border": min(border, default=None),
                            "pairs_below_minimum": sum(1 for p in pairs if p["below_minimum"])})
    lines = ["| theme | state | lowest text contrast (4.5) | lowest border contrast (3.0) | pairs below |",
             "|---|---|---|---|---|"]
    lines += [f"| {s['theme']} | {s['state']} | {s['lowest_text']} | {s['lowest_border']} | "
              f"{'error' if s['error'] else s['pairs_below_minimum']} |" for s in summary]
    evidence.write_text(sb.out / "colours" / "summary.md", "\n".join(lines) + "\n\nA pair below its minimum may be "
                        "exempt (disabled or dimmed controls, placeholders, unfocused decorative borders, stock "
                        "Textual styles): see states.json for the cells and the auditor decides.\n")
    evidence.write_json(sb.out / "colours" / "summary.json", summary)
    return {"states": len(summary), "below": sum(s["pairs_below_minimum"] for s in summary),
            "errors": sum(1 for s in summary if s["error"])}
