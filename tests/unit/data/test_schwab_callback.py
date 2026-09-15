"""Real loopback TLS with fake OAuth values; no Schwab connection or real tokens."""
from __future__ import annotations

import http.client
import queue
import socket
import ssl
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlencode

import pytest

from tradinglab.data import schwab_callback as callback

_NETWORK_SECONDS = 5.0


@pytest.mark.parametrize("uri", [
    "http://127.0.0.1:8182", "https://example.com", "https://127.0.0.2:8182",
    "https://127.0.0.1.evil.test", "https://user@127.0.0.1:8182",
    "https://127.0.0.1:0", "https://127.0.0.1:65536", "https://127.0.0.1:bad",
    "https://127.0.0.1/?x=1", "https://127.0.0.1/#x", "https://127.0.0.1?",
    " https://127.0.0.1", "https://127.0.0.1/\npath", "https://localhost\\evil",
])
def test_reject_non_exact_or_non_loopback_targets(uri):
    with pytest.raises(ValueError, match="registered HTTPS loopback"):
        callback.callback_target(uri)


@pytest.mark.parametrize(("uri", "host", "port", "path"), [
    ("https://127.0.0.1", "127.0.0.1", 443, "/"),
    ("https://127.0.0.1:8182/callback", "127.0.0.1", 8182, "/callback"),
    ("https://localhost:9443/auth/", "127.0.0.1", 9443, "/auth/"),
    ("https://[::1]:8182", "::1", 8182, "/"),
])
def test_exact_uri_preserved(uri, host, port, path):
    result = callback.callback_target(uri)
    assert (result.uri, result.host, result.port, result.path) == (uri, host, port, path)


@pytest.mark.parametrize("query", [
    "code=one", "state=wrong&code=one", "state=ok&state=ok&code=one",
    "state=ok&code=one&code=two", "state=ok&code=", "state=ok",
    "state=ok&error=no&code=one", "state=ok&error=a&error=b",
    "state=ok&code=%FF", "&".join(f"x{i}=a" for i in range(17)),
])
def test_ambiguous_responses_rejected(query):
    with pytest.raises(ValueError):
        callback.callback_response(query, "ok")


def test_denial_is_sanitized_and_code_decodes_once():
    response = callback.callback_response("state=ok&error=access_denied&error_description=SECRET", "ok")
    assert response.kind == "error" and "SECRET" not in response.message
    response = callback.callback_response(urlencode({"state": "ok", "code": "abc+%2B/="}), "ok")
    assert response.code == "abc+%2B/="


def _wait(listener):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        event = listener.poll()
        if event is not None:
            return event
        time.sleep(0.01)
    pytest.fail("callback worker did not publish an event")


@pytest.fixture
def listener(monkeypatch):
    # Protocol assertions are not latency assertions. Keep real TLS/timers, but
    # reserve the tight production limits for the deadline-specific tests below.
    monkeypatch.setattr(callback, "_IDLE_SECONDS", _NETWORK_SECONDS)
    monkeypatch.setattr(callback, "_REQUEST_SECONDS", _NETWORK_SECONDS)
    # Request an available port without running a privileged port-443 test.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    receiver = callback.CallbackListener(f"https://127.0.0.1:{port}/return", "NONCE")
    try:
        receiver.start()
        assert _wait(receiver).kind == "ready"
        yield receiver
    finally:
        receiver.close()
        receiver._thread.join(3)
        assert not receiver._thread.is_alive()


def _request(listener, path, host=None):
    # Only the test client accepts the freshly generated local self-signed cert.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    connection = http.client.HTTPSConnection(
        "127.0.0.1", listener.target.port, timeout=_NETWORK_SECONDS, context=context)
    try:
        headers = {} if host is None else {"Host": host}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return response.status, dict(response.headers), response.read()
    finally:
        connection.close()


def test_real_https_callback_rejects_probes_then_returns_once(listener, caplog):
    for path, host in (
        ("/wrong?state=NONCE&code=secret", None),
        ("/return?state=WRONG&code=secret", None),
        ("/return?state=NONCE&code=secret", "evil.test"),
        ("/return?state=NONCE&code=one&code=two", None),
    ):
        assert _request(listener, path, host)[0] == 400
        assert listener.poll() is None
    status, headers, body = _request(listener, "/return?state=NONCE&code=FAKE_CODE")
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert b"FAKE_CODE" not in body and b"NONCE" not in body
    assert b"replaceState" in body and "FAKE_CODE" not in caplog.text
    assert _wait(listener) == callback.CallbackEvent("authorized", code="FAKE_CODE")
    listener._thread.join(3)
    assert not listener._thread.is_alive()
    assert listener.poll() is None


def test_denial_consumes_attempt(listener):
    assert _request(listener, "/return?state=NONCE&error=access_denied")[0] == 200
    assert _wait(listener).kind == "error"


