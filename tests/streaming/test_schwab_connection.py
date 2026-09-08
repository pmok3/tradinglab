"""Scripted real-worker tests. No credentials, sockets, HTTP or app startup."""

from __future__ import annotations

import json
import sys
import threading
from collections import deque
from dataclasses import FrozenInstanceError
from datetime import datetime
from types import SimpleNamespace

import pytest

from tradinglab.core.timezones import ET
from tradinglab.streaming import schwab_connection as wire
from tradinglab.streaming.base import StreamState, StreamStatus
from tradinglab.streaming.schwab import SchwabStreamSource

INFO = {
    "schwabClientCustomerId": "fake-customer", "schwabClientCorrelId": "fake-correl",
    "schwabClientChannel": "fake-channel", "schwabClientFunctionId": "fake-function",
    "streamerSocketUrl": "wss://not-a-server.invalid",
}
AT = datetime(2024, 7, 1, 9, 30, tzinfo=ET)


def ack(request, code=0):
    return {"response": [{
        "service": request["service"], "command": request["command"],
        "requestid": request["requestid"], "content": {"code": code, "msg": "private-vendor-text"},
    }]}


def levelone(symbol="AAPL", **fields):
    return {"data": [{"service": "LEVELONE_EQUITIES", "timestamp": int(AT.timestamp() * 1000),
                      "content": [{"key": symbol, **fields}]}]}


def chart(symbol="AAPL"):
    return {"data": [{"service": "CHART_EQUITY", "timestamp": int(AT.timestamp() * 1000),
                      "content": [{"key": symbol, "seq": 0, "1": 779, "2": 100, "3": 102,
                                   "4": 99, "5": 101, "6": 50, "7": int(AT.timestamp() * 1000),
                                   "8": 19859}]}]}


class ScriptSocket:
    def __init__(self, incoming=(), *, idle=None, reply=None):
        self.incoming = deque(incoming)
        self.sent = []
        self.closed = False
        self.idle = idle
        self.reply = reply or ack

    def send(self, raw):
        request = json.loads(raw)["requests"][0]
        self.sent.append(request)
        response = self.reply(request)
        if response is not None:
            self.incoming.append(response)

    def recv(self):
        if not self.incoming:
            if self.idle:
                self.idle(self)
            if not self.incoming:
                raise OSError("private socket error")
        item = self.incoming.popleft()
        if isinstance(item, BaseException):
            raise item
        return item if isinstance(item, str) else json.dumps(item)

    def close(self):
        self.closed = True


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(wire._Connection, "start", lambda self: None)
    monkeypatch.setattr(wire, "_access_token", lambda: "fake-token")
    monkeypatch.setattr(wire, "fetch_streamer_info", lambda token: dict(INFO))
    monkeypatch.setattr(wire.time, "time", lambda: AT.timestamp())
    src = SchwabStreamSource()
    yield src
    src.close()


def serve(monkeypatch, src, sock):
    monkeypatch.setattr(wire, "_Socket", lambda url: sock)
    conn = src._connection
    with pytest.raises(OSError):
        conn._connect_and_serve()
    assert sock.closed
    return conn


def commands(sock):
    return [(r["service"], r["command"], r["parameters"].get("keys")) for r in sock.sent]


def test_login_heartbeats_and_per_service_initial_subs_not_request_id_heuristics(monkeypatch, offline):
    offline.subscribe("AAPL", "1m", lambda *a: None)
    offline.subscribe_quotes(["MSFT"], lambda q: None)
    observed = []
    sock = ScriptSocket([{"notify": [{"heartbeat": "1719830000000"}]}],
                        idle=lambda s: observed.append(offline.get_status().state))
    serve(monkeypatch, offline, sock)
    assert commands(sock) == [
        ("ADMIN", "LOGIN", None), ("LEVELONE_EQUITIES", "SUBS", "AAPL,MSFT"),
        ("CHART_EQUITY", "SUBS", "AAPL"),
    ]
    assert [r["requestid"] for r in sock.sent] == ["0", "1", "2"]
    assert observed == [StreamState.LIVE]
    assert offline.get_status("AAPL").state == StreamState.CONNECTING


