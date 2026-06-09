"""Modeless "Manage Indicators…" dialog.

Replaces the four hard-coded ``Add SMA(20)`` / ``Add EMA(50)`` / ``Add
RSI(14)`` / ``Add Bollinger Bands`` quick-add menu entries with a
single editor that lets the user add any number of indicators, pick
their kind from a dropdown, configure every parameter declared in the
factory's ``params_schema``, and toggle which charts (Primary /
Compare) each indicator renders on.

Design notes
------------

* **Modeless singleton.** A single instance lives on the app as
  ``app._indicator_dialog``. Re-opening focuses the existing window.
  Modeless lets users see chart edits land while the dialog stays
  open. ``Toplevel.transient(app)`` makes it stack with the main
  window without modally grabbing input.

* **Manager subscription, not snapshot ownership.** The dialog reads
  the manager state on open and on every observed mutation
  (``add`` / ``remove`` / ``update`` / ``clear`` / ``preset_loaded`` /
  ``loaded``). If something else mutates the manager (Clear All,
  preset load, future automation), the dialog reconciles instead of
  scribbling stale ids back in.

* **Live commit with debounced text/numeric edits.** Checkboxes,
  comboboxes, and spinbox arrow clicks commit immediately. Free-form
  numeric / text typing debounces 250 ms via ``after`` so typing
  ``200`` doesn't fire three add/update/render cycles. Commit
  validates by *instantiating* the underlying factory; on
  ``Exception`` the row is reverted to the last-good params (no
  status popup — silent revert keeps the editing flow smooth).

* **Scope checkboxes preserve drilldown.** The dialog exposes only
  Primary / Compare. The full
  :data:`indicators.config.SCOPES` includes ``"drilldown"``; if a
  config arrives with ``"drilldown"`` set we keep it across edits so
  the dialog cannot silently strip a third-party scope membership.

* **Both checkboxes off ⇒ ``visible=False``.** The row's last
  non-empty Primary/Compare scope set is preserved internally so
  re-checking either box restores it intact. ``visible`` is the
  master flag the manager honours via ``IndicatorConfig.applies_to``.

* **Unknown-kind rows are read-only.** Configs hydrated from saved
  state with a ``kind_id`` that is not currently registered are
  presented as a non-editable row labelled
  ``"Unknown indicator (<kind_id>)"`` with a Remove option only.
  Editing fields are disabled so the dialog cannot rewrite an
  unknown config and silently delete its data.

* **Stable row keys, not list positions.** Selection
  (``"Remove Selected"`` button) binds row-radiobuttons to a per-row
  monotonic key. Rebuild / reorder doesn't change which row is
  marked.
"""

from __future__ import annotations

import tkinter as tk
from itertools import count
from tkinter import ttk
from typing import Any, ClassVar

from ..indicators._palette import FALLBACK_GRAY
from ..indicators.base import INDICATORS, LineStyle, factory_by_kind_id, factory_is_available_for
from ..indicators.config import (
    DEFAULT_SCOPES,
    IndicatorConfig,
    IndicatorManager,
)
from ._modal_base import BaseModalDialog, make_scrollable_form, protect_combobox_wheel
from ._param_widgets import (
    build_param_widget,
    label_text_for,
    tooltip_text_for,
)
from ._param_widgets import (
    combo_width_for_choices as _combo_width_for_choices,
)
from ._param_widgets import (
    spinbox_width_for as _spinbox_width_for,
)
from ._widget_metrics import _CHAR_PX
from .color_palette import pick_color
from .colors import WARN_AMBER
from .indicator_acronyms import explain_kind_id
from .tooltip import ToolTip

# Full toolbar interval set — kept in sync with ``app._INTERVALS``.
# Used as the per-row checkbox source when sandbox is not active.
_ALL_INTERVALS: tuple[str, ...] = (
    "1m", "2m", "5m", "15m", "30m", "1h", "1d", "1wk", "1mo",
)


# Debounce window for free-form numeric / text edits. Spinbox arrow
# clicks and combobox selections still commit immediately; this only
# coalesces typing.
_TYPING_DEBOUNCE_MS = 250


def _filter_indicator_kind_displays(
    kinds_by_display: dict[str, str],
    query: str,
) -> tuple[str, ...]:
    """Return indicator display names matching id/display/help text."""
    needle = str(query or "").strip().casefold()
    if not needle:
        return tuple(kinds_by_display.keys())
    tokens = [t for t in needle.split() if t]
    out: list[str] = []
    for display, kind_id in kinds_by_display.items():
        haystack = " ".join((
            str(display),
            str(kind_id),
            explain_kind_id(kind_id),
        )).casefold()
        if needle in haystack or (tokens and all(t in haystack for t in tokens)):
            out.append(display)
    return tuple(out)


def _format_anchor_label(ts: str) -> str:
    """Render an ISO ``anchor_ts`` value as a compact ``YYYY-MM-DD HH:MM``.

    Returns ``"(first bar)"`` for blank values (the default seed when
    a fresh Anchored VWAP is created — the manager event handler
    resolves it to a real timestamp asynchronously). Falls back to the
    raw string if parsing fails so the user still sees something
    informative rather than a cryptic empty label.
    """
    raw = (ts or "").strip()
    if not raw:
        return "(first bar)"
    try:
        from datetime import datetime
        s = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        dt = datetime.fromisoformat(s)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return raw[:18]



# Monotonic row key generator. Survives row add/remove churn so
# selection state stored on a row's radiobutton can't be confused with
# a removed-and-re-added row.
_ROW_KEY = count(1)


