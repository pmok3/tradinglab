"""Technical indicator protocol, parameter schema, registry.

An *indicator* transforms an OHLCV series into one or more named line
series (e.g. ``{"sma": [...]}``, ``{"upper": [...], "middle": [...],
"lower": [...]}``). Output arrays are always the same length as the
input ``candles``, padded with ``NaN`` where the indicator is
undefined (typically the first ``period-1`` samples).

Indicator *factories* live in :data:`INDICATORS` keyed by display name
(``"SMA"``, ``"EMA"``, ``"RSI"``, ...). Instantiating a factory yields
a configured indicator bound to a specific parameter set::

    sma20 = INDICATORS["SMA"](length=20)
    lines = sma20.compute(candles)  # {"sma": np.ndarray}

Each indicator class additionally declares:

- ``kind_id: ClassVar[str]`` — stable identity used in persistence
  and routing. Stays constant even if the human-readable display name
  changes (e.g. ``"sma"``, ``"bbands"``).
- ``params_schema: ClassVar[Tuple[ParamDef, ...]]`` — typed parameter
  metadata used by the UI to auto-generate the Add Indicator dialog
  (Spinbox / Checkbox / Combobox / Entry by ``kind``) and by config
  hydration to validate persisted values.
- ``default_style: ClassVar[Dict[str, LineStyle]]`` — per-output-key
  default color/width hints. Render layer seeds new
  ``IndicatorConfig`` instances from these.

This layer is intentionally pure: no matplotlib, no Tk, no main-thread
coupling. It is therefore safe to invoke from any worker thread and
trivially unit-testable. Rendering and UI wiring live in ``app.py``
and ``indicators.config.IndicatorManager``.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, NamedTuple, Protocol

import numpy as np

from ..constants import INTRADAY_INTERVALS
from ..core.bars import Bars
from ..models import Candle
from ._palette import FALLBACK_GRAY

# --- Parameter schema --------------------------------------------------------

# Allowed param kinds. Keep narrow on purpose so the auto-generated
# dialog stays simple. Custom indicators that need exotic types should
# expose a ``str`` field with a documented format and parse internally.
PARAM_KINDS = ("int", "float", "bool", "str", "choice")


@dataclass(frozen=True)
class ParamDef:
    """One parameter on an indicator factory.

    ``kind``    — one of :data:`PARAM_KINDS`. Drives the dialog widget.
    ``default`` — initial value (used both as ``__init__`` default and
                  as the dialog seed).
    ``min`` / ``max`` / ``step`` — optional numeric bounds (only meaningful
                  for ``int`` / ``float``).
    ``choices`` — for ``kind="choice"``: ordered tuple of allowed values
                  rendered as a Combobox.
    ``description`` — short user-facing label / tooltip text.
    """

    name: str
    kind: str
    default: Any
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: tuple[Any, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if self.kind not in PARAM_KINDS:
            raise ValueError(f"unknown ParamDef kind: {self.kind!r}")
        if self.kind == "choice" and not self.choices:
            raise ValueError(f"ParamDef {self.name!r}: choice kind requires non-empty choices")


# --- Line style --------------------------------------------------------------


@dataclass(frozen=True)
class LineStyle:
    """Per-output-key visual style hint.

    Indicators declare a ``default_style`` dict keyed by output name
    (``"sma"``, ``"upper"``, ...) so the render layer can pick sensible
    defaults when the user adds an instance. Per-instance overrides
    live on :class:`IndicatorConfig`.

    ``color`` is any matplotlib-acceptable color string. ``width`` is
    points. ``visible`` lets per-key visibility toggle without removing
    the indicator (useful for BB upper/lower lines).
    """

    color: str = FALLBACK_GRAY
    width: float = 1.2
    visible: bool = True


# --- Indicator protocol ------------------------------------------------------


class Indicator(Protocol):
    """Compute one or more line series from a candle history.

    Class-level attributes (declared on the *class*, not instance):
      ``kind_id``           — stable persistence id (e.g. ``"sma"``).
      ``kind_version``      — bumped when persisted ``params`` schema changes.
      ``params_schema``     — tuple of :class:`ParamDef` describing
                              factory parameters; drives the dialog.
      ``default_style``     — ``{output_key: LineStyle}`` defaults.
      ``scannable_outputs`` — tuple of ``(output_key, dtype)`` pairs that
                              the scanner should expose. Empty (the
                              default) means the indicator is NOT
                              surfaced as a scanner field — preserves the
                              fail-closed policy so categorical/boolean
                              outputs don't silently leak into numeric
                              comparisons. See
                              :mod:`tradinglab.scanner.fields` for the
                              dtype string contract (``"numeric"`` /
                              ``"bool"``).
      ``resets_daily``      — ``True`` when the indicator's output is
                              anchored to the regular session (resets at
                              session open). Used by the scanner's
                              within-last-N-bars walk to clamp look-back
                              windows to the current session.

    Instance attributes:
      ``name``          — human-readable label (e.g. ``"SMA(20)"``);
                          used in legends.
      ``overlay``       — ``True`` if the indicator draws on the price
                          axes (moving averages, Bollinger); ``False``
                          if it needs its own pane (RSI, MACD).

    Compute method:
      ``compute(candles)`` returns ``{output_key: ndarray}`` with each
      array the same length as ``candles``. Undefined positions are
      ``NaN``.
    """

    kind_id: ClassVar[str]
    kind_version: ClassVar[int]
    params_schema: ClassVar[tuple[ParamDef, ...]]
    default_style: ClassVar[dict[str, LineStyle]]
    scannable_outputs: ClassVar[tuple[tuple[str, str], ...]] = ()
    resets_daily: ClassVar[bool] = False

    name: str
    overlay: bool

    def compute(self, candles: list[Candle]) -> dict[str, np.ndarray]:
        ...


# ---------------------------------------------------------------------------
# BaseIndicator — concrete mixin providing the canonical compute(candles) shim
# ---------------------------------------------------------------------------
#
# 17 of the 17 built-in indicators (plus the codegen template in
# ``indicators/expression.py``) used to ship a byte-identical 2-line
# ``compute(candles) -> dict[str, np.ndarray]`` that built a ``Bars``
# from the candle list and forwarded to ``compute_arr(bars)``. That
# was duplication waiting to drift — and it was a footgun for new
# indicators (forget the shim → ``IndicatorMemo`` raises at runtime
# because ``compute`` is the documented public entry point).
#
# Solution: a tiny concrete mixin. Indicator classes inherit from
# :class:`BaseIndicator` so the shim is provided for free. They keep
# implementing ``compute_arr(bars)`` (the fast path); the mixin's
# ``compute(candles)`` just builds a ``Bars`` and forwards.


class BaseIndicator:
    """Default ``compute(candles)`` implementation.

    Subclasses MUST implement ``compute_arr(bars: Bars) -> dict[str,
    np.ndarray]``. The mixin's ``compute(candles)`` is a thin shim
    that builds a ``Bars`` from the candle list and forwards.

    Pre-mixin, every indicator class hand-rolled the same 2-line
    shim — see CLAUDE.md for the audit trail. The mixin is purely
    additive: indicators that already inherited from nothing now
    inherit from ``BaseIndicator``; the shim body and any explicit
    declaration is removed; behaviour and tests unchanged.

    Note: ``BaseIndicator`` does NOT supply ``compute_arr`` — that's
    the indicator-specific math. It also does NOT supply the
    ``kind_id`` / ``kind_version`` / ``params_schema`` /
    ``default_style`` / ``scannable_outputs`` ClassVars — those
    remain per-indicator. The mixin owns only the
    ``Bars.from_candles`` boilerplate.
    """

    def compute(self, candles: list[Candle]) -> dict[str, np.ndarray]:
        return self.compute_arr(Bars.from_candles(candles))

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:  # pragma: no cover
        """Subclasses MUST override. Never called directly here."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement compute_arr(bars)"
        )

    @classmethod
    def effective_output_keys(cls, params: dict) -> tuple[str, ...]:
        """Return the output keys actually visible for these ``params``.

        Drives the in-chart legend (`gui/readout_legend.py`) and any
        future "skip computing an output that's not shown" path.
        Default: every key in ``default_style`` (or a single synthetic
        key for indicators with no declared style). Multi-output
        indicators that emit all-NaN for some outputs depending on
        params (e.g. AVWAP with ``bands="off"`` emits NaN for every
        band but still returns the keys in its dict) MUST override
        this to declare which keys are genuinely visible — otherwise
        the legend shows a row per all-NaN output. Also: indicators
        with a non-default visual top-down order (Bollinger upper →
        middle → lower) may override here to put them in chart-order.

        Audit ``legend-condensation`` — added for the sprint that
        collapsed multi-output rows into a single consolidated legend
        row per indicator config.
        """
        default_style = getattr(cls, "default_style", None)
        if default_style and hasattr(default_style, "keys"):
            keys = tuple(str(k) for k in default_style.keys())
            if keys:
                return keys
        kind_id = getattr(cls, "kind_id", "") or ""
        return (str(kind_id) or "value",)