def test_quote_universe_never_creates_chart_subscriptions_and_changes_batch(monkeypatch, offline):
    sub = offline.subscribe_quotes([f"S{i:03d}" for i in range(500)], lambda q: None)
    phase = 0

    def idle(sock):
        nonlocal phase
        if phase == 0:
            phase += 1
            sub.set_symbols([f"S{i:03d}" for i in range(100, 501)])
            sock.incoming.append(TimeoutError())

    sock = ScriptSocket(idle=idle)
    serve(monkeypatch, offline, sock)
    requests = sock.sent[1:]
    assert [(r["service"], r["command"]) for r in requests] == [
        ("LEVELONE_EQUITIES", "SUBS"), ("LEVELONE_EQUITIES", "UNSUBS"), ("LEVELONE_EQUITIES", "ADD"),
    ]
    assert len(requests[0]["parameters"]["keys"].split(",")) == 500
    assert len(requests[1]["parameters"]["keys"].split(",")) == 100
    assert requests[2]["parameters"]["keys"] == "S500"


def test_bar_joins_quote_symbol_and_quote_newcomers_get_images_without_dropping_others(monkeypatch, offline):
    q1 = offline.subscribe_quotes(["AAPL"], lambda q: None)
    phase, unsubscribe = 0, None

    def idle(sock):
        nonlocal phase, unsubscribe
        phase += 1
        if phase == 1:
            unsubscribe = offline.subscribe("AAPL", "1m", lambda *a: None)
        elif phase == 2:
            offline.subscribe_quotes(["AAPL"], lambda q: None)
        elif phase == 3:
            unsubscribe()
            q1.close()
        else:
            return
        sock.incoming.append(TimeoutError())

    sock = ScriptSocket(idle=idle)
    serve(monkeypatch, offline, sock)
    assert commands(sock) == [
        ("ADMIN", "LOGIN", None), ("LEVELONE_EQUITIES", "SUBS", "AAPL"),
        ("LEVELONE_EQUITIES", "ADD", "AAPL"), ("CHART_EQUITY", "SUBS", "AAPL"),
        ("LEVELONE_EQUITIES", "ADD", "AAPL"), ("CHART_EQUITY", "UNSUBS", "AAPL"),
    ]


def test_changes_during_pending_ack_reconcile_after_ack(monkeypatch, offline):
    sub = offline.subscribe_quotes(["AAPL"], lambda q: None)
    changed = False

    def reply(request):
        nonlocal changed
        if request["command"] == "SUBS" and not changed:
            changed = True
            sub.set_symbols(["MSFT"])
        return ack(request)

    sock = ScriptSocket(reply=reply)
    serve(monkeypatch, offline, sock)
    assert commands(sock)[1:] == [
        ("LEVELONE_EQUITIES", "SUBS", "AAPL"),
        ("LEVELONE_EQUITIES", "UNSUBS", "AAPL"),
        ("LEVELONE_EQUITIES", "ADD", "MSFT"),
    ]


@pytest.mark.parametrize("failed_service,state", [
    ("ADMIN", StreamState.AUTH_REQUIRED), ("LEVELONE_EQUITIES", StreamState.ERROR),
    ("CHART_EQUITY", StreamState.ERROR),
])
def test_rejected_login_or_service_never_reports_live_and_always_closes(monkeypatch, offline, failed_service, state):
    offline.subscribe("AAPL", "1m", lambda *a: None)
    sock = ScriptSocket(reply=lambda request: ack(request, 3 if request["service"] == failed_service else 0))
    monkeypatch.setattr(wire, "_Socket", lambda url: sock)
    with pytest.raises(wire._Failure) as exc:
        offline._connection._connect_and_serve()
    assert exc.value.state == state
    assert offline.get_status().state != StreamState.LIVE
    assert "private-vendor-text" not in str(exc.value)
    assert sock.closed


