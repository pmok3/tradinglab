"""Modal-ish "Edit Entry Strategy" dialog.

Mirrors :mod:`gui.exits_dialog` but for the simpler
:class:`tradinglab.entries.model.EntryStrategy` aggregate (one
trigger per strategy, no leg/OCO machinery, no trailing-stop / time-of-
day kinds).

Surface area
------------

A single :class:`EntriesDialog` instance can be opened by passing in
either a fresh-blank or existing :class:`EntryStrategy`. The dialog
exposes:

* **Identity**: id (read-only) + name + enabled checkbox.
* **Direction**: LONG / SHORT radio.
* **Universe**: radio over symbols / scanner_id / from_attached_chart;
  per-mode entry widget.
* **Trigger**: kind dropdown over MARKET / LIMIT / STOP / STOP_LIMIT /
  INDICATOR / SCANNER_ALERT, with kind-specific param widgets
  (price / stop_price / scanner_id / a :class:`BlockEditor` for the
  INDICATOR condition).
* **Sizing**: kind dropdown FIXED_QTY / FIXED_NOTIONAL + qty / notional
  + share_rounding (down / nearest).
* **On-fill exits**: multi-select checkboxes against
  ``exit_strategies`` passed at construction.
* **Lifecycle**: cooldown_secs, max_fires_per_session_per_symbol,
  max_fires_per_session_total, position_already_open_policy,
  arm_window_start, arm_window_end, require_market_open.

The dialog is **not** a singleton — callers (the EntriesTab) construct
a fresh dialog per open. ``Save`` / ``Save & Close`` runs
:func:`tradinglab.entries.model.validate_strategy` and refuses to
fire ``on_save`` when errors exist; the errors render inline in the
status label and via per-field labels.
"""
from __future__ import annotations

import logging
import tkinter as tk
from collections.abc import Callable, Sequence
from tkinter import ttk

from ..entries.model import (
    Direction,
    EntryStrategy,
    PositionAlreadyOpenPolicy,
    ShareRounding,
    SizingKind,
    TriggerKind,
    Universe,
    validate_strategy,
)
from ..exits.model import ExitStrategy
from ..scanner.model import Group as ConditionGroup
from ._modal_base import protect_combobox_wheel
from ._modal_keys import bind_modal_keys
from .colors import ERROR_RED, MUTED_GREY
from .scanner_block_editor import BlockEditor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_TRIGGER_KIND_CHOICES: tuple[tuple[TriggerKind, str], ...] = (
    (TriggerKind.MARKET,        "Market"),
    (TriggerKind.LIMIT,         "Limit"),
    (TriggerKind.STOP,          "Stop"),
    (TriggerKind.STOP_LIMIT,    "Stop-Limit"),
    (TriggerKind.INDICATOR,     "Indicator"),
    (TriggerKind.SCANNER_ALERT, "Scanner Alert"),
)
_TRIGGER_KIND_LABEL = {k: lbl for k, lbl in _TRIGGER_KIND_CHOICES}
_TRIGGER_KIND_BY_LABEL = {lbl: k for k, lbl in _TRIGGER_KIND_CHOICES}

_SIZING_KIND_CHOICES: tuple[tuple[SizingKind, str], ...] = (
    (SizingKind.FIXED_QTY,      "Fixed Qty"),
    (SizingKind.FIXED_NOTIONAL, "Fixed Notional ($)"),
)
_SIZING_KIND_LABEL = {k: lbl for k, lbl in _SIZING_KIND_CHOICES}
_SIZING_KIND_BY_LABEL = {lbl: k for k, lbl in _SIZING_KIND_CHOICES}

_DIRECTION_CHOICES = (Direction.LONG, Direction.SHORT)

_ROUNDING_CHOICES: tuple[tuple[ShareRounding, str], ...] = (
    (ShareRounding.DOWN,    "Round down"),
    (ShareRounding.NEAREST, "Round nearest"),
)
_ROUNDING_BY_LABEL = {lbl: r for r, lbl in _ROUNDING_CHOICES}

_POLICY_CHOICES: tuple[tuple[PositionAlreadyOpenPolicy, str], ...] = (
    (PositionAlreadyOpenPolicy.BLOCK, "Block"),
    (PositionAlreadyOpenPolicy.STACK, "Stack"),
)
_POLICY_BY_LABEL = {lbl: p for p, lbl in _POLICY_CHOICES}

