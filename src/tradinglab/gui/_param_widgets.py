"""Shared ParamDef widget construction.

Single source of truth for the bool/choice/int/float/str dispatcher
that previously lived in 3 sites (``indicator_dialog``,
``scanner_block_editor`` twice). Future ParamDef kinds get added here
exactly once; the §7.19 ``pdef.description``-label fix applies
uniformly.

Does NOT cover the exits-dialog-widgets ``_render_field`` taxonomy
(``time_str`` / ``enum_with_none`` / ``enum_str``) — that lives in
a sibling helper per audit #8.
"""

from __future__ import annotations

import math
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any, Literal

from ..indicators.base import ParamDef

CommitPolicy = Literal["eager", "debounced", "on_focus_out", "manual"]

# Default Combobox / Spinbox / Entry widths when the caller does not
# pass an explicit ``width=``. Kept conservative — callers that need
# fancier sizing (e.g. ``indicator_dialog``'s schema-driven Combobox
# width) compute their own and pass it in.
_DEFAULT_WIDTH_BY_KIND: dict[str, int] = {
    "choice": 10,
    "int": 6,
    "float": 6,
    "str": 14,
}


def combo_width_for_choices(choices: Any) -> int:
    """Pick a Combobox ``width=`` that fits the longest choice."""
    try:
        items = [str(c) for c in (choices or ())]
    except TypeError:
        items = []
    if not items:
        return 10
    longest = max(len(s) for s in items)
    return max(8, min(30, longest + 2))


def spinbox_width_for(pdef: Any) -> int:
    """Pick a Spinbox ``width=`` for an int / float ``ParamDef``."""
    def _digits(v: Any) -> int:
        if v is None:
            return 6
        try:
            return len(str(v))
        except Exception:  # noqa: BLE001
            return 6

    width = max(_digits(getattr(pdef, "min", None)),
                _digits(getattr(pdef, "max", None))) + 2
    return max(6, min(14, width))


def label_text_for(pdef: ParamDef) -> str:
    """Return the user-facing label for ``pdef``.

    Source-of-truth for the §7.19 ``pdef.description`` rule: the
    short user-facing string takes precedence over the underscore-
    snake ``pdef.name`` so a wide label (e.g. ``"Include current in
    denom"``) renders the same in every dialog. Falls back to
    ``pdef.name`` when ``description`` is empty. Always trailed with
    ``":"`` for visual consistency.
    """
    label = (getattr(pdef, "description", "") or pdef.name) + ":"
    return label


def tooltip_text_for(pdef: ParamDef) -> str:
    """Return a concise non-essential tooltip for a parameter field."""
    name = str(getattr(pdef, "name", "") or "")
    label = label_text_for(pdef).rstrip(":")
    specific: dict[str, str] = {
        "denominator_includes_current": (
            "Include current bar in average denominator. Turn off to compare "
            "the current bar against prior bars only."
        ),
        "session_filter": "Choose which trading sessions contribute to this calculation.",
        "z_score": "Show the value as a Z-score versus recent history.",
        "compare_symbol": "Optional companion ticker used by relative/cross-symbol calculations.",
    }
    if name in specific:
        return specific[name]

    parts = [label] if label else []
    choices = getattr(pdef, "choices", None)
    if choices:
        try:
            parts.append("Choices: " + ", ".join(str(c) for c in choices))
        except TypeError:
            pass
    min_v = getattr(pdef, "min", None)
    max_v = getattr(pdef, "max", None)
    if min_v is not None or max_v is not None:
        if min_v is not None and max_v is not None:
            parts.append(f"Range: {min_v} to {max_v}.")
        elif min_v is not None:
            parts.append(f"Minimum: {min_v}.")
        else:
            parts.append(f"Maximum: {max_v}.")
    return " ".join(parts)


def param_group_for(pdef: ParamDef) -> str:
    """Return the simple UI group for a ParamDef: ``Basic`` or ``Advanced``."""
    advanced_names = {
        "session_filter",
        "denominator_includes_current",
        "z_score",
        "compare_symbol",
        "anchor_ts",
    }
    if str(getattr(pdef, "name", "")) in advanced_names:
        return "Advanced"
    return "Basic"


