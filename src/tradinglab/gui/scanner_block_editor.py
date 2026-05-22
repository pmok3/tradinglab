"""Scanner block editor: recursive AND/OR widget for authoring scan trees.

Three nested Tk widgets:

- :class:`BlockEditor` — top-level container that hosts a single root
  :class:`Group`. ``set_root`` / ``get_root`` swap the tree;
  ``on_change`` fires after every user edit so the parent dialog can
  persist or live-evaluate.
- :class:`_GroupFrame` — header (combinator combo + enabled + delete)
  plus a children area plus two ``Add`` buttons.
- :class:`_ConditionFrame` — left :class:`_FieldRefPicker` + operator
  combo + per-operator named-params row + interval combo + enabled +
  delete.

Plus a leaf widget:

- :class:`_FieldRefPicker` — type combo (Number / Builtin / Indicator)
  driving a contextual value widget, with the indicator branch laying
  out one widget per ``ParamDef`` in the indicator's ``params_schema``
  and an output-key combo for multi-output indicators (Bollinger,
  ADX, SMI).

The editor mutates the tree in place and fires ``on_change`` after
each commit. It does **not** persist; the parent (Scanner tab dialog)
owns saving.

Validation
----------

- Param widgets coerce their string contents to ``int`` / ``float``
  using ``ParamDef.kind``; on ParseError the prior value is kept and a
  status string is set on the row (no popup — keep editing flow
  smooth).
- Operator changes preserve the prior left field; ``params`` is reset
  to the new operator's schema with sensible defaults.
- Field-type changes preserve the user's typed numeric value when
  switching Number ↔ Builtin (so accidentally clicking a different
  type doesn't lose work).

Stable IDs
----------

Conditions and Groups created here use the model's ``_new_id`` UUID4
factory, so every widget has a stable identity for the
:class:`scanner.runner.MatchHistory` and Treeview row keys. The
editor never re-keys an existing node.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from ..indicators.base import factory_by_kind_id
from ..scanner.fields import all_fields, get_field
from ..scanner.model import (
    ALL_OPERATORS,
    FIELD_KIND_BUILTIN,
    FIELD_KIND_INDICATOR,
    FIELD_KIND_LITERAL,
    OP_CROSSES_ABOVE,
    OP_CROSSES_BELOW,
    OP_INSIDE_BAR,
    OP_NR7,
    OP_OUTSIDE_BAR,
    OPERATOR_PARAM_SCHEMA,
    WITHIN_LAST_MODE_ALL,
    WITHIN_LAST_MODE_ANY,
    WITHIN_LAST_MODE_EXACTLY,
    Condition,
    FieldRef,
    Group,
)

LOG = logging.getLogger(__name__)


# Interval picker values — kept in sync with the rest of the toolbar.
_INTERVALS: Tuple[str, ...] = ("1m", "2m", "5m", "15m", "30m", "1h", "1d", "1wk", "1mo")

#: Operators with no left-field semantic (purely structural). The left
#: field is still required by the model (UI surfaces a fixed
#: builtin('close') sentinel) but is hidden from the editor to avoid
#: confusing the user.
_NO_LEFT_OPS = frozenset({OP_INSIDE_BAR, OP_OUTSIDE_BAR, OP_NR7})


# ---------------------------------------------------------------------------
# Adaptive flow layout helper
# ---------------------------------------------------------------------------


def _compute_flow_rows(
    widths: List[int],
    budget: int,
    *,
    pad: int = 6,
) -> List[Tuple[int, int]]:
    """Return ``[(row, col), ...]`` placements for a flow / wrap layout.

    Greedy first-fit: place each child on the current row until adding
    the next exceeds ``budget``, then wrap to a new row. The first
    child on every row is always placed regardless of width (so a
    single oversize widget still gets a row of its own rather than
    being silently dropped).

    Pure function — does NOT touch any Tk widget. Called by
    :meth:`_FieldRefPicker._reflow_value_pane` once it has measured
    each child's required width. Extracted so the algorithm can be
    unit-tested without depending on window-manager realization.

    Args:
        widths: Required width (px) of each child, in left-to-right
            visual order.
        budget: Max usable width (px) per row. Must be > 0.
        pad: Per-child horizontal padding allowance (px), counted
            against the running width.

    Returns:
        A list of ``(row, col)`` tuples, one per input width, in the
        same order as ``widths``. ``row`` and ``col`` are 0-indexed.
    """
    if budget <= 0:
        budget = 1
    out: List[Tuple[int, int]] = []
    row = 0
    col = 0
    used = 0
    for w in widths:
        cost = max(0, int(w)) + max(0, int(pad))
        if col > 0 and used + cost > budget:
            row += 1
            col = 0
            used = 0
        out.append((row, col))
        col += 1
        used += cost
    return out


# ---------------------------------------------------------------------------
# FieldRef picker
# ---------------------------------------------------------------------------


class _FieldRefPicker(ttk.Frame):
    """Composite widget producing a :class:`FieldRef`.

    Layout (one visual row, grid-managed):

        [Type ▾] [Value widget] [param row]? [Output ▾]?

    The picker is *self-driving*: once instantiated, it owns its
    internal :class:`FieldRef` and offers :meth:`get` / :meth:`set` to
    interrogate / replace it. Mutations fire ``on_change`` if given.
    """

    _TYPE_LABELS = {
        "Number": FIELD_KIND_LITERAL,
        "Builtin": FIELD_KIND_BUILTIN,
        "Indicator": FIELD_KIND_INDICATOR,
    }
    _TYPE_BY_KIND = {v: k for k, v in _TYPE_LABELS.items()}

    def __init__(
        self,
        master: tk.Misc,
        *,
        ref: Optional[FieldRef] = None,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(master)
        self._on_change = on_change
        self._ref: FieldRef = ref or FieldRef.builtin("close")
        # Cache: last numeric the user typed, restored when toggling
        # back to Number from another type.
        self._last_literal: float = 0.0

        # ----- type selector -------------------------------------------------
        self._type_var = tk.StringVar(value=self._TYPE_BY_KIND[self._ref.kind])
        self._type_combo = ttk.Combobox(
            self, textvariable=self._type_var,
            state="readonly", width=9,
            values=tuple(self._TYPE_LABELS.keys()),
        )
        self._type_combo.grid(row=0, column=0, padx=(0, 4))
        self._type_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_type_change())

        # ----- value widgets (built lazily by _rebuild_value_pane) -----------
        self._value_pane = ttk.Frame(self)
        self._value_pane.grid(row=0, column=1, sticky="nw")
        self._param_widgets: Dict[str, tk.Variable] = {}
        self._output_var = tk.StringVar()
        self._field_id_var = tk.StringVar()
        self._literal_var = tk.StringVar()

        # ----- adaptive flow layout state -----------------------------------
        # ``_flow_children`` is the ordered list of widgets that participate
        # in the indicator-branch flow layout (indicator combo, each param
        # wrap, optional output combo). Empty for non-indicator branches —
        # those use a single row=0 layout that doesn't need wrapping.
        self._flow_children: List[tk.Widget] = []
        # Pending after_id for the debounced reflow callback. Tracked so
        # ``_rebuild_value_pane`` can cancel before destroying children
        # (avoids the callback running against destroyed widgets).
        self._reflow_after_id: Optional[str] = None
        # Cache the Toplevel reference so ``_on_destroy`` can unbind even
        # if ``winfo_toplevel`` becomes unsafe by then.
        self._toplevel_for_reflow: Optional[tk.Misc] = None
        self._toplevel_bind_id: Optional[str] = None

        self._rebuild_value_pane()

        # Bind to the Toplevel's ``<Configure>`` so the layout adapts as
        # the dialog window is resized. The Toplevel's width is the
        # most stable signal: nothing in our chain (Notebook tab → scroll
        # canvas → BlockEditor → ConditionFrame → picker) sets an
        # explicit width, so each container's width is determined by
        # its content. Toplevel width breaks the feedback loop.
        try:
            top = self.winfo_toplevel()
        except tk.TclError:
            top = None
        if top is not None and top is not self:
            try:
                self._toplevel_for_reflow = top
                self._toplevel_bind_id = top.bind(
                    "<Configure>",
                    self._on_toplevel_configure,
                    add="+",
                )
            except tk.TclError:
                self._toplevel_for_reflow = None
                self._toplevel_bind_id = None
        self.bind("<Destroy>", self._on_destroy)

    # -- public API -----------------------------------------------------------

    def get(self) -> FieldRef:
        """Return the current :class:`FieldRef`. Always re-derives from widget state."""
        return self._collect()

    def set(self, ref: FieldRef) -> None:
        """Replace the current ref + rebuild widgets. No on_change fire."""
        self._ref = ref
        self._type_var.set(self._TYPE_BY_KIND[ref.kind])
        self._rebuild_value_pane()

    # -- internals ------------------------------------------------------------

    def _on_type_change(self) -> None:
        new_kind = self._TYPE_LABELS[self._type_var.get()]
        if new_kind == self._ref.kind:
            return
        if new_kind == FIELD_KIND_LITERAL:
            self._ref = FieldRef.literal(self._last_literal)
        elif new_kind == FIELD_KIND_BUILTIN:
            self._ref = FieldRef.builtin("close")
        else:
            # Pick the first registered indicator alphabetically (so
            # the default seed matches the user-visible dropdown
            # ordering — see the indicator combobox population below).
            ids = sorted(
                (s.id for s in all_fields() if s.kind == "indicator"),
                key=str.casefold,
            )
            self._ref = FieldRef.indicator(ids[0]) if ids else FieldRef.builtin("close")
        self._rebuild_value_pane()
        self._fire()

    def _rebuild_value_pane(self) -> None:
        # Cancel any pending reflow before destroying the widgets it
        # would target. Without this, a 50ms-delayed reflow can fire
        # against half-destroyed children and raise TclError.
        if self._reflow_after_id is not None:
            try:
                self.after_cancel(self._reflow_after_id)
            except tk.TclError:
                pass
            self._reflow_after_id = None
        for w in self._value_pane.winfo_children():
            try:
                w.destroy()
            except tk.TclError:
                pass
        self._param_widgets = {}
        self._flow_children = []
        kind = self._ref.kind
        if kind == FIELD_KIND_LITERAL:
            self._literal_var = tk.StringVar(
                value=_format_number(self._ref.value if self._ref.value is not None else 0.0)
            )
            entry = ttk.Entry(self._value_pane, textvariable=self._literal_var, width=10)
            entry.grid(row=0, column=0, padx=(0, 4))
            entry.bind("<FocusOut>", lambda _e: self._commit_literal())
            entry.bind("<Return>", lambda _e: self._commit_literal())
            return

        if kind == FIELD_KIND_BUILTIN:
            ids = [s.id for s in all_fields() if s.kind == "builtin"]
            self._field_id_var = tk.StringVar(
                value=self._ref.id if self._ref.id in ids else (ids[0] if ids else "close")
            )
            cb = ttk.Combobox(
                self._value_pane, textvariable=self._field_id_var,
                state="readonly", values=tuple(ids), width=18,
            )
            cb.grid(row=0, column=0, padx=(0, 4))
            cb.bind("<<ComboboxSelected>>", lambda _e: self._commit_builtin())
            return

        # FIELD_KIND_INDICATOR
        # Sort alphabetically (case-insensitive) so the dropdown is
        # browsable. Used by Scanner blocks, Exits dialog (indicator
        # triggers), and Entries dialog (indicator triggers).
        ids = sorted(
            (s.id for s in all_fields() if s.kind == "indicator"),
            key=str.casefold,
        )
        if not ids:
            ttk.Label(self._value_pane, text="(no indicators registered)").grid(row=0, column=0)
            return
        if self._ref.id not in ids:
            self._ref = FieldRef.indicator(ids[0])
        self._field_id_var = tk.StringVar(value=self._ref.id)
        ind_combo = ttk.Combobox(
            self._value_pane, textvariable=self._field_id_var,
            state="readonly", values=tuple(ids), width=14,
        )
        ind_combo.grid(row=0, column=0, padx=(0, 4))
        ind_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_indicator_change())
        self._flow_children.append(ind_combo)

        # Indicator params (length, etc.). For indicators with many
        # params (RVOL has 8) the params + indicator combo + output
        # combo can exceed any reasonable window width on a single
        # row, so they're laid out via the adaptive flow algorithm in
        # ``_reflow_value_pane`` (called via ``after_idle`` below and
        # on every Toplevel resize).
        spec = get_field(self._ref.id, kind="indicator")
        if spec is None:
            return
        for i, pdef in enumerate(spec.params_schema):
            wrap = self._build_param_widget(pdef, col=1 + i)
            if wrap is not None:
                self._flow_children.append(wrap)

        # Output-key combo (only if >1 output).
        if len(spec.output_keys) > 1:
            current = self._ref.output_key or spec.default_output_key
            self._output_var = tk.StringVar(value=current)
            out_combo = ttk.Combobox(
                self._value_pane, textvariable=self._output_var,
                state="readonly", values=tuple(spec.output_keys), width=8,
            )
            out_combo.grid(row=0, column=1 + len(spec.params_schema), padx=(4, 0))
            out_combo.bind("<<ComboboxSelected>>", lambda _e: self._commit_indicator())
            self._flow_children.append(out_combo)

        # Schedule the initial flow layout. ``after_idle`` runs after
        # Tk has had a chance to compute requested widths, so each
        # widget reports its true ``winfo_reqwidth`` rather than 1.
        try:
            self._reflow_after_id = self.after_idle(self._reflow_value_pane)
        except tk.TclError:
            self._reflow_after_id = None

    # -- value commit handlers ------------------------------------------------

    def _commit_literal(self) -> None:
        try:
            v = float(self._literal_var.get())
        except (TypeError, ValueError):
            # Revert displayed text to last-good.
            self._literal_var.set(_format_number(self._last_literal))
            return
        self._last_literal = v
        self._ref = FieldRef.literal(v)
        self._fire()

    def _commit_builtin(self) -> None:
        new_id = self._field_id_var.get()
        if new_id and new_id != self._ref.id:
            self._ref = FieldRef.builtin(new_id)
            self._fire()

    def _on_indicator_change(self) -> None:
        new_id = self._field_id_var.get()
        if new_id and new_id != self._ref.id:
            self._ref = FieldRef.indicator(new_id)
            self._rebuild_value_pane()
            self._fire()

    def _commit_indicator(self) -> None:
        params: Dict[str, Any] = {}
        spec = get_field(self._ref.id, kind="indicator")
        if spec is None:
            return
        for pdef in spec.params_schema:
            var = self._param_widgets.get(pdef.name)
            if var is None:
                continue
            try:
                raw = var.get()
            except tk.TclError:
                continue
            params[pdef.name] = _coerce_paramdef_value(pdef, raw)
        output_key = ""
        if len(spec.output_keys) > 1:
            try:
                output_key = self._output_var.get()
            except tk.TclError:
                pass
        self._ref = FieldRef.indicator(self._ref.id, params=params, output_key=output_key)
        self._fire()

    # -- helpers --------------------------------------------------------------

    def _build_param_widget(self, pdef: Any, *, col: int) -> Optional[ttk.Frame]:
        """Build one parameter wrap (label + widget); return the wrap.

        The wrap is gridded at ``(row=0, column=col)`` for the initial
        layout pass; ``_reflow_value_pane`` may regrid to a different
        ``(row, col)`` when the dialog is too narrow for one row.
        Returning the wrap lets the caller append it to
        ``self._flow_children`` for the flow-layout walk.
        """
        wrap = ttk.Frame(self._value_pane)
        wrap.grid(row=0, column=col, padx=(2, 0))
        ttk.Label(wrap, text=pdef.name + ":").pack(side="left")
        seed = (self._ref.params or {}).get(pdef.name, pdef.default)
        if pdef.kind == "bool":
            var = tk.BooleanVar(value=bool(seed))
            cb = ttk.Checkbutton(wrap, variable=var,
                                 command=self._commit_indicator)
            cb.pack(side="left")
        elif pdef.kind == "choice":
            var = tk.StringVar(value=str(seed))
            cb = ttk.Combobox(wrap, textvariable=var, state="readonly",
                              values=tuple(str(c) for c in pdef.choices), width=8)
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", lambda _e: self._commit_indicator())
        elif pdef.kind in ("int", "float"):
            var = tk.StringVar(value=_format_number(seed))
            kwargs: Dict[str, Any] = {"textvariable": var, "width": 6}
            kwargs["from_"] = pdef.min if pdef.min is not None else -1e12
            kwargs["to"]    = pdef.max if pdef.max is not None else  1e12
            kwargs["increment"] = pdef.step if pdef.step is not None \
                else (1 if pdef.kind == "int" else 0.1)
            sb = ttk.Spinbox(wrap, command=self._commit_indicator, **kwargs)
            sb.pack(side="left")
            sb.bind("<FocusOut>", lambda _e: self._commit_indicator())
            sb.bind("<Return>",   lambda _e: self._commit_indicator())
        else:
            var = tk.StringVar(value=str(seed))
            ent = ttk.Entry(wrap, textvariable=var, width=8)
            ent.pack(side="left")
            ent.bind("<FocusOut>", lambda _e: self._commit_indicator())
            ent.bind("<Return>",   lambda _e: self._commit_indicator())
        self._param_widgets[pdef.name] = var
        return wrap

    # -- adaptive flow layout ------------------------------------------------

    def _on_toplevel_configure(self, event: Optional[Any] = None) -> None:
        """Debounced ``<Configure>`` handler bound to the Toplevel.

        Filters out descendant configures (Tk's ``<Configure>`` only
        fires on the bound widget itself, so this is mostly defence in
        depth) and schedules a reflow for ~50ms later. Re-firing the
        scheduled callback within the window cancels the prior pending
        one so a continuous resize drag results in one final layout
        pass rather than dozens.
        """
        if self._toplevel_for_reflow is None:
            return
        # Defensive filter — only proceed if the event source IS the
        # toplevel we bound to (descendant Configure events should not
        # reach here under standard Tk binding semantics, but the
        # extra check costs nothing).
        if event is not None and getattr(event, "widget", None) is not self._toplevel_for_reflow:
            return
        if self._reflow_after_id is not None:
            try:
                self.after_cancel(self._reflow_after_id)
            except tk.TclError:
                pass
            self._reflow_after_id = None
        try:
            if not self.winfo_exists():
                return
            self._reflow_after_id = self.after(50, self._reflow_value_pane)
        except tk.TclError:
            pass

    def _reflow_value_pane(self) -> None:
        """Recompute and apply the flow layout for ``_flow_children``.

        Width budget is derived from the **Toplevel** width because
        nothing in our container chain has a fixed width — using a
        descendant container's width would create a feedback loop
        (regridding to wrap shrinks the descendant, which would
        report a smaller width on the next pass). The reservation
        accounts for the non-picker columns of the surrounding
        :class:`_ConditionFrame` (enabled checkbox, operator combo,
        params frame, interval combo, delete button, plus padding)
        and assumes the budget is split between two pickers when the
        right-hand side of the comparison is also field-typed.
        """
        self._reflow_after_id = None
        if not self._flow_children:
            return
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            top = self._toplevel_for_reflow or self.winfo_toplevel()
            win_w = top.winfo_width() if top is not None else 0
        except tk.TclError:
            return
        # Heuristic reservation. Empirically derived from the chrome
        # of :class:`_ConditionFrame` (≈30 enabled + 120 op + 70
        # interval + 30 delete + 30 dialog padding ≈ 280) plus a bit
        # for the picker's own type combo (~80 px) factored into the
        # split. The ``// 2`` accounts for two pickers competing for
        # row width when both sides of the comparison are fields.
        nonpicker_chrome_px = 280
        if win_w < 100:
            # Toplevel not yet realized — bail, will re-fire on the
            # first real Configure once the window has a real size.
            return
        available = max(220, win_w - nonpicker_chrome_px)
        budget = max(180, available // 2)

        # Measure each child's required width with a fresh idletasks
        # pass so spinbox/combobox widths are correct.
        widths: List[int] = []
        live_children: List[tk.Widget] = []
        for w in self._flow_children:
            try:
                if not w.winfo_exists():
                    continue
                w.update_idletasks()
                req = max(1, int(w.winfo_reqwidth()))
            except tk.TclError:
                continue
            widths.append(req)
            live_children.append(w)
        if not live_children:
            return
        placements = _compute_flow_rows(widths, budget=budget, pad=6)
        for w, (row, col) in zip(live_children, placements):
            try:
                w.grid_configure(
                    row=row, column=col,
                    padx=(2, 0),
                    pady=(0 if row == 0 else 2, 0),
                    sticky="nw",
                )
            except tk.TclError:
                pass

    def _on_destroy(self, _event: Optional[Any] = None) -> None:
        """Tear down pending callbacks + Toplevel binding on destroy.

        Without the unbind, the Toplevel keeps a reference to the
        bound method and would fire ``_on_toplevel_configure``
        against a destroyed picker on the next resize.
        """
        if self._reflow_after_id is not None:
            try:
                self.after_cancel(self._reflow_after_id)
            except tk.TclError:
                pass
            self._reflow_after_id = None
        if self._toplevel_for_reflow is not None and self._toplevel_bind_id:
            try:
                self._toplevel_for_reflow.unbind(
                    "<Configure>", self._toplevel_bind_id)
            except tk.TclError:
                pass
        self._toplevel_for_reflow = None
        self._toplevel_bind_id = None

    def _collect(self) -> FieldRef:
        return self._ref

    def _fire(self) -> None:
        if self._on_change:
            try:
                self._on_change()
            except Exception:  # noqa: BLE001
                LOG.exception("FieldRefPicker on_change raised")


# ---------------------------------------------------------------------------
# Condition frame
# ---------------------------------------------------------------------------


_TRANSITION_OPS_FOR_UI = frozenset({OP_CROSSES_ABOVE, OP_CROSSES_BELOW})

#: Mode dropdown options, in the order they appear in the UI.
_LOOKBACK_MODES_FULL: Tuple[str, ...] = (
    WITHIN_LAST_MODE_ANY,
    WITHIN_LAST_MODE_ALL,
    WITHIN_LAST_MODE_EXACTLY,
)
#: Mode options for transition operators: ``all`` is hidden because
#: "every bar in the window is a cross" is not a meaningful trader
#: pattern. ``exactly`` stays — "the cross fired exactly N bars ago"
#: IS meaningful.
_LOOKBACK_MODES_FOR_TRANSITION: Tuple[str, ...] = (
    WITHIN_LAST_MODE_ANY,
    WITHIN_LAST_MODE_EXACTLY,
)


class _LookbackCluster(ttk.Frame):
    """Inline ``[bars: N ▾mode]`` cluster for within-last-N-bars look-back.

    Mutates ``node.within_last_bars`` / ``node.within_last_mode`` in
    place and fires ``on_change``. Works for both :class:`Condition`
    and :class:`Group` since both carry the same two fields.

    Visual states:

    * ``within_last_bars == 0`` → muted (the look-back is dormant).
    * ``within_last_bars > 0`` → emphasized via accent foreground.

    For Condition nodes the parent calls :meth:`set_op` on operator
    changes so the mode dropdown can hide ``all`` when the op is a
    transition (``crosses_above`` / ``crosses_below``). Group nodes
    don't have an op of their own, so they always show the full mode
    list.
    """

    _MUTED_FG = "#888888"
    _ACTIVE_FG = "#1f4ea1"

    def __init__(
        self,
        master: tk.Misc,
        *,
        node: Union[Condition, Group],
        on_change: Optional[Callable[[], None]] = None,
        op: Optional[str] = None,
    ) -> None:
        super().__init__(master)
        self._node = node
        self._on_change = on_change
        self._current_op = op  # None for Group nodes

        self._label = ttk.Label(self, text="look back:", width=10)
        self._label.pack(side="left", padx=(0, 2))

        self._bars_var = tk.StringVar(value=str(int(node.within_last_bars)))
        self._bars_spin = ttk.Spinbox(
            self, from_=0, to=50, increment=1, width=4,
            textvariable=self._bars_var,
            command=self._on_bars_change,
        )
        self._bars_spin.pack(side="left", padx=(0, 2))
        self._bars_spin.bind("<FocusOut>", lambda _e: self._on_bars_change())
        self._bars_spin.bind("<Return>",   lambda _e: self._on_bars_change())

        self._mode_var = tk.StringVar(value=str(node.within_last_mode))
        self._mode_combo = ttk.Combobox(
            self, textvariable=self._mode_var, state="readonly",
            values=self._modes_for_op(op), width=8,
        )
        self._mode_combo.pack(side="left", padx=(0, 0))
        self._mode_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self._on_mode_change(),
        )

        self._update_emphasis()

    # -- public API -----------------------------------------------------------

    def set_op(self, op: str) -> None:
        """Update the cluster's operator context (Condition only).

        Re-binds the mode dropdown values; if the current mode is
        ``all`` and the new op is a transition, coerces it back to
        ``any`` to keep the UI consistent with the engine's hidden-
        ``all``-for-transitions invariant.
        """
        self._current_op = op
        new_values = self._modes_for_op(op)
        self._mode_combo.configure(values=new_values)
        if self._mode_var.get() not in new_values:
            self._mode_var.set(WITHIN_LAST_MODE_ANY)
            self._node.within_last_mode = WITHIN_LAST_MODE_ANY
            # Don't fire on_change here — caller (op-change handler)
            # already does after committing the op switch.

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _modes_for_op(op: Optional[str]) -> Tuple[str, ...]:
        if op in _TRANSITION_OPS_FOR_UI:
            return _LOOKBACK_MODES_FOR_TRANSITION
        return _LOOKBACK_MODES_FULL

    def _on_bars_change(self) -> None:
        try:
            n = int(float(self._bars_var.get()))
        except (TypeError, ValueError):
            n = self._node.within_last_bars
        n = max(0, min(50, n))
        # Keep the displayed string in sync after clamping.
        self._bars_var.set(str(n))
        if n != self._node.within_last_bars:
            self._node.within_last_bars = n
            self._update_emphasis()
            self._fire()
        else:
            self._update_emphasis()

    def _on_mode_change(self) -> None:
        v = self._mode_var.get()
        if v in _LOOKBACK_MODES_FULL and v != self._node.within_last_mode:
            self._node.within_last_mode = v
            self._fire()

    def _update_emphasis(self) -> None:
        active = self._node.within_last_bars > 0
        try:
            self._label.configure(
                foreground=self._ACTIVE_FG if active else self._MUTED_FG,
            )
        except tk.TclError:
            pass

    def _fire(self) -> None:
        if self._on_change:
            try:
                self._on_change()
            except Exception:  # noqa: BLE001
                LOG.exception("LookbackCluster on_change raised")


# ---------------------------------------------------------------------------
# Condition frame
# ---------------------------------------------------------------------------


class _ConditionFrame(ttk.Frame):
    """Render and edit one :class:`Condition` leaf."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        cond: Condition,
        on_change: Optional[Callable[[], None]] = None,
        on_delete: Optional[Callable[["_ConditionFrame"], None]] = None,
        default_interval: str = "5m",
    ) -> None:
        super().__init__(master, padding=(4, 2))
        self.cond = cond
        self._on_change = on_change
        self._on_delete = on_delete
        self._default_interval = default_interval

        self._build()

    # -- public API -----------------------------------------------------------

    def get(self) -> Condition:
        return self.cond

    # -- layout ---------------------------------------------------------------

    def _build(self) -> None:
        # Enabled checkbox.
        # NOTE: ``sticky="nw"`` on every cell keeps the chrome
        # (checkbox / op / params / interval / delete) anchored to
        # the top of row 0 even when the left ``_FieldRefPicker``
        # grows to multiple sub-rows via its adaptive flow layout.
        # Without it, Tk's default centring would visually float the
        # operator combo halfway down the picker on RVOL-with-many-
        # params conditions.
        self._enabled_var = tk.BooleanVar(value=self.cond.enabled)
        ttk.Checkbutton(self, variable=self._enabled_var,
                        command=self._on_enabled_toggle)\
            .grid(row=0, column=0, padx=(0, 4), sticky="nw")

        # Left field picker.
        self._left_picker = _FieldRefPicker(
            self, ref=self.cond.left, on_change=self._on_left_change,
        )
        self._left_picker.grid(row=0, column=1, padx=(0, 6), sticky="nw")

        # Operator dropdown.
        self._op_var = tk.StringVar(value=self.cond.op)
        self._op_combo = ttk.Combobox(
            self, textvariable=self._op_var, state="readonly",
            values=ALL_OPERATORS, width=14,
        )
        self._op_combo.grid(row=0, column=2, padx=(0, 6), sticky="nw")
        self._op_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_op_change())

        # Per-op named-params row.
        self._params_frame = ttk.Frame(self)
        self._params_frame.grid(row=0, column=3, padx=(0, 6), sticky="nw")
        self._param_widgets: Dict[str, Any] = {}
        self._build_params_row()

        # Look-back cluster: [bars: N ▾mode]. Sits between the per-op
        # params and the interval picker. Always visible; muted when
        # bars=0 so the screen real-estate is honest about feature
        # discoverability without screaming at users who don't use it.
        self._lookback = _LookbackCluster(
            self, node=self.cond, op=self.cond.op,
            on_change=self._fire,
        )
        self._lookback.grid(row=0, column=4, padx=(0, 6), sticky="nw")

        # Interval picker.
        self._interval_var = tk.StringVar(value=self.cond.interval or self._default_interval)
        ttk.Combobox(
            self, textvariable=self._interval_var, state="readonly",
            values=_INTERVALS, width=5,
        ).grid(row=0, column=5, padx=(0, 6), sticky="nw")
        self._interval_var.trace_add("write", lambda *_a: self._on_interval_change())

        # Delete button.
        ttk.Button(self, text="✕", width=3, command=self._do_delete)\
            .grid(row=0, column=6, padx=(0, 0), sticky="nw")

        # Conditional left visibility for structural ops.
        self._update_left_visibility()

    def _build_params_row(self) -> None:
        for w in self._params_frame.winfo_children():
            try:
                w.destroy()
            except tk.TclError:
                pass
        self._param_widgets = {}
        schema = OPERATOR_PARAM_SCHEMA.get(self.cond.op, ())
        for i, (name, kind) in enumerate(schema):
            wrap = ttk.Frame(self._params_frame)
            wrap.grid(row=0, column=i, padx=(0, 6))
            ttk.Label(wrap, text=name + ":").pack(side="left")
            current = self.cond.params.get(name)
            if kind == "field":
                ref = current if isinstance(current, FieldRef) else FieldRef.literal(0.0)
                picker = _FieldRefPicker(wrap, ref=ref,
                                         on_change=self._commit_params)
                picker.pack(side="left")
                self._param_widgets[name] = ("field", picker)
            else:
                # int / float
                seed = current if isinstance(current, (int, float)) else (
                    1 if kind == "int" else 1.0
                )
                var = tk.StringVar(value=_format_number(seed))
                kwargs: Dict[str, Any] = {
                    "textvariable": var, "width": 6,
                    "from_": -1e12, "to": 1e12,
                    "increment": 1 if kind == "int" else 0.1,
                }
                sb = ttk.Spinbox(wrap, command=self._commit_params, **kwargs)
                sb.pack(side="left")
                sb.bind("<FocusOut>", lambda _e: self._commit_params())
                sb.bind("<Return>",   lambda _e: self._commit_params())
                self._param_widgets[name] = (kind, var)

    def _update_left_visibility(self) -> None:
        """Hide left picker for purely structural ops (inside_bar / outside_bar / nr7)."""
        if self.cond.op in _NO_LEFT_OPS:
            self._left_picker.grid_remove()
        else:
            self._left_picker.grid()

    # -- commits --------------------------------------------------------------

    def _on_enabled_toggle(self) -> None:
        self.cond.enabled = bool(self._enabled_var.get())
        self._fire()

    def _on_left_change(self) -> None:
        self.cond.left = self._left_picker.get()
        self._fire()

    def _on_op_change(self) -> None:
        new_op = self._op_var.get()
        if new_op == self.cond.op or new_op not in OPERATOR_PARAM_SCHEMA:
            return
        # Build fresh params from the new schema's defaults.
        new_params: Dict[str, Any] = {}
        for name, kind in OPERATOR_PARAM_SCHEMA[new_op]:
            new_params[name] = (
                FieldRef.literal(0.0) if kind == "field" else
                (1 if kind == "int" else 1.0)
            )
        # Mutate the existing Condition in place so the parent Group's
        # children list (which holds the same object) sees the change.
        # __post_init__ only runs at construction time, so direct
        # attribute assignment is safe.
        self.cond.op = new_op
        self.cond.params = new_params
        self._build_params_row()
        self._update_left_visibility()
        # Notify the look-back cluster so it can refresh its mode list
        # (and coerce 'all' → 'any' if the new op is a transition).
        try:
            self._lookback.set_op(new_op)
        except (AttributeError, tk.TclError):
            # Cluster may not exist yet during early construction.
            pass
        self._fire()

    def _on_interval_change(self) -> None:
        v = self._interval_var.get()
        if v and v != self.cond.interval:
            self.cond.interval = v
            self._fire()

    def _commit_params(self) -> None:
        new_params: Dict[str, Any] = {}
        for name, (kind, widget) in self._param_widgets.items():
            if kind == "field":
                new_params[name] = widget.get()
            elif kind == "int":
                try:
                    new_params[name] = int(float(widget.get()))
                except (TypeError, ValueError):
                    new_params[name] = self.cond.params.get(name, 1)
            else:  # float
                try:
                    new_params[name] = float(widget.get())
                except (TypeError, ValueError):
                    new_params[name] = self.cond.params.get(name, 1.0)
        self.cond.params = new_params
        self._fire()

    def _do_delete(self) -> None:
        if self._on_delete:
            self._on_delete(self)

    def _fire(self) -> None:
        if self._on_change:
            try:
                self._on_change()
            except Exception:  # noqa: BLE001
                LOG.exception("ConditionFrame on_change raised")


