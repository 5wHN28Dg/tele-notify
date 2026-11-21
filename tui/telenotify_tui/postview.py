"""PostView: a Static that draws outside text (a `rtl.Block`) right to left
where it should be, and copies it back in reading order.

Given anything else (a string, a rich Text) it is a plain Static. Given a
Block it lays the block out for its own width (again on resize), draws the
lines as Textual Content, so the mouse can select them, and answers a copy
with the characters under the selection as they were stored, not in the
order they are drawn (which, for Arabic, would paste reversed and in
presentation forms).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil

from textual.app import App
from textual.content import Content
from textual.geometry import Size
from textual.selection import Selection
from textual.widgets import Static

from .rtl import Block, Line, logical


class PostView(Static):
    DEFAULT_CSS = """
    PostView { text-wrap: nowrap; text-overflow: clip; }
    """

    _width = 0   # the width last drawn at

    @property
    def block(self) -> Block | None:
        c = self.content
        return c if isinstance(c, Block) else None

    def _parts(self, block: Block, width: int) -> tuple[list[Content], list[Line]]:
        width = max(1, width)
        head = Content.from_rich_text(block.head).wrap(width) if block.head is not None else []
        return head, block.layout(width)

    def _drawn(self, block: Block, width: int) -> Content:
        head, body = self._parts(block, width)
        style = str(block.style) if block.style else ""
        lines = head + [Content(" " * line.pad) + Content.styled("".join(p.text for p in line.pieces), style)
                        for line in body]
        return Content("\n").join(lines)

    def render(self):
        block = self.block
        if block is None:
            return super().render()
        self._width = self.content_region.width or self.size.width
        return self._drawn(block, self._width)

    def get_content_width(self, container: Size, viewport: Size) -> int:
        if self.block is None:
            return super().get_content_width(container, viewport)
        return container.width

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        block = self.block
        if block is None:
            return super().get_content_height(container, viewport, width)
        head, body = self._parts(block, width)
        return len(head) + len(body)

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        block = self.block
        if block is None:
            return super().get_selection(selection)
        head, body = self._parts(block, self._width or self.size.width)
        drawn = [h.plain for h in head] + [line.text for line in body]
        if not drawn:
            return None
        y0 = selection.start.y if selection.start is not None else 0
        y1 = min(selection.end.y if selection.end is not None else len(drawn) - 1, len(drawn) - 1)
        head_text, picks = [], []
        for y in range(y0, y1 + 1):
            x0 = selection.start.x if selection.start is not None and y == y0 else 0
            x1 = selection.end.x if selection.end is not None and y == y1 else len(drawn[y])
            if y < len(head):
                head_text.append(drawn[y][x0:x1])
            else:
                picks.append((y - len(head), x0, x1))
        parts = head_text + ([logical(block.text, body, picks)] if picks else [])
        return "\n".join(parts), "\n"


COPY_TIMEOUT = 3.0   # seconds a clipboard program may take
NOT_COPIED = ("Sent {n} to your terminal's clipboard (OSC 52). If nothing pastes, your terminal "
              "blocks it: select with the mouse instead.")


def _chars(n: int) -> str:
    return f"{n} character{'s' if n != 1 else ''}"


def clipboard_command() -> list[str] | None:
    """The desktop's own clipboard program, when the screen runs on this machine's desktop (not over
    SSH, where it would fill the far machine's clipboard): wl-copy on Wayland, else xclip or xsel on X11."""
    env = os.environ
    if env.get("SSH_CONNECTION"):
        return None
    if env.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        return ["wl-copy"]
    if env.get("DISPLAY"):
        if shutil.which("xclip"):
            return ["xclip", "-selection", "clipboard"]
        if shutil.which("xsel"):
            return ["xsel", "--clipboard", "--input"]
    return None


async def _desktop_copy(command: list[str], text: str) -> bool:
    """Run `command` with `text` on stdin only (it is untrusted: never in argv). True if it took it."""
    try:
        proc = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.DEVNULL,
                                                    stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    try:
        await asyncio.wait_for(proc.communicate(text.encode("utf-8")), COPY_TIMEOUT)
    except (TimeoutError, OSError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        return False
    return proc.returncode == 0


def copy_text(text: str) -> str:
    """What `y` copies: the post as a mouse selection of all of it gives it (PostView.get_selection),
    in reading order and without bidi controls (rtl.safe, through Block)."""
    return Block(text).text


def copy_post(app: App, text: str) -> None:
    """Put a post's text on the clipboard and say what happened (#78). OSC 52 always goes to the
    terminal, but nothing tells us whether the terminal honoured it; so on this machine's desktop the
    desktop's clipboard program is used as well, and only then does the note say "Copied"."""
    content = copy_text(text)
    app.copy_to_clipboard(content)
    command = clipboard_command()

    async def run() -> None:
        ok = command is not None and await _desktop_copy(command, content)
        n = _chars(len(content))
        app.notify(f"Copied the post's text ({n})." if ok else NOT_COPIED.format(n=n), markup=False)

    app.run_worker(run(), group="copy-post", exclusive=True)
