"""Single owner of the chart's X-window preservation intent.

Historically the chart preserved the user's visible X window (matplotlib
``xlim`` — which bars/dates are on screen) across data reloads via a
SCATTERED set of instance booleans on ``ChartApp``:

* ``_preserve_xlim_on_render``          — keep the exact bar-index window
* ``_preserve_xlim_by_time_on_render``  — remap the calendar window onto a
                                          new (possibly different-length) series
* ``_slide_xlim_to_right_edge``         — keep width, shift to the newest bar
* ``_axis_switch_inflight``             — an explicit async source/interval
                                          switch is loading
* ``_pending_axis_switch_time_preserve``— durable companion so the switch's
                                          completing render re-asserts time-preserve

These were set/cleared/consumed at ~40 sites across ~8 files with fragile,
implicit precedence and one-shot flags that leaked across async boundaries.
Every recent view-preservation bug (compare-toggle candle-creep, ticker-switch
misalignment, source-switch "jump to 2021", …) was a symptom of that design.

``ViewController`` centralises the whole decision into ONE place with a small
explicit vocabulary of INTENTS (:class:`ViewMode`) and three guarantees that
structurally prevent the recurring bug classes:

1. **One total-ordered precedence.** ``render_directives`` is the single point
   that resolves the intent into the ``(preserve, by_time, slide)`` triple the
   renderer consumes. When a time-remap is active it FORCES index-preserve off
   (``by_time`` wins over index), so the two can never silently conflict — the
   root of the "index-preserve reused a stale window against a longer series →
   jumped years back" bug.
2. **Durable intent across async loads.** While an explicit switch load is in
   flight (:meth:`load_pending`), intervening renders (poll tick, prefetch
   daily-synth refresh, reference-data redraw, deferred idle render) resolve to
   HOLD — they keep the current view and CONSUME NOTHING. The switch's own
   completing render (after :meth:`begin_completing_load`) is the only one that
   applies + consumes the intent. Symmetrically, an intervening
   :meth:`request` that does NOT start a new switch is IGNORED, so a mid-switch
   re-arm (``arm_keep_bars`` from a poll/compare/pan path) cannot overwrite the
   armed one-shot intent either. Together these guard BOTH the consumption and
   the intent-setting sides, replacing the per-bug
   ``_pending_axis_switch_time_preserve`` band-aid with one generic rule.
3. **One-shot vs sticky is explicit.** ``KEEP_DATES`` / ``SNAP_RIGHT`` are
   one-shot (applied once, then the view reverts to plain index-preserve);
   ``KEEP_BARS`` is sticky (a pan/zoom persists across later renders);
   ``DEFAULT`` means "right-edge default window".

The controller stores the three legacy booleans internally so ``ChartApp`` can
expose the historical flag NAMES as thin bridging properties (keeping the large
existing test surface working) while the DECISION logic lives here, fully
unit-testable without Tk/matplotlib.

Not thread-safe; every caller is on the Tk main thread.
"""

from __future__ import annotations

from enum import Enum


class ViewMode(Enum):
    """What should happen to the visible X window on the next render."""

    #: Right-edge default window (fresh load, reset-view, new ticker at the
    #: default view, interval change).
    DEFAULT = "default"
    #: Preserve the exact bar-index window (pan, wheel/rubber-band zoom,
    #: drilldown, any same-series redraw). STICKY across later renders.
    KEEP_BARS = "keep_bars"
    #: Remap the calendar (date) window onto the freshly-loaded series — for a
    #: source-only switch, or a historical ticker/compare switch where the new
    #: series can differ in length/shape. ONE-SHOT.
    KEEP_DATES = "keep_dates"
    #: Keep the current width but shift the window to the newest bar (a live
    #: poll tick while the user is glued to the right edge). ONE-SHOT.
    SNAP_RIGHT = "snap_right"


#: Modes that are applied exactly once and then fall back to plain
#: index-preserve (KEEP_BARS) so subsequent renders hold the resolved window.
_ONE_SHOT: frozenset[ViewMode] = frozenset({ViewMode.KEEP_DATES, ViewMode.SNAP_RIGHT})


def mode_to_flags(mode: ViewMode) -> tuple[bool, bool, bool]:
    """Map a :class:`ViewMode` to ``(preserve_index, preserve_by_time, slide)``.

    This is the ONLY translation from the intent vocabulary to the legacy
    render-directive triple that ``ChartApp._compute_slot_window`` consumes.
    """
    if mode is ViewMode.KEEP_BARS:
        return (True, False, False)
    if mode is ViewMode.KEEP_DATES:
        return (False, True, False)
    if mode is ViewMode.SNAP_RIGHT:
        return (True, False, True)
    return (False, False, False)  # DEFAULT


def is_one_shot(mode: ViewMode) -> bool:
    """True when ``mode`` is applied once then reverts to index-preserve."""
    return mode in _ONE_SHOT


