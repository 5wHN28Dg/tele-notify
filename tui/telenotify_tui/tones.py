"""Readable error, warning and success colours for text, from the theme.

Textual's own $text-warning is 3.3:1 on textual-light's background, and fixed colours ("red",
"yellow") read at 4.47:1 in textual-dark and below 2:1 under NO_COLOR, where Textual's monochrome
filter turns every colour into its grey (#139). Each tone here starts from the theme's own
$text-error / $text-warning / $text-success and is moved toward $foreground until it reaches
MINIMUM on every background these texts sit on, in colour and in grey.

The app adds them as CSS variables ($tn-error, $tn-warning, $tn-success): use them in CSS, in
Content styles ("$tn-error"), or for rich Text through `tone(app, "error")`.
"""

from __future__ import annotations

from textual.color import Color

MINIMUM = 4.6   # WCAG's 4.5:1 for text, with a little room for rounding in the terminal
BACKGROUNDS = ("background", "surface", "panel")
TONES = {"tn-error": "text-error", "tn-warning": "text-warning", "tn-success": "text-success"}


def _lum(c: Color) -> float:
    def lin(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b)


def ratio(a: Color, b: Color) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _solid(value: str, under: Color) -> Color:
    c = Color.parse(value)
    return c if c.a >= 1 else under + c


def readable(start: Color, toward: Color, backgrounds: list[Color]) -> Color:
    """`start` moved toward `toward` in small steps until it reaches MINIMUM on every background,
    in colour and in grey (NO_COLOR)."""
    for step in range(21):
        c = start.blend(toward, step / 20)
        if all(ratio(c, bg) >= MINIMUM and ratio(c.monochrome, bg.monochrome) >= MINIMUM for bg in backgrounds):
            return c
    return toward


def tones(variables: dict[str, str]) -> dict[str, str]:
    """The tn-* variables for a theme's variables."""
    base = Color.parse(variables["background"])
    backgrounds = [_solid(variables[n], base) for n in BACKGROUNDS if n in variables]
    fg = _solid(variables["foreground"], base)
    out = {}
    for name, start in TONES.items():
        first = _solid(variables[start], base) if start in variables else fg
        out[name] = readable(first, fg, backgrounds).hex
    return out


def tone(app, kind: str) -> str:
    """The current theme's readable colour for "error", "warning" or "success" (a hex string), for
    rich Text; the text must be drawn again when the theme changes."""
    variables = app.theme_variables
    return variables.get(f"tn-{kind}") or tones(variables)[f"tn-{kind}"]