def test_live_waits_for_every_service_ack_even_with_early_data(monkeypatch, offline):
    got = []
    offline.subscribe("AAPL", "1m", lambda kind, c: got.append(kind))
    conn = offline._connection
    observed = []

    def reply(request):
        if request["service"] == "CHART_EQUITY":
            return None
        return ack(request)

    def idle(sock):
        if not got:
            sock.incoming.append(levelone(**{"3": 100, "35": int(AT.timestamp() * 1000)}))
        else:
            observed.append(offline.get_status("AAPL").state)

    serve(monkeypatch, offline, ScriptSocket(idle=idle, reply=reply))
    assert got == ["rollover"]
    assert observed == [StreamState.CONNECTING]
    assert len(conn._pending) == 1


def test_usable_symbol_data_authoritative_kind_and_old_provisional_suppression(monkeypatch, offline):
    got, quotes = [], []
    offline.subscribe("AAPL", "1m", lambda kind, c: got.append((kind, c)))
    offline.subscribe_quotes(["MSFT"], quotes.append)
    phase = 0
    statuses = []

    def idle(sock):
        nonlocal phase
        phase += 1
        statuses.append(offline.get_status("AAPL").state)
        if phase == 1:
            sock.incoming.extend([
                levelone(**{"1": 99, "2": 101}),
                levelone("MSFT", **{"3": 10, "12": 9}),
                levelone(**{"3": float("nan"), "35": int(AT.timestamp() * 1000)}),
            ])
        elif phase == 2:
            sock.incoming.append(levelone(**{"3": 100, "35": int(AT.timestamp() * 1000)}))
        elif phase == 3:
            sock.incoming.extend([chart(), levelone(**{"3": 999})])

    serve(monkeypatch, offline, ScriptSocket(idle=idle))
    assert [k for k, c in got] == ["rollover", "closed"]
    assert got[-1][1].close == 101
    assert quotes[0].prev_close == 9
    assert statuses == [StreamState.CONNECTING, StreamState.CONNECTING, StreamState.LIVE, StreamState.LIVE]


def test_reconnect_resets_subs_and_generation_and_requires_fresh_token(monkeypatch, offline):
    offline.subscribe("AAPL", "1m", lambda *a: None)
    first = ScriptSocket()
    serve(monkeypatch, offline, first)
    assert offline.get_status().generation == 1
    second = ScriptSocket()
    serve(monkeypatch, offline, second)
    assert offline.get_status().generation == 2
    assert commands(first) == commands(second)
    monkeypatch.setattr(wire, "_access_token", lambda: None)
    opened = []
    monkeypatch.setattr(wire, "_Socket", lambda url: opened.append(url))
    with pytest.raises(wire._Failure, match="authorization required"):
        offline._connection._connect_and_serve()
    assert offline.get_status().generation == 3
    assert opened == []


class StopAfterWaits:
    def __init__(self, count):
        self.waits = []
        self.count = count
        self.stopped = False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, delay):
        self.waits.append(delay)
        self.stopped = len(self.waits) >= self.count
        return self.stopped


def test_bounded_backoff_starts_at_one_and_resets_after_healthy_session(monkeypatch, offline, caplog):
    offline.subscribe_quotes(["AAPL"], lambda q: None)
    conn = offline._connection
    stop = StopAfterWaits(10)
    conn._stop = stop
    monkeypatch.setattr(offline, "_connection_finished", lambda conn: None)
    attempts = []

    def connect():
        attempts.append(1)
        conn._healthy = len(attempts) == 9
        raise OSError("fake-token private socket error")

    monkeypatch.setattr(conn, "_connect_and_serve", connect)
    conn._run()
    assert stop.waits == [1, 2, 4, 8, 16, 30, 30, 30, 1, 2]
    assert offline.get_status().state == StreamState.DISCONNECTED
    assert "private socket error" not in caplog.text