def open_indicator_dialog(app: tk.Tk) -> IndicatorDialog:
    """Open or re-focus the singleton Manage Indicators dialog.

    Stores the instance on ``app._indicator_dialog`` so subsequent
    invocations reuse it. If the singleton was destroyed (user closed
    the window), the slot is cleared and a fresh dialog is created.
    """
    dlg_mgr = getattr(app, "_dialog_mgr", None)
    if dlg_mgr is not None:
        def _factory() -> IndicatorDialog:
            dlg = IndicatorDialog(app)
            try:
                app._indicator_dialog = dlg  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            return dlg

        dlg = dlg_mgr.open_or_focus("indicator", _factory)
        try:
            app._indicator_dialog = dlg  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        return dlg
    existing = getattr(app, "_indicator_dialog", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                try:
                    existing.deiconify()
                    existing.lift()
                    existing.focus_set()
                except tk.TclError:
                    pass
                return existing
        except tk.TclError:
            pass
        # Stale ref (window was destroyed); fall through to recreate.
        try:
            app._indicator_dialog = None  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    dlg = IndicatorDialog(app)
    try:
        app._indicator_dialog = dlg  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    return dlg


class _IndicatorRow:
    """Holds widgets + state for a single editor row.

    The row is the unit of identity in the dialog: it has a stable
    ``row_key`` (independent of position in the list) and a
    ``config_id`` linking it to the manager's :class:`IndicatorConfig`
    once the row has been committed at least once.
    """

    __slots__ = (
        "row_key", "config_id", "container",
        "radio_btn",
        "drag_handle",
        "kind_var", "kind_combo", "kind_tooltip", "help_label",
        "param_subframe", "param_vars", "param_widgets",
        "param_max_cols_applied",
        "primary_var", "compare_var",
        "preserved_extra_scopes",
        "preserved_active_scopes",
        "interval_subframe",
        "interval_vars",
        "interval_max_cols_applied",
        "preserved_intervals",
        "color_subframe",
        "color_buttons",
        "style_overrides",
        "last_good_params",
        "is_unknown",
        "suppress",
        "debounce_after_id",
        "applied_kind_id",
    )

    def __init__(self, row_key: int) -> None:
        self.row_key = row_key
        self.config_id: int | None = None
        # Tk widgets populated by the dialog when the row is built.
        self.container: tk.Frame | None = None
        self.radio_btn: ttk.Radiobutton | None = None
        # Drag-and-drop handle (``≡`` glyph). Mouse drag on this label
        # reorders rows; ``Alt+↑`` / ``Alt+↓`` on it (or anywhere in
        # the row) provides a keyboard fallback for the smoke harness.
        self.drag_handle: tk.Label | None = None
        self.kind_var: tk.StringVar | None = None
        self.kind_combo: ttk.Combobox | None = None
        # Hover tooltip on ``kind_combo`` that surfaces the full name
        # of the indicator acronym and a one-line description. Created
        # lazily in ``_build_row``; ``_on_kind_changed`` keeps its
        # text in sync with the current selection.
        self.kind_tooltip: ToolTip | None = None
        self.help_label: tk.Label | None = None
        self.param_subframe: tk.Frame | None = None
        # Per-param Tk variable (BooleanVar / StringVar) and its widget.
        self.param_vars: dict[str, tk.Variable] = {}
        self.param_widgets: dict[str, tk.Widget] = {}
        # Most-recently-applied param-grid column count for this row.
        # Tracked so :meth:`IndicatorDialog._do_resize_reflow_rows`
        # can implement hysteresis — it only re-grids when the target
        # column count actually changes (audit item #1; CLAUDE.md
        # §7.19 hysteresis pattern adapted to discrete int column
        # counts).
        self.param_max_cols_applied: int | None = None
        self.primary_var: tk.BooleanVar | None = None
        self.compare_var: tk.BooleanVar | None = None
        # Scope members other than ``main`` / ``compare`` (today only
        # ``"drilldown"``). Preserved across edits so the two-checkbox
        # UI can never silently strip them.
        self.preserved_extra_scopes: frozenset[str] = frozenset()
        # Last non-empty {"main"} ∪ {"compare"} subset, restored when
        # the user un-toggles both and then re-checks one. Without
        # this, "uncheck Primary, uncheck Compare" would lose the
        # last-known scope assignment and "recheck Primary" would
        # default to {"main"} regardless of where the user started.
        self.preserved_active_scopes: frozenset[str] = DEFAULT_SCOPES
        # Per-interval visibility (b41). One BooleanVar per interval
        # currently exposed in the row's interval-checkbox subframe;
        # the available set depends on sandbox state (sandbox active
        # → display_intervals + "1d" if registered; otherwise the full
        # toolbar set). Empty ``preserved_intervals`` ⇔ "all" (matches
        # the legacy IndicatorConfig.intervals=() semantic so old
        # presets keep working unchanged).
        self.interval_subframe: tk.Frame | None = None
        self.interval_vars: dict[str, tk.BooleanVar] = {}
        self.interval_max_cols_applied: int | None = None
        self.preserved_intervals: tuple[str, ...] = ()
        # Per-output color overrides (b42). One color-swatch button
        # per indicator output key (e.g. "sma" / "upper" / "middle"
        # / "lower" for Bollinger). ``style_overrides`` maps the
        # output key to a hex color the user has chosen via the
        # native OS color chooser; a key absent from the dict means
        # "use the factory's default_style color". Rebuilt whenever
        # the row's kind changes, so a SMA→EMA switch correctly
        # resets to the new factory's output keys.
        self.color_subframe: tk.Frame | None = None
        self.color_buttons: dict[str, tk.Frame] = {}
        self.style_overrides: dict[str, str] = {}
        # Last params dict that successfully constructed an indicator
        # — used to revert in-place when validation fails so the
        # chart never sees an invalid config.
        self.last_good_params: dict[str, Any] = {}
        self.is_unknown: bool = False
        # Construction / rebuild guard: while True, widget traces must
        # not commit (otherwise rebuilding the param subframe would
        # commit half-built rows).
        self.suppress: bool = True
        # Pending debounce job id (Tk ``after`` handle) for typing.
        self.debounce_after_id: str | None = None
        # The ``kind_id`` whose param widgets are currently rendered in
        # this row. Tracked so :meth:`IndicatorDialog._on_kind_changed`
        # can short-circuit a spurious ``<FocusOut>`` on the kind
        # combobox (Windows fires ``<FocusOut>`` when the dropdown
        # popdown is posted/dismissed, even when the value is
        # unchanged) instead of tearing the row down + re-theming the
        # whole dialog — the "window flickers when I click the
        # dropdown" bug. ``None`` until the first param build.
        self.applied_kind_id: str | None = None


class IndicatorDialog(BaseModalDialog):
    """Modeless editor over :class:`IndicatorManager`.

    Lifetime: lives on the app as ``app._indicator_dialog`` until the
    user closes it (``WM_DELETE_WINDOW``) or the app shuts down. The
    manager subscription is registered in ``__init__`` and unhooked
    in ``destroy``.

    Migrated from :class:`tk.Toplevel` to :class:`BaseModalDialog`;
    ``BaseModalDialog`` owns ``title`` / ``transient`` / geometry
    persistence / ESC + WM_DELETE wiring via ``_finalize_modal``.
    Crucially we pass ``grab=False`` to keep the dialog **modeless**
    so the user can still interact with the chart while it's open.
    """

    #: Per-app-session memory of the last-picked MA type. Persisted in
    #: memory only — never written to disk — so the bias resets on a
    #: fresh launch (the trader-agent recommendation; users who want
    #: SMA-by-default after restart still get it). Updated whenever a
    #: ``kind_id == "ma"`` row is committed; injected as the seed for
    #: future Moving Average rows whose ``params`` don't already
    #: specify ``ma_type``.
    _last_used_ma_type: ClassVar[str] = "SMA"

    #: Whether this dialog DEFERS chart renders while editing (the
    #: snappy "Apply"-button flow). True for the full Manage Indicators
    #: dialog; the single-overlay quick-edit popup
    #: (:class:`_PerIndicatorDialog`) overrides this to ``False`` so it
    #: keeps rendering live (a focused single-indicator edit benefits
    #: from instant feedback — deliberate exception, see its spec). The
    #: deferred-apply meta-test classifies every indicator-editing window
    #: by this flag.
    _DEFERS_RENDER: ClassVar[bool] = True

    def __init__(
        self,
        app: tk.Tk,
        *,
        restricted_to_config_id: int | None = None,
    ) -> None:
        # ``BaseModalDialog`` handles title / transient / geometry
        # persistence (``dlg.indicator`` → 980x560) and (via
        # ``_finalize_modal`` at the bottom of __init__)
        # WM_DELETE_WINDOW + ESC. The default geometry is large enough
        # to host the widest built-in indicator row (Bollinger Bands:
        # kind dropdown + Primary/Compare scope checkboxes + 4 param
        # widgets including the ``Moving Average`` choice combobox +
        # per-interval checkbox strip + per-output color swatches).
        # Without it the Toplevel auto-sizes to the canvas's narrow
        # requested width and the rightmost widgets get clipped — most
        # visibly the Bollinger Bands ``Moving Average`` dropdown.
        # ``minsize`` keeps the user from shrinking the dialog into the
        # same broken state.
        super().__init__(
            app,
            title="Manage Indicators",
            geometry_key="dlg.indicator",
            default_geometry="980x560",
        )
        # When non-None this dialog only ever displays / reconciles the
        # one matching config — used by ``gui.per_indicator_dialog``
        # for the per-row popup spawned by double-clicking an overlay
        # legend entry. Set BEFORE ``_build_layout`` /
        # ``_reconcile_from_manager`` so the initial seed honours the
        # filter.
        self._restricted_to_config_id: int | None = restricted_to_config_id
        self.minsize(880, 420)
        self._app = app
        self._manager: IndicatorManager = app._indicator_manager
        # Display-name → kind_id map for the kind combobox. Sorted
        # alphabetically (case-insensitive) by display name so the
        # dropdown is browsable across all consumers (the per-row Kind
        # combobox at line ~530, the per-interval refresh at
        # ``_kind_dropdown_values``, and the new-row default seed at
        # ``_build_row``). The underlying ``INDICATORS`` registry
        # preserves insertion order; we sort at the UI layer so
        # programmatic registration order is decoupled from user-facing
        # ordering.
        self._kinds_by_display: dict[str, str] = {}
        sorted_items = sorted(
            INDICATORS.items(), key=lambda kv: kv[0].casefold(),
        )
        for display_name, factory in sorted_items:
            kind_id = getattr(factory, "kind_id", None)
            if kind_id:
                self._kinds_by_display[display_name] = kind_id
        # Row state — stable order matches the visual layout.
        self._rows: list[_IndicatorRow] = []
        # Shared IntVar driving every row's selection radiobutton.
        # Value is the row's ``row_key`` (NOT its list index) so a
        # row removed mid-session can't shift selection onto another
        # row.
        self._selected_key = tk.IntVar(value=0)
        # When True, manager-event reconciliation is in flight and
        # commit handlers must not call back into the manager.
        self._reconciling = False
        # Drag-to-reorder transient state (b43). ``_drag_row`` is the
        # row currently being dragged; ``_drag_indicator`` is a thin
        # horizontal Frame placed via ``place()`` on ``_rows_inner``
        # to show the drop target between rows. ``_drag_target_index``
        # is the post-removal target index that the next
        # ``ButtonRelease-1`` would commit to ``manager.reorder``.
        self._drag_row: _IndicatorRow | None = None
        self._drag_indicator: tk.Frame | None = None
        self._drag_target_index: int = 0
        # Per-row tooltip instances on the drag-handle glyph. Kept on
        # the dialog so they don't get garbage-collected while a row
        # is alive.
        self._tooltips: list[ToolTip] = []
        # Debounced resize-reflow state (audit item #1 / CLAUDE.md
        # §7.19 hysteresis pattern). On every <Configure> we
        # schedule a 100 ms-deferred re-flow of every row's param
        # grid; if the resulting column count for a row is unchanged
        # from ``row.param_max_cols_applied`` we skip the re-grid
        # so steady-state drags don't thrash the layout.
        self._rows_resize_after_id: str | None = None
        self._rows_resize_bind_id: str | None = None
        # Snapshot the manager state for cancel/revert semantics.
        # On "Cancel" the dialog restores this snapshot so the chart
        # reverts to its pre-dialog state.
        self._snapshot = self._manager.to_dict()
        self._dirty = False
        # Deferred-render ("Apply") state. ``_pending_dirty`` = there are
        # indicator changes the chart has NOT been repainted with yet
        # (distinct from ``_dirty`` = there is session state worth
        # keeping). ``_render_deferred_active`` tracks whether we have an
        # outstanding ``app._begin_defer_indicator_render`` to balance on
        # teardown. ``_auto_apply_var`` (default OFF) flips to live
        # rendering. Only meaningful when ``_defers_render`` is True.
        self._defers_render: bool = bool(type(self)._DEFERS_RENDER)
        self._pending_dirty = False
        self._render_deferred_active = False
        self._auto_apply_var = tk.BooleanVar(value=False)
        self._apply_btn: ttk.Button | None = None
        self._base_title = "Manage Indicators"
        # Build the chrome.
        self._build_layout()
        self._protect_combobox_wheel()
        # Subscribe BEFORE seeding so a concurrent mutation during
        # the initial seed run still results in a single, consistent
        # final state on the next event tick.
        self._unsubscribe = self._manager.subscribe(self._on_manager_event)
        # Seed rows from the current active list.
        self._reconcile_from_manager()
        # Lift after layout so the new window is on top.
        try:
            self.lift()
            self.focus_set()
        except tk.TclError:
            pass
        # Sync to the app's current theme (light/dark) so the dialog's
        # ``tk.Frame`` / ``tk.Canvas`` widgets — which the global
        # ttk.Style does NOT manage — pick up the right backgrounds.
        # Subsequent theme toggles arrive via ``_apply_theme`` (called
        # from the parent app's ``_apply_theme`` dispatcher).
        try:
            self._apply_theme()
        except Exception:  # noqa: BLE001
            pass
        # Modeless editor: ESC cancels (reverts + closes), Ctrl+S saves
        # and closes. Return is NOT bound (committing edits is per-row,
        # not dialog-wide). ESC + WM_DELETE_WINDOW are wired by the
        # ``_finalize_modal`` call at the end of __init__; Ctrl+S is
        # an IndicatorDialog-specific extension layered on top.
        self.bind("<Control-s>", lambda _e: self._on_save_close())
        # Apply (deferred-render flush). Ctrl+Return — NOT bare Return,
        # which commits the focused param Entry/Spinbox/Combobox. Fires
        # from anywhere in the dialog, including inside a field.
        if self._defers_render:
            self.bind("<Control-Return>", lambda _e: self._apply())
            self.bind("<Control-KP_Enter>", lambda _e: self._apply())
        # Resize reactivity (audit item #1): bind the Toplevel's
        # ``<Configure>`` event so dragging the dialog edge re-flows
        # each row's param grid. Debounced 100 ms; uses the
        # ``param_max_cols_applied`` per-row hysteresis so we only
        # re-grid when the column count actually changes.
        try:
            self._rows_resize_bind_id = self.bind(
                "<Configure>", self._on_toplevel_resize, add="+",
            )
        except tk.TclError:
            self._rows_resize_bind_id = None
        self.bind("<Destroy>", self._on_destroy_resize_binding, add="+")
        # Backstop: balance any outstanding render-deferral if the window
        # is destroyed by a path other than _teardown (which also ends
        # it, idempotently). Filter to the Toplevel's OWN destroy so a
        # child-widget destroy during a row rebuild can't end deferral
        # mid-edit.
        self.bind(
            "<Destroy>",
            lambda e: self._end_render_deferral() if e.widget is self else None,
            add="+",
        )

        # Final modal wiring — wire ESC/WM_DELETE to ``_on_cancel`` and
        # leave Return unbound (``primary=None``). ``grab=False`` is
        # non-negotiable: this dialog is **modeless** by design (the
        # user must be able to interact with the chart while it's
        # open). See class docstring + CLAUDE.md notes.
        self._finalize_modal(primary=None, cancel=self._on_cancel, grab=False)
        # Enter deferred-render mode last, once the window is fully built
        # and seeded. From here, per-row edits mutate the manager but do
        # NOT repaint the chart until the user clicks Apply (or Save and
        # Close). Scoped to this dialog via the app's depth counter; the
        # quick-edit popup (``_DEFERS_RENDER=False``) skips this and keeps
        # rendering live.
        if self._defers_render and not self._auto_apply_var.get():
            self._begin_render_deferral()

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _apply_theme(self) -> None:
        """Repaint Toplevel + every ``tk.Frame`` / ``tk.Canvas`` / ``tk.Label`` descendant.

        Mirrors the parent app's window theme step. ttk widgets
        (Buttons, Combobox, Checkbutton, Radiobutton, Label, Entry,
        Spinbox, Scrollbar) inherit colors from the global
        ``ttk.Style`` so we don't need to touch them here. The plain
        ``tk.Frame`` / ``tk.Canvas`` widgets used for layout chrome
        keep their default light backgrounds otherwise, producing a
        bright dialog over a dark app — what the user reported.

        Plain ``tk.Label`` widgets (drag handles, help icons, swatch
        captions) likewise keep their default light bg + black fg
        unless we paint them. Each label receives the theme bg/fg
        unless tagged with ``_preserve_fg = True`` (icons whose
        colour carries meaning — e.g. the blue help glyph).

        Idempotent + safe to call from a torn-down state.
        """
        try:
            theme = getattr(self._app, "_theme", None) or {}
        except Exception:  # noqa: BLE001
            theme = {}
        bg = theme.get("win_bg")
        if not bg:
            return
        fg = theme.get("text", "#000000")
        try:
            self.configure(background=bg)
        except tk.TclError:
            return

        def _walk(w: tk.Widget) -> None:
            for child in w.winfo_children():
                cls = child.__class__
                # Skip widgets explicitly tagged as theme-exempt
                # (e.g. color swatches whose ``bg`` IS the data the
                # user is looking at — see ``_rebuild_color_buttons``).
                if getattr(child, "_no_theme", False):
                    continue
                if cls is tk.Frame or cls is tk.Canvas:
                    try:
                        child.configure(background=bg)
                    except tk.TclError:
                        pass
                elif cls is tk.Label:
                    # Audit ``indicator-dialog-label-theme``: plain
                    # tk.Label widgets need explicit bg/fg in dark
                    # mode — they don't inherit from ttk.Style.
                    try:
                        child.configure(background=bg)
                    except tk.TclError:
                        pass
                    if not getattr(child, "_preserve_fg", False):
                        try:
                            child.configure(foreground=fg)
                        except tk.TclError:
                            pass
                _walk(child)

        try:
            _walk(self)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _teardown(self) -> None:
        """Mechanical teardown: unsubscribe, cancel debounces, clear
        singleton, destroy window. Shared by cancel and save-close."""
        # Balance any outstanding render-deferral FIRST so the app's
        # depth counter never leaks if a later step raises.
        self._end_render_deferral()
        try:
            self._unsubscribe()
        except Exception:  # noqa: BLE001
            pass
        # Cancel any pending debounced commits before destroying
        # widgets — otherwise the ``after`` callback would fire on a
        # destroyed Toplevel.
        for row in list(self._rows):
            self._cancel_pending_debounce(row)
        try:
            if getattr(self._app, "_indicator_dialog", None) is self:
                self._app._indicator_dialog = None  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _on_cancel(self) -> None:
        """Cancel: revert the indicator manager to the snapshot taken
        when the dialog opened, then tear down.

        ``load_dict`` fires ``"loaded"`` which re-issues config ids,
        so any open per-indicator popups will auto-close (their
        tracked id is no longer valid). This is correct — reverting
        the whole indicator state means per-indicator popups have
        nothing to show."""
        if self._dirty and self._snapshot is not None:
            self._reconciling = True
            try:
                self._manager.load_dict(self._snapshot)
            finally:
                self._reconciling = False
        self._teardown()

    def _on_save_close(self) -> None:
        """Accept the current live indicator state and close.

        The live changes have already been applied to the chart via
        the manager's live-commit pipeline. This button simply
        clears the snapshot (so the state is kept for the session)
        and tears down the dialog. To persist across sessions, the
        user should use Indicators → Save Preset.

        Per-indicator on-save validators (see
        :meth:`_collect_save_close_errors`) get a chance to refuse the
        close. When any row reports an error, the offending widget is
        focused, a ``messagebox.showerror`` explains the problem, and
        the dialog stays open so the user can fix the value.
        """
        errors = self._collect_save_close_errors()
        if errors:
            row, widget, message = errors[0]
            try:
                from tkinter import messagebox
                messagebox.showerror(
                    "Invalid indicator parameter",
                    message,
                    parent=self,
                )
            except tk.TclError:
                pass
            # Focus the offending widget so the user lands on the
            # field with the problem. Combobox + Entry both honour
            # focus_set; ignore failures (widget may have been
            # destroyed between collect + present).
            try:
                if widget is not None:
                    widget.focus_set()
                    if hasattr(widget, "icursor"):
                        try:
                            widget.icursor("end")
                        except tk.TclError:
                            pass
            except tk.TclError:
                pass
            return
        # Implicit Apply: never keep a config the user hasn't seen
        # rendered. Flushes one render iff there are pending changes
        # (no-op otherwise). Live mode already painted.
        self._apply()
        self._snapshot = None  # discard the revert point
        self._teardown()

    def _collect_save_close_errors(
        self,
    ) -> list[tuple[Any, tk.Widget | None, str]]:
        """Run per-indicator save-close validators across every row.

        Returns a list of ``(row, offending_widget_or_None, message)``
        tuples — empty when every row is acceptable. Currently the
        only registered validator is the RRVOL compare-symbol
        syntactic check; the pattern is extensible — add more
        kind-id-dispatched branches as new free-text parameters
        appear. Audit ``rrvol-compare-symbol``.
        """
        errors: list[tuple[Any, tk.Widget | None, str]] = []
        rows = getattr(self, "_rows", None) or ()
        for row in rows:
            try:
                kind_display = (row.kind_var.get() or "").strip()
            except tk.TclError:
                continue
            kind_id = self._kinds_by_display.get(kind_display)
            if kind_id != "rrvol":
                continue
            var = row.param_vars.get("compare_symbol")
            if var is None:
                continue
            try:
                raw = var.get()
            except tk.TclError:
                continue
            # Defer the actual validation to the indicator module so
            # the rule lives next to the param schema.
            from ..indicators.rrvol import validate_compare_symbol
            ok, msg = validate_compare_symbol(raw)
            if not ok:
                widget = row.param_widgets.get("compare_symbol")
                errors.append((row, widget, msg))
        return errors

    # Backward-compat alias — external code that calls ``_on_close``
    # (e.g. the per-indicator popup's ``super()._on_close()``) still
    # routes through the cancel path.
    def _on_close(self) -> None:  # noqa: D401
        """Alias for ``_on_cancel`` — WM_DELETE_WINDOW and Escape."""
        self._on_cancel()

    # ------------------------------------------------------------------
    # Dirty tracking
    # ------------------------------------------------------------------

    def _mark_dirty(self) -> None:
        """Flag the dialog as having unsaved changes.

        Updates the title bar with a ``•`` suffix and enables the
        Save and Close button."""
        if self._dirty:
            return
        self._dirty = True
        try:
            btn = getattr(self, "_save_close_btn", None)
            if btn is not None:
                btn.configure(state="normal")
        except tk.TclError:
            pass
        self._refresh_dirty_title()

    def _refresh_dirty_title(self) -> None:
        """Sync the window title with the current dirty state."""
        title = self._base_title
        if self._dirty:
            title += " \u2022"
        try:
            self.title(title)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Deferred render / Apply (snappy indicator editing)
    # ------------------------------------------------------------------

    def _begin_render_deferral(self) -> None:
        """Ask the app to suppress indicator-driven chart paints.

        Idempotent per dialog: tracks ``_render_deferred_active`` so a
        second call (e.g. toggling auto-apply off then on then off)
        cannot leave the app's depth counter unbalanced. No-op when the
        app has no defer hook (stub-root unit tests)."""
        if self._render_deferred_active:
            return
        begin = getattr(self._app, "_begin_defer_indicator_render", None)
        if not callable(begin):
            return
        try:
            begin()
            self._render_deferred_active = True
        except Exception:  # noqa: BLE001
            pass

    def _end_render_deferral(self) -> None:
        """Balance :meth:`_begin_render_deferral` (idempotent)."""
        if not self._render_deferred_active:
            return
        end = getattr(self._app, "_end_defer_indicator_render", None)
        self._render_deferred_active = False
        if callable(end):
            try:
                end()
            except Exception:  # noqa: BLE001
                pass

    def _mark_pending(self) -> None:
        """Flag that the chart has un-rendered indicator changes.

        Called on every dialog-driven manager mutation. A no-op in live
        mode (``_defers_render`` False or auto-apply on) — there the
        chart already repainted, so nothing is pending. Enables the
        Apply button as the pending signal."""
        if not self._render_deferred_active:
            return
        self._pending_dirty = True
        self._update_apply_state()

    def _update_apply_state(self) -> None:
        """Enable Apply iff there are un-rendered changes."""
        btn = getattr(self, "_apply_btn", None)
        if btn is None:
            return
        try:
            btn.configure(state="normal" if self._pending_dirty else "disabled")
        except tk.TclError:
            pass

    def _apply(self) -> None:
        """Render the current indicator settings to the chart now.

        Flushes exactly one ``_render`` via the app, clears the pending
        flag, and makes this the new Cancel baseline (so a later Cancel
        reverts only the changes made *after* this Apply — classic
        Apply semantics). Guarded no-op when nothing is pending (keeps
        the Ctrl+Return shortcut harmless)."""
        if not self._defers_render or not self._pending_dirty:
            return
        flush = getattr(self._app, "_flush_indicator_render", None)
        if callable(flush):
            try:
                flush()
            except Exception:  # noqa: BLE001
                pass
        self._pending_dirty = False
        # Apply becomes the revert point: Cancel after Apply keeps the
        # applied work and only discards subsequent un-applied edits.
        try:
            self._snapshot = self._manager.to_dict()
        except Exception:  # noqa: BLE001
            pass
        self._update_apply_state()

    def _on_auto_apply_toggled(self) -> None:
        """Auto-apply checkbox flipped.

        ON  → resume live rendering (end deferral) and flush the current
              state so the chart immediately reflects it.
        OFF → re-enter deferral (subsequent edits wait for Apply)."""
        if not self._defers_render:
            return
        if bool(self._auto_apply_var.get()):
            self._end_render_deferral()
            # Push current state to the chart so turning auto-apply on
            # behaves like an immediate Apply.
            flush = getattr(self._app, "_flush_indicator_render", None)
            if callable(flush):
                try:
                    flush()
                except Exception:  # noqa: BLE001
                    pass
            self._pending_dirty = False
            self._update_apply_state()
        else:
            self._begin_render_deferral()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        """Create the scrollable rows area and bottom button bar."""
        # Outer padding frame so the canvas + bottom bar share
        # consistent margins.
        outer = tk.Frame(self, padx=8, pady=8)
        outer.pack(fill="both", expand=True)
        # --- Header banner ---
        # Newly-added indicators default to the currently-active
        # interval only (see ``_on_click_add``). That surprised users
        # — adding a chandelier on the 1d chart, switching to 5m,
        # and seeing the line vanish silently. Banner makes the
        # behavior discoverable without changing the default.
        banner_text = (
            "Tip: newly added indicators are enabled only on the current "
            "chart interval. To show on additional intervals, expand the "
            "row and check the other interval boxes."
        )
        try:
            banner = ttk.Label(
                outer, text=banner_text,
                wraplength=520, justify="left",
                foreground="#666666",
            )
            banner.pack(fill="x", padx=2, pady=(0, 6))
            self._header_banner = banner
        except tk.TclError:
            self._header_banner = None
        # --- Bottom bar --- packed BEFORE the scrollable region so it always
        # claims its natural height regardless of dialog size. Canonical
        # Tkinter pattern for a fixed footer: anchor it with side="bottom"
        # first, then let the scrollable area fill the remaining space.
        bar = tk.Frame(outer)
        bar.pack(side="bottom", fill="x", pady=(8, 0))
        self._add_button = ttk.Button(
            bar, text="Add Indicator", command=self._on_click_add,
        )
        self._add_button.pack(side="left")
        ttk.Button(bar, text="Remove Selected",
                   command=self._on_click_remove).pack(side="left",
                                                       padx=(6, 0))
        # Auto-apply toggle (deferred-render feature). Default OFF: edits
        # wait for Apply. ON: edits render live (exploratory scrubbing).
        # Only meaningful for the full dialog (the popup renders live).
        if self._defers_render:
            self._auto_apply_chk = ttk.Checkbutton(
                bar, text="Auto-apply", variable=self._auto_apply_var,
                command=self._on_auto_apply_toggled,
            )
            self._auto_apply_chk.pack(side="left", padx=(12, 0))
        self._budget_label = ttk.Label(bar, text="", foreground=WARN_AMBER)
        self._budget_label.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Cancel",
                   command=self._on_cancel).pack(side="right")
        self._save_close_btn = ttk.Button(
            bar, text="Save and Close", command=self._on_save_close,
            state="disabled",
        )
        self._save_close_btn.pack(side="right", padx=(0, 6))
        # Apply (deferred-render flush) — sits left of Save and Close so
        # it's closest to the rows it affects. Enabled only when there
        # are un-rendered changes. Omitted entirely for live dialogs
        # (the per-indicator popup), which never defer.
        if self._defers_render:
            self._apply_btn = ttk.Button(
                bar, text="Apply", command=self._apply, state="disabled",
            )
            self._apply_btn.pack(side="right", padx=(0, 6))
        # --- Scrollable rows region --- fills the remaining middle space.
        # Canvas + Scrollbar + inner Frame triple is built by
        # :func:`_modal_base.make_scrollable_form` (audit item #5).
        # ``bind_mousewheel=False`` because this dialog keeps a
        # specialised wheel install path (``_install_wheel_bindings``
        # / ``_uninstall_wheel_bindings`` plus the per-button
        # ``_on_mousewheel`` / ``_on_button4`` / ``_on_button5``
        # methods below) that the unit tests in
        # ``test_indicator_dialog_mousewheel.py`` drive directly —
        # they predate the helper.
        scroll_wrap = tk.Frame(outer)
        scroll_wrap.pack(fill="both", expand=True)
        inner, canvas = make_scrollable_form(
            scroll_wrap, bind_mousewheel=False,
        )
        canvas.configure(height=320)

        # Mouse-wheel scrolling. The dialog is modeless, so we
        # install the global ``<MouseWheel>`` handler only while the
        # cursor is inside the canvas — otherwise spinning the wheel
        # over the main chart would also drive this canvas. Linux
        # uses ``<Button-4>`` / ``<Button-5>`` instead of
        # ``<MouseWheel>``; both are handled. Mirrors the proven
        # pattern in ``entries_dialog.py``.
        #
        # Callbacks are stashed as instance methods (``_on_mousewheel``
        # etc.) so headless tests can drive them directly without
        # relying on ``event_generate`` to deliver ``<Enter>`` /
        # ``<Leave>`` virtual events (which don't fire reliably on
        # Tk without a real cursor).
        self._rows_canvas = canvas
        canvas.bind("<Enter>", lambda _e: self._install_wheel_bindings())
        canvas.bind("<Leave>", lambda _e: self._uninstall_wheel_bindings())
        # Drop the global wheel binding when the dialog goes away to
        # avoid leaking it to the main chart if the user closes the
        # window without first moving the cursor outside the canvas.
        self.bind(
            "<Destroy>",
            lambda _e: self._uninstall_wheel_bindings(),
            add="+",
        )

        self._rows_inner = inner

    # ------------------------------------------------------------------
    # Mouse-wheel binding helpers (extracted as methods so headless
    # tests can drive them directly — Tk's ``<Enter>`` virtual event
    # only fires reliably with a real cursor).
    # ------------------------------------------------------------------

    def _on_mousewheel(self, e: tk.Event) -> None:
        canvas = getattr(self, "_rows_canvas", None)
        if canvas is None:
            return
        try:
            delta = int(getattr(e, "delta", 0))
            if delta:
                canvas.yview_scroll(int(-1 * (delta / 120)), "units")
        except tk.TclError:
            pass

    def _on_button4(self, _e: tk.Event) -> None:
        canvas = getattr(self, "_rows_canvas", None)
        if canvas is None:
            return
        try:
            canvas.yview_scroll(-1, "units")
        except tk.TclError:
            pass

    def _on_button5(self, _e: tk.Event) -> None:
        canvas = getattr(self, "_rows_canvas", None)
        if canvas is None:
            return
        try:
            canvas.yview_scroll(1, "units")
        except tk.TclError:
            pass

    def _install_wheel_bindings(self) -> None:
        """Install the global ``<MouseWheel>`` / ``<Button-4>`` /
        ``<Button-5>`` handlers on every Tk widget. Called when the
        cursor enters the rows canvas."""
        canvas = getattr(self, "_rows_canvas", None)
        if canvas is None:
            return
        try:
            canvas.bind_all("<MouseWheel>", self._on_mousewheel)
            canvas.bind_all("<Button-4>", self._on_button4)
            canvas.bind_all("<Button-5>", self._on_button5)
        except tk.TclError:
            pass

    def _uninstall_wheel_bindings(self) -> None:
        """Drop the global wheel handlers. Called when the cursor
        leaves the canvas (or when the dialog is destroyed) so wheel
        events over the main chart no longer drive this dialog."""
        canvas = getattr(self, "_rows_canvas", None)
        if canvas is None:
            return
        try:
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Row construction / teardown
    # ------------------------------------------------------------------

    def _build_row(
        self,
        cfg: IndicatorConfig | None,
        *,
        parent: tk.Widget | None = None,
        include_radio: bool = True,
        include_drag_handle: bool = True,
    ) -> _IndicatorRow:
        """Create a row, populated from ``cfg`` (or seeded with the
        first registered kind's defaults if ``cfg`` is None).

        ``parent`` overrides the default mount point
        (``self._rows_inner``) — the per-indicator popup uses this to
        mount the same row inside its own content frame without going
        through the manager-dialog scrollable canvas.

        ``include_radio`` and ``include_drag_handle`` toggle the
        leading "Remove Selected" radiobutton and the ``≡`` drag glyph
        respectively. The per-indicator popup hides both since it has
        no reorder / multi-selection chrome.

        The row is appended to ``self._rows`` and packed at the bottom
        of ``parent``. Returns the row so the caller can choose to
        commit immediately (Add Indicator path) or skip commit
        (manager-event reconciliation path)."""
        row = _IndicatorRow(next(_ROW_KEY))
        # Suppress commits during widget construction.
        row.suppress = True
        mount = parent if parent is not None else self._rows_inner
        # Outer container for this row, with a thin separator at the
        # bottom so multiple rows visually separate.
        container = tk.Frame(mount, padx=4, pady=4,
                             relief="ridge", borderwidth=1)
        container.pack(fill="x", pady=(0, 4))
        row.container = container
        # Top line: [radio] [drag] [kind dropdown] [help] [Primary chk] [Compare chk]
        top = tk.Frame(container)
        top.pack(fill="x")
        if include_radio:
            row.radio_btn = ttk.Radiobutton(
                top, value=row.row_key, variable=self._selected_key,
            )
            row.radio_btn.pack(side="left")
        if include_drag_handle:
            # Drag-and-drop handle (b43). The ``≡`` glyph signals that the
            # row can be reordered. The label itself owns the
            # press/motion/release bindings so motion events from child
            # widgets don't pollute the drag state. ``Alt+↑`` / ``Alt+↓``
            # on the row container provide a keyboard fallback that the
            # smoke harness exercises (synthesised matplotlib-style mouse
            # drags through Tk are unreliable on Windows CI).
            row.drag_handle = tk.Label(
                top, text="\u2630", cursor="sb_v_double_arrow", padx=4,
                takefocus=False,
            )
            row.drag_handle.pack(side="left", padx=(2, 4))
            # Tooltip hint surfaces on hover (450ms delay). The dialog
            # holds a reference list so the tooltip outlives the local
            # binding scope here.
            self._tooltips.append(ToolTip(row.drag_handle, "Drag to reorder"))
            row.drag_handle.bind(
                "<ButtonPress-1>",
                lambda _e, r=row: self._on_drag_start(r),
            )
            row.drag_handle.bind(
                "<B1-Motion>",
                lambda e, r=row: self._on_drag_motion(r, e),
            )
            row.drag_handle.bind(
                "<ButtonRelease-1>",
                lambda _e, r=row: self._on_drag_release(r),
            )
        # Keyboard reorder. Bound on every widget that can plausibly
        # have focus inside the row so the user doesn't have to hunt
        # for the right one. ``add="+"`` so we don't clobber any
        # existing bindings on the radio / handle. Bindings are only
        # meaningful when the row participates in the multi-row
        # manager dialog — the per-indicator popup omits radio + drag
        # entirely and the keyboard hooks just no-op via
        # ``_move_row_by_keyboard`` (which short-circuits when
        # ``len(self._rows) < 2``).
        kbd_widgets: list[tk.Widget] = [container, top]
        if row.radio_btn is not None:
            kbd_widgets.append(row.radio_btn)
        if row.drag_handle is not None:
            kbd_widgets.append(row.drag_handle)
        for w in kbd_widgets:
            w.bind(
                "<Alt-Up>",
                lambda _e, r=row: self._move_row_by_keyboard(r, -1),
                add="+",
            )
            w.bind(
                "<Alt-Down>",
                lambda _e, r=row: self._move_row_by_keyboard(r, +1),
                add="+",
            )
        row.kind_var = tk.StringVar()
        row.kind_combo = ttk.Combobox(
            top,
            textvariable=row.kind_var,
            state="normal",
            values=tuple(self._kinds_by_display.keys()),
            width=28,
        )
        row.kind_combo.pack(side="left", padx=(4, 8))
        # Help icon — opens per-indicator documentation. The blue
        # foreground colour is signal (it visually marks the icon as
        # interactive), so we keep it intact in dark mode — the
        # theme walker honors ``_preserve_fg``. Audit
        # ``indicator-dialog-label-theme``.
        help_label = tk.Label(
            top, text="\u24d8", foreground="#58a6ff", cursor="hand2",
            font=("TkDefaultFont", 10),
        )
        help_label._preserve_fg = True  # type: ignore[attr-defined]
        help_label.pack(side="left", padx=(6, 0))
        help_label.bind(
            "<Button-1>",
            lambda _e, r=row: self._open_indicator_help(r),
        )
        row.help_label = help_label
        # Tooltip on the kind selector — surfaces the full name of the
        # acronym ("RSI" → "Relative Strength Index") and a one-line
        # blurb explaining what it measures. Helps new users decode
        # the indicator menu without leaving the dialog. The text is
        # kept in sync with the current selection by
        # ``_on_kind_changed`` and the initial ``_hydrate_row_from_config``.
        row.kind_tooltip = ToolTip(row.kind_combo, "")
        self._tooltips.append(row.kind_tooltip)
        row.kind_combo.bind(
            "<<ComboboxSelected>>",
            lambda _e, r=row: self._on_kind_changed(r),
        )
        row.kind_combo.bind(
            "<KeyRelease>",
            lambda _e, r=row: self._on_kind_combo_keyrelease(r),
        )
        row.kind_combo.bind(
            "<Return>",
            lambda _e, r=row: self._on_kind_changed(r),
        )
        row.kind_combo.bind(
            "<FocusOut>",
            lambda _e, r=row: self._on_kind_changed(r),
        )
        # Scope checkboxes — Primary / Compare. Both unchecked ⇒
        # visible=False; manager.update preserves the row's last
        # active scopes so re-checking restores them.
        row.primary_var = tk.BooleanVar(value=False)
        row.compare_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top, text="Primary", variable=row.primary_var,
            command=lambda r=row: self._commit_now(r),
        ).pack(side="left", padx=(0, 4))
        ttk.Checkbutton(
            top, text="Compare", variable=row.compare_var,
            command=lambda r=row: self._commit_now(r),
        ).pack(side="left")
        # Per-interval visibility row (b41). Checkboxes are dynamic:
        # the available set follows the active sandbox display_intervals
        # while a session is running, and the full toolbar interval set
        # otherwise. Empty selection (= all unchecked) is normalized at
        # commit time to "all checked" rather than "indicator hidden
        # everywhere", so a user who blanks the row doesn't make the
        # indicator silently disappear from every chart.
        row.interval_subframe = tk.Frame(container)
        row.interval_subframe.pack(fill="x", pady=(2, 0))
        # Per-output color picker row (b42). One small swatch button
        # per output key — clicking opens the native OS color chooser.
        row.color_subframe = tk.Frame(container)
        row.color_subframe.pack(fill="x", pady=(2, 0))
        # Param area: a sub-frame we destroy + rebuild whenever the
        # selected kind changes. Lives on its own line under the top
        # row so long param sets wrap naturally.
        row.param_subframe = tk.Frame(container)
        row.param_subframe.pack(fill="x", pady=(4, 0))
        # Hydrate widget state from cfg (or defaults).
        self._hydrate_row_from_config(row, cfg)
        # Append AFTER widgets exist so reconciliation doesn't see a
        # half-built row in self._rows.
        self._rows.append(row)
        # Construction done — allow commits.
        row.suppress = False
        # Repaint freshly-created tk.Frame containers so they match
        # the current theme (otherwise rows added in dark mode
        # render with a default light background).
        try:
            self._apply_theme()
        except Exception:  # noqa: BLE001
            pass
        return row

    def _hydrate_row_from_config(
        self, row: _IndicatorRow, cfg: IndicatorConfig | None,
    ) -> None:
        """Set kind_var, scope vars, build param widgets, and stash
        last_good_params / preserved_extra_scopes from ``cfg``.

        When ``cfg`` is None (Add Indicator click), seeds the row with
        the first registered kind and that factory's default params.
        """
        # Determine the kind to display.
        if cfg is None:
            # Seed: first registered display-name.
            if not self._kinds_by_display:
                # No indicators registered (extreme edge case in tests).
                row.kind_var.set("")
                return
            display_name = next(iter(self._kinds_by_display.keys()))
            kind_id = self._kinds_by_display[display_name]
            params: dict[str, Any] = {}
            scopes: frozenset[str] = DEFAULT_SCOPES
            visible = True
            row.is_unknown = False
            row.config_id = None
        else:
            row.config_id = cfg.id
            row.is_unknown = bool(cfg.unknown)
            kind_id = cfg.kind_id
            params = dict(cfg.params)
            scopes = cfg.scopes
            visible = cfg.visible
            display_name = self._display_for_kind_id(kind_id)
            if row.is_unknown:
                # Show a clear placeholder label and leave the kind
                # combobox in a non-selectable, non-resolving state.
                display_name = f"Unknown indicator ({kind_id})"
        row.kind_var.set(display_name)
        self._refresh_kind_tooltip(row, kind_id)
        # Scope state — visible flag overrides; if visible=False we
        # show both checkboxes off but remember the active scopes for
        # later restoration.
        active = frozenset(scopes & {"main", "compare"})
        if active:
            row.preserved_active_scopes = active
        else:
            # If a user previously saved a config with empty
            # main/compare scopes (e.g. drilldown-only), keep the
            # default fallback so re-checking restores something
            # meaningful.
            row.preserved_active_scopes = DEFAULT_SCOPES
        if visible and "main" in scopes:
            row.primary_var.set(True)
        else:
            row.primary_var.set(False)
        if visible and "compare" in scopes:
            row.compare_var.set(True)
        else:
            row.compare_var.set(False)
        row.preserved_extra_scopes = frozenset(scopes - {"main", "compare"})
        # Per-interval visibility (b41). Empty cfg.intervals = "all";
        # explicit tuple = only those intervals.
        if cfg is None:
            # Default for newly-added indicator: only the currently
            # active chart interval. Set in _commit_first_for_new_row
            # rather than here, because at hydrate time we don't know
            # if the row is "new from Add Indicator" vs "new from a
            # manager event for an existing config".
            row.preserved_intervals = ()
        else:
            row.preserved_intervals = tuple(cfg.intervals)
        self._rebuild_interval_checkboxes(row)
        # Per-output color overrides (b42). Hydrate from the saved
        # config's style dict — only the entries the user actually
        # changed are kept; the others fall back to default_style.
        row.style_overrides = {}
        if cfg is not None:
            for k, ls in (cfg.style or {}).items():
                col = getattr(ls, "color", None)
                if col:
                    row.style_overrides[str(k)] = str(col)
        self._rebuild_color_buttons(row, kind_id)
        # Last-good params snapshot: starts at the hydrated values so
        # an immediate revert before the user has typed anything goes
        # back to the on-disk state.
        row.last_good_params = dict(params)
        # Build param widgets.
        self._build_param_widgets(row, kind_id, params)
        # Disable everything for unknown rows except the radio + the
        # row's own remove path (handled at the bottom-bar level).
        if row.is_unknown:
            self._set_row_editable(row, False)
        else:
            self._set_row_editable(row, True)

    def _set_row_editable(self, row: _IndicatorRow, editable: bool) -> None:
        """Enable / disable every editable widget in the row.

        Called once per row after hydration. Unknown-kind rows pass
        ``editable=False`` so the user can read but not silently
        rewrite the placeholder."""
        state = "normal" if editable else "disabled"
        ro_state = "readonly" if editable else "disabled"
        try:
            row.kind_combo.configure(state=ro_state)  # type: ignore[union-attr]
        except tk.TclError:
            pass
        # Iterate child widgets in the param subframe + the scope
        # checkboxes (top row). The radiobutton stays enabled so the
        # user can still select an unknown row to remove it.
        for w in row.param_widgets.values():
            try:
                w.configure(state=state)
            except tk.TclError:
                pass
        # The two scope Checkbuttons are children of ``container``'s
        # first child (the "top" frame); walk by widget class.
        if row.container is not None:
            for child in row.container.winfo_children():
                for sub in child.winfo_children():
                    if isinstance(sub, ttk.Checkbutton):
                        try:
                            sub.configure(state=state)
                        except tk.TclError:
                            pass

    def _build_param_widgets(
        self, row: _IndicatorRow, kind_id: str,
        seed_values: dict[str, Any],
    ) -> None:
        """Destroy and rebuild the row's param subframe to match the
        ``params_schema`` of ``kind_id``.

        ``seed_values`` provides initial values for params that exist
        in the new schema (e.g. preserving ``length`` across
        SMA→EMA). Schema entries with no seed value fall back to the
        ParamDef default. Extra keys in ``seed_values`` not declared
        by the new schema are dropped (they don't survive the kind
        change anyway)."""
        # Per-session muscle-memory hook: when adding (or kind-switching
        # to) a Moving Average row without an explicit ``ma_type``, seed
        # with the last MA type the user picked this session. Keeps the
        # SMA-vs-EMA bias sticky without persisting to disk.
        if kind_id == "ma" and "ma_type" not in seed_values:
            seed_values = dict(seed_values)
            seed_values["ma_type"] = type(self)._last_used_ma_type
        # Clear existing.
        sub = row.param_subframe
        if sub is None:
            return
        # Record the kind whose widgets we are about to render so a
        # later spurious ``<FocusOut>`` on the kind combobox can
        # short-circuit in ``_on_kind_changed`` (see that method + the
        # ``applied_kind_id`` field doc) rather than rebuilding the row
        # and re-walking the whole dialog via ``_apply_theme``.
        row.applied_kind_id = kind_id
        for w in sub.winfo_children():
            try:
                w.destroy()
            except tk.TclError:
                pass
        row.param_vars = {}
        row.param_widgets = {}
        if row.is_unknown:
            # Show an informational label instead of widgets.
            ttk.Label(sub, text=f"(read-only — unknown kind '{kind_id}')")\
                .pack(side="left")
            return
        pair = factory_by_kind_id(kind_id)
        if pair is None:
            ttk.Label(sub, text=f"(unknown kind: {kind_id!r})")\
                .pack(side="left")
            return
        _display_name, factory_cls = pair
        schema = getattr(factory_cls, "params_schema", ()) or ()
        # Adaptive grid: each cell sizes itself to its content
        # (combobox to its widest choice, spinbox to its max digit
        # count, str entry to a moderate default). Then the number of
        # columns per visual row is chosen to fit inside the dialog's
        # current width. This lets ATR's longer ``session_filter``
        # choices (e.g. ``"regular_plus_premarket"``) wrap to a second
        # row instead of clipping the rightmost widget. Keep
        # ``ParamDef.description`` short (≤ ~12 chars); long prose
        # belongs in the indicator's colocated ``.spec.md``.
        max_cols = self._compute_max_cols_for_schema(schema)
        row.param_max_cols_applied = max_cols
        for i, pdef in enumerate(schema):
            grid_row = i // max_cols
            grid_col = i % max_cols
            self._build_one_param_widget(
                row, pdef, seed_values,
                grid_pos=(grid_row, grid_col),
            )
        # Param subframe was just rebuilt — repaint the new
        # ``tk.Frame`` wrappers so they don't flash light on a dark
        # background when the user changes kind.
        try:
            self._apply_theme()
        except Exception:  # noqa: BLE001
            pass

    def _compute_max_cols_for_schema(self, schema: tuple[Any, ...]) -> int:
        """How many parameter cells should one visual row hold?

        Estimates the natural cell width per ParamDef (label text +
        widget content) using the shared font metrics from
        :mod:`gui._widget_metrics` (`_CHAR_PX`), divides the dialog's
        inner-frame width by the widest estimate, and floors to an
        integer column count clamped at 1. The fit-based math itself
        already prevents overflow — there is no upper clamp, so on a
        wide screen ATR's 6-param schema may sit on a single row.

        Falls back to the dialog's own width (minus chrome) when
        ``_rows_inner`` hasn't been laid out yet (first paint during
        ``__init__`` happens before the WM maps the dialog). The first
        real ``<Configure>`` after mapping triggers
        :meth:`_do_resize_reflow_rows` which re-grids each row to the
        correct column count.
        """
        if not schema:
            return 1

        def _cell_chars(p: Any) -> int:
            # Label text width
            label_chars = len((getattr(p, "description", None)
                                or getattr(p, "name", "")) or "") + 2
            # Widget content width estimate
            kind = getattr(p, "kind", "str")
            if kind == "bool":
                widget_chars = 3
            elif kind == "choice":
                widget_chars = _combo_width_for_choices(
                    getattr(p, "choices", ())
                )
            elif kind in ("int", "float"):
                widget_chars = _spinbox_width_for(p)
            elif kind == "str" and getattr(p, "choices", ()):
                widget_chars = max(
                    _combo_width_for_choices(p.choices), 8,
                )
            else:
                widget_chars = 14
            # 4-char padding between label and widget; 4-char
            # right-padding between cells.
            return label_chars + 4 + widget_chars + 4

        widest_chars = max(_cell_chars(p) for p in schema)
        # Use the shared ``_CHAR_PX`` constant from
        # :mod:`gui._widget_metrics` so a future font-metric tuning
        # propagates into both this dialog and the
        # ``_ConditionFrame`` inline-vs-stacked classifier
        # (CLAUDE.md §7.19).
        widest_px = max(80, int(widest_chars * _CHAR_PX))

        avail_px = self._available_row_width_px()
        if avail_px <= 1:
            return 1

        cols = max(1, avail_px // widest_px)
        return int(cols)

    # ------------------------------------------------------------------
    # Resize-reactive layout (audit item #1; CLAUDE.md §7.19 pattern)
    # ------------------------------------------------------------------

    def _on_toplevel_resize(self, event: Any | None = None) -> None:
        """Debounced ``<Configure>`` handler — re-flow param grids on resize.

        Filters to only the dialog Toplevel itself (Tk's ``<Configure>``
        only fires on the bound widget, so this is defence in depth)
        and schedules :meth:`_do_resize_reflow_rows` ~100 ms later.
        Re-firing within the debounce window cancels the prior pending
        callback so a continuous drag results in ONE final reflow pass
        rather than dozens.

        Mirrors :meth:`_ConditionFrame._on_toplevel_resize` in
        ``scanner_block_editor.py``.
        """
        if event is not None and getattr(event, "widget", None) is not self:
            return
        if self._rows_resize_after_id is not None:
            try:
                self.after_cancel(self._rows_resize_after_id)
            except tk.TclError:
                pass
            self._rows_resize_after_id = None
        try:
            if not self.winfo_exists():
                return
            self._rows_resize_after_id = self.after(
                100, self._do_resize_reflow_rows,
            )
        except tk.TclError:
            pass

    def _do_resize_reflow_rows(self) -> None:
        """Per-row hysteresis-gated re-grid of param-wrap frames.

        For each row, recomputes the fit-based column count via
        :meth:`_compute_max_cols_for_schema` and re-grids the existing
        param-wrap frames if (and only if) the target column count
        differs from ``row.param_max_cols_applied``. The hysteresis
        gate is the discrete integer column count itself — dragging
        the dialog edge does NOT thrash rows whose schemas continue
        to fit at the same column count.

        Does NOT destroy or rebuild the param widgets — preserving
        focus and partial input. The wrap frames are children of
        ``row.param_subframe`` in creation order (matching the
        ParamDef schema order); we just call ``grid_configure`` to
        move them to the new ``(row, col)``.
        """
        self._rows_resize_after_id = None
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        for row in self._rows:
            self._maybe_regrid_row_params(row)
            self._maybe_regrid_row_intervals(row)

    def _maybe_regrid_row_params(self, row: _IndicatorRow) -> None:
        """Re-grid one row's param wraps if its column count changed."""
        sub = row.param_subframe
        if sub is None:
            return
        try:
            if not sub.winfo_exists():
                return
        except tk.TclError:
            return
        if row.is_unknown or row.kind_var is None:
            return
        display = (row.kind_var.get() or "").strip()
        kind_id = self._kinds_by_display.get(display)
        if not kind_id:
            return
        pair = factory_by_kind_id(kind_id)
        if pair is None:
            return
        _name, factory_cls = pair
        schema = getattr(factory_cls, "params_schema", ()) or ()
        if not schema:
            return
        new_max_cols = self._compute_max_cols_for_existing_param_widgets(row)
        if new_max_cols is None:
            new_max_cols = self._compute_max_cols_for_schema(schema)
        if new_max_cols == row.param_max_cols_applied:
            return
        # Wrap frames live as direct children of ``param_subframe``
        # in creation order; ``_build_one_param_widget`` made one
        # ``tk.Frame`` per ParamDef in schema order.
        try:
            children = [w for w in sub.winfo_children()
                        if isinstance(w, tk.Frame)]
        except tk.TclError:
            return
        for i, wrap in enumerate(children):
            try:
                wrap.grid_configure(
                    row=i // new_max_cols,
                    column=i % new_max_cols,
                )
            except tk.TclError:
                pass
        row.param_max_cols_applied = new_max_cols

    def _compute_max_cols_for_existing_param_widgets(
        self, row: _IndicatorRow,
    ) -> int | None:
        """Column count from actual widget request widths, if available."""
        sub = row.param_subframe
        if sub is None:
            return None
        try:
            children = [w for w in sub.winfo_children() if isinstance(w, tk.Frame)]
        except tk.TclError:
            return None
        if not children:
            return None
        widths: list[int] = []
        for child in children:
            try:
                child.update_idletasks()
                widths.append(max(1, int(child.winfo_reqwidth()) + 12))
            except tk.TclError:
                return None
        avail_px = self._available_row_width_px()
        if avail_px <= 1:
            return 1
        widest_px = max(widths)
        return max(1, avail_px // widest_px)

    def _available_row_width_px(self) -> int:
        try:
            avail_px = int(self._rows_inner.winfo_width() or 0)
            if avail_px <= 1:
                avail_px = int(self._rows_inner.winfo_reqwidth() or 0)
        except Exception:  # noqa: BLE001
            avail_px = 0
        if avail_px <= 1:
            try:
                avail_px = int(self.winfo_width() or self.winfo_reqwidth() or 0)
            except Exception:  # noqa: BLE001
                avail_px = 0
        if avail_px <= 1:
            avail_px = 880
        return max(0, avail_px - 32)

    def _maybe_regrid_row_intervals(self, row: _IndicatorRow) -> None:
        sf = row.interval_subframe
        if sf is None:
            return
        try:
            children = list(sf.winfo_children())
        except tk.TclError:
            return
        if len(children) <= 1:
            return
        avail_px = self._available_row_width_px()
        widths: list[int] = []
        for child in children:
            try:
                child.update_idletasks()
                widths.append(max(1, int(child.winfo_reqwidth()) + 4))
            except tk.TclError:
                return
        row_index = 0
        col_index = 0
        used = 0
        placements: list[tuple[int, int]] = []
        for width in widths:
            if col_index and used + width > avail_px:
                row_index += 1
                col_index = 0
                used = 0
            placements.append((row_index, col_index))
            used += width
            col_index += 1
        max_cols = max((col for _row, col in placements), default=0) + 1
        if max_cols == row.interval_max_cols_applied:
            return
        for child, (grid_row, grid_col) in zip(children, placements, strict=True):
            try:
                child.grid_configure(row=grid_row, column=grid_col)
            except tk.TclError:
                pass
        row.interval_max_cols_applied = max_cols

    def _on_destroy_resize_binding(self, _event: Any | None = None) -> None:
        """Tear down pending resize callback + ``<Configure>`` binding."""
        if self._rows_resize_after_id is not None:
            try:
                self.after_cancel(self._rows_resize_after_id)
            except tk.TclError:
                pass
            self._rows_resize_after_id = None
        if self._rows_resize_bind_id:
            try:
                self.unbind("<Configure>", self._rows_resize_bind_id)
            except tk.TclError:
                pass
            self._rows_resize_bind_id = None


    def _build_one_param_widget(
        self, row: _IndicatorRow, pdef: Any,
        seed_values: dict[str, Any],
        grid_pos: tuple[int, int] | None = None,
    ) -> None:
        """Render a single ParamDef as label + appropriate widget.

        Delegates the per-kind dispatcher to
        :func:`gui._param_widgets.build_param_widget` — the single
        source of truth shared with ``scanner_block_editor``. This
        method retains responsibility for the ``param_subframe``
        layout (grid vs pack), label rendering, recording the
        ``param_vars`` / ``param_widgets`` entries on the row, and
        the Anchored VWAP ``anchor_ts`` special-case (the inner
        Button must remain individually addressable so the dialog
        disables it on read-only rows).
        """
        sub = row.param_subframe
        wrap = tk.Frame(sub)
        if grid_pos is not None:
            grid_row, grid_col = grid_pos
            wrap.grid(row=grid_row, column=grid_col,
                      sticky="w", padx=(0, 12), pady=(0, 2))
        else:
            wrap.pack(side="left", padx=(0, 8))
        label = ttk.Label(wrap, text=label_text_for(pdef))
        label.pack(side="left")
        seed = seed_values.get(pdef.name, pdef.default)

        # Anchor_ts kept inline (not delegated): the inner Button
        # must be stored in ``param_widgets[name]`` directly so the
        # unknown-row read-only path can ``configure(state=…)`` it.
        if pdef.kind == "str" and pdef.name == "anchor_ts":
            var = tk.StringVar(value=str(seed))
            display = tk.StringVar(value=_format_anchor_label(str(seed)))
            lbl = ttk.Label(wrap, textvariable=display, width=18)
            lbl.pack(side="left", padx=(2, 4))
            btn = ttk.Button(
                wrap, text="Pick Anchor…",
                command=lambda r=row: self._on_pick_anchor(r),
            )
            btn.pack(side="left")
            var.trace_add(
                "write",
                lambda *_a, v=var, d=display: d.set(_format_anchor_label(v.get())),
            )
            row.param_vars[pdef.name] = var
            row.param_widgets[pdef.name] = btn
            return

        # Pre-compute per-kind width to preserve the schema-driven
        # cell sizing the dialog has historically used.
        kind = getattr(pdef, "kind", "str")
        if kind == "choice":
            width: int | None = _combo_width_for_choices(pdef.choices)
        elif kind == "str" and getattr(pdef, "choices", ()):
            width = max(_combo_width_for_choices(pdef.choices), 8)
        elif kind in ("int", "float"):
            width = _spinbox_width_for(pdef)
        else:
            width = None

        var, widget = build_param_widget(
            wrap, pdef, seed,
            on_change=lambda r=row: self._commit_debounced(r),
            on_commit_eager=lambda r=row: self._commit_now(r),
            commit_policy="debounced",
            debounce_ms=_TYPING_DEBOUNCE_MS,
            width=width,
        )
        widget.pack(side="left", padx=(2, 0))
        tip_text = tooltip_text_for(pdef)
        if tip_text:
            self._tooltips.append(ToolTip(label, tip_text))
            self._tooltips.append(ToolTip(widget, tip_text))
        row.param_vars[pdef.name] = var
        row.param_widgets[pdef.name] = widget

    # ------------------------------------------------------------------
    # Manager event reconciliation
    # ------------------------------------------------------------------

    def _on_manager_event(
        self, event: str, _cfg: IndicatorConfig | None,
    ) -> None:
        """Manager subscribers — pulled events keep dialog rows in
        step with external mutations.

        ``redraw`` events are ignored (the chart subscribes for
        those). For everything else we rebuild from the current
        ``manager.list()``: cheap (handful of widgets) and avoids the
        complexity of per-event diffing for an editor that the user
        is normally interacting with one row at a time.

        When ``self._restricted_to_config_id`` is set (per-indicator
        popup), the dialog self-destructs if the restricted config is
        gone (``remove`` of our id, ``clear``, ``loaded``,
        ``preset_loaded``) since the popup has no useful state to
        present once the underlying config disappears."""
        if event == "redraw":
            return
        if self._reconciling:
            return  # Our own commit just fired; ignore the echo.
        # Mark the dialog dirty on any non-cosmetic mutation.
        if event in ("add", "remove", "update", "clear", "reorder",
                      "loaded", "preset_loaded"):
            self._mark_dirty()
            # An external (non-own-commit) mutation while deferring means
            # the chart is now behind — surface it via Apply.
            self._mark_pending()
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        if self._restricted_to_config_id is not None:
            if event in ("clear", "loaded", "preset_loaded"):
                # Wholesale rebuilds invalidate the ``IndicatorConfig.id``
                # space (ids are process-monotonic and re-issued on
                # hydrate). The popup's tracked id is no longer
                # meaningful — close rather than guess.
                self._on_close()
                return
            if event == "remove":
                if _cfg is not None and getattr(_cfg, "id", None) == \
                        self._restricted_to_config_id:
                    self._on_close()
                    return
                # Some other config was removed — irrelevant.
                return
            if event in ("add", "reorder"):
                # Neither affects the popup's view of its one config.
                return
            # event == "update" (or anything else): fall through to
            # reconcile so any external change to our config is picked
            # up in the popup widgets.
        self._reconcile_from_manager()

    def _reconcile_from_manager(self) -> None:
        """Atomic rebuild: destroy every existing row, then re-create
        rows from ``manager.list()``.

        The selection key is preserved when the matching row still
        exists by config id; otherwise selection clears (0). When
        ``self._restricted_to_config_id`` is set, only that one config
        is rebuilt (other configs in the manager are ignored)."""
        self._reconciling = True
        try:
            old_selection = int(self._selected_key.get() or 0)
            old_selection_cid: int | None = None
            for r in self._rows:
                if r.row_key == old_selection:
                    old_selection_cid = r.config_id
                    break
            # Destroy existing.
            for r in list(self._rows):
                self._cancel_pending_debounce(r)
                if r.container is not None:
                    try:
                        r.container.destroy()
                    except tk.TclError:
                        pass
            self._rows = []
            self._selected_key.set(0)
            # Rebuild.
            for cfg in self._manager.list():
                if self._restricted_to_config_id is not None and \
                        cfg.id != self._restricted_to_config_id:
                    continue
                self._build_row(cfg)
            # Restore selection if the same config still exists.
            if old_selection_cid is not None:
                for r in self._rows:
                    if r.config_id == old_selection_cid:
                        self._selected_key.set(r.row_key)
                        break
        finally:
            self._reconciling = False
        # Newly-rebuilt rows contain fresh Combobox/Spinbox widgets
        # whose ttk class binding would silently mutate the selected
        # value on wheel-over (see CLAUDE.md §7.11). Re-apply the
        # guard so all freshly-created widgets are protected.
        self._protect_combobox_wheel()

    # ------------------------------------------------------------------
    # Drag-to-reorder (b43)
    # ------------------------------------------------------------------

    def _row_index(self, row: _IndicatorRow) -> int:
        """Return ``row``'s current position in ``self._rows``, or
        ``-1`` if it has been removed (e.g. by a manager event firing
        between drag-start and drag-release)."""
        for i, r in enumerate(self._rows):
            if r is row:
                return i
        return -1

    def _compute_drop_target(self, mouse_y_in_inner: int) -> int:
        """Translate ``mouse_y_in_inner`` (a y coordinate inside
        ``self._rows_inner``) into a "gap index" in ``[0, len(rows)]``.

        The gap index is the visual insert position before any row is
        removed: 0 = above the first row, ``len(rows)`` = below the
        last. The caller adjusts for the dragged row's current
        position when calling :meth:`IndicatorManager.reorder` (whose
        target is interpreted on the post-removal list)."""
        for i, r in enumerate(self._rows):
            if r.container is None:
                continue
            try:
                top = r.container.winfo_y()
                height = r.container.winfo_height()
            except tk.TclError:
                continue
            if mouse_y_in_inner < top + (height // 2):
                return i
        return len(self._rows)

    def _ensure_drop_indicator(self) -> tk.Frame | None:
        """Lazily create the thin Frame that visualises the drop
        position. Lives on ``_rows_inner`` and is shown via
        ``place()`` only while a drag is in flight."""
        if self._drag_indicator is not None:
            return self._drag_indicator
        try:
            ind = tk.Frame(
                self._rows_inner, height=3, bg="#3b82f6",
            )
        except tk.TclError:
            return None
        self._drag_indicator = ind
        return ind

    def _show_drop_indicator(self, gap_index: int) -> None:
        """Place the drop indicator above the row at ``gap_index`` (or
        below the last row if ``gap_index == len(rows)``)."""
        ind = self._ensure_drop_indicator()
        if ind is None or not self._rows:
            return
        try:
            if gap_index >= len(self._rows):
                # Below the last row.
                last = self._rows[-1].container
                if last is None:
                    return
                y = last.winfo_y() + last.winfo_height()
            else:
                target = self._rows[gap_index].container
                if target is None:
                    return
                y = target.winfo_y()
            inner_w = max(1, self._rows_inner.winfo_width())
            ind.place(x=0, y=max(0, y - 1), width=inner_w, height=3)
            ind.lift()
        except tk.TclError:
            pass

    def _hide_drop_indicator(self) -> None:
        ind = self._drag_indicator
        if ind is None:
            return
        try:
            ind.place_forget()
        except tk.TclError:
            pass

    def _on_drag_start(self, row: _IndicatorRow) -> None:
        if self._reconciling:
            return
        self._drag_row = row
        self._drag_target_index = self._row_index(row)

    def _on_drag_motion(self, row: _IndicatorRow, event: Any) -> None:
        if self._drag_row is not row:
            return
        try:
            inner_y_root = self._rows_inner.winfo_rooty()
            mouse_y_root = event.y_root
        except tk.TclError:
            return
        gap = self._compute_drop_target(mouse_y_root - inner_y_root)
        self._drag_target_index = gap
        self._show_drop_indicator(gap)

    def _on_drag_release(self, row: _IndicatorRow) -> None:
        if self._drag_row is not row:
            return
        gap = self._drag_target_index
        self._drag_row = None
        self._hide_drop_indicator()
        cur = self._row_index(row)
        if cur < 0 or row.config_id is None:
            return
        # Translate visual gap (pre-removal) into post-removal index
        # that ``manager.reorder`` expects: items below the source
        # shift up by one when the source is popped.
        target = gap - 1 if gap > cur else gap
        if target == cur:
            return
        self._manager.reorder(row.config_id, target)

    def _move_row_by_keyboard(
        self, row: _IndicatorRow, delta: int,
    ) -> str:
        """``Alt+↑`` / ``Alt+↓`` keyboard fallback. Moves the row by
        ``delta`` slots and returns ``"break"`` so Tk doesn't also
        navigate focus."""
        if self._reconciling or row.config_id is None:
            return "break"
        cur = self._row_index(row)
        if cur < 0:
            return "break"
        new_index = cur + delta
        if new_index < 0 or new_index >= len(self._rows):
            return "break"
        self._manager.reorder(row.config_id, new_index)
        return "break"

    # ------------------------------------------------------------------
    # Kind-change handler
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------

    def _on_pick_anchor(self, row: _IndicatorRow) -> None:
        """Arm the chart for a one-shot anchor-pick click.

        Delegates to ``ChartApp._begin_anchor_pick(config_id)``. The
        app is responsible for cursor + status feedback, intercepting
        the next chart click, and calling ``manager.update`` with the
        new ``anchor_ts`` (merged into existing params so
        ``price_source`` / ``bands`` are preserved).

        Defensive: if the host app doesn't expose
        ``_begin_anchor_pick`` (older / partial test harnesses), the
        button is a no-op rather than raising.
        """
        cfg_id = row.config_id
        if cfg_id is None:
            return
        begin = getattr(self._app, "_begin_anchor_pick", None)
        if not callable(begin):
            return
        try:
            begin(cfg_id)
        except Exception:  # noqa: BLE001
            pass

    def _on_kind_changed(self, row: _IndicatorRow) -> None:
        """User picked a different indicator kind from the dropdown.

        Rebuilds param widgets to match the new schema (params don't
        transfer — different indicators have different schemas). The
        new defaults are committed via ``_commit_now``; if they fail
        validation the row falls back to last_good (which still has
        the OLD kind's params, so the manager state is unchanged
        until the user fixes the inputs)."""
        if row.suppress or row.is_unknown:
            return
        new_display = (row.kind_var.get() or "").strip()
        new_kind_id = self._kinds_by_display.get(new_display)
        if not new_kind_id:
            matches = _filter_indicator_kind_displays(self._kinds_by_display, new_display)
            if len(matches) != 1:
                return
            new_display = matches[0]
            new_kind_id = self._kinds_by_display[new_display]
            row.kind_var.set(new_display)
        self._refresh_kind_tooltip(row, new_kind_id)
        # Idempotency guard (flicker fix). The kind combobox binds
        # ``<FocusOut>`` so a typed-and-tabbed-away kind name still
        # commits — but on Windows ttk also fires ``<FocusOut>`` when
        # the dropdown popdown is merely posted/dismissed, and a
        # re-pick of the same value fires ``<<ComboboxSelected>>``.
        # In both cases the resolved kind is UNCHANGED, so tearing down
        # + rebuilding the param widgets and re-walking the entire
        # dialog via ``_apply_theme`` would just make the window flicker
        # for no state change. Only rebuild when the kind actually
        # differs from what's currently rendered (``applied_kind_id``).
        if new_kind_id == row.applied_kind_id:
            return
        # Suppress while we tear down + rebuild widgets so the trace
        # callbacks fired by ``var.set`` during construction don't
        # ping us back into another commit cycle.
        self._cancel_pending_debounce(row)
        row.suppress = True
        try:
            # Carry over any param values that exist in the new
            # schema (e.g. ``length`` survives SMA → EMA).
            seed = self._collect_param_values(row)
            self._build_param_widgets(row, new_kind_id, seed)
            # Output keys differ per kind — clear stale per-output
            # color overrides and rebuild the color-swatch row to
            # match the new factory's default_style keys (b42).
            row.style_overrides = {}
            self._rebuild_color_buttons(row, new_kind_id)
        finally:
            row.suppress = False
        self._commit_now(row)
        # Param widgets were torn down and rebuilt — re-guard the new
        # Combobox/Spinbox descendants (see CLAUDE.md §7.11).
        self._protect_combobox_wheel()

    def _on_kind_combo_keyrelease(self, row: _IndicatorRow) -> None:
        combo = row.kind_combo
        if combo is None:
            return
        query = (row.kind_var.get() or "").strip()
        values = _filter_indicator_kind_displays(self._kinds_by_display, query)
        if not values:
            values = tuple(self._kinds_by_display.keys())
        try:
            combo.configure(values=values)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Commit / validation
    # ------------------------------------------------------------------

    def _commit_debounced(self, row: _IndicatorRow) -> None:
        """Schedule a commit ~250 ms after the last keystroke.

        Used for free-form numeric / text edits where committing per
        keystroke would (a) trigger 3 chart re-renders for typing
        ``200`` and (b) fight the user mid-input by reverting partial
        values like ``2`` (probably out of bounds for ``length``)."""
        if row.suppress:
            return
        self._cancel_pending_debounce(row)
        try:
            row.debounce_after_id = self.after(
                _TYPING_DEBOUNCE_MS, lambda r=row: self._commit_now(r),
            )
        except tk.TclError:
            pass

    def _cancel_pending_debounce(self, row: _IndicatorRow) -> None:
        """Cancel any scheduled debounced commit — called before a
        new debounce, on row destruction, and on dialog close."""
        if row.debounce_after_id is not None:
            try:
                self.after_cancel(row.debounce_after_id)
            except tk.TclError:
                pass
            row.debounce_after_id = None

    def _collect_param_values(self, row: _IndicatorRow) -> dict[str, Any]:
        """Read raw widget values into a dict, coercing to declared
        types. Raises ``ValueError`` on a coercion failure so the
        caller can revert."""
        # Build coercers from the current factory's schema.
        kind_id = self._kinds_by_display.get(
            (row.kind_var.get() or "").strip(),
        )
        if not kind_id:
            return dict(row.last_good_params)
        pair = factory_by_kind_id(kind_id)
        if pair is None:
            return dict(row.last_good_params)
        _name, cls = pair
        schema = getattr(cls, "params_schema", ()) or ()
        out: dict[str, Any] = {}
        for pdef in schema:
            var = row.param_vars.get(pdef.name)
            if var is None:
                # Param didn't survive a kind change — fall back to
                # the schema default so the factory call is valid.
                out[pdef.name] = pdef.default
                continue
            raw = var.get()
            if pdef.kind == "bool":
                out[pdef.name] = bool(raw)
            elif pdef.kind == "int":
                out[pdef.name] = int(float(raw))
            elif pdef.kind == "float":
                out[pdef.name] = float(raw)
            elif pdef.kind == "choice":
                # Find the original value matching the stringified one.
                s = str(raw)
                match = next(
                    (c for c in pdef.choices if str(c) == s),
                    pdef.default,
                )
                out[pdef.name] = match
            else:
                out[pdef.name] = str(raw)
        return out

    def _build_scopes(self, row: _IndicatorRow) -> tuple[frozenset[str], bool]:
        """Combine the two checkboxes + preserved drilldown into a
        full scopes frozenset, and decide ``visible``.

        Both checkboxes off ⇒ ``visible=False`` and scopes fall back
        to ``preserved_active_scopes`` (so re-checking either box
        restores the previous scope assignment as-is)."""
        primary_on = bool(row.primary_var.get()) if row.primary_var else False
        compare_on = bool(row.compare_var.get()) if row.compare_var else False
        active: list[str] = []
        if primary_on:
            active.append("main")
        if compare_on:
            active.append("compare")
        if active:
            row.preserved_active_scopes = frozenset(active)
            visible = True
            scopes = frozenset(active) | row.preserved_extra_scopes
        else:
            visible = False
            # Preserve last known active scopes so `visible=True`
            # can be restored later without losing scope state.
            scopes = row.preserved_active_scopes | row.preserved_extra_scopes
        return scopes, visible

    def _available_intervals(self) -> tuple[str, ...]:
        """Intervals exposed in each row's per-interval checkbox group.

        - Sandbox active → ``app._sandbox.display_intervals``, plus
          ``"1d"`` when daily context is registered (matches the
          toolbar restriction logic in ``app._restrict_toolbar_intervals_for_sandbox``).
        - Otherwise → the full :data:`_ALL_INTERVALS` toolbar set.
        """
        try:
            app = self._app
            sb = getattr(app, "_sandbox", None)
            is_active = bool(getattr(app, "_is_sandbox_active",
                                     lambda: False)())
            if is_active and sb is not None:
                ivs = list(getattr(sb, "display_intervals", None) or [])
                if not ivs:
                    base_iv = getattr(sb, "interval", None)
                    if base_iv:
                        ivs = [base_iv]
                # Daily context — included when the controller has
                # any daily series registered for the focus symbol.
                try:
                    daily_map = getattr(sb, "daily_full_by_symbol", {}) or {}
                    if daily_map and "1d" not in ivs:
                        ivs.append("1d")
                except Exception:  # noqa: BLE001
                    pass
                if ivs:
                    return tuple(ivs)
        except Exception:  # noqa: BLE001
            pass
        return _ALL_INTERVALS

    def _rebuild_interval_checkboxes(self, row: _IndicatorRow) -> None:
        """Tear down + rebuild ``row.interval_subframe`` to match the
        currently-available interval set.

        Var values are seeded from ``row.preserved_intervals``: empty
        tuple means "all checked" (legacy "applies to every interval"
        semantic); otherwise only intervals listed in the tuple are
        checked. Intervals in ``preserved_intervals`` that are NOT
        currently available are kept in ``preserved_intervals`` so
        switching back (e.g. exiting sandbox) restores them.
        """
        sf = row.interval_subframe
        if sf is None:
            return
        for child in list(sf.winfo_children()):
            try:
                child.destroy()
            except tk.TclError:
                pass
        row.interval_vars = {}
        intervals = self._available_intervals()
        if not intervals:
            return
        try:
            tk.Label(sf, text="Intervals:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        except tk.TclError:
            return
        all_checked_default = not row.preserved_intervals
        preserved_set = set(row.preserved_intervals)
        # Suppress commits while flipping the BooleanVars during build.
        was_suppressed = row.suppress
        row.suppress = True
        try:
            for itv in intervals:
                checked = (all_checked_default
                           or (itv in preserved_set))
                var = tk.BooleanVar(value=checked)
                chk = ttk.Checkbutton(
                    sf, text=itv, variable=var,
                    command=lambda r=row: self._commit_now(r),
                )
                chk.grid(row=0, column=len(row.interval_vars) + 1,
                         sticky="w", padx=(0, 4))
                row.interval_vars[itv] = var
        finally:
            row.suppress = was_suppressed
        row.interval_max_cols_applied = None
        self._maybe_regrid_row_intervals(row)
        # Apply theme so the freshly-built children pick up dark mode.
        try:
            self._apply_theme()
        except Exception:  # noqa: BLE001
            pass

    def _build_intervals(self, row: _IndicatorRow) -> tuple[str, ...]:
        """Collect the row's checked intervals.

        Returns the ``IndicatorConfig.intervals`` value to commit.
        Normalisation:

        - All checkboxes off → fall back to ``preserved_intervals``
          (so the user can't accidentally hide an indicator from
          every chart by un-checking everything; if that was their
          intent, they should toggle ``visible`` off via the scope
          checkboxes instead).
        - All checkboxes on AND the available set is the full toolbar
          set → return ``()`` (legacy "applies to all" semantic so
          presets saved here re-hydrate cleanly across UI versions).
        - Otherwise → merge the checked intervals with any
          ``preserved_intervals`` entries that aren't in the currently
          available set (so switching modes doesn't silently strip
          memberships the user can't see in this mode).
        """
        if not row.interval_vars:
            return tuple(row.preserved_intervals)
        available = list(row.interval_vars.keys())
        checked = [iv for iv, v in row.interval_vars.items()
                   if bool(v.get())]
        if not checked:
            # Don't drop to empty-but-not-all (= "applies to nothing").
            return tuple(row.preserved_intervals)
        # Merge in preserved entries that aren't visible right now.
        hidden = [iv for iv in row.preserved_intervals
                  if iv not in available]
        merged = list(checked) + [iv for iv in hidden if iv not in checked]
        # All-on AND full toolbar set → emit empty tuple = "all".
        if (len(checked) == len(available)
                and not hidden
                and tuple(available) == _ALL_INTERVALS):
            row.preserved_intervals = ()
            return ()
        merged_t = tuple(merged)
        row.preserved_intervals = merged_t
        return merged_t

    def refresh_available_intervals(self) -> None:
        """Public hook: rebuild every row's interval checkboxes.

        Called by the app on sandbox session start / end and on
        ``set_display_interval`` so the visible interval set tracks
        the available chart intervals."""
        for row in list(self._rows):
            try:
                self._rebuild_interval_checkboxes(row)
            except tk.TclError:
                pass
        # The set of intervals the user can pick from also gates
        # which kinds are "available" in the kind dropdown — so any
        # change to the available-intervals set should also refresh
        # the kind dropdown to reflect the current chart interval.
        try:
            self.refresh_kind_dropdown()
        except tk.TclError:
            pass
        self._apply_pane_budget_gate()

    # --- b48: kind dropdown availability + pane budget gate ---------

    def _current_chart_interval(self) -> str:
        """Best-effort lookup of the chart's current interval string."""
        var = getattr(self._app, "interval_var", None)
        if var is None:
            return ""
        try:
            return str(var.get() or "")
        except Exception:  # noqa: BLE001
            return ""

    def _kind_dropdown_values(self, interval: str) -> tuple[tuple[str, ...], dict[str, str]]:
        """Build the displayed values for the kind combobox.

        Returns ``(values, label_to_kind_id)``. Kinds whose factory
        :func:`factory_is_available_for` reports ``ok=False`` for the
        current interval get a ``" — needs intraday"`` (or other
        reason-derived) suffix. The label-to-kind-id map omits the
        suffixed labels so picking one fails to resolve and the
        commit path reverts the row to its last good state.
        """
        values: list[str] = []
        label_map: dict[str, str] = {}
        for display_name, kind_id in self._kinds_by_display.items():
            entry = factory_by_kind_id(kind_id)
            if entry is None:
                values.append(display_name)
                label_map[display_name] = kind_id
                continue
            _name, factory = entry
            avail = factory_is_available_for(factory, interval) if interval else None
            if avail is None or avail.ok:
                values.append(display_name)
                label_map[display_name] = kind_id
            else:
                # Show greyed-equivalent: a non-resolvable label so
                # the commit path rejects accidental selection.
                reason = avail.reason or "unavailable"
                values.append(f"{display_name}  —  ({reason})")
                # NOTE: not added to label_map on purpose.
        return tuple(values), label_map

    def refresh_kind_dropdown(self, interval: str | None = None) -> None:
        """Public hook: refresh every row's kind combobox values
        for the current (or supplied) chart interval.

        Wired from app.py on ``interval_var`` trace so a user
        switching from 5m → 1d sees intraday-only indicators
        annotated as unavailable in the dropdown. Existing rows
        whose kind is now unavailable remain editable (their kind
        label still appears as the row's selected item) but render
        is auto-filtered by :meth:`IndicatorConfig.applies_to`.
        """
        if interval is None:
            interval = self._current_chart_interval()
        values, label_map = self._kind_dropdown_values(interval)
        # Replace the canonical label_map only for non-unavailable
        # entries; keep the original full registry for legacy lookups.
        # We store the per-interval restricted view on a sidecar attr
        # used by commit-path lookups.
        self._kinds_by_display_for_interval = label_map
        for row in list(self._rows):
            try:
                if row.kind_combo is not None:
                    row.kind_combo.configure(values=values)
            except tk.TclError:
                pass

    def _apply_pane_budget_gate(self) -> None:
        """Disable the Add Indicator button + show a status message
        when adding another non-overlay would violate the figure's
        price-floor (40%) layout invariant.

        Uses :func:`tradinglab.indicators.render.compute_layout`'s
        existing ``can_add_more`` flag — no new layout math here.
        """
        btn = getattr(self, "_add_button", None)
        lbl = getattr(self, "_budget_label", None)
        if btn is None:
            return
        try:
            from ..indicators import render as _ind_render
        except Exception:  # noqa: BLE001
            return
        try:
            interval = self._current_chart_interval()
            n_groups = len(_ind_render.applicable_pane_groups(
                self._manager, "main", interval,
            ))
            fig = getattr(self._app, "_figure", None)
            fig_h = float(fig.get_figheight()) if fig is not None else 6.0
            # Probe: would adding ONE more pane group still satisfy the
            # price-floor invariant?
            _, can_add_more = _ind_render.compute_layout(
                1 + n_groups + 1, fig_h,
            )
        except Exception:  # noqa: BLE001
            return
        try:
            if can_add_more:
                btn.state(["!disabled"])
                if lbl is not None:
                    lbl.configure(text="")
            else:
                btn.state(["disabled"])
                if lbl is not None:
                    lbl.configure(text="Pane budget reached — remove an indicator first.")
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # b42 — per-output color picker (native OS chooser)
    # ------------------------------------------------------------------

    @staticmethod
    def _default_style_for_kind(kind_id: str) -> dict[str, LineStyle]:
        """Look up the factory's ``default_style`` dict for ``kind_id``.

        Returns ``{}`` for unknown kinds. The dict is keyed by output
        name (e.g. ``"sma"``, or ``"middle"``/``"upper"``/``"lower"``
        for Bollinger Bands)."""
        pair = factory_by_kind_id(kind_id)
        if pair is None:
            return {}
        _name, cls = pair
        return dict(getattr(cls, "default_style", {}) or {})

    def _resolved_color_for(self, row: _IndicatorRow, key: str,
                            default_style: dict[str, LineStyle]) -> str:
        """Return the color currently assigned to output ``key``.

        Override (if any) wins; otherwise the factory default; finally
        the global ``LineStyle()`` fallback (FALLBACK_GRAY)."""
        if key in row.style_overrides:
            return row.style_overrides[key]
        ls = default_style.get(key)
        if ls is not None:
            return getattr(ls, "color", FALLBACK_GRAY) or FALLBACK_GRAY
        return FALLBACK_GRAY

    def _rebuild_color_buttons(self, row: _IndicatorRow,
                               kind_id: str) -> None:
        """Tear down + rebuild the row's color-swatch buttons.

        One button per output key declared in the factory's
        ``default_style``. Each button shows the resolved color as
        its background; clicking it opens the native OS color
        chooser and commits the chosen color via ``_commit_now``.
        """
        sf = row.color_subframe
        if sf is None:
            return
        for child in list(sf.winfo_children()):
            try:
                child.destroy()
            except tk.TclError:
                pass
        row.color_buttons = {}
        if row.is_unknown:
            return
        default_style = self._default_style_for_kind(kind_id)
        if not default_style:
            return
        try:
            tk.Label(sf, text="Colors:").pack(side="left", padx=(0, 4))
        except tk.TclError:
            return
        for key in default_style.keys():
            color = self._resolved_color_for(row, key, default_style)
            cell = tk.Frame(sf)
            cell.pack(side="left", padx=(0, 8))
            swatch = tk.Frame(
                cell, width=22, height=14,
                bg=color, bd=1, relief="solid",
                cursor="hand2",
            )
            # Theme walker skips this frame — its bg IS the user
            # data we want to display.
            swatch._no_theme = True  # type: ignore[attr-defined]
            swatch.pack_propagate(False)
            swatch.pack(side="left", padx=(0, 3))
            swatch.bind(
                "<Button-1>",
                lambda _e, r=row, k=str(key): self._on_pick_color_for_output(r, k),
            )
            tk.Label(cell, text=str(key)).pack(side="left")
            row.color_buttons[str(key)] = swatch
        # Theme freshly-built frames so light/dark mode matches.
        try:
            self._apply_theme()
        except Exception:  # noqa: BLE001
            pass

    def _on_pick_color_for_output(self, row: _IndicatorRow,
                                  key: str) -> None:
        """Open the themed colour chooser for output ``key`` and commit.

        Called from the swatch button's ``<Button-1>`` binding. The
        picker (`gui.color_palette.pick_color`) opens the themed
        `ThemedColorChooser` (audit ``themed-color-chooser`` — a
        Win32-ChooseColor look-alike that follows the app theme).
        The chooser is modal — we read its result, store it on the
        row's ``style_overrides``, restyle the swatch widget, and
        commit through the manager so the chart redraws."""
        if row.is_unknown:
            return
        kind_display = (row.kind_var.get() or "").strip()
        kind_id = self._kinds_by_display.get(kind_display)
        if not kind_id:
            return
        default_style = self._default_style_for_kind(kind_id)
        current = self._resolved_color_for(row, key, default_style)
        chosen = pick_color(self, initial=current,
                            title=f"Pick color — {key}")
        if not chosen:
            return
        row.style_overrides[key] = chosen
        # Re-tint the swatch in place so the user sees the change
        # before the chart redraws.
        sw = row.color_buttons.get(key)
        if sw is not None:
            try:
                sw.configure(bg=chosen)
            except tk.TclError:
                pass
        self._commit_now(row)

    def _build_style(self, row: _IndicatorRow,
                     kind_id: str) -> dict[str, LineStyle]:
        """Materialise the row's per-output style overrides.

        Only emits entries for outputs whose color differs from the
        factory's ``default_style`` color, so a config saved without
        any user-picked colors round-trips with an empty ``style``
        dict (matches the "no overrides" intent and lets future
        default_style tweaks propagate through unchanged).
        """
        if row.is_unknown:
            return {}
        default_style = self._default_style_for_kind(kind_id)
        out: dict[str, LineStyle] = {}
        for key, color in row.style_overrides.items():
            ls_default = default_style.get(key)
            default_color = (getattr(ls_default, "color", FALLBACK_GRAY)
                             if ls_default is not None else FALLBACK_GRAY)
            if (color or "").upper() == (default_color or "").upper():
                # User picked the default — no override needed.
                continue
            width = (getattr(ls_default, "width", 1.2)
                     if ls_default is not None else 1.2)
            visible = (getattr(ls_default, "visible", True)
                       if ls_default is not None else True)
            out[str(key)] = LineStyle(color=color, width=float(width),
                                      visible=bool(visible))
        return out

    def _commit_now(self, row: _IndicatorRow) -> None:
        """Validate the row and commit to the manager.

        Validation: instantiate the factory with the candidate
        params. On any exception the row reverts to ``last_good_params``
        (each widget's ``var.set(...)`` fires under ``suppress`` so
        we don't re-enter commit). On success, recompute
        ``display_name`` from the freshly-constructed indicator's
        ``.name`` and update / add."""
        if row.suppress or self._reconciling:
            return
        if row.is_unknown:
            # Don't ever commit an unknown row — we'd silently swap
            # its ``kind_id`` for the kind currently displayed in the
            # combobox.
            return
        kind_display = (row.kind_var.get() or "").strip()
        kind_id = self._kinds_by_display.get(kind_display)
        if not kind_id:
            return
        pair = factory_by_kind_id(kind_id)
        if pair is None:
            return
        _display_name, cls = pair
        try:
            params = self._collect_param_values(row)
            indicator = cls(**params)
        except Exception:  # noqa: BLE001
            # Validation failed — revert widget values to last good.
            self._revert_row_to_last_good(row)
            return
        scopes, visible = self._build_scopes(row)
        intervals_t = self._build_intervals(row)
        style_overrides = self._build_style(row, kind_id)
        new_display = getattr(indicator, "name", kind_id)
        # Mark our own commits so the manager's notify->reconcile path
        # doesn't tear down the row mid-edit.
        self._reconciling = True
        try:
            if row.config_id is None:
                cfg = IndicatorConfig(
                    kind_id=kind_id,
                    kind_version=int(getattr(cls, "kind_version", 1)),
                    display_name=str(new_display),
                    params=dict(params),
                    scopes=scopes,
                    intervals=intervals_t,
                    style=style_overrides,
                    visible=visible,
                    pane_group=str(getattr(cls, "pane_group", "") or ""),
                )
                added = self._manager.add(cfg)
                row.config_id = added.id
            else:
                self._manager.update(
                    row.config_id,
                    kind_id=kind_id,
                    kind_version=int(getattr(cls, "kind_version", 1)),
                    display_name=str(new_display),
                    params=dict(params),
                    scopes=scopes,
                    intervals=intervals_t,
                    style=style_overrides,
                    visible=visible,
                )
        finally:
            self._reconciling = False
        # Snapshot the params so a future failed edit can revert.
        row.last_good_params = dict(params)
        # Update the per-session MA-type memory whenever the user
        # commits a Moving Average row. Captures both the direct
        # type-dropdown change AND the silent change that happens when
        # the user switches a non-MA kind into MA (the seed becomes
        # the prior memory; if they then commit, it persists).
        if kind_id == "ma":
            picked = str(params.get("ma_type") or "").upper()
            if picked:
                type(self)._last_used_ma_type = picked
        # Mark dirty so the Save and Close button enables. Called
        # here rather than in _on_manager_event because _reconciling
        # suppresses the event callback during our own commits.
        self._mark_dirty()
        # Deferred-render: this commit mutated the manager but (in
        # deferred mode) did not repaint the chart — light up Apply.
        self._mark_pending()

    def _revert_row_to_last_good(self, row: _IndicatorRow) -> None:
        """After a validation failure, set every param widget back to
        ``row.last_good_params`` without re-firing commit."""
        row.suppress = True
        try:
            for name, var in row.param_vars.items():
                if name in row.last_good_params:
                    val = row.last_good_params[name]
                    if isinstance(var, tk.BooleanVar):
                        try:
                            var.set(bool(val))
                        except tk.TclError:
                            pass
                    else:
                        try:
                            var.set(str(val))
                        except tk.TclError:
                            pass
        finally:
            row.suppress = False

    # ------------------------------------------------------------------
    # Display-name helpers
    # ------------------------------------------------------------------

    def _open_indicator_help(self, row) -> None:
        """Open the authored markdown help doc for the row's current
        indicator kind."""
        kind_display = (row.kind_var.get() or "").strip()
        kind_id = self._kinds_by_display.get(kind_display)
        if not kind_id:
            return
        try:
            from .._resources import resource_path
            doc_path = resource_path("docs", "indicators", f"{kind_id}.md")
        except Exception:
            doc_path = None
        if doc_path is None or not doc_path.is_file():
            # Fallback: show the acronym tooltip text
            from tkinter import messagebox

            from .indicator_acronyms import explain_kind_id
            text = explain_kind_id(kind_id)
            messagebox.showinfo(f"About {kind_display}", text, parent=self)
            return
        try:
            from .doc_viewer import open_doc_viewer
            open_doc_viewer(self, doc_path)
        except Exception:
            pass

    def _refresh_kind_tooltip(self, row: _IndicatorRow, kind_id: str) -> None:
        """Sync the row's kind-combobox tooltip with the current kind.

        Looks up the (full name, blurb) entry in ``indicator_acronyms``
        and pushes it onto ``row.kind_tooltip`` so a hover surfaces the
        explanation. No-op when the tooltip hasn't been built yet
        (e.g. unknown kind placeholder rows that bypass the standard
        build path).
        """
        tip = row.kind_tooltip
        if tip is None:
            return
        try:
            tip.set_text(explain_kind_id(kind_id))
        except tk.TclError:
            pass

    def _display_for_kind_id(self, kind_id: str) -> str:
        """Reverse-lookup the dropdown label for ``kind_id``. Falls
        back to the raw id if the kind is unregistered."""
        for display_name, kid in self._kinds_by_display.items():
            if kid == kind_id:
                return display_name
        return kind_id

    # ------------------------------------------------------------------
    # Bottom-bar buttons
    # ------------------------------------------------------------------

    def _on_click_add(self) -> None:
        """User clicked Add Indicator — append a new row pre-seeded
        with the first registered kind / its defaults / scope=Primary,
        then commit immediately so the chart reflects it."""
        row = self._build_row(None)
        # Default: Primary on, Compare off so the new indicator
        # appears immediately on the main chart.
        row.suppress = True
        try:
            if row.primary_var is not None:
                row.primary_var.set(True)
            # Default per-interval visibility: only the currently
            # active chart interval is checked (b41). User asked for
            # "newly-added indicator only shows on the interval it
            # was added on" rather than the legacy all-intervals
            # default.
            try:
                cur_itv = (self._app.interval_var.get() or "").strip()
            except Exception:  # noqa: BLE001
                cur_itv = ""
            if cur_itv and cur_itv in row.interval_vars:
                for itv, var in row.interval_vars.items():
                    try:
                        var.set(itv == cur_itv)
                    except tk.TclError:
                        pass
                row.preserved_intervals = (cur_itv,)
        finally:
            row.suppress = False
        # Auto-select the new row so the next Remove Selected click
        # targets it without an extra click.
        self._selected_key.set(row.row_key)
        self._commit_now(row)
        # New row introduced fresh Combobox/Spinbox widgets — re-guard
        # them so wheel-over doesn't silently mutate (CLAUDE.md §7.11).
        self._protect_combobox_wheel()

    def _protect_combobox_wheel(self) -> None:
        """Re-apply the Combobox/Spinbox wheel-guard across the dialog.

        Idempotent. Called after the initial build and after any
        dynamic widget rebuild (``_reconcile_from_manager``,
        ``_on_kind_changed``, ``_on_click_add``) so newly-created
        comboboxes / spinboxes are guarded too. Without this, scrolling
        over a param widget in the dialog would silently mutate its
        value because the ttk class binding wins over our bind_all
        canvas handler. See ``protect_combobox_wheel`` docstring and
        CLAUDE.md §7.11 for the full story.
        """
        target = getattr(self, "_rows_canvas", None)
        try:
            protect_combobox_wheel(self, scroll_target=target)
        except tk.TclError:
            pass

    def _on_click_remove(self) -> None:
        """User clicked Remove Selected — drop the row whose
        radiobutton is currently active, removing it from the manager
        (cascading the chart redraw via the manager's normal event
        flow)."""
        target_key = int(self._selected_key.get() or 0)
        if target_key == 0:
            return
        row = next((r for r in self._rows if r.row_key == target_key), None)
        if row is None:
            return
        self._cancel_pending_debounce(row)
        if row.config_id is not None:
            self._reconciling = True
            try:
                self._manager.remove(row.config_id)
            finally:
                self._reconciling = False
        # Tear down the row's UI.
        if row.container is not None:
            try:
                row.container.destroy()
            except tk.TclError:
                pass
        try:
            self._rows.remove(row)
        except ValueError:
            pass
        self._selected_key.set(0)
        self._mark_dirty()
        self._mark_pending()