# ---------------------------------------------------------------------------
# compute_arr dispatch
# ---------------------------------------------------------------------------
#
# Indicators may optionally implement ``compute_arr(bars: Bars)`` —
# exactly equivalent to ``compute(candles)`` but consuming a pre-built
# :class:`tradinglab.core.bars.Bars` view so the caller doesn't pay
# an ``np.fromiter`` extraction cost per indicator. Migrating an
# indicator from "just compute" to "compute + compute_arr" is purely
# additive — the public ``compute(candles)`` API is preserved as a
# thin wrapper that builds a ``Bars`` and forwards.
#
# Use :func:`compute_via_bars` from the chart-render hot path so the
# fast path is taken automatically for migrated indicators while
# legacy ones still work unchanged.


def compute_via_bars(indicator: Any, bars: Bars) -> dict[str, np.ndarray]:
    """Run an indicator over a ``Bars`` view, preferring the native fast path.

    * If ``indicator.compute_arr(bars)`` exists, call it directly.
    * Otherwise fall back to ``indicator.compute(bars.candles)``;
      this requires the ``Bars`` to retain its candle back-reference
      (set by :meth:`Bars.from_candles` or any caller that passes
      ``candles=`` to :meth:`Bars.from_arrays`).

    Raises
    ------
    ValueError
        Indicator has neither ``compute_arr`` nor a usable candle
        back-reference. Should never happen in chart render — the
        ChartApp build path always retains the candle list.
    """
    fn = getattr(indicator, "compute_arr", None)
    if callable(fn):
        return fn(bars)
    if bars.candles is None:
        raise ValueError(
            f"{type(indicator).__name__} has no compute_arr and "
            "Bars has no candles back-reference; cannot compute."
        )
    return indicator.compute(bars.candles)


