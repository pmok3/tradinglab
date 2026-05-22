"""``SandboxController`` — Phase 1b/1c UI controller for the sandbox kernel.

Bridges :class:`SandboxEngine` to the chart UI. Owns session lifecycle:

* ``start_session`` — captures a memento of pre-sandbox app state, builds
  the engine, ticks once so the first bar is visible, and installs a
  truncated per-symbol candle list on the primary chart slot. Optional
  ``clip_to_session_day`` plus ``lookback_days`` integrate the Phase 1c
  eligible-days deck — only the chosen day's data (plus context bars)
  reaches the engine.
* ``next_bar`` — advances the engine one tick, appends to the per-symbol
  visible-candle lists *in place* (so the indicator cache + series cache
  keep their identity-based hits), and redraws the focused slot. After
  every tick, any newly-emitted ``PostTradeReview`` runs through the
  registered post-trade callback (which the UI uses to drive the
  mandatory review modal) and a follow-up screenshot.
* ``set_focus`` — swaps the primary slot to a different ticker's visible
  list at the current clock.
* ``submit_order`` — validates, mints a deterministic ``ord-NNNN`` id,
  uses the engine clock's timestamp, queues with the engine, and
  captures a "pre-trade" screenshot so the chart context at the
  moment-of-submission is preserved for journaling.
* ``end_session`` — restores from the memento (so the user is back to
  whatever they were doing before) and returns the engine's
  :class:`SessionResult`.

The controller never reads or writes app state directly except through a
small set of named primitives on :class:`ChartApp`:
``_install_sandbox_primary_series``, ``_invalidate_focused_panels``,
``_draw_slice``, ``_render``, ``_capture_chart_png``, plus the standard
Tk vars (``ticker_var`` / ``compare_var`` / ``interval_var``). This is
the explicit boundary — see the readability audit, finding #2.

Locked-decision references (see plan.md): Q3 multi-ticker, Q9 watchlist
sidebar drives focus, Q10 manual turn-based, Q11 user-supplied size.
Phase 1c adds: deck-driven session bounds (Q12), mandatory post-trade
review (Q13), per-trade screenshot capture (Q14), setup-tag taxonomy.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as _dt
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as _np

from .aggregation import aggregate as _aggregate
from .aggregation import divides_evenly, interval_minutes
from .bars import from_candles
from .deck import filter_candles_to_session
from .engine import SandboxEngine
from .journal import PostTradeReview, PreTradeEntry
from .orders import Order, Side
from .replay_events import EventsControllerMixin
from .session import SessionResult, SessionSpec
from .tags import TagStore


@contextlib.contextmanager
def _silent_tcl(*extra_excs: type[BaseException]):
    """Swallow ``tk.TclError`` (plus any ``extra_excs``) — narrow guard
    for app callbacks during replay because the ChartApp's Tk widgets
    may already be torn down at app-close time. Replaces the
    boilerplate ``try: ...; except tk.TclError: pass`` blocks that
    otherwise dot every controller method that touches the GUI.

    Pass extra exception types for sites that historically also
    swallowed e.g. ``AttributeError`` from a missing app attribute.
    """
    excs = (tk.TclError,) + extra_excs
    try:
        yield
    except excs:
        pass


def _candles_match(a: List[Any], b: List[Any]) -> bool:
    """Cheap fingerprint compare for register_ticker idempotency.

    Length + first/last bar timestamps + last close. Catches the
    "same data fetched again" case without paying for a full
    element-by-element compare.
    """
    if len(a) != len(b):
        return False
    if not a:
        return True
    if a[0].date != b[0].date or a[-1].date != b[-1].date:
        return False
    return float(a[-1].close) == float(b[-1].close)


@dataclass
class SandboxMemento:
    """Snapshot of pre-sandbox app state for end-of-session restoration.

    Stored at ``start_session`` time, applied at ``end_session`` time.
    Per the rubber-duck critique: ad-hoc restore drops state on the
    floor; a memento makes the contract explicit and testable.

    Attribute reads at capture time are deliberately direct (no
    blanket exception suppression): if the app's Tk vars are
    misconfigured the memento contract is broken and a hard failure
    is preferable to silently restoring partial state. The single
    legitimate failure boundary — ``_render`` during teardown — keeps
    its narrowly-scoped guard.
    """
    primary: List[Any]
    compare: List[Any]
    candles: List[Any]
    ticker: str
    compare_ticker: str
    compare_on: bool
    interval: str
    # Phase 1c-redux: pre-session drilldown state. The sandbox clears
    # ``_drilldown_day`` at start so its watchlist + reload paths don't
    # try to re-zoom to a daily-chart drilldown that doesn't exist on
    # the engine timeline. We restore it on end_session so the user
    # picks up exactly where they were before opening the sandbox.
    drilldown_day: Any = None
    # Watermark / tab-label backing fields. These are mutated by
    # ``_install_sandbox_*_series`` so the watermark tracks the
    # sandbox-driven symbol when the user Space-cycles or swaps focus.
    confirmed_primary_ticker: str = ""
    confirmed_compare_ticker: str = ""

    @classmethod
    def capture(cls, app: Any) -> "SandboxMemento":
        return cls(
            primary=list(getattr(app, "_primary", []) or []),
            compare=list(getattr(app, "_compare", []) or []),
            candles=list(getattr(app, "candles", []) or []),
            ticker=app.ticker_var.get(),
            compare_ticker=app.compare_ticker_var.get(),
            compare_on=bool(app.compare_var.get()),
            interval=app.interval_var.get(),
            drilldown_day=getattr(app, "_drilldown_day", None),
            confirmed_primary_ticker=getattr(app, "_confirmed_primary_ticker", ""),
            confirmed_compare_ticker=getattr(app, "_confirmed_compare_ticker", ""),
        )

    def restore(self, app: Any) -> None:
        app._primary = self.primary
        app._compare = self.compare
        app.candles = self.primary  # candles is an alias of _primary
        app.ticker_var.set(self.ticker)
        app.compare_ticker_var.set(self.compare_ticker)
        app.compare_var.set(self.compare_on)
        app.interval_var.set(self.interval)
        if hasattr(app, "_drilldown_day"):
            app._drilldown_day = self.drilldown_day
        if hasattr(app, "_confirmed_primary_ticker"):
            app._confirmed_primary_ticker = self.confirmed_primary_ticker
        if hasattr(app, "_confirmed_compare_ticker"):
            app._confirmed_compare_ticker = self.confirmed_compare_ticker
        # _render touches Tk widgets that may already be torn down at
        # app-close time — narrow guard, not a blanket sweep.
        with _silent_tcl():
            app._render()


@dataclass
class SandboxController(EventsControllerMixin):
    """Active-session orchestrator. One instance per :class:`ChartApp`."""

    app: Any
    engine: Optional[SandboxEngine] = None
    spec: Optional[SessionSpec] = None
    interval: str = "5m"
    focus_symbol: Optional[str] = None
    full_candles_by_symbol: Dict[str, List[Any]] = field(default_factory=dict)
    visible_candles_by_symbol: Dict[str, List[Any]] = field(default_factory=dict)
    bars_by_symbol: Dict[str, Any] = field(default_factory=dict)
    active: bool = False
    _memento: Optional[SandboxMemento] = None
    _next_order_seq: int = 0

    # Phase 1c-redux: open-universe session metadata. ``session_date``
    # and ``lookback_days`` are captured at start and used by
    # :meth:`register_ticker` to trim newly-loaded symbols to the same
    # window the reference ticker was trimmed to. ``reference_symbol``
    # is the master-clock anchor and cannot be re-loaded mid-session
    # (the timeline is frozen at ``start_session`` time).
    session_date: Optional[_dt.date] = None
    lookback_days: int = 1
    reference_symbol: Optional[str] = None

    # Phase 1c: tag store + post-trade callback + screenshot dir.
    tag_store: TagStore = field(default_factory=TagStore)
    _post_trade_callback: Optional[Callable[[PostTradeReview], Optional[str]]] = None
    session_id: Optional[str] = None
    screenshot_dir: Optional[Path] = None
    _post_trade_count_seen: int = 0

    # Phase 1d: extended-hours filter + blind / auto-cycle. When
    # ``include_extended`` is False, master timeline + per-symbol bar
    # series omit pre / post-market candles. When ``auto_cycle`` is
    # True, ``next_bar()`` past end-of-day rolls into the next
    # eligible date (drawn from ``_eligible_dates`` shuffled by
    # ``deck_seed``) and replay continues until the user explicitly
    # ends the session. Closed and prior cycles' fills / pre-trades /
    # post-trades / equity-curve points are accumulated in the
    # ``_archived_*`` lists; :meth:`result` merges them with the
    # current engine's state.
    include_extended: bool = False
    auto_cycle: bool = False
    blind: bool = False
    _eligible_dates: List[_dt.date] = field(default_factory=list)
    _cycle_index: int = 0
    _raw_full_candles: Dict[str, List[Any]] = field(default_factory=dict)
    _archived_fills: List[Any] = field(default_factory=list)
    _archived_pre_trades: List[PreTradeEntry] = field(default_factory=list)
    _archived_post_trades: List[PostTradeReview] = field(default_factory=list)
    _archived_equity: List[Any] = field(default_factory=list)
    _archived_cash_adjustments: List[Any] = field(default_factory=list)
    _archived_quantity_adjustments: List[Any] = field(default_factory=list)

    # Phase 1d-multitf: daily-context series for higher-timeframe
    # display while the master clock ticks intraday. ``daily_full_by_symbol``
    # holds **raw** daily candles per symbol (not pre-trimmed by
    # session_date — visibility is derived dynamically from the
    # current master-clock session date so auto-cycle / inter-day
    # ticks expose the just-finished session bar without re-fetching).
    # ``daily_lookback_bars`` caps the number of prior daily bars
    # exposed at any time. ``display_interval`` is the *currently
    # rendered* interval (None = sandbox.interval, intraday); set by
    # the chart layer when the user toggles to ``"1d"``.
    daily_lookback_bars: int = 100
    display_interval: Optional[str] = None
    daily_full_by_symbol: Dict[str, List[Any]] = field(default_factory=dict)
    # Phase 1d-multitf-2: user-selected intraday display intervals
    # (smallest = primary tick interval, others are aggregated from
    # primary on the fly). Always contains ``self.interval`` as the
    # smallest entry; additional entries must each be an integer
    # multiple of ``self.interval`` (validated at start_session). The
    # toolbar interval combobox is restricted to this list (+ "1d"
    # if daily context is registered) while the session is active.
    display_intervals: Tuple[str, ...] = ()
    # Cached "session date of clock.now_ts" at the start of the most
    # recent next_bar; updated each tick. Used by the chart layer to
    # detect when the daily-context series should be re-installed
    # because the master clock crossed midnight.
    _last_clock_session_date: Optional[_dt.date] = None

    # Events feature: per-symbol raw event bundles fetched at session
    # start / register_ticker time. Stored as opaque ``Any`` because
    # the events subpackage is imported lazily — the controller never
    # touches event internals directly; it only forwards to
    # :func:`tradinglab.events.gating.events_visible_for`. Empty
    # by default; populated by the app's events-prefetch path which
    # writes via :meth:`set_event_bundle`.
    _raw_full_events: Dict[str, Any] = field(default_factory=dict)
    _events_fetch_token: int = 0

    # M5 (ChartStack lockstep): per-tick subscribers fired
    # synchronously inside ``next_bar`` and ``cycle_to_next`` after
    # the engine has advanced. The ChartStack panel registers one
    # such callback so each card advances exactly one bar per
    # ``next_bar`` call (the §5.3 "synchronous fan-out on Tk
    # thread" guarantee). Subscribers receive no arguments — they
    # read the controller's :attr:`visible_candles_by_symbol` map
    # directly. Returns a ``release()`` callable for clean
    # unregistration; cleared on ``end_session``.
    _card_subscribers: List[Callable[[], None]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def is_active(self) -> bool:
        return bool(self.active)

    def register_card_subscriber(
        self,
        callback: Callable[[], None],
    ) -> Callable[[], None]:
        """Register a per-tick card subscriber. Returns an unregister fn.

        M5 ChartStack lockstep: every registered callback is fired
        synchronously inside :meth:`next_bar` (and
        :meth:`cycle_to_next`) after the engine has advanced and
        per-symbol visible lists have been extended. Subscribers
        are invoked on the Tk thread; they may safely mutate
        Tk-side state (panel cache, redraw flags) but should
        return promptly — the next bar's redraw is gated on all
        subscribers completing.

        The returned ``release()`` callable removes the
        subscription idempotently. Exceptions raised by a
        subscriber are caught (logged via the standard exception
        path elsewhere) so one bad subscriber cannot block the
        rest of the tick.
        """
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._card_subscribers.append(callback)

        def _release(_cb=callback, _self=self) -> None:
            try:
                _self._card_subscribers.remove(_cb)
            except ValueError:
                pass

        return _release

    def _fire_card_subscribers(self) -> None:
        """Fire each registered card subscriber. Exception-safe.

        Iterates a point-in-time snapshot so a subscriber that
        unregisters itself mid-fire (e.g. on sandbox detach)
        doesn't disturb the loop.
        """
        for cb in list(self._card_subscribers):
            try:
                cb()
            except Exception:  # noqa: BLE001 - one bad subscriber must not block the rest
                pass

    def set_post_trade_callback(
        self,
        cb: Optional[Callable[[PostTradeReview], Optional[str]]],
    ) -> None:
        """Register (or clear) the callback that gathers the user's
        post-trade review text.

        The callback is invoked synchronously from ``next_bar`` for each
        newly-closed trade. It receives the engine-emitted
        :class:`PostTradeReview` and must return the user's review
        text (a non-empty string). Returning ``None`` or ``""`` leaves
        the review empty — the contract is intentionally permissive
        for headless callers (smoke tests, batch runners) that have
        no UI to drive.
        """
        self._post_trade_callback = cb

    def start_session(
        self,
        *,
        spec: SessionSpec,
        session_date: _dt.date,
        interval: str,
        reference_symbol: str,
        reference_candles: List[Any],
        lookback_days: int = 1,
        screenshot_dir: Optional[Path] = None,
        include_extended: bool = False,
        auto_cycle: bool = False,
        blind: bool = False,
        eligible_dates: Optional[List[_dt.date]] = None,
        daily_lookback_bars: int = 100,
        daily_reference_candles: Optional[List[Any]] = None,
        display_intervals: Optional[Sequence[str]] = None,
    ) -> None:
        """Begin a sandbox session anchored on a single reference ticker.

        Phase 1c-redux open-universe model. The master timeline is
        derived from ``reference_candles`` (typically SPY) trimmed to
        ``[session_date - lookback_days, end-of-data]``. Additional
        symbols join via :meth:`register_ticker` mid-session — they
        do **not** extend the timeline.

        Phase 1d additions:

        * ``include_extended`` — when False (default), filter
          pre/post-market bars out of the master timeline and every
          per-symbol BarSeries. The user only sees regular-session
          replay.
        * ``auto_cycle`` — when True, the master timeline is also
          *day-bounded* (just the chosen ``session_date``, plus
          ``lookback_days`` of context behind), and on
          end-of-timeline the controller rolls to the next eligible
          date drawn from ``eligible_dates`` (seeded shuffle).
        * ``blind`` — informational flag the panel reads to suppress
          the date portion of the clock readout. Does not alter
          replay behaviour.

        Failure modes:

        * No bars survive the trim → :class:`ValueError`.
        * Already-active session → :class:`RuntimeError`.
        * ``auto_cycle=True`` with no eligible_dates → :class:`ValueError`.
        """
        if self.active:
            raise RuntimeError("sandbox session already active")
        if not reference_candles:
            raise ValueError("reference_candles is empty")
        if auto_cycle and not eligible_dates:
            raise ValueError(
                "auto_cycle requires a non-empty eligible_dates list")

        # Phase 1d-multitf-2: validate + canonicalise display_intervals.
        # Default = (interval,) for back-compat. The smallest entry MUST
        # equal ``interval`` (the primary tick interval) and every other
        # entry must be an integer multiple of primary so aggregation
        # is well-defined.
        if display_intervals is None:
            canonical_intervals: Tuple[str, ...] = (interval,)
        else:
            seen: List[str] = []
            for itv in display_intervals:
                if itv not in seen:
                    seen.append(itv)
            if interval not in seen:
                seen.append(interval)
            seen.sort(key=interval_minutes)
            if seen[0] != interval:
                raise ValueError(
                    f"display_intervals smallest entry must equal primary "
                    f"interval {interval!r}; got smallest={seen[0]!r}")
            for itv in seen[1:]:
                if not divides_evenly(interval, itv):
                    raise ValueError(
                        f"display_intervals: {itv!r} is not an integer "
                        f"multiple of primary {interval!r}")
            canonical_intervals = tuple(seen)

        # Reset multi-cycle archives on every fresh start_session.
        self._archived_fills = []
        self._archived_pre_trades = []
        self._archived_post_trades = []
        self._archived_equity = []
        self._archived_cash_adjustments = []
        self._archived_quantity_adjustments = []
        self._cycle_index = 0
        self._raw_full_candles = {reference_symbol: list(reference_candles)}
        # Reset daily-context state. Display interval defaults to the
        # sandbox's intraday interval; chart layer toggles it to "1d".
        self.daily_lookback_bars = max(0, int(daily_lookback_bars))
        self.daily_full_by_symbol = {}
        self.display_interval = None
        if daily_reference_candles:
            # Store raw daily; visibility is derived dynamically.
            self.daily_full_by_symbol[reference_symbol] = list(
                daily_reference_candles)

        trimmed_ref = filter_candles_to_session(
            reference_candles, session_date,
            lookback_days=lookback_days,
            bounded=auto_cycle,
            regular_only=not include_extended,
        )
        if not trimmed_ref:
            raise ValueError(
                f"reference {reference_symbol!r} has no bars in "
                f"[{session_date} - {lookback_days}d, "
                f"{'session_date+1d' if auto_cycle else 'end'}]"
                f"{' regular-only' if not include_extended else ''}"
            )

        ref_bars = from_candles(reference_symbol, interval, trimmed_ref)

        self.full_candles_by_symbol = {reference_symbol: list(trimmed_ref)}
        self.bars_by_symbol = {reference_symbol: ref_bars}
        # Stable visible list grown in place each tick.
        self.visible_candles_by_symbol = {reference_symbol: []}

        # Engine: master timeline frozen at construction = reference.ts.
        self.engine = SandboxEngine(
            spec=spec,
            bars_by_symbol={reference_symbol: ref_bars},
            master_timeline=ref_bars.ts.copy(),
        )
        self.spec = spec
        self.interval = interval
        self.session_date = session_date
        self.lookback_days = int(lookback_days)
        self.reference_symbol = reference_symbol
        self.include_extended = bool(include_extended)
        self.auto_cycle = bool(auto_cycle)
        self.blind = bool(blind)
        self._eligible_dates = list(eligible_dates or [])
        self.display_intervals = canonical_intervals
        self.session_id = (
            f"sandbox-"
            f"{_dt.datetime.now(_dt.timezone.utc).strftime('%Y%m%dT%H%M%S')}-"
            f"{int(spec.deck_seed)}"
        )
        self.screenshot_dir = screenshot_dir
        if screenshot_dir is not None:
            screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._post_trade_count_seen = 0

        self._memento = SandboxMemento.capture(self.app)

        # Clear drilldown so the watchlist / reload paths during
        # sandbox don't try to re-zoom to a stale daily drilldown.
        if hasattr(self.app, "_drilldown_day"):
            self.app._drilldown_day = None

        # Cancel/drain background fetch jobs. Bumps _fetch_token so
        # any callback in flight from a pre-sandbox _load_data_async
        # finds itself stale and bails out. Clearing _prefetched_raw
        # belt-and-braces against a torn-down future-completion that
        # already wrote into the slot.
        try:
            self.app._cancel_background_fetch_jobs()
        except AttributeError:
            try:
                self.app._stop_stream()
            except Exception:  # noqa: BLE001
                pass
            for jname in ("_poll_job", "_reload_job"):
                j = getattr(self.app, jname, None)
                if j is not None:
                    with _silent_tcl():
                        self.app.after_cancel(j)
                    setattr(self.app, jname, None)
        if hasattr(self.app, "_fetch_token"):
            self.app._fetch_token = int(self.app._fetch_token) + 1
        if hasattr(self.app, "_prefetched_raw"):
            self.app._prefetched_raw = None

        self.active = True
        self.focus_symbol = reference_symbol

        # Events feature: kick off async prefetch for the reference
        # symbol. Result arrives on the Tk thread and writes via
        # :meth:`set_event_bundle`. No-op for headless callers without
        # ``_fetch_executor`` / ``after`` (sync inline fetch falls back
        # to bundle = None and the EventsView simply stays empty).
        try:
            self.prefetch_events_for(reference_symbol)
        except Exception:  # noqa: BLE001
            pass

        # First tick: clock.index goes from -1 to 0. Visible list grows.
        self.engine.tick()
        self._sync_visible_to_clock()
        # Phase 1d-followup: fast-forward to session_date open so the
        # user starts with the lookback days as visible "history" and
        # the next user tick lands on the first bar of the chosen
        # session day. Side-effect-free: bumps the clock index
        # directly + replays the visible buffer; does not invoke
        # engine.tick (so no fills, no equity-curve points, no MAE/MFE
        # rolling for the warmup bars — there are no positions yet).
        self._fast_forward_to_session_open()

        # Pre-install reset: clear pre-sandbox compare state. Compare
        # mid-session reopens via the dialog or the toolbar checkbox
        # and re-registers a sandbox-controlled visible list — but at
        # session start we don't want a stale pre-session compare
        # series leaking into the first render. Only the start path
        # does this; subsequent focus swaps via ``set_focus`` /
        # ``_install_focus_for_display`` must leave the compare slot
        # alone, otherwise space-cycling the primary watchlist would
        # silently drop a user-enabled compare chart (b37 regression).
        try:
            self.app._sandbox_reset_compare_for_session_start()
        except AttributeError:
            pass
        self.app._install_sandbox_primary_series(
            symbol=reference_symbol,
            candles=self.visible_candles_by_symbol[reference_symbol],
            interval=self.interval,
            full_session_length=self.full_display_length_for(reference_symbol),
        )
        # Seed the day tracker so the first day-boundary detection in
        # ``next_bar`` has a baseline.
        self._last_clock_session_date = self.current_session_date()
        panel = getattr(self.app, "_sandbox_panel", None)
        if panel is not None:
            with _silent_tcl():
                panel.refresh()

    def register_ticker(
        self,
        symbol: str,
        candles: List[Any],
    ) -> List[Any]:
        """Add ``symbol`` to the active session at the current clock.

        Returns the per-symbol *visible* candle list — callers install
        it on a chart slot. The list is the engine's append-only buffer
        for this symbol and grows in place on every subsequent tick;
        its identity is stable for the rest of the session, so series
        + indicator caches keyed off ``id(visible)`` never miss.

        Idempotent: re-registering with the same content fingerprint
        returns the existing visible list (no engine mutation, no
        catch-up replay). Re-registering with **different** content
        is rejected with :class:`ValueError` — replacing a BarSeries
        mid-session would retroactively change open-position avg cost
        / MAE / MFE accounting (rubber-duck D blocker).

        ``candles`` is trimmed to the session window before registration
        so a symbol with multi-month history doesn't drag the engine's
        per-symbol storage out of proportion. Symbols whose data has
        no overlap with the session window register as empty visible
        lists (caller may surface "symbol has no data for this date").
        """
        if not self.active or self.engine is None:
            raise RuntimeError("no active sandbox session")
        if not isinstance(symbol, str):
            raise TypeError(
                f"symbol must be str, got {type(symbol).__name__}"
            )
        symbol = symbol.strip()
        if not symbol:
            raise ValueError("symbol must be non-empty")

        # Remember the raw (un-trimmed) source so an auto-cycle to a
        # different session_date can re-trim against the new window
        # without losing data.
        self._raw_full_candles[symbol] = list(candles or [])

        trimmed = filter_candles_to_session(
            candles or [], self.session_date,
            lookback_days=self.lookback_days,
            bounded=self.auto_cycle,
            regular_only=not self.include_extended,
        )

        if symbol in self.bars_by_symbol:
            # Idempotency: same length + same first/last date → no-op.
            existing = self.full_candles_by_symbol[symbol]
            if _candles_match(existing, trimmed):
                return self.visible_candles_by_symbol[symbol]
            raise ValueError(
                f"symbol {symbol!r} is already registered in this session "
                f"with different content; restart the session to load "
                f"updated bars"
            )

        bars = from_candles(symbol, self.interval, trimmed)
        # engine.register_bars enforces its own immutability check —
        # raises ValueError on different-content re-register.
        self.engine.register_bars(symbol, bars)
        self.full_candles_by_symbol[symbol] = list(trimmed)
        self.bars_by_symbol[symbol] = bars
        visible: List[Any] = []
        self.visible_candles_by_symbol[symbol] = visible
        # Catch up: append every full-list entry whose ts is <= now_ts.
        self._sync_visible_for_symbol(symbol)
        # Kick off async events prefetch for this newly-registered
        # symbol; bundle arrives on the Tk thread and the corporate
        # actions queue is registered via :meth:`set_event_bundle`.
        try:
            self.prefetch_events_for(symbol)
        except Exception:  # noqa: BLE001
            pass
        return visible

    def end_session(self) -> Optional[SessionResult]:
        """Tear down. Returns final SessionResult or None if not active.

        In auto-cycle mode the returned result merges all archived
        cycles' fills / pre-trades / post-trades / equity points with
        the current engine's pending state, so the saved session
        covers every cycle the user replayed.
        """
        if not self.active:
            return None
        result = self.result()
        self.active = False
        # M5 ChartStack lockstep: fire one last subscriber pass so
        # any registered card can observe the final
        # ``active=False`` state (panel uses this to restore manual
        # pin state + restart live streams) BEFORE we drop the
        # subscription list.
        self._fire_card_subscribers()
        self._card_subscribers.clear()
        if self._memento is not None:
            self._memento.restore(self.app)
            self._memento = None
            # _render touches Tk widgets that may already be torn down at
            # app-close time — narrow guard, not a blanket sweep.
            with _silent_tcl():
                self.app._render()
        # Hide panel if the app exposed one.
        try:
            self.app._hide_sandbox_panel()
        except AttributeError:
            pass
        return result

    def result(self) -> Optional[SessionResult]:
        """Merged SessionResult covering every cycle (and the current).

        Single-cycle sessions return the engine's own result unchanged
        — no archived state. Multi-cycle (auto-cycle) sessions
        concatenate archived + current lists in cycle order.
        """
        if self.engine is None:
            return None
        cur = self.engine.result()
        if not (self._archived_fills or self._archived_pre_trades
                or self._archived_post_trades or self._archived_equity
                or self._archived_cash_adjustments
                or self._archived_quantity_adjustments):
            return cur
        return SessionResult(
            spec=cur.spec,
            fills=list(self._archived_fills) + list(cur.fills),
            pre_trades=list(self._archived_pre_trades)
                       + list(cur.pre_trades),
            post_trades=list(self._archived_post_trades)
                        + list(cur.post_trades),
            equity_curve=list(self._archived_equity)
                         + list(cur.equity_curve),
            final_cash=float(cur.final_cash),
            cash_adjustments=list(self._archived_cash_adjustments)
                             + list(cur.cash_adjustments),
            quantity_adjustments=list(self._archived_quantity_adjustments)
                                 + list(cur.quantity_adjustments),
        )

    def cycle_to_next(self) -> bool:
        """Roll into the next eligible session date (auto-cycle path).

        Auto-flattens any still-open positions at the last bar's close
        (synthetic fills, no slippage / commission) so they don't
        carry across what is conceptually a random unrelated trading
        day. Each flatten emits a :class:`PostTradeReview` so the
        journal still records the round-trip.

        Picks the next date from ``_eligible_dates`` shuffled by
        ``deck_seed`` (round-robin once exhausted), re-trims the
        reference candles + every previously-registered ticker
        against the new window, builds a fresh :class:`SandboxEngine`
        with ``starting_cash = current portfolio cash`` (so equity
        carries forward), ticks once so the first bar of the new day
        is visible, and reinstalls the focused symbol's series on
        the chart.

        Returns ``True`` on success, ``False`` if there are no
        eligible dates left or the rebuild produced empty bars
        (caller surfaces "no more eligible dates").
        """
        if self.engine is None or not self._eligible_dates:
            return False

        # 1) Auto-flatten open positions at the last bar's close.
        last_ts = int(self.engine.clock.now_ts)
        last_close_by_symbol: Dict[str, float] = {}
        for sym, bs in self.bars_by_symbol.items():
            if len(bs.close) > 0:
                last_close_by_symbol[sym] = float(bs.close[-1])
        flattens = self.engine.flatten_all_at_close(
            last_bar_ts=last_ts,
            prices=last_close_by_symbol,
        )
        # Drive the flatten-emitted post-trades through the same
        # callback the panel uses (so the user reviews them too — but
        # without the modal blocking we surface a synthesised note).
        self._handle_new_post_trades()

        # 2) Archive the current engine's state, then drop it.
        prev = self.engine.result()
        self._archived_fills.extend(prev.fills)
        self._archived_pre_trades.extend(prev.pre_trades)
        self._archived_post_trades.extend(prev.post_trades)
        self._archived_equity.extend(prev.equity_curve)
        self._archived_cash_adjustments.extend(prev.cash_adjustments)
        self._archived_quantity_adjustments.extend(prev.quantity_adjustments)
        cash_carry = float(self.engine.portfolio.cash)

        # 3) Pick the next date (deterministic round-robin on the
        # shuffled deck, advancing past the *current* session_date so
        # we never re-pick the same day on a single cycle hop).
        from .deck import shuffle_dates
        deck_seed = int(self.spec.deck_seed) if self.spec is not None else 0
        order = shuffle_dates(self._eligible_dates, deck_seed)
        try:
            cur_idx = order.index(self.session_date)
        except ValueError:
            cur_idx = -1
        self._cycle_index += 1
        new_date = order[(cur_idx + 1) % len(order)]

        # 4) Re-trim reference candles to the new window.
        ref_sym = self.reference_symbol
        ref_raw = self._raw_full_candles.get(ref_sym, [])
        trimmed_ref = filter_candles_to_session(
            ref_raw, new_date,
            lookback_days=self.lookback_days,
            bounded=True,
            regular_only=not self.include_extended,
        )
        if not trimmed_ref:
            return False
        ref_bars = from_candles(ref_sym, self.interval, trimmed_ref)

        # 5) Build a new engine with cash carried forward.
        new_spec = dataclasses.replace(self.spec, starting_cash=cash_carry)
        self.engine = SandboxEngine(
            spec=new_spec,
            bars_by_symbol={ref_sym: ref_bars},
            master_timeline=ref_bars.ts.copy(),
        )
        self.session_date = new_date
        self.spec = new_spec
        self.full_candles_by_symbol = {ref_sym: list(trimmed_ref)}
        self.bars_by_symbol = {ref_sym: ref_bars}
        self.visible_candles_by_symbol = {ref_sym: []}
        self._post_trade_count_seen = 0
        # Bump events fetch token so any in-flight callbacks from the
        # previous cycle bail out before writing into the new engine.
        # Existing bundles stay in place — events are stable across the
        # session calendar, only the corporate-action queues need
        # rebinding (handled below per-symbol).
        self._events_fetch_token += 1

        # 6) Re-register every previously-known ticker against the new
        # window. Symbols whose data doesn't intersect the new day
        # register as empty (visible list stays empty until the user
        # reloads the ticker — same UX as a symbol with no data).
        for sym, raw in list(self._raw_full_candles.items()):
            if sym == ref_sym:
                continue
            re_trimmed = filter_candles_to_session(
                raw, new_date,
                lookback_days=self.lookback_days,
                bounded=True,
                regular_only=not self.include_extended,
            )
            if not re_trimmed:
                continue
            bars = from_candles(sym, self.interval, re_trimmed)
            try:
                self.engine.register_bars(sym, bars)
            except ValueError:
                # Shouldn't happen — fresh engine. Defensive only.
                continue
            self.full_candles_by_symbol[sym] = list(re_trimmed)
            self.bars_by_symbol[sym] = bars
            self.visible_candles_by_symbol[sym] = []

        # 6b) Re-register corporate actions for every symbol that has
        # an event bundle cached. The fresh engine in step 5 starts
        # with an empty action queue; without this loop, prior-cycle
        # dividend / split events would silently no-op in the new
        # cycle.
        for sym in list(self._raw_full_events.keys()):
            bundle = self._raw_full_events.get(sym)
            if bundle is None:
                continue
            try:
                self._register_corporate_actions_from_bundle(sym, bundle)
            except Exception:  # noqa: BLE001
                pass

        # 7) Tick once so the user sees the new day's first bar.
        self.engine.tick()
        self._sync_visible_to_clock()
        # Fast-forward to session_date open so the new cycle also
        # gets ~lookback_days of prior intraday context visible
        # before the user's next N-press.
        self._fast_forward_to_session_open()

        # 8) Reinstall focus on the chart. Only when a panel is
        # actually showing — auto-cycle in headless / smoke contexts
        # must not pull the app's chart state out from under non-
        # sandbox tests. Honours ``display_interval`` so the user
        # remains on whichever timeframe they were viewing.
        focus = self.focus_symbol if self.focus_symbol in \
            self.visible_candles_by_symbol else ref_sym
        self.focus_symbol = focus
        if getattr(self.app, "_sandbox_panel", None) is not None:
            self._install_focus_for_display(focus)
            # Compare slot may alias an old visible list — clear it so
            # the user re-engages compare in the new cycle if they
            # want it.
            with _silent_tcl():
                cmp_var = getattr(self.app, "compare_var", None)
                if cmp_var is not None and bool(cmp_var.get()):
                    cmp_var.set(False)
                    if hasattr(self.app, "_on_compare_toggle"):
                        self.app._on_compare_toggle()

        return True

    # ------------------------------------------------------------------
    # Replay controls
    # ------------------------------------------------------------------

    def next_bar(self) -> bool:
        """Advance one tick. Returns True if a tick occurred, False at end.

        After the tick:
        1. Per-symbol visible lists grow in place to match the new clock.
        2. Indicator + series cache for the *focused* list are invalidated
           via the app's :meth:`_invalidate_focused_panels` primitive.
        3. The focused slot is redrawn via :meth:`_draw_slice`.
        4. Any newly-emitted :class:`PostTradeReview` runs through
           ``_post_trade_callback`` (Phase 1c) to gather the user's
           review text. The closed-trade record is replaced in-place
           with one carrying ``user_review``. A "post" screenshot is
           captured if a ``screenshot_dir`` was configured.
        """
        if not self.active or self.engine is None:
            return False
        if not self.engine.tick():
            # End-of-day. In auto-cycle mode, roll into the next
            # eligible date and treat that as a real tick (the user
            # presses N at the last bar and sees the next day's first
            # bar without the replay halting).
            if self.auto_cycle and self.cycle_to_next():
                # cycle_to_next has already ticked the new engine and
                # reinstalled the focused series; fall through to the
                # post-tick refresh below.
                pass
            else:
                return False
        else:
            self._sync_visible_to_clock()
        # Indicator cache: the visible list grew by one. The app primitive
        # owns the cross-cache invalidation contract — see audit #2.
        self._invalidate_focused()
        # Phase 1d-multitf: track day-boundary crossings so the daily
        # display can be refreshed only when the master clock crosses
        # midnight (rather than on every intraday tick — would be
        # wasteful since daily_visible only changes on day rollover).
        prev_day = self._last_clock_session_date
        cur_day = self.current_session_date()
        self._last_clock_session_date = cur_day
        day_changed = (prev_day is not None and cur_day is not None
                       and prev_day != cur_day)

        if self.display_interval == "1d":
            # Daily display: skip the per-intraday-tick chart refresh
            # entirely (daily series did not change). On day-boundary
            # crossings, re-install the now-extended daily-visible
            # slice so the just-completed session bar appears.
            if day_changed and self.focus_symbol:
                self._install_focus_for_display(self.focus_symbol)
        elif self.display_interval and self.display_interval != self.interval:
            # Higher-TF intraday display (e.g. user viewing 15m while
            # primary ticks 5m). Re-install aggregated series each tick
            # — the trailing bucket grows in place to reflect the new
            # primary bar's high/low/close/volume contribution. Cost is
            # O(visible primary bars) which is negligible at sandbox
            # scale (~78 bars/day with 1d lookback).
            if self.focus_symbol:
                self._install_focus_for_display(self.focus_symbol)
        else:
            # Append-aware redraw. Use the streaming-rollover refresh
            # helper instead of a full ``_draw_slice(0, N)`` so matplotlib
            # only rebuilds the visible-window slice (driven by xlim) and
            # the right-edge xlim shifts with the new bar. With a 5-day
            # lookback intraday session, this drops per-tick redraw cost
            # from O(visible_total) to O(visible_window).
            with _silent_tcl():
                if hasattr(self.app, "_refresh_view_after_append"):
                    self.app._refresh_view_after_append("primary")
                else:
                    visible = self.visible_candles_by_symbol[self.focus_symbol]
                    self.app._draw_slice("primary", 0, len(visible))

        # Per-tick compare refresh. The compare slot's candle list is the
        # controller's identity-stable ``visible_candles_by_symbol[sym]``
        # for whatever ticker the user installed there
        # (``_install_sandbox_compare_series``); the list grows in place
        # each tick. Without this refresh the primary chart progresses
        # while the compare panel sits frozen at session-start state.
        # Invalidates the series + indicator caches keyed against that
        # list (id-based) before re-rendering. The display-interval
        # branches above already handle primary's higher-TF / daily
        # cases; for sandbox the compare slot is currently expected to
        # mirror the primary's tick interval, so a simple per-tick redraw
        # is sufficient.
        cmp_on = False
        with _silent_tcl():
            cmp_var = getattr(self.app, "compare_var", None)
            cmp_on = bool(cmp_var.get()) if cmp_var is not None else False
        cmp_list = getattr(self.app, "_compare", None) if cmp_on else None
        if cmp_list:
            # Pure-append on the compare side too — prefer the
            # append-aware notification so the indicator cache's
            # incremental hook can take the fast path for the compare
            # slot. Falls back to full invalidate if the app predates
            # the split (defensive parity with ``_invalidate_focused``).
            notify = getattr(self.app, "_notify_focused_panels_appended", None)
            if notify is not None:
                try:
                    notify(cmp_list)
                except Exception:  # noqa: BLE001
                    pass
            else:
                invalidator = getattr(self.app, "_invalidate_focused_panels", None)
                if invalidator is not None:
                    try:
                        invalidator(cmp_list)
                    except Exception:  # noqa: BLE001
                        pass
            with _silent_tcl():
                if hasattr(self.app, "_refresh_view_after_append"):
                    self.app._refresh_view_after_append("compare")

        self._handle_new_post_trades()

        panel = getattr(self.app, "_sandbox_panel", None)
        if panel is not None:
            with _silent_tcl():
                panel.refresh()
        # Watchlist Last/Change must follow the replay clock. Day-only
        # tick when the clock crosses midnight is enough — chg/pct
        # depend on prior session close (constant within a day) and
        # last_intraday only updates the displayed primary/compare
        # panels, not the watchlist Last column. But "last" in the
        # watchlist *should* track the replay clock for every pinned
        # ticker, so refresh on every tick. The cached fetcher hits
        # the in-memory cache for already-loaded tickers, so cost is
        # one dict lookup + one slice per pinned symbol per tick.
        try:
            refresh = getattr(self.app, "_refresh_watchlist_for_sandbox",
                              None)
            if refresh is not None:
                refresh()
        except Exception:  # noqa: BLE001
            pass
        # Scanner re-evaluation runs after the watchlist refresh so any
        # universe-wide candle list extensions for this tick are applied
        # first. Tolerated to a no-op when the host app has no scanner
        # tab (smoke tests / headless callers).
        try:
            refresh_scan = getattr(self.app, "_refresh_scanner_for_sandbox",
                                   None)
            if refresh_scan is not None:
                refresh_scan()
        except Exception:  # noqa: BLE001
            pass
        # Exits evaluation runs after the scanner — a strategy may
        # depend on a scanner indicator value, so the scanner's
        # IndicatorMemo should be warm by now. Tolerated no-op when
        # the host app has no exits stack (smoke / headless).
        # Entries fire BEFORE exits within a single tick: a market /
        # limit / stop / indicator / scanner-alert entry should fill on
        # this bar, mint a position via tracker.open_from_fill, and
        # then any on_fill_exit_ids should bracket it before the same
        # tick's exits-evaluation pass runs. Tolerated no-op when the
        # host app has no entries stack.
        try:
            refresh_entries = getattr(
                self.app, "_refresh_entries_for_sandbox", None)
            if refresh_entries is not None:
                refresh_entries()
        except Exception:  # noqa: BLE001
            pass
        try:
            refresh_exits = getattr(self.app, "_refresh_exits_for_sandbox",
                                    None)
            if refresh_exits is not None:
                refresh_exits()
        except Exception:  # noqa: BLE001
            pass
        # M5 ChartStack lockstep: fire registered card subscribers
        # synchronously after the engine + every other subsystem has
        # observed the new bar. Subscribers see fully-settled state.
        self._fire_card_subscribers()
        return True

    def set_focus(self, symbol: str) -> None:
        """Swap the primary chart to ``symbol`` at the current clock.

        Honours :attr:`display_interval`:

        * ``"1d"`` — install daily series for ``symbol`` (falls back to
          primary intraday if no daily was registered for that ticker,
          e.g. the lazy fetch failed).
        * Higher-TF intraday — install aggregated series.
        * Otherwise — install raw primary visible list.
        """
        if not self.active:
            return
        if symbol not in self.visible_candles_by_symbol:
            return
        if symbol == self.focus_symbol:
            return
        self.focus_symbol = symbol
        self._install_focus_for_display(symbol)
        panel = getattr(self.app, "_sandbox_panel", None)
        if panel is not None:
            with _silent_tcl():
                panel.refresh()

    def aggregated_visible_for(self, symbol: str, target_interval: str) -> List[Any]:
        """Aggregate the visible primary candles for ``symbol`` to ``target_interval``.

        Used by the multi-interval display path. ``target_interval``
        must be in :attr:`display_intervals` (otherwise the caller is
        outside the user's allowed set). Returns an empty list if
        ``symbol`` isn't registered.
        """
        if symbol not in self.visible_candles_by_symbol:
            return []
        primary = self.visible_candles_by_symbol[symbol]
        if target_interval == self.interval:
            return list(primary)
        return _aggregate(primary, self.interval, target_interval)

    def full_display_length_for(self, symbol: str) -> int:
        """Eventual bar count for the active display interval at session end.

        Used by :meth:`_install_focus_for_display` to pre-allocate the
        chart's xlim so the primary panel is sized to span the full
        session window from session start. Bars whose timestamps have
        not yet been crossed simply leave empty space on the right;
        revealed bars stay anchored at their final positions.

        Mirrors the dispatch in :meth:`_install_focus_for_display`:

        * ``"1d"`` display → length of ``daily_full_by_symbol[symbol]``.
        * Higher-TF intraday display (e.g. 15m view on 5m primary) →
          length of the full primary list aggregated to the target
          interval (computed once; the controller does not cache).
        * Otherwise → length of the full raw primary list.

        Returns ``0`` when the symbol is unknown or the relevant full
        list is empty (caller treats that as "skip pre-allocation").
        """
        disp = self.display_interval
        if disp == "1d":
            return len(self.daily_full_by_symbol.get(symbol) or [])
        primary_full = self.full_candles_by_symbol.get(symbol) or []
        if disp and disp != self.interval:
            try:
                return len(_aggregate(primary_full, self.interval, disp))
            except Exception:  # noqa: BLE001
                return len(primary_full)
        return len(primary_full)

    def _install_focus_for_display(self, symbol: str) -> None:
        """Install the focus chart with candles matching ``self.display_interval``.

        Single chokepoint for the three places that re-render the
        focused symbol after a state change (next_bar, set_focus,
        set_display_interval, cycle_to_next). Honours the active
        display interval:

        * ``None`` or equal to primary → install raw visible primary list.
        * ``"1d"`` → install :meth:`daily_visible_for` series.
          If empty, falls back to intraday and clears display_interval.
        * Other (must be in :attr:`display_intervals`) → install the
          aggregated higher-TF series via :meth:`aggregated_visible_for`.
        """
        with _silent_tcl(AttributeError):
            disp = self.display_interval
            full_len = self.full_display_length_for(symbol)
            if disp == "1d":
                daily = self.daily_visible_for(symbol)
                if daily:
                    self.app._install_sandbox_primary_series(
                        symbol=symbol,
                        candles=daily,
                        interval="1d",
                        full_session_length=full_len,
                    )
                    return
                # No daily for this symbol — drop back to intraday.
                self.display_interval = None
                disp = None
                full_len = self.full_display_length_for(symbol)
            if disp and disp != self.interval:
                # Higher-TF intraday: aggregate from primary.
                agg = self.aggregated_visible_for(symbol, disp)
                self.app._install_sandbox_primary_series(
                    symbol=symbol,
                    candles=agg,
                    interval=disp,
                    full_session_length=full_len,
                )
                return
            # Primary intraday (display_interval is None or == primary).
            self.app._install_sandbox_primary_series(
                symbol=symbol,
                candles=self.visible_candles_by_symbol.get(symbol, []),
                interval=self.interval,
                full_session_length=full_len,
            )

    def set_display_interval(self, interval: str) -> bool:
        """Toggle the chart display between intraday timeframes / daily context.

        Returns True on a successful swap. Accepts:

        * ``self.interval`` (the sandbox's primary tick interval) —
          switches back to per-tick intraday view.
        * Any other interval in :attr:`display_intervals` — switches to
          a higher-TF aggregated view (computed on the fly from primary
          bars; the trailing aggregated bar is in-progress and updates
          each tick).
        * ``"1d"`` — switches to daily-context view (completed sessions
          only, capped to ``self.daily_lookback_bars`` bars).

        Any other value is rejected (returns False) — the caller
        should revert the UI's interval var. Daily-mode requests for
        a symbol with no registered daily series degrade to intraday
        with a False return so the caller can surface a status warning.
        """
        if not self.active:
            return False
        if not self.focus_symbol:
            return False
        if interval == self.interval:
            self.display_interval = None
            self._install_focus_for_display(self.focus_symbol)
            return True
        if interval == "1d":
            sym = self.focus_symbol
            daily = self.daily_visible_for(sym)
            if not daily:
                return False
            self.display_interval = "1d"
            self._install_focus_for_display(sym)
            return True
        if interval in self.display_intervals:
            # Validated at start_session: interval is an integer
            # multiple of primary, so aggregation is well-defined.
            self.display_interval = interval
            self._install_focus_for_display(self.focus_symbol)
            return True
        return False

    # ------------------------------------------------------------------
    # Order intake
    # ------------------------------------------------------------------

    def submit_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        pre_trade_data: Dict[str, Any],
    ) -> str:
        """Queue an order with a mandatory pre-trade journal entry.

        ``pre_trade_data`` keys: ``setup_tag``, ``thesis``,
        ``conviction``, ``size``, ``target`` (Optional[float]),
        ``notes``. ``thesis`` and ``size`` are mandatory.

        Side-effect (Phase 1c): captures a "pre-trade" PNG screenshot
        of the chart if a ``screenshot_dir`` was configured at
        ``start_session`` time.
        """
        if not self.active or self.engine is None:
            raise RuntimeError("no active sandbox session")
        if symbol not in self.bars_by_symbol:
            raise ValueError(f"symbol {symbol!r} not in this session")
        thesis = str(pre_trade_data.get("thesis") or "").strip()
        if not thesis:
            raise ValueError("thesis is mandatory")
        if float(quantity) <= 0:
            raise ValueError("quantity must be positive")

        side_enum = Side.BUY if str(side).lower() == "buy" else Side.SELL
        self._next_order_seq += 1
        order_id = f"ord-{self._next_order_seq:04d}"
        # Use the *engine clock* timestamp, not wall time — ensures
        # session-result reproducibility across replays.
        ts = int(self.engine.clock.now_ts)

        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=side_enum,
            quantity=float(quantity),
            submitted_ts=ts,
        )
        target = pre_trade_data.get("target")
        prox = self._compute_event_proximity(symbol, ts)
        pre = PreTradeEntry(
            order_id=order_id,
            ts=ts,
            symbol=symbol,
            side=side_enum.value,
            setup_tag=str(pre_trade_data.get("setup_tag") or ""),
            thesis=thesis,
            conviction=int(pre_trade_data.get("conviction") or 3),
            size=float(pre_trade_data.get("size") or quantity),
            target=(None if target in (None, "") else float(target)),
            notes=str(pre_trade_data.get("notes") or ""),
            next_earnings_ts=prox["next_earnings_ts"],
            last_earnings_ts=prox["last_earnings_ts"],
            last_dividend_ts=prox["last_dividend_ts"],
            last_split_ts=prox["last_split_ts"],
            earnings_proximity_tag=prox["earnings_proximity_tag"],
            dividend_proximity_tag=prox["dividend_proximity_tag"],
        )
        self.engine.submit_order(order, pre_trade=pre)

        # Phase 1c: capture chart context at submit time.
        self._capture_screenshot(f"{order_id}_pre.png")

        return order_id

    # ------------------------------------------------------------------
    # Inspection helpers (for the SandboxPanel / smoke)
    # ------------------------------------------------------------------

    def positions_snapshot(self) -> List[Dict[str, Any]]:
        if self.engine is None:
            return []
        out: List[Dict[str, Any]] = []
        for sym, pos in self.engine.portfolio.positions.items():
            if pos.quantity == 0.0:
                continue
            out.append({
                "symbol": sym,
                "quantity": float(pos.quantity),
                "avg_cost": float(pos.avg_cost),
                "realized_pnl": float(pos.realized_pnl),
            })
        return out

    def cash(self) -> float:
        if self.engine is None:
            return 0.0
        return float(self.engine.portfolio.cash)

    def clock_ts(self) -> Optional[int]:
        if self.engine is None or self.engine.clock.index < 0:
            return None
        return int(self.engine.clock.now_ts)

    def tickers(self) -> List[str]:
        return list(self.full_candles_by_symbol.keys())

    # ------------------------------------------------------------------
    # Daily-context (multi-timeframe) helpers
    # ------------------------------------------------------------------

    def register_daily_for(
        self,
        symbol: str,
        daily_candles: List[Any],
    ) -> None:
        """Lazy-attach a per-symbol raw daily series for 1d-context display.

        Optional: failure to fetch daily must NOT block intraday
        registration (rubber-duck blocker). Idempotent — calling with
        the same symbol replaces the cached raw series, which is fine
        because daily history is effectively immutable for replay.
        """
        if not symbol or not daily_candles:
            return
        self.daily_full_by_symbol[symbol] = list(daily_candles)

    def current_session_date(self) -> Optional[_dt.date]:
        """Return the *date* of the master clock's current bar (UTC).

        Used to gate which daily bars are visible: bars with a
        session date strictly less than this one are completed
        history; the bar matching this date (if any) is the
        in-progress session and is **not** shown on the daily chart.
        """
        ts = self.clock_ts()
        if ts is None:
            return None
        return _dt.datetime.fromtimestamp(
            int(ts), tz=_dt.timezone.utc).date()

    def daily_visible_for(self, symbol: str) -> List[Any]:
        """Return the daily-bar slice for ``symbol`` visible at the
        current master-clock time.

        Visibility rule (rubber-duck-approved): bar whose session
        date is **strictly less** than the current clock's session
        date is included; the in-progress day's bar (if present in
        the raw series) is omitted. The result is capped to the
        last ``self.daily_lookback_bars`` entries.

        Empty list is a valid return — symbol may have been
        registered with no daily series, the clock may be before
        any daily bar, or the lookback cap may have evicted
        everything.
        """
        full = self.daily_full_by_symbol.get(symbol)
        if not full:
            return []
        cur = self.current_session_date()
        if cur is None:
            return []
        out: List[Any] = []
        for c in full:
            d = getattr(c, "date", None)
            if d is None:
                continue
            cd = d.date() if isinstance(d, _dt.datetime) else d
            if cd < cur:
                out.append(c)
        cap = max(0, int(self.daily_lookback_bars))
        if cap and len(out) > cap:
            out = out[-cap:]
        return out

    # ------------------------------------------------------------------
    # Events feature
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _sync_visible_to_clock(self) -> None:
        """Grow each per-symbol visible list to match the current clock."""
        if self.engine is None or self.engine.clock.index < 0:
            return
        for sym in list(self.full_candles_by_symbol):
            self._sync_visible_for_symbol(sym)

    def _fast_forward_to_session_open(self) -> None:
        """Advance the clock to the first bar of ``session_date``.

        Side-effect-free warmup: bumps ``engine.clock.index`` directly
        and re-syncs the per-symbol visible lists. Does *not* invoke
        ``engine.tick()`` for the warmup bars, so:

        * no pending orders fill (there are none at session start anyway),
        * no MAE/MFE rolls (no positions),
        * no equity-curve points are appended for the lookback days
          (the curve starts on the user's first session-day bar).

        If session_date is None, or no master-timeline bar lands on /
        after the session-date open (e.g. session_date is the very
        first day in the window, or beyond the data), this is a no-op.
        Cleans up any equity-curve point produced by the previous
        ``engine.tick()`` so the curve doesn't include a stray "bar 0"
        from the lookback period.
        """
        if self.engine is None or self.session_date is None:
            return
        timeline = self.engine.clock.timeline
        if timeline is None or len(timeline) == 0:
            return
        target_ts = int(_dt.datetime.combine(
            self.session_date, _dt.time(0, 0),
            tzinfo=_dt.timezone.utc).timestamp())
        # First index whose ts >= start-of-session-day (UTC midnight).
        target_idx = int(_np.searchsorted(timeline, target_ts, side="left"))
        if target_idx <= 0 or target_idx >= len(timeline):
            return
        if self.engine.clock.index >= target_idx:
            return
        # Replace the warmup equity points (if any) with a single
        # point at the new index, so the curve starts cleanly on the
        # session-day open.
        self.engine.clock.index = target_idx
        try:
            self.engine.portfolio.equity_curve.clear()
        except AttributeError:
            pass
        self._sync_visible_to_clock()

    def _sync_visible_for_symbol(self, symbol: str) -> None:
        """Grow ``symbol``'s visible list to ``clock.now_ts``.

        Per-symbol extent comes from ``BarSeries.index_for_ts(now_ts)``
        so symbols missing some master-timeline timestamps don't leak
        future bars (rubber-duck blocker — clock+symbol mismatch).
        """
        if self.engine is None or self.engine.clock.index < 0:
            return
        ts = int(self.engine.clock.now_ts)
        full = self.full_candles_by_symbol.get(symbol)
        bs = self.bars_by_symbol.get(symbol)
        visible = self.visible_candles_by_symbol.get(symbol)
        if full is None or bs is None or visible is None:
            return
        if len(full) == 0:
            return
        i = bs.index_for_ts(ts)
        if i is None:
            # No bar for this sym at-or-before ts: keep existing extent.
            return
        target_n = int(i) + 1
        # Append only — never reslice, never replace, so list identity
        # (and therefore series-cache + indicator-cache hits) survives.
        while len(visible) < target_n and len(visible) < len(full):
            visible.append(full[len(visible)])

    def _invalidate_focused(self) -> None:
        """Notify the app that the focused list grew by one bar.

        Routes through :meth:`ChartApp._notify_focused_panels_appended`
        (append-aware variant of ``_invalidate_focused_panels``) so the
        indicator cache's incremental extension hook can detect the
        same-id length-grew condition on the next render and route
        through ``inc_step`` for indicators that support it (SMA, EMA
        today). Falls back to the full-invalidate primitive if the
        app doesn't expose the append-aware method (older test doubles
        / external callers). Either way the controller never reaches
        into ``app._series_cache`` / ``app._indicator_cache`` directly
        (audit #2).
        """
        if not self.focus_symbol:
            return
        visible = self.visible_candles_by_symbol.get(self.focus_symbol)
        if visible is None:
            return
        notify = getattr(self.app, "_notify_focused_panels_appended", None)
        if notify is not None:
            notify(visible)
            return
        # Fallback for app objects that predate the append-aware split.
        invalidator = getattr(self.app, "_invalidate_focused_panels", None)
        if invalidator is None:
            return
        invalidator(visible)

    def _handle_new_post_trades(self) -> None:
        """Drive the post-trade review flow for any closures this tick.

        For each new :class:`PostTradeReview` the engine emitted, calls
        the registered callback (if any) to obtain the user's review
        text, replaces the engine's record in place via
        :func:`dataclasses.replace`, and captures a "post" screenshot.
        """
        if self.engine is None:
            return
        all_post = self.engine.post_trades
        if len(all_post) <= self._post_trade_count_seen:
            return
        for i in range(self._post_trade_count_seen, len(all_post)):
            ptr = all_post[i]
            user_review = ""
            if self._post_trade_callback is not None:
                try:
                    res = self._post_trade_callback(ptr)
                    if res:
                        user_review = str(res)
                except Exception:  # noqa: BLE001
                    user_review = ""
            if user_review:
                all_post[i] = dataclasses.replace(ptr, user_review=user_review)
            ref_id = ptr.ref_pre_trade_id or f"close-{i:04d}"
            self._capture_screenshot(f"{ref_id}_post.png")
        self._post_trade_count_seen = len(all_post)

    def _capture_screenshot(self, filename: str) -> Optional[Path]:
        """Save the current chart figure to ``screenshot_dir / filename``.

        No-op if no screenshot dir was configured (smoke / headless).
        Returns the written path on success, ``None`` otherwise.
        """
        if self.screenshot_dir is None:
            return None
        capture = getattr(self.app, "_capture_chart_png", None)
        if capture is None:
            return None
        path = self.screenshot_dir / filename
        try:
            capture(path)
        except Exception:  # noqa: BLE001
            return None
        return path


__all__ = ("SandboxController", "SandboxMemento")
