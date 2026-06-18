"""Interactive Schwab OAuth sign-in dialog.

Replaces the terminal-only ``python -m tradinglab.data.schwab_login`` flow
with an in-app, guided popup (Tools → "Connect to Schwab…"). It does NOT
embed Schwab's login page in a webview — that is an OAuth anti-pattern
(RFC 8252: native apps must use the system browser) and Schwab blocks
embedded webviews anyway. Instead it drives the standard, secure flow:

1. Open Schwab's authorization URL in the user's **system browser** (where
   the password manager / 2FA / passkeys all work).
2. Schwab redirects to the registered ``https://127.0.0.1`` redirect URI;
   the browser shows a "can't reach this page" error — expected, because we
   run no local listener.
3. The user copies that full redirected URL from the address bar and pastes
   it into the dialog. We verify the OAuth ``state`` nonce (CSRF defence),
   extract the code, exchange it for tokens on a background thread, and save
   the DPAPI-protected token cache.

All the OAuth crypto is reused from :mod:`tradinglab.data.schwab_login` and
:mod:`tradinglab.data.schwab_auth` — this module is purely the GUI shell.
"""
from __future__ import annotations

import secrets
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from ..data.credentials import get_credentials
from ..data.schwab_auth import (
    build_token_cache,
    is_access_token_fresh,
    is_refresh_token_alive,
    load_token_cache,
    save_token_cache,
    token_cache_path,
)
from ..data.schwab_login import (
    build_authorize_url,
    exchange_code_for_tokens,
    extract_code,
    extract_state,
)
from ._modal_base import BaseModalDialog, protect_combobox_wheel
from .colors import MUTED_GREY

_DEFAULT_REDIRECT_URI = "https://127.0.0.1"


