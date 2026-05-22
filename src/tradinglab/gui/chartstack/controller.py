"""ChartStack per-card controller — FSM + subscription accounting.

Each :class:`CardWidget` owns a :class:`CardController` that tracks
its data lifecycle (idle → fetching → ready → live → halted →
error) and holds the current binding. M2 wires the first-paint
fetch path: ``start()`` submits a worker on
``owner_app._fetch_executor`` that pulls bars from the active data
source and pushes them back to the Tk thread via the existing
``owner_app._worker_inbox`` queue (a new ``"card_stash"`` kind).
M3 lights up the streaming path: after a successful first-paint
the controller calls ``start_stream(registry)`` to subscribe via
:class:`SubscriptionRegistry`, which refcount-dedupes upstream
``STREAM_SOURCES`` subscriptions so two cards bound to the same
``(src, ticker, interval)`` share a single broker stream (the
§5.3 "no 5× broker-quota explosion" guarantee).

Token gating: every ``start()`` / ``bind()`` / ``stop()`` bumps
``_token``. Worker bodies + stream events embed the token they
observed at submit time in their payloads; ``apply_card_stash``
and ``apply_stream_event`` (on the panel) drop payloads whose
token is stale, so a slow fetch — or a stream tick — landing
after the user re-bound the slot is silently discarded. Same
pattern as ``ChartApp._fetch_token`` / ``_stream_token``.
"""

from __future__ import annotations

import threading
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .binding import CardBinding


class CardState(Enum):
    """Lifecycle state for one card's data pipeline."""

    IDLE = "IDLE"
    FETCHING = "FETCHING"
    READY = "READY"
    LIVE = "LIVE"
    HALTED = "HALTED"
    ERROR = "ERROR"


class SubscriptionRegistry:
    """Refcount-dedupe ``(src, ticker, interval)`` stream subscriptions.

    Two cards bound to the same ``(src, ticker, interval)`` share
    one upstream subscription on the underlying
    :data:`~tradinglab.streaming.STREAM_SOURCES` provider — the
    registry holds the per-key unsubscribe handle and dispatches
    incoming events to every registered consumer callback.

    Threading:
    Upstream callbacks may fire from any thread (per the
    ``StreamSource`` contract). Consumer callbacks registered here
    are likewise called on whatever thread the upstream chose;
    consumers are responsible for marshalling (the ChartStack uses
    ``owner._stream_queue.put`` — a thread-safe :class:`queue.Queue`
    — so the Tk drain picks them up). A simple lock guards the
    internal dispatch map; the dispatch itself iterates a
    point-in-time snapshot to avoid holding the lock across
    consumer-supplied code.
    """

    def __init__(self) -> None:
        # key → {"unsub": Callable[[], None],
        #        "callbacks": list[StreamCallback]}
        self._entries: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._lock = threading.RLock()

    def count(self, src: str, ticker: str, interval: str) -> int:
        """Return the number of active consumers for this key."""
        with self._lock:
            entry = self._entries.get((src, ticker, interval))
            return len(entry["callbacks"]) if entry is not None else 0

    def subscribe(
        self,
        src: str,
        ticker: str,
        interval: str,
        callback: Callable[[str, Any], None],
        *,
        upstream_factory: Callable[
            [str, str, str, Callable[[str, Any], None]],
            Optional[Callable[[], None]],
        ],
    ) -> Callable[[], None]:
        """Register ``callback`` for tick + rollover events on the key.

        Returns a per-consumer release handle. The handle is
        idempotent — calling it twice has no additional effect. On
        the first consumer for a key, ``upstream_factory`` is
        invoked with a registry-owned fan-out callback that
        broadcasts every event to all current consumers; the
        upstream's returned unsubscribe handle is held until the
        last consumer releases.

        ``upstream_factory`` returning ``None`` means "no upstream
        available" — the consumer is still tracked (so a later
        consumer for the same key doesn't double-subscribe), but no
        events will ever fire. The first consumer's release on a
        no-upstream key is a no-op on the upstream side.
        """
        key = (src, ticker, interval)
        is_first = False
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = {"unsub": None, "callbacks": []}
                self._entries[key] = entry
                is_first = True
            entry["callbacks"].append(callback)

        if is_first:
            try:
                unsub = upstream_factory(
                    src, ticker, interval, self._make_dispatcher(key))
            except Exception:  # noqa: BLE001 - factory failure → no upstream
                unsub = None
            with self._lock:
                # Re-fetch in case a concurrent release wiped the
                # entry between our check and the upstream call.
                entry = self._entries.get(key)
                if entry is not None:
                    entry["unsub"] = unsub
                elif unsub is not None:
                    # Entry got pulled while we were upstream-subscribing
                    # — release the just-created stream so we don't leak.
                    try:
                        unsub()
                    except Exception:  # noqa: BLE001
                        pass

        released = {"done": False}

        def _release() -> None:
            if released["done"]:
                return
            released["done"] = True
            stale_unsub: Optional[Callable[[], None]] = None
            with self._lock:
                entry = self._entries.get(key)
                if entry is None:
                    return
                try:
                    entry["callbacks"].remove(callback)
                except ValueError:
                    return
                if not entry["callbacks"]:
                    stale_unsub = entry.get("unsub")
                    self._entries.pop(key, None)
            if stale_unsub is not None:
                try:
                    stale_unsub()
                except Exception:  # noqa: BLE001
                    pass

        return _release

    def _make_dispatcher(
        self, key: tuple[str, str, str]
    ) -> Callable[[str, Any], None]:
        """Return the closure handed to the upstream `subscribe`.

        Iterates a point-in-time snapshot of the callback list so
        consumer callbacks (which we don't control) can't deadlock
        on the registry lock.
        """

        def _dispatch(kind: str, bar: Any) -> None:
            with self._lock:
                entry = self._entries.get(key)
                if entry is None:
                    return
                callbacks = list(entry["callbacks"])
            for cb in callbacks:
                try:
                    cb(kind, bar)
                except Exception:  # noqa: BLE001 - never break the upstream loop
                    pass

        return _dispatch

    # Legacy helpers (kept for backward-compat with the M1 refcount-only
    # tests; they don't touch the upstream layer).
    def refcount(self, src: str, ticker: str, interval: str) -> int:
        """Synthetic increment used by M1 unit tests; returns new count."""
        key = (src, ticker, interval)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = {"unsub": None, "callbacks": []}
                self._entries[key] = entry
            entry["callbacks"].append(_LEGACY_REFCOUNT_SENTINEL)
            return len(entry["callbacks"])

    def release(self, src: str, ticker: str, interval: str) -> int:
        """Synthetic decrement used by M1 unit tests; returns new count."""
        key = (src, ticker, interval)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return 0
            if _LEGACY_REFCOUNT_SENTINEL in entry["callbacks"]:
                entry["callbacks"].remove(_LEGACY_REFCOUNT_SENTINEL)
            if not entry["callbacks"]:
                self._entries.pop(key, None)
                return 0
            return len(entry["callbacks"])