# Factory type: ``IndicatorFactory(**params) -> Indicator``. Parameter
# names are indicator-specific. Keeping it as a plain Callable avoids a
# combinatorial explosion of Protocol subclasses.
IndicatorFactory = Callable[..., Indicator]


# --- Interval availability --------------------------------------------------


class Availability(NamedTuple):
    """Outcome of an indicator factory's interval-availability check.

    ``ok`` — true when the indicator may be used on the given interval.
    ``reason`` — short, user-facing explanation rendered as a tooltip
                 on the disabled menu item; empty when ``ok`` is true.
    """

    ok: bool
    reason: str = ""


def intraday_only(interval: str) -> Availability:
    """Shared helper for indicators that need sub-daily bars.

    Uses :data:`tradinglab.constants.INTRADAY_INTERVALS` as the
    single source of truth so the whole app speaks the same vocabulary
    of interval strings.
    """
    if interval in INTRADAY_INTERVALS:
        return Availability(True, "")
    return Availability(False, "Requires an intraday interval (e.g. 1m, 5m, 15m, 1h)")


def factory_is_available_for(
    factory: Any,
    interval: str,
    params: Mapping[str, Any] | None = None,
) -> Availability:
    """Resolve a factory's interval availability to an :class:`Availability`.

    Order of resolution:

    1. ``factory.is_available_for(interval, params)`` — preferred form
       for params-aware availability (e.g. :class:`RVOL` whose
       ``cumulative`` / ``time_of_day`` modes are intraday-only while
       ``simple`` works on every interval).
    2. ``factory.is_available_for(interval)`` — legacy single-arg form.
    3. ``factory.available_intervals`` (frozenset / tuple) — legacy
       attribute form. Membership check against ``interval``.
    4. Otherwise — :class:`Availability(True, "")`.

    Plugin indicators that don't define either keep working unchanged.
    The two-arg form is detected by inspecting the callable's signature;
    factories with ``**kwargs`` are assumed to accept ``params``.
    """
    if factory is None:
        return Availability(True, "")
    method = getattr(factory, "is_available_for", None)
    if callable(method):
        # Detect whether the method accepts a ``params`` kwarg / second
        # positional. Use signature inspection so plugin indicators that
        # only accept ``interval`` keep working unchanged.
        accepts_params = False
        try:
            sig = inspect.signature(method)
            for p in sig.parameters.values():
                if p.kind == inspect.Parameter.VAR_KEYWORD:
                    accepts_params = True
                    break
                if p.name == "params":
                    accepts_params = True
                    break
            else:
                # Fallback: non-self positional parameters > 1 ⇒ has params slot.
                positionals = [
                    p for p in sig.parameters.values()
                    if p.kind in (
                        inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    )
                ]
                if len(positionals) >= 2:
                    accepts_params = True
        except (TypeError, ValueError):
            accepts_params = False
        try:
            if accepts_params:
                res = method(interval, params or {})
            else:
                res = method(interval)
        except Exception:  # noqa: BLE001
            return Availability(True, "")
        if isinstance(res, Availability):
            return res
        if isinstance(res, tuple) and len(res) == 2:
            try:
                return Availability(bool(res[0]), str(res[1] or ""))
            except Exception:  # noqa: BLE001
                return Availability(True, "")
        return Availability(bool(res), "")
    intervals = getattr(factory, "available_intervals", None)
    if intervals:
        try:
            ok = interval in intervals
        except TypeError:
            ok = True
        return Availability(ok, "" if ok else "Not available for this interval")
    return Availability(True, "")


