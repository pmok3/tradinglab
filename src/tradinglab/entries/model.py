"""Pure-logic data model for entry strategies.

This module owns the on-disk and in-memory schema for entry strategies.
It is Tk-free and side-effect-free.

An :class:`EntryStrategy` is fundamentally simpler than an
:class:`tradinglab.exits.model.ExitStrategy`:

- One :class:`EntryTrigger` per strategy (no leg/OCO machinery — entries
  fire once and create a position; there is no "partial profit-take leg"
  concept at entry time).
- A :class:`SizingRule` that determines qty at fire time.
- A :class:`Universe` that determines which symbols the strategy watches.
- An optional ``on_fill_exit_ids`` list of exit-strategy ids to bind to
  the new position when it opens (the bracket-on-fill pattern).
- Lifecycle gates: ``cooldown_secs``, ``max_fires_per_session_per_symbol``,
  arm window, position-already-open policy.

JSON round-trip is provided via :meth:`EntryStrategy.to_dict` /
:meth:`from_dict`. Schema migration scaffolding is in :func:`migrate`.

Validation lives in :func:`validate_strategy` (NOT in ``__post_init__``)
so the GUI can construct half-built strategies during editing. Storage,
load, and arming all call ``validate_strategy`` to refuse invalid state.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..scanner.model import Group as ConditionGroup

__all__ = [
    "TriggerKind",
    "Direction",
    "SizingKind",
    "TimeInForce",
    "OrderSide",
    "PositionAlreadyOpenPolicy",
    "ShareRounding",
    "SizingRule",
    "Universe",
    "EntryTrigger",
    "CreatedWith",
    "EntryStrategy",
    "validate_strategy",
    "migrate",
    "CURRENT_SCHEMA_VERSION",
]


CURRENT_SCHEMA_VERSION: int = 1


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TriggerKind(str, Enum):
    """The six entry trigger kinds.

    ``MARKET`` fires on the next CLOSED bar after arm (``is_close=True``)
    — not on forming-bar updates. ``LIMIT`` / ``STOP`` / ``STOP_LIMIT``
    use touched-through detection symmetric to the exits paper engine.
    ``INDICATOR`` reuses ``scanner.model.Group`` for arbitrary boolean
    expressions on memo'd indicators. ``SCANNER_ALERT`` fires when a
    saved scanner emits a NEW match for a symbol the strategy is
    watching.
    """

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    INDICATOR = "indicator"
    SCANNER_ALERT = "scanner_alert"


class Direction(str, Enum):
    """Entry direction. LONG creates a long position; SHORT a short."""

    LONG = "long"
    SHORT = "short"


class SizingKind(str, Enum):
    """V1 ships only fixed sizing modes.

    Equity-aware modes (``PERCENT_EQUITY`` / ``RISK_FIXED_DOLLAR`` /
    ``ATR_RISK``) are deferred to v2 — they require an Account / Cash
    model that doesn't exist today.
    """

    FIXED_QTY = "fixed_qty"
    FIXED_NOTIONAL = "fixed_notional"


class ShareRounding(str, Enum):
    """How to round a fractional sizing computation to whole shares."""

    DOWN = "down"
    NEAREST = "nearest"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"


class OrderSide(str, Enum):
    """Side of the *entry* order.

    LONG entry => BUY. SHORT entry => SELL_SHORT (treated as a short-open
    on the broker side; the paper engine doesn't distinguish at the
    fill-decision level — direction is carried via the eventual
    :class:`tradinglab.positions.model.PositionSide`).
    """

    BUY = "buy"
    SELL_SHORT = "sell_short"


class PositionAlreadyOpenPolicy(str, Enum):
    """What to do when a position already exists for the symbol+side.

    ``BLOCK`` (default) — skip firing; audit ``entry_blocked``.
    ``STACK`` — open a second independent position. Each strategy sets
    this independently; "block" is per-strategy (Strategy A blocks
    Strategy A from re-firing while A's position is open; A doesn't
    block B).
    """

    BLOCK = "block"
    STACK = "stack"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _as_float_opt(v: Any) -> float | None:
    if v is None:
        return None
    return float(v)


def _as_str_opt(v: Any) -> str | None:
    if v is None:
        return None
    return str(v)


# ---------------------------------------------------------------------------
# SizingRule
# ---------------------------------------------------------------------------


@dataclass
class SizingRule:
    """How many shares to buy / short when the trigger fires.

    Tagged-union semantics: which fields are meaningful depends on
    ``kind``. Validation lives in :func:`validate_strategy`.

    - ``FIXED_QTY``: ``qty`` (positive float; whole or fractional shares).
    - ``FIXED_NOTIONAL``: ``notional`` (dollar amount) — fired qty
      computed at fire time as ``floor(notional / ref_price)`` (or
      ``round(...)`` depending on ``share_rounding``).
    """

    kind: SizingKind = SizingKind.FIXED_QTY
    qty: float | None = None
    notional: float | None = None
    share_rounding: ShareRounding = ShareRounding.DOWN

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind.value,
            "share_rounding": self.share_rounding.value,
        }
        if self.qty is not None:
            out["qty"] = float(self.qty)
        if self.notional is not None:
            out["notional"] = float(self.notional)
        return out

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> SizingRule:
        if not isinstance(d, Mapping):
            raise TypeError(f"SizingRule.from_dict expects mapping, got {type(d).__name__}")
        kind_str = d.get("kind", SizingKind.FIXED_QTY.value)
        try:
            kind = SizingKind(kind_str)
        except ValueError as exc:
            raise ValueError(f"unknown SizingKind: {kind_str!r}") from exc
        rounding_str = d.get("share_rounding", ShareRounding.DOWN.value)
        try:
            rounding = ShareRounding(rounding_str)
        except ValueError as exc:
            raise ValueError(f"unknown ShareRounding: {rounding_str!r}") from exc
        return cls(
            kind=kind,
            qty=_as_float_opt(d.get("qty")),
            notional=_as_float_opt(d.get("notional")),
            share_rounding=rounding,
        )


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------


@dataclass
class Universe:
    """Which symbols this strategy watches.

    Exactly one of :attr:`symbols` (non-empty), :attr:`scanner_id`, or
    :attr:`from_attached_chart` must be set. The constructor is
    permissive (allows partial / empty state for GUI editing);
    :func:`validate_strategy` enforces XOR on save / load / arm.
    """

    symbols: tuple[str, ...] = field(default_factory=tuple)
    scanner_id: str | None = None
    from_attached_chart: bool = False

    def __post_init__(self) -> None:
        # Coerce list -> tuple + uppercase symbols deterministically.
        self.symbols = tuple(s.upper() for s in self.symbols)

    def is_empty(self) -> bool:
        return (
            not self.symbols
            and not self.scanner_id
            and not self.from_attached_chart
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.symbols:
            out["symbols"] = list(self.symbols)
        if self.scanner_id:
            out["scanner_id"] = self.scanner_id
        if self.from_attached_chart:
            out["from_attached_chart"] = True
        return out

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Universe:
        if not isinstance(d, Mapping):
            raise TypeError(f"Universe.from_dict expects mapping, got {type(d).__name__}")
        syms_raw = d.get("symbols", ())
        if isinstance(syms_raw, (list, tuple)):
            syms = tuple(str(s) for s in syms_raw)
        else:
            raise ValueError("Universe.symbols must be a list/tuple")
        return cls(
            symbols=syms,
            scanner_id=_as_str_opt(d.get("scanner_id")),
            from_attached_chart=bool(d.get("from_attached_chart", False)),
        )


# ---------------------------------------------------------------------------
# EntryTrigger
# ---------------------------------------------------------------------------


@dataclass
class EntryTrigger:
    """A single entry condition.

    Tagged-union: which fields are meaningful depends on :attr:`kind`.

    - ``MARKET``: nothing extra (fires on next closed bar after arm).
    - ``LIMIT``: ``price`` (LONG: bar.low <= price; SHORT: bar.high >= price).
    - ``STOP``: ``stop_price`` (LONG: bar.high >= stop; SHORT: bar.low <= stop).
    - ``STOP_LIMIT``: ``stop_price`` + ``price`` (limit ceiling for LONG /
      floor for SHORT).
    - ``INDICATOR``: ``condition`` (a :class:`scanner.model.Group`) +
      ``interval`` (bar interval to evaluate against; ``None`` falls
      back to a default supplied by the evaluator).
    - ``SCANNER_ALERT``: ``scanner_id``; fires when the named scanner
      emits a NEW match for any symbol in the strategy's universe.
    """

    id: str = field(default_factory=_new_id)
    kind: TriggerKind = TriggerKind.MARKET

    # Price family (LIMIT / STOP / STOP_LIMIT)
    price: float | None = None
    stop_price: float | None = None

    # Indicator
    condition: ConditionGroup | None = None
    interval: str | None = None
    evaluate_intrabar: bool = False

    # Scanner alert
    scanner_id: str | None = None

    # Common
    time_in_force: TimeInForce = TimeInForce.DAY
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind.value,
            "time_in_force": self.time_in_force.value,
            "label": self.label,
        }
        for k in ("price", "stop_price", "interval", "scanner_id"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        if self.kind == TriggerKind.INDICATOR:
            out["evaluate_intrabar"] = self.evaluate_intrabar
            if self.condition is not None:
                out["condition"] = self.condition.to_dict()
        return out

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> EntryTrigger:
        if not isinstance(d, Mapping):
            raise TypeError(f"EntryTrigger.from_dict expects mapping, got {type(d).__name__}")
        kind_str = d.get("kind", TriggerKind.MARKET.value)
        try:
            kind = TriggerKind(kind_str)
        except ValueError as exc:
            raise ValueError(f"unknown TriggerKind: {kind_str!r}") from exc

        cond_raw = d.get("condition")
        cond = ConditionGroup.from_dict(cond_raw) if isinstance(cond_raw, Mapping) else None

        return cls(
            id=str(d.get("id") or _new_id()),
            kind=kind,
            price=_as_float_opt(d.get("price")),
            stop_price=_as_float_opt(d.get("stop_price")),
            condition=cond,
            interval=_as_str_opt(d.get("interval")),
            evaluate_intrabar=bool(d.get("evaluate_intrabar", False)),
            scanner_id=_as_str_opt(d.get("scanner_id")),
            time_in_force=TimeInForce(d.get("time_in_force", TimeInForce.DAY.value)),
            label=str(d.get("label", "")),
        )


# ---------------------------------------------------------------------------
# CreatedWith / EntryStrategy
# ---------------------------------------------------------------------------


@dataclass
class CreatedWith:
    app: str = "tradinglab"
    version: str = "0.0.0"
    template: bool = False  # True for prepackaged templates

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"app": self.app, "version": self.version}
        if self.template:
            out["template"] = True
        return out

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> CreatedWith:
        return cls(
            app=str(d.get("app", "tradinglab")),
            version=str(d.get("version", "0.0.0")),
            template=bool(d.get("template", False)),
        )


@dataclass
class EntryStrategy:
    """Top-level entry-strategy aggregate.

    The ``enabled`` field is persistent config: a disabled strategy is
    still in the user's library but cannot be armed. The arm STATE
    (whether a strategy is currently active and watching for triggers)
    lives in :class:`tradinglab.entries.evaluator.EntryEvaluator` —
    NOT on this object — so app restart wipes arm state without
    requiring a save round-trip.
    """

    id: str = field(default_factory=_new_id)
    name: str = ""
    direction: Direction = Direction.LONG
    universe: Universe = field(default_factory=Universe)
    trigger: EntryTrigger = field(default_factory=EntryTrigger)
    sizing: SizingRule = field(default_factory=SizingRule)
    on_fill_exit_ids: tuple[str, ...] = field(default_factory=tuple)

    # Persistent config / lifecycle gates
    enabled: bool = True
    cooldown_secs: int = 0
    max_fires_per_session_per_symbol: int = 1
    max_fires_per_session_total: int | None = None
    position_already_open_policy: PositionAlreadyOpenPolicy = (
        PositionAlreadyOpenPolicy.BLOCK
    )
    arm_window_start: str = "09:35"  # ET HH:MM (regular session)
    arm_window_end: str = "15:30"
    require_market_open: bool = True

    # Provenance
    schema_version: int = CURRENT_SCHEMA_VERSION
    created_with: CreatedWith = field(default_factory=CreatedWith)
    created_at: str = field(default_factory=_utcnow_iso)
    updated_at: str = field(default_factory=_utcnow_iso)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Coerce list -> tuple defensively (callers may pass a list).
        if not isinstance(self.on_fill_exit_ids, tuple):
            self.on_fill_exit_ids = tuple(self.on_fill_exit_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "direction": self.direction.value,
            "universe": self.universe.to_dict(),
            "trigger": self.trigger.to_dict(),
            "sizing": self.sizing.to_dict(),
            "on_fill_exit_ids": list(self.on_fill_exit_ids),
            "enabled": self.enabled,
            "cooldown_secs": self.cooldown_secs,
            "max_fires_per_session_per_symbol": self.max_fires_per_session_per_symbol,
            "max_fires_per_session_total": self.max_fires_per_session_total,
            "position_already_open_policy": self.position_already_open_policy.value,
            "arm_window_start": self.arm_window_start,
            "arm_window_end": self.arm_window_end,
            "require_market_open": self.require_market_open,
            "schema_version": self.schema_version,
            "created_with": self.created_with.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> EntryStrategy:
        if not isinstance(d, Mapping):
            raise TypeError(f"EntryStrategy.from_dict expects mapping, got {type(d).__name__}")
        version = int(d.get("schema_version", CURRENT_SCHEMA_VERSION))
        if version > CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"EntryStrategy schema_version {version} > current "
                f"{CURRENT_SCHEMA_VERSION}; refusing to load"
            )
        if version < CURRENT_SCHEMA_VERSION:
            d = migrate(d, from_version=version)

        direction_str = d.get("direction", Direction.LONG.value)
        try:
            direction = Direction(direction_str)
        except ValueError as exc:
            raise ValueError(f"unknown Direction: {direction_str!r}") from exc

        policy_str = d.get(
            "position_already_open_policy",
            PositionAlreadyOpenPolicy.BLOCK.value,
        )
        try:
            policy = PositionAlreadyOpenPolicy(policy_str)
        except ValueError as exc:
            raise ValueError(
                f"unknown PositionAlreadyOpenPolicy: {policy_str!r}"
            ) from exc

        on_fill_raw = d.get("on_fill_exit_ids", ())
        if not isinstance(on_fill_raw, (list, tuple)):
            raise ValueError("on_fill_exit_ids must be a list/tuple")

        return cls(
            id=str(d.get("id") or _new_id()),
            name=str(d.get("name", "")),
            direction=direction,
            universe=Universe.from_dict(d.get("universe", {})),
            trigger=EntryTrigger.from_dict(d.get("trigger", {})),
            sizing=SizingRule.from_dict(d.get("sizing", {})),
            on_fill_exit_ids=tuple(str(x) for x in on_fill_raw),
            enabled=bool(d.get("enabled", True)),
            cooldown_secs=int(d.get("cooldown_secs", 0)),
            max_fires_per_session_per_symbol=int(
                d.get("max_fires_per_session_per_symbol", 1)
            ),
            max_fires_per_session_total=(
                int(d["max_fires_per_session_total"])
                if d.get("max_fires_per_session_total") is not None
                else None
            ),
            position_already_open_policy=policy,
            arm_window_start=str(d.get("arm_window_start", "09:35")),
            arm_window_end=str(d.get("arm_window_end", "15:30")),
            require_market_open=bool(d.get("require_market_open", True)),
            schema_version=CURRENT_SCHEMA_VERSION,
            created_with=CreatedWith.from_dict(d.get("created_with", {})),
            created_at=str(d.get("created_at", _utcnow_iso())),
            updated_at=str(d.get("updated_at", _utcnow_iso())),
            extra=dict(d.get("extra", {})),
        )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


_HHMM_RE = __import__("re").compile(r"^[0-2][0-9]:[0-5][0-9]$")


def validate_strategy(strategy: EntryStrategy) -> list[str]:
    """Return human-readable error strings; empty list = valid.

    Called on save (storage write), on load (BrokenStrategy detection),
    and on arm (refuse to arm invalid strategies). NOT called from
    constructors (so the GUI can construct half-built strategies during
    editing).

    Errors covered:

    - ``name`` non-empty.
    - ``direction`` is a known value (enforced by the enum constructor).
    - ``universe``: exactly ONE of symbols / scanner_id /
      from_attached_chart must be set.
    - ``trigger``: kind-specific completeness (LIMIT requires price,
      STOP requires stop_price, etc.).
    - ``sizing``: kind-specific (FIXED_QTY requires qty > 0,
      FIXED_NOTIONAL requires notional > 0).
    - Lifecycle: ``cooldown_secs >= 0``, ``max_fires_per_session_per_symbol >= 1``,
      arm window times match HH:MM and start <= end.
    """
    errs: list[str] = []
    if not strategy.name.strip():
        errs.append("strategy name is empty")

    # Universe XOR
    u = strategy.universe
    set_count = sum(
        [bool(u.symbols), bool(u.scanner_id), bool(u.from_attached_chart)]
    )
    if set_count == 0:
        errs.append(
            "universe is empty: must specify symbols, scanner_id, "
            "or from_attached_chart"
        )
    elif set_count > 1:
        errs.append(
            f"universe must specify exactly ONE of symbols/scanner_id/"
            f"from_attached_chart (got {set_count})"
        )
    if u.symbols:
        for s in u.symbols:
            if not s or not s.replace(".", "").replace("-", "").isalnum():
                errs.append(f"universe.symbols: invalid symbol {s!r}")

    # Trigger
    for msg in _validate_trigger(strategy.trigger):
        errs.append(f"trigger: {msg}")

    # Cross-check: SCANNER_ALERT trigger requires universe.scanner_id
    # OR a separate symbols list (not from_attached_chart, since the
    # scanner doesn't know about the chart's active symbol).
    if strategy.trigger.kind == TriggerKind.SCANNER_ALERT:
        if not strategy.trigger.scanner_id:
            errs.append("SCANNER_ALERT trigger requires trigger.scanner_id")
        if u.from_attached_chart:
            errs.append(
                "SCANNER_ALERT trigger is incompatible with from_attached_chart"
                " universe (use symbols or scanner_id instead)"
            )

    # Sizing
    for msg in _validate_sizing(strategy.sizing):
        errs.append(f"sizing: {msg}")

    # Lifecycle
    if strategy.cooldown_secs < 0:
        errs.append(f"cooldown_secs must be >= 0, got {strategy.cooldown_secs}")
    if strategy.max_fires_per_session_per_symbol < 1:
        errs.append(
            f"max_fires_per_session_per_symbol must be >= 1, got "
            f"{strategy.max_fires_per_session_per_symbol}"
        )
    if (
        strategy.max_fires_per_session_total is not None
        and strategy.max_fires_per_session_total < 1
    ):
        errs.append(
            f"max_fires_per_session_total must be >= 1 when set, got "
            f"{strategy.max_fires_per_session_total}"
        )
    if not _HHMM_RE.match(strategy.arm_window_start):
        errs.append(
            f"arm_window_start must be HH:MM, got {strategy.arm_window_start!r}"
        )
    if not _HHMM_RE.match(strategy.arm_window_end):
        errs.append(
            f"arm_window_end must be HH:MM, got {strategy.arm_window_end!r}"
        )
    if (
        _HHMM_RE.match(strategy.arm_window_start)
        and _HHMM_RE.match(strategy.arm_window_end)
        and strategy.arm_window_start > strategy.arm_window_end
    ):
        errs.append(
            f"arm_window_start ({strategy.arm_window_start}) must be <= "
            f"arm_window_end ({strategy.arm_window_end})"
        )

    return errs


def _validate_trigger(t: EntryTrigger) -> list[str]:
    errs: list[str] = []
    if t.kind == TriggerKind.LIMIT:
        if t.price is None or t.price <= 0:
            errs.append("LIMIT trigger requires price > 0")
    elif t.kind == TriggerKind.STOP:
        if t.stop_price is None or t.stop_price <= 0:
            errs.append("STOP trigger requires stop_price > 0")
    elif t.kind == TriggerKind.STOP_LIMIT:
        if t.stop_price is None or t.stop_price <= 0:
            errs.append("STOP_LIMIT trigger requires stop_price > 0")
        if t.price is None or t.price <= 0:
            errs.append("STOP_LIMIT trigger requires limit price > 0")
    elif t.kind == TriggerKind.INDICATOR:
        if t.condition is None:
            errs.append("INDICATOR trigger requires a condition")
    elif t.kind == TriggerKind.SCANNER_ALERT:
        if not t.scanner_id:
            errs.append("SCANNER_ALERT trigger requires scanner_id")
    # MARKET has no required fields.
    return errs


def _validate_sizing(s: SizingRule) -> list[str]:
    errs: list[str] = []
    if s.kind == SizingKind.FIXED_QTY:
        if s.qty is None or s.qty <= 0:
            errs.append("FIXED_QTY sizing requires qty > 0")
    elif s.kind == SizingKind.FIXED_NOTIONAL:
        if s.notional is None or s.notional <= 0:
            errs.append("FIXED_NOTIONAL sizing requires notional > 0")
    return errs


# ---------------------------------------------------------------------------
# Migration scaffolding
# ---------------------------------------------------------------------------


def migrate(d: Mapping[str, Any], *, from_version: int) -> dict[str, Any]:
    """Migrate an older-schema dict to the current schema.

    No migrations exist for v1; this is scaffolding for future versions.
    """
    out = dict(d)
    if from_version < 1:
        out["schema_version"] = 1
    return out
