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
import math
import tkinter as tk
from collections.abc import Callable, Mapping
from tkinter import ttk
from typing import Any

from ..indicators.base import ParamDef
from ..scanner.fields import all_fields, get_field
from ..scanner.model import (
    ALL_OPERATORS,
    FIELD_KIND_BUILTIN,
    FIELD_KIND_INDICATOR,
    FIELD_KIND_LITERAL,
    OP_BETWEEN,
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
from ._modal_base import (
    BaseModalDialog,
    make_scrollable_form,
    protect_combobox_wheel,
)
from ._param_widgets import (
    build_param_widget,
    combo_width_for_choices,
    label_text_for,
    param_group_for,
    spinbox_width_for,
    tooltip_text_for,
    validate_param_value,
)
from ._widget_metrics import (
    _CHAR_PX,
    _CHECKBOX_PX,
    _COMBO_OVERHEAD,
    _ENTRY_OVERHEAD,
    _FRAME_PAD_PX,
    _SPINBOX_OVERHEAD,
)
from .tooltip import ToolTip

LOG = logging.getLogger(__name__)

#: Circled-i glyph used as the hover-for-description info icon next to each
#: editable parameter in :class:`_FieldRefParamDialog`.
_INFO_ICON_GLYPH = "\u24d8"  # ⓘ


# Interval picker values — kept in sync with the rest of the toolbar.
_INTERVALS: tuple[str, ...] = ("1m", "2m", "5m", "15m", "30m", "1h", "1d", "1wk", "1mo")

#: Operators with no left-field semantic (purely structural). The left
#: field is still required by the model (UI surfaces a fixed
#: builtin('close') sentinel) but is hidden from the editor to avoid
#: confusing the user.
_NO_LEFT_OPS = frozenset({OP_INSIDE_BAR, OP_OUTSIDE_BAR, OP_NR7})


# ---------------------------------------------------------------------------
# Cross-symbol (FieldRef.symbol) UI plumbing
# ---------------------------------------------------------------------------

#: Sentinel value shown in the Symbol combo for "this ref evaluates
#: against the active symbol". A literal sentinel string keeps the
#: combo a single uniform widget (no special-case empty value); the
#: picker maps it back to ``ref.symbol = ""`` on commit. Visible
#: glyph keeps the dropdown self-explanatory at a glance.
_ACTIVE_SYMBOL_SENTINEL: str = "(active)"

#: Placeholder text shown in the ``@`` Entry when the user hasn't
#: pinned a cross-symbol. Visually grey (see
#: :meth:`_FieldRefPicker._symbol_placeholder_fg`). Empty entry ==
#: active symbol (no cross-symbol pin); typing any ticker overrides.
#: Cleared on FocusIn so the user can start typing immediately;
#: re-applied on FocusOut if the field is still empty. Alias of the
#: legacy ``_ACTIVE_SYMBOL_SENTINEL`` constant for back-compat with
#: existing test imports.
_SYMBOL_PLACEHOLDER: str = _ACTIVE_SYMBOL_SENTINEL

VIEW_AUTO = "Auto layout"
VIEW_COMPACT = "Compact rows"
VIEW_DETAILED = "Detailed cards"


# ---------------------------------------------------------------------------
# Adaptive flow layout helper
# ---------------------------------------------------------------------------


def _compute_flow_rows(
    widths: list[int],
    budget: int,
    *,
    pad: int = 6,
) -> list[tuple[int, int]]:
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
    out: list[tuple[int, int]] = []
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


def _filter_field_specs_for_query(specs: list[Any], query: str) -> tuple[str, ...]:
    """Return field ids matching ``query`` in id, label, or description."""
    needle = str(query or "").strip().casefold()
    if not needle:
        return tuple(str(s.id) for s in specs)
    tokens = [t for t in needle.split() if t]
    out: list[str] = []
    for spec in specs:
        haystack = " ".join((
            str(getattr(spec, "id", "")),
            str(getattr(spec, "label", "")),
            str(getattr(spec, "description", "")),
        )).casefold()
        if needle in haystack or (tokens and all(t in haystack for t in tokens)):
            out.append(str(spec.id))
    return tuple(out)


#: Min indicator-param count that flips :class:`_ConditionFrame` to
#: ``"stacked"`` layout. Picked at 3 so EMA / SMA / RSI / ATR / VWAP
#: / AVWAP / LRSI (all 1-2 params) stay on the simple inline row
#: while RVOL (6 trigger-relevant) / Bollinger (2) and friends with
#: multi-output get the bigger 3-row layout. See
#: :meth:`_ConditionFrame._classify_layout` for the full rule.
_COMPLEX_INDICATOR_PARAM_THRESHOLD: int = 3


#: Assumed character budget for a compact indicator summary token
#: (e.g. ``(20, vs=QQQ)``). Used by :func:`_estimate_picker_width` in
#: compact mode. Generous so the fit-based classifier doesn't
#: under-estimate a long token and over-pack the inline row.
_SUMMARY_TOKEN_EST_CHARS: int = 18


#: Empirically-derived font/widget metrics for the inline-width
#: estimator live in :mod:`._widget_metrics` so the indicator-dialog
#: param-wrap classifier can share the same calibration (avoiding the
#: previous "7-px-per-char heuristic duplicated in two places" hazard
#: called out in the generalisation audit). The ``_CHAR_PX`` /
#: ``_COMBO_OVERHEAD`` / etc names are imported at module scope above
#: so existing call sites and tests work unchanged.


def _estimate_pdef_width(pdef: Any) -> int:
    """Estimate pixel width of one ParamDef wrap (label + widget).

    Reads ``pdef.description or pdef.name`` for the label and
    ``pdef.kind`` for the widget shape. Pure function — no Tk calls.
    """
    label = (getattr(pdef, "description", "") or pdef.name) + ":"
    label_px = len(label) * _CHAR_PX + 4
    kind = pdef.kind
    if kind == "bool":
        return label_px + _CHECKBOX_PX
    if kind == "choice":
        # Combobox width=8 chars (default in _build_param_widget).
        return label_px + 8 * _CHAR_PX + _COMBO_OVERHEAD
    if kind in ("int", "float"):
        # Spinbox width=6 chars (default in _build_param_widget).
        return label_px + 6 * _CHAR_PX + _SPINBOX_OVERHEAD
    # Fallback Entry width=8.
    return label_px + 8 * _CHAR_PX + _ENTRY_OVERHEAD


def _estimate_picker_width(ref: FieldRef | None, *, compact: bool = False) -> int:
    """Estimate inline pixel width of a ``_FieldRefPicker`` for ``ref``.

    Sum of the type combo + value widgets (combo / spinbox / entry
    / param wraps) + optional output combo + optional Symbol cluster
    + per-gap padding. Pure function — no Tk calls.

    Drives :meth:`_ConditionFrame._classify_layout` for fit-based
    inline-vs-stacked selection. Falls back to a safe value for
    unknown indicator IDs (treats them as "wide enough to stack").

    When ``compact`` is True, indicator refs are estimated for the
    collapsed compact branch (type combo + indicator combo + a short
    summary token + an Edit button + symbol cluster) instead of the
    full per-parameter inline layout — matching the compact-mode
    pickers the :class:`_ConditionFrame` builds for indicator operands.
    """
    type_combo = 9 * _CHAR_PX + _COMBO_OVERHEAD  # picker type combo width=9
    if ref is None or ref.kind == FIELD_KIND_LITERAL:
        return type_combo + 10 * _CHAR_PX + _ENTRY_OVERHEAD + _FRAME_PAD_PX
    symbol_cluster = 0
    if ref.symbol:
        # "@" label + space + combo width=11
        symbol_cluster = (1 * _CHAR_PX + 2) + (11 * _CHAR_PX + _COMBO_OVERHEAD) + _FRAME_PAD_PX
    if ref.kind == FIELD_KIND_BUILTIN:
        builtin_combo = 18 * _CHAR_PX + _COMBO_OVERHEAD
        return type_combo + builtin_combo + symbol_cluster + _FRAME_PAD_PX
    # Indicator
    spec = get_field(ref.id, kind="indicator")
    if spec is None:
        # Unknown indicator — assume wide to be safe (forces stacked).
        return 9999
    if compact:
        # type combo + indicator combo + summary token + Edit button
        # + the always-present indicator symbol cluster + paddings.
        ind_combo = 14 * _CHAR_PX + _COMBO_OVERHEAD
        token = _SUMMARY_TOKEN_EST_CHARS * _CHAR_PX + _FRAME_PAD_PX
        edit_btn = 6 * _CHAR_PX + _ENTRY_OVERHEAD
        indicator_symbol_cluster = (
            (1 * _CHAR_PX + 2) + (11 * _CHAR_PX + _COMBO_OVERHEAD)
        )
        return (
            type_combo + ind_combo + token + edit_btn
            + indicator_symbol_cluster + 4 * _FRAME_PAD_PX
        )
    ind_combo = 14 * _CHAR_PX + _COMBO_OVERHEAD  # indicator combo width=14
    params_total = sum(_estimate_pdef_width(p) for p in spec.params_schema)
    output_combo = (
        8 * _CHAR_PX + _COMBO_OVERHEAD if len(spec.output_keys) > 1 else 0
    )
    # Indicator picker always has a Symbol cluster (defaults to active).
    indicator_symbol_cluster = (
        (1 * _CHAR_PX + 2) + (11 * _CHAR_PX + _COMBO_OVERHEAD)
    )
    n_widgets = 2 + len(spec.params_schema) + (
        1 if len(spec.output_keys) > 1 else 0
    ) + 1  # type combo, ind combo, params..., output?, symbol
    padding = (n_widgets - 1) * _FRAME_PAD_PX
    return (
        type_combo + ind_combo + params_total
        + output_combo + indicator_symbol_cluster + padding
    )


#: Estimated chrome width of a :class:`_ConditionFrame` rendered
#: inline (everything except the LEFT picker and any RHS field
#: pickers): enabled checkbox + op combo + lookback cluster +
#: interval combo + delete button + paddings. Used by
#: :func:`_estimate_condition_inline_width`.
_CONDITION_CHROME_PX: int = (
    22                                # enabled checkbox
    + (14 * _CHAR_PX + _COMBO_OVERHEAD)  # op combo width=14
    + 150                             # lookback cluster (rough)
    + (5 * _CHAR_PX + _COMBO_OVERHEAD)   # interval combo width=5
    + 30                              # delete button
    + 6 * _FRAME_PAD_PX               # 6 gaps between chrome widgets
)


def _estimate_scalar_param_width(name: str, kind: str) -> int:
    """Estimate width of a single scalar (int/float) op param wrap.

    Uses the *operator* param name (e.g. ``lookback``, ``n``,
    ``bars``, ``tolerance_pct``) since those don't have descriptions
    in :data:`OPERATOR_PARAM_SCHEMA` — only kinds.
    """
    label_px = (len(name) + 1) * _CHAR_PX + 4  # name + colon
    if kind in ("int", "float"):
        return label_px + 6 * _CHAR_PX + _SPINBOX_OVERHEAD
    return label_px + 8 * _CHAR_PX + _ENTRY_OVERHEAD


def _estimate_condition_inline_width(cond: Condition) -> int:
    """Estimate pixel width of a full inline-mode condition row.

    Sums chrome + LEFT picker (when not hidden by ``_NO_LEFT_OPS``)
    + all per-op field/scalar param widgets. Pure function — used by
    :meth:`_ConditionFrame._classify_layout` to decide whether the
    row can comfortably fit on the dialog's available width.
    """
    width = _CONDITION_CHROME_PX
    if cond.op not in _NO_LEFT_OPS:
        width += _estimate_picker_width(cond.left, compact=True) + _FRAME_PAD_PX
    schema = OPERATOR_PARAM_SCHEMA.get(cond.op, ())
    for name, kind in schema:
        if kind == "field":
            field_ref = cond.params.get(name)
            if isinstance(field_ref, FieldRef):
                # field-typed op params get a "name:" label prefix in
                # ``_build_params_row``; account for that label too.
                label_px = (len(name) + 1) * _CHAR_PX + 4
                width += label_px + _estimate_picker_width(field_ref, compact=True) + _FRAME_PAD_PX
        else:
            width += _estimate_scalar_param_width(name, kind) + _FRAME_PAD_PX
    return width


#: Hysteresis buffer for stacked → inline transition. When the
#: condition is currently stacked, we only flip back to inline if
#: ``estimated_inline_width < available_width - _HYSTERESIS_PX`` so
#: a slow drag at the boundary doesn't cause continuous flipping.
_HYSTERESIS_PX: int = 80

#: Default assumed available width when the Toplevel hasn't been
#: realized yet (initial build before WM has mapped the window).
#: Picked to bracket typical TradingLab dialogs (entries/exits at
#: 1400 px, scanner at 1200 px, custom indicator's BlockEditor at
#: ~760 px). With 1200 px assumed: simple `close > 100` (~900 est)
#: classifies as inline; RVOL (~1900 est) as stacked. The first
#: real ``<Configure>`` after the window is mapped triggers a
#: reclassification against the actual width.
_DEFAULT_DIALOG_WIDTH_PX: int = 1200


def _picker_ref_is_complex(ref: FieldRef | None) -> bool:
    """DEPRECATED: kept only for backward compatibility with existing
    callers (tests). The classification is now fit-based —
    :meth:`_ConditionFrame._classify_layout` calls
    :func:`_estimate_condition_inline_width` and compares to the
    available dialog width. Returns True iff the picker would need
    multiple lines to render itself OR has a cross-symbol pin —
    effectively the same signal as before but for legacy uses only.
    """
    if ref is None:
        return False
    if ref.symbol:
        return True
    if ref.kind != FIELD_KIND_INDICATOR:
        return False
    spec = get_field(ref.id, kind="indicator")
    if spec is None:
        return False
    return len(spec.params_schema) >= 3 or len(spec.output_keys) > 1


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

    Adaptive flow layout
    --------------------
    The indicator branch's children (indicator combo + param wraps +
    optional output combo + optional Symbol cluster) live in
    ``self._flow_children`` and are re-gridded by
    :meth:`_reflow_value_pane` on Toplevel resize. The width budget
    splits the row in half by default (``// 2``) because two sibling
    pickers — LEFT and RHS — usually compete for the row inside a
    :class:`_ConditionFrame`. When the parent flips to its stacked
    layout (because the picker IS complex enough that the parent
    decided to give it its own row), the parent calls
    :meth:`set_layout_hint` with ``"stacked"`` and the budget skips
    the ``// 2`` split — see :meth:`_reflow_value_pane`.
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
        ref: FieldRef | None = None,
        on_change: Callable[[], None] | None = None,
        layout_hint: str = "inline",
        display_mode: str = "detailed",
        data_status_provider: Callable[[FieldRef], tuple[bool, str]] | None = None,
    ) -> None:
        super().__init__(master)
        self._on_change = on_change
        self._ref: FieldRef = ref or FieldRef.builtin("close")
        self._data_status_provider = data_status_provider
        # ``display_mode`` is "detailed" (default — every indicator
        # param gets its own inline widget, flow-wrapped) or "compact"
        # (indicator params collapse to a one-line summary token plus
        # an "Edit…" button opening :class:`_FieldRefParamDialog`).
        # Compact mode keeps parameter-heavy indicators (RRVOL, BBANDS,
        # SMI) from clipping off-screen on a narrow dialog. Builtins and
        # literals render identically in both modes.
        self._display_mode: str = display_mode if display_mode in (
            "detailed", "compact") else "detailed"
        self._compact_summary_var = tk.StringVar(value="")
        # ``layout_hint`` is "inline" (default — picker shares its
        # row with a sibling RHS picker, so the flow budget halves)
        # or "stacked" (parent gave the picker its own row; flow
        # budget uses the full row width). Mutated by the parent
        # :class:`_ConditionFrame` via :meth:`set_layout_hint` when
        # it flips between inline and stacked.
        self._layout_hint: str = layout_hint if layout_hint in (
            "inline", "stacked") else "inline"
        # Cache: last numeric the user typed, restored when toggling
        # back to Number from another type.
        self._last_literal: float = 0.0
        # Cross-symbol entry state. ``_symbol_var`` holds either the
        # empty placeholder text or an uppercased user-typed ticker.
        # ``_symbol_is_placeholder`` tracks whether the var currently
        # shows the placeholder (so FocusIn knows to clear it).
        # Recreated per ``_rebuild_value_pane`` call.
        self._symbol_var: tk.StringVar = tk.StringVar(
            value=self._ref.symbol or _SYMBOL_PLACEHOLDER
        )
        self._symbol_is_placeholder: bool = not bool(self._ref.symbol)
        self._symbol_combo: ttk.Entry | None = None
        self._symbol_badge: ttk.Label | None = None
        self._validation_var = tk.StringVar(value="")
        self._validation_label: ttk.Label | None = None
        self._applicability_var = tk.StringVar(value="")
        self._applicability_label: ttk.Label | None = None
        self._badge_frame: ttk.Frame | None = None
        self._status_badges: list[ttk.Label] = []

        # ----- type selector -------------------------------------------------
        self._type_var = tk.StringVar(value=self._TYPE_BY_KIND[self._ref.kind])
        self._type_combo = ttk.Combobox(
            self, textvariable=self._type_var,
            state="readonly", width=9,
            values=tuple(self._TYPE_LABELS.keys()),
        )
        self._type_combo.grid(row=0, column=0, padx=(0, 4))
        self._type_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_type_change())
        self.bind("<Escape>", self._on_escape)

        # ----- value widgets (built lazily by _rebuild_value_pane) -----------
        self._value_pane = ttk.Frame(self)
        self._value_pane.grid(row=0, column=1, sticky="nw")
        self._param_widgets: dict[str, tk.Variable] = {}
        self._output_var = tk.StringVar()
        self._field_id_var = tk.StringVar()
        self._literal_var = tk.StringVar()
        self._indicator_combo: ttk.Combobox | None = None

        # ----- adaptive flow layout state -----------------------------------
        # ``_flow_children`` is the ordered list of widgets that participate
        # in the indicator-branch flow layout (indicator combo, each param
        # wrap, optional output combo). Empty for non-indicator branches —
        # those use a single row=0 layout that doesn't need wrapping.
        self._flow_children: list[tk.Widget] = []
        # Per-row container frames built by ``_reflow_value_pane`` for
        # the flow layout (one ttk.Frame per logical row). Recycled on
        # every reflow; cleaned up on ``_rebuild_value_pane`` /
        # ``_on_destroy``.
        self._flow_row_frames: list[tk.Widget] = []
        # Row assignment per ``_flow_children`` index from the last
        # applied layout. Row count alone is insufficient: a narrower
        # budget can keep the same number of rows but move widgets to
        # earlier/later rows, which otherwise leaves stale clipped rows.
        self._flow_row_for_index: list[int] = []
        self._flow_widths_cache: tuple[int, ...] | None = None
        self._flow_widths_cache_ids: tuple[int, ...] = ()
        self._tooltips: list[ToolTip] = []
        # Pending after_id for the debounced reflow callback. Tracked so
        # ``_rebuild_value_pane`` can cancel before destroying children
        # (avoids the callback running against destroyed widgets).
        self._reflow_after_id: str | None = None
        # Cache the Toplevel reference so ``_on_destroy`` can unbind even
        # if ``winfo_toplevel`` becomes unsafe by then.
        self._toplevel_for_reflow: tk.Misc | None = None
        self._toplevel_bind_id: str | None = None

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

    def set_layout_hint(self, hint: str) -> None:
        """Update the layout hint and re-flow if changed.

        Called by the parent :class:`_ConditionFrame` when it flips
        between inline and stacked layouts. The hint informs
        :meth:`_reflow_value_pane` whether to halve the budget (the
        picker shares a row with a sibling) or use the full available
        width (the picker owns its row).
        """
        if hint not in ("inline", "stacked") or hint == self._layout_hint:
            return
        self._layout_hint = hint
        if self._reflow_after_id is not None:
            try:
                self.after_cancel(self._reflow_after_id)
            except tk.TclError:
                pass
            self._reflow_after_id = None
        try:
            if self.winfo_exists() and self._flow_children:
                self._reflow_after_id = self.after_idle(self._reflow_value_pane)
        except tk.TclError:
            pass

    # -- internals ------------------------------------------------------------

    def _on_type_change(self) -> None:
        new_kind = self._TYPE_LABELS[self._type_var.get()]
        if new_kind == self._ref.kind:
            return
        # Preserve the user's cross-symbol pin and interval override across
        # Builtin<->Indicator toggles. Literal has no such slots, so both drop there.
        prev_symbol = self._ref.symbol
        prev_interval = self._ref.interval
        if new_kind == FIELD_KIND_LITERAL:
            self._ref = FieldRef.literal(self._last_literal)
        elif new_kind == FIELD_KIND_BUILTIN:
            self._ref = FieldRef.builtin(
                "close",
                symbol=prev_symbol,
                interval=prev_interval,
            )
        else:
            # Pick the first registered indicator alphabetically (so
            # the default seed matches the user-visible dropdown
            # ordering — see the indicator combobox population below).
            ids = sorted(
                (s.id for s in all_fields() if s.kind == "indicator"),
                key=str.casefold,
            )
            if ids:
                self._ref = FieldRef.indicator(
                    ids[0],
                    symbol=prev_symbol,
                    interval=prev_interval,
                )
            else:
                self._ref = FieldRef.builtin(
                    "close",
                    symbol=prev_symbol,
                    interval=prev_interval,
                )
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
        self._clear_tooltips()
        for w in self._value_pane.winfo_children():
            try:
                w.destroy()
            except tk.TclError:
                pass
        self._param_widgets = {}
        self._flow_children = []
        self._invalidate_flow_width_cache()
        # Row frames are children of value_pane and destroyed above,
        # but clear our handle list so we don't keep dead references.
        self._flow_row_frames = []
        self._flow_row_for_index = []
        self._symbol_combo = None
        self._symbol_badge = None
        self._validation_label = None
        self._indicator_combo = None
        self._badge_frame = None
        self._status_badges = []
        self._validation_var.set("")
        self._applicability_label = None
        self._applicability_var.set("")
        self._badge_frame = None
        self._status_badges = []
        # Re-seed the cross-symbol var from the ref each rebuild so the
        # entry shows the persisted value (e.g. after .set(ref)).
        self._symbol_var = tk.StringVar(
            value=self._ref.symbol or _SYMBOL_PLACEHOLDER
        )
        self._symbol_is_placeholder = not bool(self._ref.symbol)
        kind = self._ref.kind
        if kind == FIELD_KIND_LITERAL:
            self._literal_var = tk.StringVar(
                value=_format_number(self._ref.value if self._ref.value is not None else 0.0)
            )
            entry = ttk.Entry(self._value_pane, textvariable=self._literal_var, width=10)
            entry.grid(row=0, column=0, padx=(0, 4))
            entry.bind("<FocusOut>", lambda _e: self._commit_literal())
            entry.bind("<Return>", lambda _e: self._commit_literal())
            self._build_applicability_label(row_index=1)
            self._refresh_applicability()
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
            # Cross-symbol combo for Builtin branch: gridded at col=1
            # so layout of existing non-cross-symbol rows is unchanged.
            sym_wrap = self._build_symbol_combo(parent=self._value_pane)
            sym_wrap.grid(row=0, column=1, padx=(6, 0))
            self._build_applicability_label(row_index=1)
            self._refresh_applicability()
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
            self._ref = FieldRef.indicator(
                ids[0],
                symbol=self._ref.symbol,
                interval=self._ref.interval,
            )
        if self._display_mode == "compact":
            self._build_compact_indicator_branch(ids=ids)
            return
        # Build the indicator branch into a single row frame initially.
        # ``_reflow_value_pane`` may subsequently tear this down and
        # rebuild with multiple row frames if the dialog is too narrow.
        self._build_indicator_branch_into_rows(target_row_count=1)

        # Schedule the initial flow layout. ``after_idle`` runs after
        # Tk has had a chance to compute requested widths, so each
        # widget reports its true ``winfo_reqwidth`` rather than 1.
        try:
            self._reflow_after_id = self.after_idle(self._reflow_value_pane)
        except tk.TclError:
            self._reflow_after_id = None

    def _clear_tooltips(self) -> None:
        for tip in getattr(self, "_tooltips", []):
            try:
                tip.detach()
            except tk.TclError:
                pass
        self._tooltips = []

    def _build_indicator_branch_into_rows(self, *, target_row_count: int) -> None:
        """Build all flow children (ind combo, params, output, symbol)
        into ``target_row_count`` row Frames packed top-to-bottom
        inside ``self._value_pane``. ``target_row_count == 1`` packs
        every widget into a single row Frame; higher counts distribute
        widgets across multiple row Frames via the same flow algorithm
        used by ``_reflow_value_pane`` (so the first widget on each
        row is a left-edge anchor).

        This method is called from:
        * ``_rebuild_value_pane`` (indicator branch) with ``target_row_count=1``
        * ``_reflow_value_pane`` when the wrap layout needs more rows

        It tears down any existing ``_flow_children`` / ``_flow_row_frames``
        before building.
        """
        # Tear down everything inside the value pane.
        self._invalidate_flow_width_cache()
        for w in self._value_pane.winfo_children():
            try:
                w.destroy()
            except tk.TclError:
                pass
        self._param_widgets = {}
        self._flow_children = []
        self._flow_row_frames = []
        self._symbol_combo = None
        self._symbol_badge = None
        self._validation_label = None
        self._indicator_combo = None
        self._badge_frame = None
        self._status_badges = []
        # Re-seed cross-symbol var from ref.
        self._symbol_var = tk.StringVar(
            value=self._ref.symbol or _SYMBOL_PLACEHOLDER
        )
        self._symbol_is_placeholder = not bool(self._ref.symbol)

        ids = sorted(
            (s.id for s in all_fields() if s.kind == "indicator"),
            key=str.casefold,
        )
        spec = get_field(self._ref.id, kind="indicator")
        if spec is None:
            return

        # Create row frames.
        for ri in range(max(1, target_row_count)):
            rf = ttk.Frame(self._value_pane)
            rf.grid(
                row=ri, column=0, sticky="w",
                pady=(0 if ri == 0 else 2, 0),
            )
            self._flow_row_frames.append(rf)
        self._build_validation_label(row_index=max(1, target_row_count))

        # Build flow widgets first as a flat list (parented to
        # self._value_pane temporarily) so we can compute placements.
        # When target_row_count == 1, we know all go into row 0; skip
        # the placement math and just pack into row 0.
        if target_row_count <= 1:
            row0 = self._flow_row_frames[0]
            self._build_flat_indicator_widgets(parent=row0, ids=ids, spec=spec)
            for w in self._flow_children:
                try:
                    w.pack(side="left", padx=(0, 6), anchor="w")
                except tk.TclError:
                    pass
            self._flow_row_for_index = [0] * len(self._flow_children)
            return

        # Multi-row: build temporarily into row 0 to MEASURE widths,
        # then redistribute into row_frames according to placements.
        # We use a fresh hidden measurement frame to avoid disturbing
        # the visible layout during the measurement pass.
        measure_frame = ttk.Frame(self._value_pane)
        measure_frame.grid(row=0, column=1)  # off the visible flow
        try:
            self._build_flat_indicator_widgets(
                parent=measure_frame, ids=ids, spec=spec,
            )
            for w in self._flow_children:
                try:
                    w.pack(side="left")
                except tk.TclError:
                    pass
            self._value_pane.update_idletasks()
            widths = [max(1, int(w.winfo_reqwidth())) for w in self._flow_children]
        finally:
            measure_frame.destroy()

        # Reset and build for real into the row_frames.
        self._param_widgets = {}
        self._flow_children = []
        self._symbol_combo = None
        self._symbol_badge = None
        self._validation_label = None
        self._indicator_combo = None
        # Re-seed cross-symbol var again (the measure pass destroyed
        # the previous one along with the measure_frame children).
        self._symbol_var = tk.StringVar(
            value=self._ref.symbol or _SYMBOL_PLACEHOLDER
        )
        self._symbol_is_placeholder = not bool(self._ref.symbol)

        # Recompute placements based on width budget.
        budget = self._flow_budget_px()
        placements = _compute_flow_rows(widths, budget=budget, pad=6)

        # Group widget order indices by row.
        row_for_index: list[int] = [r for (r, _c) in placements]
        self._flow_row_for_index = list(row_for_index)

        # Now build for real, parenting each widget under its assigned
        # row_frame so packing is local.
        # We need to know in advance which row each widget goes to;
        # use a generator over self._build_flat_indicator_widgets that
        # respects the per-widget parent.
        self._build_indicator_widgets_into_rows(
            ids=ids, spec=spec, row_for_index=row_for_index,
        )
        self._flow_widths_cache = tuple(widths)
        self._flow_widths_cache_ids = self._flow_child_ids()

    # -- compact indicator branch -------------------------------------------

    def _build_compact_indicator_branch(self, *, ids: list[str]) -> None:
        """Render the indicator branch as a compact one-line token.

        Layout: ``[indicator combo ▾] [(param summary) label] [Edit…]
        [@ symbol]``. The per-parameter widgets are NOT built inline;
        instead they are edited in :class:`_FieldRefParamDialog` opened
        by the Edit button. This keeps parameter-heavy indicators from
        clipping off-screen on a narrow dialog (see CLAUDE.md §7.19).
        """
        spec = get_field(self._ref.id, kind="indicator")
        if spec is None:
            return
        row = ttk.Frame(self._value_pane)
        row.grid(row=0, column=0, sticky="w")

        self._field_id_var = tk.StringVar(value=self._ref.id)
        ind_combo = ttk.Combobox(
            row, textvariable=self._field_id_var,
            state="normal", values=tuple(ids), width=18,
        )
        ind_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_indicator_change())
        ind_combo.bind("<KeyRelease>", self._on_indicator_combo_keyrelease)
        ind_combo.bind("<Return>", lambda _e: self._on_indicator_change())
        ind_combo.bind("<FocusOut>", lambda _e: self._on_indicator_change())
        ind_combo.pack(side="left", padx=(0, 4))
        self._indicator_combo = ind_combo

        self._compact_summary_var = tk.StringVar(value=self._compact_summary_text(spec))
        summary_lbl = ttk.Label(
            row, textvariable=self._compact_summary_var, foreground="#555555",
        )
        summary_lbl.pack(side="left", padx=(0, 4))
        self._tooltips.append(ToolTip(
            summary_lbl,
            "Current parameters. Click Edit… to change them in a "
            "stacked dialog where every option is visible.",
        ))

        edit_btn = ttk.Button(
            row, text="Edit…", width=6, command=self._open_param_dialog,
        )
        edit_btn.pack(side="left", padx=(0, 6))

        sym_wrap = self._build_symbol_combo(parent=row)
        sym_wrap.pack(side="left")

        self._build_validation_label(row_index=1)

    def _compact_summary_text(self, spec: Any) -> str:
        """Build the compact param-summary token shown next to the combo.

        Appends ``· <output>`` for multi-output indicators so the user
        can see which output is selected without opening the dialog.
        """
        summary = _indicator_param_summary(spec, self._ref.params)
        if len(spec.output_keys) > 1:
            out = self._ref.output_key or spec.default_output_key
            if out:
                summary = f"{summary} · {out}" if summary else out
        return summary

    def _open_param_dialog(self) -> None:
        """Open the stacked Apply/Cancel parameter dialog for the ref."""
        try:
            top = self.winfo_toplevel()
        except tk.TclError:
            return
        dlg = _FieldRefParamDialog(top, ref=self._ref)
        try:
            self.wait_window(dlg)
        except tk.TclError:
            return
        if dlg.result is not None:
            self._ref = dlg.result
            self._rebuild_value_pane()
            self._fire()

    def _build_validation_label(self, *, row_index: int) -> None:
        """Create the inline validation label under indicator params."""
        label = ttk.Label(
            self._value_pane,
            textvariable=self._validation_var,
            foreground="#b42318",
        )
        label.grid(row=row_index, column=0, sticky="w", pady=(2, 0))
        self._validation_label = label
        self._build_badge_frame(row_index=row_index + 1)
        self._build_applicability_label(row_index=row_index + 2)
        self._refresh_applicability()

    def _build_badge_frame(self, *, row_index: int) -> None:
        frame = ttk.Frame(self._value_pane)
        frame.grid(row=row_index, column=0, sticky="w", pady=(2, 0))
        self._badge_frame = frame
        self._refresh_status_badges()

    def _build_applicability_label(self, *, row_index: int) -> None:
        """Create the text-only applicability line under picker controls."""
        if self._badge_frame is None:
            self._build_badge_frame(row_index=row_index)
            row_index += 1
        label = ttk.Label(
            self._value_pane,
            textvariable=self._applicability_var,
            foreground="#666666",
        )
        label.grid(row=row_index, column=0, sticky="w", pady=(2, 0))
        self._applicability_label = label

    def _refresh_applicability(self) -> None:
        self._applicability_var.set(
            _applicability_text_for_ref(self._ref, self._data_status_provider)
        )
        self._refresh_status_badges()

    def _build_flat_indicator_widgets(
        self, *, parent: tk.Misc, ids: list[str], spec: Any,
    ) -> None:
        """Create indicator-branch widgets as children of ``parent``,
        appending each to ``self._flow_children`` in left-to-right
        order: [ind_combo, *param_wraps, optional output_combo,
        symbol_combo].

        The caller is responsible for packing/gridding each created
        widget within ``parent`` (this method intentionally doesn't
        place them so we can reuse the build logic for both the
        single-row and multi-row layouts).
        """
        self._field_id_var = tk.StringVar(value=self._ref.id)
        ind_combo = ttk.Combobox(
            parent, textvariable=self._field_id_var,
            state="normal", values=tuple(ids), width=18,
        )
        ind_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_indicator_change())
        ind_combo.bind("<KeyRelease>", self._on_indicator_combo_keyrelease)
        ind_combo.bind("<Return>", lambda _e: self._on_indicator_change())
        ind_combo.bind("<FocusOut>", lambda _e: self._on_indicator_change())
        self._indicator_combo = ind_combo
        self._flow_children.append(ind_combo)

        last_group = ""
        for pdef in spec.params_schema:
            group = param_group_for(pdef)
            if group != last_group:
                header = self._build_param_group_header(parent=parent, text=group)
                self._flow_children.append(header)
                last_group = group
            wrap = self._build_param_widget(pdef, parent=parent)
            if wrap is not None:
                self._flow_children.append(wrap)

        if len(spec.output_keys) > 1:
            current = self._ref.output_key or spec.default_output_key
            self._output_var = tk.StringVar(value=current)
            out_combo = ttk.Combobox(
                parent, textvariable=self._output_var,
                state="readonly", values=tuple(spec.output_keys), width=8,
            )
            out_combo.bind("<<ComboboxSelected>>", lambda _e: self._commit_indicator())
            self._flow_children.append(out_combo)

        sym_wrap = self._build_symbol_combo(parent=parent)
        self._flow_children.append(sym_wrap)

    def _build_indicator_widgets_into_rows(
        self, *, ids: list[str], spec: Any, row_for_index: list[int],
    ) -> None:
        """Same as :meth:`_build_flat_indicator_widgets` but each
        widget is created as a child of ``self._flow_row_frames[row]``
        per the ``row_for_index`` list. Pack ``side="left"`` inside
        each row frame.
        """
        # Compute the widget order: [ind_combo, *params, output?, sym].
        # row_for_index has the same length and ordering.
        order: list[tuple[str, Any]] = [("ind_combo", None)]
        last_group = ""
        for pdef in spec.params_schema:
            group = param_group_for(pdef)
            if group != last_group:
                order.append(("group", group))
                last_group = group
            order.append(("param", pdef))
        if len(spec.output_keys) > 1:
            order.append(("output", spec))
        order.append(("symbol", None))

        if len(order) != len(row_for_index):
            # Mismatch — fall back to single row.
            row_for_index = [0] * len(order)

        for (kind, payload), row_idx in zip(order, row_for_index, strict=False):
            row_idx = max(0, min(row_idx, len(self._flow_row_frames) - 1))
            row_frame = self._flow_row_frames[row_idx]
            if kind == "ind_combo":
                self._field_id_var = tk.StringVar(value=self._ref.id)
                w = ttk.Combobox(
                    row_frame, textvariable=self._field_id_var,
                    state="normal", values=tuple(ids), width=18,
                )
                w.bind("<<ComboboxSelected>>", lambda _e: self._on_indicator_change())
                w.bind("<KeyRelease>", self._on_indicator_combo_keyrelease)
                w.bind("<Return>", lambda _e: self._on_indicator_change())
                w.bind("<FocusOut>", lambda _e: self._on_indicator_change())
                w.pack(side="left", padx=(0, 6), anchor="w")
                self._indicator_combo = w
                self._flow_children.append(w)
            elif kind == "param":
                wrap = self._build_param_widget(payload, parent=row_frame)
                if wrap is not None:
                    wrap.pack(side="left", padx=(0, 6), anchor="w")
                    self._flow_children.append(wrap)
            elif kind == "group":
                header = self._build_param_group_header(parent=row_frame, text=str(payload))
                header.pack(side="left", padx=(0, 6), anchor="w")
                self._flow_children.append(header)
            elif kind == "output":
                current = self._ref.output_key or payload.default_output_key
                self._output_var = tk.StringVar(value=current)
                w = ttk.Combobox(
                    row_frame, textvariable=self._output_var,
                    state="readonly", values=tuple(payload.output_keys), width=8,
                )
                w.bind("<<ComboboxSelected>>", lambda _e: self._commit_indicator())
                w.pack(side="left", padx=(0, 6), anchor="w")
                self._flow_children.append(w)
            else:  # symbol
                sym_wrap = self._build_symbol_combo(parent=row_frame)
                sym_wrap.pack(side="left", padx=(0, 6), anchor="w")
                self._flow_children.append(sym_wrap)

    @staticmethod
    def _build_param_group_header(*, parent: tk.Misc, text: str) -> ttk.Label:
        return ttk.Label(parent, text=text)

    def _rebuild_indicator_branch_into_rows(self, target_row_count: int) -> None:
        """Tear down + rebuild the indicator branch with the requested
        number of row frames. Called by :meth:`_reflow_value_pane`
        when the wrap row count changes.
        """
        self._build_indicator_branch_into_rows(target_row_count=target_row_count)

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
        sym = self._current_symbol_from_combo()
        if new_id and (new_id != self._ref.id or sym != self._ref.symbol):
            self._ref = FieldRef.builtin(new_id, symbol=sym, interval=self._ref.interval)
            self._refresh_applicability()
            self._fire()

    def _on_indicator_change(self) -> None:
        new_id = self._field_id_var.get()
        ids = self._indicator_ids()
        if new_id not in ids:
            matches = _filter_field_specs_for_query(self._indicator_specs(), new_id)
            if len(matches) == 1:
                new_id = matches[0]
                self._field_id_var.set(new_id)
            else:
                return
        if new_id and new_id != self._ref.id:
            self._ref = FieldRef.indicator(
                new_id,
                symbol=self._ref.symbol,
                interval=self._ref.interval,
            )
            self._rebuild_value_pane()
            self._fire()

    def _on_indicator_combo_keyrelease(self, _event: Any | None = None) -> None:
        combo = self._indicator_combo
        if combo is None:
            return
        query = self._field_id_var.get()
        values = _filter_field_specs_for_query(self._indicator_specs(), query)
        if not values:
            values = self._indicator_ids()
        try:
            combo.configure(values=values)
        except tk.TclError:
            pass

    def _on_escape(self, _event: Any | None = None) -> str:
        """Restore current displayed values and clear inline validation."""
        self._validation_var.set("")
        if self._ref.kind == FIELD_KIND_INDICATOR:
            self._field_id_var.set(self._ref.id)
            if self._indicator_combo is not None:
                try:
                    self._indicator_combo.configure(values=self._indicator_ids())
                except tk.TclError:
                    pass
        elif self._ref.kind == FIELD_KIND_BUILTIN:
            self._field_id_var.set(self._ref.id)
        elif self._ref.kind == FIELD_KIND_LITERAL:
            self._literal_var.set(_format_number(self._ref.value))
        return "break"

    @staticmethod
    def _indicator_specs() -> list[Any]:
        return sorted(
            (s for s in all_fields() if s.kind == "indicator"),
            key=lambda s: str(s.id).casefold(),
        )

    @classmethod
    def _indicator_ids(cls) -> tuple[str, ...]:
        return tuple(str(s.id) for s in cls._indicator_specs())

    def _commit_indicator(self) -> None:
        spec = get_field(self._ref.id, kind="indicator")
        if spec is None:
            return
        if self._display_mode == "compact":
            # Compact mode has no inline param widgets — preserve the
            # ref's existing params/output and only fold in the latest
            # cross-symbol pin (the one editable control on the row).
            sym = self._current_symbol_from_combo()
            self._ref = FieldRef.indicator(
                self._ref.id,
                params=dict(self._ref.params),
                output_key=self._ref.output_key,
                symbol=sym,
                interval=self._ref.interval,
            )
            self._refresh_applicability()
            self._fire()
            return
        params: dict[str, Any] = {}
        for pdef in spec.params_schema:
            var = self._param_widgets.get(pdef.name)
            if var is None:
                continue
            try:
                raw = var.get()
            except tk.TclError:
                continue
            ok, value, message = validate_param_value(pdef, raw)
            if not ok:
                self._validation_var.set(message)
                return
            params[pdef.name] = value
        self._validation_var.set("")
        output_key = ""
        if len(spec.output_keys) > 1:
            try:
                output_key = self._output_var.get()
            except tk.TclError:
                pass
        sym = self._current_symbol_from_combo()
        self._ref = FieldRef.indicator(
            self._ref.id,
            params=params,
            output_key=output_key,
            symbol=sym,
            interval=self._ref.interval,
        )
        self._refresh_applicability()
        self._fire()

    # -- cross-symbol entry --------------------------------------------------

    def _current_symbol_from_combo(self) -> str:
        """Read the current Symbol entry value, mapping placeholder → ``""``."""
        try:
            raw = self._symbol_var.get()
        except tk.TclError:
            return self._ref.symbol or ""
        s = (raw or "").strip().upper()
        if not s or s == _SYMBOL_PLACEHOLDER.upper():
            return ""
        return s

    def _build_symbol_combo(self, parent: tk.Misc | None = None) -> ttk.Frame:
        """Build the ``@ [ticker entry]`` cluster wrap and return it.

        Plain ``ttk.Entry`` with placeholder behavior — NO dropdown,
        NO history, NO suggestions. The user types ANY ticker on
        demand; that's the whole point of cross-symbol pinning. An
        empty entry means "use the active symbol" (no pin).

        Placeholder behavior:

        * Empty + unfocused → shows ``(active)`` in muted grey.
        * Click / FocusIn → placeholder clears, ready for typing.
        * FocusOut with empty content → placeholder re-displays;
          ref commits with ``symbol=""``.
        * Typed text → uppercased on commit (Return / FocusOut).

        Wrap is created as a child of ``parent`` (defaulting to
        ``self._value_pane``) and packed by the caller — this lets
        the flow-layout walker place it inside a per-row sub-frame.
        """
        parent_widget = parent if parent is not None else self._value_pane
        wrap = ttk.Frame(parent_widget)
        ttk.Label(wrap, text="@").pack(side="left", padx=(0, 2))
        entry = ttk.Entry(
            wrap, textvariable=self._symbol_var, width=11,
            foreground=self._symbol_placeholder_fg(),
        )
        entry.pack(side="left")
        # Seed initial display: real value if pinned, else placeholder.
        if not self._ref.symbol:
            self._symbol_var.set(_SYMBOL_PLACEHOLDER)
            self._symbol_is_placeholder = True
        else:
            self._symbol_var.set(self._ref.symbol)
            self._symbol_is_placeholder = False
            try:
                entry.configure(foreground=self._symbol_active_fg())
            except tk.TclError:
                pass
        entry.bind("<FocusIn>", self._on_symbol_focus_in)
        entry.bind("<FocusOut>", self._on_symbol_focus_out)
        entry.bind("<Return>", lambda _e: self._commit_symbol())
        self._symbol_combo = entry
        self._tooltips.append(ToolTip(
            entry,
            "Leave blank to use the active ticker. Type SPY, QQQ, etc. "
            "to compare another ticker at the same bar time.",
        ))
        self._refresh_symbol_badge()
        return wrap

    def _refresh_symbol_badge(self) -> None:
        """Show a text badge for cross-symbol refs."""
        symbol = str(self._ref.symbol or "").strip().upper()
        if self._symbol_badge is not None:
            try:
                self._symbol_badge.destroy()
            except tk.TclError:
                pass
            self._symbol_badge = None
        if not symbol or self._symbol_combo is None:
            return
        badge = ttk.Label(self._symbol_combo.master, text=f"@{symbol}")
        badge.pack(side="left", padx=(4, 0))
        self._symbol_badge = badge
        self._status_badges.append(badge)
        self._tooltips.append(ToolTip(
            badge,
            f"Evaluates this value on {symbol}.",
        ))
        self._refresh_status_badges()

    def _refresh_status_badges(self) -> None:
        frame = self._badge_frame
        symbol_badge = self._symbol_badge
        self._status_badges = []
        if symbol_badge is not None:
            self._status_badges.append(symbol_badge)
        if frame is None:
            return
        for child in list(frame.winfo_children()):
            try:
                child.destroy()
            except tk.TclError:
                pass

        for text, tip in _badges_for_ref(self._ref, self._data_status_provider):
            badge = ttk.Label(frame, text=text)
            badge.pack(side="left", padx=(0, 4))
            self._status_badges.append(badge)
            if tip:
                self._tooltips.append(ToolTip(badge, tip))

    @staticmethod
    def _symbol_placeholder_fg() -> str:
        """Grey foreground for the ``(active)`` placeholder text."""
        return "#888888"

    @staticmethod
    def _symbol_active_fg() -> str:
        """Normal foreground for a real ticker pin (theme-default-ish)."""
        return "black"

    def _on_symbol_focus_in(self, _event: Any | None = None) -> None:
        """Clear the placeholder when the user clicks into the entry."""
        if not self._symbol_is_placeholder:
            return
        try:
            self._symbol_var.set("")
            self._symbol_combo.configure(foreground=self._symbol_active_fg())
        except tk.TclError:
            return
        self._symbol_is_placeholder = False

    def _on_symbol_focus_out(self, _event: Any | None = None) -> None:
        """Restore the placeholder if the user leaves the entry empty.

        Also commits the ref (so a typed ticker is captured on tab-out).
        """
        self._commit_symbol()
        try:
            raw = (self._symbol_var.get() or "").strip()
        except tk.TclError:
            return
        if not raw:
            try:
                self._symbol_var.set(_SYMBOL_PLACEHOLDER)
                self._symbol_combo.configure(foreground=self._symbol_placeholder_fg())
            except tk.TclError:
                return
            self._symbol_is_placeholder = True

    def _commit_symbol(self) -> None:
        """Commit the typed ticker into the ref's ``symbol`` field."""
        new_sym = self._current_symbol_from_combo()
        if new_sym == (self._ref.symbol or ""):
            if new_sym:
                self._normalize_symbol_display(new_sym)
            return
        if new_sym:
            self._normalize_symbol_display(new_sym)
        # Rebuild the ref with the new symbol. The kind-specific
        # commit path also picks up the latest param/output state in
        # case the user changed both before tabbing out.
        if self._ref.kind == FIELD_KIND_INDICATOR:
            self._commit_indicator()
        elif self._ref.kind == FIELD_KIND_BUILTIN:
            self._commit_builtin()
        else:
            # Literal: shouldn't happen — symbol entry isn't shown.
            self._fire()
        self._refresh_symbol_badge()
        self._refresh_applicability()

    def _normalize_symbol_display(self, symbol: str) -> None:
        try:
            self._symbol_var.set(symbol)
            if self._symbol_combo is not None:
                self._symbol_combo.configure(foreground=self._symbol_active_fg())
            self._symbol_is_placeholder = False
        except tk.TclError:
            pass

    # -- helpers --------------------------------------------------------------

    def _build_param_widget(
        self,
        pdef: Any,
        *,
        parent: tk.Misc | None = None,
    ) -> ttk.Frame | None:
        """Build one parameter wrap (label + widget); return the wrap.

        Wrap is created as a child of ``parent`` (defaulting to
        ``self._value_pane``). The caller is responsible for placing
        the returned wrap via ``pack()`` / ``grid()``.

        The label is sourced from :attr:`ParamDef.description` (the
        short user-facing text — e.g. ``"Include current in denom"``)
        with the underscore-snake ``pdef.name`` as the fallback when
        ``description`` is empty. This keeps the row narrow enough to
        fit RVOL's 6 trigger-relevant params on typical dialog widths.

        Per-kind widget construction delegates to
        :func:`gui._param_widgets.build_param_widget` (eager commit
        policy: every variable write fires ``_commit_indicator``).
        """
        parent_widget = parent if parent is not None else self._value_pane
        wrap = ttk.Frame(parent_widget)
        label = ttk.Label(wrap, text=label_text_for(pdef))
        label.pack(side="left")
        seed = (self._ref.params or {}).get(pdef.name, pdef.default)
        # Width follows the same schema-driven sizing as the chart
        # indicator dialog: dropdowns fit their longest option and
        # spinboxes fit their declared numeric range. The flow layout
        # will add rows as needed instead of clipping long RRVOL/RVOL
        # params such as "Include current in denom".
        kind = getattr(pdef, "kind", "str")
        if kind == "choice":
            width: int | None = combo_width_for_choices(getattr(pdef, "choices", ()))
        elif kind in ("int", "float"):
            width = spinbox_width_for(pdef)
        elif kind == "str" and getattr(pdef, "choices", ()):
            width = max(combo_width_for_choices(getattr(pdef, "choices", ())), 8)
        elif kind == "str":
            width = 14
        else:
            width = None
        var, widget = build_param_widget(
            wrap, pdef, seed,
            on_change=self._commit_indicator,
            commit_policy="eager",
            width=width,
        )
        widget.pack(side="left")
        tip_text = tooltip_text_for(pdef)
        if tip_text:
            self._tooltips.append(ToolTip(label, tip_text))
            self._tooltips.append(ToolTip(widget, tip_text))
        # The scanner picker historically also commits on Spinbox /
        # Entry FocusOut + Return so a tab-out always persists even
        # when the eager trace already fired. Re-bind those events
        # here so behaviour is preserved for the still-unfocused
        # widget cases.
        if isinstance(widget, (ttk.Spinbox, ttk.Entry)):
            widget.bind("<FocusOut>", lambda _e: self._commit_indicator())
            widget.bind("<Return>",   lambda _e: self._commit_indicator())
        self._param_widgets[pdef.name] = var
        return wrap

    # -- adaptive flow layout ------------------------------------------------

    def _invalidate_flow_width_cache(self) -> None:
        self._flow_widths_cache = None
        self._flow_widths_cache_ids = ()

    def _flow_child_ids(self) -> tuple[int, ...]:
        return tuple(id(w) for w in self._flow_children)

    def _flow_widths_for_children(self) -> list[int]:
        ids = self._flow_child_ids()
        if self._flow_widths_cache is not None and ids == self._flow_widths_cache_ids:
            try:
                if all(w.winfo_exists() for w in self._flow_children):
                    return list(self._flow_widths_cache)
            except tk.TclError:
                self._invalidate_flow_width_cache()

        widths: list[int] = []
        for w in self._flow_children:
            try:
                if not w.winfo_exists():
                    widths.append(1)
                    continue
                w.update_idletasks()
                widths.append(max(1, int(w.winfo_reqwidth())))
            except tk.TclError:
                widths.append(1)
        self._flow_widths_cache = tuple(widths)
        self._flow_widths_cache_ids = ids
        return widths

    def _on_toplevel_configure(self, event: Any | None = None) -> None:
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

        Implementation: widgets live inside ``self._flow_row_frames``
        — one row Frame per logical row of widgets, packed
        ``side="top", anchor="w"`` inside ``self._value_pane``. Each
        widget packs ``side="left"`` inside its row Frame. When the
        target row count changes (e.g. dialog resized so a single-row
        layout no longer fits) the widgets are TORN DOWN and rebuilt
        with the new row count — needed because Tk doesn't support
        widget reparenting and the column-width-inheritance problem
        of a single shared grid wastes ~80 px on RVOL's narrower top
        row (where ``Mode:`` only needs ~110 px but the column is
        sized to fit ``Include current in denom:`` at ~165 px below).

        Rebuild on row-count-change is rare in practice (user picks
        an indicator → first reflow may flip from 1→N rows). Mid-
        edit rebuild can lose focus on a spinbox; this is an
        accepted trade-off for the correctness of the visual layout.
        """
        self._reflow_after_id = None
        if not self._flow_children:
            return
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        budget = self._flow_budget_px()

        widths = self._flow_widths_for_children()
        if not widths:
            return
        placements = _compute_flow_rows(widths, budget=budget, pad=6)
        target_row_count = max((p[0] for p in placements), default=0) + 1
        row_for_index = [r for (r, _c) in placements]
        current_row_count = len(self._flow_row_frames)
        if (
            target_row_count == current_row_count
            and target_row_count >= 1
            and row_for_index == self._flow_row_for_index
        ):
            # No reflow needed — widgets are already in the right
            # row frames. (We do not need to re-grid them within their
            # row frame because horizontal packing is automatic via
            # pack side="left".)
            return

        # Row count changed — tear down + rebuild.
        self._rebuild_indicator_branch_into_rows(target_row_count)

    def _flow_budget_px(self) -> int:
        """Return the current pixel budget for one picker flow row.

        When Tk has not mapped the Toplevel yet, ``winfo_width()``
        can be 1. Use requested width as a fallback so the initial
        RRVOL/RVOL layout still wraps instead of rendering one clipped
        mega-row until the user manually resizes the dialog.
        """
        try:
            top = self._toplevel_for_reflow or self.winfo_toplevel()
            win_w = top.winfo_width() if top is not None else 0
            if win_w < 100 and top is not None:
                win_w = max(win_w, int(top.winfo_reqwidth() or 0))
        except tk.TclError:
            win_w = 0
        if win_w < 100:
            win_w = 1000
        available = max(220, win_w - 280)
        if self._layout_hint == "stacked":
            return max(180, available)
        return max(180, available // 2)

    def _on_destroy(self, _event: Any | None = None) -> None:
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
_LOOKBACK_MODES_FULL: tuple[str, ...] = (
    WITHIN_LAST_MODE_ANY,
    WITHIN_LAST_MODE_ALL,
    WITHIN_LAST_MODE_EXACTLY,
)
#: Mode options for transition operators: ``all`` is hidden because
#: "every bar in the window is a cross" is not a meaningful trader
#: pattern. ``exactly`` stays — "the cross fired exactly N bars ago"
#: IS meaningful.
_LOOKBACK_MODES_FOR_TRANSITION: tuple[str, ...] = (
    WITHIN_LAST_MODE_ANY,
    WITHIN_LAST_MODE_EXACTLY,
)


class _FieldRefParamDialog(BaseModalDialog):
    """Modal Apply/Cancel popup for editing an indicator ``FieldRef``'s
    parameters (and output key) stacked vertically.

    Opened by the compact :class:`_FieldRefPicker` "Edit…" button so
    parameter-heavy indicators (RRVOL, BBANDS, SMI) never clip off-screen
    on a narrow dialog. Every trigger-relevant param gets its own labeled
    row in a scrollable, wheel-guarded form.

    The cross-symbol ``FieldRef.symbol`` pin is intentionally NOT edited
    here — it stays a visible ``@TICKER`` badge on the condition row (see
    CLAUDE.md §7.18). On Apply, :attr:`result` holds a brand-new
    ``FieldRef`` carrying the edited params while preserving the original
    ``id`` / ``symbol`` / ``interval``. On Cancel (or invalid input)
    :attr:`result` stays ``None``.
    """

    def __init__(self, parent: tk.Misc, *, ref: FieldRef) -> None:
        super().__init__(
            parent,
            title="Edit indicator parameters",
            geometry_key="dlg.field_ref_params",
            default_geometry="380x460",
            resizable=(True, True),
        )
        self._ref = ref
        self.result: FieldRef | None = None
        self._param_vars: dict[str, tk.Variable] = {}
        self._pdefs: dict[str, ParamDef] = {}
        self._info_tooltips: dict[str, ToolTip] = {}
        self._output_var: tk.StringVar | None = None
        self._error_var = tk.StringVar(value="")
        self._build_layout()
        self._finalize_modal(primary=self._on_primary, cancel=self._on_cancel)

    # -- layout --------------------------------------------------------------

    def _build_layout(self) -> None:
        spec = get_field(self._ref.id, kind="indicator")
        title = spec.label if spec is not None else self._ref.id

        header = ttk.Label(self, text=title, font=("TkDefaultFont", 10, "bold"))
        header.pack(side="top", anchor="w", padx=10, pady=(10, 4))

        body = ttk.Frame(self)
        body.pack(side="top", fill="both", expand=True, padx=4, pady=2)
        inner, canvas = make_scrollable_form(body)
        self._form_canvas = canvas

        if spec is not None:
            for pdef in spec.params_schema:
                self._build_param_row(inner, pdef)

            if len(spec.output_keys) > 1:
                self._build_output_row(inner, spec)

        # Footer: validation message + Cancel / Apply.
        footer = ttk.Frame(self)
        footer.pack(side="bottom", fill="x", padx=10, pady=(4, 10))
        ttk.Label(footer, textvariable=self._error_var, foreground="#c0392b").pack(
            side="left", anchor="w"
        )
        ttk.Button(footer, text="Apply", command=self._on_primary).pack(
            side="right", padx=(6, 0)
        )
        ttk.Button(footer, text="Cancel", command=self._on_cancel).pack(side="right")

        protect_combobox_wheel(self, scroll_target=canvas)

    def _add_info_icon(self, row: tk.Misc, key: str, tip: str) -> None:
        """Render a hover-for-description (i) icon next to a param label."""
        if not tip:
            return
        icon = ttk.Label(
            row,
            text=_INFO_ICON_GLYPH,
            foreground="#1f4ea1",
            cursor="question_arrow",
        )
        icon.pack(side="left", anchor="w", padx=(3, 0))
        self._info_tooltips[key] = ToolTip(icon, tip)

    def _build_param_row(self, parent: tk.Misc, pdef: ParamDef) -> None:
        row = ttk.Frame(parent)
        row.pack(side="top", fill="x", padx=6, pady=3)
        label = ttk.Label(row, text=label_text_for(pdef))
        label.pack(side="left", anchor="w")
        tip = tooltip_text_for(pdef)
        if tip:
            ToolTip(label, tip)
        self._add_info_icon(row, pdef.name, tip)
        seed = self._ref.params.get(pdef.name, getattr(pdef, "default", None))
        var, widget = build_param_widget(parent, pdef, seed, commit_policy="manual")
        widget.pack(in_=row, side="right", anchor="e")
        self._param_vars[pdef.name] = var
        self._pdefs[pdef.name] = pdef

    def _build_output_row(self, parent: tk.Misc, spec: Any) -> None:
        row = ttk.Frame(parent)
        row.pack(side="top", fill="x", padx=6, pady=3)
        ttk.Label(row, text="Output:").pack(side="left", anchor="w")
        self._add_info_icon(
            row,
            "__output__",
            "Which output series of this indicator to compare against.",
        )
        current = self._ref.output_key or spec.default_output_key
        self._output_var = tk.StringVar(value=current)
        ttk.Combobox(
            row,
            textvariable=self._output_var,
            state="readonly",
            values=tuple(spec.output_keys),
            width=max(combo_width_for_choices(spec.output_keys), 8),
        ).pack(side="right", anchor="e")

    # -- actions -------------------------------------------------------------

    def _on_primary(self) -> None:
        spec = get_field(self._ref.id, kind="indicator")
        params: dict[str, Any] = {}
        if spec is not None:
            for pdef in spec.params_schema:
                var = self._param_vars.get(pdef.name)
                if var is None:
                    continue
                try:
                    raw = var.get()
                except tk.TclError:
                    continue
                ok, value, message = validate_param_value(pdef, raw)
                if not ok:
                    self._error_var.set(message)
                    return
                params[pdef.name] = value
        self._error_var.set("")
        output_key = self._ref.output_key
        if self._output_var is not None:
            try:
                output_key = self._output_var.get()
            except tk.TclError:
                pass
        self.result = FieldRef.indicator(
            self._ref.id,
            params=params,
            output_key=output_key,
            symbol=self._ref.symbol,
            interval=self._ref.interval,
        )
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _on_cancel(self) -> None:
        self.result = None
        try:
            self.destroy()
        except tk.TclError:
            pass


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
        node: Condition | Group,
        on_change: Callable[[], None] | None = None,
        op: str | None = None,
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
    def _modes_for_op(op: str | None) -> tuple[str, ...]:
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
    """Render and edit one :class:`Condition` leaf.

    Two visual layouts:

    * ``"inline"`` — historical 7-column single row used for simple
      conditions like ``close > 100`` or ``ema(20) > 100``.
    * ``"stacked"`` — 3-row layout used when the LEFT picker is
      "complex" (cross-symbol pin, indicator with 3+ params, or
      multi-output indicator), the op is ``between`` (which has two
      field params on its own row), or any of the per-op field
      params (``right`` / ``low`` / ``high`` / ``target`` /
      ``reference``) is complex itself.

    The layout is selected by :meth:`_classify_layout` at build time
    and re-evaluated whenever the user changes the op (``_on_op_change``),
    the LEFT picker (``_on_left_change``), or a per-op field param
    (``_on_param_field_change``). Layout flips fire ``on_change`` so
    the consumer dialog's wheel-guard re-applies — see
    CLAUDE.md §7.11 / §7.19.

    Widget identity is preserved across layout flips: the same
    Checkbutton / op Combobox / lookback cluster / interval combo /
    delete button instances are simply re-gridded into new
    ``(row, column)`` positions. The per-op param widgets are the
    one exception — they rebuild on every op change because the
    schema changes.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        cond: Condition,
        on_change: Callable[[], None] | None = None,
        on_delete: Callable[[_ConditionFrame], None] | None = None,
        default_interval: str = "5m",
        view_mode: str = VIEW_AUTO,
        data_status_provider: Callable[[FieldRef], tuple[bool, str]] | None = None,
    ) -> None:
        super().__init__(master, padding=(4, 2))
        self.cond = cond
        self._on_change = on_change
        self._on_delete = on_delete
        self._default_interval = default_interval
        self._view_mode = view_mode if view_mode in (
            VIEW_AUTO, VIEW_COMPACT, VIEW_DETAILED,
        ) else VIEW_AUTO
        self._data_status_provider = data_status_provider
        # Debounced resize-reclassify state.
        self._resize_after_id: str | None = None
        self._toplevel_for_resize: tk.Misc | None = None
        self._toplevel_resize_bind_id: str | None = None

        self._build()

        # Bind to the Toplevel ``<Configure>`` so window resize
        # triggers a re-classification (fit-based inline ↔ stacked
        # flip when the dialog gets narrower/wider). The picker also
        # binds for its own internal flow wrap; this binding is
        # specifically for the OUTER ConditionFrame layout decision.
        try:
            top = self.winfo_toplevel()
        except tk.TclError:
            top = None
        if top is not None and top is not self:
            try:
                self._toplevel_for_resize = top
                self._toplevel_resize_bind_id = top.bind(
                    "<Configure>",
                    self._on_toplevel_resize,
                    add="+",
                )
            except tk.TclError:
                self._toplevel_for_resize = None
                self._toplevel_resize_bind_id = None
        self.bind("<Destroy>", self._on_destroy_resize_binding)

    # -- public API -----------------------------------------------------------

    def get(self) -> Condition:
        return self.cond

    # -- layout ---------------------------------------------------------------

    def _build(self) -> None:
        # Decide the layout FIRST so ``_build_params_row`` can render
        # field-param wraps in the correct orientation (horizontal for
        # inline, vertical for stacked).
        self._current_layout: str = self._classify_layout()
        self._summary_label: ttk.Label | None = None
        self._build_shared_widgets()
        self._build_params_row()
        self._apply_layout()

    def _build_shared_widgets(self) -> None:
        """Create the widgets that participate in BOTH inline and stacked layouts.

        These widgets are gridded into different (row, column)
        positions by :meth:`_apply_inline_layout` /
        :meth:`_apply_stacked_layout`; they're never destroyed by a
        layout flip.

        ``sticky="nw"`` on every cell keeps the chrome
        (checkbox / op / params / interval / delete) anchored to
        the top of row 0 even when the left ``_FieldRefPicker``
        grows to multiple sub-rows via its adaptive flow layout.
        Without it, Tk's default centring would visually float the
        operator combo halfway down the picker on RVOL-with-many-
        params conditions.
        """
        self._enabled_var = tk.BooleanVar(value=self.cond.enabled)
        self._enabled_chk = ttk.Checkbutton(
            self, variable=self._enabled_var,
            command=self._on_enabled_toggle,
        )

        self._left_picker = _FieldRefPicker(
            self, ref=self.cond.left, on_change=self._on_left_change,
            layout_hint="inline", display_mode="compact",
            data_status_provider=self._data_status_provider,
        )

        self._op_var = tk.StringVar(value=self.cond.op)
        self._op_combo = ttk.Combobox(
            self, textvariable=self._op_var, state="readonly",
            values=ALL_OPERATORS, width=14,
        )
        self._op_combo.bind("<<ComboboxSelected>>",
                            lambda _e: self._on_op_change())

        # Two sub-frames inside the params region: scalar widgets
        # (int / float — e.g. lookback, n, bars, tolerance_pct) stay
        # next to the op combo; field widgets (right / low / high /
        # target / reference) move to their own row in the stacked
        # layout.
        self._params_scalar_frame = ttk.Frame(self)
        self._params_fields_frame = ttk.Frame(self)
        self._param_widgets: dict[str, Any] = {}

        self._lookback = _LookbackCluster(
            self, node=self.cond, op=self.cond.op,
            on_change=self._fire,
        )

        self._interval_var = tk.StringVar(
            value=self.cond.interval or self._default_interval)
        self._interval_combo = ttk.Combobox(
            self, textvariable=self._interval_var, state="readonly",
            values=_INTERVALS, width=5,
        )
        self._interval_var.trace_add(
            "write", lambda *_a: self._on_interval_change())

        self._delete_btn = ttk.Button(
            self, text="✕", width=3, command=self._do_delete)
        self._summary_label = ttk.Label(self, text=_condition_summary_text(self.cond))

    def _apply_layout(self) -> None:
        """(Re)grid every shared widget to match ``self._current_layout``.

        Idempotent: safe to call any time. ``grid_forget`` is called
        on every shared widget first so re-gridding doesn't pile
        ghost cells.

        Does NOT rebuild the per-op param widgets — the orientation
        of the field wraps inside ``_params_fields_frame`` depends
        on the layout (vertical in stacked, horizontal in inline)
        so callers that flip layouts must call
        :meth:`_build_params_row` separately, BEFORE this method,
        with ``self._current_layout`` already updated.
        """
        for w in (
            self._enabled_chk, self._left_picker, self._op_combo,
            self._params_scalar_frame, self._params_fields_frame,
            self._lookback, self._interval_combo, self._delete_btn,
            self._summary_label,
        ):
            try:
                w.grid_forget()
            except tk.TclError:
                pass

        layout = self._current_layout
        if self._view_mode == VIEW_COMPACT:
            self._apply_compact_layout()
            return

        # Propagate the layout hint to every embedded picker so its
        # internal flow-layout budget reflects the row's true width.
        try:
            self._left_picker.set_layout_hint(layout)
        except (AttributeError, tk.TclError):
            pass
        for _name, (kind, widget) in self._param_widgets.items():
            if kind == "field":
                try:
                    widget.set_layout_hint(layout)
                except (AttributeError, tk.TclError):
                    pass

        if layout == "stacked":
            self._apply_stacked_layout()
        else:
            self._apply_inline_layout()

        self._update_left_visibility()

    def _apply_compact_layout(self) -> None:
        self._enabled_chk.grid(row=0, column=0, padx=(0, 4), sticky="nw")
        if self._summary_label is not None:
            self._summary_label.configure(text=_condition_summary_text(self.cond))
            self._summary_label.grid(row=0, column=1, padx=(0, 6), sticky="w")
        self._delete_btn.grid(row=0, column=2, sticky="ne")

    def _apply_inline_layout(self) -> None:
        """Historical 7-column single-row layout.

        Used for simple conditions like ``close > 100``. Every
        chrome widget shares row 0 with the LEFT picker; the picker
        gets the half-row flow budget for its internal wrap.
        """
        self._enabled_chk.grid(row=0, column=0, padx=(0, 4), sticky="nw")
        self._left_picker.grid(row=0, column=1, padx=(0, 6), sticky="nw")
        self._op_combo.grid(row=0, column=2, padx=(0, 6), sticky="nw")
        self._params_scalar_frame.grid(
            row=0, column=3, padx=(0, 6), sticky="nw")
        self._params_fields_frame.grid(
            row=0, column=4, padx=(0, 6), sticky="nw")
        self._lookback.grid(row=0, column=5, padx=(0, 6), sticky="nw")
        self._interval_combo.grid(row=0, column=6, padx=(0, 6), sticky="nw")
        self._delete_btn.grid(row=0, column=7, padx=(0, 0), sticky="nw")

    def _apply_stacked_layout(self) -> None:
        """3-row layout used when the LEFT picker or any RHS picker is complex.

        Visual structure::

            row 0: [enabled] [LEFT picker (columnspan 3) .........] [interval] [✕]
            row 1:           [op]   [scalar params]   [lookback]
            row 2:           [field params (RHS)]

        The LEFT picker takes columnspan 3 so it expands all the way
        to the interval combo. Field params (row 2) also columnspan
        3 — they vertically stack inside ``_params_fields_frame``
        for ops with multiple field params (e.g. ``between``).
        """
        self._enabled_chk.grid(row=0, column=0, padx=(0, 4), sticky="nw")
        self._left_picker.grid(
            row=0, column=1, columnspan=3, padx=(0, 6), sticky="new")
        self._interval_combo.grid(
            row=0, column=4, padx=(0, 6), sticky="nw")
        self._delete_btn.grid(
            row=0, column=5, padx=(0, 0), sticky="nw")

        self._op_combo.grid(
            row=1, column=1, padx=(0, 6), pady=(2, 0), sticky="nw")
        self._params_scalar_frame.grid(
            row=1, column=2, padx=(0, 6), pady=(2, 0), sticky="nw")
        self._lookback.grid(
            row=1, column=3, padx=(0, 6), pady=(2, 0), sticky="nw")

        self._params_fields_frame.grid(
            row=2, column=1, columnspan=3, padx=(0, 6),
            pady=(2, 0), sticky="new")

    def _classify_layout(self) -> str:
        """Return ``"stacked"`` if the row should use the 3-row layout, else ``"inline"``.

        **Fit-based** classification (new generalised rule):

        * ``op == between`` → stacked (two RHS field pickers stack
          vertically reads better than horizontally).
        * Otherwise: compare :func:`_estimate_condition_inline_width`
          to :meth:`_get_available_width`. If the inline rendering
          would overflow the dialog's available width → stacked. If
          it fits comfortably → inline.

        **Hysteresis**: when currently stacked, require an
        ``_HYSTERESIS_PX`` buffer before flipping back to inline.
        This prevents flip-flopping during a slow drag at the
        boundary between fits and doesn't-fit.

        **Fallback when toplevel not realized**: assume a 1200 px
        available width — typical of the dialogs that mount the
        BlockEditor (entries / exits at 1400 px, scanner at 1200 px,
        custom indicator at 980 px right pane). This makes the
        classifier deterministic during the initial build before
        the window has been mapped; the first real ``<Configure>``
        will trigger reclassification against the actual width.

        Window-resize reactive: bound to the Toplevel ``<Configure>``
        event via :meth:`_on_toplevel_resize`, so the user dragging
        the dialog wider or narrower automatically flips the layout.
        """
        op = self.cond.op
        if self._view_mode == VIEW_DETAILED:
            return "stacked"
        if self._view_mode == VIEW_COMPACT:
            return "compact"
        if op == OP_BETWEEN:
            return "stacked"
        try:
            inline_width = _estimate_condition_inline_width(self.cond)
            available = self._get_available_width()
        except Exception:  # noqa: BLE001
            return getattr(self, "_current_layout", "inline")
        if available < 100:
            available = _DEFAULT_DIALOG_WIDTH_PX
        current = getattr(self, "_current_layout", None)
        if current == "stacked":
            return "inline" if inline_width < (available - _HYSTERESIS_PX) else "stacked"
        # Currently inline (or unset) — flip to stacked on overflow.
        return "stacked" if inline_width > available else "inline"

    def _get_available_width(self) -> int:
        """Return the actual width available for the condition row.

        Walks up the widget tree looking for the nearest
        :class:`BlockEditor` ancestor (which is packed
        ``fill="both", expand=True`` inside the dialog scroll
        canvas). Falls back to the Toplevel width minus a small
        chrome reservation when the BlockEditor is not yet realized.
        """
        # Walk up looking for BlockEditor.
        try:
            w: tk.Misc | None = self
            while w is not None:
                if isinstance(w, BlockEditor):
                    be_width = int(w.winfo_width())
                    if be_width > 100:
                        return be_width - 20  # small padding allowance
                    break
                w = w.master
        except tk.TclError:
            pass
        # Fallback: Toplevel.
        try:
            top = self.winfo_toplevel()
            top_w = int(top.winfo_width())
            if top_w > 100:
                return max(400, top_w - 80)
        except tk.TclError:
            pass
        return 0  # unrealized — caller treats as "unknown"

    def _relayout_if_needed(self) -> bool:
        """If the classification has changed, rebuild params + re-grid.

        Returns True when a flip happened (caller may want to fire an
        extra ``on_change`` so the consumer's wheel-guard re-applies
        on the freshly rebuilt field-picker widgets — see
        CLAUDE.md §7.19).

        The rebuild path destroys the existing field-param pickers
        and creates new ones with the correct orientation
        (vertical in stacked, horizontal in inline). Scalar-param
        widgets share the same single-row inside
        ``_params_scalar_frame`` in both layouts but are rebuilt
        alongside for simplicity.
        """
        new_layout = self._classify_layout()
        if new_layout == self._current_layout:
            return False
        self._current_layout = new_layout
        # Rebuild params so field-wrap orientation flips and the
        # in-flight picker is destroyed (it would still carry its
        # old layout_hint reflow budget otherwise).
        self._build_params_row()
        self._apply_layout()
        return True

    def _on_toplevel_resize(self, event: Any | None = None) -> None:
        """Debounced ``<Configure>`` handler — re-classify on resize.

        Triggered when the user drags the dialog edge. We debounce
        with ``after(100, ...)`` so a continuous drag results in
        ONE final reclassification rather than dozens. Each call
        cancels the prior scheduled one.

        Re-fires ``on_change`` when the layout actually flips so the
        consumer dialog's wheel-guard re-applies on the freshly
        rebuilt per-op pickers (CLAUDE.md §7.19).
        """
        if self._toplevel_for_resize is None:
            return
        # Filter to only the bound Toplevel — descendant Configure
        # events shouldn't reach here under standard Tk binding
        # semantics, but defensively check anyway.
        if event is not None and getattr(event, "widget", None) is not self._toplevel_for_resize:
            return
        if self._resize_after_id is not None:
            try:
                self.after_cancel(self._resize_after_id)
            except tk.TclError:
                pass
            self._resize_after_id = None
        try:
            if not self.winfo_exists():
                return
            self._resize_after_id = self.after(100, self._do_resize_reclassify)
        except tk.TclError:
            pass

    def _do_resize_reclassify(self) -> None:
        """Run the deferred reclassification + fire ``on_change`` on flip."""
        self._resize_after_id = None
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        flipped = self._relayout_if_needed()
        if flipped:
            self._fire()

    def _on_destroy_resize_binding(self, _event: Any | None = None) -> None:
        """Tear down pending resize callback + Toplevel <Configure> binding."""
        if self._resize_after_id is not None:
            try:
                self.after_cancel(self._resize_after_id)
            except tk.TclError:
                pass
            self._resize_after_id = None
        if self._toplevel_for_resize is not None and self._toplevel_resize_bind_id:
            try:
                self._toplevel_for_resize.unbind(
                    "<Configure>", self._toplevel_resize_bind_id)
            except tk.TclError:
                pass
        self._toplevel_for_resize = None
        self._toplevel_resize_bind_id = None

    def _build_params_row(self) -> None:
        """Tear down and re-render the per-op param widgets.

        Scalar params (int / float) go into ``_params_scalar_frame``
        — they sit next to the op combo in both layouts. Field
        params (FieldRef-typed slots) go into ``_params_fields_frame``
        — they share the op row in inline mode but move to their own
        row in stacked mode.

        In stacked mode field params are gridded vertically inside
        ``_params_fields_frame`` (one per row) so an operator like
        ``between`` shows ``low`` and ``high`` stacked rather than
        side-by-side. In inline mode they sit horizontally.
        """
        for frame in (self._params_scalar_frame, self._params_fields_frame):
            for w in frame.winfo_children():
                try:
                    w.destroy()
                except tk.TclError:
                    pass
        self._param_widgets = {}
        schema = OPERATOR_PARAM_SCHEMA.get(self.cond.op, ())
        layout = getattr(self, "_current_layout", "inline")
        is_stacked = layout == "stacked"
        scalar_col = 0
        field_idx = 0
        for name, kind in schema:
            if kind == "field":
                wrap = ttk.Frame(self._params_fields_frame)
                if is_stacked:
                    wrap.grid(row=field_idx, column=0,
                              padx=(0, 6), pady=(2 if field_idx else 0, 0),
                              sticky="nw")
                else:
                    wrap.grid(row=0, column=field_idx,
                              padx=(0, 6), sticky="nw")
                ttk.Label(wrap, text=name + ":").pack(side="left")
                current = self.cond.params.get(name)
                ref = current if isinstance(current, FieldRef) \
                    else FieldRef.literal(0.0)
                picker = _FieldRefPicker(
                    wrap, ref=ref,
                    on_change=self._on_param_field_change,
                    layout_hint=layout, display_mode="compact",
                    data_status_provider=self._data_status_provider,
                )
                picker.pack(side="left")
                self._param_widgets[name] = ("field", picker)
                field_idx += 1
            else:
                wrap = ttk.Frame(self._params_scalar_frame)
                wrap.grid(row=0, column=scalar_col, padx=(0, 6))
                ttk.Label(wrap, text=name + ":").pack(side="left")
                current = self.cond.params.get(name)
                seed = current if isinstance(current, (int, float)) else (
                    1 if kind == "int" else 1.0
                )
                # Synthesize a ParamDef so we can route through the
                # shared widget builder. The OPERATOR_PARAM_SCHEMA
                # uses bare (name, kind) tuples — no min/max/step,
                # no description — so the helper applies defaults
                # (from_=-1e12, to=1e12, increment=1 / 0.1, width=6).
                synth = ParamDef(name=name, kind=kind, default=seed)
                var, widget = build_param_widget(
                    wrap, synth, seed,
                    commit_policy="manual",
                )
                widget.pack(side="left")
                # ConditionFrame consumes ``var.get()`` on its own
                # schedule via ``_commit_params``; bind FocusOut /
                # Return / spinbox-arrow to trigger that commit
                # explicitly so the manual policy still fires when
                # the user tabs out or arrow-spams.
                if isinstance(widget, ttk.Spinbox):
                    widget.configure(command=self._commit_params)
                    widget.bind("<FocusOut>", lambda _e: self._commit_params())
                    widget.bind("<Return>",   lambda _e: self._commit_params())
                self._param_widgets[name] = (kind, var)
                scalar_col += 1

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
        # LEFT picker is the dominant classifier — flipping rvol →
        # close MUST collapse the row back to inline (and vice versa).
        self._relayout_if_needed()
        self._fire()

    def _on_op_change(self) -> None:
        new_op = self._op_var.get()
        if new_op == self.cond.op or new_op not in OPERATOR_PARAM_SCHEMA:
            return
        # Build fresh params from the new schema's defaults.
        new_params: dict[str, Any] = {}
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
        # Op changed → may transition between inline and stacked
        # (e.g. binary → between). Update the classification BEFORE
        # rebuilding the params row so the field-wrap orientation
        # matches the new layout.
        self._current_layout = self._classify_layout()
        self._build_params_row()
        self._apply_layout()
        # Notify the look-back cluster so it can refresh its mode list
        # (and coerce 'all' → 'any' if the new op is a transition).
        try:
            self._lookback.set_op(new_op)
        except (AttributeError, tk.TclError):
            # Cluster may not exist yet during early construction.
            pass
        self._fire()

    def _on_param_field_change(self) -> None:
        """RHS / per-op field picker changed — commit + maybe re-layout.

        Toggling a per-op field picker from Number to an Indicator
        with 3+ params can flip the classification stacked ↔ inline,
        so re-check after each commit. When a flip happens the
        ``_relayout_if_needed`` rebuild destroys the field-picker
        widgets and creates new ones, so we ``_fire()`` once more
        afterwards to give the consumer's wheel-guard a chance to
        re-apply on the new widgets (CLAUDE.md §7.19).
        """
        self._commit_params()
        if self._relayout_if_needed():
            self._fire()

    def _on_interval_change(self) -> None:
        v = self._interval_var.get()
        if v and v != self.cond.interval:
            self.cond.interval = v
            self._fire()

    def _commit_params(self) -> None:
        new_params: dict[str, Any] = {}
        for name, (kind, widget) in self._param_widgets.items():
            if kind == "field":
                new_params[name] = widget.get()
            elif kind == "int":
                try:
                    numeric = float(widget.get())
                except (TypeError, ValueError, OverflowError):
                    new_params[name] = self.cond.params.get(name, 1)
                    continue
                if not math.isfinite(numeric) or not numeric.is_integer():
                    new_params[name] = self.cond.params.get(name, 1)
                    continue
                new_params[name] = int(numeric)
            else:  # float
                try:
                    numeric = float(widget.get())
                except (TypeError, ValueError, OverflowError):
                    new_params[name] = self.cond.params.get(name, 1.0)
                    continue
                if not math.isfinite(numeric):
                    new_params[name] = self.cond.params.get(name, 1.0)
                    continue
                new_params[name] = numeric
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
        on_change: Callable[[], None] | None = None,
        on_delete: Callable[[_GroupFrame], None] | None = None,
        default_interval: str = "5m",
        is_root: bool = False,
        view_mode: str = VIEW_AUTO,
        data_status_provider: Callable[[FieldRef], tuple[bool, str]] | None = None,
    ) -> None:
        super().__init__(master, padding=(6, 4),
                         relief="solid", borderwidth=1)
        self.group = group
        self._on_change = on_change
        self._on_delete = on_delete
        self._default_interval = default_interval
        self._is_root = is_root
        self._view_mode = view_mode
        self._data_status_provider = data_status_provider
        self._child_frames: list[_GroupFrame | _ConditionFrame] = []

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
                    view_mode=self._view_mode,
                    data_status_provider=self._data_status_provider,
                )
            elif isinstance(child, Condition):
                wf = _ConditionFrame(
                    self._children_frame, cond=child,
                    on_change=self._on_change,
                    on_delete=self._remove_child_widget,
                    default_interval=self._default_interval,
                    view_mode=self._view_mode,
                    data_status_provider=self._data_status_provider,
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

    def _remove_child_widget(self, widget: _GroupFrame | _ConditionFrame) -> None:
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
        root: Group | None = None,
        on_change: Callable[[], None] | None = None,
        default_interval: str = "5m",
        data_status_provider: Callable[[FieldRef], tuple[bool, str]] | None = None,
    ) -> None:
        super().__init__(master)
        self._on_change = on_change
        self._default_interval = default_interval
        self._data_status_provider = data_status_provider
        self._root_group: Group = root or Group(combinator="and", children=[])
        self._root_frame: _GroupFrame | None = None
        self._view_var = tk.StringVar(value=VIEW_AUTO)
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 4))
        ttk.Label(header, text="Builder view:").pack(side="left", padx=(0, 4))
        self._view_combo = ttk.Combobox(
            header,
            textvariable=self._view_var,
            state="readonly",
            values=(VIEW_AUTO, VIEW_COMPACT, VIEW_DETAILED),
            width=16,
        )
        self._view_combo.pack(side="left")
        self._view_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_view_mode_change())
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

    def set_view_mode(self, mode: str) -> None:
        if mode not in (VIEW_AUTO, VIEW_COMPACT, VIEW_DETAILED):
            mode = VIEW_AUTO
        self._view_var.set(mode)
        self._render_root()

    def _on_view_mode_change(self) -> None:
        self.set_view_mode(self._view_var.get())

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
            view_mode=self._view_var.get(),
            data_status_provider=self._data_status_provider,
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


