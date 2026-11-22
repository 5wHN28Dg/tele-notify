"""The app shell: four tabs, one per screen.

Each screen is a widget in telenotify_tui/screens/ with a `reload()`
method; the app calls it when the tab is shown and every few seconds while
it is visible, so the numbers stay live while the bot runs.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterable
from functools import partial
from typing import ClassVar
from weakref import WeakKeyDictionary

from textual import events
from textual.actions import parse as parse_action
from textual.app import App, ComposeResult, ScreenStackError, SystemCommand
from textual.binding import Binding, BindingsMap, BindingType
from textual.dom import DOMNode
from textual.errors import NoWidget
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Header, TabbedContent, TabPane, Tabs
from textual.widgets._footer import FooterKey

from . import prefs
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


def is_letter_key(key: str) -> bool:
    """A single-letter shortcut (s, p, D, J...): what the letter-keys switch turns off (#140)."""
    return len(key) == 1 and key.isalpha()


class MainScreen(Screen):
    """The screen under the dialogs. Its keys follow two rules:

    - With the focus on the tab bar, the shown tab's own keys still work (and show in the footer
      and the keys panel), as if the focus were in the tab: the Bot box names `s` whatever has the
      focus (PR #137 review run 7). Each tab's keys stay its own: on Review, `s` is still Send.
    - With the letter keys off (Settings, #140), no single-letter key here does anything or is
      shown; the ctrl keys, the number keys, esc, enter, space and the arrows stay. The dialogs are
      screens of their own, so their y / n still work.

    Textual has no public hook for the keys in effect, so this extends Screen._binding_chain, the
    list both the key handling and the footer read (Textual 8.2, pinned below 9 in pyproject)."""

    @property
    def _binding_chain(self) -> list[tuple[DOMNode, BindingsMap]]:
        return self.key_chain(letters=getattr(self.app, "letter_keys", True))

    chains_built = 0   # how many times key_chain ran: the start-up self-check reads it

    def key_chain(self, letters: bool) -> list[tuple[DOMNode, BindingsMap]]:
        """The keys in effect, nearest the focus first; `letters`: keep the single-letter ones."""
        self.chains_built += 1
        chain = super()._binding_chain
        shown = self.tab_from_bar()
        if shown is not None:
            chain.insert(1, (shown, shown._bindings.copy()))
        if not letters:
            for _node, bindings in chain:
                for key in [k for k in bindings.key_to_bindings if is_letter_key(k)]:
                    del bindings.key_to_bindings[key]
        return chain

    def on_key(self, event: events.Key) -> None:
        """With the focus on the tab bar, the shown tab sees the key first, as it does with the focus
        inside it: Review notes when each key was *pressed* (its double-tap guard, #141) and says why
        an early s / k / d / u is ignored (run 9). This runs before the app's bindings do."""
        shown = self.tab_from_bar()
        handler = getattr(shown, "on_key", None)
        if callable(handler):
            handler(event)

    def tab_from_bar(self) -> Widget | None:
        """The shown tab's own widget (its pane) while the app's tab bar has the focus, else None."""
        focused = self.focused
        tabs = focused.parent if isinstance(focused, Tabs) else None
        if isinstance(tabs, TabbedContent) and tabs.parent is self and tabs.active:   # the app's tab bar
            shown = tabs.get_pane(tabs.active).children
            return shown[0] if shown else None
        return None

    def key_nodes(self) -> list[DOMNode]:
        """The nodes whose keys apply where the focus is, nearest first, as key_chain has them but
        before a box that types (an Input) takes the letters: for the command palette."""
        focused = self.focused
        nodes: list[DOMNode] = list(focused.ancestors_with_self) if focused is not None else [self, self.app]
        shown = self.tab_from_bar()
        if shown is not None:
            nodes.insert(1, shown)
        return nodes


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
        self.letter_keys = prefs.load(paths.config).letter_keys   # the screen's own setting (#140)
        self.letter_keys_work = True   # False: the start-up self-check found the switch can't act
        self.letter_keys_problem = ""   # why the last switch wasn't kept for next time, if it wasn't
        self._letters_off_said = False   # "Letter keys are off" said once since they were turned off
        self.refresh_seconds = refresh_seconds
        self._reload_queued: float | None = None   # when a reload was queued (monotonic), until it runs
        self._show_sub_title()

    def _show_sub_title(self) -> None:
        """The header's line: first a mark while the letter keys are off (#140), so it is never cut."""
        off = "letter keys off (ctrl+p) · " if not self.letter_keys else ""
        self.sub_title = off + ("DRY RUN · " if self.paths.dry_run else "") + visual(self.paths.config)

    def get_default_screen(self) -> Screen:
        return MainScreen(id="_default")

    def set_letter_keys(self, on: bool) -> str:
        """Turn the single-letter shortcuts on or off (#140), at once and for next time.
        Returns "" when saved, else why it wasn't (the switch still holds for this run)."""
        if not self.letter_keys_work:
            return prefs.LETTER_KEYS_BROKEN
        self.letter_keys = on
        self._letters_off_said = False
        problem = ""
        try:
            prefs.save(self.paths.config, prefs.Prefs(letter_keys=on))
        except OSError as e:
            problem = prefs.not_saved(self.paths.config, on, e)
            self.log.warning(f"letter keys: {prefs.path_for(self.paths.config)} not written: {e!r}")
        self.letter_keys_problem = problem   # the Settings Screen tab shows it, however it was switched
        self._show_sub_title()
        self._refresh_key_hints()
        return problem

    def _refresh_key_hints(self) -> None:
        """The footer, and the keys each tab names on screen, after the letter keys changed."""
        self.refresh_bindings()
        for pane in self.query("TabPane > *"):
            show = getattr(pane, "show_key_hints", None)
            if callable(show):
                show()

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        """Textual's commands, then every single-letter key in effect where the focus is (on the
        shown tab), so each one's action is also one ctrl+p away; with the letter keys off (#140),
        that is how the actions with no button or other key (Paste a post, Flip...) are reached."""
        yield from super().get_system_commands(screen)
        if not isinstance(screen, MainScreen):
            return
        if self.letter_keys_work:
            turn = "off" if self.letter_keys else "on"
            yield SystemCommand(f"Letter keys: turn {turn}",
                                "single-letter shortcuts such as s, p and y (also in Settings, Screen)",
                                partial(self._letter_keys_from_palette, not self.letter_keys))
        tab = (self.query_one(TabbedContent).active or "").capitalize()   # the tab's name, without its marks
        seen: set[str] = set()
        for node in screen.key_nodes():
            for key, binding in node._bindings:
                if not is_letter_key(key) or key in seen or binding.action in seen:
                    continue
                seen.update((key, binding.action))
                _ns, name, params = parse_action(binding.action)
                if node.check_action(name, params):   # off (or hidden) now: not offered
                    yield SystemCommand(f"{tab}: {binding.description}",
                                        f"the {key} key{'' if self.letter_keys else ' (letter keys are off)'}",
                                        partial(self.run_action, binding.action, node))

    def _letter_keys_from_palette(self, on: bool) -> None:
        """The palette's "Letter keys: turn …": as the switch, and a note if it wasn't kept (run 9)."""
        problem = self.set_letter_keys(on)
        if problem:
            self.notify(problem, severity="warning", timeout=10)

    def key_hint(self, text: str) -> str:
        """`text` (a key named on screen, such as " [f]") while the letter keys are on, else ""."""
        return text if self.letter_keys else ""

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
        self._check_letter_keys()
        if self.refresh_seconds > 0:
            self.set_interval(self.refresh_seconds, self._tick)
        self._reload_soon()

    def _check_letter_keys(self) -> None:
        """The letter-keys switch and the tab bar's keys rest on MainScreen extending a private part
        of Textual. Check that Textual still reads the keys through it; if not, say so (log, and on
        the Settings Screen tab) and leave the letter keys on, rather than a switch that does nothing."""
        # This proves only the path the footer and keys panel read (active_bindings); the key handling
        # reads the same chain in Textual 8.2, which the pin (<8.3, pyproject.toml) holds (run 9).
        screen = self.screen
        before = getattr(screen, "chains_built", 0)
        _ = screen.active_bindings   # what the footer and the key handling both read
        if isinstance(screen, MainScreen) and screen.chains_built > before:
            return
        self.letter_keys_work = False
        self.letter_keys = True
        self._show_sub_title()
        self._refresh_key_hints()   # the Settings switch greys and says why
        self.log.warning(prefs.LETTER_KEYS_BROKEN)

    def on_key(self, event: events.Key) -> None:
        """A letter key that would be a shortcut, pressed while the letter keys are off: say once
        why nothing happened and how to turn them on (#140); boxes that type never get here with
        it in the chain, since a box that takes the letter removes it from the chain first."""
        if self.letter_keys or self._letters_off_said or not is_letter_key(event.key):
            return
        screen = self.screen
        if not isinstance(screen, MainScreen):
            return
        if any(event.key in bindings.key_to_bindings for _n, bindings in screen.key_chain(letters=True)):
            self._letters_off_said = True
            self.notify("Letter keys are off: ctrl+p → Letter keys: turn on", timeout=6)

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
        # the keys in effect depend on the shown tab while the tab bar has the focus (MainScreen),
        # and Textual redraws the footer only when the focus moves: redraw it here (run 8)
        self.refresh_bindings()
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
