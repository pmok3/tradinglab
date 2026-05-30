"""Base classes for the project's modal Toplevels.

Every modal dialog in the GUI used to repeat the same boilerplate:

* ``super().__init__(parent)`` then ``title()``, ``transient(parent)``,
  ``grab_set()``.
* Manually pick a ``geometry()`` string and re-center over the parent.
* Bind ``<Escape>`` to cancel and ``<Return>`` to the primary action
  (sometimes inconsistent — some dialogs forgot one or both).
* Lay out a footer with [Cancel] and either [OK] / [Save] / [Save & Close]
  — order varied across dialogs (the UI/UX audit flagged this).
* For editor-style dialogs (Entries / Exits): a 4-button footer
  ``[Validate] [Cancel] [Apply] [Save & Close]`` whose pack order is
  fiddly because ``side="right"`` reverses visual order.

This module hosts two base classes that collapse the boilerplate:

:class:`BaseModalDialog`
    Toplevel subclass that wires title + transient + grab + ESC/Return
    keybindings + geometry-store persistence. Subclasses override
    :meth:`_on_cancel` and (optionally) :meth:`_on_primary` to hook
    the standard close gestures.

:class:`BaseEditorDialog`
    Adds :meth:`_build_editor_footer` which produces the canonical
    ``[Validate] [Cancel] [Apply] [Save & Close]`` footer in the
    correct visual order, plus a status label slot that callbacks can
    fill with validation messages.

Subclasses are expected to call :meth:`_finalize_modal` at the END of
``__init__`` (after widgets exist) so the geometry restore + key
bindings see the final geometry. The contract is opt-in — existing
dialogs that don't yet use the base class continue to work; new
dialogs (and refactors of existing ones) just inherit and skip the
boilerplate.

Why a *base class* and not a mixin or wrapper function
------------------------------------------------------
The boilerplate is order-sensitive: ``grab_set`` must run after
``transient`` and before geometry restore, and the key bindings must
attach to ``self`` (the Toplevel). A subclass relationship matches the
typical "Toplevel-based dialog" pattern in the codebase and keeps
type-checkers happy without extra ``cast`` calls.

Geometry persistence is delegated to :mod:`geometry_store` — every
dialog passes a stable ``geometry_key`` (e.g. ``"dlg.entries"``) and
the store auto-restores last-known size/position + auto-persists on
``<Configure>``. Off-screen geometries are clamped (multi-monitor
safety) in :func:`geometry_store._clamp_to_screen`.

Theming
-------
Subclasses may pass ``apply_dark_theme=True`` to opt into the
project's dark-mode color propagation. The base class queries the
parent for an ``apply_dark_theme_to(toplevel)`` method and calls it
when the parent supports it — this lets dark-mode-aware apps tint the
dialog without each dialog needing to re-implement the lookup. Apps
that don't expose that hook are silently no-op'd.
"""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any

from ._modal_keys import bind_modal_keys
from .colors import ERROR_RED
from .geometry_store import store as _gstore


