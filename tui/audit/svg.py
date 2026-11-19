"""Reading Textual's SVG screenshots back: the text on each row, and the
foreground / background colour of each cell (R6), plus a greyscale copy.

Textual's `export_screenshot` uses Rich's `export_svg`: cells are 12.2 px
wide (20 px font, aspect 0.61) and rows 24.4 px high; every background is
a <rect> at (col * 12.2, row * 24.4 + 1.5) and every run of text a <text>
at (col * 12.2, row * 24.4 + 20) whose CSS class carries its fill colour
(dim and reverse already applied).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from rich.cells import cell_len

CHAR_W = 20 * 0.61
LINE_H = 20 * 1.22

_STYLE = re.compile(r"\.(terminal-[\w-]+-r\d+)\s*\{([^}]*)\}")
_RECT = re.compile(r'<rect fill="(#[0-9a-fA-F]{6})" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="[\d.]+"')
_TEXT = re.compile(r'<text class="([\w-]+)" x="([\d.]+)" y="([\d.]+)" textLength="[\d.]+" '
                   r'clip-path="url\(#[\w-]+-line-\d+\)">(.*?)</text>', re.DOTALL)
_HEX = re.compile(r"#([0-9a-fA-F]{6})\b")
BORDER = re.compile(r"[─-▟■-◿▀-▟▁▔▏▕]")


@dataclass
class Cell:
    ch: str = " "
    fg: str = ""
    bg: str = ""
    bold: bool = False
    underline: bool = False


@dataclass
class Grid:
    width: int
    height: int
    cells: list[list[Cell]] = field(default_factory=list)

    def text(self) -> list[str]:
        return ["".join(c.ch for c in row).rstrip() for row in self.cells]


def parse(svg: str, width: int, height: int) -> Grid:
    styles: dict[str, tuple[str, bool, bool]] = {}
    for cls, body in _STYLE.findall(svg):
        m = _HEX.search(body)
        styles[cls] = (("#" + m.group(1).lower()) if m else "", "bold" in body, "underline" in body)
    g = Grid(width, height, [[Cell() for _ in range(width)] for _ in range(height)])
    for fill, x, y, w in _RECT.findall(svg):
        row = round((float(y) - 1.5) / LINE_H)
        col = round(float(x) / CHAR_W)
        n = round(float(w) / CHAR_W)
        if 0 <= row < height:
            for c in range(max(col, 0), min(col + n, width)):
                g.cells[row][c].bg = fill.lower()
    for cls, x, y, raw in _TEXT.findall(svg):
        row = round((float(y) - 20) / LINE_H)
        col = round(float(x) / CHAR_W)
        if not 0 <= row < height:
            continue
        fg, bold, underline = styles.get(cls, ("", False, False))
        for ch in html.unescape(raw).replace("\xa0", " "):
            if col >= width:
                break
            cell = g.cells[row][col]
            cell.ch, cell.fg, cell.bold, cell.underline = ch, fg, bold, underline
            for extra in range(1, cell_len(ch)):        # a wide character covers the next cell
                if col + extra < width:
                    g.cells[row][col + extra].ch = ""
            col += max(cell_len(ch), 1)
    return g


def text(svg: str, width: int, height: int) -> str:
    return "\n".join(parse(svg, width, height).text()) + "\n"


# --------------------------------------------------------------- contrast

def _lum(hex_colour: str) -> float:
    from textual.color import Color

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = Color.parse(hex_colour).normalized
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast_exact(fg: str, bg: str) -> float:
    """The WCAG 2 contrast ratio of two colours (parsed with Textual's Color)."""
    a, b = sorted((_lum(fg), _lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def contrast(fg: str, bg: str) -> float:
    """The ratio rounded for display; never use it for pass or fail (4.495 isn't 4.5)."""
    return round(contrast_exact(fg, bg), 2)


def pairs(grid: Grid, region: tuple[int, int, int, int]) -> list[dict]:
    """The distinct (fg, bg) pairs of the non-blank cells in `region` (x, y, w, h), with the
    contrast, the cell count, an example of the text and whether the cells are border /
    block characters (3 : 1 applies) or text (4.5 : 1)."""
    x0, y0, w, h = region
    seen: dict[tuple[str, str, bool], dict] = {}
    for y in range(max(y0, 0), min(y0 + h, grid.height)):
        for x in range(max(x0, 0), min(x0 + w, grid.width)):
            c = grid.cells[y][x]
            if not c.ch.strip() or not c.fg or not c.bg:
                continue
            border = bool(BORDER.match(c.ch))
            key = (c.fg, c.bg, border)
            if key not in seen:
                seen[key] = {"fg": c.fg, "bg": c.bg, "kind": "border" if border else "text",
                             "contrast": contrast(c.fg, c.bg), "cells": 0, "sample": "",
                             "bold": c.bold, "underline": c.underline}
            e = seen[key]
            e["cells"] += 1
            if len(e["sample"]) < 24:
                e["sample"] += c.ch
    for e in seen.values():
        e["minimum"] = 3.0 if e["kind"] == "border" else 4.5
        e["below_minimum"] = contrast_exact(e["fg"], e["bg"]) < e["minimum"]
    return sorted(seen.values(), key=lambda e: (e["contrast"], -e["cells"]))


def greyscale(svg: str) -> str:
    """The same SVG with every #rrggbb colour turned into its grey (Rec. 709 luma)."""
    def grey(m: re.Match) -> str:
        v = m.group(1)
        r, g, b = int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
        y = round(0.2126 * r + 0.7152 * g + 0.0722 * b)
        return f"#{y:02x}{y:02x}{y:02x}"
    return _HEX.sub(grey, svg)
