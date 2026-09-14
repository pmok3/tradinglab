"""Temporary HTTPS loopback receiver for the desktop Schwab OAuth flow."""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import logging
import os
import queue
import secrets
import socket
import ssl
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

LOG = logging.getLogger(__name__)
_REQUEST_SECONDS = 2.0
_CLEAR_QUERY = 'history.replaceState(null, "", location.pathname);'
_SCRIPT_HASH = base64.b64encode(hashlib.sha256(_CLEAR_QUERY.encode()).digest()).decode()


@dataclass(frozen=True)
class CallbackTarget:
    uri: str
    host: str
    port: int
    path: str
    authority: str


@dataclass(frozen=True)
class CallbackEvent:
    kind: Literal["ready", "authorized", "error"]
    code: str = ""
    message: str = ""


def callback_target(uri: str) -> CallbackTarget:
    """Validate without DNS or rewriting the registered redirect URI."""
    try:
        parsed = urlsplit(uri)
        port = parsed.port if parsed.port is not None else 443
        if (
            not uri or uri != uri.strip() or any(ord(c) <= 32 or ord(c) == 127 for c in uri)
            or parsed.scheme != "https" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or "?" in uri or "#" in uri
            or not 1 <= port <= 65535 or "\\" in uri
        ):
            raise ValueError
    except ValueError:
        raise ValueError(
            "Automatic sign-in needs the exact registered HTTPS loopback URI "
            "(127.0.0.1, localhost, or [::1]), without a query or fragment."
        ) from None
    # localhost is bound to IPv4 loopback explicitly, never resolved through DNS.
    host = "::1" if parsed.hostname == "::1" else "127.0.0.1"
    return CallbackTarget(uri, host, port, parsed.path or "/", parsed.netloc.lower())


def callback_response(query: str, state: str) -> CallbackEvent:
    """Validate one response; never echo provider-controlled text in errors."""
    try:
        fields = parse_qs(query, keep_blank_values=True, max_num_fields=16, errors="strict")
    except (ValueError, UnicodeError):
        raise ValueError("Invalid authorization response.") from None
    states = fields.get("state", [])
    if (
        not state or len(states) != 1
        or not secrets.compare_digest(states[0].encode("utf-8"), state.encode("utf-8"))
    ):
        raise ValueError("Security check failed (state mismatch). Start a fresh sign-in.")
    codes, errors = fields.get("code", []), fields.get("error", [])
    if errors:
        if len(errors) != 1 or codes:
            raise ValueError("Invalid authorization response.")
        return CallbackEvent("error", message="Schwab sign-in was declined or could not be completed.")
    if len(codes) != 1 or not codes[0] or len(codes[0]) > 8192:
        raise ValueError("Authorization response must contain exactly one code.")
    return CallbackEvent("authorized", code=codes[0])


def _tls_context() -> ssl.SSLContext:
    # Optional dependency: imports must not prevent ordinary credential editing.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TradingLab local sign-in")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(minutes=10))
        .add_extension(x509.SubjectAlternativeName([
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            x509.IPAddress(ipaddress.ip_address("::1")), x509.DNSName("localhost"),
        ]), critical=False)
        .sign(key, hashes.SHA256())
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with tempfile.TemporaryDirectory(prefix="tradinglab-oauth-tls-") as folder:
        for filename, payload in (
            ("cert.pem", cert.public_bytes(serialization.Encoding.PEM)),
            ("key.pem", key.private_bytes(serialization.Encoding.PEM,
                                         serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption())),
        ):
            path = Path(folder) / filename
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
                stream.write(payload)
        context.load_cert_chain(str(Path(folder) / "cert.pem"), str(Path(folder) / "key.pem"))
    return context


class _LoopbackServer(HTTPServer):
    allow_reuse_address = False

    def __init__(self, owner: CallbackListener, context: ssl.SSLContext) -> None:
        self.owner = owner
        self.context = context
        self._active_lock = threading.Lock()
        self._active: socket.socket | None = None
        self._request_timer: threading.Timer | None = None
        self.address_family = socket.AF_INET6 if owner.target.host == "::1" else socket.AF_INET
        super().__init__((owner.target.host, owner.target.port), _CallbackHandler)
        self.timeout = 0.1

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def get_request(self):
        connection, address = self.socket.accept()
        connection.settimeout(1.0)
        try:
            # Wrap without I/O, then publish the cancellable socket before handshake.
            with self._active_lock:
                connection = self.context.wrap_socket(
                    connection, server_side=True, do_handshake_on_connect=False)
                self._active = connection
                self._request_timer = threading.Timer(_REQUEST_SECONDS, self._interrupt, args=(connection,))
                self._request_timer.daemon = True
                self._request_timer.start()
            if self.owner._stop.is_set():
                raise OSError("Callback cancelled")
            connection.do_handshake()
            return connection, address
        except (OSError, ValueError):
            self.shutdown_request(connection)
            raise

    @staticmethod
    def _interrupt(connection: socket.socket) -> None:
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass  # Idempotent: the request may already have closed.

    def interrupt(self) -> None:
        with self._active_lock:
            if self._active is not None:
                self._interrupt(self._active)

    def shutdown_request(self, request) -> None:
        with self._active_lock:
            if self._request_timer is not None:
                self._request_timer.cancel()
                self._request_timer = None
            if self._active is request:
                self._active = None
        super().shutdown_request(request)

    def handle_error(self, request, client_address) -> None:
        if not self.owner._stop.is_set():
            LOG.warning("Local Schwab callback request failed.")