def test_rejection_is_published_as_sanitized_error_by_worker(monkeypatch, offline, caplog):
    offline.subscribe("AAPL", "1m", lambda *a: None)
    conn = offline._connection
    monkeypatch.setattr(offline, "_connection_finished", lambda conn: None)
    conn._stop = StopAfterWaits(1)
    sock = ScriptSocket(reply=lambda r: ack(r, 19 if r["service"] == "CHART_EQUITY" else 0))
    monkeypatch.setattr(wire, "_Socket", lambda url: sock)
    conn._run()
    status = offline.get_status("AAPL")
    assert status.state == StreamState.ERROR
    assert "CHART_EQUITY" in status.message
    assert "fake-token" not in caplog.text
    assert "private-vendor-text" not in caplog.text
    assert sock.closed


@pytest.mark.parametrize("mode,state", [("ack", StreamState.ERROR), ("heartbeat", StreamState.STALE)])
def test_missing_ack_or_heartbeat_has_bounded_deadline(monkeypatch, offline, mode, state):
    clock = [100.0]
    monkeypatch.setattr(wire.time, "monotonic", lambda: clock[0])
    offline.subscribe_quotes(["AAPL"], lambda q: None)

    def idle(sock):
        clock[0] += 31
        sock.incoming.append(TimeoutError())

    sock = ScriptSocket(idle=idle, reply=(lambda r: None) if mode == "ack" else ack)
    monkeypatch.setattr(wire, "_Socket", lambda url: sock)
    with pytest.raises(wire._Failure) as exc:
        offline._connection._connect_and_serve()
    assert exc.value.state == state
    assert sock.closed


def test_heartbeats_keep_transport_live_while_quiet_symbol_fallback_is_local(monkeypatch, offline):
    clock = [100.0]
    monkeypatch.setattr(wire.time, "monotonic", lambda: clock[0])
    offline.subscribe("AAPL", "1m", lambda *a: None)
    phase = 0
    observed = []

    def idle(sock):
        nonlocal phase
        phase += 1
        if phase == 1:
            sock.incoming.append(chart())
        elif phase < 8:
            clock[0] += 20
            sock.incoming.append({"notify": [{"heartbeat": "1719830000000"}]})
        else:
            observed.append((offline.get_status(), offline.get_status("AAPL")))

    serve(monkeypatch, offline, ScriptSocket(idle=idle))
    transport, symbol = observed[0]
    assert transport.state == StreamState.LIVE
    assert symbol.state == StreamState.STALE
    assert "shared transport is healthy" in symbol.message


def test_callback_close_suppresses_remaining_batch_and_never_reconnects(monkeypatch, offline):
    got = []

    def callback(kind, candle):
        got.append(kind)
        offline.close()

    offline.subscribe("AAPL", "1m", callback)
    sock = ScriptSocket(idle=lambda s: s.incoming.extend([chart(), chart()]))
    monkeypatch.setattr(wire, "_Socket", lambda url: sock)
    conn = offline._connection
    conn._run()
    assert got == ["closed"]
    assert sock.closed
    assert offline._connection is None
    assert offline.get_status().state == StreamState.CLOSED


def test_status_is_immutable_and_subscribe_rejects_unsupported_interval(offline):
    with pytest.raises(FrozenInstanceError):
        offline.get_status().state = StreamState.LIVE
    with pytest.raises(ValueError, match="only 1m"):
        offline.subscribe("AAPL", "5m", lambda *a: None)
    assert offline._connection is None


