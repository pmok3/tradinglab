"""Schwab WebSocket streaming source.

Implements :class:`tradinglab.streaming.base.StreamSource` against
Schwab's streamer API. One persistent WS connection per process, with
multiplexed per-symbol subscriptions on top.

Wire architecture
-----------------

::

    SchwabStreamSource
        ├── _Connection (singleton, lazily started)
        │     ├── ws thread (recv loop)
        │     └── clock thread (forces minute rollovers for quiet symbols)
        └── per-(ticker,interval) subscriptions
              ├── on_event callback (set by caller)
              └── MinuteBarBuilder (LEVELONE → 1-min bars)

LEVELONE drives in-progress bars; CHART_EQUITY arrives ~5–30s after
the minute closes and overwrites bars that already rolled (silent
correction — the next paint picks up the authoritative OHLCV).

Failure modes
-------------

* Missing credentials → ``subscribe`` is a no-op (debug log).
* Missing/expired refresh token → no-op + warning log telling the
  user to run ``schwab_login``.
* ``websocket-client`` not installed → no-op + warning.
* Non-1m intervals → no-op + debug (consistent with
  :class:`SyntheticStreamSource` rejecting daily+).
* WS disconnect → exponential backoff reconnect (1, 2, 4, 8, 16, 30s
  cap), subscriptions resumed automatically.

What is NOT in this module
--------------------------

* The OAuth dance (lives in ``data/schwab_auth.py``).
* Bar aggregation logic (lives in ``streaming/schwab_aggregator.py``,
  pure and unit-tested).
* The user-preference fetch that returns the symbol's actual streamer
  URL — it's HTTP, see ``_fetch_streamer_info``.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..data._http import MAX_RESPONSE_BYTES, credentialed_opener
from ..models import Candle
from .base import StreamCallback
from .schwab_aggregator import (
    MinuteBarBuilder,
    chart_equity_to_candle,
    decode_chart_equity_content,
    decode_levelone_content,
)

LOG = logging.getLogger(__name__)


# Reconnect backoff schedule (seconds). Capped at the last value.
_BACKOFF = (1, 2, 4, 8, 16, 30)


# ---------------------------------------------------------------------------
# User-preference helper (fetches per-user streamer URL + tokens)
# ---------------------------------------------------------------------------


USER_PREFERENCE_URL = "https://api.schwabapi.com/trader/v1/userPreference"


def fetch_streamer_info(  # pragma: no cover - network path
    access_token: str,
) -> dict[str, Any]:
    """Return the ``streamerInfo[0]`` dict from /userPreference.

    Schwab's streamer URL and customer/correl IDs are *per user*, so
    every connection has to fetch this once.
    """
    req = urllib.request.Request(USER_PREFERENCE_URL, headers={
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    })
    with credentialed_opener().open(req, timeout=15) as resp:
        payload = json.loads(resp.read(MAX_RESPONSE_BYTES).decode("utf-8"))
    info = payload.get("streamerInfo") or []
    if not info:
        raise RuntimeError("schwab: /userPreference returned no streamerInfo")
    return info[0]


# ---------------------------------------------------------------------------
# Login + subscription request builders (pure, testable)
# ---------------------------------------------------------------------------


def build_login_request(
    streamer_info: dict[str, Any], access_token: str, request_id: int = 0,
) -> dict[str, Any]:
    """Construct the LOGIN admin request for Schwab's streamer.

    Reference: https://developer.schwab.com/streamer-api → ADMIN.LOGIN
    """
    return {
        "service": "ADMIN",
        "command": "LOGIN",
        "requestid": str(request_id),
        "SchwabClientCustomerId": streamer_info.get("schwabClientCustomerId"),
        "SchwabClientCorrelId": streamer_info.get("schwabClientCorrelId"),
        "parameters": {
            "Authorization": access_token,
            "SchwabClientChannel": streamer_info.get("schwabClientChannel"),
            "SchwabClientFunctionId": streamer_info.get("schwabClientFunctionId"),
        },
    }


def build_subs_request(
    service: str, symbols: list[str], fields: list[str],
    streamer_info: dict[str, Any], request_id: int,
) -> dict[str, Any]:
    """Construct a SUBS / ADD request for a given service.

    Schwab uses ``SUBS`` to replace the entire subscription set and
    ``ADD`` to incrementally extend; we use ADD for additional
    symbols on an existing connection.
    """
    return {
        "service": service,
        "command": "SUBS" if request_id <= 1 else "ADD",
        "requestid": str(request_id),
        "SchwabClientCustomerId": streamer_info.get("schwabClientCustomerId"),
        "SchwabClientCorrelId": streamer_info.get("schwabClientCorrelId"),
        "parameters": {
            "keys": ",".join(symbols),
            "fields": ",".join(fields),
        },
    }


# Field IDs we want from each service. Keep these as strings — that's
# what Schwab's wire protocol expects.
LEVELONE_FIELD_IDS = ["0", "1", "2", "3", "4", "5", "8", "10", "11", "12", "35"]
CHART_EQUITY_FIELD_IDS = ["0", "1", "2", "3", "4", "5", "6", "7"]


# ---------------------------------------------------------------------------
# Subscription bookkeeping
# ---------------------------------------------------------------------------


class _Subscription:
    """One (symbol → callback + bar builder) pairing.

    Multiple subscribers for the same symbol get their own
    ``_Subscription`` instances; the connection fans the same wire
    message out to each (each maintains its own builder so a late
    subscriber gets a fresh in-progress bar with its own seed).
    """

    __slots__ = ("symbol", "interval", "callback", "builder", "alive")

    def __init__(
        self, symbol: str, interval: str, callback: StreamCallback,
        seed_close: float,
    ) -> None:
        self.symbol = symbol
        self.interval = interval
        self.callback = callback
        self.builder = MinuteBarBuilder(seed_close=seed_close)
        self.alive = True


# ---------------------------------------------------------------------------
# Source implementation
# ---------------------------------------------------------------------------


class SchwabStreamSource:
    """Schwab streaming source. One process-wide singleton expected.

    The source is dormant until the first :meth:`subscribe`; the WS
    connection is created on demand. Closing the last subscription
    leaves the connection up for ~30s before tearing it down (avoids
    thrashing when the user flips between symbols).
    """

    def __init__(
        self, *, seed_lookup: Callable[[str, str], float | None] | None = None,
    ) -> None:
        # Optional injectable lookup that returns the most-recent
        # close from REST history for seeding new subscriptions. The
        # production wiring sets this to consult the same yfinance /
        # Schwab REST history the chart already loaded; tests inject
        # a stub. Returning ``None`` means "use a placeholder of 0.0".
        self._seed_lookup = seed_lookup

        self._lock = threading.RLock()
        self._subs: dict[str, list[_Subscription]] = {}
        self._symbols_subscribed: set[str] = set()
        self._connection: _Connection | None = None
        # Quote axis (streaming/schwab_quotes.py). Kept here rather than
        # in its own source because Schwab allows exactly ONE streamer
        # connection per user — a second socket is not a second feed, it
        # evicts the first. Quote symbols join ``_symbols_subscribed`` so
        # the reconnect path re-images them with everything else, but
        # they get no ``_Subscription`` and therefore no MinuteBarBuilder.
        self._quote_symbols: set[str] = set()
        self._quote_callbacks: list[Any] = []

    def _refresh_symbols_locked(self) -> set[str]:
        """Recompute the wire subscription set. Caller holds ``_lock``."""
        self._symbols_subscribed = set(self._subs) | set(self._quote_symbols)
        return self._symbols_subscribed

    # --- quote axis ---

    def subscribe_quotes(self, symbols, on_quote):
        """Subscribe the quote fan-out. See ``streaming/quotes.py``."""
        from .schwab_quotes import SchwabQuoteSubscription

        sub = SchwabQuoteSubscription(self, on_quote)
        with self._lock:
            self._quote_callbacks.append(sub)
        sub.set_symbols(symbols)
        return sub

    def _open_connection(self) -> _Connection | None:
        """Open the connection if credentials allow.

        Caller must **not** hold ``_lock``: ``get_access_token`` can make
        a synchronous HTTPS round trip to Schwab's ``/oauth/token``, and
        this is reachable from the Tk thread. Holding the lock across it
        would freeze the UI *and* stall the socket thread's dispatch for
        the duration — which is why ``subscribe()`` has always fetched
        the token before taking the lock.
        """
        with self._lock:
            if self._connection is not None:
                return self._connection
        try:
            import websocket  # type: ignore  # noqa: F401
        except ImportError:
            LOG.warning(
                "schwab-stream: websocket-client is not installed. "
                "Install with `pip install tradinglab[schwab]`.")
            return None
        from ..data.credentials import get_credentials
        from ..data.schwab_auth import get_access_token
        creds = get_credentials().schwab
        if not creds.is_configured():
            LOG.debug("schwab-stream: credentials not configured")
            return None
        token = get_access_token(creds)
        if not token:
            LOG.warning("schwab-stream: no valid access token.")
            return None
        with self._lock:
            # Another thread may have won the race while we were on the
            # network; one connection per user means we must not replace
            # a live one.
            if self._connection is None:
                self._connection = _Connection(self, creds, token)
                self._connection.start()
            return self._connection

    def _apply_quote_symbols(self) -> None:
        """Reconcile the union of every quote subscriber's symbol set.

        Reads the union **under the lock** rather than trusting a
        caller-supplied snapshot: a subscriber assigns its own
        ``symbols`` before calling in, so a `close()` interleaving there
        could otherwise resurrect a dead subscriber's symbols
        permanently — which would also keep the idle check false forever
        and leak the connection.
        """
        with self._lock:
            union: set[str] = set()
            for cb in self._quote_callbacks:
                union |= getattr(cb, "symbols", set())
            previous_wire = set(self._symbols_subscribed)
            previous_quote = set(self._quote_symbols)
            self._quote_symbols = union
            current = self._refresh_symbols_locked()
            # A symbol newly entering the QUOTE set needs an ADD even if
            # it is already on the wire for the bar axis: Schwab sends a
            # full image only in response to SUBS/ADD and change-only
            # deltas after that, so without a re-image the book's first
            # entry for it would be a delta and `prev_close` would never
            # arrive. A duplicate ADD is harmless — it just re-images.
            to_add = sorted((current - previous_wire) | (union - previous_quote))
            to_remove = sorted(previous_wire - current)
            has_conn = self._connection is not None
        if not current and not has_conn:
            # Nothing wanted and nothing open — notably the teardown
            # path, which must never dial out just to hang up.
            return
        conn = self._connection if has_conn else self._open_connection()
        if conn is None:
            return
        # Batched: one message per service, not one per symbol. At S&P
        # scale the per-symbol loop was 500 blocking socket writes on
        # open and 1000 on close, all on the Tk thread.
        if to_add:
            conn.add_symbols(to_add)
        if to_remove:
            conn.remove_symbols(to_remove)

    def _drop_quote_subscription(self, sub: Any) -> None:
        with self._lock:
            self._quote_callbacks = [
                c for c in self._quote_callbacks if c is not sub
            ]
            still_wanted = any(
                getattr(c, "symbols", set()) for c in self._quote_callbacks
            )
            idle = not self._subs and not still_wanted
            conn = self._connection
            if idle:
                # Tear down wholesale rather than UNSUBS-ing every symbol
                # first: the connection is about to close, so the
                # unsubscribe traffic would be pure waste.
                self._quote_symbols = set()
                self._refresh_symbols_locked()
                self._connection = None
        if idle:
            if conn is not None:
                conn.shutdown()
            return
        self._apply_quote_symbols()

    def _dispatch_quote(self, symbol: str, decoded: dict[str, Any]) -> None:
        """Fan one LEVELONE update out to the quote subscribers."""
        from .schwab_quotes import quote_from_levelone

        with self._lock:
            subs = list(self._quote_callbacks)
        if not subs:
            return
        quote = quote_from_levelone(symbol, decoded)
        for sub in subs:
            try:
                sub.deliver(quote)
            except Exception:
                LOG.exception("schwab-stream: quote subscriber raised")

    def subscribe(
        self, ticker: str, interval: str, on_event: StreamCallback,
    ) -> Callable[[], None]:
        if interval != "1m":
            LOG.debug(
                "schwab-stream: only 1m interval is supported today, "
                "got %r — returning no-op", interval)
            return lambda: None

        # Late-imported so a missing ``websocket-client`` install
        # doesn't crash module import.
        try:
            import websocket  # type: ignore  # noqa: F401
        except ImportError:
            LOG.warning(
                "schwab-stream: websocket-client is not installed. "
                "Install with `pip install tradinglab[schwab]` to "
                "enable Schwab streaming.")
            return lambda: None

        from ..data.credentials import get_credentials
        from ..data.schwab_auth import get_access_token
        creds = get_credentials().schwab
        if not creds.is_configured():
            LOG.debug("schwab-stream: credentials not configured")
            return lambda: None
        token = get_access_token(creds)
        if not token:
            LOG.warning(
                "schwab-stream: no valid access token. Run "
                "`python -m tradinglab.data.schwab_login` first.")
            return lambda: None

        seed = 0.0
        if self._seed_lookup is not None:
            seed_val = self._seed_lookup(ticker, interval)
            if seed_val is not None:
                seed = float(seed_val)

        sub = _Subscription(ticker.upper(), interval, on_event, seed)
        with self._lock:
            self._subs.setdefault(sub.symbol, []).append(sub)
            new_symbol = sub.symbol not in self._symbols_subscribed
            self._refresh_symbols_locked()
            if self._connection is None:
                self._connection = _Connection(self, creds, token)
                self._connection.start()
            elif new_symbol:
                self._connection.add_symbol(sub.symbol)

        # Open the in-progress bar locally so the consumer sees an
        # immediate "rollover" — symmetric with the synthetic source.
        try:
            event = sub.builder.open_initial_bar(datetime.now())
            on_event(*event)
        except Exception:
            LOG.exception("schwab-stream: initial bar emit failed")

        def _unsubscribe() -> None:
            with self._lock:
                sub.alive = False
                lst = self._subs.get(sub.symbol)
                if lst:
                    lst[:] = [s for s in lst if s.alive]
                    if not lst:
                        self._subs.pop(sub.symbol, None)
                        self._refresh_symbols_locked()
                        # A symbol the QUOTE axis still wants must stay
                        # on the wire — dropping the last bar subscriber
                        # is not the same as nobody caring about it.
                        if (
                            self._connection is not None
                            and sub.symbol not in self._quote_symbols
                        ):
                            self._connection.remove_symbol(sub.symbol)
                if not self._subs and not self._quote_symbols and self._connection:
                    self._connection.shutdown()
                    self._connection = None
        return _unsubscribe

    # --- internal: dispatchers called from the connection thread ---

    def _dispatch_levelone(self, symbol: str, decoded: dict[str, Any]) -> None:
        # The quote axis first: it is a plain dict write per subscriber,
        # whereas the bar path runs an aggregator per subscription. A
        # slow or raising bar subscriber must not delay the quote
        # snapshot the UI samples.
        self._dispatch_quote(symbol, decoded)
        now = datetime.now()
        with self._lock:
            subs = list(self._subs.get(symbol.upper(), ()))
        for sub in subs:
            if not sub.alive:
                continue
            try:
                events = sub.builder.apply_levelone(decoded, now=now)
            except Exception:
                LOG.exception("schwab-stream: aggregator error for %s", symbol)
                continue
            for kind, candle in events:
                _safe_invoke(sub.callback, kind, candle)

    def _dispatch_chart_equity(self, symbol: str, decoded: dict[str, Any]) -> None:
        candle = chart_equity_to_candle(decoded)
        if candle is None:
            return
        with self._lock:
            subs = list(self._subs.get(symbol.upper(), ()))
        # CHART_EQUITY corrects an already-closed bar. We surface it
        # as a "tick" event carrying the authoritative Candle for the
        # closed minute — the BarsBuffer matches by timestamp and
        # overwrites the prior LEVELONE-synthesized bar.
        #
        # Per design: there's no separate "correction" event kind;
        # consumers handle this via existing tick-replace-by-timestamp.
        for sub in subs:
            if not sub.alive:
                continue
            _safe_invoke(sub.callback, "tick", candle)


def _safe_invoke(cb: StreamCallback, kind: str, candle: Candle) -> None:
    try:
        cb(kind, candle)
    except Exception:
        LOG.exception("schwab-stream: subscriber callback raised")


# ---------------------------------------------------------------------------
# WebSocket connection thread
# ---------------------------------------------------------------------------


class _Connection:
    """The persistent WS connection. One per :class:`SchwabStreamSource`.

    Owns two threads:
      * ``_ws_thread`` — recv loop.
      * ``_clock_thread`` — wakes once a second to give every active
        builder a chance to roll a quiet minute over even when no
        LEVELONE update arrived.

    Reconnect: the recv loop catches WS errors, closes the socket,
    sleeps the next backoff value, and re-runs the connect+login+subs
    sequence. ``_subscribed_request_id`` ensures the second call uses
    SUBS (not ADD) on a fresh connection.
    """

    def __init__(self, source: SchwabStreamSource, creds, access_token: str) -> None:
        self._source = source
        self._creds = creds
        self._access_token = access_token
        self._streamer_info: dict[str, Any] | None = None
        self._ws = None
        self._stop = threading.Event()
        self._ws_thread: threading.Thread | None = None
        self._clock_thread: threading.Thread | None = None
        self._request_id = 0
        self._connected_subs_sent = False
        self._lock = threading.Lock()

    def start(self) -> None:
        self._ws_thread = threading.Thread(
            target=self._run, name="schwab-ws", daemon=True)
        self._ws_thread.start()
        self._clock_thread = threading.Thread(
            target=self._clock_run, name="schwab-clock", daemon=True)
        self._clock_thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass

    def add_symbol(self, symbol: str) -> None:
        """Send an ADD for one new symbol on the existing connection."""
        self.add_symbols([symbol])

    def add_symbols(self, symbols: list[str]) -> None:
        """Send one ADD covering ``symbols``.

        Batched because the quote axis reconciles whole universes: one
        message per symbol was 500 blocking socket writes on open at S&P
        scale, every one of them on the Tk thread.
        """
        if not symbols:
            return
        if not self._connected_subs_sent or self._streamer_info is None:
            return  # initial subs hasn't run yet; it'll include these
        self._send_subs(list(symbols))

    def remove_symbol(self, symbol: str) -> None:
        """Send an UNSUBS for one symbol on the existing connection."""
        self.remove_symbols([symbol])

    def remove_symbols(self, symbols: list[str]) -> None:
        """Send one UNSUBS per service covering ``symbols``."""
        if not symbols:
            return
        if not self._connected_subs_sent or self._streamer_info is None:
            return
        info = self._streamer_info
        self._request_id += 1
        keys = ",".join(symbols)
        for service in ("LEVELONE_EQUITIES", "CHART_EQUITY"):
            unsub = {
                "service": service,
                "command": "UNSUBS",
                "requestid": str(self._request_id),
                "SchwabClientCustomerId": info.get("schwabClientCustomerId"),
                "SchwabClientCorrelId": info.get("schwabClientCorrelId"),
                "parameters": {"keys": keys},
            }
            self._send_json({"requests": [unsub]})

    # --- internal ---

    def _send_json(self, obj: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            ws.send(json.dumps(obj))
        except Exception:
            LOG.exception("schwab-stream: send failed")

    def _send_subs(self, symbols: list[str]) -> None:
        info = self._streamer_info
        assert info is not None
        self._request_id += 1
        l1_req = build_subs_request(
            "LEVELONE_EQUITIES", symbols, LEVELONE_FIELD_IDS,
            info, self._request_id,
        )
        self._request_id += 1
        ce_req = build_subs_request(
            "CHART_EQUITY", symbols, CHART_EQUITY_FIELD_IDS,
            info, self._request_id,
        )
        self._send_json({"requests": [l1_req, ce_req]})

    def _run(self) -> None:  # pragma: no cover - integration path
        backoff_idx = 0
        while not self._stop.is_set():
            try:
                self._connect_and_serve()
                # Clean exit (we asked it to stop) — leave loop.
                if self._stop.is_set():
                    break
                # Server-initiated close; reconnect after backoff.
                backoff_idx = min(backoff_idx + 1, len(_BACKOFF) - 1)
            except Exception as exc:
                LOG.warning("schwab-stream: connection error: %s", exc)
                backoff_idx = min(backoff_idx + 1, len(_BACKOFF) - 1)
            if self._stop.is_set():
                break
            wait = _BACKOFF[backoff_idx]
            LOG.info("schwab-stream: reconnect in %ds", wait)
            self._stop.wait(wait)

    def _connect_and_serve(self) -> None:  # pragma: no cover - integration path
        import websocket  # type: ignore

        # Refresh access token + streamer info every reconnect — both
        # may have rotated since the last connect.
        from ..data.schwab_auth import get_access_token
        token = get_access_token(self._creds) or self._access_token
        self._access_token = token
        self._streamer_info = fetch_streamer_info(token)
        ws_url = self._streamer_info.get("streamerSocketUrl")
        if not ws_url:
            raise RuntimeError("schwab-stream: no streamerSocketUrl in user preferences")

        self._ws = websocket.create_connection(ws_url, timeout=15)
        self._connected_subs_sent = False
        # LOGIN
        self._request_id = 0
        login = build_login_request(self._streamer_info, token, self._request_id)
        self._send_json({"requests": [login]})

        # Wait for login ACK before SUBS. Any non-OK code aborts.
        login_ack = self._read_one()
        if not _is_login_ok(login_ack):
            raise RuntimeError(f"schwab-stream: login failed: {login_ack!r}")

        # Initial SUBS for every currently-tracked symbol.
        with self._source._lock:
            symbols = sorted(self._source._symbols_subscribed)
        if symbols:
            self._request_id = 1  # so build_subs_request emits SUBS not ADD
            self._send_subs(symbols)
        self._connected_subs_sent = True

        # Recv loop.
        while not self._stop.is_set():
            msg = self._read_one()
            if msg is None:
                # Clean close from server side.
                break
            self._handle_message(msg)

    def _read_one(self) -> dict[str, Any] | None:  # pragma: no cover - network
        ws = self._ws
        if ws is None:
            return None
        raw = ws.recv()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            LOG.warning("schwab-stream: malformed message: %r", raw[:200])
            return {}

    def _handle_message(self, msg: dict[str, Any]) -> None:
        # The Schwab streamer envelope is one of:
        #   {"data": [{"service": "...", "content": [{...}, ...]}]}
        #   {"response": [{...ack...}]}
        #   {"notify": [{"heartbeat": ...}]}
        for entry in msg.get("data") or ():
            service = entry.get("service")
            content = entry.get("content") or []
            if service == "LEVELONE_EQUITIES":
                for c in content:
                    decoded = decode_levelone_content(c)
                    sym = decoded.get("symbol")
                    if sym:
                        self._source._dispatch_levelone(str(sym), decoded)
            elif service == "CHART_EQUITY":
                for c in content:
                    decoded = decode_chart_equity_content(c)
                    sym = decoded.get("symbol")
                    if sym:
                        self._source._dispatch_chart_equity(str(sym), decoded)
        # response / notify: nothing to do at the bar layer.

    def _clock_run(self) -> None:  # pragma: no cover - timing
        """Once a second, give every builder a chance to roll a quiet minute."""
        while not self._stop.wait(1.0):
            now = datetime.now()
            with self._source._lock:
                # Snapshot to avoid holding the lock across callbacks.
                items = [(sym, list(subs)) for sym, subs in self._source._subs.items()]
            for _sym, subs in items:
                for sub in subs:
                    if not sub.alive:
                        continue
                    try:
                        events = sub.builder.maybe_rollover(now)
                    except Exception:
                        LOG.exception("schwab-stream: clock rollover error")
                        continue
                    for kind, candle in events:
                        _safe_invoke(sub.callback, kind, candle)


def _is_login_ok(msg: dict[str, Any] | None) -> bool:
    if not msg:
        return False
    for r in msg.get("response") or ():
        if r.get("service") == "ADMIN" and r.get("command") == "LOGIN":
            content = r.get("content") or {}
            return content.get("code") == 0
    return False
