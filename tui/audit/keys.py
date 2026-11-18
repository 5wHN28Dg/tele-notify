"""Keys (R1, R8.1): the footer and app.active_bindings for every widget along
each place's focus chain (tabs, settings sections, dialogs)."""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable

from textual.widgets import TabbedContent
from textual.widgets._footer import FooterKey

from . import drive, evidence
from . import sandbox as sbx
from .sandbox import Sandbox


def bindings(app) -> list[dict]:
    out = []
    for key, ab in app.active_bindings.items():
        b = ab.binding
        out.append({"key": key, "display": app.get_key_display(b), "action": b.action,
                    "description": b.description, "shown": b.show, "enabled": ab.enabled,
                    "priority": b.priority, "defined_on": drive.describe(ab.node)})
    return sorted(out, key=lambda r: r["key"])


def footer(app) -> list[dict]:
    width = app.size.width
    out = []
    for screen in app.screen_stack:
        for fk in screen.query(FooterKey):
            r = fk.region
            out.append({"key": fk.key, "display": fk.key_display, "description": fk.description,
                        "action": fk.action, "dimmed": bool(fk._disabled),
                        "on_screen": r.width > 0 and r.right <= width, "region": [r.x, r.y, r.width, r.height]})
    return out


async def walk(app, pilot, place: str) -> dict:
    chain = list(app.screen.focus_chain)
    stops = []
    for w in chain:
        try:
            app.screen.set_focus(w)
            await pilot.pause()
            r = w.region
            stops.append({"widget": drive.describe(w), "region": [r.x, r.y, r.width, r.height],
                          "focused_now": drive.describe(app.focused), "bindings": bindings(app),
                          "footer": footer(app)})
        except Exception as e:  # noqa: BLE001 - evidence
            stops.append({"widget": drive.describe(w), "error": drive.error_text(e)})
    return {"place": place, "screen": type(app.screen).__name__,
            "focus_chain": [drive.describe(w) for w in chain], "stops": stops}


Place = tuple[str, Callable[..., Awaitable[None]], Callable[..., Awaitable[None]] | None]


def places() -> list[Place]:
    """(name, get there, leave again or None)."""
    async def keys(app, pilot, *k):
        await pilot.press(*k)
        await drive.settle(app, pilot)

    def p(*k):
        return lambda app, pilot: keys(app, pilot, *k)

    async def section(app, pilot, sec):
        app.set_focus(None)   # a focused widget in the old section would switch straight back
        app.query_one("#sections", TabbedContent).active = f"sec-{sec}"
        await drive.settle(app, pilot)

    async def click(app, pilot, sel):
        await pilot.click(sel)
        await drive.settle(app, pilot)

    async def corpus_test(app, pilot):
        app.query_one("#save").focus()          # t from inside the settings pane
        await keys(app, pilot, "t")

    async def dirty(app, pilot):
        await section(app, pilot, "roles")
        app.query_one("#editor-roles #kw-entry").focus()
        await pilot.press(*list("audit word"), "enter")
        app.query_one("#editor-roles #kw-items").focus()
        await drive.settle(app, pilot)
        await keys(app, pilot, "ctrl+r")

    out: list[Place] = [
        ("1-dashboard", p("1"), None),
        ("dialog-stop-confirm", lambda a, pl: click(a, pl, "#svc-stop"), p("escape")),
        ("dialog-dates", p("d"), p("escape")),
        ("2-review", p("2"), None),
        ("dialog-filter-read", p("r"), p("escape")),
        ("3-search", p("3"), None),
        ("4-settings-roles", p("4"), None),
    ]
    out += [(f"4-settings-{s}", functools.partial(section, sec=s), None) for s in ("places", "filters", "chats", "bot")]
    out += [("dialog-corpus-test", corpus_test, p("escape")),
            ("dialog-revert-confirm", dirty, p("n"))]
    return out


async def run(sb: Sandbox, size: tuple[int, int]) -> list[dict]:
    sbx.prepare(sb)
    app = drive.make_app(sb)
    rows = []
    async with drive.run_test(app, size) as pilot:
        await drive.settle(app, pilot)
        for name, go, leave in places():
            try:
                await go(app, pilot)
                rows.append(await walk(app, pilot, name))
                if leave:
                    await leave(app, pilot)
            except Exception as e:  # noqa: BLE001 - evidence
                rows.append({"place": name, "error": drive.error_text(e)})
    return rows


def table(rows: list[dict]) -> str:
    """Every binding per place (R8.1): key, what it says, action, shown, where defined."""
    lines = []
    for r in rows:
        lines += [f"## {r['place']}", "", "| key | description | action | in footer | enabled | defined on |",
                  "|---|---|---|---|---|---|"]
        seen = {}
        for s in r.get("stops", []):
            for b in s.get("bindings", []):
                seen.setdefault((b["key"], b["action"]), b)
        for b in seen.values():
            lines.append(f"| `{b['display']}` | {b['description']} | `{b['action']}` | {b['shown']} | "
                         f"{b['enabled']} | {b['defined_on']} |")
        if "error" in r:
            lines.append(f"\nerror: {r['error'].splitlines()[-1]}")
        lines.append("")
    return "\n".join(lines)


def collect(sb: Sandbox, sizes: list[str]) -> dict:
    sbx.set_bidi("full")
    result = {}
    for size in sizes:
        rows = asyncio.run(run(sb, drive.SIZES[size]))
        evidence.write_json(sb.out / "keys" / f"{size}.json", rows)
        evidence.write_text(sb.out / "keys" / f"{size}.md", table(rows))
        result[size] = {"places": len(rows), "errors": sum(1 for r in rows if "error" in r)}
    return result
