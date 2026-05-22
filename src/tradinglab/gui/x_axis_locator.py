"""TradingView-style adaptive x-axis locator + formatter.

Extracted from :mod:`tradinglab.app` (where it was previously
inlined as a 297-LOC block of pure module-level helpers). Owns the
``_X_PERIODS`` ladder, the lazily-built ``_AdaptiveXLocator`` matplotlib
locator class, and the ``_make_x_formatter`` factory that emits a
``FuncFormatter`` paired with the locator's ``_last_period`` back-ref.

Only two symbols are exported for use by ``ChartApp``:

* :func:`_adaptive_x_locator_class` — returns the cached locator class.
* :func:`_make_x_formatter` — returns a ``FuncFormatter`` bound to a
  particular ``slot_key`` of ``app._panel_state``.

All other names (``_X_PERIODS``, ``_x_bucket``, ``_x_pick_period``, …)
are private helpers used only inside this module.
"""

from __future__ import annotations

import numpy as np

from ..constants import is_intraday
from ..formatting import format_dt

# --- TradingView-style x-axis locator + formatter (H4: hoisted out of
# ChartApp._render so the class isn't redefined every render. Holds a
# back-ref to the app for live access to ``_panel_state`` /
# ``_display_tz``.) ----------------------------------------------------

# (unit, count, approx_seconds) ladder of human-friendly intervals. The
# locator picks the smallest period whose visible-span / period ≤ TARGET
# tick count, so ~12 ticks span the visible window.
_X_PERIODS: tuple = (
    ("minute", 1,       60),
    ("minute", 2,      120),
    ("minute", 5,      300),
    ("minute", 15,     900),
    ("minute", 30,    1800),
    ("hour",   1,     3600),
    ("hour",   2,     7200),
    ("hour",   3,    10800),
    ("hour",   4,    14400),
    ("hour",   6,    21600),
    ("hour",  12,    43200),
    ("day",    1,    86400),
    ("day",    2,   172800),
    ("week",   1,   604800),
    ("week",   2,  1209600),
    ("month",  1,  2629800),
    ("month",  3,  7889400),
    ("month",  6, 15778800),
    ("year",   1, 31557600),
    ("year",   2, 63115200),
    ("year",   5,157788000),
)