class BaseModalDialog(tk.Toplevel):
    """Toplevel base class with the modal boilerplate baked in.

    Subclasses should:

    1. Pass ``title``, ``geometry_key`` (stable string, conventionally
       ``"dlg.<name>"``) and optional ``default_geometry`` to
       ``super().__init__``.
    2. Build their widgets (typically inside a ``_build_layout``
       method).
    3. Call :meth:`_finalize_modal` at the end of ``__init__`` to wire
       ESC/Return + geometry persistence + grab/transient.

    Override :meth:`_on_cancel` (default destroys) and
    :meth:`_on_primary` (default destroys) to hook the close gestures.

    The two-phase initialisation (super-init then _finalize_modal)
    exists because the geometry restore needs ``winfo_screenwidth``
    which is unreliable on a brand-new Toplevel; calling
    ``update_idletasks`` between widget creation and geometry restore
    yields stable values.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str = "",
        geometry_key: str | None = None,
        default_geometry: str = "640x480",
        resizable: tuple[bool, bool] = (True, True),
        apply_dark_theme: bool = True,
    ) -> None:
        super().__init__(parent)
        if title:
            self.title(title)
        try:
            self.transient(parent)
        except tk.TclError:
            pass
        self.resizable(*resizable)

        self._parent_ref = parent
        self._geometry_key = geometry_key
        self._default_geometry = default_geometry
        self._apply_dark = apply_dark_theme
        self._finalized = False

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------
    def _finalize_modal(
        self,
        *,
        primary: Callable[[], None] | None = None,
        cancel: Callable[[], None] | None = None,
        grab: bool = True,
    ) -> None:
        """Wire keybindings, geometry persistence, grab.

        Call at the end of ``__init__`` after all widgets exist.

        ``primary`` / ``cancel`` default to :meth:`_on_primary` and
        :meth:`_on_cancel`. Pass ``None`` explicitly to suppress a
        binding (e.g. a confirm dialog with no Save action).
        """
        if self._finalized:
            return
        # Default the close-X to cancel semantic so users can dismiss
        # without committing.
        cancel_cb = cancel if cancel is not None else self._on_cancel
        primary_cb = primary if primary is not None else self._on_primary

        try:
            self.protocol("WM_DELETE_WINDOW", cancel_cb)
        except tk.TclError:
            pass

        bind_modal_keys(self, cancel=cancel_cb, primary=primary_cb)

        # Geometry restore must run AFTER widgets are laid out — let
        # Tk settle requested sizes first or screen-clamp uses zero.
        try:
            self.update_idletasks()
        except tk.TclError:
            pass
        if self._geometry_key:
            try:
                gs = _gstore()
                gs.restore_window(self, self._geometry_key, self._default_geometry)
                gs.bind_window(self, self._geometry_key)
            except Exception:  # noqa: BLE001
                # Geometry persistence is convenience; never crash the
                # dialog if the store can't load.
                pass

        if grab:
            try:
                self.grab_set()
            except tk.TclError:
                pass

        # Dark-theme propagation — best-effort. Apps wire this via
        # ``apply_dark_theme_to(top)`` on the parent / app object.
        if self._apply_dark:
            for candidate in (self._parent_ref, getattr(self._parent_ref, "master", None)):
                if candidate is None:
                    continue
                hook = getattr(candidate, "apply_dark_theme_to", None)
                if callable(hook):
                    try:
                        hook(self)
                        break
                    except Exception:  # noqa: BLE001
                        pass

        self._finalized = True

    # ------------------------------------------------------------------
    # Default action handlers
    # ------------------------------------------------------------------
    def _on_cancel(self) -> None:
        """Default ESC / [Cancel] handler — destroy without committing."""
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _on_primary(self) -> None:
        """Default Enter / primary-button handler — destroy.

        Editor dialogs override this to commit + close.
        """
        try:
            self.destroy()
        except tk.TclError:
            pass


class BaseEditorDialog(BaseModalDialog):
    """Adds the standard ``[Validate] [Cancel] [Apply] [Save & Close]`` footer.

    Subclasses wire callbacks for the four buttons via
    :meth:`_build_editor_footer` and override
    :meth:`_on_primary` to call ``_on_save_close`` so the Enter-key
    behavior matches the rightmost button.

    The footer also includes a left-aligned status label bound to
    :attr:`_status_var` — set it to a non-empty string to surface
    validation errors at the bottom of the dialog.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str = "",
        geometry_key: str | None = None,
        default_geometry: str = "900x600",
        resizable: tuple[bool, bool] = (True, True),
        apply_dark_theme: bool = True,
    ) -> None:
        super().__init__(
            parent,
            title=title,
            geometry_key=geometry_key,
            default_geometry=default_geometry,
            resizable=resizable,
            apply_dark_theme=apply_dark_theme,
        )
        self._status_var = tk.StringVar(value="")
        # Footer widgets exposed for test introspection / per-dialog
        # state tweaks (e.g. disabling [Apply] while a long task runs).
        self.btn_validate: ttk.Button | None = None
        self.btn_cancel: ttk.Button | None = None
        self.btn_apply: ttk.Button | None = None
        self.btn_save_close: ttk.Button | None = None

    # ------------------------------------------------------------------
    # Footer builder
    # ------------------------------------------------------------------
    def _build_editor_footer(
        self,
        parent: tk.Misc,
        *,
        on_validate: Callable[[], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
        on_apply: Callable[[], None] | None = None,
        on_save_close: Callable[[], None] | None = None,
        status_foreground: str = ERROR_RED,
    ) -> ttk.Frame:
        """Build the canonical 4-button editor footer.

        Visual order (left → right): ``[Validate] [Apply]
        [Save & Close] [Cancel]`` — Windows dialog convention with
        affirmative actions on the left and the dismiss action
        (Cancel) rightmost. Status label fills the remaining space
        on the left. Pass ``None`` for any callback to suppress its
        button.

        Returns the footer frame so the caller can ``pack`` /
        ``grid`` it inside their layout. Caller is expected to
        ``pack(fill="x", pady=(6, 0))`` it under the main content.
        """
        footer = ttk.Frame(parent)

        status_lbl = ttk.Label(
            footer, textvariable=self._status_var, foreground=status_foreground,
        )
        status_lbl.pack(side="left", fill="x", expand=True)
        self._status_lbl = status_lbl

        # Windows dialog convention (audit ``button-order-windows``):
        # right-aligned group reads left-to-right
        # ``[Validate] [Apply] [Save & Close] [Cancel]`` with the
        # affirmative actions to the left and the dismiss action
        # (Cancel) rightmost. ``side="right"`` packs reverse visual
        # order, so pack Cancel FIRST so it lands rightmost; then
        # Save & Close, Apply, Validate from there leftward.
        if on_cancel is not None:
            self.btn_cancel = ttk.Button(
                footer, text="Cancel", command=on_cancel,
            )
            self.btn_cancel.pack(side="right", padx=(2, 0))
        if on_save_close is not None:
            self.btn_save_close = ttk.Button(
                footer, text="Save & Close", command=on_save_close,
            )
            self.btn_save_close.pack(side="right", padx=(2, 0))
        if on_apply is not None:
            self.btn_apply = ttk.Button(
                footer, text="Apply", command=on_apply,
            )
            self.btn_apply.pack(side="right", padx=(2, 0))
        if on_validate is not None:
            self.btn_validate = ttk.Button(
                footer, text="Validate", command=on_validate,
            )
            self.btn_validate.pack(side="right", padx=(2, 0))

        return footer

    def set_status(self, msg: str, *, level: str = "error") -> None:
        """Surface ``msg`` in the status label.

        ``level`` is one of ``"error"`` (default red), ``"info"``
        (muted), ``"ok"`` (green). Affects label foreground color.
        Pass ``msg=""`` to clear.
        """
        from .colors import MUTED_GREY

        try:
            from .colors import SUCCESS_GREEN as _GREEN
        except ImportError:  # noqa: BLE001 - older builds
            _GREEN = "#2a7f3a"
        if not msg:
            self._status_var.set("")
            return
        self._status_var.set(str(msg))
        try:
            fg = {
                "error": ERROR_RED,
                "info": MUTED_GREY,
                "ok": _GREEN,
            }.get(level, ERROR_RED)
            self._status_lbl.configure(foreground=fg)
        except (AttributeError, tk.TclError):
            pass


# ---------------------------------------------------------------------------
# Combobox / Spinbox mouse-wheel guard
# ---------------------------------------------------------------------------
#
# Windows ttk Combobox / Spinbox widgets consume ``<MouseWheel>`` natively
# and *silently change their selected value* on every wheel tick. When a
# dialog hosts a scrollable canvas that binds ``<MouseWheel>`` globally
# (via ``bind_all``) so the form scrolls under the cursor, the user can
# scroll the form while the pointer happens to be over a combobox and
# silently mutate persisted state — e.g. flipping an entry-strategy
# operator from ``crosses_above`` to ``between``, which then ships to disk
# on the next Save. The "EMA 3/8 cross became ``between(0, 0)``" bug was
# exactly this: accidental wheel-over-combobox corruption.
#
# :func:`protect_combobox_wheel` walks all descendants of a root widget
# and installs a widget-local ``<MouseWheel>`` (and X11 ``<Button-4>`` /
# ``<Button-5>``) handler on every ttk Combobox / Spinbox that:
#
# 1. Forwards the wheel to an optional ``scroll_target`` canvas so the
#    enclosing scrollable form keeps responding to the wheel.
# 2. Returns ``"break"`` to stop the default class binding from
#    mutating the widget's value.
#
# Idempotent — re-applying after a partial widget rebuild is safe;
# bindings are replaced rather than stacked.


def protect_combobox_wheel(
    root: tk.Misc,
    *,
    scroll_target: tk.Widget | None = None,
) -> int:
    """Block wheel-driven value changes on Combobox/Spinbox descendants.

    Walks the widget tree under ``root`` and binds ``<MouseWheel>`` (and
    the X11 ``<Button-4>`` / ``<Button-5>`` pair) on every ``ttk.Combobox``
    / ``ttk.Spinbox`` to a no-op that returns ``"break"`` — preventing the
    class-level binding from changing the widget's selected value when
    the user scrolls over it.

    If ``scroll_target`` is provided, the wheel event is forwarded to
    that canvas's ``yview_scroll`` first so the enclosing scrollable
    form still responds to scrolling over a combobox. Without
    ``scroll_target``, wheel events on guarded widgets are simply
    swallowed (the user can still scroll the form by moving the
    cursor off the combobox).

    Returns the count of widgets guarded — handy for tests.
    """
    count = 0

    def _wheel_handler(e: Any) -> str:
        if scroll_target is not None:
            try:
                # Windows / macOS report a ``delta`` divisible by 120.
                # X11 button events have no ``delta`` attribute; the
                # ``<Button-4>`` / ``<Button-5>`` branch below covers them.
                delta = int(getattr(e, "delta", 0))
                if delta:
                    scroll_target.yview_scroll(int(-1 * (delta / 120)), "units")
            except tk.TclError:
                pass
        return "break"

    def _button4_handler(_e: Any) -> str:
        if scroll_target is not None:
            try:
                scroll_target.yview_scroll(-1, "units")
            except tk.TclError:
                pass
        return "break"

    def _button5_handler(_e: Any) -> str:
        if scroll_target is not None:
            try:
                scroll_target.yview_scroll(1, "units")
            except tk.TclError:
                pass
        return "break"

    def _walk(w: tk.Misc) -> None:
        nonlocal count
        try:
            children = w.winfo_children()
        except tk.TclError:
            return
        for child in children:
            if isinstance(child, (ttk.Combobox, ttk.Spinbox)):
                try:
                    child.bind("<MouseWheel>", _wheel_handler)
                    child.bind("<Button-4>", _button4_handler)
                    child.bind("<Button-5>", _button5_handler)
                    count += 1
                except tk.TclError:
                    pass
            _walk(child)

    _walk(root)
    return count


# ---------------------------------------------------------------------------
# Scrollable form skeleton
# ---------------------------------------------------------------------------
#
# Five dialogs in the codebase previously hand-rolled the same
# ``Canvas + Scrollbar + create_window + <Configure> bindings +
# global bind_all("<MouseWheel>", ...) + cleanup-on-destroy`` boilerplate
# (Settings dialog, EntriesDialog form + trigger_params, ExitsDialog
# legs holder, IndicatorDialog rows). The audit pass that retired
# audit item #5 collapsed the boilerplate into this helper.
#
# Compatibility contract for callers:
#
# 1. The returned canvas is intended as the ``scroll_target`` argument
#    of :func:`protect_combobox_wheel` (CLAUDE.md §7.11 — without that
#    guard, scrolling over a ttk Combobox / Spinbox silently mutates
#    its value). The helper does NOT call ``protect_combobox_wheel``
#    itself — the consumer dialog must do that AFTER it finishes
#    building its widgets, and re-do it after every partial widget
#    rebuild.
#
# 2. When ``bind_mousewheel=True`` (default), the wheel is installed
#    via ``bind_all`` on the canvas's ``<Enter>`` and removed on
#    ``<Leave>``. The inner frame's ``<Destroy>`` runs the uninstall
#    again as a backstop so the global binding never leaks past the
#    dialog's lifetime (the audit explicitly flagged ungated
#    ``bind_all`` as fragile). Pass ``bind_mousewheel=False`` for
#    nested scrollables (e.g. EntriesDialog ``trigger_params`` lives
#    inside the outer form's scrollable — the outer wheel already
#    drives both) or when the consumer keeps a specialised wheel
#    install path that tests drive directly (IndicatorDialog).


def make_scrollable_form(
    parent: tk.Misc,
    *,
    horizontal: bool = False,
    bind_mousewheel: bool = True,
) -> tuple[ttk.Frame, tk.Canvas]:
    """Build a scrollable Canvas + Scrollbar(s) + inner ``ttk.Frame`` triple.

    ``parent`` hosts the canvas and scrollbar(s). The returned inner
    frame is the widget the caller packs / grids form content into.
    The canvas is also returned so the caller can pass it as
    ``protect_combobox_wheel(scroll_target=canvas)`` (see
    CLAUDE.md §7.11 — required for any dialog whose form contains a
    ttk ``Combobox`` or ``Spinbox``).

    Layout produced inside ``parent``:

    * Vertical ``ttk.Scrollbar`` packed ``side="right", fill="y"``.
    * If ``horizontal=True``, horizontal ``ttk.Scrollbar`` packed
      ``side="bottom", fill="x"`` BEFORE the vbar / canvas so it
      spans the full body width under the canvas+vbar group.
    * ``tk.Canvas`` packed ``side="left", fill="both", expand=True``.
    * Inner ``ttk.Frame`` placed as a window on the canvas at
      ``(0, 0)`` anchored north-west.

    Auto-bindings (always installed):

    * Inner frame ``<Configure>`` updates ``canvas.scrollregion`` to
      ``canvas.bbox("all")``.
    * Canvas ``<Configure>`` resizes the inner window to
      ``event.width`` (vertical-only mode) or to
      ``max(event.width, inner.winfo_reqwidth())`` (horizontal
      mode — lets the inner frame grow beyond the canvas when its
      content is wider, so the hbar has something to scroll).

    Auto-bindings (only when ``bind_mousewheel=True``):

    * Canvas ``<Enter>`` installs ``bind_all`` on ``<MouseWheel>``
      (Windows / macOS) and ``<Button-4>`` / ``<Button-5>`` (X11)
      so the form scrolls vertically while the cursor is inside
      the canvas region.
    * Canvas ``<Leave>`` removes those ``bind_all`` hooks so wheel
      events outside the dialog (e.g. over the main chart) do not
      also drive the canvas.
    * Inner frame ``<Destroy>`` runs the same uninstall as a
      backstop in case the dialog closes while the cursor is still
      over the canvas.
    * The wheel handler returns ``"break"`` so a parent scrollable
      container does not also receive the same event and
      double-scroll.

    Returns ``(inner_frame, canvas)``.
    """
    canvas = tk.Canvas(parent, borderwidth=0, highlightthickness=0)
    vbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    if horizontal:
        hbar = ttk.Scrollbar(parent, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        # hbar packed BEFORE vbar / canvas so it spans the full body
        # width under the canvas+vbar group (pack-order semantics:
        # earlier ``side="bottom"`` siblings claim height first).
        hbar.pack(side="bottom", fill="x")
    else:
        canvas.configure(yscrollcommand=vbar.set)
    vbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    inner = ttk.Frame(canvas)
    window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _on_inner_configure(_e: Any = None) -> None:
        try:
            canvas.configure(scrollregion=canvas.bbox("all"))
            if horizontal:
                canvas_w = canvas.winfo_width()
                req_w = inner.winfo_reqwidth()
                canvas.itemconfigure(window_id, width=max(canvas_w, req_w))
        except tk.TclError:
            pass

    inner.bind("<Configure>", _on_inner_configure)

    def _on_canvas_configure(e: tk.Event) -> None:
        try:
            if horizontal:
                req_w = inner.winfo_reqwidth()
                canvas.itemconfigure(window_id, width=max(e.width, req_w))
            else:
                canvas.itemconfigure(window_id, width=e.width)
        except tk.TclError:
            pass

    canvas.bind("<Configure>", _on_canvas_configure)

    if bind_mousewheel:
        def _v_can_scroll() -> bool:
            """True only when the form content overflows the viewport.

            Guards against Tk's canvas quirk where ``yview_scroll`` still
            shifts the view when the scrollregion is *smaller* than the
            canvas (e.g. a single-parameter indicator form). When the
            content fully fits, ``yview`` reports ``(0.0, 1.0)`` and we
            refuse to scroll so the lone widget can't be dragged around.
            """
            try:
                first, last = canvas.yview()
            except (tk.TclError, ValueError):
                return False
            return not (float(first) <= 0.0 and float(last) >= 1.0)

        def _on_wheel(e: tk.Event) -> str:
            try:
                if not _v_can_scroll():
                    return "break"
                delta = int(getattr(e, "delta", 0))
                if delta:
                    canvas.yview_scroll(int(-1 * (delta / 120)), "units")
            except tk.TclError:
                pass
            return "break"

        def _on_button4(_e: tk.Event) -> str:
            try:
                if _v_can_scroll():
                    canvas.yview_scroll(-1, "units")
            except tk.TclError:
                pass
            return "break"

        def _on_button5(_e: tk.Event) -> str:
            try:
                if _v_can_scroll():
                    canvas.yview_scroll(1, "units")
            except tk.TclError:
                pass
            return "break"

        # Exposed for headless tests to assert the no-scroll-when-fitting
        # contract without synthesizing real wheel events.
        canvas._tl_wheel_handler = _on_wheel  # type: ignore[attr-defined]
        canvas._tl_v_can_scroll = _v_can_scroll  # type: ignore[attr-defined]

        def _install_wheel(_e: Any = None) -> None:
            try:
                canvas.bind_all("<MouseWheel>", _on_wheel)
                canvas.bind_all("<Button-4>", _on_button4)
                canvas.bind_all("<Button-5>", _on_button5)
            except tk.TclError:
                pass

        def _uninstall_wheel(_e: Any = None) -> None:
            try:
                canvas.unbind_all("<MouseWheel>")
                canvas.unbind_all("<Button-4>")
                canvas.unbind_all("<Button-5>")
            except tk.TclError:
                pass

        canvas.bind("<Enter>", _install_wheel)
        canvas.bind("<Leave>", _uninstall_wheel)
        inner.bind("<Destroy>", _uninstall_wheel, add="+")

    return inner, canvas


__all__ = [
    "BaseModalDialog",
    "BaseEditorDialog",
    "protect_combobox_wheel",
    "make_scrollable_form",
]


# Silence linters that flag unused imports — these are referenced
# transitively in the docstrings and via :func:`set_status`.
_ = Any
