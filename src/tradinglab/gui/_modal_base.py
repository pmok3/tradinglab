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
from tkinter import ttk
from typing import Any, Callable, Optional, Tuple

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
        geometry_key: Optional[str] = None,
        default_geometry: str = "640x480",
        resizable: Tuple[bool, bool] = (True, True),
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
        primary: Optional[Callable[[], None]] = None,
        cancel: Optional[Callable[[], None]] = None,
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
        geometry_key: Optional[str] = None,
        default_geometry: str = "900x600",
        resizable: Tuple[bool, bool] = (True, True),
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
        self.btn_validate: Optional[ttk.Button] = None
        self.btn_cancel: Optional[ttk.Button] = None
        self.btn_apply: Optional[ttk.Button] = None
        self.btn_save_close: Optional[ttk.Button] = None

    # ------------------------------------------------------------------
    # Footer builder
    # ------------------------------------------------------------------
    def _build_editor_footer(
        self,
        parent: tk.Misc,
        *,
        on_validate: Optional[Callable[[], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
        on_apply: Optional[Callable[[], None]] = None,
        on_save_close: Optional[Callable[[], None]] = None,
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


__all__ = [
    "BaseModalDialog",
    "BaseEditorDialog",
]


# Silence linters that flag unused imports — these are referenced
# transitively in the docstrings and via :func:`set_status`.
_ = Any