def _x_bucket(ts, unit: str, count: int):
    if unit == "minute":
        m = ts.hour * 60 + ts.minute
        return (ts.year, ts.month, ts.day, m // count)
    if unit == "hour":
        return (ts.year, ts.month, ts.day, ts.hour // count)
    if unit == "day":
        return ts.toordinal() // count
    if unit == "week":
        iso_year, iso_week, _ = ts.isocalendar()
        return (iso_year, iso_week // count)
    if unit == "month":
        return (ts.year * 12 + (ts.month - 1)) // count
    if unit == "year":
        return ts.year // count
    return 0


def _x_pick_period(span_seconds: float, target: int) -> tuple:
    """Smallest nice period where ``span / period ≤ target``."""
    for unit, count, secs in _X_PERIODS:
        if span_seconds / secs <= target:
            return unit, count, secs
    return _X_PERIODS[-1]


def _x_finer_period(period: tuple) -> tuple:
    """Next finer period (or unchanged at finest end). Used as a fallback
    when the chosen period yields too few visible ticks."""
    for idx, p in enumerate(_X_PERIODS):
        if p[0] == period[0] and p[1] == period[1]:
            return _X_PERIODS[idx - 1] if idx > 0 else p
    return period


def _x_coarser_period(period: tuple) -> tuple:
    """Next coarser period (or unchanged at coarsest end). Used to step
    down density when the picked period yields too many ticks."""
    for idx, p in enumerate(_X_PERIODS):
        if p[0] == period[0] and p[1] == period[1]:
            if idx < len(_X_PERIODS) - 1:
                return _X_PERIODS[idx + 1]
            return p
    return period


def _x_context_unit(period_seconds: float) -> str:
    """Larger calendar unit whose crossings become label upgrades."""
    day = 86400.0
    if period_seconds < day:
        return "day"
    if period_seconds < 28 * day:
        return "month"
    return "year"


def _x_context_crosses(prev_ts, cur_ts, ctx: str) -> bool:
    if ctx == "day":
        return prev_ts.date() != cur_ts.date()
    if ctx == "month":
        return (prev_ts.year, prev_ts.month) != (cur_ts.year, cur_ts.month)
    return prev_ts.year != cur_ts.year


def _make_adaptive_x_locator_class():
    """Lazily build the ``_AdaptiveXLocator`` class once (the matplotlib
    ``FixedLocator`` import is module-level-cheap but we keep the class
    construction lazy to avoid importing matplotlib at module-import
    time for non-GUI consumers)."""
    from matplotlib.ticker import FixedLocator

    def _safe_delta_seconds(later, earlier) -> float:
        """Compute ``(later - earlier).total_seconds()`` tolerating
        tz-aware/tz-naive mixes that arise when one source (e.g. yfinance
        disk pickle) carries tzinfo while another (in-memory fakes,
        streaming, pairing-normalized inserts) does not. Both timestamps
        represent the same exchange wall clock, so stripping tzinfo when
        only one side has it is safe and matches the policy already used
        in ``core.pairing._normalize_pairing_key``.

        Returns ``0.0`` if either value is missing or arithmetic still
        fails — callers treat 0 as "skip this diff".
        """
        if later is None or earlier is None:
            return 0.0
        try:
            return (later - earlier).total_seconds()
        except TypeError:
            l_tz = getattr(later, "tzinfo", None)
            e_tz = getattr(earlier, "tzinfo", None)
            try:
                if l_tz is not None and e_tz is None:
                    later = later.replace(tzinfo=None)
                elif e_tz is not None and l_tz is None:
                    earlier = earlier.replace(tzinfo=None)
                return (later - earlier).total_seconds()
            except Exception:  # noqa: BLE001
                return 0.0

    class _AdaptiveXLocator(FixedLocator):
        """TradingView-style x-axis locator. Caches boundary lists per
        (id(candles), period) so pan frames are cheap. Holds a back-ref
        to the host ``ChartApp`` for live access to ``_panel_state``."""
        _TARGET = 12

        def __init__(self, slot_key: str, app, interval_name: str):
            super().__init__([0])
            self._slot = slot_key
            self._app = app
            self._interval = interval_name
            self._cache: dict = {}
            self._bar_secs_cache: dict = {}
            self._last_period: tuple = ("day", 1, 86400)

        def _bar_seconds(self, cs) -> float:
            c = self._bar_secs_cache.get(id(cs))
            if c is not None:
                return c
            if len(cs) < 2:
                self._bar_secs_cache[id(cs)] = 300.0
                return 300.0
            diffs = []
            for i in range(1, min(200, len(cs))):
                d = _safe_delta_seconds(cs[i].date, cs[i - 1].date)
                if 0 < d < 86400:
                    diffs.append(d)
            if not diffs:
                s = 300.0
            else:
                diffs.sort()
                s = diffs[len(diffs) // 2]
            self._bar_secs_cache[id(cs)] = s
            return s

        def _all_boundaries(self, cs, unit: str, count: int) -> list:
            key = (id(cs), "bucket", unit, count)
            b = self._cache.get(key)
            if b is None:
                out: list = []
                if len(cs) >= 2:
                    prev = _x_bucket(cs[0].date, unit, count)
                    for i in range(1, len(cs)):
                        cur = _x_bucket(cs[i].date, unit, count)
                        if cur != prev:
                            out.append(i)
                            prev = cur
                b = out
                self._cache[key] = b
            return b

        def _current(self):
            state = self._app._panel_state.get(self._slot, {})
            cs = state.get("candles") or []
            if not cs:
                return []
            vmin, vmax = self.axis.get_view_interval()
            n = len(cs)
            lo = max(0, int(np.floor(vmin)))
            hi = min(n - 1, int(np.ceil(vmax)))
            if hi <= lo:
                return []
            is_intra = is_intraday(self._interval)
            if is_intra:
                bar_secs = self._bar_seconds(cs)
                effective_span = (hi - lo) * bar_secs
            else:
                effective_span = _safe_delta_seconds(cs[hi].date, cs[lo].date)

            unit, count, secs = _x_pick_period(effective_span, self._TARGET)
            self._last_period = (unit, count, secs)
            all_b = self._all_boundaries(cs, unit, count)
            vis = [b for b in all_b if lo <= b <= hi]

            for _ in range(2):
                if len(vis) >= max(4, self._TARGET // 2):
                    break
                finer = _x_finer_period((unit, count, secs))
                if finer == (unit, count, secs):
                    break
                unit, count, secs = finer
                self._last_period = (unit, count, secs)
                all_b = self._all_boundaries(cs, unit, count)
                vis = [b for b in all_b if lo <= b <= hi]

            for _ in range(4):
                if len(vis) <= self._TARGET:
                    break
                coarser = _x_coarser_period((unit, count, secs))
                if coarser == (unit, count, secs):
                    break
                unit, count, secs = coarser
                self._last_period = (unit, count, secs)
                all_b = self._all_boundaries(cs, unit, count)
                vis = [b for b in all_b if lo <= b <= hi]

            if not vis:
                sub = max(1, n // (self._TARGET * 4))
                vis = [i for i in range(0, n, sub) if lo <= i <= hi]
            return vis

        def __call__(self):
            self.locs = self._current()
            return list(self.locs)

        def tick_values(self, vmin, vmax):
            return self._current()

    return _AdaptiveXLocator


# Cached class — built on first use, reused for every render.
_ADAPTIVE_X_LOCATOR_CLS: type | None = None


def _adaptive_x_locator_class():
    global _ADAPTIVE_X_LOCATOR_CLS
    if _ADAPTIVE_X_LOCATOR_CLS is None:
        _ADAPTIVE_X_LOCATOR_CLS = _make_adaptive_x_locator_class()
    return _ADAPTIVE_X_LOCATOR_CLS


def _make_x_formatter(app, slot_key: str):
    """Return a matplotlib ``FuncFormatter`` that labels each tick in
    TradingView style. Reads the locator's ``_last_period`` back-ref to
    pick a fine label (``HH:MM`` / ``%d`` / ``%b`` / ``%Y``) and upgrades
    to a context label on calendar-unit crossings.
    """
    from matplotlib.ticker import FuncFormatter

    def _fmt(v, _pos, _slot=slot_key):
        cs = app._panel_state.get(_slot, {}).get("candles") or []
        if not cs:
            return ""
        n = len(cs)
        i = int(round(v))
        if i < 0 or i >= n:
            return ""
        ts = cs[i].date
        ax = app._panel_state.get(_slot, {}).get("price_ax")
        period = ("day", 1, 86400.0)
        if ax is not None:
            loc = ax.xaxis.get_major_locator()
            period = getattr(loc, "_last_period", None) or period
        unit, count, secs = period
        ctx = _x_context_unit(secs)

        if i > 0 and _x_context_crosses(cs[i - 1].date, ts, ctx):
            if ctx == "day":
                return ts.strftime("%b %d")
            if ctx == "month":
                return ts.strftime("%b")
            return ts.strftime("%Y")

        if unit == "minute" or unit == "hour":
            return format_dt(ts, "%H:%M", app._display_tz)
        if unit == "day":
            return ts.strftime("%d")
        if unit == "week":
            return ts.strftime("%d")
        if unit == "month":
            return ts.strftime("%b")
        return ts.strftime("%Y")

    return FuncFormatter(_fmt)


__all__ = ("_make_x_formatter", "_adaptive_x_locator_class")