def _applicability_text_for_ref(
    ref: FieldRef,
    data_status_provider: Callable[[FieldRef], tuple[bool, str]] | None = None,
) -> str:
    """Return text-only applicability summary for one FieldRef."""
    if ref.kind == FIELD_KIND_LITERAL:
        return "No data dependency."
    symbol = str(ref.symbol or "").strip().upper()
    interval = str(ref.interval or "").strip()
    symbol_part = f"Requires {symbol} data" if symbol else "Uses active symbol"
    interval_part = f"at {interval} interval" if interval else "at default interval"
    parts = [f"{symbol_part} {interval_part}."]
    if data_status_provider is not None:
        ok, message = _data_status_for_ref(ref, data_status_provider)
        state = "yes" if ok else "no"
        parts.append(f"Can run now: {state}. {message}")
    return " ".join(parts)


def _badges_for_ref(
    ref: FieldRef,
    data_status_provider: Callable[[FieldRef], tuple[bool, str]] | None = None,
) -> list[tuple[str, str]]:
    if ref.kind == FIELD_KIND_LITERAL:
        return []
    badges: list[tuple[str, str]] = []
    if ref.symbol:
        badges.append(("Dep", f"Requires companion symbol data for {ref.symbol}."))
    if ref.interval:
        badges.append((str(ref.interval), "Uses a non-default interval."))
    if data_status_provider is not None:
        ok, message = _data_status_for_ref(ref, data_status_provider)
        badges.append(("Data OK" if ok else "Missing data", message))
    elif ref.symbol:
        badges.append(("Data ?", "Dependency data availability has not been checked."))
    return badges


