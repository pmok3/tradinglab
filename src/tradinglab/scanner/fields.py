"""Scanner field registry: the catalog of values that may appear in a FieldRef.

Single source of truth for "what can be used in a scan condition". Both
the engine (validation + dispatch) and the GUI (combobox population)
consume this module.

Two field kinds are surfaced:

1. **Built-in scalars** — declared inline in this module. Cheap,
   schema-stable values computed directly from OHLCV NumPy arrays:
   ``close``, ``open``, ``high``, ``low``, ``volume``, ``pct_change``,
   ``gap_pct``, ``hod``, ``lod``, ``time_of_day``, ``bars_since_open``.

2. **Allowlisted indicators** — projected from
   :data:`tradinglab.indicators.base.INDICATORS` via an explicit
   ``SCANNABLE_INDICATORS`` allowlist that maps each ``kind_id`` to the
   output keys that are numerically scannable (e.g. Bollinger has
   ``upper`` / ``middle`` / ``lower``; SMA has just ``sma``).

   **Fail-closed policy:** indicators not in the allowlist are NOT
   surfaced to the scanner, even if registered chartable. This kills
   the footgun where a user picks a categorical/boolean indicator
   output in a numeric comparison and gets silent ``None`` everywhere.
   See ``scanner/fields.spec.md`` for the rationale.

The compute callables here all accept a ``BarsNp`` view (OHLCV as NumPy
columns) plus the current bar index. Returning ``None`` means
insufficient data; the engine propagates that as tri-valued ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import numpy as np

from ..core.bars import Bars, _to_naive_utc
from ..core.heikin_ashi import ha_arrays
from ..indicators.base import INDICATORS, ParamDef, factory_by_kind_id
from ..models import Candle
from .model import FIELD_KIND_BUILTIN, FIELD_KIND_INDICATOR, FieldRef

# ---------------------------------------------------------------------------
# OHLCV NumPy view
# ---------------------------------------------------------------------------
#
# ``BarsNp`` is now an alias for the canonical :class:`tradinglab.core.bars.Bars`.
# The historical scanner-only definition was the prototype for ``Bars``;
# we keep the alias because callers across the scanner package (engine,
# fields registry, tests) and the field-spec docstrings reference
# ``BarsNp`` by name. New code should prefer ``Bars`` directly.

BarsNp = Bars


# ---------------------------------------------------------------------------
# FieldSpec
# ---------------------------------------------------------------------------

DTYPE_NUMERIC = "numeric"
DTYPE_BOOL    = "bool"

# Built-in compute signature: (bars, current_index, params) -> float|None.
BuiltinCompute = Callable[[BarsNp, int, Mapping[str, Any]], Optional[float]]


@dataclass(frozen=True)
class FieldSpec:
    """One row in the field catalog.

    - ``id``           — stable identifier; matches :attr:`FieldRef.id`.
    - ``label``        — human-readable label for the field combobox.
    - ``kind``         — ``"builtin"`` or ``"indicator"``.
    - ``dtype``        — ``"numeric"`` or ``"bool"``. Engine refuses
                          mixed-type comparisons.
    - ``params_schema``— for builtins, an empty tuple in v1; for
                          indicators, mirrors the indicator's
                          ``ParamDef`` tuple.
    - ``output_keys``  — for indicators with multiple outputs (Bollinger,
                          MACD); for builtins or single-output indicators,
                          a one-element tuple containing the canonical
                          key (empty string ``""`` permitted to mean "the
                          default output").
    - ``default_output_key`` — output key used when
                          :attr:`FieldRef.output_key` is empty.
    - ``builtin_compute`` — populated only for builtin fields.
    - ``description``  — short docstring shown as tooltip.
    """

    id: str
    label: str
    kind: str
    dtype: str = DTYPE_NUMERIC
    params_schema: Tuple[ParamDef, ...] = ()
    output_keys: Tuple[str, ...] = ("",)
    default_output_key: str = ""
    builtin_compute: Optional[BuiltinCompute] = None
    description: str = ""
    # ``True`` if the field's value is anchored to the current session
    # (resets every market open) — e.g. VWAP, HOD/LOD, time-of-day RVOL.
    # The within-last-N-bars walk uses this flag to clamp the look-back
    # window's lower bound to the session-open index when ANY FieldRef
    # in the (sub)condition references a resets_daily field, so a
    # 9:35 AM "VWAP reclaim within last 5 bars" check doesn't peek at
    # yesterday's close. Path-dependent indicators (EMA, RSI, ATR, ...)
    # set this False — they correctly carry across session boundaries.
    resets_daily: bool = False


# ---------------------------------------------------------------------------
# Built-in scalar compute callables
# ---------------------------------------------------------------------------


def _at(arr: np.ndarray, i: int) -> Optional[float]:
    """Return ``arr[i]`` as a Python float, or ``None`` for OOB / NaN."""
    if i < 0 or i >= arr.size:
        return None
    v = arr[i]
    if isinstance(v, float) and v != v:  # NaN
        return None
    return float(v)


def _b_close (b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]: return _at(b.close,  i)
def _b_open  (b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]: return _at(b.open,   i)
def _b_high  (b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]: return _at(b.high,   i)
def _b_low   (b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]: return _at(b.low,    i)
def _b_volume(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]: return _at(b.volume, i)


def _b_pct_change(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    """Percentage change of close vs previous bar's close, in percent."""
    if i < 1 or i >= b.close.size:
        return None
    prev = b.close[i - 1]
    cur  = b.close[i]
    if not np.isfinite(prev) or not np.isfinite(cur) or prev == 0.0:
        return None
    return float((cur - prev) / prev * 100.0)


def _b_gap_pct(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    """Open-vs-prior-close gap in percent.

    Defined as ``(open[i] - close[i-1]) / close[i-1] * 100``. For
    intraday bars this is most useful on the first bar of a new
    session, but we compute it unconditionally — the engine has no
    cross-bar session awareness in v1.
    """
    if i < 1 or i >= b.open.size:
        return None
    prev = b.close[i - 1]
    o    = b.open[i]
    if not np.isfinite(prev) or not np.isfinite(o) or prev == 0.0:
        return None
    return float((o - prev) / prev * 100.0)


def _today_mask(b: BarsNp, i: int) -> Optional[np.ndarray]:
    """Boolean mask of bars sharing the same calendar date as ``b[i]``."""
    if i < 0 or i >= b.timestamps.size:
        return None
    today = b.timestamps[i].astype("datetime64[D]")
    days = b.timestamps.astype("datetime64[D]")
    mask = days == today
    # Restrict to the prefix [0..i] so HOD/LOD reflect what the trader
    # has actually seen at the current bar — no look-ahead.
    mask[i + 1:] = False
    return mask


def _b_hod(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    mask = _today_mask(b, i)
    if mask is None or not mask.any():
        return None
    h = b.high[mask]
    h = h[np.isfinite(h)]
    return float(h.max()) if h.size else None


def _b_lod(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    mask = _today_mask(b, i)
    if mask is None or not mask.any():
        return None
    lo = b.low[mask]
    lo = lo[np.isfinite(lo)]
    return float(lo.min()) if lo.size else None


def _b_time_of_day(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    """Minutes since UTC midnight at the current bar's timestamp."""
    if i < 0 or i >= b.timestamps.size:
        return None
    ts = b.timestamps[i]
    day_start = ts.astype("datetime64[D]").astype("datetime64[ns]")
    delta = (ts - day_start).astype("timedelta64[s]").astype(np.int64)
    return float(delta // 60)


def _b_bars_since_open(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    """Number of bars elapsed since the first regular-session bar of today.

    The first regular-session bar of today returns 0; the next returns
    1, etc. For pre-market bars before the regular session opens,
    returns 0 if there is no regular bar yet today.
    """
    if i < 0 or i >= b.timestamps.size:
        return None
    today = b.timestamps[i].astype("datetime64[D]")
    days = b.timestamps.astype("datetime64[D]")
    same_day = (days == today) & (np.arange(b.timestamps.size) <= i)
    regular = b.session == "regular"
    candidates = np.where(same_day & regular)[0]
    if candidates.size == 0:
        return 0.0
    return float(i - int(candidates[0]))


# ---------------------------------------------------------------------------
# Heikin-Ashi builtins
# ---------------------------------------------------------------------------
#
# HA values are recursive (HA_Open[i] depends on HA_Open[i-1] and
# HA_Close[i-1]) so a per-bar fetch must walk the full prefix. We cache
# the four HA arrays on a ``WeakValueDictionary``-style side table keyed
# by ``(id(BarsNp), len)`` so multiple ``ha_*`` fields evaluated against
# the same ``BarsNp`` snapshot on the same tick share one O(n) compute.
#
# The cache is intentionally process-global (not per ScanRunner) — it's
# keyed by ``id(b)`` which is unique per snapshot object, and BarsNp is
# rebuilt per tick, so stale entries die naturally when the snapshot is
# garbage-collected. We use a weakref-backed dict to avoid pinning.
#
# Note: BarsNp is a frozen dataclass holding NumPy arrays; it doesn't
# support __weakref__ by default. We therefore key on ``id(b)`` and
# additionally store the array's ``data.tobytes`` length so a recycled
# id can't return a stale entry. A small LRU cap keeps memory bounded.

from ._bars_cache import BarsKeyedCache

_ha_cache: BarsKeyedCache[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = BarsKeyedCache(max_size=64)


def _ha_for(b: BarsNp) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return cached or freshly-computed HA arrays for ``b``."""
    return _ha_cache.get_or_compute(
        b,
        lambda x: ha_arrays(x.open, x.high, x.low, x.close),
        extra_key=int(b.close.size),
    )


def _b_ha_open(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    return _at(_ha_for(b)[0], i)


def _b_ha_high(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    return _at(_ha_for(b)[1], i)


def _b_ha_low(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    return _at(_ha_for(b)[2], i)


def _b_ha_close(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    return _at(_ha_for(b)[3], i)


def _b_ha_color(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    """+1.0 if HA bar is bullish (HA_C >= HA_O), -1.0 if bearish, None on NaN."""
    ha_o, _hh, _hl, ha_c = _ha_for(b)
    if i < 0 or i >= ha_c.size:
        return None
    o = ha_o[i]; c = ha_c[i]
    if not (np.isfinite(o) and np.isfinite(c)):
        return None
    return 1.0 if c >= o else -1.0


def _ha_flat_eps(price: float) -> float:
    """Tolerance for HA flat-top / flat-bottom equality, scaled with price."""
    return max(1e-9, abs(price) * 1e-9)


def _b_ha_flat_top(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    """1.0 iff HA_High[i] == HA_Open[i] (no upper wick → bearish continuation)."""
    ha_o, ha_h, _hl, _hc = _ha_for(b)
    if i < 0 or i >= ha_h.size:
        return None
    o = ha_o[i]; h = ha_h[i]
    if not (np.isfinite(o) and np.isfinite(h)):
        return None
    return 1.0 if abs(h - o) <= _ha_flat_eps(o) else 0.0


def _b_ha_flat_bottom(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    """1.0 iff HA_Low[i] == HA_Open[i] (no lower wick → bullish continuation)."""
    ha_o, _hh, ha_l, _hc = _ha_for(b)
    if i < 0 or i >= ha_l.size:
        return None
    o = ha_o[i]; lo = ha_l[i]
    if not (np.isfinite(o) and np.isfinite(lo)):
        return None
    return 1.0 if abs(o - lo) <= _ha_flat_eps(o) else 0.0


def _ha_streak_signed(b: BarsNp, i: int) -> Optional[int]:
    """Length of the run of same-color HA bars ending at i; positive bull, negative bear."""
    ha_o, _hh, _hl, ha_c = _ha_for(b)
    if i < 0 or i >= ha_c.size:
        return None
    o_i = ha_o[i]; c_i = ha_c[i]
    if not (np.isfinite(o_i) and np.isfinite(c_i)):
        return None
    bull = c_i >= o_i
    n = 1
    j = i - 1
    while j >= 0:
        oj = ha_o[j]; cj = ha_c[j]
        if not (np.isfinite(oj) and np.isfinite(cj)):
            break
        bull_j = cj >= oj
        if bull_j != bull:
            break
        n += 1
        j -= 1
    return n if bull else -n


def _b_ha_streak(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    s = _ha_streak_signed(b, i)
    return None if s is None else float(s)


def _ha_flat_run(b: BarsNp, i: int, *, want_top: bool) -> Optional[int]:
    """Count consecutive flat-top (or flat-bottom) bars ending at i."""
    ha_o, ha_h, ha_l, _hc = _ha_for(b)
    if i < 0 or i >= ha_h.size:
        return None
    n = 0
    for j in range(i, -1, -1):
        oj = ha_o[j]
        ref = ha_h[j] if want_top else ha_l[j]
        if not (np.isfinite(oj) and np.isfinite(ref)):
            break
        eps = _ha_flat_eps(oj)
        is_flat = abs(ref - oj) <= eps if want_top else abs(oj - ref) <= eps
        if not is_flat:
            break
        n += 1
    return n


def _b_ha_flat_top_streak(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    n = _ha_flat_run(b, i, want_top=True)
    return None if n is None else float(n)


def _b_ha_flat_bottom_streak(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    n = _ha_flat_run(b, i, want_top=False)
    return None if n is None else float(n)


# ---------------------------------------------------------------------------
# Direction-aware HA flat patterns (the "strong-trend" continuation signal
# rendered by the View → Highlight Flat HA Candles overlay)
# ---------------------------------------------------------------------------
#
# These three builtins narrow the existing direction-agnostic ``ha_flat_top``
# / ``ha_flat_bottom`` to the trader-canonical *strong* variants:
#
# * ``ha_flat_bottom_bull`` — 1.0 iff bull HA bar with no lower wick
# * ``ha_flat_top_bear``    — 1.0 iff bear HA bar with no upper wick
# * ``ha_flat_strong``      — signed: +1 / -1 / 0 (None during warm-up)
#
# Doji bars (``HA_close == HA_open``) never qualify — by design, they
# are not a strong-trend signal. Computation reuses the shared HA cache
# (``_ha_for(b)``) plus the same price-scaled epsilon
# (:func:`_ha_flat_eps`) the chart overlay uses, so the chart and the
# scanner classify every bar identically.

from ..core.ha_flat import (
    HA_FLAT_BEAR,
    HA_FLAT_BULL,
    HA_FLAT_NONE,
    HA_FLAT_UNKNOWN,
    HAFlatArrays,
)
from ..core.ha_flat import (
    compute_ha_flat_arrays_np as _compute_ha_flat_np,
)

_ha_flat_cache: BarsKeyedCache[HAFlatArrays] = BarsKeyedCache(max_size=64)


def _ha_flat_for(b: BarsNp) -> HAFlatArrays:
    """Cached HAFlatArrays for ``b``; mirrors :func:`_ha_for` / :func:`_kb_for`."""
    return _ha_flat_cache.get_or_compute(
        b,
        lambda x: _compute_ha_flat_np(x.open, x.high, x.low, x.close),
        extra_key=int(b.close.size),
    )


def _b_ha_flat_bottom_bull(
    b: BarsNp, i: int, p: Mapping[str, Any]
) -> Optional[float]:
    """1.0 iff bar ``i`` is a bull HA candle with no lower wick (strong up)."""
    res = _ha_flat_for(b)
    if i < 0 or i >= res.signed.size:
        return None
    s = int(res.signed[i])
    if s == HA_FLAT_UNKNOWN:
        return None
    return 1.0 if s == HA_FLAT_BULL else 0.0


def _b_ha_flat_top_bear(
    b: BarsNp, i: int, p: Mapping[str, Any]
) -> Optional[float]:
    """1.0 iff bar ``i`` is a bear HA candle with no upper wick (strong down)."""
    res = _ha_flat_for(b)
    if i < 0 or i >= res.signed.size:
        return None
    s = int(res.signed[i])
    if s == HA_FLAT_UNKNOWN:
        return None
    return 1.0 if s == HA_FLAT_BEAR else 0.0


def _b_ha_flat_strong(
    b: BarsNp, i: int, p: Mapping[str, Any]
) -> Optional[float]:
    """+1 bull-flat-bottom / -1 bear-flat-top / 0 neither; None during warm-up."""
    res = _ha_flat_for(b)
    if i < 0 or i >= res.signed.size:
        return None
    s = int(res.signed[i])
    if s == HA_FLAT_UNKNOWN:
        return None
    return float(s)


# ---------------------------------------------------------------------------
# Key bar (RDT-style wide-range / igniting bar)
# ---------------------------------------------------------------------------
#
# Key-bar arrays are computed by ``core.key_bar.compute_key_bar_arrays``
# which drives ATR(mode="tod") + TimeOfDayRVOL (intraday) or rolling
# 20-bar means (daily/weekly/monthly) under the hood. To bridge the
# scanner's columnar :class:`BarsNp` view with that candle-list API,
# we cache the reconstructed ``List[Candle]`` and resulting key-bar
# arrays per ``id(BarsNp)`` (BarsNp is per-tick-immutable so this is
# safe under the same single-tick-snapshot rules used by the HA cache).

from ..core.key_bar import KeyBarArrays
from ..core.key_bar import compute_key_bar_arrays_np as _compute_kb_np

_kb_cache: BarsKeyedCache[KeyBarArrays] = BarsKeyedCache(max_size=64)


def _kb_for(b: BarsNp) -> KeyBarArrays:
    return _kb_cache.get_or_compute(b, _compute_kb_np)


def _kb_at_int8(arr: np.ndarray, i: int) -> Optional[float]:
    if i < 0 or i >= arr.size:
        return None
    v = int(arr[i])
    # KEY_BAR_UNKNOWN sentinel → tri-valued None.
    if v == -128:
        return None
    return float(v)


def _kb_at_int64(arr: np.ndarray, i: int) -> Optional[float]:
    if i < 0 or i >= arr.size:
        return None
    v = int(arr[i])
    if v < 0:  # -1 means "no key bar yet"
        return None
    return float(v)


def _b_key_bar(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    return _kb_at_int8(_kb_for(b).signed, i)


def _b_key_bar_bull(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    s = _kb_at_int8(_kb_for(b).signed, i)
    if s is None:
        return None
    return 1.0 if s == 1.0 else 0.0


def _b_key_bar_bear(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    s = _kb_at_int8(_kb_for(b).signed, i)
    if s is None:
        return None
    return 1.0 if s == -1.0 else 0.0


def _b_bars_since_bull_kb(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    return _kb_at_int64(_kb_for(b).bars_since_bull, i)


def _b_bars_since_bear_kb(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    return _kb_at_int64(_kb_for(b).bars_since_bear, i)


def _b_last_bull_kb_high(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    return _at(_kb_for(b).last_bull_high, i)


def _b_last_bull_kb_low(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    return _at(_kb_for(b).last_bull_low, i)


def _b_last_bear_kb_high(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    return _at(_kb_for(b).last_bear_high, i)


def _b_last_bear_kb_low(b: BarsNp, i: int, p: Mapping[str, Any]) -> Optional[float]:
    return _at(_kb_for(b).last_bear_low, i)


# ---------------------------------------------------------------------------
# Built-in catalog
# ---------------------------------------------------------------------------

_BUILTINS: Tuple[FieldSpec, ...] = (
    FieldSpec(id="close",  label="Close",  kind="builtin",
              builtin_compute=_b_close,  description="Closing price"),
    FieldSpec(id="open",   label="Open",   kind="builtin",
              builtin_compute=_b_open,   description="Opening price"),
    FieldSpec(id="high",   label="High",   kind="builtin",
              builtin_compute=_b_high,   description="Bar high"),
    FieldSpec(id="low",    label="Low",    kind="builtin",
              builtin_compute=_b_low,    description="Bar low"),
    FieldSpec(id="volume", label="Volume", kind="builtin",
              builtin_compute=_b_volume, description="Bar volume"),
    FieldSpec(id="pct_change", label="% Change",
              kind="builtin", builtin_compute=_b_pct_change,
              description="Percent change vs prior close"),
    FieldSpec(id="gap_pct", label="Gap %",
              kind="builtin", builtin_compute=_b_gap_pct,
              description="Open vs prior-close gap, percent"),
    FieldSpec(id="hod", label="High of Day",
              kind="builtin", builtin_compute=_b_hod,
              description="Highest high so far today",
              resets_daily=True),
    FieldSpec(id="lod", label="Low of Day",
              kind="builtin", builtin_compute=_b_lod,
              description="Lowest low so far today",
              resets_daily=True),
    FieldSpec(id="time_of_day", label="Time of Day (min)",
              kind="builtin", builtin_compute=_b_time_of_day,
              description="Minutes since midnight UTC",
              resets_daily=True),
    FieldSpec(id="bars_since_open", label="Bars Since Open",
              kind="builtin", builtin_compute=_b_bars_since_open,
              description="Bars since first regular-session bar today",
              resets_daily=True),
    # --- Heikin-Ashi (display-derived; advisory) ----------------------
    FieldSpec(id="ha_open",  label="HA Open",  kind="builtin",
              builtin_compute=_b_ha_open,
              description="Heikin-Ashi open"),
    FieldSpec(id="ha_high",  label="HA High",  kind="builtin",
              builtin_compute=_b_ha_high,
              description="Heikin-Ashi high"),
    FieldSpec(id="ha_low",   label="HA Low",   kind="builtin",
              builtin_compute=_b_ha_low,
              description="Heikin-Ashi low"),
    FieldSpec(id="ha_close", label="HA Close", kind="builtin",
              builtin_compute=_b_ha_close,
              description="Heikin-Ashi close"),
    FieldSpec(id="ha_color", label="HA Color (+1/-1)", kind="builtin",
              builtin_compute=_b_ha_color,
              description="+1 if HA bullish (close>=open), -1 if bearish"),
    FieldSpec(id="ha_flat_top", label="HA Flat-Top", kind="builtin",
              builtin_compute=_b_ha_flat_top,
              description="1 if HA bar has no upper wick (bearish continuation)"),
    FieldSpec(id="ha_flat_bottom", label="HA Flat-Bottom", kind="builtin",
              builtin_compute=_b_ha_flat_bottom,
              description="1 if HA bar has no lower wick (bullish continuation)"),
    FieldSpec(id="ha_streak", label="HA Streak (signed)", kind="builtin",
              builtin_compute=_b_ha_streak,
              description="Run length of same-color HA bars; +N bull / -N bear"),
    FieldSpec(id="ha_flat_top_streak", label="HA Flat-Top Streak",
              kind="builtin", builtin_compute=_b_ha_flat_top_streak,
              description="Consecutive HA bars with no upper wick"),
    FieldSpec(id="ha_flat_bottom_streak", label="HA Flat-Bottom Streak",
              kind="builtin", builtin_compute=_b_ha_flat_bottom_streak,
              description="Consecutive HA bars with no lower wick"),
    # --- Direction-aware HA flat (strong-trend continuation signals) ---
    # Mirror of the View → Highlight Flat HA Candles overlay; identical
    # classification (bull-flat-bottom + bear-flat-top) + price-scaled eps.
    FieldSpec(id="ha_flat_bottom_bull", label="HA Flat-Bottom (Bull)",
              kind="builtin", builtin_compute=_b_ha_flat_bottom_bull,
              description="1 if bull HA bar with no lower wick (strong up); 0 otherwise; None during warmup"),
    FieldSpec(id="ha_flat_top_bear", label="HA Flat-Top (Bear)",
              kind="builtin", builtin_compute=_b_ha_flat_top_bear,
              description="1 if bear HA bar with no upper wick (strong down); 0 otherwise; None during warmup"),
    FieldSpec(id="ha_flat_strong", label="HA Flat (Strong, signed)",
              kind="builtin", builtin_compute=_b_ha_flat_strong,
              description="+1 bull-flat-bottom / -1 bear-flat-top / 0 neither; None during warmup"),
    # --- Key bar (RDT-style wide-range / igniting bar) -----------------
    FieldSpec(id="key_bar", label="Key Bar (signed)", kind="builtin",
              builtin_compute=_b_key_bar,
              description="+1 bull / -1 bear / 0 not a key bar; None during warmup"),
    FieldSpec(id="key_bar_bull", label="Key Bar (Bull)", kind="builtin",
              builtin_compute=_b_key_bar_bull,
              description="1 if this bar is a bull key bar, else 0"),
    FieldSpec(id="key_bar_bear", label="Key Bar (Bear)", kind="builtin",
              builtin_compute=_b_key_bar_bear,
              description="1 if this bar is a bear key bar, else 0"),
    FieldSpec(id="bars_since_bull_key_bar", label="Bars Since Bull Key Bar",
              kind="builtin", builtin_compute=_b_bars_since_bull_kb,
              description="Bars elapsed since the most recent bull key bar"),
    FieldSpec(id="bars_since_bear_key_bar", label="Bars Since Bear Key Bar",
              kind="builtin", builtin_compute=_b_bars_since_bear_kb,
              description="Bars elapsed since the most recent bear key bar"),
    FieldSpec(id="last_bull_key_bar_high", label="Last Bull Key Bar High",
              kind="builtin", builtin_compute=_b_last_bull_kb_high,
              description="High of the most recent bull key bar"),
    FieldSpec(id="last_bull_key_bar_low", label="Last Bull Key Bar Low",
              kind="builtin", builtin_compute=_b_last_bull_kb_low,
              description="Low of the most recent bull key bar"),
    FieldSpec(id="last_bear_key_bar_high", label="Last Bear Key Bar High",
              kind="builtin", builtin_compute=_b_last_bear_kb_high,
              description="High of the most recent bear key bar"),
    FieldSpec(id="last_bear_key_bar_low", label="Last Bear Key Bar Low",
              kind="builtin", builtin_compute=_b_last_bear_kb_low,
              description="Low of the most recent bear key bar"),
)


# ---------------------------------------------------------------------------
# Indicator allowlist
# ---------------------------------------------------------------------------

# Map indicator ``kind_id`` → tuple of (output_key, dtype) pairs that are
# scannable. The first entry's ``output_key`` is taken as the default
# when :attr:`FieldRef.output_key` is empty.
#
# Indicators not in this dict are NOT surfaced to the scanner. Adding a
# new indicator to the chart does NOT automatically add it here — that's
# intentional: indicator authors must opt their indicator into scanning.
SCANNABLE_INDICATORS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "sma":  (("sma",  DTYPE_NUMERIC),),
    "ema":  (("ema",  DTYPE_NUMERIC),),
    "rsi":  (("rsi",  DTYPE_NUMERIC),),
    "bbands": (("middle", DTYPE_NUMERIC),
               ("upper",  DTYPE_NUMERIC),
               ("lower",  DTYPE_NUMERIC)),
    "atr":  (("atr",  DTYPE_NUMERIC),),
    "adx":  (("adx",  DTYPE_NUMERIC),
             ("+di",  DTYPE_NUMERIC),
             ("-di",  DTYPE_NUMERIC)),
    "vwap": (("vwap", DTYPE_NUMERIC),),
    "avwap":(("avwap",DTYPE_NUMERIC),),
    "smi":  (("smi",  DTYPE_NUMERIC),
             ("signal", DTYPE_NUMERIC)),
    "lrsi": (("lrsi", DTYPE_NUMERIC),),
    "rvol":  (("rvol", DTYPE_NUMERIC),),
    "rrvol": (("rvol", DTYPE_NUMERIC),),
}


# Indicators whose output is anchored to the current trading session
# (i.e. resets at the session open). Within-last-N-bars walks clamp to
# session-open when any FieldRef in the (sub)condition is in this set,
# so a 9:35 AM "VWAP reclaim within last 5 bars" check doesn't peek at
# yesterday's close.
#
# * ``vwap``: textbook session VWAP — anchored to today's open.
# * ``rvol``/``rrvol``: dominantly used in cumulative or time-of-day
#   modes which are daily-reset; rolling mode is the minority and the
#   clamp is conservative-safe (it only narrows the window).
#
# Path-dependent indicators (``sma``, ``ema``, ``rsi``, ``atr``, ``adx``,
# ``bbands``, ``smi``, ``lrsi``) carry across session boundaries and are
# intentionally NOT in this set — clamping them would distort the
# look-back semantics in setups that legitimately need cross-day context.
#
# ``avwap`` (anchored VWAP) is also not in this set: the anchor can be
# arbitrary (not necessarily session-open), and we don't want to falsely
# clamp a multi-day-anchored AVWAP to today only.
INDICATORS_RESETTING_DAILY: Tuple[str, ...] = (
    "vwap",
    "rvol",
    "rrvol",
)


def _indicator_field_specs() -> List[FieldSpec]:
    """Project the allowlist over the live indicator registry.

    Indicators present in the allowlist but not in ``INDICATORS`` (e.g.
    not yet imported) are skipped silently. Indicators present in
    ``INDICATORS`` but not in the allowlist are not surfaced.
    """
    out: List[FieldSpec] = []
    for kind_id, outputs in SCANNABLE_INDICATORS.items():
        entry = factory_by_kind_id(kind_id)
        if entry is None:
            continue
        display_name, factory = entry
        full_schema = tuple(getattr(factory, "params_schema", ()) or ())
        # Indicators may opt into a smaller schema for the trigger /
        # scanner block-editor form by declaring TRIGGER_RELEVANT_PARAMS.
        # When unset (the default), the full schema is exposed — matching
        # the legacy behaviour. When set, only those params are surfaced
        # in the entries / exits / scanner UIs; the rest are still
        # accepted by ``__init__`` (using their defaults / persisted
        # values) so persisted strategies round-trip cleanly. See
        # :class:`tradinglab.indicators.rvol.RVOL` for the canonical
        # example: ``threshold_warn`` / ``threshold_extreme`` are render-
        # only axhlines and have no effect on the rvol output values
        # the trigger evaluates against.
        trigger_relevant = getattr(factory, "TRIGGER_RELEVANT_PARAMS", None)
        if trigger_relevant is not None:
            keep = set(trigger_relevant)
            params_schema = tuple(p for p in full_schema if p.name in keep)
        else:
            params_schema = full_schema
        keys = tuple(k for k, _ in outputs)
        default_key = keys[0] if keys else ""
        out.append(FieldSpec(
            id=kind_id,
            label=display_name,
            kind="indicator",
            dtype=DTYPE_NUMERIC,
            params_schema=params_schema,
            output_keys=keys,
            default_output_key=default_key,
            description=getattr(factory, "__doc__", "") or "",
            resets_daily=(kind_id in INDICATORS_RESETTING_DAILY),
        ))
    return out


# ---------------------------------------------------------------------------
# Public registry API
# ---------------------------------------------------------------------------


def all_fields() -> List[FieldSpec]:
    """Return every scannable field, builtins first, then indicators.

    Computed lazily on each call so newly registered indicators appear
    automatically without app restart.
    """
    return list(_BUILTINS) + _indicator_field_specs()


def get_field(field_id: str, *, kind: str = "") -> Optional[FieldSpec]:
    """Look up a :class:`FieldSpec` by its stable id.

    ``kind`` may be ``"builtin"`` or ``"indicator"`` to disambiguate in
    the unlikely case a builtin and indicator share an id; empty string
    accepts the first match.
    """
    for spec in all_fields():
        if spec.id != field_id:
            continue
        if kind and spec.kind != kind:
            continue
        return spec
    return None


def is_scannable(ref: FieldRef) -> bool:
    """Return True iff ``ref`` references a registered, scannable field.

    Literals always pass. Builtins / indicators must be in the catalog
    and (for indicators) declare the requested ``output_key`` in their
    allowed outputs (empty ``output_key`` means "default").
    """
    if ref.kind == "literal":
        return True
    spec = get_field(ref.id, kind=ref.kind)
    if spec is None:
        return False
    if ref.kind == FIELD_KIND_INDICATOR and ref.output_key:
        return ref.output_key in spec.output_keys
    return True


def validate_field_ref(ref: FieldRef) -> None:
    """Raise :class:`ValueError` if ``ref`` is not scannable."""
    if ref.kind == "literal":
        return
    spec = get_field(ref.id, kind=ref.kind)
    if spec is None:
        raise ValueError(
            f"FieldRef references unknown {ref.kind} field id={ref.id!r}; "
            f"check the scanner field registry / allowlist"
        )
    if ref.kind == FIELD_KIND_INDICATOR and ref.output_key:
        if ref.output_key not in spec.output_keys:
            raise ValueError(
                f"Indicator {ref.id!r} does not expose scannable output "
                f"{ref.output_key!r}; allowed: {spec.output_keys}"
            )


def builtin_compute(field_id: str) -> Optional[BuiltinCompute]:
    """Return the compute callable for a builtin field id, or ``None``."""
    for spec in _BUILTINS:
        if spec.id == field_id:
            return spec.builtin_compute
    return None


def field_ref_resets_daily(ref: FieldRef) -> bool:
    """Return True if ``ref`` references a field that resets each session.

    Literals never reset. Builtin / indicator fields are looked up in the
    registry and their :attr:`FieldSpec.resets_daily` flag returned.
    Unknown fields return ``False`` (engine validation surfaces the error
    elsewhere; this helper stays defensive).
    """
    if ref.kind == "literal":
        return False
    spec = get_field(ref.id, kind=ref.kind)
    return bool(spec is not None and spec.resets_daily)


def condition_uses_daily_reset_field(node) -> bool:  # noqa: ANN001 — accepts Condition or Group
    """Return True if any FieldRef in ``node`` (Condition or Group) resets daily.

    Recursively walks Group → children. For each Condition, inspects its
    ``left`` plus every :class:`FieldRef`-typed entry in ``params``.
    Used by the within-last-N-bars walk to decide whether to clamp the
    look-back window to the current session-open index.
    """
    # Imported lazily to avoid a top-of-module circular import on
    # ``model.MatchEvidence`` etc.
    from .model import Condition, Group  # noqa: WPS433

    if isinstance(node, Group):
        return any(condition_uses_daily_reset_field(c) for c in node.children)
    if isinstance(node, Condition):
        if field_ref_resets_daily(node.left):
            return True
        for v in node.params.values():
            if isinstance(v, FieldRef) and field_ref_resets_daily(v):
                return True
        return False
    return False


__all__ = [
    "BarsNp",
    "FieldSpec",
    "DTYPE_NUMERIC",
    "DTYPE_BOOL",
    "SCANNABLE_INDICATORS",
    "INDICATORS_RESETTING_DAILY",
    "all_fields",
    "get_field",
    "is_scannable",
    "validate_field_ref",
    "builtin_compute",
    "field_ref_resets_daily",
    "condition_uses_daily_reset_field",
]


# Touch INDICATORS to silence linter unused-import — the import is
# load-bearing for the side-effect of populating the registry when the
# scanner module is imported standalone in tests.
_ = INDICATORS
