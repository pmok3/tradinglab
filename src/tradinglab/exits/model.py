"""Pure-logic data model for exit strategies.

This module owns the **on-disk** and **in-memory** schema for exit
strategies. It is Tk-free and side-effect-free.

A strategy is a list of *legs*. A leg is a list of *triggers* OR'd
together. A trigger is a single tagged-union row whose semantics depend
on its :class:`TriggerKind` (market / limit / stop / stop_limit /
trailing_stop / time_of_day / indicator).

OCO behavior is described by :class:`OCOGroup`; default
``cancel_on="full_closeout"`` means siblings are not canceled until
``position.qty_open == 0`` after the fire (the "bracket" behavior).

EOD ("end of regular session") is **not** a trigger kind. It is a
strategy-level kill switch (:attr:`ExitStrategy.eod_kill_switch`) that
fires a market exit for the entire remaining quantity at
``session_close - eod_offset_min`` minutes.

JSON round-trip is provided via :meth:`ExitStrategy.to_dict` /
:meth:`from_dict`. Schema migration scaffolding is in :func:`migrate`.
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
    "TrailUnit",
    "ActivationUnit",
    "TrailBasis",
    "TimeInForce",
    "OrderSide",
    "ExitTrigger",
    "ExitLeg",
    "OCOGroup",
    "CreatedWith",
    "ExitStrategy",
    "validate_strategy",
    "migrate",
    "CURRENT_SCHEMA_VERSION",
]


CURRENT_SCHEMA_VERSION: int = 2


class TriggerKind(str, Enum):
    """The eight first-class trigger kinds."""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TRAILING_STOP = "trailing_stop"
    TIME_OF_DAY = "time_of_day"
    INDICATOR = "indicator"
    CHANDELIER = "chandelier"


class TrailUnit(str, Enum):
    """Unit for ``ExitTrigger.trail_value`` on trailing stops."""

    PERCENT = "percent"
    DOLLAR = "dollar"
    ATR = "atr"


class ActivationUnit(str, Enum):
    """Unit for ``ExitTrigger.activation_value`` on trailing stops.

    ``R`` is "R-multiple" — a multiple of the position's initial risk
    (``avg_entry_price - hard_stop_price``). The evaluator computes the
    risk denominator from the position + paired stop leg.
    """

    PERCENT = "percent"
    DOLLAR = "dollar"
    R_MULTIPLE = "r_multiple"


class TrailBasis(str, Enum):
    """When does the high-watermark of a trailing stop update?"""

    INTRABAR = "intrabar"
    CLOSE = "close"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"


class OrderSide(str, Enum):
    """Side of the *exit* order; determined by the position side."""

    BUY = "buy"
    SELL = "sell"


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# ExitTrigger — tagged-union row
# ---------------------------------------------------------------------------


@dataclass
class ExitTrigger:
    """A single exit-condition row inside a leg.

    Tagged-union semantics: which fields are *meaningful* depends on
    ``kind``. Validation is done in :func:`validate_strategy` (not in
    ``__post_init__``) so editors can construct half-built triggers
    while the user is typing.

    Required fields per kind
    ------------------------
    - ``MARKET``: nothing extra (fires immediately on arming).
    - ``LIMIT``: ``price`` OR ``offset_pct`` OR ``offset_dollar`` (one).
    - ``STOP``: ``price`` OR ``offset_pct`` OR ``offset_dollar`` (one).
    - ``STOP_LIMIT``: stop trigger (one of price/offset_*) +
      ``stop_limit_price`` (or ``stop_limit_offset``).
    - ``TRAILING_STOP``: ``trail_unit`` + ``trail_value`` (+ optional
      activation_unit/value, trail_basis).
    - ``TIME_OF_DAY``: ``time_of_day`` (HH:MM, 24h, regular session).
    - ``INDICATOR``: ``condition`` (a scanner.model.Group) +
      ``interval`` (override; ``None`` => position interval).

    The ``qty_pct`` is resolved at *fire time* against the position's
    ``qty_open`` (B6 fix). Default 100%.
    """

    id: str = field(default_factory=_new_id)
    kind: TriggerKind = TriggerKind.MARKET

    # Price / offset family (limit, stop, stop_limit)
    price: float | None = None
    offset_pct: float | None = None
    offset_dollar: float | None = None
    stop_limit_price: float | None = None
    stop_limit_offset: float | None = None

    # Trailing-stop family
    trail_unit: TrailUnit | None = None
    trail_value: float | None = None
    activation_unit: ActivationUnit | None = None
    activation_value: float | None = None
    trail_basis: TrailBasis = TrailBasis.INTRABAR

    # Time-of-day
    time_of_day: str | None = None  # "HH:MM"

    # Indicator
    condition: ConditionGroup | None = None
    interval: str | None = None
    evaluate_intrabar: bool = False

    # Chandelier-stop family (Camp-B anchored, ratchet always on, touch
    # trigger, frozen params at activation — see exits.spec for the
    # evaluator). Default values match the locked-in design for the
    # always-on indicator and are validated within range when kind ==
    # CHANDELIER. Inert for all other kinds.
    chandelier_lookback: int = 22
    chandelier_atr_period: int = 22
    chandelier_multiplier: float = 3.0
    chandelier_ma_type: str = "RMA"

    # Common
    qty_pct: float = 100.0
    time_in_force: TimeInForce = TimeInForce.DAY
    enabled: bool = True
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind.value,
            "qty_pct": self.qty_pct,
            "time_in_force": self.time_in_force.value,
            "enabled": self.enabled,
            "label": self.label,
        }
        # Sparse: only emit set fields.
        for k in (
            "price",
            "offset_pct",
            "offset_dollar",
            "stop_limit_price",
            "stop_limit_offset",
            "trail_value",
            "activation_value",
            "time_of_day",
            "interval",
        ):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        if self.trail_unit is not None:
            out["trail_unit"] = self.trail_unit.value
        if self.activation_unit is not None:
            out["activation_unit"] = self.activation_unit.value
        # Trail basis only meaningful for trailing stops; always emit
        # if kind is trailing_stop, otherwise default-suppress.
        if self.kind == TriggerKind.TRAILING_STOP:
            out["trail_basis"] = self.trail_basis.value
        if self.kind == TriggerKind.INDICATOR:
            out["evaluate_intrabar"] = self.evaluate_intrabar
            if self.condition is not None:
                out["condition"] = self.condition.to_dict()
        if self.kind == TriggerKind.CHANDELIER:
            out["chandelier_lookback"] = int(self.chandelier_lookback)
            out["chandelier_atr_period"] = int(self.chandelier_atr_period)
            out["chandelier_multiplier"] = float(self.chandelier_multiplier)
            out["chandelier_ma_type"] = str(self.chandelier_ma_type)
        return out

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ExitTrigger:
        if not isinstance(d, Mapping):
            raise TypeError(f"ExitTrigger.from_dict expects mapping, got {type(d).__name__}")
        kind_str = d.get("kind")
        if not isinstance(kind_str, str):
            raise ValueError(f"ExitTrigger.kind missing or non-string: {kind_str!r}")
        try:
            kind = TriggerKind(kind_str)
        except ValueError as exc:
            raise ValueError(f"unknown TriggerKind: {kind_str!r}") from exc

        def _opt_enum(name: str, enum_cls):
            v = d.get(name)
            if v is None:
                return None
            try:
                return enum_cls(v)
            except ValueError as exc:
                raise ValueError(f"unknown {enum_cls.__name__}: {v!r}") from exc

        cond_raw = d.get("condition")
        cond = ConditionGroup.from_dict(cond_raw) if isinstance(cond_raw, Mapping) else None

        return cls(
            id=str(d.get("id") or _new_id()),
            kind=kind,
            price=_as_float_opt(d.get("price")),
            offset_pct=_as_float_opt(d.get("offset_pct")),
            offset_dollar=_as_float_opt(d.get("offset_dollar")),
            stop_limit_price=_as_float_opt(d.get("stop_limit_price")),
            stop_limit_offset=_as_float_opt(d.get("stop_limit_offset")),
            trail_unit=_opt_enum("trail_unit", TrailUnit),
            trail_value=_as_float_opt(d.get("trail_value")),
            activation_unit=_opt_enum("activation_unit", ActivationUnit),
            activation_value=_as_float_opt(d.get("activation_value")),
            trail_basis=TrailBasis(d.get("trail_basis", TrailBasis.INTRABAR.value)),
            time_of_day=_as_str_opt(d.get("time_of_day")),
            condition=cond,
            interval=_as_str_opt(d.get("interval")),
            evaluate_intrabar=bool(d.get("evaluate_intrabar", False)),
            chandelier_lookback=int(d.get("chandelier_lookback", 22)),
            chandelier_atr_period=int(d.get("chandelier_atr_period", 22)),
            chandelier_multiplier=float(d.get("chandelier_multiplier", 3.0)),
            chandelier_ma_type=str(d.get("chandelier_ma_type", "RMA")),
            qty_pct=float(d.get("qty_pct", 100.0)),
            time_in_force=TimeInForce(d.get("time_in_force", TimeInForce.DAY.value)),
            enabled=bool(d.get("enabled", True)),
            label=str(d.get("label", "")),
        )


# ---------------------------------------------------------------------------
# ExitLeg / OCOGroup / ExitStrategy
# ---------------------------------------------------------------------------


@dataclass
class ExitLeg:
    """A single leg of an exit strategy. Triggers are OR'd together."""

    id: str = field(default_factory=_new_id)
    label: str = ""
    triggers: list[ExitTrigger] = field(default_factory=list)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "enabled": self.enabled,
            "triggers": [t.to_dict() for t in self.triggers],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ExitLeg:
        if not isinstance(d, Mapping):
            raise TypeError(f"ExitLeg.from_dict expects mapping, got {type(d).__name__}")
        triggers_raw = d.get("triggers", [])
        if not isinstance(triggers_raw, list):
            raise ValueError("ExitLeg.triggers must be a list")
        return cls(
            id=str(d.get("id") or _new_id()),
            label=str(d.get("label", "")),
            enabled=bool(d.get("enabled", True)),
            triggers=[ExitTrigger.from_dict(t) for t in triggers_raw],
        )


