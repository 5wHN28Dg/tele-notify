"""R2.1 "must be visible": each item of the tab table (tui/README.md) checked
in the state its row names, in the dry run and the real mode.

Visible (policy, section 2): the widget's clipped region
(`screen.find_widget(w)`) equals its region, every ancestor has display on,
and its label appears in the screenshot text. The state is reached by the
shortest key path, which is recorded.
"""

from __future__ import annotations

import asyncio

from textual.css.query import NoMatches
from textual.errors import NoWidget
from textual.widgets import Button, TabbedContent

from telenotify_tui.screens.review import ReviewPane

from . import drive, evidence, svg
from . import sandbox as sbx
from .sandbox import Sandbox

# (tab, state, [(item, selector, texts that must show: any of them; None = the widget's own label)])
TABLE: list[tuple[str, str, list[tuple[str, str, tuple[str, ...] | None]]]] = [
    ("Dashboard", "first open", [
        ("service state", "#status", ("Service",)),
        ("Start button", "#svc-start", None), ("Stop button", "#svc-stop", None),
        ("Restart button", "#svc-restart", None),
        ("dry run or real", "#status", ("dry-run", "dry run", "real")),
        ("Telegram connection", "#status", ("Telegram",)),
        ("pending reviews", "#status", ("pending",)),
        ("alert count and age (or no alerts)", "#status", ("Alerts",)),
        ("the period name", "#period", ("Last 7 days",)),
        ("judged / publish / review / reject / forwarded counts", "#totals", ("judged", "forwarded")),
    ]),
    ("Review", "first open", [
        ("dry run or real", "#review-summary", ("dry run", "real")),
        ("how many are waiting", "#review-summary", ("waiting",)),
        ("the post's chat", "#detail-head", ("chat:",)),
        ("the post's reasons", "#detail-head", ("why",)),
        ("the start of its text", "#detail-text", ()),
        ("Send button", "#btn-send", None), ("Match no send button", "#btn-keep", None),
        ("Discard button", "#btn-discard", None),
    ]),
    ("Review", "after a decision", [("Undo button", "#btn-undo", None)]),
    ("Search", "first open", [
        ("the search box", "#q", ()),
        ("the first results with their verdicts", "#results", ("verdict",)),
    ]),
    ("Search", "after choosing a result", [
        ("the chosen result's verdict", "#detail-reasons", ("verdict",)),
        ("the chosen result's reasons", "#detail-reasons", ("why",)),
    ]),
    ("Settings", "first open", [
        ("saved / unsaved marker", "#settings-status", ("saved",)),
        ("problem count", "#settings-status", ("problem",)),
        ("Save button", "#save", ("Save",)), ("Test button", "#test", ("Test",)),
        ("Revert button", "#revert", ("Revert",)),
        ("list picker", "#picker-roles", ()), ("chosen list", "#editor-roles #kw-items", ()),
        ("Edit button", "#editor-roles #kw-edit", ("Edit",)),
        ("Delete button", "#editor-roles #kw-delete", ("Delete",)),
        ("Add button", "#editor-roles #kw-add", ("Add",)),
    ]),
    ("Settings", "in Chats", [("the chat table", "#chat-table", ())]),
]
TAB_KEYS = {"Dashboard": "1", "Review": "2", "Search": "3", "Settings": "4"}


def check(app, w, text: str, wanted: tuple[str, ...] | None) -> dict:
    """The policy's three conditions for one widget."""
    try:
        geo = app.screen.find_widget(w)
        clipped = geo.region.intersection(geo.clip)
        whole = clipped == geo.region and geo.region.area > 0
        region = list(geo.region)
    except NoWidget:
        whole, region = False, None
    shown = all(a.display for a in [w, *w.ancestors] if hasattr(a, "display")) and w.visible
    if wanted is None:
        wanted = (str(w.label).strip(),) if isinstance(w, Button) else ()
    label_ok = None if not wanted else any(t in text for t in wanted)
    return {"region": region, "clipped_equals_region": whole, "ancestors_displayed": shown,
            "label_checked": list(wanted), "label_in_screenshot": label_ok,
            "visible": whole and shown and label_ok is not False}