def validate_param_value(pdef: ParamDef, raw: Any) -> tuple[bool, Any, str]:
    """Validate/coerce one ParamDef value.

    Returns ``(ok, value, message)``. ``value`` is the coerced value on
    success and ``pdef.default`` on failure.
    """
    kind = getattr(pdef, "kind", "str")
    label = label_text_for(pdef).rstrip(":")
    default = getattr(pdef, "default", None)

    if kind == "int":
        try:
            numeric = float(raw)
        except (TypeError, ValueError, OverflowError):
            return False, default, f"Enter a whole number for {label}."
        if not math.isfinite(numeric) or not numeric.is_integer():
            return False, default, f"Enter a whole number for {label}."
        value = int(numeric)
        return _validate_numeric_bounds(pdef, value, label)

    if kind == "float":
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError):
            return False, default, f"Enter a number for {label}."
        if not math.isfinite(value):
            return False, default, f"Enter a finite number for {label}."
        return _validate_numeric_bounds(pdef, value, label)

    if kind == "choice":
        choices = tuple(getattr(pdef, "choices", ()) or ())
        if raw in choices:
            return True, raw, ""
        return False, default, f"Choose a valid {label}."

    if kind == "bool":
        return True, bool(raw), ""

    return True, raw, ""


def _format_bound(value: Any) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f.is_integer():
        return str(int(f))
    return f"{f:g}"


def _validate_numeric_bounds(pdef: ParamDef, value: int | float, label: str) -> tuple[bool, Any, str]:
    min_v = getattr(pdef, "min", None)
    max_v = getattr(pdef, "max", None)
    default = getattr(pdef, "default", None)
    if min_v is not None and value < min_v:
        return (
            False,
            default,
            f"Enter {label} greater than or equal to {_format_bound(min_v)}.",
        )
    if max_v is not None and value > max_v:
        return (
            False,
            default,
            f"Enter {label} less than or equal to {_format_bound(max_v)}.",
        )
    return True, value, ""