@dataclass
class OCOGroup:
    """A one-cancels-other group across legs.

    ``cancel_on="any_fire"`` is traditional OCO: any fire cancels all
    siblings immediately. ``cancel_on="full_closeout"`` (default,
    bracket-friendly) only cancels siblings when ``qty_open == 0``
    after a fire — so a partial profit-take does NOT void the stop.
    """

    leg_ids: tuple[str, ...]
    cancel_on: str = "full_closeout"  # Literal["any_fire", "full_closeout"]

    _ALLOWED_CANCEL_ON = ("any_fire", "full_closeout")

    def __post_init__(self) -> None:
        if not isinstance(self.leg_ids, tuple):
            self.leg_ids = tuple(self.leg_ids)
        if self.cancel_on not in self._ALLOWED_CANCEL_ON:
            raise ValueError(
                f"OCOGroup.cancel_on must be one of {self._ALLOWED_CANCEL_ON}, "
                f"got {self.cancel_on!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"leg_ids": list(self.leg_ids), "cancel_on": self.cancel_on}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> OCOGroup:
        if not isinstance(d, Mapping):
            raise TypeError(f"OCOGroup.from_dict expects mapping, got {type(d).__name__}")
        leg_ids_raw = d.get("leg_ids", [])
        if not isinstance(leg_ids_raw, (list, tuple)):
            raise ValueError("OCOGroup.leg_ids must be a list/tuple")
        return cls(
            leg_ids=tuple(str(x) for x in leg_ids_raw),
            cancel_on=str(d.get("cancel_on", "full_closeout")),
        )


@dataclass
class CreatedWith:
    app: str = "tradinglab"
    version: str = "0.0.0"

    def to_dict(self) -> dict[str, str]:
        return {"app": self.app, "version": self.version}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> CreatedWith:
        return cls(app=str(d.get("app", "tradinglab")), version=str(d.get("version", "0.0.0")))


@dataclass
class ExitStrategy:
    """Top-level exit-strategy aggregate.

    Each open :class:`Position` may bind to at most one
    :class:`ExitStrategy` (per-instance binding, NOT a template
    reference — the strategy is "frozen" at attach time so user edits
    on the template do not retroactively mutate live positions).
    """

    id: str = field(default_factory=_new_id)
    name: str = ""
    legs: list[ExitLeg] = field(default_factory=list)
    oco_groups: list[OCOGroup] = field(default_factory=list)
    eod_kill_switch: bool = True
    eod_offset_min: int = 5
    schema_version: int = CURRENT_SCHEMA_VERSION
    created_with: CreatedWith = field(default_factory=CreatedWith)
    created_at: str = field(default_factory=_utcnow_iso)
    updated_at: str = field(default_factory=_utcnow_iso)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "legs": [leg.to_dict() for leg in self.legs],
            "oco_groups": [g.to_dict() for g in self.oco_groups],
            "eod_kill_switch": self.eod_kill_switch,
            "eod_offset_min": self.eod_offset_min,
            "schema_version": self.schema_version,
            "created_with": self.created_with.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ExitStrategy:
        if not isinstance(d, Mapping):
            raise TypeError(f"ExitStrategy.from_dict expects mapping, got {type(d).__name__}")
        version = int(d.get("schema_version", CURRENT_SCHEMA_VERSION))
        if version > CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"ExitStrategy schema_version {version} > current {CURRENT_SCHEMA_VERSION}; "
                "refusing to load"
            )
        if version < CURRENT_SCHEMA_VERSION:
            d = migrate(d, from_version=version)

        legs_raw = d.get("legs", [])
        if not isinstance(legs_raw, list):
            raise ValueError("ExitStrategy.legs must be a list")
        oco_raw = d.get("oco_groups", [])
        if not isinstance(oco_raw, list):
            raise ValueError("ExitStrategy.oco_groups must be a list")

        return cls(
            id=str(d.get("id") or _new_id()),
            name=str(d.get("name", "")),
            legs=[ExitLeg.from_dict(l) for l in legs_raw],
            oco_groups=[OCOGroup.from_dict(g) for g in oco_raw],
            eod_kill_switch=bool(d.get("eod_kill_switch", True)),
            eod_offset_min=int(d.get("eod_offset_min", 5)),
            schema_version=CURRENT_SCHEMA_VERSION,
            created_with=CreatedWith.from_dict(d.get("created_with", {})),
            created_at=str(d.get("created_at", _utcnow_iso())),
            updated_at=str(d.get("updated_at", _utcnow_iso())),
            extra=dict(d.get("extra", {})),
        )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_strategy(strategy: ExitStrategy) -> list[str]:
    """Return a list of human-readable error strings; empty = valid.

    Callers (storage, GUI) can decide whether to refuse-save on errors
    (we do) or merely warn. Errors covered:

    - Top-level: ``name`` non-empty, ``eod_offset_min >= 0``.
    - Legs: non-empty ``id``, every ``leg.id`` is unique within the
      strategy, every leg has at least one trigger when enabled.
    - Triggers: tagged-union completeness (e.g. limit must have a
      price OR offset, not both, not neither).
    - OCO groups: every ``leg_id`` references an existing leg; groups
      are **disjoint** (no leg appears in two groups); ``cancel_on``
      is a known value (enforced by :class:`OCOGroup` itself).
    """

    errs: list[str] = []
    if not strategy.name.strip():
        errs.append("strategy name is empty")
    if strategy.eod_offset_min < 0:
        errs.append(f"eod_offset_min must be >= 0, got {strategy.eod_offset_min}")

    leg_ids = [leg.id for leg in strategy.legs]
    if len(leg_ids) != len(set(leg_ids)):
        errs.append("duplicate leg ids within strategy")
    for leg in strategy.legs:
        if not leg.id:
            errs.append("empty leg id")
        if leg.enabled and not leg.triggers:
            errs.append(f"leg {leg.label or leg.id!r} is enabled but has no triggers")
        for trig in leg.triggers:
            for msg in _validate_trigger(trig):
                errs.append(f"leg {leg.label or leg.id!r} / trigger {trig.id}: {msg}")

    leg_id_set = set(leg_ids)
    seen_in_groups: set = set()
    for idx, group in enumerate(strategy.oco_groups):
        if len(group.leg_ids) < 2:
            errs.append(f"oco group {idx}: needs at least 2 legs (got {len(group.leg_ids)})")
        for lid in group.leg_ids:
            if lid not in leg_id_set:
                errs.append(f"oco group {idx}: unknown leg_id {lid!r}")
            if lid in seen_in_groups:
                errs.append(f"oco group {idx}: leg_id {lid!r} already in another group (must be disjoint)")
            seen_in_groups.add(lid)
    return errs


