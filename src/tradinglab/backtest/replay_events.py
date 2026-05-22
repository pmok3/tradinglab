"""Events bridge mixin for :class:`SandboxController`.

Owns the controller's interaction with the
:mod:`tradinglab.events` subsystem:

* Per-symbol :class:`EventBundle` installation and idempotent
  re-install (:meth:`set_event_bundle`).
* Token-gated, Tk-thread-safe background prefetch
  (:meth:`prefetch_events_for`).
* Translation of bundle :class:`DividendRecord`s into engine
  :class:`CorporateAction`s
  (:meth:`_register_corporate_actions_from_bundle`).
* Clock-gated read accessors used by the GUI render path
  (:meth:`events_visible_for`) and the journal
  (:meth:`_compute_event_proximity`).

The engine itself never imports from :mod:`events`; this mixin is
the explicit boundary.

Mixin rules:
* No ``__init__``. Relies on attributes that
  :class:`SandboxController.__init__` initialises:
  ``_raw_full_events``, ``_events_fetch_token``, ``engine``,
  ``app``, ``active``, ``blind``, plus the ``clock_ts()`` method.
* No cooperative ``super()`` — plain MRO.
* No name collisions with other mixins or :class:`SandboxController`.
"""

from __future__ import annotations

from typing import Any

import numpy as _np