class ViewController:
    """Owns the chart's X-window preservation intent (see module docstring)."""

    __slots__ = ("_preserve", "_by_time", "_slide", "_load_pending")

    def __init__(self) -> None:
        # Canonical state = the three legacy render directives + the async
        # switch-in-flight guard. ChartApp exposes each via a bridging property.
        self._preserve = False   # index-preserve (STICKY)
        self._by_time = False    # time-remap (ONE-SHOT)
        self._slide = False      # snap-to-right (ONE-SHOT)
        self._load_pending = False  # explicit async source/interval switch in flight

    # ------------------------------------------------------------------ intent
    def request(self, mode: ViewMode, *, load_pending: bool = False) -> None:
        """Declare the desired view behaviour for the next (owning) render.

        ``load_pending=True`` marks that an explicit async switch load is now
        in flight — intervening renders will HOLD until the switch completes
        (see :meth:`render_directives`). Callers must NOT pass
        ``load_pending=True`` for synchronous reloads.

        **Durability across an in-flight switch.** While a switch load is
        already pending, a request that does NOT itself start a new switch
        (``load_pending=False``) is IGNORED — it must not overwrite the
        durable one-shot ``KEEP_DATES`` (or ``SNAP_RIGHT``) intent the switch
        armed. Without this guard an intervening re-arm through this method
        (a poll-tick ``SNAP_RIGHT``/``KEEP_BARS``, a compare re-apply's
        ``arm_keep_bars()``, a mid-fetch pan/zoom end) would silently reset
        ``by_time`` to ``False`` while ``load_pending`` stayed ``True``, so the
        switch's completing render fell back to a stale bar-INDEX window
        against the new provider's (often longer) series — the "switch source
        → jump years back" bug. ``render_directives``'s HOLD only guards the
        CONSUMPTION side; this guards the INTENT-SETTING side. A genuinely new
        explicit switch (``load_pending=True``) still supersedes.
        """
        if self._load_pending and not load_pending:
            return
        self._preserve, self._by_time, self._slide = mode_to_flags(mode)
        if load_pending:
            self._load_pending = True

    def arm_keep_bars(self) -> None:
        """Sugar: preserve the exact bar window (pan / zoom / drilldown)."""
        self.request(ViewMode.KEEP_BARS)

    # ------------------------------------------------------- async durability
    @property
    def load_pending(self) -> bool:
        """True while an explicit async source/interval switch is loading.

        Live poll ticks bail while this is set so they can't re-arm
        index-preserve or launch a competing fetch mid-switch.
        """
        return self._load_pending

    @load_pending.setter
    def load_pending(self, value: bool) -> None:
        self._load_pending = bool(value)

    def begin_completing_load(self) -> bool:
        """Called at the TOP of the load that services the current render.

        Lowers :attr:`load_pending` and returns whether this load is completing
        an explicit async switch. When True the caller should render
        SYNCHRONOUSLY so no intervening event can consume the (now-owned)
        one-shot intent before the switch's render applies it.
        """
        was = self._load_pending
        self._load_pending = False
        return was

    # ------------------------------------------------------- render consumption
    def render_directives(self) -> tuple[bool, bool, bool]:
        """Resolve the intent into ``(preserve, by_time, slide)`` for a render.

        * **During a pending switch load** — return HOLD: keep the current
          index view and CONSUME NOTHING, so an intervening render (poll tick,
          daily-synth refresh, reference redraw, deferred idle render) cannot
          eat the one-shot ``by_time`` / ``slide`` intent nor let a racing
          index-preserve re-arm win. The switch's own completing render (after
          :meth:`begin_completing_load` clears ``load_pending``) applies it.
        * **Otherwise** — consume the one-shots (``by_time`` / ``slide`` reset
          to False) and enforce ``by_time`` PRECEDENCE over index-preserve: a
          time-remap render forces ``preserve`` off (and clears the sticky
          index flag) so a stale bar-index window can never clobber the
          calendar remap.
        """
        if self._load_pending:
            return (self._preserve, False, False)
        preserve = self._preserve
        by_time = self._by_time
        slide = self._slide
        # One-shot consumption.
        self._by_time = False
        self._slide = False
        # by_time (time-remap) always wins over index-preserve.
        if by_time:
            preserve = False
            self._preserve = False
        return (preserve, by_time, slide)

    # ------------------------------------------------ legacy-flag bridge (r/w)
    # ChartApp exposes the historical flag NAMES as thin properties over these,
    # keeping the large existing test surface working while the DECISION logic
    # lives in ``render_directives`` above.
    @property
    def preserve(self) -> bool:
        return self._preserve

    @preserve.setter
    def preserve(self, value: bool) -> None:
        self._preserve = bool(value)

    @property
    def by_time(self) -> bool:
        return self._by_time

    @by_time.setter
    def by_time(self, value: bool) -> None:
        self._by_time = bool(value)

    @property
    def slide(self) -> bool:
        return self._slide

    @slide.setter
    def slide(self, value: bool) -> None:
        self._slide = bool(value)

    # ------------------------------------------------------------- test/save API
    def snapshot(self) -> tuple[bool, bool, bool, bool]:
        """Opaque state snapshot for save/restore in tests."""
        return (self._preserve, self._by_time, self._slide, self._load_pending)

    def restore(self, snap: tuple[bool, bool, bool, bool]) -> None:
        """Restore a :meth:`snapshot`."""
        self._preserve, self._by_time, self._slide, self._load_pending = snap
