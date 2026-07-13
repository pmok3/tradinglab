"""Dual-interval policy for the prefetch scheduler.

Given the on-screen ("active") interval, return the ordered set of intervals to
warm for a symbol so the two one-click escape hatches are instant:

* the active interval (what's on screen) FIRST,
* the escape hatch next — a daily (``1d``) chart's escape is the drill-down
  target ``5m``; any intraday chart's escape is the Reset-View target ``1d``,
* then any remaining of ``{5m, 1d}``.

``5m`` and ``1d`` are therefore ALWAYS present (they back Reset-View and
drill-down). Pure (no Tk / IO); unit-tested in
``tests/unit/data/prefetch/test_intervals.py``. See
``PREFETCH_SCHEDULER_DESIGN.md`` §4 and design Decision 15.
"""
from __future__ import annotations

#: The two intervals backing the one-click escape hatches (Reset-View -> 1d,
#: drill-down -> 5m). Always included in the returned order.
_ESCAPE_INTERVALS = ("5m", "1d")


def dual_interval(active_interval: str | None) -> list[str]:
    """Return the ordered interval set to warm for a symbol.

    ``active_interval`` is the on-screen interval. A blank / unknown value
    degrades to the daily default ``["1d", "5m"]`` rather than raising. The
    returned list is freshly constructed on every call (callers may mutate it).
    """
    active = (active_interval or "").strip().lower()
    if not active:
        return ["1d", "5m"]
    order = [active]
    # Daily on-screen -> the drill-down target (5m) is the escape hatch; any
    # intraday on-screen -> the Reset-View target (1d) first, then 5m.
    secondary = ["5m"] if active == "1d" else ["1d", "5m"]
    for iv in secondary:
        if iv not in order:
            order.append(iv)
    return order


__all__ = ["dual_interval"]