def _data_status_for_ref(
    ref: FieldRef,
    provider: Callable[[FieldRef], tuple[bool, str]],
) -> tuple[bool, str]:
    try:
        ok, message = provider(ref)
        return bool(ok), str(message or ("Latest data available." if ok else "Missing required data."))
    except Exception:  # noqa: BLE001
        return False, "Data status check failed."


def _condition_summary_text(cond: Condition) -> str:
        left = _field_ref_summary(cond.left)
        params = cond.params or {}
        rhs = ""
        for key in ("right", "low", "high", "target", "reference"):
            if key in params:
                value = params[key]
                rhs = _field_ref_summary(value) if isinstance(value, FieldRef) else str(value)
                break
        interval = cond.interval or ""
        bits = [left, cond.op]
        if rhs:
            bits.append(rhs)
        if interval:
            bits.append(f"@ {interval}")
        if not cond.enabled:
            bits.append("(disabled)")
        return " ".join(bits)


def _field_ref_summary(ref: FieldRef | None) -> str:
        if ref is None:
            return "(value)"
        if ref.kind == FIELD_KIND_LITERAL:
            return _format_number(ref.value)
        base = ref.id
        if ref.output_key:
            base = f"{base}.{ref.output_key}"
        if ref.symbol:
            base = f"{ref.symbol}:{base}"
        return base