def _validate_trigger(t: ExitTrigger) -> list[str]:
    errs: list[str] = []
    if not 0.0 < t.qty_pct <= 100.0:
        errs.append(f"qty_pct must be in (0, 100], got {t.qty_pct}")

    if t.kind == TriggerKind.MARKET:
        return errs
    if t.kind in (TriggerKind.LIMIT, TriggerKind.STOP):
        if _count_set(t.price, t.offset_pct, t.offset_dollar) != 1:
            errs.append(
                f"{t.kind.value} requires exactly one of (price, offset_pct, offset_dollar)"
            )
        return errs
    if t.kind == TriggerKind.STOP_LIMIT:
        if _count_set(t.price, t.offset_pct, t.offset_dollar) != 1:
            errs.append("stop_limit requires exactly one of stop trigger (price/offset_pct/offset_dollar)")
        if _count_set(t.stop_limit_price, t.stop_limit_offset) != 1:
            errs.append("stop_limit requires exactly one of (stop_limit_price, stop_limit_offset)")
        return errs
    if t.kind == TriggerKind.TRAILING_STOP:
        if t.trail_unit is None or t.trail_value is None:
            errs.append("trailing_stop requires trail_unit and trail_value")
        elif t.trail_value <= 0:
            errs.append(f"trailing_stop trail_value must be > 0, got {t.trail_value}")
        # Activation pair (both set or both unset).
        if (t.activation_unit is None) != (t.activation_value is None):
            errs.append("activation_unit and activation_value must both be set or both unset")
        return errs
    if t.kind == TriggerKind.TIME_OF_DAY:
        if not t.time_of_day:
            errs.append("time_of_day trigger requires time_of_day field (HH:MM)")
        else:
            if not _valid_hhmm(t.time_of_day):
                errs.append(f"invalid time_of_day {t.time_of_day!r}, expected HH:MM")
        return errs
    if t.kind == TriggerKind.INDICATOR:
        if t.condition is None:
            errs.append("indicator trigger requires condition")
        return errs
    if t.kind == TriggerKind.CHANDELIER:
        if int(t.chandelier_lookback) < 1:
            errs.append(
                f"chandelier_lookback must be >= 1, got {t.chandelier_lookback}"
            )
        if int(t.chandelier_atr_period) < 2:
            errs.append(
                f"chandelier_atr_period must be >= 2, got {t.chandelier_atr_period}"
            )
        if not (0.5 <= float(t.chandelier_multiplier) <= 8.0):
            errs.append(
                f"chandelier_multiplier must be in [0.5, 8.0], "
                f"got {t.chandelier_multiplier}"
            )
        if str(t.chandelier_ma_type).upper() not in ("RMA", "SMA", "EMA", "WMA"):
            errs.append(
                f"chandelier_ma_type must be one of "
                f"(RMA, SMA, EMA, WMA), got {t.chandelier_ma_type!r}"
            )
        return errs
    errs.append(f"unknown trigger kind: {t.kind!r}")
    return errs


