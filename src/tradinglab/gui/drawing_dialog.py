"""Modeless edit dialog for a single drawing (Feature C).

Opened by double-clicking a drawing on the chart (or via the
right-click "Edit Properties…" menu item). Mirrors the design
language of ``per_indicator_dialog.py``:

* Modeless Toplevel — does NOT block the main window.
* Singleton per ``drawing.id`` (managed by
  ``ChartApp._drawing_dialogs`` keyed by id; a second open
  request lifts/deiconifies the existing window).
* Live-commit with a small debounce — every change to color,
  width, style, price, or label fires
  :meth:`DrawingStore.update` after the debounce window so the
  chart updates as the user types.
* ESC closes the dialog.
* Built-in **Delete this line** button — same operation as the
  right-click "Delete this line" menu item, but reachable without
  re-finding the line on the chart.
* Auto-closes when the drawing is removed externally (right-click
  delete on the chart, "Clear All Drawings on <TICKER>", etc.).
"""
from __future__ import annotations

import math
import tkinter as tk
from collections.abc import Callable
from tkinter import colorchooser, ttk
from typing import Any

from ..drawings import DrawingStore
from ..drawings.model import Drawing, _coerce_width
from ._modal_base import BaseModalDialog, protect_combobox_wheel

# Debounce window (ms) between the last key-up / slider tweak and
# the actual ``store.update`` call. Matches the ~250 ms feel used by
# ``IndicatorDialog``'s row commits — long enough to coalesce a
# burst of typing, short enough that the chart still feels live.
_COMMIT_DEBOUNCE_MS: int = 200

_DIALOG_GEOMETRY: str = "360x320"
_DIALOG_MINSIZE: tuple = (320, 300)

# Inline-error text shown beneath the Price entry when the typed
# value isn't a valid non-negative number. Stored as module-level
# constants so unit tests (and any future localisation pass) can
# reference the exact strings (audit ``price-coerce-garbage`` and
# ``price-coerce-negative``).
_PRICE_HINT_NEGATIVE: str = "Enter a non-negative price."
_PRICE_HINT_GARBAGE: str = "Enter a number (e.g. 92.50)."
# Subtle red that reads on both light and dark window chrome —
# matches the muted destructive accent used elsewhere in the app.
_PRICE_HINT_COLOR: str = "#c0392b"

# Map of canonical (lowercase) style name → user-facing radio
# button label. ``"dashdot"`` was added after polish-review (audit
# ``drawing-style-options``) so users have a markedly different
# alternative when dashed and dotted look similar at low width;
# the display label is humanized ("Dash-dot") while the stored
# value stays lowercase to match ``model.VALID_STYLES``.
_STYLE_RADIO_LABELS: dict[str, str] = {
    "solid": "Solid",
    "dashed": "Dashed",
    "dotted": "Dotted",
    "dashdot": "Dash-dot",
}

# Muted live-commit hint displayed just above the bottom button bar
# so users know their changes are already saved to the store before
# they reach for the Close button (audit ``dialog-button-paradigms``).
# Pairs with the analogous footnote in ``per_indicator_dialog.py`` —
# both live-commit dialogs in the app expose the same wording.
_LIVE_COMMIT_HINT: str = "Changes apply immediately."
_LIVE_COMMIT_HINT_COLOR: str = "#888888"