# ---------------------------------------------------------------------------
# Group frame
# ---------------------------------------------------------------------------


class _GroupFrame(ttk.Frame):
    """Render and edit one :class:`Group` (recursive)."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        group: Group,
        on_change: Optional[Callable[[], None]] = None,
        on_delete: Optional[Callable[["_GroupFrame"], None]] = None,
        default_interval: str = "5m",
        is_root: bool = False,
    ) -> None:
        super().__init__(master, padding=(6, 4),
                         relief="solid", borderwidth=1)
        self.group = group
        self._on_change = on_change
        self._on_delete = on_delete
        self._default_interval = default_interval
        self._is_root = is_root
        self._child_frames: List[Union[_GroupFrame, _ConditionFrame]] = []

        self._build()

    # -- public API -----------------------------------------------------------

    def get(self) -> Group:
        # Children list is kept in sync as edits happen; just hand back.
        return self.group

    # -- layout ---------------------------------------------------------------

    def _build(self) -> None:
        # Header.
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 4))

        self._enabled_var = tk.BooleanVar(value=self.group.enabled)
        ttk.Checkbutton(header, variable=self._enabled_var,
                        command=self._on_enabled_toggle)\
            .pack(side="left", padx=(0, 4))

        self._combinator_var = tk.StringVar(value=self.group.combinator.upper())
        self._combinator_cb = ttk.Combobox(
            header, textvariable=self._combinator_var,
            state="readonly", values=("AND", "OR"), width=5,
        )
        self._combinator_cb.bind(
            "<<ComboboxSelected>>", lambda _e: self._on_combinator_change(),
        )

        self._add_condition_btn = ttk.Button(
            header, text="+ Condition", width=12, command=self._add_condition,
        )
        self._add_group_btn = ttk.Button(
            header, text="+ Group", width=10, command=self._add_group,
        )
        # Combinator goes before the add buttons when visible. Pack the
        # add buttons first so we have a stable anchor to pack the
        # combobox `before=` later.
        self._add_condition_btn.pack(side="left", padx=(0, 4))
        self._add_group_btn.pack(side="left", padx=(0, 4))
        self._update_combinator_visibility()

        # Group-level look-back cluster on the right side of the header.
        # Groups have no op of their own → always show the full mode
        # list (any/all/exactly). The cluster mutates ``self.group``
        # in place and fires the same on_change cascade as children.
        self._lookback = _LookbackCluster(
            header, node=self.group, op=None, on_change=self._fire,
        )
        self._lookback.pack(side="right", padx=(0, 8))

        if not self._is_root:
            ttk.Button(header, text="✕", width=3, command=self._do_delete)\
                .pack(side="right")

        # Children area.
        self._children_frame = ttk.Frame(self)
        self._children_frame.pack(fill="x", padx=(16, 0))
        self._render_children()

    def _update_combinator_visibility(self) -> None:
        """Show the AND/OR combobox only when the group has 2+ children.

        With 0 or 1 children the combinator is meaningless (nothing to
        combine), so hiding it removes UX noise — especially on the
        empty root group at first load.
        """
        cb = self._combinator_cb
        try:
            visible = bool(cb.winfo_manager())
        except tk.TclError:
            visible = False
        if len(self.group.children) >= 2:
            if not visible:
                cb.pack(side="left", padx=(0, 8),
                        before=self._add_condition_btn)
        else:
            if visible:
                cb.pack_forget()

    def _render_children(self) -> None:
        for w in self._children_frame.winfo_children():
            try:
                w.destroy()
            except tk.TclError:
                pass
        self._child_frames = []
        # Render-time sort: all conditions first, then all groups.
        # AND/OR are commutative within a group, so reordering is
        # semantically a no-op. This keeps simple atomic checks
        # together at the top instead of letting heavier nested-group
        # blocks visually orphan trailing conditions. Stable sort
        # preserves the user's relative ordering inside each bucket.
        ordered = sorted(
            self.group.children,
            key=lambda c: 0 if isinstance(c, Condition) else 1,
        )
        for child in ordered:
            if isinstance(child, Group):
                wf = _GroupFrame(
                    self._children_frame, group=child,
                    on_change=self._on_change,
                    on_delete=self._remove_child_widget,
                    default_interval=self._default_interval,
                )
            elif isinstance(child, Condition):
                wf = _ConditionFrame(
                    self._children_frame, cond=child,
                    on_change=self._on_change,
                    on_delete=self._remove_child_widget,
                    default_interval=self._default_interval,
                )
            else:
                continue
            wf.pack(fill="x", pady=(2, 2), anchor="w")
            self._child_frames.append(wf)

    # -- commits --------------------------------------------------------------

    def _on_enabled_toggle(self) -> None:
        self.group.enabled = bool(self._enabled_var.get())
        self._fire()

    def _on_combinator_change(self) -> None:
        v = self._combinator_var.get().lower()
        if v in ("and", "or") and v != self.group.combinator:
            self.group.combinator = v
            self._fire()

    def _add_condition(self) -> None:
        new = Condition(
            left=FieldRef.builtin("close"),
            op=">",
            params={"right": FieldRef.literal(0.0)},
            interval=self._default_interval,
        )
        # Insert after the last existing condition but before any
        # groups, so the persisted order matches the rendered order
        # (conditions first, groups last). _render_children also
        # sorts at display time, but doing it here keeps round-trip
        # save/load stable and predictable.
        insert_at = 0
        for i, c in enumerate(self.group.children):
            if isinstance(c, Condition):
                insert_at = i + 1
            else:
                break
        self.group.children.insert(insert_at, new)
        self._render_children()
        self._update_combinator_visibility()
        self._fire()

    def _add_group(self) -> None:
        new = Group(combinator="and", children=[])
        self.group.children.append(new)
        self._render_children()
        self._update_combinator_visibility()
        self._fire()

    def _remove_child_widget(self, widget: Union["_GroupFrame", "_ConditionFrame"]) -> None:
        target_id = (widget.group.id if isinstance(widget, _GroupFrame)
                     else widget.cond.id)
        self.group.children = [
            c for c in self.group.children
            if (getattr(c, "id", None) != target_id)
        ]
        self._render_children()
        self._update_combinator_visibility()
        self._fire()

    def _do_delete(self) -> None:
        if self._on_delete:
            self._on_delete(self)

    def _fire(self) -> None:
        if self._on_change:
            try:
                self._on_change()
            except Exception:  # noqa: BLE001
                LOG.exception("GroupFrame on_change raised")


# ---------------------------------------------------------------------------
# Top-level editor
# ---------------------------------------------------------------------------


class BlockEditor(ttk.Frame):
    """Top-level editor for a scan's root :class:`Group`.

    Use :meth:`set_root` to load a tree, :meth:`get_root` to read the
    current state. ``on_change`` fires after every user edit so the
    parent (Scanner tab dialog) can persist the scan and / or trigger
    a live re-evaluation.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        root: Optional[Group] = None,
        on_change: Optional[Callable[[], None]] = None,
        default_interval: str = "5m",
    ) -> None:
        super().__init__(master)
        self._on_change = on_change
        self._default_interval = default_interval
        self._root_group: Group = root or Group(combinator="and", children=[])
        self._root_frame: Optional[_GroupFrame] = None
        self._render_root()

    # -- public API -----------------------------------------------------------

    def get_root(self) -> Group:
        return self._root_group

    def set_root(self, group: Group) -> None:
        self._root_group = group
        self._render_root()

    def set_default_interval(self, interval: str) -> None:
        """Update the default interval used for newly-added Conditions."""
        self._default_interval = interval

    # -- internals ------------------------------------------------------------

    def _render_root(self) -> None:
        if self._root_frame is not None:
            try:
                self._root_frame.destroy()
            except tk.TclError:
                pass
            self._root_frame = None
        self._root_frame = _GroupFrame(
            self, group=self._root_group,
            on_change=self._on_change,
            on_delete=None,  # root cannot be deleted
            default_interval=self._default_interval,
            is_root=True,
        )
        self._root_frame.pack(fill="x", expand=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_number(v: Any) -> str:
    """Format a number for entry widgets without trailing zeros for ints."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f.is_integer():
        return str(int(f))
    return f"{f:g}"


def _coerce_paramdef_value(pdef: Any, raw: Any) -> Any:
    kind = getattr(pdef, "kind", "str")
    if kind == "int":
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return pdef.default
    if kind == "float":
        try:
            return float(raw)
        except (TypeError, ValueError):
            return pdef.default
    if kind == "bool":
        return bool(raw)
    if kind == "choice":
        return raw if raw in pdef.choices else pdef.default
    return raw


__all__ = ["BlockEditor"]