_INDICATOR_INTERVAL_CHOICES: tuple[str, ...] = (
    "1m", "5m", "15m", "30m", "1h", "1d",
)


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class EntriesDialog(tk.Toplevel):
    """Modal-ish editor for a single :class:`EntryStrategy`."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        strategy: EntryStrategy | None = None,
        exit_strategies: Sequence[ExitStrategy] = (),
        on_save: Callable[[EntryStrategy], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("Edit Entry Strategy")
        try:
            self.transient(master)
        except tk.TclError:
            pass
        # Geometry persistence — restore last-used size + position;
        # fall back to legacy 1400x780.
        try:
            from .geometry_store import attach_persistent_geometry
            attach_persistent_geometry(self, "dlg.entries", "1400x780")
        except tk.TclError:
            self.geometry("1400x780")
        self.minsize(900, 500)

        self._on_save = on_save
        self._on_cancel = on_cancel
        self._exit_strategies: list[ExitStrategy] = list(exit_strategies)

        # Deep-clone the incoming strategy so unsaved edits don't bleed
        # back into the caller's library snapshot.
        if strategy is None:
            self._draft = EntryStrategy(name="(new entry)")
            self._is_new = True
        else:
            self._draft = EntryStrategy.from_dict(strategy.to_dict())
            self._is_new = False

        # Per-field error labels (populated by _on_validate / _on_save).
        self._field_errors: dict[str, tk.StringVar] = {}
        self._block_editor: BlockEditor | None = None
        # Per-kind param widget container — rebuilt when trigger kind
        # changes.
        self._trigger_params: ttk.Frame | None = None
        self._trigger_param_vars: dict[str, tk.Variable] = {}
        # Universe per-mode widgets — rebuilt when universe radio changes.
        self._universe_params: ttk.Frame | None = None
        self._universe_vars: dict[str, tk.Variable] = {}
        # Exit-id checkbox vars (held to keep tk vars alive).
        self._exit_id_vars: dict[str, tk.BooleanVar] = {}

        self._build_layout()
        self._load_into_widgets()
        # Block accidental wheel-driven value changes on every Combobox /
        # Spinbox in the dialog. Without this, scrolling the form while
        # the cursor is over the operator combobox silently mutates the
        # selected op (e.g. ``crosses_above`` → ``between``) and the
        # corrupted strategy is then persisted on Save — see the
        # ``protect_combobox_wheel`` docstring for the full story.
        self._protect_combobox_wheel()
        bind_modal_keys(
            self,
            cancel=self._on_cancel_clicked,
            primary=lambda: self._on_save_clicked(close=True),
        )

    # ------------------------------------------------------------------
    # Public test/UX hooks
    # ------------------------------------------------------------------

    @property
    def draft(self) -> EntryStrategy:
        """Live (mutable) draft snapshot. Tests reach in to inspect state."""
        return self._draft

    @property
    def block_editor(self) -> BlockEditor | None:
        """The embedded BlockEditor (only when trigger.kind == INDICATOR)."""
        return self._block_editor

    @property
    def is_new(self) -> bool:
        return self._is_new

    @property
    def exit_strategy_ids_selected(self) -> tuple[str, ...]:
        return tuple(
            sid for sid, var in self._exit_id_vars.items() if bool(var.get())
        )

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        outer = ttk.Frame(self, padding=6)
        outer.pack(fill="both", expand=True)

        # Big-bet item #6: replace the 6-tab Notebook with a single
        # vertically-scrollable form composed of LabelFrames. Same
        # logical sections, dramatically more usable (no tab-hopping;
        # full draft visible at a glance for cross-section validation).
        scroll_host = ttk.Frame(outer)
        scroll_host.pack(fill="both", expand=True)
        canvas = tk.Canvas(scroll_host, highlightthickness=0, borderwidth=0)
        self._form_canvas = canvas
        vbar = ttk.Scrollbar(scroll_host, orient="vertical",
                             command=canvas.yview)
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

        form = ttk.Frame(canvas)
        form_window = canvas.create_window((0, 0), window=form, anchor="nw")

        def _on_form_configure(_e=None):
            try:
                canvas.configure(scrollregion=canvas.bbox("all"))
            except tk.TclError:
                pass
        form.bind("<Configure>", _on_form_configure)

        def _on_canvas_configure(e):
            try:
                canvas.itemconfigure(form_window, width=e.width)
            except tk.TclError:
                pass
        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(e):
            try:
                canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except tk.TclError:
                pass
        canvas.bind("<Enter>",
                    lambda _e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>",
                    lambda _e: canvas.unbind_all("<MouseWheel>"))

        sections: tuple[tuple[str, Callable[[tk.Misc], ttk.Frame]], ...] = (
            ("Identity",        self._build_identity_tab),
            ("Universe",        self._build_universe_tab),
            ("Trigger",         self._build_trigger_tab),
            ("Sizing",          self._build_sizing_tab),
            ("On-fill exits",   self._build_exits_tab),
            ("Lifecycle",       self._build_lifecycle_tab),
        )
        self._section_frames: dict[str, ttk.LabelFrame] = {}
        for title, builder in sections:
            lf = ttk.LabelFrame(form, text=title, padding=4)
            lf.pack(fill="x", expand=False, padx=2, pady=(4, 0))
            inner = builder(lf)
            inner.pack(fill="both", expand=True)
            self._section_frames[title] = lf

        # Footer
        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(6, 0))
        self._status_var = tk.StringVar(value="")
        self._status_lbl = ttk.Label(
            footer, textvariable=self._status_var, foreground=ERROR_RED,
        )
        self._status_lbl.pack(side="left", fill="x", expand=True)
        # Footer buttons: Windows dialog convention (audit
        # ``button-order-windows``) — visual order left→right
        # ``[Validate] [Apply] [Save & Close] [Cancel]`` with the
        # dismiss action rightmost. ``side="right"`` reverses pack
        # order, so pack Cancel first (lands rightmost), then
        # Save & Close, Apply, Validate.
        ttk.Button(footer, text="Cancel", command=self._on_cancel_clicked).pack(
            side="right", padx=(2, 0))
        ttk.Button(footer, text="Save & Close",
                   command=lambda: self._on_save_clicked(close=True)).pack(
            side="right", padx=(2, 0))
        ttk.Button(footer, text="Apply",
                   command=lambda: self._on_save_clicked(close=False)).pack(
            side="right", padx=(2, 0))
        ttk.Button(footer, text="Validate", command=self._on_validate).pack(
            side="right", padx=(2, 0))

    def _err_label(self, parent: tk.Misc, key: str) -> ttk.Label:
        var = tk.StringVar(value="")
        self._field_errors[key] = var
        lbl = ttk.Label(parent, textvariable=var, foreground=ERROR_RED)
        return lbl

    # ----- Identity -----

    def _build_identity_tab(self, parent: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(parent, padding=6)

        ttk.Label(f, text="Strategy id:").grid(row=0, column=0, sticky="w")
        self._id_var = tk.StringVar(value=self._draft.id)
        ttk.Label(f, textvariable=self._id_var, foreground=MUTED_GREY).grid(
            row=0, column=1, sticky="w", padx=(6, 0))

        ttk.Label(f, text="Name:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._name_var = tk.StringVar(value=self._draft.name)
        ttk.Entry(f, textvariable=self._name_var, width=40).grid(
            row=1, column=1, sticky="ew", padx=(6, 0), pady=(6, 0))
        self._name_var.trace_add("write", lambda *_: self._on_name_changed())
        self._err_label(f, "name").grid(row=2, column=1, sticky="w")

        ttk.Label(f, text="Direction:").grid(row=3, column=0, sticky="w",
                                             pady=(6, 0))
        self._direction_var = tk.StringVar(value=self._draft.direction.value)
        dirf = ttk.Frame(f)
        dirf.grid(row=3, column=1, sticky="w", pady=(6, 0))
        for d in _DIRECTION_CHOICES:
            ttk.Radiobutton(
                dirf, text=d.value.upper(), value=d.value,
                variable=self._direction_var,
                command=self._on_direction_changed,
            ).pack(side="left", padx=(0, 8))

        self._enabled_var = tk.BooleanVar(value=self._draft.enabled)
        ttk.Checkbutton(
            f, text="Enabled (in library)", variable=self._enabled_var,
            command=self._on_enabled_changed,
        ).grid(row=4, column=1, sticky="w", pady=(6, 0))

        f.columnconfigure(1, weight=1)
        return f

    # ----- Universe -----

    def _build_universe_tab(self, parent: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(parent, padding=6)

        # Determine current radio selection
        u = self._draft.universe
        if u.from_attached_chart:
            initial = "from_attached_chart"
        elif u.scanner_id:
            initial = "scanner_id"
        else:
            initial = "symbols"
        self._universe_radio_var = tk.StringVar(value=initial)

        ttk.Label(f, text="Universe mode:").grid(row=0, column=0, sticky="w")
        rf = ttk.Frame(f)
        rf.grid(row=0, column=1, sticky="w")
        for label, value in (
            ("Symbols list",        "symbols"),
            ("Scanner id",          "scanner_id"),
            ("From attached chart", "from_attached_chart"),
        ):
            ttk.Radiobutton(
                rf, text=label, value=value,
                variable=self._universe_radio_var,
                command=self._on_universe_radio_changed,
            ).pack(side="left", padx=(0, 8))

        self._universe_params = ttk.Frame(f)
        self._universe_params.grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        self._err_label(f, "universe").grid(
            row=2, column=0, columnspan=2, sticky="w")

        f.columnconfigure(1, weight=1)
        return f

    def _render_universe_params(self) -> None:
        if self._universe_params is None:
            return
        for child in list(self._universe_params.winfo_children()):
            child.destroy()
        self._universe_vars.clear()
        mode = self._universe_radio_var.get()
        if mode == "symbols":
            ttk.Label(
                self._universe_params,
                text="Symbols (comma separated):",
            ).pack(side="left")
            v = tk.StringVar(value=", ".join(self._draft.universe.symbols))
            self._universe_vars["symbols"] = v
            ttk.Entry(self._universe_params, textvariable=v, width=40).pack(
                side="left", padx=(6, 0))
            v.trace_add("write", lambda *_: self._on_universe_field_changed())
        elif mode == "scanner_id":
            ttk.Label(self._universe_params, text="Scanner id:").pack(side="left")
            v = tk.StringVar(value=self._draft.universe.scanner_id or "")
            self._universe_vars["scanner_id"] = v
            ttk.Entry(self._universe_params, textvariable=v, width=40).pack(
                side="left", padx=(6, 0))
            v.trace_add("write", lambda *_: self._on_universe_field_changed())
        else:  # from_attached_chart
            ttk.Label(
                self._universe_params,
                text="(strategy will watch the currently-attached chart symbol)",
                foreground=MUTED_GREY,
            ).pack(side="left")

    # ----- Trigger -----

    def _build_trigger_tab(self, parent: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(parent, padding=6)
        ttk.Label(f, text="Trigger kind:").grid(row=0, column=0, sticky="w")
        self._trigger_kind_var = tk.StringVar(
            value=_TRIGGER_KIND_LABEL[self._draft.trigger.kind])
        cb = ttk.Combobox(
            f, textvariable=self._trigger_kind_var, state="readonly",
            values=[lbl for _, lbl in _TRIGGER_KIND_CHOICES], width=18,
        )
        cb.grid(row=0, column=1, sticky="w", padx=(6, 0))
        cb.bind("<<ComboboxSelected>>",
                lambda _e: self._on_trigger_kind_changed())

        ttk.Label(f, text="Label:").grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        self._trigger_label_var = tk.StringVar(value=self._draft.trigger.label)
        ttk.Entry(f, textvariable=self._trigger_label_var, width=24).grid(
            row=1, column=1, sticky="w", padx=(6, 0), pady=(6, 0))
        self._trigger_label_var.trace_add(
            "write", lambda *_: self._on_trigger_label_changed())

        # Trigger params live in a scrollable canvas so wide indicator
        # rows (e.g. ema(3) crosses_above ema(8)) can scroll horizontally
        # instead of being clipped or overflowing the dialog width.
        body = ttk.Frame(f)
        body.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        canvas = tk.Canvas(body, borderwidth=0, highlightthickness=0)
        vbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        hbar = ttk.Scrollbar(body, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        hbar.pack(side="bottom", fill="x")
        vbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._trigger_params = ttk.Frame(canvas)
        self._trigger_params_window_id = canvas.create_window(
            (0, 0), window=self._trigger_params, anchor="nw")
        self._trigger_params_canvas = canvas

        def _on_trigger_holder_configure(_event: tk.Event) -> None:
            try:
                canvas.configure(scrollregion=canvas.bbox("all"))
                canvas_w = canvas.winfo_width()
                req_w = self._trigger_params.winfo_reqwidth()
                canvas.itemconfigure(
                    self._trigger_params_window_id,
                    width=max(canvas_w, req_w),
                )
            except tk.TclError:
                pass
        self._trigger_params.bind("<Configure>", _on_trigger_holder_configure)

        def _on_trigger_canvas_configure(event: tk.Event) -> None:
            try:
                req_w = self._trigger_params.winfo_reqwidth()
                canvas.itemconfigure(
                    self._trigger_params_window_id,
                    width=max(event.width, req_w),
                )
            except tk.TclError:
                pass
        canvas.bind("<Configure>", _on_trigger_canvas_configure)

        self._err_label(f, "trigger").grid(
            row=3, column=0, columnspan=2, sticky="w")

        f.columnconfigure(1, weight=1)
        f.rowconfigure(2, weight=1)
        return f

    def _render_trigger_params(self) -> None:
        if self._trigger_params is None:
            return
        for child in list(self._trigger_params.winfo_children()):
            child.destroy()
        self._trigger_param_vars.clear()
        self._block_editor = None

        kind = self._draft.trigger.kind
        if kind == TriggerKind.MARKET:
            ttk.Label(
                self._trigger_params,
                text="(MARKET — fires on next CLOSED bar after arm; no parameters)",
                foreground=MUTED_GREY,
            ).pack(anchor="w")
            return

        if kind == TriggerKind.LIMIT:
            self._render_price_field("price", self._draft.trigger.price)
            return

        if kind == TriggerKind.STOP:
            self._render_price_field(
                "stop_price", self._draft.trigger.stop_price,
                label="Stop price:")
            return

        if kind == TriggerKind.STOP_LIMIT:
            self._render_price_field(
                "stop_price", self._draft.trigger.stop_price,
                label="Stop price:")
            self._render_price_field(
                "price", self._draft.trigger.price,
                label="Limit price:")
            return

        if kind == TriggerKind.INDICATOR:
            row = ttk.Frame(self._trigger_params)
            row.pack(fill="x")
            ttk.Label(row, text="Interval:").pack(side="left")
            interval = self._draft.trigger.interval or "1m"
            v_int = tk.StringVar(value=interval)
            self._trigger_param_vars["interval"] = v_int
            ttk.Combobox(
                row, textvariable=v_int, state="readonly",
                values=_INDICATOR_INTERVAL_CHOICES, width=6,
            ).pack(side="left", padx=(6, 12))
            v_int.trace_add(
                "write", lambda *_: self._on_trigger_interval_changed())

            self._intrabar_var = tk.BooleanVar(
                value=bool(self._draft.trigger.evaluate_intrabar))
            ttk.Checkbutton(
                row, text="Evaluate intrabar (forming bar)",
                variable=self._intrabar_var,
                command=self._on_intrabar_changed,
            ).pack(side="left")

            cond = self._draft.trigger.condition or ConditionGroup(
                combinator="and", children=[])
            # Persist the (possibly-defaulted) condition back onto the
            # draft so callers / tests can rely on it being non-None for
            # INDICATOR triggers.
            self._draft.trigger.condition = cond
            self._block_editor = BlockEditor(
                self._trigger_params,
                root=cond,
                on_change=self._on_block_editor_changed,
                default_interval=interval,
            )
            self._block_editor.pack(fill="both", expand=True, pady=(6, 0))
            return

        if kind == TriggerKind.SCANNER_ALERT:
            row = ttk.Frame(self._trigger_params)
            row.pack(fill="x")
            ttk.Label(row, text="Scanner id:").pack(side="left")
            v = tk.StringVar(value=self._draft.trigger.scanner_id or "")
            self._trigger_param_vars["scanner_id"] = v
            ttk.Entry(row, textvariable=v, width=30).pack(
                side="left", padx=(6, 0))
            v.trace_add(
                "write", lambda *_: self._on_trigger_scanner_id_changed())
            return

    def _render_price_field(
        self, attr: str, current: float | None, *, label: str = "Price:",
    ) -> None:
        row = ttk.Frame(self._trigger_params)
        row.pack(fill="x", pady=(2, 0))
        ttk.Label(row, text=label).pack(side="left")
        v = tk.StringVar(value="" if current is None else f"{current:g}")
        self._trigger_param_vars[attr] = v
        ttk.Entry(row, textvariable=v, width=14).pack(side="left", padx=(6, 0))
        v.trace_add("write", lambda *_: self._on_trigger_price_changed(attr))

    # ----- Sizing -----

    def _build_sizing_tab(self, parent: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(parent, padding=6)
        ttk.Label(f, text="Sizing kind:").grid(row=0, column=0, sticky="w")
        self._sizing_kind_var = tk.StringVar(
            value=_SIZING_KIND_LABEL[self._draft.sizing.kind])
        cb = ttk.Combobox(
            f, textvariable=self._sizing_kind_var, state="readonly",
            values=[lbl for _, lbl in _SIZING_KIND_CHOICES], width=22,
        )
        cb.grid(row=0, column=1, sticky="w", padx=(6, 0))
        cb.bind("<<ComboboxSelected>>",
                lambda _e: self._on_sizing_kind_changed())

        ttk.Label(f, text="Qty (FIXED_QTY):").grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        self._sizing_qty_var = tk.StringVar(
            value="" if self._draft.sizing.qty is None
            else f"{self._draft.sizing.qty:g}")
        ttk.Entry(f, textvariable=self._sizing_qty_var, width=12).grid(
            row=1, column=1, sticky="w", padx=(6, 0), pady=(6, 0))
        self._sizing_qty_var.trace_add(
            "write", lambda *_: self._on_sizing_field_changed("qty"))

        ttk.Label(f, text="Notional $ (FIXED_NOTIONAL):").grid(
            row=2, column=0, sticky="w", pady=(6, 0))
        self._sizing_notional_var = tk.StringVar(
            value="" if self._draft.sizing.notional is None
            else f"{self._draft.sizing.notional:g}")
        ttk.Entry(f, textvariable=self._sizing_notional_var, width=12).grid(
            row=2, column=1, sticky="w", padx=(6, 0), pady=(6, 0))
        self._sizing_notional_var.trace_add(
            "write", lambda *_: self._on_sizing_field_changed("notional"))

        ttk.Label(f, text="Share rounding:").grid(
            row=3, column=0, sticky="w", pady=(6, 0))
        rounding_label = next(
            (lbl for r, lbl in _ROUNDING_CHOICES
             if r == self._draft.sizing.share_rounding),
            "Round down",
        )
        self._sizing_rounding_var = tk.StringVar(value=rounding_label)
        cb_r = ttk.Combobox(
            f, textvariable=self._sizing_rounding_var, state="readonly",
            values=[lbl for _, lbl in _ROUNDING_CHOICES], width=14,
        )
        cb_r.grid(row=3, column=1, sticky="w", padx=(6, 0), pady=(6, 0))
        cb_r.bind("<<ComboboxSelected>>",
                  lambda _e: self._on_sizing_rounding_changed())

        self._err_label(f, "sizing").grid(
            row=4, column=0, columnspan=2, sticky="w")

        f.columnconfigure(1, weight=1)
        return f

    # ----- Exits multi-select -----

    def _build_exits_tab(self, parent: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(parent, padding=6)
        ttk.Label(
            f,
            text=("Pick which existing exit strategies to bind on fill. "
                  "If none picked, the user is prompted at fill time."),
            foreground=MUTED_GREY, wraplength=560, justify="left",
        ).pack(anchor="w", pady=(0, 6))

        # Scrollable holder for checkboxes (room for many libraries).
        holder = ttk.Frame(f)
        holder.pack(fill="both", expand=True)

        if not self._exit_strategies:
            ttk.Label(
                holder,
                text="(no exit strategies in library)",
                foreground="#888",
            ).pack(anchor="w")
        else:
            current = set(self._draft.on_fill_exit_ids)
            for s in self._exit_strategies:
                v = tk.BooleanVar(value=(s.id in current))
                self._exit_id_vars[s.id] = v
                ttk.Checkbutton(
                    holder,
                    text=f"{s.name or s.id[:6]}  (id={s.id[:8]})",
                    variable=v,
                    command=self._on_exit_ids_changed,
                ).pack(anchor="w")
        return f

    # ----- Lifecycle -----

    def _build_lifecycle_tab(self, parent: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(parent, padding=6)

        rows = [
            ("Cooldown (s):",                    "cooldown_secs",
             str(self._draft.cooldown_secs)),
            ("Max fires per session per symbol:", "max_fires_per_session_per_symbol",
             str(self._draft.max_fires_per_session_per_symbol)),
            ("Max fires per session total:",     "max_fires_per_session_total",
             "" if self._draft.max_fires_per_session_total is None
             else str(self._draft.max_fires_per_session_total)),
            ("Arm window start (HH:MM):",        "arm_window_start",
             self._draft.arm_window_start),
            ("Arm window end (HH:MM):",          "arm_window_end",
             self._draft.arm_window_end),
        ]
        self._lifecycle_vars: dict[str, tk.StringVar] = {}
        for r, (label, key, value) in enumerate(rows):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=2)
            v = tk.StringVar(value=value)
            self._lifecycle_vars[key] = v
            ttk.Entry(f, textvariable=v, width=14).grid(
                row=r, column=1, sticky="w", padx=(6, 0), pady=2)
            v.trace_add("write", lambda *_, k=key: self._on_lifecycle_changed(k))

        # Position-already-open policy
        ttk.Label(
            f, text="Position already open policy:",
        ).grid(row=len(rows), column=0, sticky="w", pady=(6, 0))
        policy_label = next(
            (lbl for p, lbl in _POLICY_CHOICES
             if p == self._draft.position_already_open_policy),
            "Block",
        )
        self._policy_var = tk.StringVar(value=policy_label)
        cb = ttk.Combobox(
            f, textvariable=self._policy_var, state="readonly",
            values=[lbl for _, lbl in _POLICY_CHOICES], width=14,
        )
        cb.grid(row=len(rows), column=1, sticky="w", padx=(6, 0), pady=(6, 0))
        cb.bind("<<ComboboxSelected>>", lambda _e: self._on_policy_changed())

        self._require_market_open_var = tk.BooleanVar(
            value=self._draft.require_market_open)
        ttk.Checkbutton(
            f, text="Require market open",
            variable=self._require_market_open_var,
            command=self._on_require_market_open_changed,
        ).grid(row=len(rows) + 1, column=1, sticky="w", pady=(6, 0))

        self._err_label(f, "lifecycle").grid(
            row=len(rows) + 2, column=0, columnspan=2, sticky="w")

        f.columnconfigure(1, weight=1)
        return f

    # ------------------------------------------------------------------
    # Per-field change handlers
    # ------------------------------------------------------------------

    def _on_name_changed(self) -> None:
        self._draft.name = self._name_var.get()

    def _on_direction_changed(self) -> None:
        try:
            self._draft.direction = Direction(self._direction_var.get())
        except ValueError:
            pass

    def _on_enabled_changed(self) -> None:
        self._draft.enabled = bool(self._enabled_var.get())

    def _on_universe_radio_changed(self) -> None:
        # Reset universe to a single-mode aggregate to enforce XOR at the
        # constructor level, then re-render the per-mode entry widgets.
        mode = self._universe_radio_var.get()
        if mode == "from_attached_chart":
            self._draft.universe = Universe(from_attached_chart=True)
        elif mode == "scanner_id":
            self._draft.universe = Universe(scanner_id="")
        else:
            self._draft.universe = Universe(symbols=())
        self._render_universe_params()
        self._protect_combobox_wheel()

    def _on_universe_field_changed(self) -> None:
        mode = self._universe_radio_var.get()
        if mode == "symbols":
            raw = self._universe_vars.get("symbols")
            if raw is None:
                return
            symbols = tuple(
                s.strip().upper() for s in raw.get().split(",")
                if s.strip()
            )
            self._draft.universe = Universe(symbols=symbols)
        elif mode == "scanner_id":
            v = self._universe_vars.get("scanner_id")
            if v is None:
                return
            self._draft.universe = Universe(scanner_id=v.get().strip() or None)

    def _on_trigger_kind_changed(self) -> None:
        new = _TRIGGER_KIND_BY_LABEL.get(self._trigger_kind_var.get())
        if new is None:
            return
        self._draft.trigger.kind = new
        # Reset interval to a sensible default for INDICATOR.
        if new == TriggerKind.INDICATOR and not self._draft.trigger.interval:
            self._draft.trigger.interval = "1m"
        self._render_trigger_params()
        self._protect_combobox_wheel()

    def _protect_combobox_wheel(self) -> None:
        """Re-apply the Combobox/Spinbox wheel-guard across the dialog.

        Idempotent. Called after the initial build and after any
        dynamic widget rebuild (trigger-kind change, universe-radio
        change, BlockEditor op/kind changes) so newly-created
        comboboxes are guarded too.
        """
        target = getattr(self, "_form_canvas", None)
        try:
            protect_combobox_wheel(self, scroll_target=target)
        except tk.TclError:
            pass

    def _on_trigger_label_changed(self) -> None:
        self._draft.trigger.label = self._trigger_label_var.get()

    def _on_trigger_price_changed(self, attr: str) -> None:
        v = self._trigger_param_vars.get(attr)
        if v is None:
            return
        raw = str(v.get()).strip()
        if not raw:
            setattr(self._draft.trigger, attr, None)
            return
        try:
            setattr(self._draft.trigger, attr, float(raw))
        except (ValueError, TypeError):
            pass

    def _on_trigger_interval_changed(self) -> None:
        v = self._trigger_param_vars.get("interval")
        if v is None:
            return
        self._draft.trigger.interval = str(v.get()) or None
        if self._block_editor is not None:
            try:
                self._block_editor.set_default_interval(
                    self._draft.trigger.interval or "1m")
            except Exception:  # noqa: BLE001
                pass

    def _on_intrabar_changed(self) -> None:
        self._draft.trigger.evaluate_intrabar = bool(self._intrabar_var.get())

    def _on_block_editor_changed(self) -> None:
        if self._block_editor is None:
            return
        try:
            self._draft.trigger.condition = self._block_editor.get_root()
        except Exception:  # noqa: BLE001
            logger.exception("EntriesDialog: BlockEditor.get_root raised")
        # Op changes rebuild the per-op params row with fresh comboboxes;
        # re-apply the wheel-guard so they don't fall through unprotected.
        self._protect_combobox_wheel()

    def _on_trigger_scanner_id_changed(self) -> None:
        v = self._trigger_param_vars.get("scanner_id")
        if v is None:
            return
        self._draft.trigger.scanner_id = str(v.get()).strip() or None

    def _on_sizing_kind_changed(self) -> None:
        new = _SIZING_KIND_BY_LABEL.get(self._sizing_kind_var.get())
        if new is None:
            return
        self._draft.sizing.kind = new

    def _on_sizing_field_changed(self, attr: str) -> None:
        var = (
            self._sizing_qty_var if attr == "qty"
            else self._sizing_notional_var
        )
        raw = str(var.get()).strip()
        if not raw:
            setattr(self._draft.sizing, attr, None)
            return
        try:
            setattr(self._draft.sizing, attr, float(raw))
        except (ValueError, TypeError):
            pass

    def _on_sizing_rounding_changed(self) -> None:
        r = _ROUNDING_BY_LABEL.get(self._sizing_rounding_var.get())
        if r is not None:
            self._draft.sizing.share_rounding = r

    def _on_exit_ids_changed(self) -> None:
        self._draft.on_fill_exit_ids = self.exit_strategy_ids_selected

    def _on_lifecycle_changed(self, key: str) -> None:
        v = self._lifecycle_vars.get(key)
        if v is None:
            return
        raw = str(v.get()).strip()
        if key in ("cooldown_secs", "max_fires_per_session_per_symbol"):
            if not raw:
                # Empty -> 0 (cooldown) / 1 (max-per-symbol; keeping
                # at-least-1 invariant nice).
                setattr(self._draft, key, 0 if key == "cooldown_secs" else 1)
                return
            try:
                setattr(self._draft, key, int(float(raw)))
            except (ValueError, TypeError):
                pass
        elif key == "max_fires_per_session_total":
            if not raw:
                self._draft.max_fires_per_session_total = None
                return
            try:
                self._draft.max_fires_per_session_total = int(float(raw))
            except (ValueError, TypeError):
                pass
        elif key in ("arm_window_start", "arm_window_end"):
            setattr(self._draft, key, raw)

    def _on_policy_changed(self) -> None:
        p = _POLICY_BY_LABEL.get(self._policy_var.get())
        if p is not None:
            self._draft.position_already_open_policy = p

    def _on_require_market_open_changed(self) -> None:
        self._draft.require_market_open = bool(
            self._require_market_open_var.get())

    # ------------------------------------------------------------------
    # Initial widget population
    # ------------------------------------------------------------------

    def _load_into_widgets(self) -> None:
        """Re-render the trigger + universe panels for the loaded draft."""
        self._render_universe_params()
        self._render_trigger_params()

    # ------------------------------------------------------------------
    # Validate / Save / Cancel
    # ------------------------------------------------------------------

    def _on_validate(self) -> list[str]:
        errors = list(validate_strategy(self._draft))
        self._render_inline_errors(errors)
        if errors:
            self._status_var.set("Errors: " + "; ".join(errors[:3]))
        else:
            self._status_var.set("Valid ✓")
        return errors

    def _render_inline_errors(self, errors: list[str]) -> None:
        # Map error tokens to the nearest field-error label.
        for var in self._field_errors.values():
            var.set("")
        for err in errors:
            low = err.lower()
            key = "lifecycle"
            if "name" in low:
                key = "name"
            elif "universe" in low or "symbols" in low or "scanner_id" in low:
                key = "universe"
            elif "trigger" in low or "limit" in low or "stop" in low or \
                    "indicator" in low or "scanner_alert" in low or \
                    "market" in low:
                key = "trigger"
            elif "sizing" in low or "qty" in low or "notional" in low:
                key = "sizing"
            elif "cooldown" in low or "max_fires" in low or "arm_window" in low:
                key = "lifecycle"
            v = self._field_errors.get(key)
            if v is not None:
                cur = v.get()
                if cur:
                    v.set(cur + "; " + err)
                else:
                    v.set(err)

    def _on_save_clicked(self, *, close: bool) -> None:
        errors = list(validate_strategy(self._draft))
        self._render_inline_errors(errors)
        if errors:
            self._status_var.set("Save refused — " + "; ".join(errors[:3]))
            return
        # Bump CreatedWith.template flag off if the user is editing a
        # template (saving turns it into a regular strategy).
        if self._draft.created_with.template and self._is_new is False:
            # Editing a template-derived strategy is fine; flag stays.
            pass
        if self._on_save is not None:
            try:
                self._on_save(self._draft)
            except Exception:  # noqa: BLE001
                logger.exception("EntriesDialog: on_save raised")
                self._status_var.set("Save callback raised — see log")
                return
        self._status_var.set(f"Saved {self._draft.name!r}")
        if close:
            self.destroy()

    def _on_cancel_clicked(self) -> None:
        if self._on_cancel is not None:
            try:
                self._on_cancel()
            except Exception:  # noqa: BLE001
                logger.exception("EntriesDialog: on_cancel raised")
        self.destroy()