class EventsControllerMixin:
    """Earnings / dividends / corporate-actions bridge for SandboxController."""

    # Re-declared on SandboxController. Listed here so static analysers know
    # the mixin expects them on ``self``.
    _raw_full_events: dict[str, Any]
    _events_fetch_token: int
    engine: Any
    app: Any
    active: bool
    blind: bool

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------
    def set_event_bundle(self, symbol: str, bundle: Any) -> None:
        """Install ``bundle`` (an ``EventBundle`` from
        :mod:`tradinglab.events`) for ``symbol``.

        Idempotent on identity. The caller is the app's events-prefetch
        path, which fetches once per ticker per session. Subsequent
        re-installs simply overwrite — the engine has already consumed
        the corporate actions at :meth:`start_session` /
        :meth:`register_ticker` time, so a later refresh affects only
        the gated display.
        """
        self._raw_full_events[str(symbol)] = bundle
        if self.engine is not None:
            try:
                self._register_corporate_actions_from_bundle(symbol, bundle)
            except Exception:  # noqa: BLE001
                # Engine refuses different-content re-register — that's
                # fine, the first one wins for the rest of the session.
                pass

    def prefetch_events_for(self, symbol: str) -> None:
        """Schedule a background fetch of ``symbol``'s event bundle.

        Submits to ``app._fetch_executor`` when available so the Tk
        thread stays responsive. Token-gated by
        :attr:`_events_fetch_token` so a session restart or
        :meth:`cycle_to_next` discards in-flight results from the
        previous session. Falls back to a sync inline fetch when no
        executor / no ``after`` is available (smoke tests, headless
        callers).

        Result marshalling goes through ``app._await_future_on_tk``
        (a Tk-thread poll on the future) — never
        ``fut.add_done_callback`` + ``app.after`` from a worker
        thread. Per ``app.spec.md`` Recent history → "Worker-inbox
        queue", calling ``self.after`` from a non-main thread blocks
        ``tk.createcommand`` indefinitely on this Python/Tk build,
        which would saturate ``_fetch_executor`` after a handful of
        prefetches.
        """
        try:
            from ..defaults import get as _get_default
            from ..events import EVENT_SOURCES  # type: ignore
        except ImportError:
            return
        source_name = str(_get_default("events_source") or "yfinance")
        fetcher = EVENT_SOURCES.get(source_name)
        if fetcher is None:
            fetcher = EVENT_SOURCES.get("synthetic")
        if fetcher is None:
            return

        executor = getattr(self.app, "_fetch_executor", None)
        await_helper = getattr(self.app, "_await_future_on_tk", None)
        if executor is None or await_helper is None:
            try:
                bundle = fetcher(symbol)
            except Exception:  # noqa: BLE001
                bundle = None
            if bundle is not None:
                self.set_event_bundle(symbol, bundle)
            return

        self._events_fetch_token += 1
        token = self._events_fetch_token

        def _work():
            try:
                return fetcher(symbol)
            except Exception:  # noqa: BLE001
                return None

        def _on_done(bundle) -> None:
            if token != self._events_fetch_token:
                return
            if not self.active:
                return
            if bundle is None:
                return
            self.set_event_bundle(symbol, bundle)

        try:
            fut = executor.submit(_work)
        except (RuntimeError, AttributeError):
            return
        try:
            await_helper(fut, _on_done)
        except Exception:  # noqa: BLE001
            pass

    def _register_corporate_actions_from_bundle(
        self,
        symbol: str,
        bundle: Any,
    ) -> int:
        """Translate the bundle's :class:`DividendRecord` list into
        :class:`CorporateAction`s and register them with the engine.

        Returns the number of actions queued. Skips events whose
        ``ex_ts`` falls outside the engine's master timeline (those
        will never fire under the current clock and would only
        accumulate as inert queue entries).

        The engine itself never imports from :mod:`events`; this
        method is the explicit boundary.
        """
        if self.engine is None:
            return 0
        try:
            from .actions import CorporateAction
        except ImportError:
            return 0
        if not bundle or not getattr(bundle, "dividends", None):
            return 0

        # Map DividendRecord.kind -> CorporateAction.kind. The events
        # taxonomy ("cash" / "special" / "spinoff" / "stock_split")
        # is broader than the engine's; collapse to the four engine
        # kinds.
        kind_map = {
            "cash": "cash_dividend",
            "special": "special_dividend",
            "spinoff": "spinoff_cash",
            "stock_split": "stock_split",
        }
        # Timeline bounds (engine ts is epoch seconds; bundle ex_ts is
        # epoch ms — convert).
        timeline = self.engine.clock.timeline
        if len(timeline) == 0:
            return 0
        lo_ms = int(timeline[0]) * 1000
        hi_ms = int(timeline[-1]) * 1000

        actions: list[Any] = []
        for d in bundle.dividends:
            ex_ts_ms = int(getattr(d, "ex_ts", 0))
            if ex_ts_ms < lo_ms or ex_ts_ms > hi_ms:
                continue
            engine_kind = kind_map.get(str(getattr(d, "kind", "cash")),
                                       "cash_dividend")
            # Match the action ts to the *exact* timeline second the
            # engine will tick through. Engine ts is seconds — convert
            # back from ms and floor to the nearest timeline entry so
            # the corporate-action phase fires on the ex-date's bar.
            ex_ts_s = ex_ts_ms // 1000
            # Find first timeline entry >= ex_ts_s.
            idx = int(_np.searchsorted(timeline, ex_ts_s, side="left"))
            if idx >= len(timeline):
                continue
            action_ts = int(timeline[idx])
            actions.append(CorporateAction(
                ts=action_ts,
                kind=engine_kind,
                amount=float(getattr(d, "amount", 0.0) or 0.0),
                ratio_num=int(getattr(d, "ratio_num", 1) or 1),
                ratio_den=int(getattr(d, "ratio_den", 1) or 1),
                source_ref=str(getattr(d, "source", "") or ""),
            ))
        if not actions:
            return 0
        return self.engine.register_corporate_actions(symbol, actions)

    def events_visible_for(self, symbol: str) -> Any | None:
        """Return the gated :class:`EventsView` for ``symbol`` at the
        current clock.

        Returns ``None`` if no bundle has been installed for the
        symbol (events-prefetch still in flight, network failed, or
        the symbol was registered but events were never fetched).

        Forwards to :func:`tradinglab.events.gating.events_visible_for`
        with the current clock timestamp + the session's blind flag.
        Import is deferred so the headless backtest contract is
        preserved (engine never depends on events; the controller is
        the explicit bridge).
        """
        bundle = self._raw_full_events.get(str(symbol))
        if bundle is None:
            return None
        ts = self.clock_ts()
        if ts is None:
            return None
        try:
            from ..events.gating import events_visible_for as _gate
        except ImportError:
            return None
        # Engine clock is epoch seconds; events module uses ms.
        return _gate(bundle, int(ts) * 1000, blind=bool(self.blind))

    def _compute_event_proximity(
        self,
        symbol: str,
        ts: int,
    ) -> dict[str, Any]:
        """Snapshot the symbol's event-proximity context at ``ts`` for
        a :class:`PreTradeEntry`.

        Returns a dict with the six fields the journal record carries.
        Missing-data fallback is all-zero / empty-string — the journal
        contract is "0/'' means unknown", never raises. Forward fields
        are zeroed in blind mode.
        """
        out: dict[str, Any] = {
            "next_earnings_ts": 0,
            "last_earnings_ts": 0,
            "last_dividend_ts": 0,
            "last_split_ts": 0,
            "earnings_proximity_tag": "",
            "dividend_proximity_tag": "",
        }
        bundle = self._raw_full_events.get(str(symbol))
        if bundle is None:
            return out
        try:
            from ..defaults import TUNABLES
            from ..events.gating import events_visible_for as _gate
        except ImportError:
            return out
        # Bundle records use UTC ms-since-epoch ints. ts is master-clock
        # UTC seconds — convert before comparing.
        ts_ms = int(ts) * 1000
        view = _gate(bundle, ts_ms, blind=bool(self.blind))
        if view is None:
            return out

        # Look up the window (days) from defaults; fall back to 10.
        window_days = 10
        try:
            for t in TUNABLES:
                if t.name == "earnings_window_days":
                    window_days = int(t.default)
                    break
        except Exception:  # noqa: BLE001
            pass
        window_ms = int(window_days) * 86_400 * 1000

        past_e = list(getattr(view, "past_earnings", []) or [])
        fwd_e = list(getattr(view, "forward_earnings", []) or [])
        past_d = list(getattr(view, "past_dividends", []) or [])

        if past_e:
            most_recent = max(int(getattr(r, "ts", 0)) for r in past_e)
            out["last_earnings_ts"] = most_recent
            if 0 < (ts_ms - most_recent) <= window_ms:
                out["earnings_proximity_tag"] = "earnings_post_print"
        if fwd_e:
            nearest = min(int(getattr(r, "ts", 0)) for r in fwd_e)
            out["next_earnings_ts"] = nearest
            if 0 < (nearest - ts_ms) <= window_ms and \
               not out["earnings_proximity_tag"]:
                out["earnings_proximity_tag"] = "earnings_pre_print"

        if past_d:
            cash_divs = [r for r in past_d
                         if str(getattr(r, "kind", "cash")) != "stock_split"]
            splits = [r for r in past_d
                      if str(getattr(r, "kind", "")) == "stock_split"]
            if cash_divs:
                latest_div = max(int(getattr(r, "ex_ts", 0)) for r in cash_divs)
                out["last_dividend_ts"] = latest_div
                # "ex_div_day" if ts is on the same UTC day as the
                # latest cash dividend; "post_special_div" if any
                # special dividend hit within window_ms.
                day_ms = 86_400 * 1000
                if 0 <= (ts_ms - latest_div) < day_ms:
                    out["dividend_proximity_tag"] = "ex_div_day"
                else:
                    specials = [int(getattr(r, "ex_ts", 0)) for r in cash_divs
                                if str(getattr(r, "kind", "")) == "special"]
                    if specials:
                        latest_special = max(specials)
                        if 0 < (ts_ms - latest_special) <= window_ms:
                            out["dividend_proximity_tag"] = "post_special_div"
            if splits:
                out["last_split_ts"] = max(int(getattr(r, "ex_ts", 0))
                                           for r in splits)
        return out


__all__ = ["EventsControllerMixin"]
