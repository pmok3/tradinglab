"""Watchlist-tab mixin for :class:`tradinglab.app.ChartApp`.

Owns the snapshot-driven repaint loop for the Watchlist notebook tab and
the background price-preload helpers.

The Watchlist top-level notebook tab contains a **nested
``ttk.Notebook``** with one sub-tab per *pinned* watchlist (up to
:attr:`WatchlistManager.MAX_PINNED`; default 5). The catalog of named
watchlists can be much larger (~100); pinning is what makes a list
reachable from the UI without opening the Watchlist dialog.

State is initialised in ``ChartApp.__init__`` / ``_build_ui``:

* ``_watchlist_subnotebook``: the nested ttk.Notebook widget
* ``_watchlist_sub_frames: Dict[str, ttk.Frame]``: one frame per pinned name
* ``_watchlist_trees: Dict[str, ttk.Treeview]``: one Treeview per pinned name
* ``_watchlist_sort_by_name: Dict[str, Tuple[Optional[str], bool]]``:
  per-sub-tab click-to-sort state
* ``_watchlist_tree``: alias for the *currently-selected* pinned tree
  (kept so spec-driven smoke tests and ``_apply_theme`` continue to
  work without knowing about the multi-tree layout)
* ``_watchlist_snapshot: Dict[ticker, dict]``: shared ticker data pool;
  one ticker's row values are identical across all sub-tabs it appears in
* ``watchlist_var``: StringVar mirroring the currently-selected sub-tab
  name — updated by the sub-notebook's ``<<NotebookTabChanged>>`` binding
* ``_watchlist_tab_refresh_job``, ``_after_jobs``, ``_watchlists``:
  unchanged

Mixin rules: no ``__init__``, no cooperative ``super()``, no name
collisions. No back-import of ``tradinglab.app``.
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from ..data import DATA_SOURCES
from ..watchlists import (
    DEFAULT_WATCHLIST_NAME as _DEFAULT_WATCHLIST_NAME_CANONICAL,
)
from ..watchlists import (
    DEFAULT_WATCHLIST_TICKERS as _DEFAULT_WATCHLIST_TICKERS_CANONICAL,
)

# Canonical defaults live in :mod:`tradinglab.watchlists` (single
# source of truth). Pre-2026-05 each consumer carried its own copy
# of these constants; if a maintainer updated one they'd silently
# drift. Audit ``default-watchlist-fresh``.
_DEFAULT_WATCHLIST_NAME = _DEFAULT_WATCHLIST_NAME_CANONICAL
_DEFAULT_WATCHLIST_TICKERS = list(_DEFAULT_WATCHLIST_TICKERS_CANONICAL)

# Sentinel sub-tab labels. The "+" trailing tab is a quick-add affordance
# for creating a new watchlist without going through the Watchlists
# dialog. The "(no pins)" placeholder is rendered when zero watchlists
# are pinned. Both are reserved names that the user cannot use.
_ADD_TAB_LABEL = "+"
_EMPTY_TAB_LABEL = "(no pins)"
_RESERVED_WATCHLIST_NAMES = {_ADD_TAB_LABEL, _EMPTY_TAB_LABEL}

_WL_COLUMNS: tuple[tuple[str, int, str], ...] = (
    ("ticker", 80, "w"), ("last", 80, "center"),
    ("change", 80, "center"), ("change_pct", 70, "center"),
    # "Next Earn" surfaces the nearest forward earnings date from
    # ``app._events_cache`` as a relative "T-N AMC"-style cue. Missing
    # bundles render as a blank cell; bundles with no forward earnings
    # in the lookahead window also render blank. Sort is by ascending
    # trading-days-until with blanks trailing — the same blanks-at-
    # bottom convention used by the price columns. See plan.md
    # decision 16.
    ("next_earn", 90, "center"),
)


# Trading-day approximation: 5/7 of the calendar-day delta, ceiling.
# Same heuristic as :func:`events.gating._approx_trading_days_between` —
# duplicated here so the watchlist column avoids a circular import on
# the GUI module path during early app boot.
_MS_PER_DAY = 86_400_000


def _format_next_earn(bundle: object, *, now_ms: int) -> tuple[str, int]:
    """Return ``(display_string, trading_days_until)`` for sort.

    ``trading_days_until = 10**9`` is the missing-data sentinel — sorts
    last in ascending order, which matches the blanks-at-bottom rule
    enforced by :meth:`_watchlist_sort_key`. Empty display strings
    indicate "no data" or "no forward earnings in the lookahead
    window".
    """
    if bundle is None:
        return "", 10**9
    earnings = getattr(bundle, "earnings", None)
    if not earnings:
        return "", 10**9
    # Find the first earnings record whose ts is strictly > now_ms.
    # The bundle is sorted ascending by ts (post-init invariant).
    forward = None
    for r in earnings:
        ts = int(getattr(r, "ts", 0) or 0)
        if ts > now_ms:
            forward = r
            break
    if forward is None:
        return "", 10**9
    ts_ms = int(getattr(forward, "ts", 0) or 0)
    calendar_days = max(0, (ts_ms - now_ms + _MS_PER_DAY - 1) // _MS_PER_DAY)
    # Floor-div-then-ceil approximation of trading days.
    trading = int(max(0, (calendar_days * 5 + 6) // 7))
    when = str(getattr(forward, "when", "") or "").strip()
    if when:
        return f"T-{trading} {when}", trading
    return f"T-{trading}", trading


class WatchlistTabMixin:
    """Watchlist tab repaint + preload helpers."""

    _WATCHLIST_COL_LABELS = {
        "ticker": "Ticker",
        "last": "Last",
        "change": "Change",
        "change_pct": "Change Pct",
        "next_earn": "Next Earn",
    }

    def _ensure_default_watchlist(self) -> None:
        """Create a starter watchlist on first run, if the store is empty.

        Also guarantees at least one pinned watchlist so the UI isn't
        empty on first launch. Migration from schema v1 is handled by
        :class:`WatchlistManager`, which seeds the first entry as pinned
        when no pins are recorded.
        """
        mgr = self._watchlists
        if mgr is None:
            return
        try:
            if not mgr.list_names():
                mgr.create(_DEFAULT_WATCHLIST_NAME,
                           list(_DEFAULT_WATCHLIST_TICKERS))
            if not mgr.pinned_names() and mgr.list_names():
                try:
                    mgr.pin(mgr.list_names()[0])
                except Exception:  # noqa: BLE001
                    pass
            # The default-seeding mutations above are internal
            # bootstrapping, not user changes. Clear the dirty flag
            # so the quit prompt doesn't fire on a fresh launch
            # where the user never touched watchlists.
            if mgr.loaded_path() is None:
                mgr._dirty = False
        except Exception:  # noqa: BLE001
            pass

    # ---- subnotebook build / rebuild ----------------------------------
    def _build_watchlist_container(self, parent: tk.Misc) -> tk.Widget:
        """Create the nested Notebook that hosts pinned sub-tabs.

        Called once from ``_build_ui``. Actual pinned sub-tab contents
        are built by :meth:`_rebuild_watchlist_subtabs`, which can be
        re-run any time the pinned set changes (dialog close, context
        menu action, etc.).
        """
        container = ttk.Frame(parent)
        sub = ttk.Notebook(container)
        sub.pack(fill=tk.BOTH, expand=True)
        self._watchlist_subnotebook = sub
        self._watchlist_sub_frames: dict[str, ttk.Frame] = {}
        self._watchlist_trees: dict[str, ttk.Treeview] = {}
        self._watchlist_sort_by_name: dict[
            str, tuple[str | None, bool]] = {}
        # Placeholder frame for the empty-pin state.
        self._watchlist_empty_frame: ttk.Frame | None = None
        sub.bind("<<NotebookTabChanged>>",
                 self._on_watchlist_subtab_changed)
        sub.bind("<Button-3>", self._on_watchlist_subtab_right_click)
        self._rebuild_watchlist_subtabs()
        return container

    def _rebuild_watchlist_subtabs(self) -> None:
        """Tear down and rebuild pinned sub-tabs from the manager's
        pinned list. Preserves current sub-tab selection when possible.
        Re-applies theme so newly-created Treeviews inherit bull/bear
        colors.
        """
        sub = getattr(self, "_watchlist_subnotebook", None)
        if sub is None:
            return
        mgr = getattr(self, "_watchlists", None)
        pinned: list[str] = mgr.pinned_names() if mgr is not None else []

        current = ""
        try:
            current = self.watchlist_var.get()
        except Exception:  # noqa: BLE001
            pass

        for child in list(sub.tabs()):
            try:
                sub.forget(child)
            except Exception:  # noqa: BLE001
                pass
        for frame in list(self._watchlist_sub_frames.values()):
            try:
                frame.destroy()
            except Exception:  # noqa: BLE001
                pass
        if self._watchlist_empty_frame is not None:
            try:
                self._watchlist_empty_frame.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._watchlist_empty_frame = None
        self._watchlist_sub_frames.clear()
        self._watchlist_trees.clear()
        # Prune stale sort-state entries.
        if mgr is not None:
            existing = set(mgr.list_names())
            self._watchlist_sort_by_name = {
                k: v for k, v in self._watchlist_sort_by_name.items()
                if k in existing}

        if not pinned:
            self._build_watchlist_empty_state()
            # Append the quick-add "+" tab even in the empty state so
            # the user can create their first watchlist with one click.
            self._add_plus_subtab()
            self._sync_watchlist_tree_alias()
            return

        for name in pinned:
            frame = ttk.Frame(sub)
            tree = self._make_watchlist_tree(frame, name)
            tree.pack(fill=tk.BOTH, expand=True)
            self._watchlist_sub_frames[name] = frame
            self._watchlist_trees[name] = tree
            sub.add(frame, text=name)

        # Trailing quick-add tab (after the last pinned list).
        self._add_plus_subtab()

        target = current if current in pinned else pinned[0]
        try:
            self.watchlist_var.set(target)
        except Exception:  # noqa: BLE001
            pass
        try:
            sub.select(self._watchlist_sub_frames[target])
        except Exception:  # noqa: BLE001
            pass

        # Re-apply theme so post-init rebuilds get bull/bear tag colors.
        try:
            self._apply_theme()
        except Exception:  # noqa: BLE001
            pass

        for name in pinned:
            try:
                self._populate_watchlist_tab(name)
            except Exception:  # noqa: BLE001
                pass
        self._sync_watchlist_tree_alias()
        # Newly-pinned lists may carry tickers we've never fetched. Kick
        # off the snapshot preload so their rows fill in without forcing
        # the user to click each one.
        self._kick_watchlist_preloads()

    def _make_watchlist_tree(
        self, parent: tk.Misc, name: str,
    ) -> ttk.Treeview:
        """Build one Treeview for a pinned watchlist sub-tab."""
        tree = ttk.Treeview(
            parent,
            columns=tuple(c for c, _w, _a in _WL_COLUMNS),
            show="headings", height=20,
        )
        for col, w, anchor in _WL_COLUMNS:
            tree.heading(
                col, text=col.replace("_", " ").title(),
                command=lambda c=col, n=name: self._sort_watchlist_by(n, c),
            )
            tree.column(col, width=w, anchor=anchor)
        tree.bind("<Double-1>", self._on_watchlist_double)
        # Widget-level Space binding (highest priority — fires before
        # the Treeview class binding which otherwise toggles the
        # current selection and returns "break", swallowing the event
        # before it reaches our app-level handler). Returns "break"
        # itself so no further bindings fire.
        def _space_cycle(_e):
            try:
                self._cycle_watchlist_ticker()
            except Exception:  # noqa: BLE001
                pass
            return "break"
        tree.bind("<KeyPress-space>", _space_cycle)
        from ..constants import BEAR_COLOR, BULL_COLOR  # late import
        tree.tag_configure("bull", foreground=BULL_COLOR)
        tree.tag_configure("bear", foreground=BEAR_COLOR)
        return tree

    def _build_watchlist_empty_state(self) -> None:
        """Render a placeholder when 0 pinned watchlists.

        The placeholder explains how to add one (right-click + the
        Watchlists dialog) and is paired with a trailing "+" tab that
        invokes the quick-add flow on click.
        """
        sub = self._watchlist_subnotebook
        frame = ttk.Frame(sub)
        msg = ttk.Label(
            frame,
            text=("No pinned watchlists.\n"
                  "Click the '+' tab to add one, or open the Watchlists "
                  "dialog."),
            justify="center",
        )
        msg.pack(pady=(40, 12))
        btn = ttk.Button(
            frame, text="Open Watchlists…",
            command=self._open_watchlist_dialog,
        )
        btn.pack()
        sub.add(frame, text=_EMPTY_TAB_LABEL)
        self._watchlist_empty_frame = frame

    def _add_plus_subtab(self) -> None:
        """Append the trailing "+" sub-tab.

        Selecting this sub-tab is interpreted as "load an existing
        watchlist into a pinned slot" — :meth:`_on_watchlist_subtab_changed`
        notices the sentinel label and routes to
        :meth:`_on_add_watchlist_subtab`, which shows a picker of
        watchlists that exist in the manager but are not currently
        pinned. Brand-new watchlists are still created via the
        "Watchlists" button (which opens the full manager dialog).

        Hidden only when the manager is at ``MAX_PINNED`` capacity
        (no room to pin another). When zero unpinned candidates
        exist, the "+" tab is still rendered as a discoverable
        affordance — clicking it surfaces a status hint pointing the
        user at the Watchlists button.
        """
        sub = getattr(self, "_watchlist_subnotebook", None)
        mgr = getattr(self, "_watchlists", None)
        if sub is None or mgr is None:
            return
        if len(mgr.pinned_names()) >= mgr.MAX_PINNED:
            self._watchlist_plus_frame = None
            return
        plus_frame = ttk.Frame(sub)
        sub.add(plus_frame, text=_ADD_TAB_LABEL)
        self._watchlist_plus_frame = plus_frame

    # ---- subnotebook events -------------------------------------------
    def _on_watchlist_subtab_changed(self, _event=None) -> None:
        """Mirror the selected sub-tab into ``watchlist_var`` + repaint.

        Sentinel labels are intercepted: ``"+"`` triggers the quick-add
        flow; ``"(no pins)"`` is a non-interactive placeholder.
        """
        sub = getattr(self, "_watchlist_subnotebook", None)
        if sub is None:
            return
        try:
            idx = sub.index(sub.select())
            label = sub.tab(idx, "text")
        except Exception:  # noqa: BLE001
            return
        if label == _EMPTY_TAB_LABEL:
            return
        if label == _ADD_TAB_LABEL:
            self._on_add_watchlist_subtab()
            return
        try:
            self.watchlist_var.set(label)
        except Exception:  # noqa: BLE001
            pass
        self._sync_watchlist_tree_alias()
        try:
            self._populate_watchlist_tab(label)
        except Exception:  # noqa: BLE001
            pass

    def _on_add_watchlist_subtab(self) -> None:
        """Pin an existing watchlist via the trailing "+" sub-tab.

        Shows a picker of watchlists that exist in the manager but are
        not currently pinned. On selection, pins it + rebuilds the sub-
        tab strip + selects the new tab. On cancel / no-candidates /
        cap-reached / error, reverts the sub-tab selection so the user
        isn't left looking at the "+" tab.

        To **create** a brand-new watchlist, use the "Watchlists"
        button in the toolbar (opens the full manager dialog). The "+"
        sub-tab is purely a quick-load affordance for already-defined
        lists — when every watchlist is already pinned (no unpinned
        candidates remain) the "+" click also opens the manager so
        the user has a one-click path to create the next list rather
        than a dead-end warning.

        Reentrancy-guarded: the picker dialog runs a nested event
        loop; without the guard, repeated focus events on "+" could
        open stacked dialogs.
        """
        if getattr(self, "_adding_watchlist", False):
            return
        self._adding_watchlist = True
        # Capture the previous tab so we can revert on cancel/error.
        prev = ""
        try:
            prev = self.watchlist_var.get() or ""
        except Exception:  # noqa: BLE001
            prev = ""
        try:
            mgr = getattr(self, "_watchlists", None)
            if mgr is None:
                self._revert_subtab_selection(prev)
                return
            # Capacity check up front so we don't show a picker only to
            # fail on pin().
            if len(mgr.pinned_names()) >= mgr.MAX_PINNED:
                try:
                    self._status.warn(
                        f"Cannot pin more than {mgr.MAX_PINNED} watchlists "
                        "— unpin one first")
                except Exception:  # noqa: BLE001
                    pass
                self._revert_subtab_selection(prev)
                return
            pinned = set(mgr.pinned_names())
            candidates = [n for n in mgr.list_names()
                          if n not in pinned
                          and n not in _RESERVED_WATCHLIST_NAMES]
            if not candidates:
                try:
                    self._status.warn(
                        "No unpinned watchlists to load — opening the "
                        "Watchlists manager so you can create one")
                except Exception:  # noqa: BLE001
                    pass
                self._revert_subtab_selection(prev)
                # Hand the user off to the manager dialog where they
                # can create a new watchlist or unpin one. The manual
                # status hint above lingers in case the dialog fails
                # to open for any reason.
                try:
                    self._open_watchlist_dialog()
                except Exception:  # noqa: BLE001
                    pass
                return
            try:
                name = self._prompt_pick_unpinned_watchlist(candidates)
            except Exception:  # noqa: BLE001
                name = None
            if not name:
                self._revert_subtab_selection(prev)
                return
            # Defence-in-depth: only allow names from the candidate set.
            if name not in candidates:
                self._revert_subtab_selection(prev)
                return
            try:
                mgr.pin(name)
            except Exception as exc:  # noqa: BLE001
                try:
                    self._status.error(f"Failed to load watchlist: {exc}")
                except Exception:  # noqa: BLE001
                    pass
                self._revert_subtab_selection(prev)
                return
            self._rebuild_watchlist_subtabs()
            try:
                self.watchlist_var.set(name)
                frame = self._watchlist_sub_frames.get(name)
                if frame is not None:
                    self._watchlist_subnotebook.select(frame)
            except Exception:  # noqa: BLE001
                pass
            try:
                self._status.info(f"Loaded watchlist '{name}'")
            except Exception:  # noqa: BLE001
                pass
        finally:
            self._adding_watchlist = False

    def _prompt_pick_unpinned_watchlist(
        self, names: list[str]
    ) -> str | None:
        """Modal listbox picker. Returns the selected name or None.

        Exposed as its own method so smoke tests can stub it out.
        ``names`` is expected to be non-empty (caller checks).
        """
        dlg = tk.Toplevel(self)
        dlg.title("Load watchlist")
        # Match the active palette: tk.Toplevel + tk.Listbox are not
        # ttk widgets so the theme controller's TTK sweep does not
        # reach them — see ``watchlist-popup-theme`` audit comment on
        # ``_current_menu_colors``.
        try:
            theme = getattr(self._theme_ctrl, "theme", None) or {}
            win_bg = theme.get("win_bg", "#f0f0f0")
            tree_bg = theme.get("tree_bg", "#ffffff")
            tree_fg = theme.get("tree_fg", "#111111")
            spine = theme.get("spine", "#888888")
            dlg.configure(background=win_bg)
        except tk.TclError:
            win_bg = "#f0f0f0"
            tree_bg = "#ffffff"
            tree_fg = "#111111"
            spine = "#888888"
        try:
            dlg.transient(self)
        except Exception:  # noqa: BLE001
            pass
        dlg.resizable(False, False)
        # Geometry persistence (position only — fixed size).
        try:
            from .geometry_store import attach_persistent_geometry
            attach_persistent_geometry(dlg, "dlg.load_watchlist", "320x300")
        except tk.TclError:
            pass
        ttk.Label(
            dlg, text="Select an existing watchlist to load:"
        ).pack(padx=12, pady=(12, 6), anchor="w")
        height = min(12, max(4, len(names)))
        lb = tk.Listbox(
            dlg, height=height, exportselection=False, activestyle="dotbox",
            background=tree_bg, foreground=tree_fg,
            selectbackground=spine, selectforeground=tree_fg)
        for n in names:
            lb.insert("end", n)
        lb.selection_set(0)
        lb.pack(padx=12, pady=4, fill="both", expand=True)

        result: dict[str, str | None] = {"name": None}

        def _ok(_event=None):
            sel = lb.curselection()
            if sel:
                result["name"] = lb.get(sel[0])
            try:
                dlg.destroy()
            except Exception:  # noqa: BLE001
                pass

        def _cancel(_event=None):
            try:
                dlg.destroy()
            except Exception:  # noqa: BLE001
                pass

        btns = ttk.Frame(dlg)
        btns.pack(padx=12, pady=(6, 12), fill="x")
        ttk.Button(btns, text="Load", command=_ok).pack(side="right")
        ttk.Button(
            btns, text="Cancel", command=_cancel
        ).pack(side="right", padx=(0, 6))
        lb.bind("<Double-Button-1>", _ok)
        lb.bind("<Return>", _ok)
        dlg.bind("<Escape>", _cancel)
        dlg.protocol("WM_DELETE_WINDOW", _cancel)
        try:
            dlg.grab_set()
        except Exception:  # noqa: BLE001
            pass
        lb.focus_set()
        try:
            self.wait_window(dlg)
        except Exception:  # noqa: BLE001
            pass
        return result["name"]

    def _revert_subtab_selection(self, prev_name: str) -> None:
        """Restore sub-tab selection to ``prev_name`` (or first pinned).

        Used after the "+" tab is clicked but the add flow doesn't
        ultimately create a new watchlist (cancel, dup, error). Without
        this, the user would be left staring at the empty "+" tab.
        """
        sub = getattr(self, "_watchlist_subnotebook", None)
        frames = getattr(self, "_watchlist_sub_frames", None) or {}
        if sub is None:
            return
        target_frame = frames.get(prev_name) if prev_name else None
        if target_frame is None and frames:
            # Fall back to the first pinned tab if the previous name is
            # gone (e.g., it got unpinned between tab-changes).
            target_frame = next(iter(frames.values()))
        if target_frame is None:
            # No pinned tabs at all — fall back to the empty placeholder.
            target_frame = getattr(self, "_watchlist_empty_frame", None)
        if target_frame is None:
            return
        try:
            sub.select(target_frame)
        except Exception:  # noqa: BLE001
            pass

    def _sync_watchlist_tree_alias(self) -> None:
        """Point ``_watchlist_tree`` at the currently-selected tree.

        Smoke tests and ``_apply_theme`` treat it as *the* watchlist
        tree; this alias keeps backward compatibility with the single-
        tree layout.
        """
        name = ""
        try:
            name = self.watchlist_var.get() or ""
        except Exception:  # noqa: BLE001
            pass
        tree = self._watchlist_trees.get(name)
        if tree is None and self._watchlist_trees:
            tree = next(iter(self._watchlist_trees.values()))
        self._watchlist_tree = tree  # type: ignore[assignment]

    def _current_menu_colors(self) -> dict[str, str]:
        """Return a kwargs dict for theming tk.Menu / tk.Toplevel popups
        from the currently-active palette.

        Watchlist owns two on-demand classic Tk widgets that are *not*
        registered in ``_menubar_submenus`` (so the theme controller's
        menubar sweep skips them): the subtab right-click context menu
        (``_on_watchlist_subtab_right_click``) and the "Load watchlist"
        Toplevel picker (``_pick_watchlist_name``). Both are created on
        demand and discarded after use, so the simplest correct fix is
        to colour them at construction time from the current theme. This
        helper centralises that lookup so both call-sites stay in sync.
        Audit ``watchlist-popup-theme``.
        """
        theme = getattr(self._theme_ctrl, "theme", None) or {}
        bg = theme.get("win_bg", "#f0f0f0")
        fg = theme.get("text", "#111111")
        return dict(
            background=bg,
            foreground=fg,
            activebackground=fg,
            activeforeground=bg,
            selectcolor=fg,
            disabledforeground=theme.get("text_disabled", fg),
        )

    def _on_watchlist_subtab_right_click(self, event) -> None:
        """Pop up a context menu on the sub-tab strip (Unpin / Move)."""
        sub = getattr(self, "_watchlist_subnotebook", None)
        mgr = getattr(self, "_watchlists", None)
        if sub is None or mgr is None:
            return
        try:
            idx = sub.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        pinned = mgr.pinned_names()
        if idx < 0 or idx >= len(pinned):
            return
        name = pinned[idx]
        menu = tk.Menu(self, tearoff=0, **self._current_menu_colors())
        menu.add_command(
            label=f"Unpin '{name}'",
            command=lambda n=name: self._unpin_watchlist(n))
        menu.add_command(
            label="Move left",
            state=(tk.NORMAL if idx > 0 else tk.DISABLED),
            command=lambda n=name: self._move_pinned_watchlist(n, -1))
        menu.add_command(
            label="Move right",
            state=(tk.NORMAL if idx < len(pinned) - 1 else tk.DISABLED),
            command=lambda n=name: self._move_pinned_watchlist(n, +1))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _unpin_watchlist(self, name: str) -> None:
        mgr = getattr(self, "_watchlists", None)
        if mgr is None:
            return
        mgr.unpin(name)
        self._rebuild_watchlist_subtabs()

    def _move_pinned_watchlist(self, name: str, delta: int) -> None:
        mgr = getattr(self, "_watchlists", None)
        if mgr is None:
            return
        pinned = mgr.pinned_names()
        if name not in pinned:
            return
        i = pinned.index(name)
        j = i + delta
        if j < 0 or j >= len(pinned):
            return
        pinned[i], pinned[j] = pinned[j], pinned[i]
        try:
            mgr.reorder_pins(pinned)
        except Exception:  # noqa: BLE001
            return
        self._rebuild_watchlist_subtabs()

    # ---- click-to-sort (per-sub-tab) ----------------------------------
    def _sort_watchlist_by(self, name: str, col: str) -> None:
        """Toggle sort on ``col`` for pinned sub-tab ``name``."""
        current = self._watchlist_sort_by_name.get(name, (None, False))
        current_col, reverse = current
        if current_col == col:
            self._watchlist_sort_by_name[name] = (col, not reverse)
        else:
            self._watchlist_sort_by_name[name] = (col, False)
        self._populate_watchlist_tab(name)

    def _watchlist_sort_key(self, col: str, ticker: str, snap: dict):
        """Return a (is_missing, value) key so blanks always trail."""
        if col == "ticker":
            return (False, ticker.upper())
        if col == "last":
            v = snap.get("last")
        elif col == "change":
            v = snap.get("change_1d", snap.get("chg"))
        elif col == "change_pct":
            v = snap.get("pct_1d", snap.get("pct"))
        elif col == "next_earn":
            # Sort by ascending trading-days-until-next-earnings. The
            # missing-data sentinel from :func:`_format_next_earn` is
            # ``10**9`` which would sort last in ascending — but we
            # also want blanks to trail in *descending*, hence the
            # (is_missing, value) pair convention. Treat any value
            # >= 10**8 as missing.
            try:
                bundle = self._events_cache.get(ticker.upper())
            except AttributeError:
                bundle = None
            import time as _time
            now_ms = int(_time.time() * 1000)
            _, td = _format_next_earn(bundle, now_ms=now_ms)
            if td >= 10**8:
                return (True, 0.0)
            return (False, float(td))
        else:
            v = None
        if isinstance(v, (int, float)):
            return (False, float(v))
        return (True, 0.0)

    # ---- repaint ------------------------------------------------------
    def _populate_watchlist_tab(self, name: str | None = None) -> None:
        """Repaint the Treeview for pinned watchlist ``name`` (or the
        currently-selected sub-tab when ``name`` is ``None``).

        Uses 1d-pinned ``change_1d`` / ``pct_1d`` keys so Change columns
        match broker day-over-day change regardless of chart interval.
        Rows are tagged ``bull`` / ``bear`` based on ``change_1d`` sign.
        """
        if name is None:
            try:
                name = self.watchlist_var.get()
            except Exception:  # noqa: BLE001
                name = ""
        tree = self._watchlist_trees.get(name) if name else None
        if tree is None:
            return
        try:
            for iid in tree.get_children():
                tree.delete(iid)
        except Exception:  # noqa: BLE001
            return

        tickers = list(self._watchlist_tickers(name))
        sort_col, reverse = self._watchlist_sort_by_name.get(
            name, (None, False))
        if sort_col is not None:
            # Partition first: non-missing rows sort in either direction;
            # missing rows always trail (blanks-at-bottom semantics).
            # Using a single sorted-with-negated-key pass mishandles
            # string comparisons where one symbol is a prefix of another
            # (e.g. "A" vs "AA": negated tuple `(-65,)` < `(-65,-65)`,
            # so reverse-sort produced the same visible order).
            keyed = [
                (self._watchlist_sort_key(
                    sort_col, t, self._watchlist_snapshot.get(t, {})), t)
                for t in tickers
            ]
            non_missing = [(k[1], t) for k, t in keyed if not k[0]]
            missing = [t for k, t in keyed if k[0]]
            non_missing.sort(key=lambda kv: kv[0], reverse=reverse)
            tickers = [t for _v, t in non_missing] + missing

        try:
            for c, label in self._WATCHLIST_COL_LABELS.items():
                arrow = ""
                if c == sort_col:
                    arrow = "  ▼" if reverse else "  ▲"
                tree.heading(c, text=label + arrow)
        except Exception:  # noqa: BLE001
            pass

        for t in tickers:
            snap = self._watchlist_snapshot.get(t, {})
            last = snap.get("last")
            chg = snap.get("change_1d", snap.get("chg"))
            pct = snap.get("pct_1d", snap.get("pct"))
            last_s = f"{last:,.2f}" if isinstance(last, (int, float)) else ""
            chg_s = f"{chg:+,.2f}" if isinstance(chg, (int, float)) else ""
            pct_s = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else ""
            # Next-earn column: lookup the cached bundle (if any) and
            # format. The cache is populated by :meth:`_load_events_async`
            # on every chart load and proactively for watchlist
            # tickers via :meth:`_preload_watchlist_events`.
            try:
                bundle = self._events_cache.get(t.upper())
            except AttributeError:
                bundle = None
            import time as _time
            now_ms = int(_time.time() * 1000)
            next_earn_s, _td = _format_next_earn(bundle, now_ms=now_ms)
            tag = ()
            if isinstance(chg, (int, float)):
                tag = ("bull",) if chg >= 0 else ("bear",)
            try:
                tree.insert("", "end",
                            values=(t, last_s, chg_s, pct_s, next_earn_s),
                            tags=tag)
            except Exception:  # noqa: BLE001
                pass

    def _populate_all_watchlist_tabs(self) -> None:
        """Repaint every pinned sub-tab. Used by the debounced refresh
        path so background preload writes reach whichever sub-tab is
        visible *and* keep the others current.
        """
        for name in list(self._watchlist_trees.keys()):
            try:
                self._populate_watchlist_tab(name)
            except Exception:  # noqa: BLE001
                pass

    def _schedule_watchlist_tab_refresh(self, delay_ms: int = 60) -> None:
        """Debounce repaints: coalesce worker completions into a single
        ``after(delay_ms, _populate_all_watchlist_tabs)`` callback.
        """
        if self._watchlist_tab_refresh_job is not None:
            return
        try:
            job = self._track_after(
                int(delay_ms), self._run_watchlist_tab_refresh)
        except tk.TclError:
            return
        self._watchlist_tab_refresh_job = job

    def _run_watchlist_tab_refresh(self) -> None:
        self._watchlist_tab_refresh_job = None
        try:
            self._populate_all_watchlist_tabs()
        except Exception:  # noqa: BLE001
            pass

    # ---- ticker helpers ----------------------------------------------
    def _watchlist_tickers(self, name: str | None = None) -> list[str]:
        """Return ticker list for pinned watchlist ``name`` (current
        sub-tab when ``name`` is ``None``).
        """
        mgr = getattr(self, "_watchlists", None)
        if mgr is None:
            return []
        try:
            if name is None:
                try:
                    name = self.watchlist_var.get() or _DEFAULT_WATCHLIST_NAME
                except Exception:  # noqa: BLE001
                    name = _DEFAULT_WATCHLIST_NAME
            wl = mgr.get(name)
            if wl is None and mgr.pinned_names():
                wl = mgr.get(mgr.pinned_names()[0])
            elif wl is None and mgr.list_names():
                wl = mgr.get(mgr.list_names()[0])
            return list(wl.tickers) if wl else []
        except Exception:  # noqa: BLE001
            return []

    def _pinned_ticker_union(self) -> list[str]:
        """Return deduped union of tickers across all pinned watchlists.

        Used by the preload pipeline so a ticker shared by several
        pinned lists is fetched once, not N times.
        """
        mgr = getattr(self, "_watchlists", None)
        if mgr is None:
            return []
        seen: list[str] = []
        dedup: set = set()
        for name in mgr.pinned_names():
            wl = mgr.get(name)
            if wl is None:
                continue
            for t in wl.tickers:
                if t not in dedup:
                    dedup.add(t)
                    seen.append(t)
        return seen

    # ---- event handlers ----------------------------------------------
    def _ensure_active_watchlist_for_cycle(self) -> str | None:
        """Guarantee a non-empty active pinned watchlist for Space-cycle.

        Returns the name of the watchlist whose tickers should be used,
        or ``None`` if even the Default fallback has no tickers (e.g.,
        the user manually emptied it). When called and no watchlist is
        currently pinned, the Default list is created (if missing),
        pinned, made the active sub-tab, and the watchlist sub-notebook
        is rebuilt so the visible tab matches the cycle target.
        """
        mgr = getattr(self, "_watchlists", None)
        if mgr is None:
            return None
        # Active sub-tab name (mirrors _on_watchlist_subtab_changed).
        try:
            current = self.watchlist_var.get() or ""
        except Exception:  # noqa: BLE001
            current = ""

        pinned = mgr.pinned_names()
        if pinned:
            # User has at least one pinned list — honor whichever sub-tab
            # is currently visible (or fall back to the first pinned).
            if current in pinned:
                return current
            return pinned[0]

        # No pinned watchlists at all. Ensure Default exists, pin it,
        # and switch the visible sub-tab.
        try:
            if _DEFAULT_WATCHLIST_NAME not in mgr.list_names():
                mgr.create(_DEFAULT_WATCHLIST_NAME,
                           list(_DEFAULT_WATCHLIST_TICKERS))
            try:
                mgr.pin(_DEFAULT_WATCHLIST_NAME)
            except Exception:  # noqa: BLE001
                pass
            try:
                self.watchlist_var.set(_DEFAULT_WATCHLIST_NAME)
            except Exception:  # noqa: BLE001
                pass
            self._rebuild_watchlist_subtabs()
        except Exception:  # noqa: BLE001
            return None
        return _DEFAULT_WATCHLIST_NAME

    def _select_watchlist_subtab(self, name: str) -> None:
        """Bring the given pinned watchlist's sub-tab into view.

        Surfaces the outer "Watchlist" notebook tab and selects the
        nested sub-tab whose label matches ``name``. Used by Space-cycle
        so the user can see the list being iterated through. Silently
        no-ops if the sub-tab doesn't exist (e.g., name was unpinned
        between resolution and selection).
        """
        # Outer notebook: pop the Watchlist tab into view.
        outer = getattr(self, "_notebook", None)
        wl_frame = getattr(self, "_watchlist_outer_frame", None)
        if outer is not None and wl_frame is not None:
            try:
                outer.select(wl_frame)
            except Exception:  # noqa: BLE001
                pass
        # Inner sub-notebook: select the matching sub-tab.
        sub = getattr(self, "_watchlist_subnotebook", None)
        frames = getattr(self, "_watchlist_sub_frames", None) or {}
        target_frame = frames.get(name)
        if sub is not None and target_frame is not None:
            try:
                sub.select(target_frame)
            except Exception:  # noqa: BLE001
                pass
        # Mirror into the StringVar so any subsequent cycle resolves
        # via _ensure_active_watchlist_for_cycle to the same name.
        try:
            self.watchlist_var.set(name)
        except Exception:  # noqa: BLE001
            pass

    def _cycle_watchlist_ticker(self) -> bool:
        """Advance to the next ticker in the active watchlist.

        Triggered by the Space key. Targets the ``_last_clicked_slot``
        (defaults to ``"primary"`` when no chart has been clicked yet);
        falls back to primary when the chosen slot is ``"compare"`` but
        compare mode is off.

        Index logic is stateless — each press computes the current
        ticker's position in the active watchlist (case-insensitive)
        and advances by one with modulo wrap-around. Tickers not in
        the list start at index 0. No-ops on empty watchlists or
        single-ticker lists where the cycle would re-load the same
        symbol.

        When no watchlist is pinned, the Default list is auto-pinned
        and made the active sub-tab (via
        ``_ensure_active_watchlist_for_cycle``) so a single Space press
        produces a visible cycle even on first-launch / unpinned state.

        Honors the drill-down day lock: when zoomed into a 5m day,
        cycling preserves that day on the new ticker via
        ``_reload_preserving_drilldown`` (with latest-day fallback),
        mirroring watchlist double-click and click-to-type semantics.

        Returns True when a reload was issued, False otherwise.

        Status bar feedback is set on every code path (success, no-op,
        empty list) so the user gets visible confirmation that the
        Space-key handler fired — useful when diagnosing focus /
        binding issues in the real GUI.
        """
        active_name = self._ensure_active_watchlist_for_cycle()
        if not active_name:
            try:
                self._status.warn("Watchlist cycle: no watchlist available")
            except Exception:  # noqa: BLE001
                pass
            return False
        # Surface the active watchlist's sub-tab so the user sees what
        # they're cycling through (spec: Space switches the right-side
        # tab to match the iterating list).
        try:
            self._select_watchlist_subtab(active_name)
        except Exception:  # noqa: BLE001
            pass
        try:
            tickers = self._watchlist_tickers(active_name)
        except Exception:  # noqa: BLE001
            tickers = []
        if not tickers:
            try:
                self._status.warn(
                    f"Watchlist cycle: '{active_name}' is empty")
            except Exception:  # noqa: BLE001
                pass
            return False
        # Resolve target slot.
        slot = getattr(self, "_last_clicked_slot", "primary") or "primary"
        if slot == "compare":
            try:
                if not bool(self.compare_var.get()):
                    slot = "primary"
            except Exception:  # noqa: BLE001
                slot = "primary"
        # Current ticker on that slot.
        try:
            if slot == "compare":
                current = (self.compare_ticker_var.get() or "").strip().upper()
            else:
                current = (self.ticker_var.get() or "").strip().upper()
        except Exception:  # noqa: BLE001
            current = ""
        # Locate current ticker (case-insensitive); not found → start at 0.
        upper_list = [t.strip().upper() for t in tickers]
        try:
            idx = upper_list.index(current)
            next_ticker = upper_list[(idx + 1) % len(upper_list)]
        except ValueError:
            next_ticker = upper_list[0]
        if next_ticker == current:
            try:
                self._status.info(
                    f"Watchlist cycle: '{active_name}' has only "
                    f"'{current}' — nothing to cycle to")
            except Exception:  # noqa: BLE001
                pass
            return False
        # Apply + reload (with drill-down preservation if active).
        try:
            if slot == "compare":
                self.compare_ticker_var.set(next_ticker)
            else:
                self.ticker_var.set(next_ticker)
        except Exception:  # noqa: BLE001
            return False
        try:
            self._status.info(
                f"Watchlist cycle ({slot}): {current or '∅'} → "
                f"{next_ticker}  [{active_name}]")
        except Exception:  # noqa: BLE001
            pass
        try:
            if (getattr(self, "_drilldown_day", None) is not None
                    and self.interval_var.get() == "5m"):
                self._reload_preserving_drilldown(self._load_data)
            else:
                # Preserve the visible time window across the ticker
                # switch so the user stays on the same panned day.
                try:
                    self._preserve_xlim_by_time_on_render = True
                except Exception:  # noqa: BLE001
                    pass
                self._load_data_async()
        except Exception:  # noqa: BLE001
            return False
        return True

    def _on_watchlist_double(self, event) -> None:
        """Double-click in any pinned sub-tab sets the primary or compare ticker.

        Uses ``event.widget`` to find the right tree (multi-tree world).
        Falls back to ``_watchlist_tree`` alias for synthetic callers
        that pass a blank event.

        Routing: if the user's mouse was last over the **compare** chart
        panel, the watchlist double-click loads the symbol into the
        compare slot (mirroring click-to-type behaviour). Otherwise it
        loads into the primary slot. ``_last_hovered_slot`` is set by
        ``InteractionMixin._dispatch_hover`` and survives Notebook tab
        switches, so the slot the user was last looking at is always
        the target.
        """
        tree = getattr(event, "widget", None)
        if tree is None or not hasattr(tree, "selection"):
            tree = getattr(self, "_watchlist_tree", None)
        if tree is None:
            return
        try:
            sel = tree.selection()
        except Exception:  # noqa: BLE001
            return
        if not sel:
            return
        vals = tree.item(sel[0], "values")
        if not vals:
            return
        new_ticker = str(vals[0]).strip().upper()
        if not new_ticker:
            return
        slot = getattr(self, "_last_hovered_slot", "primary") or "primary"
        # Only honor "compare" routing if compare mode is actually on
        # — otherwise the compare panel doesn't exist and the user
        # would expect the primary chart to update.
        if slot == "compare" and bool(self.compare_var.get()):
            if new_ticker == self.compare_ticker_var.get().strip().upper():
                return
            self.compare_ticker_var.set(new_ticker)
        else:
            if new_ticker == self.ticker_var.get().strip().upper():
                return
            self.ticker_var.set(new_ticker)
        # Do NOT switch the Notebook away from the Watchlist tab — the user
        # explicitly asked for tab-preservation on watchlist-driven loads.
        # Mirror the typing path: if a drill-down day is active and we're
        # on 5m, keep that day for the new ticker (with latest-day fallback).
        if (getattr(self, "_drilldown_day", None) is not None
                and self.interval_var.get() == "5m"):
            self._reload_preserving_drilldown(self._load_data)
        else:
            # Preserve the visible time window across the ticker switch
            # so the user stays on the same panned day.
            try:
                self._preserve_xlim_by_time_on_render = True
            except Exception:  # noqa: BLE001
                pass
            self._load_data_async()

    def _kick_watchlist_preloads(self) -> None:
        """Fire-and-forget snapshot refresh for *newly-pinned* tickers.

        Only submits fetches for tickers that don't yet have a ``last``
        entry in :attr:`_watchlist_snapshot`. This keeps executor load
        bounded when the user pins a list whose tickers overlap with
        already-cached ones, and avoids re-flooding the pool on every
        sub-tab rebuild (init build, theme reload, rename, etc.).

        The full-refresh path (``_load_data`` → ``_preload_watchlist`` /
        ``_preload_watchlist_daily``) still runs unchanged on chart loads
        to keep prices fresh; this helper is purely additive for the
        "pinned a new list, want to see prices without clicking" case.
        """
        executor = getattr(self, "_executor", None)
        if executor is None:
            return
        try:
            tickers = self._pinned_ticker_union()
        except Exception:  # noqa: BLE001
            return
        # Read Tk vars once on the main thread so workers don't touch
        # them — Tcl/Tk variable access from worker threads can deadlock
        # the pool (observed under N7 async loads).
        try:
            src = self.source_var.get()
            itv = self.interval_var.get()
        except Exception:  # noqa: BLE001
            return
        for t in tickers:
            snap = self._watchlist_snapshot.get(t) or {}
            if "last" in snap and ("change_1d" in snap or "chg" in snap):
                continue
            try:
                if "last" not in snap:
                    executor.submit(self._preload_one_last, t, src, itv)
                if "change_1d" not in snap and "chg" not in snap:
                    executor.submit(self._preload_one_daily, t, src)
            except Exception:  # noqa: BLE001
                pass

    def _sandbox_watchlist_clock(self) -> tuple[bool, int | None, object | None]:
        """Return ``(sandbox_active, clock_ts, session_date)``.

        Watchlist preloads use this to slice fetched series to the
        sandbox replay clock so Last/Change reflect the historical
        moment being replayed rather than today's live values
        (look-ahead bias). All-``None`` / ``False`` when sandbox is
        not active.
        """
        try:
            if not self._is_sandbox_active() or self._sandbox is None:
                return (False, None, None)
            sb = self._sandbox
            ts = sb.clock_ts()
            sd = sb.current_session_date()
            return (True, ts, sd)
        except Exception:  # noqa: BLE001
            return (False, None, None)

    def _refresh_watchlist_for_sandbox(self) -> None:
        """Re-run the price-preload pipeline so watchlist Last/Change
        reflect the sandbox clock.

        Clears clock-dependent snapshot fields (``last``, ``change_1d``,
        ``pct_1d``, legacy ``chg``/``pct``) so the next repaint sees
        empty cells until the worker pool refills them — preferable to
        showing stale today-values during the brief refetch window.
        Then resubmits both preload helpers, which detect sandbox via
        :meth:`_sandbox_watchlist_clock` and slice the cached series to
        the replay clock.
        """
        snap_map = getattr(self, "_watchlist_snapshot", None)
        if isinstance(snap_map, dict):
            for snap in snap_map.values():
                if not isinstance(snap, dict):
                    continue
                for k in ("last", "change_1d", "pct_1d", "chg", "pct"):
                    snap.pop(k, None)
        # Bypass _preload_watchlist*'s cache-freshness short-circuit:
        # the cached candles ARE still fresh (only the sandbox clock
        # changed), but the snapshot must be re-derived from that new
        # clock. Submit the per-ticker workers directly. Last must
        # land before Daily because _preload_one_daily reads
        # snap["last"] when sandbox is active.
        executor = getattr(self, "_executor", None)
        try:
            src = self.source_var.get()
            itv = self.interval_var.get()
        except Exception:  # noqa: BLE001
            src = itv = None
        if executor is not None and src and itv:
            try:
                tickers = list(self._pinned_ticker_union())
            except Exception:  # noqa: BLE001
                tickers = []
            for t in tickers:
                try:
                    executor.submit(self._preload_one_last, t, src, itv)
                except Exception:  # noqa: BLE001
                    pass
            for t in tickers:
                try:
                    executor.submit(self._preload_one_daily, t, src)
                except Exception:  # noqa: BLE001
                    pass
        try:
            self._populate_watchlist_tab()
        except Exception:  # noqa: BLE001
            pass

    # ---- preload (ticker-union deduped) ------------------------------
    def _preload_watchlist(self) -> None:
        """Best-effort background refresh of last-price snapshots across
        every pinned watchlist (ticker-union deduped).

        Cache-aware: skips submission for any ticker whose intraday
        cache entry is already present and not stale. In-flight-deduped
        via :attr:`_watchlist_preload_inflight` so repeated calls (e.g.
        every ``_load_data``) don't pile up duplicate jobs while the
        first round is still running. The worker (:meth:`_preload_one_last`)
        clears its key in ``finally``.

        Orphan-snapshot recovery: when the cache is fresh but the
        :attr:`_watchlist_snapshot` row is missing ``last`` (e.g. after
        sandbox exit cleared the snapshot, or an earlier worker fetched
        bars but the dict write was lost), rebuild ``last`` from
        ``cached[-1].close`` directly so the watchlist doesn't sit
        forever waiting on a re-fetch that the cache-fresh check will
        keep skipping. The full sandbox-aware repaint will overwrite
        this from the next genuine fetch.
        """
        executor = self._executor
        if executor is None:
            return
        try:
            src = self.source_var.get()
            itv = self.interval_var.get()
        except Exception:  # noqa: BLE001
            return
        orphan_repaired = False
        for t in self._pinned_ticker_union():
            key = (src, t, itv)
            cached = self._full_cache.get(key)
            if cached and not self._cache_is_stale(cached, itv):
                snap = self._watchlist_snapshot.setdefault(t, {})
                if "last" not in snap and cached:
                    try:
                        snap["last"] = cached[-1].close
                        orphan_repaired = True
                    except Exception:  # noqa: BLE001
                        pass
                continue
            if key in self._watchlist_preload_inflight:
                continue
            try:
                self._watchlist_preload_inflight.add(key)
                executor.submit(self._preload_one_last, t, src, itv)
            except Exception:  # noqa: BLE001
                self._watchlist_preload_inflight.discard(key)
        if orphan_repaired:
            try:
                self._schedule_watchlist_tab_refresh()
            except Exception:  # noqa: BLE001
                pass
        # Piggy-back the events prefetch so the "Next Earn" column
        # fills in proactively as the user adds tickers to a watchlist.
        # _load_events_async is safe to call repeatedly — it dedups on
        # ``_events_fetch_inflight`` and TTLs via the cache module.
        try:
            self._preload_watchlist_events()
        except Exception:  # noqa: BLE001
            pass

    def _preload_watchlist_events(self) -> None:
        """Fan out :meth:`_load_events_async` over the pinned ticker union.

        Skips tickers already present in ``_events_cache`` so the
        background fetch doesn't churn forever. The fetcher itself is
        also inflight-deduped (see :attr:`_events_fetch_inflight`), so
        even a rapid second call is harmless.
        """
        for t in self._pinned_ticker_union():
            sym = t.upper()
            if sym in self._events_cache:
                continue
            try:
                self._load_events_async(sym)
            except Exception:  # noqa: BLE001
                pass

    def _preload_watchlist_daily(self) -> None:
        """Daily-interval background refresh for Chg/Chg% columns across
        every pinned watchlist (ticker-union deduped).

        Cache- and in-flight-aware (see :meth:`_preload_watchlist`).
        Same orphan-snapshot recovery semantics: rebuild
        ``change_1d``/``pct_1d`` from the cached daily series when the
        cache is fresh but the snapshot row is missing them.
        """
        executor = self._executor
        if executor is None:
            return
        try:
            src = self.source_var.get()
        except Exception:  # noqa: BLE001
            return
        orphan_repaired = False
        for t in self._pinned_ticker_union():
            key = (src, t, "1d")
            cached = self._full_cache.get(key)
            if cached and not self._cache_is_stale(cached, "1d"):
                snap = self._watchlist_snapshot.setdefault(t, {})
                if "change_1d" not in snap and len(cached) >= 2:
                    try:
                        prev = cached[-2].close
                        cur = cached[-1].close
                        chg = cur - prev
                        pct = (chg / prev * 100.0) if prev else 0.0
                        snap["change_1d"] = chg
                        snap["pct_1d"] = pct
                        snap.setdefault("chg", chg)
                        snap.setdefault("pct", pct)
                        if "last" not in snap:
                            snap["last"] = cur
                        orphan_repaired = True
                    except Exception:  # noqa: BLE001
                        pass
                continue
            if key in self._watchlist_preload_inflight:
                continue
            try:
                self._watchlist_preload_inflight.add(key)
                executor.submit(self._preload_one_daily, t, src)
            except Exception:  # noqa: BLE001
                self._watchlist_preload_inflight.discard(key)
        if orphan_repaired:
            try:
                self._schedule_watchlist_tab_refresh()
            except Exception:  # noqa: BLE001
                pass

    def _preload_one_last(self, ticker: str,
                          src: str | None = None,
                          itv: str | None = None) -> None:
        try:
            # Resolve src/itv on the main thread when not provided by
            # the caller. Workers that hit this branch may deadlock on
            # Tcl/Tk variable access — pass src/itv explicitly when
            # submitting from a hot path.
            if src is None or itv is None:
                try:
                    if src is None:
                        src = self.source_var.get()
                    if itv is None:
                        itv = self.interval_var.get()
                except Exception:  # noqa: BLE001
                    return
            fetcher = DATA_SOURCES.get(src)
            if fetcher is None:
                return
            cs = fetcher(ticker, itv)
            if cs:
                # Sandbox: slice to bars whose timestamp <= replay clock
                # so Last reflects the historical moment, not today's
                # live close (look-ahead bias).
                sb_active, sb_ts, _sb_date = self._sandbox_watchlist_clock()
                last_close: float | None = None
                if sb_active and sb_ts is not None:
                    for c in cs:
                        try:
                            cts = int(c.date.timestamp())
                        except Exception:  # noqa: BLE001
                            continue
                        if cts <= sb_ts:
                            last_close = c.close
                        else:
                            break
                else:
                    last_close = cs[-1].close
                if last_close is not None:
                    self._watchlist_snapshot.setdefault(ticker, {})["last"] = last_close
                # Hand bars to the Tk-thread inbox; ``self.after`` from
                # a worker thread blocks on tk.createcommand on this
                # Python/Tk build, so we use a thread-safe queue.
                # Fast-path: when invoked synchronously on the Tk
                # thread (e.g., test shims), apply directly so callers
                # can verify cache state without waiting for the
                # ``_drain_worker_inbox`` 80ms tick.
                try:
                    bars = list(cs)
                    if threading.current_thread() is threading.main_thread():
                        try:
                            self._stash_full_cache((src, ticker, itv), bars)
                        except Exception:  # noqa: BLE001
                            pass
                    else:
                        self._worker_inbox.put_nowait(
                            ("stash", ((src, ticker, itv), bars)))
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            # Clear inflight marker so subsequent _preload_watchlist
            # calls can resubmit if needed (e.g., after eviction).
            try:
                if src is not None and itv is not None:
                    self._watchlist_preload_inflight.discard(
                        (src, ticker, itv))
            except Exception:  # noqa: BLE001
                pass
            try:
                self._worker_inbox.put_nowait(("refresh", None))
            except Exception:  # noqa: BLE001
                pass

    def _preload_one_daily(self, ticker: str,
                           src: str | None = None) -> None:
        try:
            if src is None:
                try:
                    src = self.source_var.get()
                except Exception:  # noqa: BLE001
                    return
            fetcher = DATA_SOURCES.get(src)
            if fetcher is None:
                return
            cs = fetcher(ticker, "1d")
            if cs and len(cs) >= 2:
                # Sandbox: filter daily series to bars whose session
                # date is strictly less than the replay session date,
                # then compute change vs prior session close. The
                # "current" reference price is the intraday last (set
                # by _preload_one_last from the sliced intraday series)
                # so chg/pct reflect "intraday move from prior close"
                # at the replay moment — matching how a real broker
                # ticker would have rendered that day. Falls back to
                # day-over-day on the filtered tail if the intraday
                # last hasn't landed yet.
                sb_active, _sb_ts, sb_date = self._sandbox_watchlist_clock()
                if sb_active and sb_date is not None:
                    filtered = [c for c in cs if c.date.date() < sb_date]
                    if len(filtered) < 1:
                        # No daily history before the session date; bail
                        # rather than poisoning the snapshot.
                        return
                    prior_close = filtered[-1].close
                    if not prior_close:
                        return
                    snap = self._watchlist_snapshot.setdefault(ticker, {})
                    last_intraday = snap.get("last")
                    if isinstance(last_intraday, (int, float)):
                        chg = last_intraday - prior_close
                        pct = chg / prior_close * 100.0
                    elif len(filtered) >= 2:
                        chg = filtered[-1].close - filtered[-2].close
                        pct = (chg / filtered[-2].close * 100.0
                               if filtered[-2].close else 0.0)
                    else:
                        return
                    snap["change_1d"] = chg
                    snap["pct_1d"] = pct
                    snap["chg"] = chg
                    snap["pct"] = pct
                    # Do NOT setdefault("last", filtered[-1].close):
                    # in sandbox the intraday slice owns "last", and
                    # using filtered[-1].close would be the prior
                    # session's close — visibly stale.
                else:
                    chg = cs[-1].close - cs[-2].close
                    pct = (chg / cs[-2].close * 100.0) if cs[-2].close else 0.0
                    snap = self._watchlist_snapshot.setdefault(ticker, {})
                    # Pin the Change columns to the 1d aggregation regardless
                    # of the chart interval (spec §18.4).
                    snap["change_1d"] = chg
                    snap["pct_1d"] = pct
                    snap.setdefault("chg", chg)
                    snap.setdefault("pct", pct)
                    if "last" not in snap:
                        snap["last"] = cs[-1].close
                try:
                    bars = list(cs)
                    if threading.current_thread() is threading.main_thread():
                        try:
                            self._stash_full_cache((src, ticker, "1d"), bars)
                        except Exception:  # noqa: BLE001
                            pass
                    else:
                        self._worker_inbox.put_nowait(
                            ("stash", ((src, ticker, "1d"), bars)))
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            # Clear inflight marker (see :meth:`_preload_one_last`).
            try:
                if src is not None:
                    self._watchlist_preload_inflight.discard(
                        (src, ticker, "1d"))
            except Exception:  # noqa: BLE001
                pass
            try:
                self._worker_inbox.put_nowait(("refresh", None))
            except Exception:  # noqa: BLE001
                pass

    # ---- recurring poll loop -----------------------------------------
    #
    # The chart-load preload path (``_preload_watchlist`` /
    # ``_preload_watchlist_daily``, fired from ``_load_data``) only
    # runs when the user switches ticker / interval / source. If
    # INTC's first fetch transiently fails (yfinance rate-limit,
    # quota, network blip), the snapshot row stays empty until the
    # user manually loads INTC into the chart — visibly broken.
    #
    # The poll loop here re-runs the preload pipeline every
    # ``watchlist_poll_interval_sec`` seconds so transient failures
    # self-heal and live broker-style Last/Change updates land
    # without user interaction. The in-flight dedup +
    # ``_cache_is_stale`` short-circuits in the preload helpers keep
    # network load minimal — a steady-state RTH poll on a fully
    # cached watchlist is zero HTTP calls.

    # Approximate US regular trading hours in ET (09:30–16:00,
    # weekdays). Holidays not handled — at worst we poll at the live
    # cadence on a holiday, costing a few extra cache-fresh
    # short-circuit hits.
    _WATCHLIST_POLL_RTH_OPEN_MIN = 9 * 60 + 30
    _WATCHLIST_POLL_RTH_CLOSE_MIN = 16 * 60

    def _watchlist_poll_in_rth_now(self) -> bool:
        """True when current wall-clock is inside US RTH (09:30–16:00
        ET, Mon–Fri). Used by :meth:`_watchlist_poll_effective_delay_ms`
        to decide between live-cadence and off-hours intervals.

        On hosts where ``zoneinfo`` is unavailable (rare; bundled in
        Python 3.9+), conservatively returns ``True`` so the user
        sees live-cadence polling rather than a silent off-hours
        slowdown.
        """
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            et = datetime.now(ZoneInfo("America/New_York"))
        except Exception:  # noqa: BLE001
            return True
        if et.weekday() >= 5:  # Sat/Sun
            return False
        minutes = et.hour * 60 + et.minute
        return (self._WATCHLIST_POLL_RTH_OPEN_MIN
                <= minutes
                < self._WATCHLIST_POLL_RTH_CLOSE_MIN)

    def _watchlist_poll_effective_delay_ms(self) -> int | None:
        """Resolve the next poll-tick delay in milliseconds.

        Returns ``None`` when polling is disabled
        (``watchlist_poll_interval_sec == 0``) so callers can skip
        re-arming. Otherwise returns ``interval * 1000`` during RTH
        and ``interval * multiplier * 1000`` outside RTH.
        """
        try:
            from .. import defaults as _d
            interval_s = int(_d.get("watchlist_poll_interval_sec"))
            multiplier = float(_d.get("watchlist_poll_offhours_multiplier"))
        except Exception:  # noqa: BLE001
            return None
        if interval_s <= 0:
            return None
        if not self._watchlist_poll_in_rth_now():
            interval_s = int(interval_s * multiplier)
        interval_s = max(5, interval_s)
        return interval_s * 1000

    def _start_watchlist_poll_loop(self) -> None:
        """Arm the first watchlist poll tick.

        Called once from ``ChartApp.__init__`` after the initial
        :meth:`_kick_watchlist_preloads`. Subsequent ticks self-re-arm
        from :meth:`_watchlist_poll_tick`. Idempotent.
        """
        existing = getattr(self, "_watchlist_poll_job", None)
        if existing is not None:
            return
        delay_ms = self._watchlist_poll_effective_delay_ms()
        if delay_ms is None:
            self._watchlist_poll_job = None
            return
        try:
            self._watchlist_poll_job = self._track_after(
                delay_ms, self._watchlist_poll_tick)
        except tk.TclError:
            self._watchlist_poll_job = None

    def _watchlist_poll_tick(self) -> None:
        """Re-run the watchlist preload pipeline and re-arm.

        Sandbox guard: while a replay session is active the engine
        drives clock advancement, not the live poll — skip the tick
        body but DO re-arm so polling resumes immediately on sandbox
        exit.

        The preload helpers (``_preload_watchlist`` /
        ``_preload_watchlist_daily``) own their own cache-freshness
        and in-flight dedup, so a tick on a fully-cached watchlist
        during RTH costs zero HTTP calls. A tick after a transient
        fetch failure re-submits the missing tickers and clears
        the visible orphan.
        """
        self._watchlist_poll_job = None
        try:
            sandbox_active = bool(self._is_sandbox_active())
        except Exception:  # noqa: BLE001
            sandbox_active = False
        if not sandbox_active:
            try:
                self._preload_watchlist()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._preload_watchlist_daily()
            except Exception:  # noqa: BLE001
                pass
        delay_ms = self._watchlist_poll_effective_delay_ms()
        if delay_ms is None:
            return
        try:
            self._watchlist_poll_job = self._track_after(
                delay_ms, self._watchlist_poll_tick)
        except tk.TclError:
            self._watchlist_poll_job = None