# --- Display registry --------------------------------------------------------

# Display registry, keyed by human-readable name. The Add menu and
# legend use these. For routing/persistence, look up by ``kind_id`` via
# :func:`factory_by_kind_id`.
INDICATORS: dict[str, IndicatorFactory] = {}

# kind_id → (display_name, factory). Built so persistence can survive
# display-name changes.
_BY_KIND_ID: dict[str, tuple[str, IndicatorFactory]] = {}


def register_indicator(name: str, factory: IndicatorFactory) -> None:
    """Register an indicator factory under ``name`` (idempotent).

    Also indexes by ``factory.kind_id`` if the factory exposes one (all
    built-ins do; legacy custom indicators without one are still
    registerable but won't round-trip through saved configs).
    """
    INDICATORS[name] = factory
    kind_id = getattr(factory, "kind_id", None)
    if kind_id:
        _BY_KIND_ID[kind_id] = (name, factory)


def register_legacy_indicator(name: str, factory: IndicatorFactory) -> None:
    """Register a back-compat factory in :data:`_BY_KIND_ID` ONLY.

    Used by indicator families that consolidated multiple ``kind_id``
    classes into a single unified replacement (e.g. SMA + EMA → MA).
    The legacy class remains importable AND remains discoverable via
    :func:`factory_by_kind_id` so in-memory configs with the old
    ``kind_id`` keep working, but the class does NOT appear in
    :data:`INDICATORS` and so is excluded from the Add Indicator menu.

    Persisted configs (which flow through
    :meth:`indicators.config.IndicatorConfig.from_dict`) are rewritten
    to the unified ``kind_id`` via :data:`_KIND_ID_MIGRATIONS` before
    the lookup, so they instantiate the unified replacement. The
    hidden registration is the safety net for tests and any code path
    that bypasses ``from_dict`` (e.g. constructing an
    ``IndicatorConfig(kind_id="sma", ...)`` directly).
    """
    kind_id = getattr(factory, "kind_id", None)
    if kind_id:
        _BY_KIND_ID[kind_id] = (name, factory)


def factory_by_kind_id(kind_id: str) -> tuple[str, IndicatorFactory] | None:
    """Return ``(display_name, factory)`` for ``kind_id`` or ``None``."""
    return _BY_KIND_ID.get(kind_id)


def kind_id_for(name: str) -> str | None:
    """Return the ``kind_id`` for an indicator registered under ``name``."""
    f = INDICATORS.get(name)
    return getattr(f, "kind_id", None) if f else None


# --- Scanner-facing introspection helpers -----------------------------------


def iter_indicator_factories() -> list[tuple[str, str, IndicatorFactory]]:
    """Return ``[(kind_id, display_name, factory), ...]`` in registration order.

    Walks the kind_id index so legacy-registered classes (e.g. SMA / EMA
    behind the unified MovingAverage display) are still discoverable.
    Used by the scanner field registry to project indicators with a
    non-empty :attr:`Indicator.scannable_outputs` ClassVar into
    :class:`FieldSpec`s without a hand-curated allowlist.
    """
    return [(kid, name, fac) for kid, (name, fac) in _BY_KIND_ID.items()]


