"""The app shell: four tabs, one per screen.

Each screen is a widget in telenotify_tui/screens/ with a `reload()`
method; the app calls it when the tab is shown and every few seconds while
it is visible, so the numbers stay live while the bot runs.
"""

from __future__ import annotations

import time
from collections import deque
from typing import ClassVar
from weakref import WeakKeyDictionary

from textual import events
from textual.app import App, ComposeResult, ScreenStackError
from textual.binding import Binding, BindingType
from textual.errors import NoWidget
from textual.widget import Widget
from textual.widgets import Footer, Header, TabbedContent, TabPane, Tabs
from textual.widgets._footer import FooterKey

from .paths import Paths
from .rtl import visual
from .screens.dashboard import DashboardPane
from .screens.review import ReviewPane
from .screens.search import SearchPane
from .screens.settings import ConfirmModal, SettingsPane
from .store import Store
from .tones import tones

REFRESH_SECONDS = 5
THEMES = ("textual-dark", "textual-light")   # the themes offered: the ones whose colours are checked
RELOAD_LOST_SECONDS = 1.0   # a queued tab reload that hasn't run by then never will


class TeleNotifyApp(App):
    """The screen. `press_made_at` maps a widget to the times its clicks were *made*
    (the press, oldest first, one per completed click): Textual rebuilds a mouse event
    (with a new time) before a widget sees it, so dialogs that ignore early presses
    (screens/armed.py) read the real time here."""

    TITLE = "tele-notify"

    def simulate_key(self, key: str) -> None:
        """A click on a footer key: Textual makes the key when the click is *handled*; stamp it
        with when the click was made, so a slow machine can't turn an early click into a late one."""
        event = events.Key(key, None)
        made = self._footer_press(key)
        if made is not None:
            event.time = made
        self.post_message(event)

    def _footer_press(self, key: str) -> float | None:
        """When the oldest unused press on the footer key for `key` was made (each footer key's
        presses in order, so a later press elsewhere can't lend this one its time). None: no
        record (Textual's test pilot, or not a footer click), so the key keeps its own time."""
        found = None
        for widget, times in list(self.footer_downs.items()):
            if not widget.is_attached:     # a footer rebuilt since: its presses can't make a key
                del self.footer_downs[widget]
            elif found is None and getattr(widget, "key", None) == key and times:
                found = times.popleft()
        return found

    async def on_event(self, event: events.Event) -> None:
        # the driver's own events (not the forwarded copies, which have later times): a
        # press counts once its release on the same widget makes it a click, as Textual's
        # own Click is made; a press dragged off counts for nothing
        if isinstance(event, (events.MouseDown, events.MouseUp)) and not event.is_forwarded:
            try:
                widget, _ = self.get_widget_at(event.x, event.y)
            except (NoWidget, ScreenStackError):   # outside every widget, or no screen yet
                widget = None
            if isinstance(event, events.MouseDown):
                # a footer key acts on the press itself; a disabled one only rings the bell and
                # makes no key, so its press is not kept (it would put the queue one behind)
                # nor is one while another widget holds the mouse (a drag): it never sees the press
                if (isinstance(widget, FooterKey) and not getattr(widget, "_disabled", False)
                        and self.mouse_captured in (None, widget)):
                    self.footer_downs.setdefault(widget, deque()).append(event.time)
                if widget is not None:
                    self._down[event.button] = (widget, event.time)
                else:
                    self._down.pop(event.button, None)
            else:
                down = self._down.pop(event.button, None)
                if widget is not None:
                    # a release with no press of that button on the same widget still makes
                    # a Click in Textual; it's recorded as made long ago, so it's refused
                    made = down[1] if down is not None and down[0] is widget else float("-inf")
                    self.press_made_at.setdefault(widget, deque()).append(made)
        await super().on_event(event)

    CSS_PATH = "app.tcss"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("1", "show('dashboard')", "Dashboard"),
        Binding("2", "show('review')", "Review"),
        Binding("3", "show('search')", "Search"),
        Binding("4", "show('settings')", "Settings"),
        Binding("ctrl+r", "refresh_tab", "Refresh"),
        # not plain "q": it would quit while a table or list has focus
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    def __init__(self, paths: Paths, refresh_seconds: float = REFRESH_SECONDS):
        super().__init__()
        # only the two themes whose colours are checked are offered (#81): the ansi themes take the
        # terminal's own colours, dark in a dark terminal even as "light", and our hover and selection
        # styles vanish in them (#112, the owner's check); in the other built-in themes the chart's
        # series and some text fall below the contrast we need (#118)
        for name in list(self.available_themes):
            if name not in THEMES:
                self.unregister_theme(name)
        # TEXTUAL_THEME may have named one: Textual already built its colours from it, so apply
        # the theme it falls back to now (#116, review run 1)
        self.theme = self.current_theme.name
        self.footer_downs: WeakKeyDictionary[Widget, deque[float]] = WeakKeyDictionary()
        self.press_made_at: WeakKeyDictionary[Widget, deque[float]] = WeakKeyDictionary()
        self._down: dict[int, tuple[Widget, float]] = {}   # per mouse button
        self.paths = paths
        self.refresh_seconds = refresh_seconds
        self._reload_queued: float | None = None   # when a reload was queued (monotonic), until it runs
        self.sub_title = ("DRY RUN · " if paths.dry_run else "") + visual(paths.config)

    def get_css_variables(self) -> dict[str, str]:
        """The theme's variables plus $tn-error, $tn-warning and $tn-success: error, warning and
        success text readable in both themes and under NO_COLOR (tones.py, #139)."""
        variables = super().get_css_variables()
        variables.update(tones(variables))
        self.theme_variables = variables
        return variables

    def notify(self, message: str, *, title: str = "", severity="information",
               timeout: float | None = None, markup: bool = True) -> None:
        """Every notification (widgets' notify comes here too) without control
        characters, right-to-left words in order: messages carry paths, errors
        and config values."""
        super().notify(visual(message), title=visual(title), severity=severity, timeout=timeout, markup=markup)

    def store(self, timeout: float = 10) -> Store:
        """A fresh connection to the bot's state (use it in a `with` block).
        On the UI thread pass a short `timeout`: the screen freezes while it waits."""
        return Store(self.paths.state, timeout)

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="dashboard"):
            with TabPane("Dashboard", id="dashboard"):
                yield DashboardPane()
            with TabPane("Review", id="review"):
                yield ReviewPane()
            with TabPane("Search", id="search"):
                yield SearchPane()
            with TabPane("Settings", id="settings"):
                yield SettingsPane()
        yield Footer()

    def on_mount(self) -> None:
        if self.refresh_seconds > 0:
            self.set_interval(self.refresh_seconds, self._tick)
        self._reload_soon()

    def _active_pane(self):
        tabs = self.query_one(TabbedContent)
        pane = tabs.get_pane(tabs.active) if tabs.active else None
        return pane.children[0] if pane is not None and pane.children else None

    def _tick(self) -> None:
        pane = self._active_pane()
        if pane is not None and getattr(pane, "live", False):
            pane.reload()

    def action_reload(self) -> None:
        self._reload_queued = None   # a queued reload is done by this one
        pane = self._active_pane()
        if pane is not None and hasattr(pane, "reload"):
            pane.reload()

    def action_refresh_tab(self) -> None:
        """ctrl+r: on Settings with unsaved edits, offer to discard them and
        re-read config.json (a plain reload there never throws edits away)."""
        pane = self._active_pane()
        if isinstance(pane, SettingsPane) and pane.dirty:
            pane.action_revert()
        else:
            self.action_reload()

    async def action_quit(self) -> None:
        """Ask first when Settings has unsaved edits."""
        settings = self.query_one(SettingsPane)
        if not settings.dirty:
            self.exit()
            return
        if getattr(self.screen, "quit_question", False):   # a held ctrl+q: one question is enough (#92)
            return

        def done(yes: bool | None) -> None:
            if yes:
                self.exit()

        question = ConfirmModal("Settings has unsaved changes. Quit and lose them?", "Quit")
        question.quit_question = True   # other questions (revert, restart) may still have it on top
        self.push_screen(question, done)

    def action_show(self, tab: str) -> None:
        tabs = self.query_one(TabbedContent)
        if tabs.active == tab:
            # already shown: no tab change brings focus back, so don't send it to the tab bar;
            # the pane's main widget gets it, as when the tab is first shown (#60)
            pane = self._active_pane()
            focus_main = getattr(pane, "focus_main", None)
            if callable(focus_main):
                focus_main()
            self._reload_soon()   # no TabActivated follows: the key still refreshes it
            return
        # Hand focus to the tab bar first: if a widget inside the old tab kept
        # focus, Textual would move it to a sibling in that hidden tab and
        # TabbedContent would switch straight back.
        self.set_focus(self.query_one(Tabs))
        tabs.active = tab   # TabActivated reloads it: once, not twice (each read of a locked file takes time)

    def on_tabbed_content_tab_activated(self, _event: TabbedContent.TabActivated) -> None:
        self._reload_soon()

    def _reload_soon(self) -> None:
        """Reload the shown tab after this refresh. Asked again before it runs (start-up,
        a tab shown at once), it still reloads once: a read of a locked file takes time."""
        now = time.monotonic()
        # Textual runs the callback on the screen on top: if that screen goes away first,
        # the callback is dropped. A request older than a second is taken as lost.
        if self._reload_queued is not None and now - self._reload_queued < RELOAD_LOST_SECONDS:
            return
        self._reload_queued = now
        self.call_after_refresh(self.action_reload)
