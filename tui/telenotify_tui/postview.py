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
        return without_invisible("\n".join(parts)), "\n"


# direction marks and invisible characters: they shape how a line is drawn, so the drawing keeps
# them, but a copy without them pastes what was read (a link with an RLM glued on works, I5)
INVISIBLE = dict.fromkeys(map(ord, "\u200e\u200f\u061c\u200b\ufeff\u00ad\u2060"))


def without_invisible(text: str) -> str:
    """`text` without LRM, RLM, ALM, the zero-width space, the BOM, the soft hyphen and the word
    joiner (normalize.nim drops all but ALM and the word joiner too; ZWJ and ZWNJ stay: they
    change how emoji and Persian words read)."""
    return text.translate(INVISIBLE)


COPY_TIMEOUT = 3.0   # seconds a clipboard program (or tmux, asked who is attached) may take
NOT_COPIED = ("Sent {n} to your terminal's clipboard (OSC 52). If nothing pastes, your terminal "
              "blocks it: select with the mouse instead.")


def _chars(n: int) -> str:
    return f"{n} character{'s' if n != 1 else ''}"


def clipboard_command() -> list[str] | None:
    """The desktop's own clipboard program, unless the screen runs over SSH (where it would fill the
    far machine's clipboard): wl-copy on Wayland, else xclip or xsel on X11."""
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


async def _run(command: list[str], data: bytes | None = None, read: bool = False) -> tuple[int | None, bytes]:
    """Run `command` (`data`, if any, on stdin only: it may be untrusted, so never in argv) for at most
    COPY_TIMEOUT. (exit status, stdout if `read`); status None if it couldn't run or was too slow. The
    program is killed if it is too slow or the copy is cancelled (a second y, quitting), so none is left
    behind. Without `read` stdout goes nowhere: xclip and wl-copy leave a child serving the clipboard,
    which would hold a pipe open until the timeout."""
    try:
        proc = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE if read
                                                    else asyncio.subprocess.DEVNULL,
                                                    stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return None, b""
    try:
        out, _ = await asyncio.wait_for(proc.communicate(data), COPY_TIMEOUT)
    except (TimeoutError, OSError):
        return None, b""
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
    return proc.returncode, out or b""


async def attached_from_here() -> bool | None:
    """Whether the user's terminal is on this machine. Outside tmux: yes (SSH_CONNECTION is checked
    first). Inside tmux the pane keeps the environment it started with, so ask tmux what the last
    attached client brought (update-environment sets SSH_CONNECTION, or "-SSH_CONNECTION" when that
    client had none). None: tmux couldn't say. With two clients attached at once (one here, one over
    ssh), the one that attached last decides."""
    if not os.environ.get("TMUX"):
        return True
    status, out = await _run(["tmux", "show-environment", "SSH_CONNECTION"], read=True)
    line = out.decode("utf-8", "replace").strip()
    if status == 0 and line == "-SSH_CONNECTION":
        return True
    if status == 0 and line.startswith("SSH_CONNECTION="):
        return False
    return None


def copy_text(text: str) -> str:
    """What `y` copies: the post as a mouse selection of all of it gives it (PostView.get_selection),
    in reading order, without bidi controls (rtl.safe, through Block) or direction marks."""
    return without_invisible(Block(text).text)


def copy_post(app: App, text: str) -> None:
    """Put a post's text on the clipboard and say what happened (#78). OSC 52 always goes to the
    terminal, but nothing tells us whether the terminal honoured it; so on this machine's desktop the
    desktop's clipboard program is used as well. The note says "Copied" only when that took the text and
    the user's terminal is known to be here; inside tmux, when tmux can't say, it names where it went."""
    content = copy_text(text)
    app.copy_to_clipboard(content)
    command = clipboard_command()

    async def run() -> None:
        n = _chars(len(content))
        here = await attached_from_here() if command is not None else False
        ok = here is not False and command is not None and (await _run(command, content.encode("utf-8")))[0] == 0
        if not ok:
            note = NOT_COPIED.format(n=n)
        elif here:
            note = f"Copied the post's text ({n})."
        else:
            note = f"Copied to this computer's desktop clipboard ({n})."
        app.notify(note, markup=False)

    app.run_worker(run(), group="copy-post", exclusive=True)
