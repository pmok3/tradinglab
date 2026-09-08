"""Single-owner Schwab socket worker and per-service ACK reconciliation.

Only this worker performs I/O. Callers publish desired subscriptions under
the source lock; every reconnect obtains a fresh token and fresh service
state. Wire identifiers and vendor error text never enter status or logs.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..data._http import MAX_RESPONSE_BYTES, credentialed_opener
from .base import StreamState
from .schwab_aggregator import decode_chart_equity_content, decode_levelone_content

if TYPE_CHECKING:
    from .schwab import SchwabStreamSource

LOG = logging.getLogger(__name__)
_BACKOFF = (1, 2, 4, 8, 16, 30)
_ACK_TIMEOUT = 15.0
_STALE_TIMEOUT = 30.0
# Vendor ownership, not a subscription-state lock. Only workers acquire it;
# keep it through socket cleanup when a registry replaces a source instance.
_STREAMER_OWNER = threading.Lock()
USER_PREFERENCE_URL = "https://api.schwabapi.com/trader/v1/userPreference"
LEVELONE_FIELD_IDS = ["0", "1", "2", "3", "4", "5", "8", "10", "11", "12", "35"]
CHART_EQUITY_FIELD_IDS = ["0", "1", "2", "3", "4", "5", "6", "7", "8"]
_FIELDS = {"LEVELONE_EQUITIES": LEVELONE_FIELD_IDS, "CHART_EQUITY": CHART_EQUITY_FIELD_IDS}


def fetch_streamer_info(access_token: str) -> dict[str, Any]:
    req = urllib.request.Request(USER_PREFERENCE_URL, headers={
        "Authorization": f"Bearer {access_token}", "Accept": "application/json",
    })
    with credentialed_opener().open(req, timeout=15) as resp:
        payload = json.loads(resp.read(MAX_RESPONSE_BYTES).decode("utf-8"))
    info = payload.get("streamerInfo") if isinstance(payload, dict) else None
    required = ("streamerSocketUrl", "schwabClientCustomerId", "schwabClientCorrelId",
                "schwabClientChannel", "schwabClientFunctionId")
    if not isinstance(info, list) or not info or not isinstance(info[0], dict):
        raise _Failure(StreamState.ERROR, "Streamer preferences unavailable")
    if any(not isinstance(info[0].get(k), str) or not info[0][k] for k in required):
        raise _Failure(StreamState.ERROR, "Streamer preferences incomplete")
    if not info[0]["streamerSocketUrl"].startswith("wss://"):
        raise _Failure(StreamState.ERROR, "Secure streamer URL required")
    return info[0]


def build_login_request(info: dict[str, Any], access_token: str, request_id: int = 0) -> dict[str, Any]:
    req = build_subs_request("ADMIN", [], [], info, request_id, command="LOGIN")
    req["parameters"] = {
        "Authorization": access_token,
        "SchwabClientChannel": info.get("schwabClientChannel"),
        "SchwabClientFunctionId": info.get("schwabClientFunctionId"),
    }
    return req


def build_subs_request(
    service: str, symbols: list[str], fields: list[str], streamer_info: dict[str, Any],
    request_id: int, *, command: str = "SUBS",
) -> dict[str, Any]:
    """Command is explicit and independent of the correlation ID."""
    params = {"keys": ",".join(symbols)}
    if command != "UNSUBS":
        params["fields"] = ",".join(fields)
    return {
        "service": service, "command": command, "requestid": str(request_id),
        "SchwabClientCustomerId": streamer_info.get("schwabClientCustomerId"),
        "SchwabClientCorrelId": streamer_info.get("schwabClientCorrelId"),
        "parameters": params,
    }


def _is_login_ok(msg: dict[str, Any] | None) -> bool:
    return bool(msg and any(
        r.get("service") == "ADMIN" and r.get("command") == "LOGIN"
        and r.get("content", {}).get("code") == 0 for r in msg.get("response", ())
    ))


def _records(container: dict[str, Any], key: str) -> list[dict[str, Any]]:
    records = container.get(key, [])
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise _Failure(StreamState.ERROR, "Invalid streaming message structure")
    return records


def _content_code(container: dict[str, Any]) -> Any:
    content = container.get("content", {})
    if not isinstance(content, dict):
        raise _Failure(StreamState.ERROR, "Invalid streaming response content")
    return content.get("code")


def _access_token() -> str | None:
    from ..data.credentials import get_credentials
    from ..data.schwab_auth import get_access_token

    return get_access_token(get_credentials().schwab)


class _Socket:
    """Translate optional websocket-client exceptions into stdlib I/O errors."""

    def __init__(self, url: str) -> None:
        import websocket

        self._errors = websocket.WebSocketException
        self._timeout = websocket.WebSocketTimeoutException
        try:
            self._ws = websocket.create_connection(url, timeout=15)
        except self._errors:
            raise OSError("WebSocket connection failed") from None
        try:
            self._ws.settimeout(0.25)
        except (OSError, self._errors):
            self.close()
            raise OSError("WebSocket configuration failed") from None

    def send(self, raw: str) -> None:
        try:
            self._ws.send(raw)
        except self._errors:
            raise OSError("WebSocket send failed") from None

    def recv(self) -> str:
        try:
            return self._ws.recv()
        except self._timeout:
            raise TimeoutError from None
        except self._errors:
            raise OSError("WebSocket receive failed") from None

    def close(self) -> None:
        try:
            self._ws.close(timeout=0)
        except self._errors:
            raise OSError("WebSocket close failed") from None


class _Failure(Exception):
    def __init__(self, state: StreamState, message: str) -> None:
        super().__init__(message)
        self.state = state


@dataclass
class _Pending:
    service: str
    command: str
    symbols: set[str]
    images: dict[str, int]
    sent_at: float


class _Connection:
    def __init__(self, source: SchwabStreamSource) -> None:
        self._source = source
        self._stop = threading.Event()
        self._ws_thread: threading.Thread | None = None
        self._ws: _Socket | None = None
        self._info: dict[str, Any] = {}
        self._request_id = 0
        self._pending: dict[str, _Pending] = {}
        self._wire: dict[str, set[str]] = {s: set() for s in _FIELDS}
        self._started: set[str] = set()
        self._imaged: dict[str, int] = {}
        self._logged_in = False
        self._healthy = False
        self._last_received = 0.0

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def start(self) -> None:
        self._ws_thread = threading.Thread(target=self._run, name="schwab-ws", daemon=True)
        self._ws_thread.start()

    def shutdown(self) -> None:
        # Never call ws.close/join from Tk or while a subscription lock is held.
        self._stop.set()

    def _run(self) -> None:
        backoff = 0
        try:
            while not self.stopping:
                self._healthy = False
                try:
                    self._source._set_status(self, StreamState.CONNECTING, "Connecting to Schwab")
                    self._connect_and_serve()
                except _Failure as exc:
                    self._source._set_status(self, exc.state, str(exc))
                    LOG.warning("schwab-stream: %s", exc)
                except urllib.error.HTTPError as exc:
                    state = StreamState.AUTH_REQUIRED if exc.code == 401 else StreamState.ERROR
                    self._source._set_status(self, state, "Streamer HTTP request rejected")
                    LOG.warning("schwab-stream: streamer HTTP request rejected")
                except (OSError, urllib.error.URLError):
                    self._source._set_status(self, StreamState.DISCONNECTED, "Stream disconnected; retrying")
                    LOG.warning("schwab-stream: connection interrupted")
                except ImportError:
                    self._source._set_status(self, StreamState.ERROR, "Install tradinglab[schwab] to stream")
                    LOG.error("schwab-stream: optional streaming dependency unavailable")
                    return
                except (ValueError, TypeError, KeyError):
                    self._source._set_status(self, StreamState.ERROR, "Invalid streaming response")
                    LOG.error("schwab-stream: invalid streaming response")
                if self.stopping:
                    break
                if self._healthy:
                    backoff = 0
                if self._stop.wait(_BACKOFF[backoff]):
                    break
                backoff = min(backoff + 1, len(_BACKOFF) - 1)
        except Exception:
            # Thread boundary: explicitly fail closed rather than silently
            # losing the worker or logging exception text containing tokens.
            self._source._set_status(self, StreamState.ERROR, "Streaming worker failed")
            LOG.error("schwab-stream: unexpected worker failure")
        finally:
            self._source._connection_finished(self)

    def _send(self, request: dict[str, Any], images: dict[str, int] | None = None) -> None:
        if self.stopping:
            return
        assert self._ws is not None
        self._ws.send(json.dumps({"requests": [request]}))
        self._pending[request["requestid"]] = _Pending(
            request["service"], request["command"],
            set(filter(None, request["parameters"].get("keys", "").split(","))),
            images or {}, time.monotonic(),
        )

    def _reconcile(self) -> None:
        bars, quotes, images, generation = self._source._snapshot()
        desired = {"LEVELONE_EQUITIES": bars | quotes, "CHART_EQUITY": bars}
        for service, want in desired.items():
            if any(p.service == service for p in self._pending.values()):
                continue
            have = self._wire[service]
            remove = have - want
            add = want - have
            if service == "LEVELONE_EQUITIES":
                add |= {s for s in want if images.get(s, 0) > self._imaged.get(s, -1)}
            if remove:
                command, keys = "UNSUBS", remove
            elif add:
                command = "ADD" if service in self._started else "SUBS"
                keys = add if command == "ADD" else want
            else:
                continue
            self._request_id += 1
            request = build_subs_request(service, sorted(keys), _FIELDS[service], self._info,
                                         self._request_id, command=command)
            self._send(request, {s: images.get(s, 0) for s in keys})
        if not self._pending and all(self._wire[s] == want for s, want in desired.items()):
            if self._source._ready(self, generation):
                self._healthy = True

    def _connect_and_serve(self) -> None:
        self._source._new_epoch(self)
        self._source._set_status(self, StreamState.CONNECTING, "Waiting for the shared Schwab streamer")
        while not self.stopping:
            if _STREAMER_OWNER.acquire(timeout=0.25):
                break
        else:
            return
        try:
            if self.stopping:
                return
            self._source._set_status(self, StreamState.CONNECTING, "Connecting to Schwab")
            self._serve_owned()
        finally:
            _STREAMER_OWNER.release()

    def _serve_owned(self) -> None:
        self._logged_in = False
        self._pending.clear()
        self._wire = {s: set() for s in _FIELDS}
        self._started.clear()
        self._imaged.clear()
        self._request_id = 0
        try:
            token = _access_token()
            if self.stopping:
                return
            if not token:
                raise _Failure(StreamState.AUTH_REQUIRED, "Schwab authorization required")
            self._info = fetch_streamer_info(token)
            if self.stopping:
                return
            self._ws = _Socket(self._info["streamerSocketUrl"])
            if self.stopping:
                return
            self._last_received = time.monotonic()
            self._send(build_login_request(self._info, token))
            while not self.stopping:
                now = time.monotonic()
                if any(now - p.sent_at >= _ACK_TIMEOUT for p in self._pending.values()):
                    raise _Failure(StreamState.ERROR, "Streaming acknowledgement timed out")
                if now - self._last_received >= _STALE_TIMEOUT:
                    raise _Failure(StreamState.STALE, "Streamer heartbeat timed out; reconnecting")
                if self._logged_in:
                    self._reconcile()
                try:
                    raw = self._ws.recv()
                except TimeoutError:
                    continue
                if self.stopping:
                    return
                if not raw:
                    raise OSError("Stream closed")
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    LOG.warning("schwab-stream: malformed JSON envelope")
                    continue
                if not isinstance(msg, dict):
                    LOG.warning("schwab-stream: invalid message envelope")
                    continue
                self._handle_message(msg)
        finally:
            ws, self._ws = self._ws, None
            self._info = {}
            if ws is not None:
                try:
                    ws.close()
                except OSError:
                    LOG.warning("schwab-stream: socket cleanup failed")

    def _handle_message(self, msg: dict[str, Any]) -> None:
        for response in _records(msg, "response"):
            service, command = response.get("service"), response.get("command")
            pending = self._pending.get(str(response.get("requestid")))
            code = _content_code(response)
            if service not in (*_FIELDS, "ADMIN"):
                continue
            if code != 0:
                state = StreamState.AUTH_REQUIRED if service == "ADMIN" and command == "LOGIN" else StreamState.ERROR
                raise _Failure(state, f"{service} request rejected; check authorization and symbols")
            if pending is None or (service, command) != (pending.service, pending.command):
                raise _Failure(StreamState.ERROR, "Unmatched streaming acknowledgement")
            del self._pending[str(response["requestid"])]
            if service == "ADMIN":
                self._logged_in = True
            elif command == "UNSUBS":
                self._wire[service] -= pending.symbols
                if service == "LEVELONE_EQUITIES":
                    for s in pending.symbols:
                        self._imaged.pop(s, None)
            else:
                self._wire[service] |= pending.symbols
                self._started.add(service)
                if service == "LEVELONE_EQUITIES":
                    self._imaged.update(pending.images)
            self._record_traffic()
        for notice in _records(msg, "notify"):
            if "heartbeat" in notice:
                self._record_traffic()
            elif _content_code(notice) not in (None, 0):
                raise _Failure(StreamState.ERROR, "Streamer service notification; reconnecting")
        if not self._logged_in:
            return
        for entry in _records(msg, "data"):
            service = entry.get("service")
            if service not in _FIELDS:
                continue
            for content in _records(entry, "content"):
                if self.stopping:
                    return
                if content.get("code", 0) != 0 or "error" in content:
                    raise _Failure(StreamState.ERROR, "Streaming symbol rejected; check symbol and entitlement")
                decoded = (decode_levelone_content(content) if service == "LEVELONE_EQUITIES"
                           else decode_chart_equity_content(content))
                symbol = decoded.get("symbol")
                if symbol:
                    self._record_traffic()
                    if service == "LEVELONE_EQUITIES":
                        self._source._dispatch_levelone(symbol, decoded)
                    else:
                        self._source._dispatch_chart_equity(symbol, decoded)

    def _record_traffic(self) -> None:
        self._last_received = time.monotonic()
        self._source._traffic(self)