def _count_set(*values: Any) -> int:
    return sum(1 for v in values if v is not None)


def _valid_hhmm(s: str) -> bool:
    if len(s) != 5 or s[2] != ":":
        return False
    try:
        h = int(s[0:2])
        m = int(s[3:5])
    except ValueError:
        return False
    return 0 <= h <= 23 and 0 <= m <= 59


def _as_float_opt(v: Any) -> float | None:
    if v is None:
        return None
    return float(v)


def _as_str_opt(v: Any) -> str | None:
    if v is None:
        return None
    return str(v)


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


def migrate(d: Mapping[str, Any], from_version: int) -> dict[str, Any]:
    """Migrate a raw on-disk dict from ``from_version`` to current.

    Schema history:

    * v1 — initial schema (7 trigger kinds: market / limit / stop /
      stop_limit / trailing_stop / time_of_day / indicator).
    * v2 — adds :attr:`TriggerKind.CHANDELIER` and four chandelier-only
      fields on :class:`ExitTrigger` (``chandelier_lookback``,
      ``chandelier_atr_period``, ``chandelier_multiplier``,
      ``chandelier_ma_type``). The migration is purely additive: v1
      saved strategies load cleanly because the new fields fall back
      to their dataclass defaults when absent from the on-disk dict.
    """
    if from_version == CURRENT_SCHEMA_VERSION:
        return dict(d)
    if from_version > CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"cannot migrate from version {from_version} to {CURRENT_SCHEMA_VERSION}"
        )
    if from_version == 1:
        # Additive v1 → v2: nothing to rewrite on the dict. ExitTrigger
        # defaults handle the four new chandelier_* fields when they're
        # absent. Existing kinds are untouched.
        return dict(d)
    raise ValueError(f"no migration registered for schema_version {from_version}")