class _CallbackHandler(BaseHTTPRequestHandler):
    server: _LoopbackServer

    def log_message(self, format, *args) -> None:
        # BaseHTTPRequestHandler otherwise logs the authorization code in the URL.
        return

    def _respond(self, status: int, message: str) -> None:
        body = (
            '<!doctype html><html><head><title>TradingLab sign-in</title></head><body>'
            f'<h1>TradingLab</h1><p>{message}</p><script>{_CLEAR_QUERY}</script></body></html>'
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'sha256-{_SCRIPT_HASH}'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        owner = self.server.owner
        target = owner.target
        try:
            parsed = urlsplit(self.path)
            hosts = [host.lower() for host in self.headers.get_all("Host", [])]
            authorities = {target.authority}
            if target.port == 443:
                authorities.add(target.authority.removesuffix(":443"))
                authorities.add(target.authority.removesuffix(":443") + ":443")
            if (
                owner._stop.is_set() or len(self.path) > 16384
                or parsed.scheme or parsed.netloc or parsed.fragment
                or parsed.path != target.path or len(hosts) != 1 or hosts[0] not in authorities
            ):
                self._respond(400, "This is not the active TradingLab callback.")
                return
            response = callback_response(parsed.query, owner._state)
        except ValueError:
            self._respond(400, "Invalid sign-in response. Return to TradingLab to try again.")
            return
        # A matching response is single-use, including an OAuth denial.
        if not owner._claim():
            self._respond(400, "This sign-in has already ended.")
            return
        try:
            self._respond(200, "Authorization received. Return to TradingLab; you can close this window.")
        finally:
            owner._events.put(response)


class CallbackListener:
    """One bounded sign-in attempt; workers never touch Tk or persist OAuth tokens."""

    def __init__(self, uri: str, state: str, *, timeout: float = 300.0) -> None:
        self.target = callback_target(uri)
        if not state or not 0 < timeout <= 600:
            raise ValueError("A state nonce and a callback timeout of at most ten minutes are required.")
        self._state = state
        self._timeout = timeout
        self._events: queue.SimpleQueue[CallbackEvent] = queue.SimpleQueue()
        self._stop = threading.Event()
        self._result_lock = threading.Lock()
        self._server: _LoopbackServer | None = None
        self._thread = threading.Thread(target=self._run, name="SchwabCallback", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def poll(self) -> CallbackEvent | None:
        try:
            return self._events.get_nowait()
        except queue.Empty:
            return None

    def close(self) -> None:
        """Nonblocking cancellation; the worker releases its port and TLS context."""
        with self._result_lock:
            self._stop.set()
        server = self._server
        if server is not None:
            server.interrupt()

    def _claim(self) -> bool:
        with self._result_lock:
            if self._stop.is_set():
                return False
            self._stop.set()
            return True

    def _expire(self) -> None:
        if self._claim():
            self._events.put(CallbackEvent("error", message="Sign-in timed out. Please try again."))
            self.close()

    def _run(self) -> None:
        deadline = None
        try:
            context = _tls_context()
            if self._stop.is_set():
                return
            with _LoopbackServer(self, context) as server:
                self._server = server
                deadline = threading.Timer(self._timeout, self._expire)
                deadline.daemon = True
                deadline.start()
                if not self._stop.is_set():
                    self._events.put(CallbackEvent("ready"))
                while not self._stop.is_set():
                    server.handle_request()
        except ImportError:
            self._events.put(CallbackEvent(
                "error", message="Automatic sign-in needs the Schwab extra (cryptography). "
                "Install tradinglab[schwab], or use manual sign-in."
            ))
        except (OSError, ValueError):
            LOG.warning("Could not start or serve the local Schwab HTTPS callback.")
            self._events.put(CallbackEvent(
                "error", message="Could not open the local HTTPS callback. Check the registered URI "
                "and port availability, or use manual sign-in. No trust settings were changed."
            ))
        except Exception:  # worker boundary: always leave the UI with a secret-free failure
            LOG.error("Unexpected failure in local Schwab callback setup.")
            self._events.put(CallbackEvent("error", message="Local sign-in setup failed. Please retry or use manual sign-in."))
        finally:
            if deadline is not None:
                deadline.cancel()
            self._server = None
            self._stop.set()