class SchwabConnectDialog(BaseModalDialog):
    """Guided, browser-based Schwab OAuth sign-in (no embedded webview)."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(
            parent,
            title="Connect to Schwab",
            geometry_key="dlg.schwab_connect",
            default_geometry="620x500",
            resizable=(False, False),
        )
        # OAuth handshake state for the current attempt.
        self._state_nonce: str | None = None
        self._redirect_uri: str | None = None
        # Background token-exchange plumbing (§7.15: worker writes a result
        # dict, the Tk main thread polls it via ``after`` — never call
        # ``after`` from the worker).
        self._exchange_thread: threading.Thread | None = None
        self._exchange_result: dict | None = None
        self._poll_job: str | None = None

        self._url_var = tk.StringVar(value="")
        self._paste_var = tk.StringVar(value="")
        self._status_var = tk.StringVar(value="")
        self._progress_var = tk.StringVar(value="")

        self._build_widgets()
        self._refresh_status()
        protect_combobox_wheel(self)
        self._finalize_modal(primary=self._on_connect, cancel=self._on_close)

    # ------------------------------------------------------------------ build
    def _build_widgets(self) -> None:
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(0, weight=1)

        ttk.Label(
            frm,
            text="Sign in to Schwab in your browser, then paste the "
                 "redirected address back here.",
            wraplength=580, justify="left",
        ).grid(row=0, column=0, sticky="w")

        ttk.Label(frm, textvariable=self._status_var, foreground=MUTED_GREY,
                  wraplength=580, justify="left").grid(
            row=1, column=0, sticky="w", pady=(4, 8))

        ttk.Separator(frm, orient="horizontal").grid(
            row=2, column=0, sticky="ew", pady=(0, 8))

        # --- Step 1 -------------------------------------------------------
        ttk.Label(frm, text="Step 1 — Open the Schwab sign-in page",
                  font=("TkDefaultFont", 10, "bold")).grid(
            row=3, column=0, sticky="w")
        self._open_btn = ttk.Button(
            frm, text="Open Schwab sign-in in your browser",
            command=self._on_open_browser)
        self._open_btn.grid(row=4, column=0, sticky="w", pady=(4, 4))

        url_row = ttk.Frame(frm)
        url_row.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        url_row.columnconfigure(0, weight=1)
        url_entry = ttk.Entry(url_row, textvariable=self._url_var, state="readonly")
        url_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(url_row, text="Copy URL", command=self._on_copy_url).grid(
            row=0, column=1, padx=(6, 0))

        ttk.Separator(frm, orient="horizontal").grid(
            row=6, column=0, sticky="ew", pady=(0, 8))

        # --- Step 2 -------------------------------------------------------
        ttk.Label(frm, text="Step 2 — Paste the redirected address",
                  font=("TkDefaultFont", 10, "bold")).grid(
            row=7, column=0, sticky="w")
        ttk.Label(
            frm,
            text=("After you sign in, your browser will show a \"this site "
                  "can't be reached\" page — that's expected. Copy the full "
                  "address from the address bar (it starts with "
                  "https://127.0.0.1/?code=…) and paste it below."),
            foreground=MUTED_GREY, wraplength=580, justify="left",
        ).grid(row=8, column=0, sticky="w", pady=(2, 4))

        paste_entry = ttk.Entry(frm, textvariable=self._paste_var)
        paste_entry.grid(row=9, column=0, sticky="ew")
        self._connect_btn = ttk.Button(
            frm, text="Connect", command=self._on_connect)
        self._connect_btn.grid(row=10, column=0, sticky="w", pady=(6, 0))

        ttk.Label(frm, textvariable=self._progress_var, wraplength=580,
                  justify="left").grid(row=11, column=0, sticky="w", pady=(8, 0))

        # --- Footer -------------------------------------------------------
        footer = ttk.Frame(frm)
        footer.grid(row=12, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Button(footer, text="Disconnect", command=self._on_disconnect).grid(
            row=0, column=0, sticky="w")
        ttk.Button(footer, text="Close", command=self._on_close).grid(
            row=0, column=1, sticky="e")

    # ----------------------------------------------------------------- status
    @staticmethod
    def _creds():
        return get_credentials().schwab

    def _compute_status_text(self) -> str:
        creds = self._creds()
        if not creds.is_configured():
            return ("Not configured — add your Schwab App Key + Secret first "
                    "via Tools → Configure Credentials.")
        try:
            cache = load_token_cache()
        except Exception:  # noqa: BLE001
            cache = None
        if not cache:
            return "Configured, not connected — sign in below to get tokens."
        try:
            if is_access_token_fresh(cache):
                return "Connected ✓ — access token valid."
            if is_refresh_token_alive(cache):
                return "Connected ✓ — access token will auto-refresh."
        except Exception:  # noqa: BLE001
            pass
        return "Tokens expired — sign in again to reconnect."

    def _refresh_status(self) -> None:
        try:
            self._status_var.set("Status: " + self._compute_status_text())
        except tk.TclError:
            pass

    def _set_progress(self, msg: str) -> None:
        try:
            self._progress_var.set(msg)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ step 1
    def _on_open_browser(self) -> None:
        creds = self._creds()
        if not creds.is_configured():
            messagebox.showinfo(
                "Connect to Schwab",
                "Enter your Schwab App Key and App Secret first via "
                "Tools → Configure Credentials, then try again.",
                parent=self,
            )
            return
        redirect_uri = creds.redirect_uri or _DEFAULT_REDIRECT_URI
        # Fresh single-use CSRF nonce per attempt; verified byte-for-byte
        # against the echoed ``state`` on the pasted redirect URL.
        state = secrets.token_urlsafe(24)
        self._state_nonce = state
        self._redirect_uri = redirect_uri
        url = build_authorize_url(creds.app_key or "", redirect_uri, state=state)
        self._url_var.set(url)
        opened = False
        try:
            opened = bool(webbrowser.open(url))
        except Exception:  # noqa: BLE001
            opened = False
        if opened:
            self._set_progress(
                "Opened your browser. Sign in to Schwab, then paste the "
                "redirected address into Step 2.")
        else:
            self._set_progress(
                "Couldn't open a browser automatically — click \"Copy URL\" "
                "and open it yourself, then continue with Step 2.")

    def _on_copy_url(self) -> None:
        url = self._url_var.get()
        if not url:
            self._set_progress("Click \"Open Schwab sign-in\" first to "
                               "generate the URL.")
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(url)
            self._set_progress("Sign-in URL copied to clipboard.")
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ step 2
    @staticmethod
    def _verify_and_extract(
        pasted_url: str, nonce: str | None,
    ) -> tuple[str | None, str | None]:
        """Pure validator: ``(code, error)``.

        Verifies the echoed OAuth ``state`` matches ``nonce`` (constant-time)
        then extracts the auth ``code``. Returns ``(code, None)`` on success
        or ``(None, message)`` on any failure — no Tk, fully unit-testable.
        """
        pasted = (pasted_url or "").strip()
        if not pasted:
            return None, "Paste the redirected address from your browser first."
        if not nonce:
            return None, ("Click \"Open Schwab sign-in\" first to start a "
                          "login, then paste the redirected address.")
        echoed = extract_state(pasted)
        if echoed is None or not secrets.compare_digest(echoed, nonce):
            return None, ("Security check failed (state mismatch). This URL is "
                          "from a different or tampered login — click \"Open "
                          "Schwab sign-in\" to start a fresh one.")
        try:
            code = extract_code(pasted)
        except ValueError as exc:
            return None, str(exc)
        return code, None

    def _on_connect(self) -> None:
        if self._exchange_thread is not None and self._exchange_thread.is_alive():
            return  # already exchanging
        code, error = self._verify_and_extract(
            self._paste_var.get(), self._state_nonce)
        if error is not None:
            self._set_progress(error)
            return
        creds = self._creds()
        redirect_uri = (self._redirect_uri or creds.redirect_uri
                        or _DEFAULT_REDIRECT_URI)
        self._exchange_result = None
        try:
            self._connect_btn.configure(state="disabled")
        except tk.TclError:
            pass
        self._set_progress("Connecting to Schwab…")
        self._exchange_thread = threading.Thread(
            target=self._exchange_worker,
            args=(creds, redirect_uri, code),
            name="SchwabTokenExchange",
            daemon=True,
        )
        self._exchange_thread.start()
        self._poll_job = self.after(120, self._poll_exchange)

    def _exchange_worker(self, creds, redirect_uri: str, code: str) -> None:
        """Daemon-thread token exchange. Writes ``_exchange_result`` only —
        never touches Tk (§7.15)."""
        try:
            response = exchange_code_for_tokens(creds, redirect_uri, code)
            cache = build_token_cache(response)
            save_token_cache(cache)
            self._exchange_result = {"ok": True}
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self._exchange_result = {"ok": False, "error": str(exc)}

    def _poll_exchange(self) -> None:
        self._poll_job = None
        result = self._exchange_result
        if result is None:
            alive = (self._exchange_thread is not None
                     and self._exchange_thread.is_alive())
            if alive:
                self._poll_job = self.after(120, self._poll_exchange)
                return
            # Thread gone without a result — treat as a soft failure.
            result = {"ok": False, "error": "exchange ended unexpectedly"}
        try:
            self._connect_btn.configure(state="normal")
        except tk.TclError:
            pass
        if result.get("ok"):
            self._state_nonce = None
            self._paste_var.set("")
            self._set_progress(
                "Connected ✓ — tokens saved. Access token refreshes "
                "automatically; the refresh token lasts ~7 days.")
        else:
            self._set_progress(
                "Connection failed: " + str(result.get("error", "unknown error")))
        self._refresh_status()

    # ------------------------------------------------------------------ footer
    def _on_disconnect(self) -> None:
        if not messagebox.askyesno(
            "Disconnect Schwab",
            "Remove the saved Schwab tokens from this machine? You'll need to "
            "sign in again to reconnect.",
            parent=self,
        ):
            return
        try:
            path = token_cache_path()
            if path.exists():
                path.unlink()
        except OSError as exc:
            messagebox.showerror(
                "Disconnect Schwab",
                f"Could not remove the token cache:\n{exc}",
                parent=self,
            )
            return
        self._state_nonce = None
        self._refresh_status()
        self._set_progress("Disconnected — local tokens removed.")

    def _on_close(self) -> None:
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
        try:
            self.destroy()
        except tk.TclError:
            pass


def open_schwab_connect_dialog(parent: tk.Misc) -> SchwabConnectDialog | None:
    """Open the Schwab Connect dialog as a modal child of ``parent``.

    Returns the dialog instance (or ``None`` if Tk is unavailable).
    """
    try:
        dlg = SchwabConnectDialog(parent)
        parent.wait_window(dlg)
        return dlg
    except tk.TclError:
        return None


__all__ = ["SchwabConnectDialog", "open_schwab_connect_dialog"]
