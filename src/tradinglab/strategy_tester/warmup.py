"""Compute the warmup-bar requirement for every indicator referenced
by an EntryStrategy + ExitStrategy.

Walks the condition trees (mirrors :func:`evaluator._walk_authored_intervals`)
and asks each indicator factory for its warmup bar count. Returns the
**max** warmup across all indicators (plus a safety margin); a single
indicator's longest warmup is enough — warmups don't compose additively.

The runner uses this to pre-pend a "warmup window" of historical bars
before the user's ``start_date`` so EMA(8), RSI(14), MACD, etc. are
fully hydrated by the moment the active backtest period begins.
Without it, the first ~N bars of Day 1 fire no trades because the
indicator value is NaN — a footgun the user explicitly called out.

Resolution order for any ``(kind_id, params)`` pair:

1. **Explicit opt-in.** If the instance exposes a ``warmup_bars``
   attribute (int) or method (no-arg, returns int), use that value.
   Indicators that know their exact convergence (e.g. Wilder's RSI
   needs ``4 × length`` for IIR convergence, not just ``length + 1``)
   declare this attribute so empirical first-valid detection doesn't
   under-count them.
2. **Empirical detection.** Otherwise, instantiate the factory and
   run ``compute_arr`` on a synthetic 500-bar OHLCV series; return
   ``max(first_finite_index across output series) + 1``. Handles every
   built-in indicator and any user plugin uniformly — no hardcoded
   per-indicator table.
3. **Fallback.** Unknown ``kind_id`` (factory lookup miss) or compute
   failure → :data:`DEFAULT_WARMUP_BARS`.

Per-process LRU cache on ``(kind_id, frozen_params)`` so each unique
indicator configuration only pays the empirical-compute cost once.

Public surface:

* :func:`required_warmup_bars` — strategy → bar-count walker.
* :func:`warmup_bars_for_kind` — per-indicator math (see resolution
  order above).
* :func:`bars_to_calendar_days` — bar-count → calendar-days converter
  (with a 1.5× safety margin for weekends + holidays).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np

from ..entries.model import EntryStrategy
from ..entries.model import TriggerKind as EntryTriggerKind
from ..exits.model import ExitStrategy
from ..exits.model import TriggerKind as ExitTriggerKind
from ..scanner.model import Condition as _ScannerCondition
from ..scanner.model import FieldRef as _ScannerFieldRef
from ..scanner.model import Group as _ScannerGroup

__all__ = [
    "DEFAULT_WARMUP_BARS",
    "WARMUP_SAFETY_MULTIPLIER",
    "required_warmup_bars",
    "warmup_bars_for_kind",
    "bars_to_calendar_days",
]


# Safe fallback for any unknown indicator kind_id — conservative upper bound
# so a custom plugin without explicit warmup metadata still gets enough bars.
DEFAULT_WARMUP_BARS: int = 100

# 1.5× safety on the raw bar count so partial-day warmup never leaves an
# indicator under-hydrated. Applied inside :func:`required_warmup_bars`.
WARMUP_SAFETY_MULTIPLIER: float = 1.5

# Length of the synthetic OHLCV series used for empirical first-valid
# probing. Big enough to seed any practical Wilder / EMA / Bollinger
# configuration; small enough that the per-indicator probe is cheap
# (<1 ms typical) and the result is cached anyway.
_EMPIRICAL_SAMPLE_N: int = 500


def _freeze_params(p: Mapping[str, Any] | None) -> tuple[tuple[str, Any], ...]:
    """Hashable, deterministic key for a params dict (mirrors scanner.engine)."""
    if not p:
        return ()
    out: list[tuple[str, Any]] = []
    for k, v in p.items():
        # Frozenset/tuple-ify list/dict values so the result stays hashable
        # even for the rare plugin that accepts container-valued params.
        if isinstance(v, list):
            v = tuple(v)
        elif isinstance(v, dict):
            v = tuple(sorted(v.items()))
        out.append((str(k), v))
    return tuple(sorted(out))


# Module-level memo: (kind_id, frozen_params) → bar count. Strategy Tester
# Runs typically mention the same EMA/RSI twice (entry + exit), so caching
# halves the empirical-compute cost. Cleared via tests by calling
# ``_WARMUP_CACHE.clear()`` if isolation is required.
_WARMUP_CACHE: dict[tuple[str, tuple[tuple[str, Any], ...]], int] = {}


def _warmup_bars_via_attribute(instance: object) -> int | None:
    """Try the opt-in fast path: read ``instance.warmup_bars``.

    Accepts either form:

    * ``warmup_bars`` is an ``int`` attribute (or any value coercible to int).
    * ``warmup_bars`` is a no-arg callable returning an int.

    Returns ``None`` when neither form is present or when coercion fails;
    callers then fall through to empirical detection.

    Indicators that know their exact warmup opt in by declaring this
    attribute/method (e.g. RSI's Wilder convergence requires ``4×length``,
    which empirical first-valid detection would under-count to ``length+1``).
    """
    attr = getattr(instance, "warmup_bars", None)
    if attr is None:
        return None
    if callable(attr):
        try:
            attr = attr()
        except Exception:  # noqa: BLE001 — broken probe ⇒ fall through
            return None
    try:
        n = int(attr)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _synthetic_bars(n: int = _EMPIRICAL_SAMPLE_N):
    """Build a deterministic synthetic intraday OHLCV ``Bars`` view.

    Geometric-Brownian-motion-ish closes (so RVOL/VWAP-style indicators
    see realistic price action), positive volumes, 5-minute spacing
    starting at 09:30 ET, all bars tagged ``session="regular"`` so
    session-aware indicators (VWAP, ATR-ToD) have something to chew on.

    Seeded RNG → repeated calls produce identical data, so the
    per-indicator empirical first-valid index is stable across runs.
    """
    from ..core.bars import Bars

    rng = np.random.default_rng(seed=42)
    returns = rng.normal(0.0001, 0.005, size=n)
    closes = 100.0 * np.exp(np.cumsum(returns))
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    highs = np.maximum(opens, closes) * (1.0 + np.abs(rng.normal(0, 0.002, size=n)))
    lows = np.minimum(opens, closes) * (1.0 - np.abs(rng.normal(0, 0.002, size=n)))
    volumes = rng.lognormal(10.0, 0.5, size=n)
    timestamps = (
        np.datetime64("2026-01-05T14:30")
        + np.arange(n, dtype="int64") * np.timedelta64(5, "m")
    )
    sessions = np.full(n, "regular", dtype=object)
    return Bars.from_arrays(
        open=opens, high=highs, low=lows, close=closes,
        volume=volumes, timestamps=timestamps, session=sessions,
    )


def _warmup_bars_empirical(instance: object) -> int:
    """Run the indicator on a synthetic 500-bar series and return the
    first-finite-index + 1, taken as the max across output series.

    Returns :data:`DEFAULT_WARMUP_BARS` when the indicator raises during
    compute OR returns all-NaN for every output series (defensive: a
    broken / mis-parameterised indicator shouldn't silently report a
    misleading 0-bar warmup).
    """
    try:
        bars = _synthetic_bars(_EMPIRICAL_SAMPLE_N)
        fn = getattr(instance, "compute_arr", None)
        if callable(fn):
            outputs = fn(bars)
        else:
            outputs = instance.compute(list(bars.candles or []))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return DEFAULT_WARMUP_BARS

    if not outputs:
        return DEFAULT_WARMUP_BARS

    max_first = -1
    for arr in outputs.values():
        try:
            a = np.asarray(arr)
        except Exception:  # noqa: BLE001
            continue
        if a.dtype.kind not in ("f", "i", "u"):
            continue
        mask = np.isfinite(a)
        idxs = np.flatnonzero(mask)
        if idxs.size == 0:
            continue
        first = int(idxs[0])
        if first > max_first:
            max_first = first

    if max_first < 0:
        # Every output was all-NaN over 500 bars — treat as unknown.
        return DEFAULT_WARMUP_BARS

    # warmup count = first_valid_index + 1 ("after this many bars, the
    # indicator is hydrated"). Floor at 1, cap at the sample size.
    return max(1, min(_EMPIRICAL_SAMPLE_N, max_first + 1))


def warmup_bars_for_kind(kind_id: str, params: dict[str, Any] | None) -> int:
    """Return the minimum bar count needed to seed indicator ``kind_id``.

    Resolution order (see module docstring for the rationale):

    1. ``instance.warmup_bars`` attribute / method — explicit opt-in.
    2. Empirical first-finite detection on a 500-bar synthetic series.
    3. :data:`DEFAULT_WARMUP_BARS` when the factory is unknown OR the
       compute path raises.

    Cached per-process by ``(kind_id, frozen_params)`` so repeated
    references inside one strategy (entry + exit) compute the warmup
    once. ``params`` may be ``None`` (treated as empty).
    """
    from ..indicators.base import factory_by_kind_id

    kid = (kind_id or "").strip().lower()
    cache_key = (kid, _freeze_params(params))
    cached = _WARMUP_CACHE.get(cache_key)
    if cached is not None:
        return cached

    entry = factory_by_kind_id(kid)
    if entry is None:
        _WARMUP_CACHE[cache_key] = DEFAULT_WARMUP_BARS
        return DEFAULT_WARMUP_BARS
    _display_name, factory = entry

    try:
        instance = factory(**(dict(params) if params else {}))
    except Exception:  # noqa: BLE001 — bad/unknown params ⇒ safe fallback
        _WARMUP_CACHE[cache_key] = DEFAULT_WARMUP_BARS
        return DEFAULT_WARMUP_BARS

    explicit = _warmup_bars_via_attribute(instance)
    if explicit is not None:
        _WARMUP_CACHE[cache_key] = explicit
        return explicit

    result = _warmup_bars_empirical(instance)
    _WARMUP_CACHE[cache_key] = result
    return result


def _walk_field_kinds(
    node: _ScannerGroup | _ScannerCondition | None,
) -> list[tuple[str, dict[str, Any]]]:
    """Yield every ``(indicator_kind_id, params)`` pair under a Group/Condition tree.

    Walks both the LHS (``node.left``) and any RHS :class:`FieldRef`
    values in ``node.params``. Literal / builtin field refs are skipped
    (they need no warmup). Returns ``[]`` for ``None`` so callers can
    pass optional condition trees directly.
    """
    out: list[tuple[str, dict[str, Any]]] = []
    if node is None:
        return out
    if isinstance(node, _ScannerCondition):
        for ref in (node.left, *list((node.params or {}).values())):
            if isinstance(ref, _ScannerFieldRef) and ref.kind == "indicator" and ref.id:
                out.append((str(ref.id), dict(ref.params or {})))
        return out
    # Group: recurse into children.
    for child in node.children:
        out.extend(_walk_field_kinds(child))
    return out


def required_warmup_bars(
    entry_strategy: EntryStrategy | None,
    exit_strategy: ExitStrategy | None,
) -> int:
    """Return the warmup bar count required by any indicator the strategies
    reference (max across indicators × :data:`WARMUP_SAFETY_MULTIPLIER`,
    rounded up).

    Returns ``0`` when neither strategy references an indicator-based
    trigger (e.g. MARKET entry + STOP exit — no condition trees to walk
    and no chandelier leg).
    """
    pairs: list[tuple[str, dict[str, Any]]] = []

    # Entry trigger condition tree (INDICATOR triggers).
    if entry_strategy is not None:
        et = entry_strategy.trigger
        if et.kind is EntryTriggerKind.INDICATOR and et.condition is not None:
            pairs.extend(_walk_field_kinds(et.condition))
        # SCANNER_ALERT entry references a scan definition that we can't
        # cheaply resolve here without touching disk; the runner's
        # condition walk in collect_interval_overrides loads it explicitly.
        # For warmup, an unknown scan falls back to the default fallback
        # via the empty-pairs branch below.

    # Exit-leg INDICATOR triggers + CHANDELIER triggers (which carry their
    # own indicator-style params on the trigger object itself).
    if exit_strategy is not None:
        for leg in exit_strategy.legs:
            if not leg.enabled:
                continue
            for trig in leg.triggers:
                if not trig.enabled:
                    continue
                if trig.kind is ExitTriggerKind.INDICATOR and trig.condition is not None:
                    pairs.extend(_walk_field_kinds(trig.condition))
                elif trig.kind is ExitTriggerKind.CHANDELIER:
                    pairs.append((
                        "chandelier",
                        {
                            "lookback": int(trig.chandelier_lookback),
                            "atr_period": int(trig.chandelier_atr_period),
                        },
                    ))

    if not pairs:
        return 0

    max_bars = 0
    for kid, params in pairs:
        n = warmup_bars_for_kind(kid, params)
        if n > max_bars:
            max_bars = n

    return int(math.ceil(max_bars * WARMUP_SAFETY_MULTIPLIER))


# Approximate bar counts per US-equity RTH trading day (390 min / day).
_BARS_PER_RTH_DAY: dict[str, int] = {
    "1m": 390,
    "5m": 78,
    "15m": 26,
    "30m": 13,
    "1h": 7,   # 6.5h session rounded up
    "1d": 1,
    "1w": 1,   # one bar per week — handled specially below
}


def bars_to_calendar_days(bars: int, interval: str) -> int:
    """Convert a bar count at ``interval`` to a calendar-day window.

    Multiplies the trading-day count by **1.5** to absorb weekends +
    US-equity holidays so the runner's pre-pended fetch range is wide
    enough to actually contain ``bars`` trading bars after the date
    slice. Always returns ≥ ``1`` when ``bars > 0``.

    For ``1w`` we multiply by 7 (one bar ≈ one calendar week).
    Unknown intervals are treated as ``1d``.
    """
    if bars <= 0:
        return 0
    iv = (interval or "1d").lower()
    if iv == "1w":
        return max(1, int(math.ceil(bars * 7)))
    per_day = _BARS_PER_RTH_DAY.get(iv, 1)
    trading_days = math.ceil(bars / per_day)
    calendar_days = math.ceil(trading_days * 1.5)
    return max(1, int(calendar_days))
