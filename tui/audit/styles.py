"""R14 style scan of the screen's source, and the per-view DOM query.

1. Literal colours (hex, rgb() / hsl(), named colours) in CSS (app.tcss and
   DEFAULT_CSS) and in styles or markup in the Python code, where a theme
   variable ($primary...) should be. `dim`, `bold`, `reverse`... aren't colours.
2. padding / margin / grid-gutter values other than 0, 1 or 2 cells.
3. Per tab view (a mode a tab switches to is its own view): the displayed
   `variant="primary"` buttons and the headings drawn in $primary (borders
   and border titles don't count), and whether each is the tab table's main
   action.
"""

from __future__ import annotations

import asyncio
import functools
import io
import re
import tokenize
from pathlib import Path

from rich.color import ANSI_COLOR_NAMES

from . import appsrc, drive, evidence
from . import sandbox as sbx
from .sandbox import Sandbox

COLOUR_NAMES = {n for n in ANSI_COLOR_NAMES if n != "default"} | {
    "white", "black", "red", "green", "blue", "yellow", "magenta", "cyan", "orange", "purple", "pink", "grey",
    "gray", "brown", "lime", "navy", "teal", "olive", "maroon", "silver", "aqua", "fuchsia", "gold", "crimson"}
STYLE_WORDS = {"bold", "dim", "italic", "underline", "reverse", "strike", "blink", "not", "on", "none", "b", "i",
               "u", "s", "underline2", "overline", "conceal", "frame", "encircle", "default"}
COLOUR_PROPS = re.compile(r"^(color|background|border[\w-]*|outline[\w-]*|tint|scrollbar[\w-]*|link-[\w-]*|"
                          r"text-[\w-]*color|[\w-]*-color|[\w-]*-background)$")
SPACING_PROPS = re.compile(r"^(padding|margin|grid-gutter)(-(top|right|bottom|left))?$")
HEX = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")   # #rgba is too like an id (#feed)
FUNC = re.compile(r"\b(rgb|rgba|hsl|hsla)\(")
DECL = re.compile(r"([a-z-]+)\s*:\s*([^;{}]+);?")
MARKUP = re.compile(r"\[(/?)([a-z0-9#_ ]+)\]")


def _literal_colour(value: str) -> list[str]:
    hits = HEX.findall(value) + [m.group(0) for m in FUNC.finditer(value)]
    words = re.findall(r"[a-z_0-9]+", re.sub(r"\$[\w-]+", "", value.lower()))
    hits += [w for w in words if w in COLOUR_NAMES]
    return hits


def scan_css(text: str, where: str) -> list[dict]:
    out = []
    for m in DECL.finditer(text):
        prop, value = m.group(1), m.group(2).strip()
        line = text.count("\n", 0, m.start()) + 1
        if COLOUR_PROPS.match(prop):
            hits = _literal_colour(value)
            if hits:
                out.append({"rule": "R14.1", "where": f"{where}:{line}", "property": prop, "value": value,
                            "literal": hits})
        if SPACING_PROPS.match(prop):
            nums = re.findall(r"-?\d+(?:\.\d+)?%?", value)
            bad = [n for n in nums if n not in ("0", "1", "2")]
            if bad:
                out.append({"rule": "R14.2", "where": f"{where}:{line}", "property": prop, "value": value,
                            "values": bad})
    return out


def _style_string(s: str) -> list[str]:
    """A string that is a whole Rich style ("bold red", "#ff0000 on blue"): its colours."""
    words = s.lower().split()
    if not words or len(words) > 6:
        return []
    if all(w in STYLE_WORDS or w in COLOUR_NAMES or HEX.fullmatch(w) or FUNC.match(w) for w in words):
        return [w for w in words if w in COLOUR_NAMES or HEX.fullmatch(w) or FUNC.match(w)]
    return []


def scan_python(text: str, where: str) -> list[dict]:
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type != tokenize.STRING:
            continue
        line = tok.start[0]
        try:
            value = eval(tok.string, {"__builtins__": {}})   # a literal from tokenize, no names
        except Exception:  # noqa: BLE001 - f-strings and the like: scanned as raw text
            value = tok.string
        if not isinstance(value, str):
            continue
        if "{" in value and ":" in value and ";" in value:            # a CSS block (DEFAULT_CSS)
            for hit in scan_css(value, where):
                hit["where"] = f"{where}:{line + int(hit['where'].rsplit(':', 1)[1]) - 1}"
                out.append(hit)
            continue
        hits = _style_string(value)
        for m in MARKUP.finditer(value):
            if not m.group(1):                                       # an opening tag
                hits += _style_string(m.group(2))
        if hits:
            out.append({"rule": "R14.1", "where": f"{where}:{line}", "string": value[:80], "literal": hits})
    return out