def _format_anchor_label(ts: str) -> str:
    """Mirror of ``indicator_dialog._format_anchor_label``.

    Kept local to avoid a circular import (indicator_dialog imports
    this module). The two implementations MUST stay byte-identical;
    if you change one, change the other. Tests pin the behaviour.
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


def build_param_widget(
    parent: tk.Misc,
    pdef: ParamDef,
    seed: Any,
    *,
    on_change: Callable[[], None] | None = None,
    on_commit_eager: Callable[[], None] | None = None,
    commit_policy: CommitPolicy = "eager",
    debounce_ms: int = 250,
    choices_override: tuple[Any, ...] | None = None,
    width: int | None = None,
    anchor_pick_callback: Callable[[], None] | None = None,
) -> tuple[tk.Variable, tk.Widget]:
    """Build the right Tk widget for ``pdef`` and return ``(var, widget)``.

    The caller is responsible for placing the returned ``widget`` via
    ``grid()`` / ``pack()`` and for rendering the label (see
    :func:`label_text_for`).

    Commit policy
    -------------

    * ``"eager"`` — ``on_change`` fires on every ``trace_add('write')``
      event (typing, arrow click, combobox pick, checkbox flip).
    * ``"debounced"`` — typing coalesces into one call ``debounce_ms``
      after the last variable write. Discrete commit events
      (Checkbutton ``command=``, Combobox ``<<ComboboxSelected>>``,
      Spinbox ``command=``) fire ``on_commit_eager`` immediately if
      provided, else fall back to firing the debounced ``on_change``
      path. Use ``on_commit_eager`` to preserve the
      indicator-dialog UX where clicking a checkbox commits
      instantly but typing a number coalesces.
    * ``"on_focus_out"`` — ``on_change`` fires only on ``<FocusOut>``
      or ``<Return>``. Useful for free-text fields where every
      keystroke shouldn't fire downstream work.
    * ``"manual"`` — no callback is wired. The caller is responsible
      for consuming ``var.get()`` on its own schedule.

    ``choices_override`` lets the caller swap out the
    ``pdef.choices`` enumeration without mutating the ParamDef
    (e.g. scope-pinned indicator name list).

    Handles every kind in :data:`PARAM_KINDS` — ``bool`` / ``choice``
    / ``int`` / ``float`` / ``str``. The ``anchor_ts`` special-case
    (legacy hack in ``indicator_dialog``) is preserved here when
    ``pdef.kind == "str"`` AND ``pdef.name == "anchor_ts"``: the
    helper builds the ``Pick Anchor…`` Button + read-only label
    pair, wires the var → label re-format trace, and uses
    ``anchor_pick_callback`` for the button's command (no-op when
    omitted). The returned ``widget`` is a ``ttk.Frame`` holding
    the label + button cluster.
    """
    kind = getattr(pdef, "kind", "str")

    # anchor_ts special case (legacy Anchored VWAP hack).
    if kind == "str" and getattr(pdef, "name", "") == "anchor_ts":
        return _build_anchor_ts(parent, pdef, seed, anchor_pick_callback)

    eff_width = width if width is not None else _DEFAULT_WIDTH_BY_KIND.get(kind, 14)

    if kind == "bool":
        var: tk.Variable = tk.BooleanVar(value=bool(seed))
        widget: tk.Widget = ttk.Checkbutton(parent, variable=var)
    elif kind == "choice":
        var = tk.StringVar(value=str(seed))
        raw_choices = choices_override if choices_override is not None else pdef.choices
        widget = ttk.Combobox(
            parent, textvariable=var,
            state="readonly",
            values=tuple(str(c) for c in (raw_choices or ())),
            width=eff_width,
        )
    elif kind == "str" and (choices_override is not None or getattr(pdef, "choices", ())):
        raw_choices = choices_override if choices_override is not None else pdef.choices
        var = tk.StringVar(value=str(seed))
        widget = ttk.Combobox(
            parent, textvariable=var,
            state="normal",
            values=tuple(str(c) for c in (raw_choices or ())),
            width=eff_width,
        )
    elif kind in ("int", "float"):
        var = tk.StringVar(value=_format_seed(seed))
        kwargs: dict[str, Any] = {
            "textvariable": var, "width": eff_width,
            "from_": pdef.min if pdef.min is not None else -1e12,
            "to":    pdef.max if pdef.max is not None else  1e12,
            "increment": pdef.step if pdef.step is not None else (1 if kind == "int" else 0.1),
        }
        widget = ttk.Spinbox(parent, **kwargs)
    else:  # "str"
        var = tk.StringVar(value=str(seed))
        widget = ttk.Entry(parent, textvariable=var, width=eff_width)

    _wire_commit_policy(widget, var, on_change, commit_policy, debounce_ms, on_commit_eager)
    return var, widget


def _format_seed(seed: Any) -> str:
    """Format a numeric seed without trailing zeros for ints.

    Mirrors :func:`scanner_block_editor._format_number` so the two
    consumers render identical initial text.
    """
    try:
        f = float(seed)
    except (TypeError, ValueError):
        return str(seed)
    if f.is_integer():
        return str(int(f))
    return f"{f:g}"


def _build_anchor_ts(
    parent: tk.Misc,
    pdef: ParamDef,
    seed: Any,
    pick_callback: Callable[[], None] | None,
) -> tuple[tk.Variable, tk.Widget]:
    """Anchor_ts: read-only label + Pick Anchor… button pair.

    Returns ``(var, button)`` — the StringVar holds the raw ISO
    timestamp (mutated by external pick flow), the button is the
    placeable widget. A sibling label is created and gridded inside
    a synthetic frame attached as ``widget.master``'s child so the
    caller's pack/grid lands the button cluster — but to keep the
    helper's "place the widget yourself" contract intact, we wrap
    label+button in a frame and return the frame as the widget.
    """
    wrap = ttk.Frame(parent)
    var = tk.StringVar(value=str(seed))
    display = tk.StringVar(value=_format_anchor_label(str(seed)))
    lbl = ttk.Label(wrap, textvariable=display, width=18)
    lbl.pack(side="left", padx=(0, 4))
    btn = ttk.Button(
        wrap, text="Pick Anchor…",
        command=(pick_callback if pick_callback is not None else (lambda: None)),
    )
    btn.pack(side="left")
    var.trace_add(
        "write",
        lambda *_a, v=var, d=display: d.set(_format_anchor_label(v.get())),
    )
    return var, wrap


def _wire_commit_policy(
    widget: tk.Widget,
    var: tk.Variable,
    on_change: Callable[[], None] | None,
    policy: CommitPolicy,
    debounce_ms: int,
    on_commit_eager: Callable[[], None] | None = None,
) -> None:
    """Bind ``on_change`` / ``on_commit_eager`` to ``var`` / ``widget``
    per ``policy``.

    For ``"debounced"`` policy, discrete commit events use
    ``on_commit_eager`` when provided (fires immediately) and fall
    back to ``on_change`` (debounced) otherwise.
    """
    if on_change is None or policy == "manual":
        return
    if policy == "eager":
        var.trace_add("write", lambda *_a: on_change())
        return
    if policy == "debounced":
        state = {"after_id": None}

        def _fire() -> None:
            state["after_id"] = None
            try:
                on_change()
            except tk.TclError:
                pass

        def _schedule(*_a: Any) -> None:
            prev = state["after_id"]
            if prev is not None:
                try:
                    widget.after_cancel(prev)
                except tk.TclError:
                    pass
            try:
                state["after_id"] = widget.after(debounce_ms, _fire)
            except tk.TclError:
                state["after_id"] = None

        var.trace_add("write", _schedule)
        eager = on_commit_eager if on_commit_eager is not None else on_change
        if isinstance(widget, ttk.Checkbutton):
            widget.configure(command=eager)
        elif isinstance(widget, ttk.Combobox):
            widget.bind("<<ComboboxSelected>>", lambda _e: eager())
        elif isinstance(widget, ttk.Spinbox):
            widget.configure(command=eager)
        return
    if policy == "on_focus_out":
        widget.bind("<FocusOut>", lambda _e: on_change())
        widget.bind("<Return>",   lambda _e: on_change())
        return