@pytest.mark.parametrize("envelope", [
    {"response": [{"service": "CHART_EQUITY", "command": "SUBS", "requestid": "999",
                   "content": {"code": 0}}]},
    {"notify": [{"service": "ADMIN", "content": {"code": 12, "msg": "private-error"}}]},
    {"data": [{"service": "LEVELONE_EQUITIES", "content": [{"key": "BAD", "code": 17}]}]},
    {"response": "bad"}, {"data": [{"service": "CHART_EQUITY", "content": [42]}]},
])
def test_protocol_service_notifications_and_symbol_errors_fail_explicitly(monkeypatch, offline, envelope):
    offline.subscribe("AAPL", "1m", lambda *a: None)
    sock = ScriptSocket(idle=lambda s: s.incoming.append(envelope))
    monkeypatch.setattr(wire, "_Socket", lambda url: sock)
    with pytest.raises(wire._Failure) as exc:
        offline._connection._connect_and_serve()
    assert exc.value.state == StreamState.ERROR
    assert "private-error" not in str(exc.value)
    assert sock.closed


def test_raw_bad_json_is_never_logged_and_does_not_replace_valid_data(monkeypatch, offline, caplog):
    offline.subscribe_quotes(["AAPL"], lambda q: None)
    sock = ScriptSocket(["not-json-private-token", "[1,2,3]"])
    serve(monkeypatch, offline, sock)
    assert "malformed JSON" in caplog.text
    assert "invalid message envelope" in caplog.text
    assert "private-token" not in caplog.text


def test_old_vendor_snapshot_does_not_mark_symbol_fresh(monkeypatch, offline):
    monkeypatch.setattr(wire.time, "time", lambda: AT.timestamp() + 3600)
    offline.subscribe("AAPL", "1m", lambda *a: None)
    phase, states = 0, []

    def idle(sock):
        nonlocal phase
        phase += 1
        if phase == 1:
            sock.incoming.append(chart())
        else:
            states.append((offline.get_status().state, offline.get_status("AAPL").state))

    serve(monkeypatch, offline, ScriptSocket(idle=idle))
    assert states == [(StreamState.LIVE, StreamState.STALE)]


def test_status_detects_lost_heartbeat_even_before_worker_next_poll(monkeypatch, offline):
    clock = [100.0]
    monkeypatch.setattr(wire.time, "monotonic", lambda: clock[0])
    offline.subscribe_quotes(["AAPL"], lambda q: None)
    serve(monkeypatch, offline, ScriptSocket())
    assert offline.get_status().state == StreamState.LIVE
    clock[0] += 31
    assert offline.get_status().state == StreamState.STALE


def test_failed_send_closes_socket_instead_of_silently_advancing_membership(monkeypatch, offline):
    offline.subscribe_quotes(["AAPL"], lambda q: None)

    class FailedSend(ScriptSocket):
        def send(self, raw):
            raise OSError("private-token")

    sock = FailedSend()
    conn = serve(monkeypatch, offline, sock)
    assert not conn._pending
    assert not any(conn._wire.values())


def test_seed_lookup_is_deprecated_never_called_or_emitted(monkeypatch, offline):
    got = []
    src = SchwabStreamSource(seed_lookup=lambda *args: pytest.fail("synchronous history lookup"))
    src.subscribe("AAPL", "1m", lambda *args: got.append(args))
    src.close()
    assert got == []


def test_preferences_use_bounded_credentialed_request_and_validate_payload(monkeypatch):
    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, count):
            assert count == wire.MAX_RESPONSE_BYTES
            return json.dumps({"streamerInfo": [INFO]}).encode()

    def open_request(request, timeout):
        captured.append((request, timeout))
        return Response()

    monkeypatch.setattr(wire, "credentialed_opener", lambda: SimpleNamespace(open=open_request))
    assert wire.fetch_streamer_info("test-token") == INFO
    request, timeout = captured[0]
    assert timeout == 15
    assert request.get_header("Authorization") == "Bearer test-token"