# Internal sentinel for legacy refcount/release helpers.
_LEGACY_REFCOUNT_SENTINEL = object()


class CardController:
    """Per-card FSM + held :class:`CardBinding`.

    M2: ``start()`` submits a fetch via ``owner_app._fetch_executor``
    when ``owner_app`` exposes one; the worker pushes bars back to
    the Tk thread through ``owner_app._worker_inbox`` as a
    ``("card_stash", payload)`` item.
    M3: ``start_stream(registry)`` subscribes via the dedupe
    registry; stream callbacks marshal to ``owner_app._stream_queue``
    as ``(token, "card:N", src, ticker, interval, kind, bar)``
    tuples — the same shape the main chart uses, but with a
    slot prefix the drain branch keys on.
    ``stop()`` resets state and bumps the token so any in-flight
    fetch / stream event is dropped.
    """

    def __init__(self, slot_index: int, owner_app: object | None = None) -> None:
        self.slot_index = int(slot_index)
        self.owner_app = owner_app
        self._state = CardState.IDLE
        self._binding: Optional["CardBinding"] = None
        self._token: int = 0
        self._stream_release: Optional[Callable[[], None]] = None
        # Captured at stream-subscribe time so stop_stream can release
        # without needing the registry reference back.
        self._stream_key: Optional[tuple[str, str, str]] = None
        # M5: index of the bar where a halt was detected. ``None``
        # means the card is not halted. The render layer reads
        # ``halt_index`` to paint the vertical-bar glyph + grey
        # treatment. Independent of FSM state so callers can leave
        # a card in LIVE while flagging a halt mid-session.
        self._halt_index: Optional[int] = None

    # --- read-only properties -----------------------------------------
    @property
    def state(self) -> CardState:
        """Current FSM state."""
        return self._state

    @property
    def binding(self) -> Optional["CardBinding"]:
        """Current binding (or ``None`` for an empty slot)."""
        return self._binding

    @property
    def token(self) -> int:
        """Latest submit token; payloads tagged with older tokens are stale."""
        return self._token

    @property
    def stream_key(self) -> Optional[tuple[str, str, str]]:
        """Return the ``(src, ticker, interval)`` of the active stream, or None."""
        return self._stream_key

    @property
    def halt_index(self) -> Optional[int]:
        """Bar-index of the detected halt, or ``None`` if not halted.

        M5: render layer reads this to draw the vertical-bar glyph
        and grey-out the sparkline. Reset by ``bind()`` / ``stop()``
        / ``clear_halt()``.
        """
        return self._halt_index

    @property
    def is_halted(self) -> bool:
        """Convenience: True when ``halt_index`` is set."""
        return self._halt_index is not None

    # --- transitions --------------------------------------------------
    def bind(self, binding: "CardBinding | None") -> None:
        """Replace the held binding. Resets state to IDLE, bumps token,
        and tears down any active stream subscription.

        Bumping the token here means any fetch / stream event that
        was in flight for the *previous* binding is silently
        dropped when its payload eventually lands — there is no
        need to cancel the worker explicitly.
        """
        self.stop_stream()
        self._binding = binding
        self._state = CardState.IDLE
        self._token += 1
        # A new binding always clears any prior halt; the new
        # symbol has its own halt state, independent of the old.
        self._halt_index = None

    def start(self) -> None:
        """Kick a one-shot fetch via ``owner_app._fetch_executor``.

        Resolves source + interval on the *calling* (Tk) thread per
        the worker-inbox contract — workers must not touch Tcl/Tk
        variables. No-op when:

        * binding is ``None``
        * owner has no ``_fetch_executor`` (test stub / detached construct)
        * source / interval are unreadable

        Pushes ``("card_stash", (slot_index, token, symbol, bars))``
        onto ``owner_app._worker_inbox`` on completion. The inbox
        drain (``_drain_worker_inbox`` in ``gui/polling.py``) routes
        the payload to ``ChartStackPanel.apply_card_stash``.
        """
        if self._binding is None or self.owner_app is None:
            return
        owner = self.owner_app
        executor = getattr(owner, "_fetch_executor", None)
        inbox = getattr(owner, "_worker_inbox", None)
        if executor is None or inbox is None:
            return
        try:
            src = owner.source_var.get()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return
        # ChartStack cards are pinned to the daily timeframe per the
        # 2026-05-16 simplification — they are mini daily candlestick
        # charts, independent of the main chart's interval.
        itv = "1d"

        symbol = self._binding.symbol
        self._token += 1
        token = self._token
        slot = self.slot_index
        self._state = CardState.FETCHING

        def _worker(_sym=symbol, _src=src, _itv=itv, _slot=slot, _tok=token,
                    _inbox=inbox, _self=self) -> None:
            bars: list = []
            try:
                # Lazy import — DATA_SOURCES touches yfinance at import,
                # which we don't want in the chartstack module's import
                # graph (kept clean for unit tests without yfinance).
                from ...data import DATA_SOURCES
                fetcher = DATA_SOURCES.get(_src)
                if fetcher is not None:
                    cs = fetcher(_sym, _itv)
                    if cs:
                        bars = list(cs)
            except Exception:  # noqa: BLE001 - any fetch failure → empty bars
                bars = []
            payload = (_slot, _tok, _sym, bars)
            try:
                if threading.current_thread() is threading.main_thread():
                    # Test-shim path: dispatch synchronously so callers
                    # don't have to spin the worker_inbox drain.
                    panel = getattr(_self.owner_app, "_chartstack", None)
                    if panel is not None and hasattr(panel, "apply_card_stash"):
                        try:
                            panel.apply_card_stash(_slot, _tok, _sym, bars)
                        except Exception:  # noqa: BLE001
                            pass
                    else:
                        _inbox.put_nowait(("card_stash", payload))
                else:
                    _inbox.put_nowait(("card_stash", payload))
            except Exception:  # noqa: BLE001
                pass

        try:
            executor.submit(_worker)
        except Exception:  # noqa: BLE001
            self._state = CardState.ERROR

    def start_stream(
        self,
        registry: SubscriptionRegistry,
        *,
        is_intraday: Callable[[str], bool] | None = None,
    ) -> None:
        """Subscribe to live ticks via the dedupe ``registry``.

        Resolves ``(src, ticker, interval)`` on the calling Tk
        thread, refuses non-intraday intervals (no streaming for
        daily / weekly), and refuses sources missing from
        ``STREAM_SOURCES``. Stream events are stamped with the
        current ``token`` and routed through
        ``owner_app._stream_queue`` as
        ``(token, "card:<slot>", src, ticker, interval, kind, bar)``
        tuples — the same shape the main chart uses, distinguished
        only by the ``"card:"`` slot prefix the drain keys on.

        Calling ``start_stream`` while an existing subscription is
        active is a no-op when the resolved key matches; otherwise
        the prior stream is released first.
        """
        if self._binding is None or self.owner_app is None:
            return
        owner = self.owner_app
        try:
            src = owner.source_var.get()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return
        # ChartStack cards run on the daily timeframe (2026-05-16
        # simplification) — daily bars don't tick during a session
        # in a meaningful way, so streaming is unconditionally
        # disabled regardless of the upstream `is_intraday` gate.
        itv = "1d"
        symbol = self._binding.symbol
        if not symbol or not src or not itv:
            return
        # Intraday-only gate; caller may override (tests stub out
        # `is_intraday` to avoid pulling the full models module).
        if is_intraday is None:
            try:
                from ...constants import is_intraday as _is_intraday
                is_intraday = _is_intraday  # type: ignore[assignment]
            except Exception:  # noqa: BLE001
                is_intraday = lambda _i: True  # type: ignore[assignment]
        try:
            if not is_intraday(itv):
                return
        except Exception:  # noqa: BLE001
            return

        new_key = (src, symbol, itv)
        if self._stream_key == new_key and self._stream_release is not None:
            return  # already subscribed to the right thing
        # Tear down any stale subscription first.
        self.stop_stream()

        stream_queue = getattr(owner, "_stream_queue", None)
        if stream_queue is None:
            return

        token = self._token
        slot_id = f"card:{self.slot_index}"

        def _on_event(kind: str, bar: Any,
                      _q=stream_queue, _slot=slot_id, _src=src,
                      _tic=symbol, _itv=itv, _tok=token) -> None:
            try:
                _q.put_nowait((_tok, _slot, _src, _tic, _itv, kind, bar))
            except Exception:  # noqa: BLE001
                pass

        def _factory(src_: str, ticker_: str, interval_: str,
                     dispatch: Callable[[str, Any], None]
                     ) -> Optional[Callable[[], None]]:
            try:
                from ...streaming import STREAM_SOURCES
            except Exception:  # noqa: BLE001
                return None
            stream = STREAM_SOURCES.get(src_)
            if stream is None:
                return None
            try:
                return stream.subscribe(ticker_, interval_, dispatch)
            except Exception:  # noqa: BLE001
                return None

        try:
            release = registry.subscribe(
                src, symbol, itv, _on_event, upstream_factory=_factory)
        except Exception:  # noqa: BLE001
            release = None

        if release is None:
            return
        self._stream_release = release
        self._stream_key = new_key
        self._state = CardState.LIVE

    def mark_halted(self, halt_index: int) -> None:
        """Flag the card as halted at the given bar index.

        Used by M5 sandbox lockstep (and future live halt
        detection): when a halt is detected, the render layer
        switches to a grey treatment and overlays a vertical bar
        glyph at ``halt_index``. Transitions FSM to
        :attr:`CardState.HALTED`.
        """
        idx = int(halt_index)
        if idx < 0:
            idx = 0
        self._halt_index = idx
        self._state = CardState.HALTED

    def clear_halt(self) -> None:
        """Clear the halt flag and transition out of HALTED.

        If a stream was previously active, ``LIVE`` is the right
        post-halt state; otherwise fall back to ``READY``. Bumping
        the FSM only — does not restart any stream subscription.
        """
        self._halt_index = None
        if self._state == CardState.HALTED:
            if self._stream_release is not None:
                self._state = CardState.LIVE
            else:
                self._state = CardState.READY

    def stop_stream(self) -> None:
        """Release the active stream subscription (idempotent)."""
        release = self._stream_release
        self._stream_release = None
        self._stream_key = None
        if release is None:
            return
        try:
            release()
        except Exception:  # noqa: BLE001
            pass

    def mark_ready(self) -> None:
        """Transition to READY (called by the panel after a stash applies)."""
        # Don't clobber LIVE: an in-flight stream subscription wins
        # the FSM "above" READY.
        if self._state != CardState.LIVE:
            self._state = CardState.READY

    def mark_error(self) -> None:
        """Transition to ERROR (called when a fetch returned no bars)."""
        self._state = CardState.ERROR

    def stop(self) -> None:
        """Tear down all subscriptions, bump token, reset state to IDLE.

        Bumping the token drops any in-flight stash / stream event
        that lands post-stop, so the panel won't redraw a card that
        the user has explicitly halted.
        """
        self.stop_stream()
        self._token += 1
        self._state = CardState.IDLE
        self._halt_index = None


__all__ = ["CardController", "CardState", "SubscriptionRegistry"]