def test_certificate_is_ephemeral_and_scoped_to_loopback(monkeypatch, tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    locations = []
    original = callback.tempfile.TemporaryDirectory
    def temporary(**kwargs):
        result = original(dir=tmp_path, **kwargs)
        locations.append(result.name)
        return result
    monkeypatch.setattr(callback.tempfile, "TemporaryDirectory", temporary)
    contexts = []
    original_load = ssl.SSLContext.load_cert_chain
    certificates = []
    def load(context, certfile, keyfile):
        cert = x509.load_pem_x509_certificate(callback.Path(certfile).read_bytes())
        key = serialization.load_pem_private_key(callback.Path(keyfile).read_bytes(), password=None)
        assert cert.public_key().public_numbers() == key.public_key().public_numbers()
        certificates.append(cert)
        return original_load(context, certfile, keyfile)
    monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", load)
    for _ in range(2):
        contexts.append(callback._tls_context())
    assert all(context.minimum_version == ssl.TLSVersion.TLSv1_2 for context in contexts)
    assert len(locations) == 2 and not list(tmp_path.iterdir())
    assert certificates[0].serial_number != certificates[1].serial_number
    cert = certificates[0]
    assert cert.not_valid_after_utc - cert.not_valid_before_utc == callback.timedelta(minutes=11)
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == ["localhost"]
    assert {str(ip) for ip in san.get_values_for_type(x509.IPAddress)} == {"127.0.0.1", "::1"}


@pytest.mark.parametrize("failure", ["crypto", "tls"])
def test_setup_failure_never_reports_ready(monkeypatch, failure):
    def fail():
        raise ImportError if failure == "crypto" else OSError
    monkeypatch.setattr(callback, "_tls_context", fail)
    listener = callback.CallbackListener("https://127.0.0.1:8182", "nonce")
    listener.start()
    event = _wait(listener)
    listener._thread.join(3)
    assert event.kind == "error"
    assert listener.poll() is None


def test_occupied_port_and_timeout_have_visible_results(listener):
    second = callback.CallbackListener(listener.target.uri, "other")
    second.start()
    assert _wait(second).kind == "error"
    second._thread.join(3)
    listener.close()
    listener._thread.join(3)
    timed = callback.CallbackListener(listener.target.uri, "nonce", timeout=0.1)
    timed.start()
    assert _wait(timed).kind == "ready"
    assert "timed out" in _wait(timed).message
    timed._thread.join(3)


def test_cancellation_releases_port_without_authorization(listener):
    listener.close()
    listener._thread.join(3)
    assert listener.poll() is None
    with socket.socket() as sock:
        sock.bind((listener.target.host, listener.target.port))


@pytest.mark.parametrize("ending", ["request-timeout", "cancel", "handshake-error"])
def test_production_deadlines_cover_the_published_handshake(monkeypatch, ending):
    connection, wrapped, timer = Mock(), Mock(), Mock()
    timer_factory = Mock(return_value=timer)
    # Patch only this module's threading reference, not other workers' timers.
    monkeypatch.setattr(callback, "threading", SimpleNamespace(**{
        **vars(threading), "Timer": timer_factory,
    }))
    server = callback._LoopbackServer.__new__(callback._LoopbackServer)
    server.owner = SimpleNamespace(_stop=threading.Event())
    server.socket = Mock()
    address = ("127.0.0.1", 12345)
    server.socket.accept.return_value = connection, address
    server.context = Mock()
    server.context.wrap_socket.return_value = wrapped
    server._active_lock = threading.Lock()
    server._active = None
    server._request_timer = None

    def handshake():
        assert server._active is wrapped
        connection.settimeout.assert_called_once_with(1.0)
        server.context.wrap_socket.assert_called_once_with(
            connection, server_side=True, do_handshake_on_connect=False)
        timer_factory.assert_called_once_with(2.0, server._interrupt, args=(wrapped,))
        assert timer.daemon is True
        timer.start.assert_called_once_with()
        if ending == "request-timeout":
            timer_factory.call_args.args[1](*timer_factory.call_args.kwargs["args"])
        elif ending == "cancel":
            server.interrupt()
        else:
            raise ssl.SSLError("fake handshake failure")
        wrapped.shutdown.assert_called_once_with(socket.SHUT_RDWR)

    wrapped.do_handshake.side_effect = handshake
    if ending == "handshake-error":
        with pytest.raises(ssl.SSLError, match="fake handshake failure"):
            server.get_request()
    else:
        assert server.get_request() == (wrapped, address)
        server.shutdown_request(wrapped)
    timer.cancel.assert_called_once_with()
    wrapped.close.assert_called_once_with()
    assert server._active is None and server._request_timer is None


@pytest.mark.parametrize("ending", ["cancel", "attempt-timeout", "request-timeout"])
def test_trickled_headers_cannot_hold_callback_open(monkeypatch, ending):
    request_seconds = callback._REQUEST_SECONDS
    if ending == "request-timeout":
        monkeypatch.setattr(callback, "_REQUEST_SECONDS", 0.4)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    listener = callback.CallbackListener(
        f"https://127.0.0.1:{port}/return", "NONCE",
        timeout=0.4 if ending == "attempt-timeout" else 10,
    )
    listener.start()
    assert _wait(listener).kind == "ready"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    started, stop_sending = threading.Event(), threading.Event()
    client = context.wrap_socket(socket.create_connection(("127.0.0.1", port), timeout=2),
                                 server_hostname="127.0.0.1")
    def trickle():
        try:
            client.sendall(f"GET /return HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-Slow: ".encode())
            started.set()
            while not stop_sending.wait(0.05):
                client.sendall(b"x")
        except OSError:
            pass
    peer = threading.Thread(target=trickle, daemon=True)
    peer.start()
    try:
        assert started.wait(2)
        if ending == "cancel":
            listener.close()
        if ending == "request-timeout":
            peer.join(2)
            assert not peer.is_alive(), "absolute request deadline must interrupt active headers"
            assert listener._thread.is_alive(), "only the malformed request should end"
            monkeypatch.setattr(callback, "_REQUEST_SECONDS", request_seconds)
            assert _request(listener, "/return?state=NONCE&code=OK")[0] == 200
            assert _wait(listener).kind == "authorized"
        elif ending == "attempt-timeout":
            assert "timed out" in _wait(listener).message
        listener._thread.join(2)
        assert not listener._thread.is_alive(), "cancellation/expiry must not depend on peer finishing headers"
        with socket.socket() as rebound:
            rebound.bind(("127.0.0.1", port))
    finally:
        stop_sending.set()
        client.close()
        listener.close()
        peer.join(2)
        listener._thread.join(2)