@pytest.mark.parametrize("info", [
    {}, {**INFO, "streamerSocketUrl": "ws://insecure.invalid"},
    {**INFO, "schwabClientCustomerId": None},
])
def test_invalid_preferences_fail_without_leaking_identifiers(monkeypatch, info):
    from contextlib import nullcontext
    from io import BytesIO

    payload = json.dumps({"streamerInfo": [info]}).encode()
    monkeypatch.setattr(wire, "credentialed_opener",
                        lambda: SimpleNamespace(open=lambda *a, **k: nullcontext(BytesIO(payload))))
    with pytest.raises(wire._Failure) as exc:
        wire.fetch_streamer_info("private-token")
    assert exc.value.state == StreamState.ERROR
    assert "private-token" not in str(exc.value)


def test_socket_adapter_translates_timeouts_and_errors_and_bounds_close(monkeypatch):
    class SocketError(Exception):
        pass

    class SocketTimeout(SocketError):
        pass

    calls = []
    error = [SocketTimeout("private-token")]

    def recv():
        raise error[0]

    ws = SimpleNamespace(
        settimeout=lambda value: calls.append(("timeout", value)),
        send=lambda raw: calls.append(("send", raw)),
        recv=recv,
        close=lambda **kwargs: calls.append(("close", kwargs)),
    )
    monkeypatch.setitem(sys.modules, "websocket", SimpleNamespace(
        WebSocketException=SocketError, WebSocketTimeoutException=SocketTimeout,
        create_connection=lambda url, timeout: ws,
    ))
    adapter = wire._Socket("wss://fake.invalid")
    with pytest.raises(TimeoutError):
        adapter.recv()
    error[0] = SocketError("private-token")
    with pytest.raises(OSError, match="receive failed") as exc:
        adapter.recv()
    assert "private-token" not in str(exc.value)
    adapter.close()
    assert calls == [("timeout", 0.25), ("close", {"timeout": 0})]


def test_auth_is_worker_only_unlocked_and_close_during_auth_never_opens_socket(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    src = SchwabStreamSource()
    main_thread = threading.get_ident()
    token_thread = []
    opened = []

    def token():
        token_thread.append(threading.get_ident())
        assert src._lock.acquire(blocking=False)
        src._lock.release()
        entered.set()
        assert release.wait(3)
        return "fake-token"

    monkeypatch.setattr(wire, "_access_token", token)
    monkeypatch.setattr(wire, "_Socket", lambda url: opened.append(url))
    sub = src.subscribe("AAPL", "1m", lambda *args: None)
    conn = src._connection
    try:
        assert entered.wait(3)
        src.close()
        sub()
    finally:
        release.set()
        conn._ws_thread.join(3)
        src.close()
    assert token_thread and token_thread[0] != main_thread
    assert not conn._ws_thread.is_alive()
    assert opened == []
    assert src.get_status().state == StreamState.CLOSED


def test_last_unsubscribe_then_new_subscription_waits_for_old_socket_finally(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    src = SchwabStreamSource()
    sockets = []

    def socket_factory(url):
        assert all(s.closed for s in sockets), "two simultaneous streamer sockets"
        sock = ScriptSocket()
        if not sockets:
            def idle(s):
                entered.set()
                assert release.wait(3)
                raise TimeoutError
            sock.idle = idle
        else:
            sock.idle = lambda s: src.close()
        sockets.append(sock)
        return sock

    monkeypatch.setattr(wire, "_access_token", lambda: "fake-token")
    monkeypatch.setattr(wire, "fetch_streamer_info", lambda token: INFO)
    monkeypatch.setattr(wire, "_Socket", socket_factory)
    drop = src.subscribe("AAPL", "1m", lambda *a: None)
    old = src._connection
    try:
        assert entered.wait(3)
        drop()
        keep = src.subscribe("MSFT", "1m", lambda *a: None)
        assert src._connection is old
    finally:
        release.set()
        old._ws_thread.join(3)
    # The successor starts from the old worker's finally, not subscribe().
    successor = src._connection
    if successor is not None:
        successor._ws_thread.join(3)
    src.close()
    assert len(sockets) == 2
    assert all(s.closed for s in sockets)
    assert commands(sockets[1])[1:][0] == ("LEVELONE_EQUITIES", "SUBS", "MSFT")