#: Abbreviations applied to *non-numeric* indicator parameter names in
#: the compact picker token (e.g. ``rrvol(20, vs=QQQ)``). Numeric params
#: (lengths / periods / std multipliers) are always rendered by value,
#: so they never appear here. Keeps the token short enough to fit
#: alongside the operator + RHS on a narrow dialog.
_PARAM_NAME_ABBREV: dict[str, str] = {
    "denominator_includes_current": "incl_cur",
    "z_score": "z",
    "compare_symbol": "vs",
    "session_filter": "sess",
    "aggregator": "agg",
    "mode": "mode",
}

#: Abbreviations applied to selected *choice* parameter values.
_PARAM_VALUE_ABBREV: dict[str, str] = {
    "time_of_day": "tod",
    "regular_plus_premarket": "reg+pre",
    "regular_only": "reg",
}


def _indicator_param_summary(spec: Any, params: Mapping[str, Any] | None) -> str:
    """Return a compact parenthesized parameter summary for an indicator.

    Policy (pinned by ``tests/unit/gui/test_field_ref_summary.py``):

    * **Numeric params** (``int`` / ``float``) are *always* shown by
      value — they are the indicator's identity (lengths, periods, std
      multipliers). Rendered first, in schema order.
    * **Non-numeric params** (``choice`` / ``str`` / ``bool``) are shown
      *only when they differ from the schema default*, abbreviated via
      :data:`_PARAM_NAME_ABBREV` / :data:`_PARAM_VALUE_ABBREV`:

      - ``mode`` renders just the (abbreviated) value, e.g. ``tod``;
      - ``compare_symbol`` renders ``vs=QQQ``;
      - booleans render the bare abbreviation when ``True``
        (``z``, ``incl_cur``) or ``<abbrev>=off`` when toggled off from
        a ``True`` default;
      - other choices/strings render ``<abbrev>=<value>``.

    Returns ``""`` when the schema has no parameters at all.
    """
    schema = tuple(getattr(spec, "params_schema", ()) or ())
    if not schema:
        return ""
    p = dict(params or {})
    num_bits: list[str] = []
    flag_bits: list[str] = []
    for pdef in schema:
        name = getattr(pdef, "name", "")
        kind = getattr(pdef, "kind", "str")
        default = getattr(pdef, "default", None)
        value = p.get(name, default)
        if kind in ("int", "float"):
            num_bits.append(_format_number(value))
            continue
        # Non-numeric: skip when equal to default (default noise).
        if value == default:
            continue
        abbrev = _PARAM_NAME_ABBREV.get(name, name)
        if kind == "bool":
            flag_bits.append(abbrev if value else f"{abbrev}=off")
        elif name == "mode":
            flag_bits.append(_PARAM_VALUE_ABBREV.get(str(value), str(value)))
        elif name == "compare_symbol":
            flag_bits.append(f"vs={value}")
        else:
            val_txt = _PARAM_VALUE_ABBREV.get(str(value), str(value))
            flag_bits.append(f"{abbrev}={val_txt}")
    bits = num_bits + flag_bits
    if not bits:
        return ""
    return "(" + ", ".join(bits) + ")"


def _field_ref_compact_token(ref: FieldRef | None) -> str:
    """Return the compact one-line token shown in compact picker mode.

    Examples: ``3.5`` (literal), ``close`` (builtin), ``rvol(20)`` /
    ``rrvol(30, vs=QQQ)`` / ``smi.signal(14, 3, 3, 3)`` (indicators).

    The cross-symbol pin (:attr:`FieldRef.symbol`) is intentionally
    **excluded** — it is surfaced separately as an ``@SPY`` badge so it
    is not conflated with RRVOL's ``compare_symbol`` benchmark param.
    """
    if ref is None:
        return "(value)"
    if ref.kind == FIELD_KIND_LITERAL:
        return _format_number(ref.value)
    if ref.kind == FIELD_KIND_BUILTIN:
        return ref.id
    base = ref.id
    if ref.output_key:
        base = f"{base}.{ref.output_key}"
    spec = get_field(ref.id, kind="indicator")
    summary = _indicator_param_summary(spec, ref.params) if spec is not None else ""
    return f"{base}{summary}"


__all__ = ["BlockEditor"]