def indicator_scannable_outputs(factory: Any) -> tuple[tuple[str, str], ...]:
    """Return a factory's ``scannable_outputs`` tuple (empty by default).

    Defensive — accepts any factory-like object (test stubs, plugin
    classes, lambdas wrapping a class) and falls back to ``()`` when the
    ClassVar is missing or not a tuple-of-pairs. Empty means the
    indicator opted out / never opted in: the scanner registry skips it.
    """
    raw = getattr(factory, "scannable_outputs", ())
    if not raw:
        return ()
    try:
        out: list[tuple[str, str]] = []
        for entry in raw:
            key, dtype = entry
            out.append((str(key), str(dtype)))
        return tuple(out)
    except (TypeError, ValueError):
        return ()


def indicator_resets_daily(factory: Any) -> bool:
    """Return True iff ``factory.resets_daily`` is truthy (default False)."""
    return bool(getattr(factory, "resets_daily", False))


# --- Deprecated kind_id migrations -----------------------------------------

#: Map of deprecated ``kind_id`` -> ``(new_kind_id, params_to_merge)``.
#:
#: Used by :meth:`indicators.config.IndicatorConfig.from_dict` to migrate
#: persisted configs whose original indicator class was folded into a
#: more general one with a discriminator parameter (e.g. ``"bbands_ema"``
#: was folded into ``"bbands"`` with ``ma_type="EMA"``).
#:
#: Migrations are applied at hydration time only; in-memory configs are
#: never re-keyed. The merged params are inserted only when the user's
#: persisted ``params`` dict does NOT already specify the discriminator
#: (so a user who manually edits the JSON to override always wins).
#: Subset of :data:`_KIND_ID_MIGRATIONS` that should ONLY apply to
#: chart-side persisted configs (loaded via
#: :meth:`indicators.config.IndicatorConfig.from_dict`). The scanner
#: surface intentionally keeps the legacy ``"sma"`` / ``"ema"`` field
#: ids visible (per :data:`scanner.fields.SCANNABLE_INDICATORS`), so
#: deserializing a scanner ``FieldRef`` must NOT rewrite the kind_id.
#: See ``scanner.model.FieldRef.from_dict``.
_CHART_ONLY_MIGRATION_KIND_IDS: frozenset = frozenset({"sma", "ema"})

_KIND_ID_MIGRATIONS: dict[str, tuple[str, dict[str, Any]]] = {
    "bbands_ema": ("bbands", {"ma_type": "EMA"}),
    "atr_sma":    ("atr",    {"ma_type": "SMA"}),
    # MA family collapse: legacy ``sma`` / ``ema`` indicators were
    # merged into a single ``ma`` indicator with a discriminator. The
    # discriminator and the source default ride along so an unparam'd
    # legacy config hydrates to identical behaviour.
    "sma": ("ma", {"ma_type": "SMA", "source": "Close"}),
    "ema": ("ma", {"ma_type": "EMA", "source": "Close"}),
    # RVOL family collapse: 6 legacy ids → unified ``rvol`` + mode + z_score.
    "rvol_simple":   ("rvol", {"mode": "simple"}),
    "rvol_cum":      ("rvol", {"mode": "cumulative"}),
    "rvol_tod":      ("rvol", {"mode": "time_of_day"}),
    "rvol_z_simple": ("rvol", {"mode": "simple",      "z_score": True}),
    "rvol_z_tod":    ("rvol", {"mode": "time_of_day", "z_score": True}),
    "rvol_z_cum":    ("rvol", {"mode": "cumulative",  "z_score": True}),
    # RRVOL family collapse: 3 legacy ids → unified ``rrvol`` + mode.
    "rrvol_simple":  ("rrvol", {"mode": "simple"}),
    "rrvol_cum":     ("rrvol", {"mode": "cumulative"}),
    "rrvol_tod":     ("rrvol", {"mode": "time_of_day"}),
}

#: Map of legacy MA ``kind_id`` -> persisted output key. After
#: migrating to the unified ``ma`` indicator (which emits ``"ma"``),
#: any ``"sma"`` / ``"ema"`` style keys must be remapped to ``"ma"``
#: so the user's customised colour / visibility / width survives the
#: collapse. Consumed by :mod:`indicators.config`.
_LEGACY_MA_OUTPUT_KEYS: dict[str, str] = {
    "sma": "sma",
    "ema": "ema",
}

