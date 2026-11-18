"""Search tab tasks: find a post and see why (open a result); re-judge it."""

from __future__ import annotations

from textual.widgets import DataTable, Static, TabbedContent

from . import Counter, Task, TaskFailed

QUERY = "Basra"


def _detail(c: Counter) -> str:
    return str(c.app.query_one("#search-detail-head", Static).content)


def opened(c: Counter) -> str:
    """The result under the cursor (the second one) is the one shown in the detail."""
    pane = c.app.query_one("SearchPane")
    table = c.app.query_one("#results", DataTable)
    if table.row_count < 2 or table.cursor_row != 1:
        raise TaskFailed(f"the second result isn't chosen (rows {table.row_count}, cursor {table.cursor_row})")
    key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
    j = pane._rows.get(key)
    head = _detail(c)
    if j is None or f"msg {j.msg_id}" not in head:
        raise TaskFailed(f"the detail shows {head!r}, not the chosen result")
    return head


async def _search_kb(c: Counter) -> None:
    await c.key("3")
    await c.tab_to(lambda: c.focused_id() == "q")
    await c.type(QUERY)
    await c.key("enter")                       # searches and moves into the results
    await c.key("down")


async def _search_mouse(c: Counter) -> None:
    await c.click(c.app.query_one(TabbedContent).get_tab("search"), what="the Search tab")
    await c.click("#q", what="the search box")
    await c.type(QUERY)                        # typing searches (after a short pause)
    await c.wait(1.0)
    await c.click("#results", offset=(5, 2), what="the second result row")


def rejudged(c: Counter) -> str:
    opened(c)
    result = str(c.app.query_one("#judge-result", Static).content)
    if "review" not in result:
        raise TaskFailed(f"no new verdict shown: {result!r}")
    return result


async def _rejudge_kb(c: Counter) -> None:
    await _search_kb(c)
    await c.key("j")


async def _rejudge_mouse(c: Counter) -> None:
    await _search_mouse(c)
    await c.click("#rejudge", what="Re-judge now")


TASKS = [
    Task("Search", "search and open a result", _search_kb, _search_mouse, opened),
    Task("Search", "re-judge a result", _rejudge_kb, _rejudge_mouse, rejudged),
]
