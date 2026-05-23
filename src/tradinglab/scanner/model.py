"""Scanner data model: pure dataclasses + JSON round-trip.

A *scan* is a tree of typed blocks. Internally we represent it as a
:class:`ScanDefinition` whose ``root`` is a :class:`Group` of
:class:`Condition` and nested :class:`Group` children, combined with
``"and"`` / ``"or"``.

Persistence shape::

    {
      "schema_version": 1,
      "id": "<uuid>",
      "name": "Strong RVOL setup",
      "rank_by": {"kind":"indicator","id":"rvol","params":{"mode":"cumulative"}},
      "rank_dir": "desc",
      "primary_interval": "5m",
      "universe_filter": {"kind":"all"},
      "options": {"show_insufficient_data_rows": false, "default_view": "new"},
      "output_columns": null,                     // null = auto from leaves
      "root": {
        "type": "group", "id": "<uuid>", "combinator": "and", "enabled": true,
        "children": [
          {"type": "condition", "id": "<uuid>", "enabled": true,
           "interval": "5m",
           "left":  {"kind":"indicator","id":"rvol","params":{"mode":"cumulative"}},
           "op":    ">",
           "params":{"right": {"kind":"literal","value":2.0}}},
          ...
        ]
      },
      "created_with": {"app":"tradinglab","version":"..."},
      "created_at":  "2026-05-04T...",
      "updated_at":  "2026-05-04T..."
    }

The model layer is **pure data**: it does not interpret operators, does
not look up fields against the registry, and does not evaluate
anything. It only enforces structural well-formedness and round-trips
through JSON without losing information.

**Design decisions** (from rubber-duck + SWE critique):

- Every operator carries its arguments in a **named** ``params`` dict
  (``right``, ``target``, ``tolerance_pct``, ``lookback``, ``bars``,
  ``low``/``high``, ``n``, ``reference``). No positional ``args`` —
  forward-compat for new operator parameters.
- Every :class:`Condition` and :class:`Group` has a stable UUID ``id``.
  Treeview columns, match-history rings, and per-leaf UI state reference
  these IDs so user edits don't break downstream state.
- :class:`FieldRef` carries an optional ``interval`` override; v1 engine
  rejects non-null overrides (``NotImplementedError``) but the field
  is persisted so v2 can light it up without re-migrating saved scans.
- :class:`ScanOptions` is typed; an ``extra`` dict captures forward-compat
  unknown keys instead of an untyped bag for everything.
- :class:`UniverseFilter` is always an object; ``kind`` discriminates
  ``"all"`` / ``"watchlist"`` / ``"symbols"``. No ``null``.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from dataclasses import field as dc_field
from datetime import datetime, timezone
from typing import Any, ClassVar

# Indicator kind_id migrations live in the indicator base module so they
# stay co-located with the registry. Imported lazily inside FieldRef.from_dict
# to avoid a circular import (scanner.fields imports this module).

# Schema version. Bump whenever the on-disk JSON shape changes in a way
# that requires a migration. See :func:`migrate`.
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Operators — names + per-operator named-param schemas
# ---------------------------------------------------------------------------

# Operator string identifiers. Persistence-stable. New operators MUST be
# appended; existing operators MUST NOT be renamed without a migration.
OP_GT  = ">"
OP_LT  = "<"
OP_GE  = ">="
OP_LE  = "<="
OP_EQ  = "=="
OP_NE  = "!="
OP_BETWEEN        = "between"
OP_CROSSES_ABOVE  = "crosses_above"
OP_CROSSES_BELOW  = "crosses_below"
OP_IS_RISING      = "is_rising"
OP_IS_FALLING     = "is_falling"
OP_WITHIN_PCT     = "within_pct"
OP_NEW_HIGH_N     = "new_high_n_bars"
OP_NEW_LOW_N      = "new_low_n_bars"
OP_HOLDING_ABOVE  = "holding_above"
OP_HOLDING_BELOW  = "holding_below"
OP_INSIDE_BAR     = "inside_bar"
OP_OUTSIDE_BAR    = "outside_bar"
OP_NR7            = "nr7"


#: Map operator id → ordered tuple of ``(param_name, param_kind)``. The
#: order is for UI rendering; the param dict on a Condition is keyed by
#: ``param_name`` and order does not matter for evaluation.
#:
#: ``param_kind`` is one of:
#:   - ``"field"``  — value is a :class:`FieldRef` (any kind).
#:   - ``"int"``    — value is a Python ``int`` (e.g. lookback, n, bars).
#:   - ``"float"``  — value is a Python ``float`` (e.g. tolerance_pct).
#:
#: The engine uses this map for validation; the GUI uses it to render
#: the per-operator param row in the block editor.
OPERATOR_PARAM_SCHEMA: dict[str, tuple[tuple[str, str], ...]] = {
    OP_GT:  (("right", "field"),),
    OP_LT:  (("right", "field"),),
    OP_GE:  (("right", "field"),),
    OP_LE:  (("right", "field"),),
    OP_EQ:  (("right", "field"),),
    OP_NE:  (("right", "field"),),
    OP_BETWEEN:        (("low",  "field"), ("high", "field")),
    OP_CROSSES_ABOVE:  (("right", "field"), ("lookback", "int")),
    OP_CROSSES_BELOW:  (("right", "field"), ("lookback", "int")),
    OP_IS_RISING:      (("lookback", "int"),),
    OP_IS_FALLING:     (("lookback", "int"),),
    OP_WITHIN_PCT:     (("target", "field"), ("tolerance_pct", "float")),
    OP_NEW_HIGH_N:     (("n", "int"),),
    OP_NEW_LOW_N:      (("n", "int"),),
    OP_HOLDING_ABOVE:  (("reference", "field"), ("bars", "int")),
    OP_HOLDING_BELOW:  (("reference", "field"), ("bars", "int")),
    OP_INSIDE_BAR:     (),
    OP_OUTSIDE_BAR:    (),
    OP_NR7:            (),
}

ALL_OPERATORS: tuple[str, ...] = tuple(OPERATOR_PARAM_SCHEMA.keys())


def operator_param_schema(op: str) -> tuple[tuple[str, str], ...]:
    """Return the named-param schema for ``op``. Raise on unknown op."""
    if op not in OPERATOR_PARAM_SCHEMA:
        raise ValueError(f"unknown operator: {op!r}")
    return OPERATOR_PARAM_SCHEMA[op]


# ---------------------------------------------------------------------------
# Within-last-N-bars modifier
# ---------------------------------------------------------------------------
#
# Per-Condition / per-Group temporal quantifier. Lets users author setups
# like "EMA3 crossed_below EMA8 within last 2 bars AND red key bar now".
#
# * ``within_last_bars`` (int, default 0): how many bars BACK from the
#   current bar to walk. ``0`` ≡ today's behavior (current bar only).
#   ``N=2`` walks the closed range ``[i-2, i]`` (3 bars total, including
#   the current bar). UI label reads "looking back N bars".
# * ``within_last_mode`` (str, default ``"any"``):
#     - ``"any"``: True if the inner predicate is True on ANY bar in
#       the window. Bread-and-butter mode.
#     - ``"all"``: True only if the inner predicate is True on EVERY
#       bar in the window. Meaningless for transition operators
#       (``crosses_above``/``crosses_below``); the GUI hides it for them.
#     - ``"exactly"``: True only if the inner predicate is True at
#       exactly bar ``i - N`` (the oldest bar in the window). Useful
#       for "inside_bar exactly 2 bars ago AND breakout now" setups.
#
# Persisted only when non-default to keep JSON minimal and to ensure
# legacy files round-trip unchanged. No SCHEMA_VERSION bump needed.
WITHIN_LAST_MODE_ANY = "any"
WITHIN_LAST_MODE_ALL = "all"
WITHIN_LAST_MODE_EXACTLY = "exactly"

WITHIN_LAST_MODES: tuple[str, ...] = (
    WITHIN_LAST_MODE_ANY,
    WITHIN_LAST_MODE_ALL,
    WITHIN_LAST_MODE_EXACTLY,
)


def _validate_within_last(
    container: str, bars: int, mode: str
) -> None:
    """Raise ``ValueError`` if look-back fields are out of range."""
    if not isinstance(bars, int) or isinstance(bars, bool):
        raise ValueError(
            f"{container}.within_last_bars must be a non-negative int, got {bars!r}"
        )
    if bars < 0:
        raise ValueError(
            f"{container}.within_last_bars must be >= 0, got {bars}"
        )
    if mode not in WITHIN_LAST_MODES:
        raise ValueError(
            f"{container}.within_last_mode must be one of "
            f"{sorted(WITHIN_LAST_MODES)}, got {mode!r}"
        )


# ---------------------------------------------------------------------------
# MatchEvidence — per-leaf payload for "fired N bars ago" reporting
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchEvidence:
    """Payload describing WHEN a look-back leaf was satisfied.

    Emitted by the engine for every :class:`Condition` or :class:`Group`
    whose ``within_last_bars > 0`` evaluates to True. Surfaced in scanner
    row tooltips, entries/exits audit logs, and replay overlay markers
    so a trader sees "EMA cross fired 1 bar ago at 10:35".

    * ``node_id``: id of the Condition or Group that produced this
      evidence (matches :attr:`Condition.id` / :attr:`Group.id`).
    * ``bars_ago``: how many bars back from the evaluation index the
      predicate was satisfied. ``0`` = current bar.
        - ``"any"`` mode: bars_ago of the most-recent True bar.
        - ``"all"`` mode: bars_ago of the OLDEST bar in the (all-True)
          window — i.e. the start of the run. Window length is the
          parent's ``within_last_bars``.
        - ``"exactly"`` mode: equals the parent's ``within_last_bars``.
    * ``timestamp``: ISO-8601 string of that bar's open time. Empty
      when the engine cannot resolve a timestamp (defensive).
    * ``value``: LHS field value at the trigger bar, or ``None`` if
      the leaf is a Group (no scalar) or the value is undefined.
    """

    node_id: str
    bars_ago: int
    timestamp: str = ""
    value: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "node_id": self.node_id,
            "bars_ago": int(self.bars_ago),
        }
        if self.timestamp:
            d["timestamp"] = self.timestamp
        if self.value is not None:
            d["value"] = float(self.value)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> MatchEvidence:
        v = d.get("value")
        return cls(
            node_id=str(d["node_id"]),
            bars_ago=int(d["bars_ago"]),
            timestamp=str(d.get("timestamp", "")),
            value=None if v is None else float(v),
        )


# ---------------------------------------------------------------------------
# FieldRef — references a value computable on (symbol, interval, bar_index)
# ---------------------------------------------------------------------------

#: Allowed values for :attr:`FieldRef.kind`.
FIELD_KIND_BUILTIN   = "builtin"
FIELD_KIND_INDICATOR = "indicator"
FIELD_KIND_LITERAL   = "literal"

_FIELD_KINDS = (FIELD_KIND_BUILTIN, FIELD_KIND_INDICATOR, FIELD_KIND_LITERAL)


@dataclass(frozen=True)
class FieldRef:
    """A single value-reference appearing on either side of a comparison.

    Three kinds:

    - **``"builtin"``** — a built-in scalar field (``"close"``, ``"high"``,
      ``"volume"``, ``"pct_change"``, ``"hod"``, ``"time_of_day"``, ...).
      ``params`` is empty; ``output_key`` is empty.
    - **``"indicator"``** — references an entry in the indicator registry
      via stable ``id`` (the ``kind_id``). ``params`` carries the
      indicator's factory parameters (e.g. ``{"length": 50}``).
      ``output_key`` selects one of the indicator's named outputs
      (``"sma"``, ``"upper"``/``"middle"``/``"lower"`` for Bollinger,
      etc.); empty string means the indicator's default output.
    - **``"literal"``** — a numeric constant. ``value`` carries the
      number; all other fields are empty.

    ``interval`` is an **optional override**: when non-null, this field
    is evaluated on a different interval than the parent
    :class:`Condition`'s ``interval``. v1 engine raises
    ``NotImplementedError`` if non-null. The slot is persisted so v2 can
    light it up without re-migrating saved scans.
    """

    kind: str
    id: str = ""
    params: Mapping[str, Any] = dc_field(default_factory=dict)
    output_key: str = ""
    value: float | None = None
    interval: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in _FIELD_KINDS:
            raise ValueError(f"FieldRef.kind must be one of {_FIELD_KINDS}, got {self.kind!r}")
        if self.kind == FIELD_KIND_LITERAL:
            if self.value is None:
                raise ValueError("FieldRef(kind='literal') requires a numeric value")
            if self.id or self.params or self.output_key:
                raise ValueError("FieldRef(kind='literal') must not set id/params/output_key")
        else:
            if not self.id:
                raise ValueError(f"FieldRef(kind={self.kind!r}) requires non-empty id")
            if self.value is not None:
                raise ValueError(f"FieldRef(kind={self.kind!r}) must not set value")
        # Freeze params to a plain dict for predictable serialization.
        object.__setattr__(self, "params", dict(self.params))

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind}
        if self.kind == FIELD_KIND_LITERAL:
            d["value"] = float(self.value)  # type: ignore[arg-type]
        else:
            d["id"] = self.id
            if self.params:
                d["params"] = dict(self.params)
            if self.output_key:
                d["output_key"] = self.output_key
        if self.interval is not None:
            d["interval"] = self.interval
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> FieldRef:
        if not isinstance(d, Mapping):
            raise TypeError(f"FieldRef.from_dict expects a mapping, got {type(d).__name__}")
        kind = d.get("kind")
        if kind == FIELD_KIND_LITERAL:
            return cls(
                kind=FIELD_KIND_LITERAL,
                value=float(d["value"]),
                interval=d.get("interval"),
            )
        ref_id = str(d.get("id", ""))
        params = dict(d.get("params", {}) or {})
        output_key = str(d.get("output_key", ""))
        # Apply legacy indicator kind_id migrations transparently so
        # saved scans / exits / entries that reference folded ids
        # (e.g. ``rvol_cum`` → ``rvol`` with ``mode="cumulative"``)
        # keep working without user action.
        if kind == FIELD_KIND_INDICATOR and ref_id:
            from ..indicators.base import (
                _LEGACY_Z_OUTPUT_KIND_IDS,
                migrate_kind_id,
            )
            original_id = ref_id
            ref_id, params = migrate_kind_id(ref_id, params)
            # Legacy ``rvol_z_*`` indicators emitted the ``"z"`` output
            # key; the unified ``rvol`` emits ``"rvol"``. Remap any
            # explicitly-stored ``output_key="z"`` so persisted scans
            # don't suddenly resolve to a missing output.
            if (original_id in _LEGACY_Z_OUTPUT_KIND_IDS
                    and output_key == "z"):
                output_key = "rvol"
            # NOTE: the SMA/EMA -> MovingAverage collapse intentionally
            # does NOT migrate scanner FieldRefs. The scanner allowlist
            # (``SCANNABLE_INDICATORS`` in ``scanner.fields``) keeps
            # ``"sma"`` and ``"ema"`` as separate scannable field ids
            # backed by the legacy registry entries (preserved via
            # ``register_legacy_indicator``). Chart-side indicator
            # configs DO migrate -- see ``IndicatorConfig.from_dict``.
        return cls(
            kind=str(kind),
            id=ref_id,
            params=params,
            output_key=output_key,
            interval=d.get("interval"),
        )

    # -- convenience ---------------------------------------------------------

    @classmethod
    def literal(cls, value: float) -> FieldRef:
        return cls(kind=FIELD_KIND_LITERAL, value=float(value))

    @classmethod
    def builtin(cls, id: str, *, output_key: str = "",
                interval: str | None = None) -> FieldRef:
        return cls(kind=FIELD_KIND_BUILTIN, id=id, output_key=output_key,
                   interval=interval)

    @classmethod
    def indicator(cls, id: str, *, params: Mapping[str, Any] | None = None,
                  output_key: str = "", interval: str | None = None) -> FieldRef:
        return cls(kind=FIELD_KIND_INDICATOR, id=id,
                   params=dict(params or {}), output_key=output_key,
                   interval=interval)


# ---------------------------------------------------------------------------
# Operator-param value (FieldRef | int | float)
# ---------------------------------------------------------------------------

# Values appearing in :attr:`Condition.params`. Per-operator schemas
# (see :data:`OPERATOR_PARAM_SCHEMA`) say which named slot expects which
# kind. We persist FieldRef-valued slots as nested dicts and primitive
# slots as raw JSON numbers.
ParamValue = FieldRef | int | float


def _serialize_param_value(v: ParamValue) -> Any:
    if isinstance(v, FieldRef):
        return v.to_dict()
    if isinstance(v, bool):
        # Reject explicitly: bool is a subclass of int but a logic-typed
        # primitive in the schema would be footgun-prone.
        raise TypeError("operator param values must be FieldRef | int | float (got bool)")
    if isinstance(v, (int, float)):
        return v
    raise TypeError(f"unsupported operator param value: {type(v).__name__}")


def _deserialize_param_value(v: Any) -> ParamValue:
    if isinstance(v, Mapping) and "kind" in v:
        return FieldRef.from_dict(v)
    if isinstance(v, bool):
        raise TypeError("operator param value cannot be a bool")
    if isinstance(v, (int, float)):
        return v
    raise TypeError(f"cannot deserialize param value of type {type(v).__name__}")


# ---------------------------------------------------------------------------
# Condition + Group — the expression tree
# ---------------------------------------------------------------------------


def _new_id() -> str:
    """Return a fresh UUID4 string. Centralized so tests can monkeypatch."""
    return str(uuid.uuid4())


@dataclass
class Condition:
    """One leaf comparison: ``left <op> params``.

    ``params`` is a dict whose keys match the operator's named-param
    schema (see :data:`OPERATOR_PARAM_SCHEMA`). Values are :class:`FieldRef`
    for ``"field"``-typed slots, ``int`` for ``"int"``-typed slots, and
    ``float`` for ``"float"``-typed slots. The model layer enforces only
    that the keys match the schema; the engine validates value types.

    ``interval`` is the default evaluation interval for this condition's
    operands (e.g. ``"5m"``, ``"1d"``). Any :class:`FieldRef` whose own
    ``interval`` override is non-null will (in v2) be evaluated on that
    interval instead.
    """

    left: FieldRef
    op: str
    params: dict[str, ParamValue] = dc_field(default_factory=dict)
    interval: str = "5m"
    enabled: bool = True
    id: str = dc_field(default_factory=_new_id)
    # Reserved for future inline notes; persisted only when non-empty.
    comment: str = ""
    # Within-last-N-bars temporal quantifier. See "Within-last-N-bars
    # modifier" section above for semantics. Persisted only when
    # non-default to keep legacy JSON byte-identical.
    within_last_bars: int = 0
    within_last_mode: str = WITHIN_LAST_MODE_ANY

    type: ClassVar[str] = "condition"

    def __post_init__(self) -> None:
        if self.op not in OPERATOR_PARAM_SCHEMA:
            raise ValueError(f"Condition.op unknown: {self.op!r}")
        if not self.id:
            self.id = _new_id()
        # Validate that param keys match the operator's schema. Don't
        # enforce value types here — the engine does that with full
        # field-registry context.
        expected = {name for name, _kind in OPERATOR_PARAM_SCHEMA[self.op]}
        provided = set(self.params.keys())
        unknown = provided - expected
        if unknown:
            raise ValueError(
                f"Condition({self.op!r}): unknown param(s) {sorted(unknown)}; "
                f"expected {sorted(expected)}"
            )
        missing = expected - provided
        if missing:
            raise ValueError(
                f"Condition({self.op!r}): missing required param(s) {sorted(missing)}"
            )
        _validate_within_last("Condition", self.within_last_bars, self.within_last_mode)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": self.type,
            "id": self.id,
            "enabled": self.enabled,
            "interval": self.interval,
            "left": self.left.to_dict(),
            "op": self.op,
            "params": {k: _serialize_param_value(v) for k, v in self.params.items()},
        }
        if self.comment:
            d["comment"] = self.comment
        if self.within_last_bars:
            d["within_last_bars"] = int(self.within_last_bars)
        if self.within_last_mode != WITHIN_LAST_MODE_ANY:
            d["within_last_mode"] = self.within_last_mode
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Condition:
        if d.get("type") != "condition":
            raise ValueError(f"Condition.from_dict: expected type='condition', got {d.get('type')!r}")
        params_raw = d.get("params") or {}
        params: dict[str, ParamValue] = {
            k: _deserialize_param_value(v) for k, v in params_raw.items()
        }
        return cls(
            id=str(d.get("id") or _new_id()),
            enabled=bool(d.get("enabled", True)),
            interval=str(d.get("interval", "5m")),
            left=FieldRef.from_dict(d["left"]),
            op=str(d["op"]),
            params=params,
            comment=str(d.get("comment", "")),
            within_last_bars=int(d.get("within_last_bars", 0)),
            within_last_mode=str(d.get("within_last_mode", WITHIN_LAST_MODE_ANY)),
        )


@dataclass
class Group:
    """Internal node combining children with ``"and"`` or ``"or"``.

    Children may be :class:`Condition` leaves or nested :class:`Group`
    nodes. An empty group evaluates to ``None`` (insufficient data) — see
    engine semantics.
    """

    combinator: str = "and"
    children: list[Condition | Group] = dc_field(default_factory=list)
    enabled: bool = True
    id: str = dc_field(default_factory=_new_id)
    # Within-last-N-bars temporal quantifier — applied to the entire
    # subtree's evaluation. Lets users author "(EMA cross AND volume
    # spike) on the SAME bar, anywhere in the last N bars" — strictly
    # more expressive than per-Condition look-back. See "Within-last-
    # N-bars modifier" section above for semantics.
    within_last_bars: int = 0
    within_last_mode: str = WITHIN_LAST_MODE_ANY

    type: ClassVar[str] = "group"

    def __post_init__(self) -> None:
        if self.combinator not in ("and", "or"):
            raise ValueError(f"Group.combinator must be 'and' or 'or', got {self.combinator!r}")
        if not self.id:
            self.id = _new_id()
        _validate_within_last("Group", self.within_last_bars, self.within_last_mode)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": self.type,
            "id": self.id,
            "enabled": self.enabled,
            "combinator": self.combinator,
            "children": [c.to_dict() for c in self.children],
        }
        if self.within_last_bars:
            d["within_last_bars"] = int(self.within_last_bars)
        if self.within_last_mode != WITHIN_LAST_MODE_ANY:
            d["within_last_mode"] = self.within_last_mode
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Group:
        if d.get("type") != "group":
            raise ValueError(f"Group.from_dict: expected type='group', got {d.get('type')!r}")
        children: list[Condition | Group] = []
        for c in d.get("children", ()):
            ctype = c.get("type") if isinstance(c, Mapping) else None
            if ctype == "condition":
                children.append(Condition.from_dict(c))
            elif ctype == "group":
                children.append(Group.from_dict(c))
            else:
                raise ValueError(f"Group child has unknown type: {ctype!r}")
        return cls(
            id=str(d.get("id") or _new_id()),
            enabled=bool(d.get("enabled", True)),
            combinator=str(d.get("combinator", "and")),
            children=children,
            within_last_bars=int(d.get("within_last_bars", 0)),
            within_last_mode=str(d.get("within_last_mode", WITHIN_LAST_MODE_ANY)),
        )


# ---------------------------------------------------------------------------
# UniverseFilter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UniverseFilter:
    """Restricts the symbol set a scan runs over.

    - ``kind="all"``       — entire preloaded universe (default).
    - ``kind="watchlist"`` — symbols belonging to a saved watchlist;
                              ``name`` is the watchlist name.
    - ``kind="symbols"``   — explicit symbol list in ``symbols``.
    """

    kind: str = "all"
    name: str = ""
    symbols: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in ("all", "watchlist", "symbols"):
            raise ValueError(f"UniverseFilter.kind invalid: {self.kind!r}")
        if self.kind == "watchlist" and not self.name:
            raise ValueError("UniverseFilter(watchlist) requires a non-empty name")
        if self.kind == "symbols" and not self.symbols:
            raise ValueError("UniverseFilter(symbols) requires a non-empty list")
        # Normalize symbols tuple.
        object.__setattr__(self, "symbols", tuple(s.upper() for s in self.symbols))

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind}
        if self.kind == "watchlist":
            d["name"] = self.name
        elif self.kind == "symbols":
            d["symbols"] = list(self.symbols)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> UniverseFilter:
        kind = str(d.get("kind", "all"))
        return cls(
            kind=kind,
            name=str(d.get("name", "")),
            symbols=tuple(d.get("symbols", ()) or ()),
        )

    @classmethod
    def all(cls) -> UniverseFilter:
        return cls(kind="all")


# ---------------------------------------------------------------------------
# OutputColumn
# ---------------------------------------------------------------------------

OUTPUT_COL_CONDITION_VALUE = "condition_value"
OUTPUT_COL_FIELD           = "field"


@dataclass
class OutputColumn:
    """One Treeview column in a scan's results table.

    Two kinds:

    - ``"condition_value"`` — references a leaf condition by
      ``condition_id``. The column shows the LHS metric value that the
      engine computed for that condition. Renames/edits to the condition
      preserve column identity through the stable id.
    - ``"field"`` — references an arbitrary :class:`FieldRef` evaluated
      on ``interval``. Useful when the user wants to see a value that
      isn't part of the match logic (e.g. ATR for risk sizing).
    """

    kind: str
    label: str = ""
    visible: bool = True
    condition_id: str = ""
    field: FieldRef | None = None
    interval: str = ""
    id: str = dc_field(default_factory=_new_id)

    def __post_init__(self) -> None:
        if self.kind == OUTPUT_COL_CONDITION_VALUE:
            if not self.condition_id:
                raise ValueError("OutputColumn(condition_value) requires condition_id")
        elif self.kind == OUTPUT_COL_FIELD:
            if self.field is None:
                raise ValueError("OutputColumn(field) requires a field FieldRef")
        else:
            raise ValueError(f"OutputColumn.kind invalid: {self.kind!r}")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "visible": self.visible,
        }
        if self.kind == OUTPUT_COL_CONDITION_VALUE:
            d["condition_id"] = self.condition_id
        else:
            d["field"] = self.field.to_dict()  # type: ignore[union-attr]
            d["interval"] = self.interval
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> OutputColumn:
        kind = str(d.get("kind", ""))
        if kind == OUTPUT_COL_CONDITION_VALUE:
            return cls(
                id=str(d.get("id") or _new_id()),
                kind=kind,
                label=str(d.get("label", "")),
                visible=bool(d.get("visible", True)),
                condition_id=str(d["condition_id"]),
            )
        if kind == OUTPUT_COL_FIELD:
            return cls(
                id=str(d.get("id") or _new_id()),
                kind=kind,
                label=str(d.get("label", "")),
                visible=bool(d.get("visible", True)),
                field=FieldRef.from_dict(d["field"]),
                interval=str(d.get("interval", "")),
            )
        raise ValueError(f"OutputColumn.from_dict: unknown kind {kind!r}")


# ---------------------------------------------------------------------------
# ScanOptions
# ---------------------------------------------------------------------------

VIEW_NEW    = "new"     # edge-triggered: new false→true transitions
VIEW_ACTIVE = "active"  # current-state: rows currently matching


@dataclass
class ScanOptions:
    """Per-scan behavior knobs.

    ``extra`` captures unknown keys round-trip-safely so a future build
    that adds a new option doesn't strip the value when an older build
    saves the file. Don't bury behavioral knobs in ``extra`` — they live
    as typed attributes here.
    """

    show_insufficient_data_rows: bool = False
    default_view: str = VIEW_NEW
    new_view_capacity: int = 500
    extra: dict[str, Any] = dc_field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.default_view not in (VIEW_NEW, VIEW_ACTIVE):
            raise ValueError(f"ScanOptions.default_view invalid: {self.default_view!r}")
        if self.new_view_capacity < 1:
            raise ValueError("ScanOptions.new_view_capacity must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "show_insufficient_data_rows": self.show_insufficient_data_rows,
            "default_view": self.default_view,
            "new_view_capacity": self.new_view_capacity,
        }
        if self.extra:
            d["extra"] = dict(self.extra)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ScanOptions:
        known = {"show_insufficient_data_rows", "default_view", "new_view_capacity", "extra"}
        return cls(
            show_insufficient_data_rows=bool(d.get("show_insufficient_data_rows", False)),
            default_view=str(d.get("default_view", VIEW_NEW)),
            new_view_capacity=int(d.get("new_view_capacity", 500)),
            extra={**dict(d.get("extra", {}) or {}),
                   **{k: v for k, v in d.items() if k not in known}},
        )


# ---------------------------------------------------------------------------
# ScanDefinition
# ---------------------------------------------------------------------------

RANK_DIR_DESC = "desc"
RANK_DIR_ASC  = "asc"


@dataclass
class CreatedWith:
    """Audit metadata identifying the build that created/edited the scan."""
    app: str = "tradinglab"
    version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"app": self.app, "version": self.version}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> CreatedWith:
        return cls(app=str(d.get("app", "tradinglab")),
                   version=str(d.get("version", "")))


@dataclass
class ScanDefinition:
    """The complete description of one saved scan."""

    name: str
    root: Group
    primary_interval: str = "5m"
    universe_filter: UniverseFilter = dc_field(default_factory=UniverseFilter.all)
    output_columns: list[OutputColumn] | None = None
    options: ScanOptions = dc_field(default_factory=ScanOptions)
    rank_by: FieldRef | None = None
    rank_dir: str = RANK_DIR_DESC
    rank_interval: str = ""              # "" → use primary_interval
    schema_version: int = SCHEMA_VERSION
    id: str = dc_field(default_factory=_new_id)
    created_with: CreatedWith = dc_field(default_factory=CreatedWith)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ScanDefinition.name is required")
        if self.rank_dir not in (RANK_DIR_ASC, RANK_DIR_DESC):
            raise ValueError(f"ScanDefinition.rank_dir invalid: {self.rank_dir!r}")
        now = _utcnow_iso()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "primary_interval": self.primary_interval,
            "universe_filter": self.universe_filter.to_dict(),
            "options": self.options.to_dict(),
            "rank_dir": self.rank_dir,
            "rank_interval": self.rank_interval,
            "root": self.root.to_dict(),
            "created_with": self.created_with.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.rank_by is not None:
            d["rank_by"] = self.rank_by.to_dict()
        if self.output_columns is None:
            d["output_columns"] = None
        else:
            d["output_columns"] = [c.to_dict() for c in self.output_columns]
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ScanDefinition:
        d = migrate(d, int(d.get("schema_version", 1)))
        cols_raw = d.get("output_columns", None)
        cols: list[OutputColumn] | None
        if cols_raw is None:
            cols = None
        else:
            cols = [OutputColumn.from_dict(c) for c in cols_raw]
        return cls(
            schema_version=int(d.get("schema_version", SCHEMA_VERSION)),
            id=str(d.get("id") or _new_id()),
            name=str(d["name"]),
            primary_interval=str(d.get("primary_interval", "5m")),
            universe_filter=UniverseFilter.from_dict(d.get("universe_filter", {"kind": "all"})),
            output_columns=cols,
            options=ScanOptions.from_dict(d.get("options", {})),
            rank_by=(FieldRef.from_dict(d["rank_by"]) if d.get("rank_by") else None),
            rank_dir=str(d.get("rank_dir", RANK_DIR_DESC)),
            rank_interval=str(d.get("rank_interval", "")),
            root=Group.from_dict(d["root"]),
            created_with=CreatedWith.from_dict(d.get("created_with", {})),
            created_at=str(d.get("created_at", "")),
            updated_at=str(d.get("updated_at", "")),
        )

    # -- helpers -------------------------------------------------------------

    def touch(self) -> ScanDefinition:
        """Return a copy with ``updated_at`` set to now."""
        return replace(self, updated_at=_utcnow_iso())

    def all_conditions(self) -> list[Condition]:
        """Depth-first list of every leaf :class:`Condition` in the tree."""
        out: list[Condition] = []
        _walk_conditions(self.root, out)
        return out


def _walk_conditions(node: Condition | Group, out: list[Condition]) -> None:
    if isinstance(node, Condition):
        out.append(node)
        return
    for c in node.children:
        _walk_conditions(c, out)


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


def migrate(d: Mapping[str, Any], from_version: int) -> dict[str, Any]:
    """Migrate a raw scan dict from ``from_version`` to :data:`SCHEMA_VERSION`.

    Currently a no-op (we are at v1). Future migrations chain through
    here in order. Returns a *new* dict — never mutates the input.

    Always raises :class:`ValueError` if the version is newer than this
    build understands (forward compat is not safe).
    """
    if from_version > SCHEMA_VERSION:
        raise ValueError(
            f"scan schema version {from_version} is newer than this build "
            f"supports ({SCHEMA_VERSION}); refusing to load"
        )
    out = dict(d)
    # No migrations yet — every version chain step would mutate `out`
    # in-place and bump out["schema_version"].
    out["schema_version"] = SCHEMA_VERSION
    return out


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    """ISO-8601 UTC timestamp, second-resolution, with trailing ``Z``."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