class DrawingDialog(BaseModalDialog):
    """Edit-properties popup for a single :class:`Drawing`.

    Parameters
    ----------
    parent
        The :class:`tkinter.Tk` root (usually ``ChartApp``).
    store
        The shared :class:`DrawingStore` instance. Mutations route
        through ``store.update`` / ``store.remove`` so the event
        bus fires and the chart re-renders.
    drawing
        The drawing being edited. The dialog tracks ``drawing.id``
        and reads fresh state via ``store.get`` on every commit so
        stale-instance issues (drawing edited from another popup,
        ticker switched out from under us) don't blow up the
        commit path.
    on_close
        Optional callback fired exactly once when the dialog
        closes. ``ChartApp`` passes a remove-from-registry handle.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        store: DrawingStore,
        drawing: Drawing,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        # Modeless dialog — pass ``grab=False`` to _finalize_modal.
        # Inherits the base class's title / transient / geometry
        # persistence wiring; the ``_close`` method still owns the
        # debounced-commit flush + unsubscribe + on_close callback.
        super().__init__(
            parent,
            title=self._title_for(drawing),
            geometry_key="dlg.drawing",
            default_geometry=_DIALOG_GEOMETRY,
        )
        try:
            self.minsize(*_DIALOG_MINSIZE)
        except tk.TclError:
            pass
        # Stash the parent (typically ``ChartApp``) so ``_apply_theme``
        # can re-read the resolved palette on light↔dark toggle. The
        # parent's ``_theme`` attribute is the canonical source — same
        # pattern that :class:`IndicatorDialog._apply_theme` uses
        # (audit ``tk-frame-swatch-theme``).
        self._app = parent
        self._store = store
        self._drawing_id: str = drawing.id
        self._on_close_cb = on_close
        # Debounced commit bookkeeping.
        self._commit_job: str | None = None
        # Closed-already flag so ``_close`` is idempotent even when
        # both the on_close protocol AND the ESC binding fire.
        self._closed: bool = False
        # Tk variables — initialised from the live drawing state.
        self._price_var = tk.StringVar(value=f"{drawing.price:g}")
        self._color_var = tk.StringVar(value=drawing.color)
        self._width_var = tk.DoubleVar(value=float(drawing.width))
        self._style_var = tk.StringVar(value=drawing.style)
        self._label_var = tk.StringVar(value=drawing.label)
        # Subscribe to store events so we auto-close when the
        # drawing is removed externally.
        self._unsubscribe = store.subscribe(self._on_store_event)
        self._build_layout()
        protect_combobox_wheel(self)
        # Modeless: no grab, no Return-key primary. WM_DELETE / ESC
        # both route through ``_close`` which flushes pending commits.
        self._finalize_modal(primary=None, cancel=self._close, grab=False)

    # ------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------

    def _build_layout(self) -> None:
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        # Two-column grid: label + widget. Pad each row generously.
        grid = ttk.Frame(outer)
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(1, weight=1)

        # --- price -----------------------------------------------
        ttk.Label(grid, text="Price:").grid(
            row=0, column=0, sticky="nw", padx=(0, 8), pady=4)
        # Stack the entry + inline error hint inside a sub-frame so
        # the hint can appear directly beneath the entry without
        # disturbing the outer grid's row numbering. Audit
        # ``price-coerce-garbage``: the hint surfaces a one-line
        # explanation ("Enter a number…" / "Enter a non-negative
        # price.") when ``_parsed_price`` would reject the typed
        # value, so the user understands why the line on the chart
        # isn't moving instead of assuming the dialog is broken.
        price_box = ttk.Frame(grid)
        price_box.grid(row=0, column=1, sticky="ew", pady=4)
        price_box.columnconfigure(0, weight=1)
        price_entry = ttk.Entry(price_box, textvariable=self._price_var,
                                width=14)
        price_entry.grid(row=0, column=0, sticky="ew")
        self._price_hint = ttk.Label(
            price_box, text="", foreground=_PRICE_HINT_COLOR,
        )
        # Reserve the slot below the entry up-front; the label's
        # ``text`` flips between empty (no hint) and the canonical
        # message strings, but it always occupies the same row so
        # the dialog doesn't reflow as the user types.
        self._price_hint.grid(row=1, column=0, sticky="w", pady=(2, 0))
        # Live-commit on keystroke + on focus-out (which the
        # debounce will collapse together). The same trace also
        # refreshes the inline hint so the user gets immediate
        # feedback as they type.
        self._price_var.trace_add("write", lambda *_: self._schedule_commit())
        self._price_var.trace_add(
            "write", lambda *_: self._update_price_hint())

        # --- color -----------------------------------------------
        ttk.Label(grid, text="Color:").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        color_frame = ttk.Frame(grid)
        color_frame.grid(row=1, column=1, sticky="ew", pady=4)
        self._color_swatch = tk.Frame(
            color_frame, width=24, height=20,
            highlightthickness=1, highlightbackground="#888888",
        )
        # The swatch's ``background`` IS the data (current drawing
        # color); mark it ``_no_theme`` so any future tk.Frame walker
        # in this dialog (or its parent's cascade) doesn't repaint it.
        # ``highlightbackground`` is the border ring around the swatch
        # and IS theme-following — set by :meth:`_apply_theme`.
        self._color_swatch._no_theme = True  # type: ignore[attr-defined]
        self._color_swatch.pack(side="left", padx=(0, 6))
        self._apply_swatch_color(self._color_var.get())
        # Audit ``clickable-swatch``: the swatch reads as a button
        # affordance (24x20px solid color rectangle next to a
        # "Choose…" label) — users expect to click it directly
        # instead of hunting for the explicit button. Bind Button-1
        # so a left-click on the swatch opens the color picker, and
        # swap the cursor to ``hand2`` to advertise the affordance.
        try:
            self._color_swatch.configure(cursor="hand2")
        except tk.TclError:
            pass
        self._color_swatch.bind(
            "<Button-1>", lambda _e: self._choose_color())
        ttk.Button(
            color_frame, text="Choose…",
            command=self._choose_color,
        ).pack(side="left")

        # --- width -----------------------------------------------
        ttk.Label(grid, text="Width:").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        width_frame = ttk.Frame(grid)
        width_frame.grid(row=2, column=1, sticky="ew", pady=4)
        self._width_value_label = ttk.Label(
            width_frame, text=self._format_width(self._width_var.get()))
        # Slider 1.0 – 5.0 in 0.5 steps. Matplotlib's linewidth is
        # measured in points; below 1.0 the four styles
        # (solid / dashed / dotted / dashdot) become visually
        # indistinguishable on most displays, so the floor is
        # bumped from the original 0.5 to 1.0 (audit
        # ``drawing-style-options``).
        self._width_slider = ttk.Scale(
            width_frame, from_=1.0, to=5.0, orient="horizontal",
            variable=self._width_var,
            command=self._on_width_drag,
        )
        self._width_slider.pack(side="left", fill="x", expand=True,
                                padx=(0, 6))
        # Snap the variable to a half-integer when the user releases
        # the slider thumb so the slider's physical position lines up
        # with the displayed label after every drag. Keyboard-driven
        # changes (focus + arrow) still get quantized inside
        # ``_commit_now``.
        self._width_slider.bind(
            "<ButtonRelease-1>", self._on_width_release)
        self._width_value_label.pack(side="left")

        # --- style -----------------------------------------------
        ttk.Label(grid, text="Style:").grid(
            row=3, column=0, sticky="w", padx=(0, 8), pady=4)
        style_frame = ttk.Frame(grid)
        style_frame.grid(row=3, column=1, sticky="ew", pady=4)
        # VALID_STYLES is a frozenset (unordered); the display
        # order is fixed for UX consistency. ``dashdot`` was added
        # after polish-review (audit ``drawing-style-options``) so
        # users always have a markedly different alternative when
        # dashed/dotted look similar at low width. The radio label
        # is humanized (see ``_STYLE_RADIO_LABELS`` at module-level)
        # even though the canonical value remains lowercase.
        for _i, style_name in enumerate(
            ("solid", "dashed", "dotted", "dashdot"),
        ):
            ttk.Radiobutton(
                style_frame,
                text=_STYLE_RADIO_LABELS[style_name],
                value=style_name, variable=self._style_var,
                command=self._schedule_commit,
            ).pack(side="left", padx=(0, 8))
        # Belt-and-suspenders: the Radiobutton ``command`` only
        # fires on user click, not when the underlying StringVar
        # is set programmatically (tests do this; future code may
        # too). Trace ensures the commit pipeline runs either way.
        self._style_var.trace_add("write", lambda *_: self._schedule_commit())

        # --- label -----------------------------------------------
        ttk.Label(grid, text="Label:").grid(
            row=4, column=0, sticky="w", padx=(0, 8), pady=4)
        label_entry = ttk.Entry(grid, textvariable=self._label_var)
        label_entry.grid(row=4, column=1, sticky="ew", pady=4)
        self._label_var.trace_add("write", lambda *_: self._schedule_commit())

        # --- live-commit hint ------------------------------------
        # Muted text just above the bottom bar so users understand
        # their edits are already saved to the store; the Close
        # button below merely dismisses the window (audit
        # ``dialog-button-paradigms``).
        hint_wrap = ttk.Frame(outer)
        hint_wrap.pack(fill="x", pady=(8, 0))
        self._live_commit_hint = ttk.Label(
            hint_wrap, text=_LIVE_COMMIT_HINT,
            foreground=_LIVE_COMMIT_HINT_COLOR,
        )
        self._live_commit_hint.pack(side="left", anchor="w")

        # --- bottom bar ------------------------------------------
        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(6, 0))
        # Delete on the left so the destructive action is visually
        # separated from the safe "Close" button on the right.
        ttk.Button(bar, text="Delete this line",
                   command=self._on_delete).pack(side="left")
        ttk.Button(bar, text="Close",
                   command=self._close).pack(side="right")

        # Initial focus on the price entry so a typing-first user
        # can adjust the price immediately without clicking.
        try:
            price_entry.focus_set()
            price_entry.icursor("end")
            price_entry.selection_range(0, "end")
        except tk.TclError:
            pass

        # Apply current theme once the widget tree is built. This
        # repaints the Toplevel + plain-tk chrome AND syncs the swatch
        # border colour to ``theme["grid"]`` so the swatch no longer
        # shows a hardcoded mid-grey ring against the dark window
        # background (audit ``tk-frame-swatch-theme``).
        self._apply_theme()

    # ------------------------------------------------------------
    # Title helper
    # ------------------------------------------------------------

    @staticmethod
    def _title_for(drawing: Drawing) -> str:
        suffix = f" — {drawing.ticker}" if drawing.ticker else ""
        return f"Edit horizontal line{suffix}"

    @staticmethod
    def _format_width(value: Any) -> str:
        try:
            return f"{DrawingDialog._quantize_width(value):.1f}"
        except (TypeError, ValueError):
            return "1.0"

    @staticmethod
    def _quantize_width(value: Any) -> float:
        """Snap to 0.5 increments inside ``[1.0, 5.0]``.

        Audit ``drawing-width-spinbox``: ttk.Scale has no native
        ``resolution=`` option, so dragging the thumb produces
        arbitrary continuous floats (``2.7384...``). The displayed
        value label rounded to one decimal (``2.7``) but the
        persisted ``Drawing.width`` carried the full unrounded float
        — bad UX because re-opening the dialog showed ``2.7`` while
        the chart rendered the original ``2.7384...``-pt line.

        Quantizing on both display and commit (and snapping the
        variable on ``<ButtonRelease-1>``) means the rendered line,
        the displayed label, and the persisted value are always the
        same exact half-integer.

        Uses ``floor(v * 2 + 0.5) / 2`` rather than ``round(v * 2)
        / 2`` deliberately: Python's :func:`round` does banker's
        rounding (round-half-to-even) so ``round(0.5) == 0``, which
        would surprise users sliding through the half-step
        positions (``1.25 → 1.0`` instead of ``1.25 → 1.5``).
        Floor-with-half-bias gives the conventional round-half-up
        behaviour that matches the visual midpoint a user expects.
        """
        try:
            v = float(value)
        except (TypeError, ValueError):
            return 1.0
        if not math.isfinite(v):
            return 1.0
        q = math.floor(v * 2 + 0.5) / 2
        if q < 1.0:
            return 1.0
        if q > 5.0:
            return 5.0
        return q

    # ------------------------------------------------------------
    # Color picker
    # ------------------------------------------------------------

    def _choose_color(self) -> None:
        current = self._color_var.get() or "#2962ff"
        try:
            picked = colorchooser.askcolor(
                color=current, parent=self,
                title="Pick line color")
        except tk.TclError:
            picked = (None, None)
        new_hex = picked[1] if picked and picked[1] else None
        if not new_hex:
            return
        # Tk gives back ``#aabbcc`` lowercase; keep it lowercase to
        # match the codebase's hex-literal convention (see
        # `tests/unit/test_hex_case_constants.py`).
        new_hex = new_hex.lower() if new_hex.startswith("#") else new_hex
        self._color_var.set(new_hex)
        self._apply_swatch_color(new_hex)
        self._schedule_commit()

    def _apply_swatch_color(self, color: str) -> None:
        try:
            self._color_swatch.configure(background=color)
        except tk.TclError:
            pass

    # ------------------------------------------------------------
    # Theme cascade (audit ``tk-frame-swatch-theme``)
    # ------------------------------------------------------------

    def _apply_theme(self) -> None:
        """Repaint Toplevel + plain-tk chrome to match the parent app's theme.

        Mirrors :meth:`IndicatorDialog._apply_theme` (same skip-via-
        ``_no_theme`` walking convention so the colour swatch — whose
        ``background`` IS the data — is left alone).

        Additionally syncs the swatch's ``highlightbackground`` (its
        border ring) to ``theme["grid"]`` so the ring follows the
        theme instead of staying a hardcoded ``#888888`` mid-grey
        regardless of mode.

        Idempotent and safe to call from a torn-down state.
        """
        try:
            theme = getattr(self._app, "_theme", None) or {}
        except Exception:  # noqa: BLE001
            theme = {}
        bg = theme.get("win_bg")
        border = theme.get("grid")
        if bg:
            try:
                self.configure(background=bg)
            except tk.TclError:
                pass

            def _walk(w: tk.Widget) -> None:
                for child in w.winfo_children():
                    cls = child.__class__
                    if getattr(child, "_no_theme", False):
                        continue
                    if cls is tk.Frame or cls is tk.Canvas:
                        try:
                            child.configure(background=bg)
                        except tk.TclError:
                            pass
                    _walk(child)

            try:
                _walk(self)
            except tk.TclError:
                pass
        if border:
            try:
                self._color_swatch.configure(highlightbackground=border)
            except tk.TclError:
                pass

    # ------------------------------------------------------------
    # Width slider
    # ------------------------------------------------------------

    def _on_width_drag(self, _value: str) -> None:
        # ttk.Scale fires this every pixel of drag. Update the
        # display label immediately; let the debounced commit
        # coalesce the actual store update.
        try:
            self._width_value_label.configure(
                text=self._format_width(self._width_var.get()))
        except tk.TclError:
            pass
        self._schedule_commit()

    def _on_width_release(self, _event: Any) -> None:
        # Snap to the nearest 0.5 increment so the slider's physical
        # thumb position lines up with the displayed value AND the
        # persisted ``Drawing.width``. The variable trace fires the
        # debounced commit; the next ``_commit_now`` re-quantizes
        # (idempotent), then the store update goes through with the
        # exact half-integer.
        try:
            current = float(self._width_var.get())
        except (TypeError, ValueError):
            return
        snapped = self._quantize_width(current)
        if abs(current - snapped) > 1e-9:
            try:
                self._width_var.set(snapped)
            except tk.TclError:
                pass

    # ------------------------------------------------------------
    # Debounced commit
    # ------------------------------------------------------------

    def _schedule_commit(self) -> None:
        if self._closed:
            return
        if self._commit_job is not None:
            try:
                self.after_cancel(self._commit_job)
            except tk.TclError:
                pass
        try:
            self._commit_job = self.after(
                _COMMIT_DEBOUNCE_MS, self._commit_now)
        except tk.TclError:
            # No mainloop (headless tests) — commit synchronously.
            self._commit_now()

    def _commit_now(self) -> None:
        self._commit_job = None
        if self._closed:
            return
        # Snapshot what's in the form. Coerce price and width
        # here; the model also coerces on ``replace`` but we want
        # to silently drop a non-numeric typed price (rather than
        # surface an error) — the user is mid-typing.
        price = self._parsed_price()
        if price is None:
            return  # leave the chart at the last good value
        changes: dict = {
            "price": price,
            "color": self._color_var.get(),
            "width": _coerce_width(self._quantize_width(self._width_var.get())),
            "style": self._style_var.get(),
            "label": self._label_var.get(),
        }
        try:
            self._store.update(self._drawing_id, **changes)
        except Exception:  # noqa: BLE001
            # Mutation failed silently — keep the dialog open so
            # the user can re-attempt.
            pass

    def _parsed_price(self) -> float | None:
        """Return the typed price as a finite ``float >= 0``.

        Returns ``None`` for empty/non-numeric/NaN/Inf input AND
        for negative values. Stock charts have a y-axis that starts
        at 0, so a negative price has no meaningful pixel position;
        the most likely cause is a typo (extra ``-``) or a forgotten
        decimal point. Returning ``None`` causes :meth:`_commit_now`
        to silently leave the chart at the last good value, which
        preserves the user's in-progress typing without snapping the
        line off-screen. Audit ``price-coerce-negative``.
        """
        raw = self._price_var.get().strip()
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            return None
        if not math.isfinite(value):
            return None
        if value < 0.0:
            return None
        return value

    def _classify_price_input(self) -> str:
        """Categorise the current Price-entry contents.

        Returns one of:

        * ``"ok"`` — empty, or a finite non-negative number.
        * ``"negative"`` — parses to a finite negative number.
        * ``"garbage"`` — non-numeric (or NaN/Inf).

        Drives the inline hint label (audit
        ``price-coerce-garbage``). Empty is treated as ``"ok"`` —
        the user may be mid-edit and we don't want to flash a red
        warning the instant they delete all characters before
        typing a replacement.
        """
        raw = self._price_var.get().strip()
        if not raw:
            return "ok"
        try:
            value = float(raw)
        except ValueError:
            return "garbage"
        if not math.isfinite(value):
            return "garbage"
        if value < 0.0:
            return "negative"
        return "ok"

    def _update_price_hint(self) -> None:
        """Refresh the inline hint label under the Price entry.

        Called from the ``_price_var`` write trace on every
        keystroke. The hint flips between empty (entry is valid or
        empty) and one of the canonical explanatory strings
        (``_PRICE_HINT_NEGATIVE`` / ``_PRICE_HINT_GARBAGE``).
        Defensive against torn-down or partially-built dialogs —
        a missing ``_price_hint`` attribute is silently tolerated
        so unit tests that bypass ``_build_layout`` don't trip.
        Audit ``price-coerce-garbage``.
        """
        hint = getattr(self, "_price_hint", None)
        if hint is None:
            return
        kind = self._classify_price_input()
        text = ""
        if kind == "negative":
            text = _PRICE_HINT_NEGATIVE
        elif kind == "garbage":
            text = _PRICE_HINT_GARBAGE
        try:
            hint.configure(text=text)
        except tk.TclError:
            pass

    # ------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------

    def _on_delete(self) -> None:
        try:
            self._store.remove(self._drawing_id)
        except Exception:  # noqa: BLE001
            pass
        # The "remove" event will trigger ``_on_store_event`` which
        # closes us automatically; call ``_close`` defensively in
        # case the store is autosave=False (no subscriber re-entry).
        self._close()

    # ------------------------------------------------------------
    # Store event handler
    # ------------------------------------------------------------

    def _on_store_event(
        self,
        event: str,
        _ticker: str | None,
        drawing: Drawing | None,
    ) -> None:
        # Auto-close on any event that removes our drawing.
        if event in ("remove",) and drawing is not None \
                and drawing.id == self._drawing_id:
            self._close()
            return
        if event in ("clear_all",):
            self._close()
            return
        if event == "clear_symbol":
            # The ticker was cleared; close if our drawing's ticker
            # was the target. Look it up — _ticker is the bucket key.
            current = self._store.get(self._drawing_id)
            if current is None:
                self._close()

    # ------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------

    def _close(self) -> None:
        if self._closed:
            return
        # Flush any pending debounced commit BEFORE marking closed
        # so the user's last edit (typed within
        # ``_COMMIT_DEBOUNCE_MS`` of pressing close) isn't silently
        # dropped. The Tk ``StringVar``/``DoubleVar`` references
        # used by ``_commit_now`` are destroyed below; we must read
        # them while they still exist.  See Feature C regression
        # #C4 (adversarial review 2026-05).
        if self._commit_job is not None:
            try:
                self.after_cancel(self._commit_job)
            except tk.TclError:
                pass
            self._commit_job = None
            try:
                self._commit_now()
            except Exception:  # noqa: BLE001
                pass
        self._closed = True
        try:
            self._unsubscribe()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass
        cb = self._on_close_cb
        self._on_close_cb = None
        if cb is not None:
            try:
                cb()
            except Exception:  # noqa: BLE001
                pass


__all__ = ["DrawingDialog"]