async def _tab_until(app, pilot, done, limit: int = 20) -> list[str]:
    keys = []
    while not done() and len(keys) < limit:
        await pilot.press("tab")
        await pilot.pause()
        keys.append("tab")
    return keys


async def reach(app, pilot, tab: str, state: str) -> list[str]:
    """Go to the state by its shortest key path; returns the keys pressed."""
    keys: list[str] = []
    if app.query_one(TabbedContent).active != tab.lower():   # already there: no key needed
        keys.append(TAB_KEYS[tab])
        await pilot.press(keys[0])
        await drive.settle(app, pilot)
    if state == "after a decision":
        await asyncio.sleep(ReviewPane.DECIDE_DELAY + 0.1)   # waits aren't keys
        await pilot.press("s")
        keys.append("s")
    elif state == "after choosing a result":
        keys += await _tab_until(app, pilot, lambda: app.focused is not None and app.focused.id == "results")
    elif state == "in Chats":
        sections = app.query_one("#sections", TabbedContent)
        bar = sections.query_one("ContentTabs")
        keys += await _tab_until(app, pilot, lambda: app.focused is bar)
        while sections.active != "sec-chats" and len(keys) < 30:
            await pilot.press("right")
            await pilot.pause()
            keys.append("right")
    await drive.settle(app, pilot)
    return keys


async def run(sb: Sandbox, size: tuple[int, int], out) -> list[dict]:
    sbx.prepare(sb)
    app = drive.make_app(sb)
    rows = []
    async with drive.run_test(app, size) as pilot:
        await drive.settle(app, pilot)
        for tab, state, items in TABLE:
            name = f"{tab}-{state.replace(' ', '-')}"
            try:
                keys = await reach(app, pilot, tab, state)
                info = await drive.shot(app, pilot, out, name)
                w, h = app.size
                text = svg.text((out / f"{name}.svg").read_text(encoding="utf-8"), w, h)
                for item, sel, wanted in items:
                    try:
                        widget = app.screen.query_one(sel)
                        r = check(app, widget, text, wanted)
                    except NoMatches:
                        r = {"visible": False, "missing": f"no widget {sel}"}
                    rows.append({"tab": tab, "state": state, "keys": keys, "item": item, "widget": sel,
                                 "shot": info["name"], **r})
            except Exception as e:  # noqa: BLE001 - evidence
                rows.append({"tab": tab, "state": state, "error": drive.error_text(e)})
    return rows


def collect(sb: Sandbox, sizes: list[str], modes: list[str]) -> dict:
    sbx.set_bidi("full")
    keep = sb.dry_run
    result = {}
    try:
        for mode in modes:
            sb.dry_run = mode != "real"
            for size in sizes:
                out = sb.out / "visible" / mode / size
                rows = asyncio.run(run(sb, drive.SIZES[size], out))
                evidence.write_json(out / "items.json", rows)
                lines = ["| tab | state (keys) | item | visible | why not |", "|---|---|---|---|---|"]
                for r in rows:
                    why = r.get("error", "").splitlines()[-1:] or [
                        k for k in ("clipped_equals_region", "ancestors_displayed", "label_in_screenshot")
                        if r.get(k) is False] or ([r["missing"]] if "missing" in r else [])
                    lines.append(f"| {r['tab']} | {r['state']} ({' '.join(r.get('keys', []))}) | "
                                 f"{r.get('item', '')} | {r.get('visible')} | {', '.join(why)} |")
                evidence.write_text(out / "items.md", "\n".join(lines) + "\n")
                result[f"{mode}/{size}"] = {"items": len(rows),
                                            "not_visible": sum(1 for r in rows if not r.get("visible"))}
    finally:
        sb.dry_run = keep
    return result