#: Set of legacy z-score kind_ids whose ``style`` / ``output_key``
#: dicts use the ``"z"`` output key. After migrating to the unified
#: ``rvol`` indicator (which emits ``"rvol"``), any ``"z"`` keys must
#: be remapped to ``"rvol"`` so the user's customised colour /
#: visibility / output-routing survives the collapse. Consumers in
#: :mod:`indicators.config` and :mod:`scanner.model` apply the remap.
_LEGACY_Z_OUTPUT_KIND_IDS: frozenset = frozenset({
    "rvol_z_simple", "rvol_z_tod", "rvol_z_cum",
})


#: Indicator families whose legacy classes used the parameter name
#: ``lookback_days`` for what is now the unified :attr:`RVOL.length` /
#: :attr:`RRVOL.length`. Configs persisted by the legacy classes (or
#: by partial-migration builds that rewrote ``kind_id`` to the unified
#: id but left the parameter name unchanged) carry ``lookback_days``
#: in their params dict; the unified ``__init__`` only accepts
#: ``length``, so without a rename the factory call raises
#: ``TypeError: RVOL.__init__() got an unexpected keyword argument
#: 'lookback_days'``.
_LOOKBACK_DAYS_RENAME_FAMILIES: frozenset = frozenset({"rvol", "rrvol"})


def _rename_legacy_lookback_days(
    new_kind_id: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Rename ``lookback_days`` → ``length`` for the rvol/rrvol family.

    Pure helper. Idempotent. The rename is targeted to the unified
    rvol-family kind_ids so it cannot corrupt other indicators that
    happen to use a ``lookback_days`` parameter name (none currently
    do, but this guard future-proofs the contract).
    """
    if new_kind_id not in _LOOKBACK_DAYS_RENAME_FAMILIES:
        return params
    if "lookback_days" not in params:
        return params
    out = dict(params)
    legacy_val = out.pop("lookback_days")
    # Caller-supplied ``length`` always wins (mirrors the
    # discriminator-precedence rule in :func:`migrate_kind_id`).
    if "length" not in out:
        out["length"] = legacy_val
    return out


def migrate_kind_id(
    kind_id: str,
    params: dict[str, Any],
    *,
    include_chart_only: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Apply :data:`_KIND_ID_MIGRATIONS` and return ``(new_kind_id, new_params)``.

    Beyond the kind-id rewrite, also performs the
    ``lookback_days`` → ``length`` rename for the rvol/rrvol family
    (legacy classes used the longer name). The rename runs both:

    * On the freshly-migrated params (legacy ``kind_id`` path), AND
    * On already-migrated configs (when ``kind_id`` is already
      ``"rvol"`` or ``"rrvol"`` but the params still carry the legacy
      ``lookback_days`` key) — covers configs persisted during a
      partial-migration build.

    ``include_chart_only`` controls whether migrations listed in
    :data:`_CHART_ONLY_MIGRATION_KIND_IDS` (currently the
    ``sma``/``ema`` → ``ma`` collapse) are applied. Chart configs
    (``IndicatorConfig.from_dict``) pass ``True``; scanner configs
    (``FieldRef.from_dict``) leave the default ``False`` so that
    ``sma`` / ``ema`` remain scanner-visible field ids backed by the
    legacy registry entries.

    Returns the inputs unchanged when no migration applies. Never
    raises.
    """
    mig = _KIND_ID_MIGRATIONS.get(kind_id)
    if (
        mig is not None
        and not include_chart_only
        and kind_id in _CHART_ONLY_MIGRATION_KIND_IDS
    ):
        mig = None
    if mig is None:
        # Defensive pass: the kind_id is already current, but params
        # may still carry the legacy ``lookback_days`` key. This keeps
        # configs written by partial-migration builds loadable.
        if (
            kind_id in _LOOKBACK_DAYS_RENAME_FAMILIES
            and isinstance(params, dict)
            and "lookback_days" in params
        ):
            return kind_id, _rename_legacy_lookback_days(kind_id, params)
        return kind_id, params
    new_kid, defaults = mig
    new_params = dict(defaults)
    new_params.update(params or {})
    new_params = _rename_legacy_lookback_days(new_kid, new_params)
    return new_kid, new_params
