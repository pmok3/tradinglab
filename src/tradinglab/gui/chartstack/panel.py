"""ChartStack panel — top-level Tk container for the mini-chart strip.

Owns a single shared :class:`matplotlib.figure.Figure` plus one
:class:`~matplotlib.backends.backend_tkagg.FigureCanvasTkAgg`
(option A from §5.1 of the synthesis). N stacked
:class:`~matplotlib.axes.Axes` are partitioned across N
:class:`CardWidget` slots; per-card-bbox blitting (M3) targets
those slots individually.

ChartApp owns this panel via composition (constructor kwarg
``owner=self``); the spec explicitly rejects a 12th mixin. M2
reads ``owner._watchlist_snapshot`` and submits fetches through
``owner._fetch_executor`` + ``owner._worker_inbox`` for first-paint
sparklines. M3 wires streams via ``owner._stream_queue`` (using the
shared queue rather than a new one — the ``"card:N"`` slot prefix
lets ``_drain_stream_queue`` route events back here); per-card
blitting collapses tick → restore_region + draw_artist + blit, and
an ``after_idle`` coalescer caps redraws at one per Tk idle so
burst ticks don't pile up.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import TYPE_CHECKING, Any

from . import dpi as _dpi
from . import owner_state as _owner_state
from . import settings_adapter as _adapter
from .alerts import AlertEngine, AlertResult, AlertTier
from .binding import CardBinding, resolve_bindings
from .card import CardWidget
from .controller import SubscriptionRegistry
from .render import draw_card_placeholder, draw_card_sparkline
from .series_cache import Bar, CardSeriesCache

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..geometry_store import GeometryStore


# Placeholder symbols used when `owner._watchlist_snapshot` is empty
# (or when the panel is constructed with `owner=None` in unit tests).
_PLACEHOLDER_SYMBOLS = ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN")


def _bars_from_candles(candles: object, maxlen: int) -> list[Bar]:
    """Adapt a ``Candle`` list (data layer) to internal ``Bar`` instances.

    Truncates to the trailing ``maxlen`` candles so the cache stays
    bounded regardless of how much history the fetcher returns.
    Tolerates missing fields (returns whatever is present) and
    accepts both attribute access and dict-shaped rows for test
    fixtures.
    """
    if not candles:
        return []
    seq = list(candles)[-maxlen:]
    out: list[Bar] = []
    for c in seq:
        try:
            ts = getattr(c, "date", None) or (c.get("date") if isinstance(c, dict) else None)
            o = float(getattr(c, "open", None) if not isinstance(c, dict) else c.get("open"))
            h = float(getattr(c, "high", None) if not isinstance(c, dict) else c.get("high"))
            lo = float(getattr(c, "low", None) if not isinstance(c, dict) else c.get("low"))
            cl = float(getattr(c, "close", None) if not isinstance(c, dict) else c.get("close"))
            v = float(getattr(c, "volume", 0.0) if not isinstance(c, dict) else c.get("volume", 0.0))
            sess = (getattr(c, "session", None)
                    if not isinstance(c, dict) else c.get("session"))
            sess_s = str(sess) if sess is not None else "regular"
        except (TypeError, ValueError):
            continue
        out.append(Bar(ts=ts, open=o, high=h, low=lo, close=cl, volume=v,
                       session=sess_s))
    return out


def _bar_from_event_bar(evt_bar: object) -> Bar | None:
    """Adapt a single event-side bar (``Candle`` from a stream) to a ``Bar``.

    Returns ``None`` when fields are missing / unparseable so the
    caller can drop the event without raising. Captures the
    pre-/regular-/post-market ``session`` so card overlays (PMH/PML,
    session-anchored VWAP) can filter without redoing classification.
    """
    if evt_bar is None:
        return None
    try:
        ts = getattr(evt_bar, "date", None)
        if ts is None and isinstance(evt_bar, dict):
            ts = evt_bar.get("date")
        if isinstance(evt_bar, dict):
            o = float(evt_bar["open"])
            h = float(evt_bar["high"])
            lo = float(evt_bar["low"])
            cl = float(evt_bar["close"])
            v = float(evt_bar.get("volume", 0.0))
            sess = str(evt_bar.get("session", "regular"))
        else:
            o = float(evt_bar.open)  # type: ignore[attr-defined]
            h = float(evt_bar.high)  # type: ignore[attr-defined]
            lo = float(evt_bar.low)  # type: ignore[attr-defined]
            cl = float(evt_bar.close)  # type: ignore[attr-defined]
            v = float(getattr(evt_bar, "volume", 0.0))
            sess = str(getattr(evt_bar, "session", "regular"))
    except (KeyError, AttributeError, TypeError, ValueError):
        return None
    return Bar(ts=ts, open=o, high=h, low=lo, close=cl, volume=v, session=sess)


class ChartStackPanel(ttk.Frame):
    """Vertical strip of N mini-chart cards. M3 streaming + blit."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        owner: object | None = None,
        geometry_store: GeometryStore | None = None,
    ) -> None:
        super().__init__(master)
        self.owner = owner
        self.geometry_store = geometry_store

        self._on_card_promote_callback: Callable[[str], None] | None = None
        self._mpl_cids: list[int] = []
        self._visible = True

        # Build matplotlib figure + canvas (single-canvas option A).
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure

        n = max(1, _adapter.card_count())
        # M7: DPI-aware card cap. 4K-class displays may host up to
        # 6 cards; standard displays are capped at 5 to preserve the
        # main chart's 70 % minimum width. If the configured count
        # exceeds the cap, clamp + mirror the clamped count onto
        # ``self._effective_card_count`` so the owner can surface a
        # status-bar warning the first time the cap bit.
        cap = _dpi.card_count_cap(self)
        if n > cap:
            self._card_count_capped_from: int | None = n
            n = cap
        else:
            self._card_count_capped_from = None
        self._effective_card_count = n
        self._figure = Figure(figsize=(2.2, max(1.0, 0.96 * n)), dpi=100)
        # Stacked axes; squeeze=False so a single-card stack still yields a list.
        axes_grid = self._figure.subplots(
            nrows=n, ncols=1, squeeze=False
        )
        axes_list = [axes_grid[i][0] for i in range(n)]
        # Tight-ish layout; cards are intentionally close together.
        try:
            self._figure.subplots_adjust(
                left=0.04, right=0.98, top=0.98, bottom=0.02, hspace=0.15
            )
        except Exception:  # noqa: BLE001 - non-fatal layout hint
            pass

        self._canvas = FigureCanvasTkAgg(self._figure, master=self)
        widget = self._canvas.get_tk_widget()
        widget.pack(fill=tk.BOTH, expand=True)

        # Build cards.
        self._cards: list[CardWidget] = [
            CardWidget(self, idx, ax) for idx, ax in enumerate(axes_list)
        ]
        # Per-card series caches (M2): key = slot_index → CardSeriesCache.
        # Slot-keyed (not symbol-keyed) because rebinding a slot to a new
        # symbol invalidates the cache anyway via `set_binding`.
        max_bars = int(_adapter.get("chartstack.sparkline_bar_count") or 60)
        self._series_caches: dict[int, CardSeriesCache] = {
            card.slot_index: CardSeriesCache(maxlen=max_bars)
            for card in self._cards
        }

        # M3 streaming + blit infrastructure ------------------------------
        # Shared registry refcount-dedupes upstream stream subscriptions
        # so two cards bound to "AAPL@5m" share one broker stream.
        self._subscription_registry = SubscriptionRegistry()
        # Per-slot blit-bg cache; populated on `draw_event` and
        # invalidated by `_invalidate_bbox_caches` whenever the
        # canvas size / axes layout changes (binding swap, theme,
        # destroy).
        self._bbox_bgs: dict[int, Any] = {}
        # `after_idle` coalescer: dirty slot set + scheduled job id.
        self._dirty_slots: set[int] = set()
        self._idle_flush_after: str | None = None
        # Track ChartApp's `after()` jobs so we can release them on destroy.
        self._after_jobs: set[str] = set()
        # M4: per-card border tints. Maps slot_index → "#RRGGBB" or
        # absent for no-tint. The M6 alert engine will populate this
        # via :meth:`set_card_tint`; M4 establishes the visual API.
        self._card_tints: dict[int, str] = {}
        # M5: user-driven manual pins. Symbols pinned via the
        # context menu (or future pin API) get a dedicated card
        # slot when binding mode is HYBRID. Stored as a plain list
        # so insertion order = pin order. Sandbox attach snapshots
        # this so pins added during a sandbox session don't leak
        # back to live mode at ``detach_sandbox`` time.
        self._manual_pins: list[object] = []
        # M5: sandbox lockstep state. ``_sandbox`` is the
        # :class:`SandboxController` we're currently attached to;
        # ``_sandbox_subscription_release`` is the unregister fn
        # returned by ``register_card_subscriber``;
        # ``_sandbox_pre_pins`` is the snapshot taken at attach
        # time so detach can restore.
        self._sandbox: Any = None
        self._sandbox_subscription_release: Callable[[], None] | None = None
        self._sandbox_pre_pins: list[object] | None = None

        # M6: four-tier alert engine. One engine per panel; its
        # per-card state (PMH edge, prev unrealized P&L, tier-3
        # ping pacing) is keyed by slot index so binding swaps
        # clear cleanly via ``_alert_engine.reset(slot)``. Per-slot
        # last-applied tier is mirrored here so the panel knows
        # when to update the tint vs. leave it alone.
        self._alert_engine = AlertEngine()
        self._slot_alert_tier: dict[int, AlertTier] = {}
        self._slot_alert_badge: dict[int, str | None] = {}

        # Theme palette resolved from :func:`apply_theme`. ``None``
        # until the owner cascades a theme dict in (typically
        # immediately after construction via
        # ``ChartApp._apply_theme``). Stored so card re-renders
        # — ``_render_card_sparkline``, ``draw_card_placeholder``
        # — can pick up the right text / facecolor without round-
        # tripping through ``apply_theme`` on every flush.
        self._theme_palette: dict[str, str] | None = None

        # Click-to-promote: hit-test mpl button-press against each Axes.
        # mpl_connect returns a CID; we stash it for clean disconnect on
        # destroy(). mpl_connect itself is GUI-thread safe (Tkinter
        # backend wraps the callbacks in widget bindings).
        try:
            cid = self._canvas.mpl_connect(
                "button_press_event", self._on_canvas_click)
            self._mpl_cids.append(cid)
        except Exception:  # noqa: BLE001 - test stubs may lack mpl_connect
            pass
        # M3: draw_event fires after every full repaint; we snapshot
        # each card's blit background here so subsequent tick events
        # can blit just the dirty axes without redrawing the whole
        # figure.
        try:
            cid = self._canvas.mpl_connect(
                "draw_event", self._on_canvas_draw)
            self._mpl_cids.append(cid)
        except Exception:  # noqa: BLE001
            pass

        # First-paint refresh — populates placeholders with resolved bindings.
        self.refresh()

    # --------------------------------------------------------- public API --
    def refresh(self) -> None:
        """Re-resolve bindings and redraw all cards.

        In addition to placeholder bind, kick the controller's
        ``start()`` for every non-empty slot so first-paint fetches
        kick off in parallel, and ``start_stream()`` so live ticks
        flow into the slot's series cache. The fetch result lands
        via ``apply_card_stash`` (called by the worker-inbox drain);
        stream ticks land via ``apply_stream_event`` (called by
        ``_drain_stream_queue``).
        """
        bindings = self._resolve()
        for card, binding in zip(self._cards, bindings, strict=False):
            # Reset the cache when the binding changes so we don't
            # paint stale bars from the previous symbol.
            prev = card.binding.symbol if card.binding is not None else None
            new = binding.symbol if binding is not None else None
            if prev != new:
                cache = self._series_caches.get(card.slot_index)
                if cache is not None:
                    cache.invalidate()
                # M6: a binding change invalidates per-slot alert
                # state (edge flags, prev unrealized P&L, pacing).
                self._clear_alert_for_slot(card.slot_index)
            card.set_binding(binding)
            if binding is not None:
                try:
                    card.controller.start()
                except Exception:  # noqa: BLE001 - never block the refresh
                    pass
                try:
                    card.controller.start_stream(self._subscription_registry)
                except Exception:  # noqa: BLE001
                    pass
        # Layout changed → blit-bg caches are stale.
        self._invalidate_bbox_caches()
        try:
            self._canvas.draw_idle()
        except Exception:  # noqa: BLE001 - test stubs may not implement draw_idle
            pass

    def apply_card_stash(
        self,
        slot_index: int,
        token: int,
        symbol: str,
        candles: object,
    ) -> None:
        """Receive bars from the worker-inbox drain and re-render the card.

        Token-gated: payloads whose ``token`` is older than the
        controller's current ``token`` are dropped (the slot was
        re-bound while the fetch was in flight). Symbol mismatch is
        also a stale signal — same drop semantics.
        """
        if slot_index < 0 or slot_index >= len(self._cards):
            return
        card = self._cards[slot_index]
        if card.binding is None or card.binding.symbol != symbol:
            return
        if card.controller.token != token:
            return
        cache = self._series_caches.get(slot_index)
        if cache is None:
            return
        bars = _bars_from_candles(candles, cache.maxlen)
        cache.invalidate()
        for b in bars:
            cache.append_rollover(b)
        if bars:
            try:
                self._render_card_sparkline(card, bars)
                card.controller.mark_ready()
            except Exception:  # noqa: BLE001 - never block the drain
                draw_card_placeholder(card.ax, card.binding,
                                      theme=self._theme_palette)
                card.controller.mark_error()
        else:
            draw_card_placeholder(card.ax, card.binding,
                                  theme=self._theme_palette)
            card.controller.mark_error()
        # Full redraw — invalidates blit-bg, will be re-captured on
        # the next draw_event tick.
        self._invalidate_bbox_caches(slot_index)
        try:
            self._canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass

    def apply_stream_event(
        self,
        slot_index: int,
        token: int,
        kind: str,
        bar: Any,
    ) -> None:
        """Receive a single stream tick / rollover from the drain.

        Mutates the per-slot ``CardSeriesCache`` in place
        (``upsert_tick`` for ``"tick"`` events, ``append_rollover``
        for ``"rollover"``) and schedules an ``after_idle``
        coalesced redraw. Per-card-bbox blitting flushes the dirty
        slots on the next idle so a 100 Hz tick burst collapses to
        roughly one paint per Tk idle (~16 ms).

        Token-gated: events with stale tokens (older than the
        controller's current ``_token``) are dropped — the slot
        was re-bound while the event was queued.
        """
        if kind not in ("tick", "rollover"):
            return
        if slot_index < 0 or slot_index >= len(self._cards):
            return
        card = self._cards[slot_index]
        if card.controller.token != token:
            return
        cache = self._series_caches.get(slot_index)
        if cache is None:
            return
        parsed = _bar_from_event_bar(bar)
        if parsed is None:
            return
        try:
            if kind == "tick":
                cache.upsert_tick(
                    parsed.ts,
                    (parsed.open, parsed.high, parsed.low,
                     parsed.close, parsed.volume),
                    session=parsed.session,
                )
            else:  # rollover
                cache.append_rollover(parsed)
        except (TypeError, ValueError):
            return
        # Mark the slot dirty + schedule a coalesced flush.
        self._dirty_slots.add(slot_index)
        # M6: evaluate alerts for this card on every tick. Live-mode
        # ticks come through this path; sandbox ticks come through
        # ``_on_sandbox_tick`` which has its own batched call.
        self._evaluate_alerts_for_slot(slot_index)
        self._schedule_idle_flush()

    def set_card_tint(self, slot_index: int, color: str | None) -> None:
        """Set or clear the per-card border tint.

        M4 establishes this hook; the M6 alert engine will drive it
        (amber for Tier-1, blue for Tier-2, red for Tier-3). Passing
        ``color=None`` clears the tint. Marks the slot dirty so the
        next flush re-renders with the updated spine color.
        """
        if slot_index < 0 or slot_index >= len(self._cards):
            return
        if color is None:
            self._card_tints.pop(slot_index, None)
        else:
            self._card_tints[slot_index] = str(color)
        self._dirty_slots.add(slot_index)
        self._invalidate_bbox_caches(slot_index)
        self._schedule_idle_flush()

    # ---------------------------------------------------- M6 alerts API --
    def _evaluate_alerts_for_slot(self, slot_index: int) -> None:
        """Run the alert engine for one slot and apply its tint.

        Reads the slot's :class:`CardSeriesCache`, the owner's
        scanner / position state via :mod:`owner_state`, and asks
        the :class:`AlertEngine` for the highest-tier result. The
        result drives :meth:`set_card_tint` + a header badge for
        Tier-4. No-op when ChartStack is disabled or the slot has
        no binding.
        """
        if not _adapter.is_enabled():
            return
        if slot_index < 0 or slot_index >= len(self._cards):
            return
        card = self._cards[slot_index]
        if card.binding is None:
            self._clear_alert_for_slot(slot_index)
            return
        cache = self._series_caches.get(slot_index)
        bars = list(cache.snapshot()) if cache is not None else []
        symbol = card.binding.symbol
        # Owner-state reads — wrapped in try/except so a broken
        # owner can never block the alert engine.
        try:
            scanner_row = _owner_state.scanner_row_for(self.owner, symbol)
        except Exception:  # noqa: BLE001
            scanner_row = None
        try:
            position = _owner_state.open_position_for(self.owner, symbol)
        except Exception:  # noqa: BLE001
            position = None
        interval_minutes = self._interval_minutes_for(card)
        days_to_earnings, is_exdiv_today = self._events_context_for(symbol)
        try:
            result = self._alert_engine.evaluate(
                slot_index,
                bars=bars,
                interval_minutes=interval_minutes,
                position=position,
                scanner_row=scanner_row,
                days_to_earnings=days_to_earnings,
                is_exdiv_today=is_exdiv_today,
            )
        except Exception:  # noqa: BLE001 - never let alerts break a tick
            return
        self._apply_alert_result(slot_index, result)

    def _evaluate_alerts_for_all_cards(self) -> None:
        """Sandbox path: evaluate every bound card."""
        if not _adapter.is_enabled():
            return
        for card in self._cards:
            if card.binding is None:
                continue
            self._evaluate_alerts_for_slot(card.slot_index)

    def _apply_alert_result(self, slot_index: int, result: AlertResult) -> None:
        """Persist result + drive the tint/badge surfaces."""
        cur_tier = self._slot_alert_tier.get(slot_index, AlertTier.NONE)
        cur_badge = self._slot_alert_badge.get(slot_index)
        new_tier = result.tier
        new_badge = result.badge
        # Tint update: only call set_card_tint when the color
        # actually changed, to avoid re-marking slots dirty on
        # every tick.
        if new_tier is not cur_tier:
            self.set_card_tint(slot_index, result.color)
            self._slot_alert_tier[slot_index] = new_tier
        if new_badge != cur_badge:
            self._slot_alert_badge[slot_index] = new_badge
            # Badge re-paint piggybacks on the dirty-slot flush.
            self._dirty_slots.add(slot_index)

    def _clear_alert_for_slot(self, slot_index: int) -> None:
        """Reset the engine's per-slot state + clear visual surfaces."""
        try:
            self._alert_engine.reset(slot_index)
        except Exception:  # noqa: BLE001
            pass
        if self._slot_alert_tier.get(slot_index, AlertTier.NONE) is not AlertTier.NONE:
            self.set_card_tint(slot_index, None)
            self._slot_alert_tier[slot_index] = AlertTier.NONE
        if self._slot_alert_badge.get(slot_index) is not None:
            self._slot_alert_badge[slot_index] = None
            self._dirty_slots.add(slot_index)

    def _interval_minutes_for(self, card: CardWidget) -> int:
        """Resolve the card's interval in minutes (best-effort).

        Reads ``owner.interval_var`` (same source the controller
        uses for stream subscriptions) and parses "1m"/"5m"/"15m"
        into an integer. Defaults to 5 so the alert engine stays
        on the conservative RVOL threshold (1.8×) when the
        interval is ambiguous.
        """
        try:
            raw = self.owner.interval_var.get()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return 5
        if not isinstance(raw, str):
            return 5
        s = raw.strip().lower()
        if s.endswith("m"):
            try:
                return max(int(s[:-1]), 1)
            except ValueError:
                return 5
        return 5

    def _events_context_for(
        self, symbol: str
    ) -> tuple[int | None, bool]:
        """Pull (days-to-next-earnings, ex-div-today) from the owner.

        ChartApp keeps an ``_events_cache: dict[str, EventBundle]``
        from the historical-earnings feature; read it best-effort.
        Returns ``(None, False)`` when the cache is missing or the
        symbol has no bundle so Tier-4 stays quiet rather than
        firing spurious alerts during dev.
        """
        bundle = None
        try:
            cache = getattr(self.owner, "_events_cache", None) or {}
            if isinstance(cache, dict):
                bundle = cache.get(symbol.upper())
        except Exception:  # noqa: BLE001
            bundle = None
        if bundle is None:
            return (None, False)
        days = None
        is_exdiv = False
        try:
            import datetime as _dt
            today = _dt.date.today()
            # Earnings: find smallest ts >= today (in days).
            earn = getattr(bundle, "earnings", None) or []
            for rec in earn:
                ts_ms = getattr(rec, "ts", None)
                if ts_ms is None:
                    continue
                ts_date = _dt.datetime.fromtimestamp(
                    float(ts_ms) / 1000.0,
                    _dt.timezone.utc,
                ).date()
                diff = (ts_date - today).days
                if diff >= 0 and (days is None or diff < days):
                    days = diff
            divs = getattr(bundle, "dividends", None) or []
            for rec in divs:
                ex_ts = getattr(rec, "ex_ts", None)
                if ex_ts is None:
                    continue
                ex_date = _dt.datetime.fromtimestamp(
                    float(ex_ts) / 1000.0,
                    _dt.timezone.utc,
                ).date()
                if ex_date == today:
                    is_exdiv = True
                    break
        except Exception:  # noqa: BLE001
            return (None, False)
        return (days, is_exdiv)


    # ----------------------------------------------------- M5 pinning API --
    def pin_symbol(self, symbol: object) -> None:
        """Add ``symbol`` to the manual pin list (deduped, ordered).

        M5: pinned symbols get priority slot allocation in HYBRID
        binding mode. Idempotent — repeated pins are no-ops.
        Re-resolves bindings via ``refresh()`` so the new pin
        takes effect on the next layout pass.
        """
        if symbol is None:
            return
        # Dedupe by stringified value so callers can pass either
        # a raw symbol string or a richer object that stringifies
        # to it.
        key = str(symbol)
        for existing in self._manual_pins:
            if str(existing) == key:
                return
        self._manual_pins.append(symbol)
        self.refresh()

    def unpin_symbol(self, symbol: object) -> None:
        """Remove ``symbol`` from the manual pin list (no-op if absent).

        Re-resolves bindings via ``refresh()``.
        """
        key = str(symbol)
        before = len(self._manual_pins)
        self._manual_pins = [
            p for p in self._manual_pins if str(p) != key
        ]
        if len(self._manual_pins) != before:
            self.refresh()

    def clear_manual_pins(self) -> None:
        """Wipe the manual pin list and re-resolve bindings."""
        if not self._manual_pins:
            return
        self._manual_pins = []
        self.refresh()

    def get_manual_pins(self) -> tuple[object, ...]:
        """Read-only snapshot of the manual pin list."""
        return tuple(self._manual_pins)

    # --------------------------------------------------- M5 sandbox lockstep --
    def attach_sandbox(self, sandbox: Any) -> None:
        """Attach to a :class:`SandboxController` for lockstep replay.

        Snapshots the current manual pin list (restored on
        :meth:`detach_sandbox`) so pins added during a sandbox
        session don't leak back to live mode. Stops every active
        card stream — live-feed cards are not allowed while a
        sandbox is active (would break the look-ahead guarantee).
        Registers a per-tick subscriber on the sandbox controller
        so each ``next_bar`` refreshes every card's cache from
        ``sandbox.visible_candles_by_symbol``.

        Idempotent: re-attaching to the same sandbox is a no-op.
        """
        if sandbox is None or sandbox is self._sandbox:
            return
        if self._sandbox is not None:
            # Different sandbox already attached — detach first.
            self.detach_sandbox()
        # Snapshot pins so detach can restore.
        self._sandbox_pre_pins = list(self._manual_pins)
        # Stop every active card stream — no live feeds during
        # sandbox. The controller's ``stop_stream`` is idempotent.
        for card in self._cards:
            try:
                card.controller.stop_stream()
            except Exception:  # noqa: BLE001
                pass
        self._sandbox = sandbox
        # Register the per-tick subscriber. Storing the release
        # callable lets ``detach_sandbox`` unregister cleanly
        # even if the sandbox is later restarted.
        try:
            self._sandbox_subscription_release = (
                sandbox.register_card_subscriber(self._on_sandbox_tick))
        except Exception:  # noqa: BLE001
            self._sandbox_subscription_release = None
        # Fire one initial tick so cards reflect the freshly-
        # advanced sandbox state without waiting for next_bar.
        self._on_sandbox_tick()

    def detach_sandbox(self) -> None:
        """Detach from the active sandbox controller.

        Restores the pre-attach manual pin snapshot (M5: "pins
        don't carry back to live mode") and restarts streams
        for every bound card via ``refresh()``. Idempotent.
        """
        if self._sandbox is None:
            return
        release = self._sandbox_subscription_release
        self._sandbox_subscription_release = None
        if release is not None:
            try:
                release()
            except Exception:  # noqa: BLE001
                pass
        self._sandbox = None
        # Restore the pre-attach pin snapshot so any pins added
        # during the sandbox session are forgotten.
        if self._sandbox_pre_pins is not None:
            self._manual_pins = list(self._sandbox_pre_pins)
            self._sandbox_pre_pins = None
        # M6: alert engine state is sandbox-session-specific; wipe
        # it before live mode resumes so PMH edge flags and prev
        # P&L deltas don't bleed across sandbox→live boundaries.
        try:
            self._alert_engine.reset()
        except Exception:  # noqa: BLE001
            pass
        for slot in range(len(self._cards)):
            if self._slot_alert_tier.get(slot, AlertTier.NONE) is not AlertTier.NONE:
                self.set_card_tint(slot, None)
            self._slot_alert_tier[slot] = AlertTier.NONE
            self._slot_alert_badge[slot] = None
        # Re-resolve bindings + restart streams. Refresh() takes
        # care of the start_stream calls.
        self.refresh()

    def _on_sandbox_tick(self) -> None:
        """Per-tick callback invoked by the sandbox controller.

        Reads the per-symbol visible candle list from the
        controller for each card with a binding, snapshots it
        into the card's series cache, and marks the slot dirty
        so the next idle-flush blits the updated sparkline.
        Token bumps are NOT issued — the data is authoritative
        for the current binding (no stale-payload concern).

        If the sandbox has been deactivated (``end_session``
        already ran), this is the panel's signal to detach.
        """
        sb = self._sandbox
        if sb is None:
            return
        # ``end_session`` flips ``active`` to False then fires
        # subscribers one last time; use that as the detach
        # signal so live-mode state restoration happens
        # without an explicit ChartApp wire-through.
        try:
            still_active = bool(sb.is_active())
        except Exception:  # noqa: BLE001
            still_active = False
        if not still_active:
            self.detach_sandbox()
            return
        try:
            visible_map = sb.visible_candles_by_symbol
        except AttributeError:
            return
        for card in self._cards:
            if card.binding is None:
                continue
            sym = card.binding.symbol
            candles = visible_map.get(sym)
            if not candles:
                continue
            cache = self._series_caches.get(card.slot_index)
            if cache is None:
                continue
            bars = _bars_from_candles(candles, cache.maxlen)
            cache.invalidate()
            for b in bars:
                cache.append_rollover(b)
            self._dirty_slots.add(card.slot_index)
        # M6: evaluate alerts after every card cache settles.
        self._evaluate_alerts_for_all_cards()
        self._schedule_idle_flush()

    def apply_theme(self, theme: object | None = None) -> None:
        """Recolor figure / axes / cards from a theme palette.

        ``theme`` accepts three shapes for backwards compatibility:

        * a palette ``dict`` matching ``constants.LIGHT_THEME`` /
          ``DARK_THEME`` (with ``fig_bg`` / ``ax_bg`` / ``text`` and
          friends) — the primary path now that
          :meth:`ChartApp._apply_theme` cascades the already-resolved
          palette directly. User-authored overrides from
          ``settings.json`` ride through automatically.
        * a ``str`` (``"dark"`` / ``"light"``) — legacy entry; we
          resolve it via :func:`constants.resolve_theme` using the
          owner's ``_theme_overrides`` when available so overrides
          still apply.
        * ``None`` / unknown — falls back to the light-mode default.

        Beyond recoloring the figure patch and each axes facecolor,
        we also mark every card slot dirty and schedule an idle
        flush. The re-render pass re-invokes
        :func:`render.draw_card_candles` /
        :func:`render.draw_card_placeholder` with the new palette
        so the symbol / placeholder text colors persist across
        subsequent ``ax.clear()`` calls (which would otherwise reset
        text artists to matplotlib's default black on every refresh).
        """
        palette = self._resolve_theme_palette(theme)
        self._theme_palette = palette
        bg = palette.get("fig_bg") or palette.get("win_bg") or "#ffffff"
        ax_bg = palette.get("ax_bg") or bg
        fg = palette.get("text") or "#202020"
        try:
            self._figure.patch.set_facecolor(bg)
            for card in self._cards:
                card.ax.set_facecolor(ax_bg)
                for txt in card.ax.texts:
                    # Skip the colored %chg label — its color encodes
                    # direction and must not be overwritten by theme.
                    try:
                        if txt.get_ha() == "right":
                            continue
                    except Exception:  # noqa: BLE001
                        pass
                    txt.set_color(fg)
                # Mark every slot dirty so the next idle flush
                # re-renders with the new palette baked into the
                # text artists — guards against `ax.clear()` paths
                # that would otherwise reset text colors on the
                # next sparkline update.
                self._dirty_slots.add(card.slot_index)
            self._invalidate_bbox_caches()
            self._schedule_idle_flush()
            self._canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass

    def _resolve_theme_palette(self, theme: object | None) -> dict[str, str]:
        """Normalize ``theme`` into a palette ``dict``.

        Dict inputs are returned as-is when they look like a real
        palette (presence of any of ``fig_bg`` / ``ax_bg`` /
        ``text``). String inputs are routed through
        :func:`constants.resolve_theme` so user-authored overrides
        on the owning app cascade through. The fallback for
        bare-stub callers (unit tests with no owner) is the
        hardcoded light / dark default — never raises.
        """
        if isinstance(theme, dict) and (
            "fig_bg" in theme or "ax_bg" in theme or "text" in theme
        ):
            return dict(theme)
        if isinstance(theme, str):
            mode = "dark" if theme.lower() == "dark" else "light"
        elif isinstance(theme, dict) and theme.get("dark"):
            mode = "dark"
        else:
            mode = "light"
        try:
            from ...constants import resolve_theme
            owner = getattr(self, "owner", None)
            overrides = getattr(owner, "_theme_overrides", None) if owner is not None else None
            return dict(resolve_theme(mode, overrides))
        except Exception:  # noqa: BLE001 - never block theme application
            if mode == "dark":
                return {
                    "fig_bg": "#1e1e1e", "ax_bg": "#2b2b2b",
                    "text": "#dcdcdc", "win_bg": "#1e1e1e",
                }
            return {
                "fig_bg": "#fafafa", "ax_bg": "#ffffff",
                "text": "#111111", "win_bg": "#f0f0f0",
            }

    def set_visible(self, visible: bool) -> None:
        """Pack-forget or re-pack the panel."""
        visible = bool(visible)
        if visible == self._visible:
            return
        self._visible = visible
        try:
            if visible:
                self.pack(side=tk.LEFT, fill=tk.BOTH, expand=False)
            else:
                self.pack_forget()
        except Exception:  # noqa: BLE001 - parent geometry manager may differ
            pass

    def is_visible(self) -> bool:
        """Return the panel's last ``set_visible`` state."""
        return self._visible

    def demote_to(self, promoted_symbol: str, demoted_symbol: str) -> None:
        """Rebind the card currently showing ``promoted_symbol`` to ``demoted_symbol``.

        Called by ChartApp's promote callback after the main chart
        adopts ``promoted_symbol``: the just-vacated card slot picks
        up the symbol that was on the main chart (same-slot demote
        per §2.5 of the synthesis). No-op when the promoted card is
        unfindable or the demoted symbol is empty / equal.
        """
        if not promoted_symbol or not demoted_symbol:
            return
        if promoted_symbol == demoted_symbol:
            return
        for card in self._cards:
            if card.binding is None:
                continue
            if card.binding.symbol == promoted_symbol:
                new_binding = CardBinding(
                    symbol=demoted_symbol,
                    source_label=card.binding.source_label,
                )
                cache = self._series_caches.get(card.slot_index)
                if cache is not None:
                    cache.invalidate()
                card.set_binding(new_binding)
                try:
                    card.controller.start()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    card.controller.start_stream(self._subscription_registry)
                except Exception:  # noqa: BLE001
                    pass
                self._invalidate_bbox_caches(card.slot_index)
                break
        try:
            self._canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass

    @property
    def cards(self) -> list[CardWidget]:
        """Read-only view onto the slot list (for tests / introspection)."""
        return list(self._cards)

    @property
    def figure(self):
        """Return the shared matplotlib :class:`Figure`."""
        return self._figure

    @property
    def canvas(self):
        """Return the underlying ``FigureCanvasTkAgg``."""
        return self._canvas

    @property
    def subscription_registry(self) -> SubscriptionRegistry:
        """Return the shared :class:`SubscriptionRegistry` (for tests)."""
        return self._subscription_registry

    # Public callback hook (settable; fires on click-to-promote).
    @property
    def on_card_promote(self) -> Callable[[str], None] | None:
        """Callback fired when a card is promoted to the main chart."""
        return self._on_card_promote_callback

    @on_card_promote.setter
    def on_card_promote(self, cb: Callable[[str], None] | None) -> None:
        self._on_card_promote_callback = cb

    # ----------------------------------------------------------- destroy --
    def destroy(self) -> None:  # noqa: D401 - Tk override
        """Clean up matplotlib connects + Tk widgets + stream subs."""
        # Cancel any pending after-idle flush.
        if self._idle_flush_after is not None:
            try:
                self.after_cancel(self._idle_flush_after)
            except Exception:  # noqa: BLE001
                pass
            self._idle_flush_after = None
        # M5: release sandbox lockstep subscription so the
        # controller doesn't hold a dangling reference to a
        # destroyed panel.
        if self._sandbox_subscription_release is not None:
            try:
                self._sandbox_subscription_release()
            except Exception:  # noqa: BLE001
                pass
            self._sandbox_subscription_release = None
        self._sandbox = None
        # Release every card's stream subscription before tearing
        # down the figure (otherwise the registry would hold dangling
        # refs into a destroyed canvas).
        for card in self._cards:
            try:
                card.controller.stop_stream()
            except Exception:  # noqa: BLE001
                pass
        for cid in self._mpl_cids:
            try:
                self._canvas.mpl_disconnect(cid)
            except Exception:  # noqa: BLE001
                pass
        self._mpl_cids.clear()
        self._bbox_bgs.clear()
        self._dirty_slots.clear()
        try:
            self._figure.clear()
        except Exception:  # noqa: BLE001
            pass
        super().destroy()

    # ----------------------------------------------------------- helpers --
    def _resolve(self) -> list[CardBinding | None]:
        """Resolve current bindings from owner state (or placeholders)."""
        n = len(self._cards)
        watchlist = list(getattr(self.owner, "_watchlist_snapshot", []) or [])
        if not watchlist:
            watchlist = list(_PLACEHOLDER_SYMBOLS[:n])
        mode = _adapter.binding_mode()
        # M6: scanner top-N + open-positions are now sourced from
        # ChartApp state via duck-typed helpers in ``owner_state``.
        # Both are optional — empty owner state collapses HYBRID to
        # the watchlist branch, matching the M2/M5 behavior.
        scanner = tuple(_owner_state.scanner_symbols(self.owner))
        positions = tuple(_owner_state.open_position_symbols(self.owner))
        return resolve_bindings(
            mode,
            watchlist=watchlist,
            scanner_results=scanner,
            open_positions=positions,
            manual_pins=tuple(self._manual_pins),
            card_count=n,
        )

    def _on_canvas_click(self, event) -> None:
        """Hit-test the mpl click against each card's Axes; fire promote."""
        cb = self._on_card_promote_callback
        if cb is None:
            return
        # Only handle primary button (left click). Right-click / context
        # menus are M4.
        try:
            if int(event.button) != 1:
                return
        except (TypeError, ValueError, AttributeError):
            return
        if event.inaxes is None:
            return
        for card in self._cards:
            if card.ax is event.inaxes and card.binding is not None:
                try:
                    cb(card.binding.symbol)
                except Exception:  # noqa: BLE001 - never blow up the canvas
                    pass
                return

    # ============================================================== blit ==
    def _on_canvas_draw(self, _event) -> None:
        """Snapshot each card's blit background after a full repaint.

        Called from `mpl_connect("draw_event", ...)` — the matplotlib
        backend fires this after every full canvas draw. We capture
        the per-axes pixel buffer here so subsequent ``apply_stream_event``
        ticks can blit just the dirty card region via
        ``canvas.restore_region(bg) + ax.draw_artist(line) +
        canvas.blit(ax.bbox)``.
        """
        try:
            self._bbox_bgs.clear()
            for card in self._cards:
                try:
                    bg = self._canvas.copy_from_bbox(card.ax.bbox)
                    self._bbox_bgs[card.slot_index] = bg
                except Exception:  # noqa: BLE001 - bbox unavailable on Agg
                    pass
        except Exception:  # noqa: BLE001
            pass

    def _sparkline_kwargs(self) -> dict:
        """Stub kwargs for the candle renderer.

        Post-simplification (2026-05-16) the candle renderer
        accepts no overlay toggles — VWAP / PMH-PML / wash /
        volume-stroke / last-3-candles were removed when cards
        became plain daily candlesticks. This method survives so
        the M6 alert path (which still wants the per-slot tint
        + halt index plumbing) stays a one-line dict-merge.
        """
        return {}

    def _render_card_sparkline(self, card: CardWidget, bars: list) -> None:
        """Render a card body as daily OHLC candlesticks.

        Thin wrapper over :func:`draw_card_candles` (formerly
        ``draw_card_sparkline``) that forwards the per-slot tint
        + halt index. Callers are :meth:`apply_card_stash`
        (first-paint) and :meth:`_flush_dirty_cards` (sandbox
        refresh).
        """
        kw = self._sparkline_kwargs()
        kw["tint"] = self._card_tints.get(card.slot_index)
        kw["theme"] = self._theme_palette
        # halt_index is plumbed through for backwards-compat with
        # the M5 alert path. The candle renderer ignores it.
        try:
            kw["halted_at"] = card.controller.halt_index
        except AttributeError:
            kw["halted_at"] = None
        draw_card_sparkline(card.ax, bars, binding=card.binding, **kw)

    def _invalidate_bbox_caches(self, slot_index: int | None = None) -> None:
        """Drop blit backgrounds (full or single slot).

        Called after any operation that mutates artist layout
        (binding swap, theme change, panel resize). The next
        draw_event will repopulate.
        """
        if slot_index is None:
            self._bbox_bgs.clear()
            return
        self._bbox_bgs.pop(slot_index, None)

    def _schedule_idle_flush(self) -> None:
        """Arm a single ``after_idle`` flush for the dirty slot set.

        Idempotent — repeated calls before the flush fires collapse
        to a single redraw. Critical for the 100 Hz tick coalescing
        target (§5.2: 5-card coalesced burst ≤ 10 ms).
        """
        if self._idle_flush_after is not None:
            return
        try:
            self._idle_flush_after = self.after_idle(self._flush_dirty_cards)
        except Exception:  # noqa: BLE001 - test environments without after_idle
            self._idle_flush_after = None
            # Fallback: flush synchronously so tests can verify the
            # blit path without spinning Tk's event loop.
            try:
                self._flush_dirty_cards()
            except Exception:  # noqa: BLE001
                pass

    def _flush_dirty_cards(self) -> None:
        """Per-card-bbox blit pass for every dirty slot.

        Path:
        1. For each dirty slot:
           a. Re-render the sparkline against the current cache
              snapshot (cheap — at most 60 line points).
           b. If a blit-bg snapshot exists, ``restore_region`` +
              ``draw_artist(line)`` + ``blit(ax.bbox)`` — no full
              canvas redraw.
           c. Otherwise (first-tick-after-binding-change), fall
              back to ``draw_idle()``; the resulting ``draw_event``
              will capture fresh bgs.
        2. Clear the dirty set + the scheduled-flush handle.
        """
        self._idle_flush_after = None
        slots = list(self._dirty_slots)
        self._dirty_slots.clear()
        if not slots:
            return
        needs_full_redraw = False
        for idx in slots:
            if idx < 0 or idx >= len(self._cards):
                continue
            card = self._cards[idx]
            cache = self._series_caches.get(idx)
            if cache is None:
                continue
            bars = cache.snapshot()
            if len(bars) < 2:
                # Not enough to draw a sparkline yet — defer.
                continue
            try:
                self._render_card_sparkline(card, bars)
            except Exception:  # noqa: BLE001
                continue
            bg = self._bbox_bgs.get(idx)
            if bg is None:
                needs_full_redraw = True
                continue
            try:
                self._canvas.restore_region(bg)
                for artist in card.ax.get_children():
                    # Draw only artists that live inside this card's
                    # axes — sparkline Line2D + the symbol/%chg text.
                    try:
                        card.ax.draw_artist(artist)
                    except Exception:  # noqa: BLE001
                        pass
                self._canvas.blit(card.ax.bbox)
            except Exception:  # noqa: BLE001
                # On any blit failure, fall back to a full draw_idle so
                # the user doesn't see a stuck sparkline.
                needs_full_redraw = True
        if needs_full_redraw:
            try:
                self._canvas.draw_idle()
            except Exception:  # noqa: BLE001
                pass


__all__ = ["ChartStackPanel"]