def static_scan(app_dir: Path) -> list[dict]:
    pkg = app_dir / "telenotify_tui"
    rows = []
    for f in sorted(pkg.rglob("*")):
        rel = str(f.relative_to(app_dir))
        if f.suffix == ".tcss":
            rows += scan_css(f.read_text(encoding="utf-8"), rel)
        elif f.suffix == ".py":
            rows += scan_python(f.read_text(encoding="utf-8"), rel)
    return rows


# --------------------------------------------------------------- DOM query

MAIN_ACTION = {"dashboard": {"period"}, "review": {"btn-send"}, "search": {"q", "results"}, "settings": {"save"}}


def _views():
    """(view name, tab id, how to get there)."""
    async def keys(app, pilot, *k):
        for key in k:
            await pilot.press(key)
        await drive.settle(app, pilot)

    async def section(app, pilot, sec):
        from textual.widgets import TabbedContent

        app.set_focus(None)
        app.query_one("#sections", TabbedContent).active = f"sec-{sec}"
        await drive.settle(app, pilot)

    async def paste(app, pilot):
        app.query_one("#results").focus()
        await keys(app, pilot, "p")

    out = [("dashboard: live feed", "dashboard", lambda a, p: keys(a, p, "1", "f")),
           ("dashboard: chats", "dashboard", lambda a, p: keys(a, p, "c")),
           ("dashboard: alerts & health", "dashboard", lambda a, p: keys(a, p, "a")),
           ("review", "review", lambda a, p: keys(a, p, "2")),
           ("search", "search", lambda a, p: keys(a, p, "3")),
           ("search: paste mode", "search", paste),
           ("settings: roles", "settings", lambda a, p: keys(a, p, "escape", "4"))]
    out += [(f"settings: {s}", "settings", functools.partial(section, sec=s)) for s in ("places", "filters", "chats", "bot")]
    return out


def _shown(app, w) -> bool:
    from textual.errors import NoWidget

    try:
        geo = app.screen.find_widget(w)
    except NoWidget:
        return False
    return geo.region.intersection(geo.clip).area > 0 and all(a.display for a in [w, *w.ancestors]
                                                                  if hasattr(a, "display"))


async def dom_query(sb: Sandbox) -> list[dict]:
    from textual.color import Color
    from textual.widgets import Button, TabbedContent

    sbx.prepare(sb)
    app = drive.make_app(sb)
    rows = []
    async with drive.run_test(app, (120, 40)) as pilot:
        await drive.settle(app, pilot)
        primary = Color.parse(app.theme_variables["primary"])
        for name, tab, go in _views():
            try:
                await go(app, pilot)
                pane = app.query_one(TabbedContent).get_pane(tab)
                found = []
                for w in pane.query("*"):
                    if not _shown(app, w):
                        continue
                    if isinstance(w, Button) and w.variant == "primary":
                        found.append({"kind": "primary button", "widget": drive.describe(w),
                                      "label": str(w.label), "main_action": w.id in MAIN_ACTION[tab]})
                    elif not isinstance(w, Button) and w.styles.color == primary and not w.children:
                        found.append({"kind": "$primary text", "widget": drive.describe(w),
                                      "main_action": w.id in MAIN_ACTION[tab]})
                rows.append({"view": name, "count": len(found), "items": found,
                             "more_than_one": len(found) > 1,
                             "not_the_main_action": [f["widget"] for f in found if not f["main_action"]]})
            except Exception as e:  # noqa: BLE001 - evidence
                rows.append({"view": name, "error": drive.error_text(e)})
    return rows


def collect(sb: Sandbox) -> dict:
    import os

    app_dir = Path(os.environ.get(appsrc.ENV, str(appsrc.HERE)))
    scan = static_scan(app_dir)
    sbx.set_bidi("full")
    views = asyncio.run(dom_query(sb))
    evidence.write_json(sb.out / "styles" / "scan.json", scan)
    evidence.write_json(sb.out / "styles" / "views.json", views)
    lines = ["## R14.1 / R14.2 source scan", "", "| rule | where | what |", "|---|---|---|"]
    lines += [f"| {r['rule']} | {r['where']} | `{r.get('property', '')}` "
              f"{r.get('value', r.get('string', ''))!r} -> {r.get('literal', r.get('values'))} |" for r in scan]
    lines += ["", "## R14.3 primary buttons and $primary text per tab view (120 x 40)", "",
              "| view | count | items | not the main action |", "|---|---|---|---|"]
    lines += [f"| {v['view']} | {v.get('count', 'error')} | "
              f"{', '.join(i['widget'] for i in v.get('items', []))} | {', '.join(v.get('not_the_main_action', []))} |"
              for v in views]
    evidence.write_text(sb.out / "styles" / "summary.md", "\n".join(lines) + "\n")
    return {"R14.1": sum(1 for r in scan if r["rule"] == "R14.1"), "R14.2": sum(1 for r in scan if r["rule"] == "R14.2"),
            "views_with_more_than_one": sum(1 for v in views if v.get("more_than_one"))}
