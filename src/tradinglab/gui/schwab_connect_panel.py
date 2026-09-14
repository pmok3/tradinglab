"""Inline Schwab account authorization for the Credentials window.

The system browser owns login/MFA; a temporary HTTPS loopback listener returns
the authorization code. Tk alone publishes the resulting token cache. Manual
paste-back remains an explicit fallback, not the normal flow.
"""
from __future__ import annotations

import secrets
import threading
import tkinter as tk
import webbrowser
from collections.abc import Callable
from http.client import HTTPException
from tkinter import messagebox, ttk
from urllib.parse import urlsplit

from ..data.credentials import get_credentials
from ..data.schwab_auth import (
    TokenCacheError,
    build_token_cache,
    cache_matches_credentials,
    clear_token_cache,
    is_access_token_fresh,
    is_refresh_token_alive,
    load_token_cache,
    save_token_cache,
    schwab_failure_result,
    token_cache_generation,
)
from ..data.schwab_callback import CallbackListener, callback_response
from ..data.schwab_login import (
    build_authorize_url,
    exchange_code_for_tokens,
)
from .colors import MUTED_GREY

_DEFAULT_REDIRECT_URI = "https://127.0.0.1"


class SchwabConnectPanel(ttk.Frame):
    """Reusable account sign-in controls; no modal or application ownership."""

    def __init__(
        self, parent: tk.Misc, *,
        on_connection_changed: Callable[[], None] | None = None,
        on_prepare: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        # OAuth handshake state for the current attempt.
        self._state_nonce: str | None = None
        self._redirect_uri: str | None = None
        self._authorization_credentials: tuple[str | None, str | None, str] | None = None
        self._on_connection_changed = on_connection_changed
        self._on_prepare = on_prepare
        self._closed = False
        self._exchange_generation: int | None = None
        # Background token-exchange plumbing (§7.15: worker writes a result
        # dict, the Tk main thread polls it via ``after`` — never call
        # ``after`` from the worker).
        self._exchange_thread: threading.Thread | None = None
        self._exchange_result: dict | None = None
        self._poll_job: str | None = None
        self._callback: CallbackListener | None = None
        self._callback_job: str | None = None

        self._url_var = tk.StringVar(master=self, value="")
        self._paste_var = tk.StringVar(master=self, value="")
        self._status_var = tk.StringVar(master=self, value="")
        self._progress_var = tk.StringVar(master=self, value="")
        self._manual_var = tk.BooleanVar(master=self, value=False)

        self._build_widgets()
        self._refresh_status()
        self.bind("<Destroy>", self._on_destroy, add="+")

    # ------------------------------------------------------------------ build
    def _build_widgets(self) -> None:
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(0, weight=1)

        ttk.Label(
            frm,
            text="These app settings identify your developer application, not your "
                 "Schwab account login. Sign in on Schwab's website; TradingLab "
                 "receives the return and saves both OAuth tokens automatically.",
            wraplength=490, justify="left",
        ).grid(row=0, column=0, sticky="w")

        ttk.Label(frm, textvariable=self._status_var, foreground=MUTED_GREY,
                  wraplength=490, justify="left").grid(
            row=1, column=0, sticky="w", pady=(4, 8))

        buttons = ttk.Frame(frm)
        buttons.grid(row=2, column=0, sticky="w")
        self._open_btn = ttk.Button(
            buttons, text="Save Schwab settings & sign in" if self._on_prepare else "Sign in with Schwab",
            command=self._on_open_browser)
        self._open_btn.pack(side="left")
        ttk.Button(buttons, text="Cancel sign-in", command=self.cancel).pack(side="left", padx=4)
        ttk.Button(buttons, text="Disconnect", command=self._on_disconnect).pack(side="left")
        ttk.Label(
            frm, text="A temporary HTTPS listener runs only on this computer. Your "
            "browser may show a certificate prompt for the local callback, never "
            "for Schwab's website. No Windows trust settings are changed.",
            foreground=MUTED_GREY, wraplength=490, justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(4, 0))
        ttk.Checkbutton(frm, text="Use manual URL paste-back instead",
                        variable=self._manual_var, command=self._manual_changed).grid(
                            row=4, column=0, sticky="w", pady=(4, 0))

        self._manual_frame = ttk.Frame(frm)
        self._manual_frame.grid(row=5, column=0, sticky="ew")
        self._manual_frame.columnconfigure(0, weight=1)
        url_row = ttk.Frame(self._manual_frame)
        url_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        url_row.columnconfigure(0, weight=1)
        url_entry = ttk.Entry(url_row, textvariable=self._url_var, state="readonly")
        url_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(url_row, text="Copy URL", command=self._on_copy_url).grid(
            row=0, column=1, padx=(6, 0))

        ttk.Label(
            self._manual_frame, text="In manual mode the redirected page may be unreachable. "
            "Paste its complete address below (not your password or a token).",
            foreground=MUTED_GREY, wraplength=490, justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(2, 4))
        self._paste_entry = ttk.Entry(self._manual_frame, textvariable=self._paste_var)
        self._paste_entry.grid(row=2, column=0, sticky="ew")
        self._paste_entry.bind("<Return>", self._manual_return)
        self._paste_entry.bind("<KP_Enter>", self._manual_return)
        self._connect_btn = ttk.Button(
            self._manual_frame, text="Finish sign-in", command=self._on_connect)
        self._connect_btn.grid(row=3, column=0, sticky="w", pady=(4, 0))
        self._manual_frame.grid_remove()
        ttk.Label(frm, textvariable=self._progress_var, wraplength=490,
                  justify="left").grid(row=6, column=0, sticky="w", pady=(6, 0))

    def _manual_changed(self) -> None:
        self.cancel()
        if self._manual_var.get():
            self._manual_frame.grid()
        else:
            self._manual_frame.grid_remove()

    def _manual_return(self, _event) -> str:
        self._on_connect()
        return "break"  # Do not invoke the parent's Save & Close on other drafts.

    # ----------------------------------------------------------------- status
    @staticmethod
    def _creds():
        return get_credentials().schwab

    def _compute_status_text(self) -> str:
        creds = self._creds()
        if not creds.is_configured():
            return "Not configured — enter your developer app key, secret and registered redirect URI above."
        try:
            cache = load_token_cache()
        except TokenCacheError:
            return "Token cache unavailable — check file access/protection or reconnect."
        if not cache:
            return "Configured, not connected — sign in below to get tokens."
        if not cache_matches_credentials(cache, creds):
            return "App credentials changed — sign in again to reconnect."
        if is_access_token_fresh(cache):
            return "Connected ✓ — access token valid."
        if is_refresh_token_alive(cache):
            return "Connected ✓ — access token will auto-refresh."
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
        if self._closed or self._exchange_result is not None or self._callback is not None:
            return
        if self._on_prepare is not None and not self._on_prepare():
            return
        creds = self._creds()
        if not creds.is_configured():
            messagebox.showinfo(
                "Connect to Schwab",
                "Enter your developer App Key and App Secret in the Schwab section first.",
                parent=self,
            )
            return
        redirect_uri = creds.redirect_uri or _DEFAULT_REDIRECT_URI
        # Fresh single-use CSRF nonce per attempt; verified byte-for-byte
        # against the echoed ``state`` on the pasted redirect URL.
        state = secrets.token_urlsafe(24)
        self._state_nonce = state
        self._redirect_uri = redirect_uri
        self._authorization_credentials = (creds.app_key, creds.app_secret, redirect_uri)
        self._exchange_generation = token_cache_generation()
        url = build_authorize_url(creds.app_key or "", redirect_uri, state=state)
        self._url_var.set(url)
        if self._manual_var.get():
            self._launch_browser()
            return
        try:
            self._callback = CallbackListener(redirect_uri, state)
            self._callback.start()
        except (ValueError, OSError, RuntimeError):
            self._callback = None
            self._state_nonce = None
            self._set_progress("Cannot start automatic sign-in. Use the exact registered HTTPS loopback "
                               "URI above, or select manual URL paste-back.")
            return
        self._open_btn.configure(state="disabled")
        self._set_progress("Preparing the local HTTPS callback…")
        self._callback_job = self.after(100, self._poll_callback)

    def _launch_browser(self) -> None:
        try:
            opened = bool(webbrowser.open(self._url_var.get(), new=1, autoraise=True))
        except (webbrowser.Error, OSError):
            opened = False
        if opened:
            self._set_progress("Sign in in the browser window. " + (
                "Paste the redirected address below to finish." if self._manual_var.get()
                else f"TradingLab is waiting for the return to {self._redirect_uri}."
            ))
        else:
            self._stop_callback()
            self._set_progress("Couldn't open your browser. Select manual URL paste-back, "
                               "start sign-in again, and use Copy URL.")

    def _identity_matches(self) -> bool:
        creds = self._creds()
        return self._authorization_credentials == (
            creds.app_key, creds.app_secret, creds.redirect_uri or _DEFAULT_REDIRECT_URI
        ) and self._exchange_generation == token_cache_generation()

    def _poll_callback(self) -> None:
        self._callback_job = None
        if self._closed or self._callback is None:
            return
        if not self._identity_matches():
            self.cancel()
            self._set_progress("App settings or authorization changed. Start a fresh sign-in.")
            return
        event = self._callback.poll()
        if event is not None:
            if event.kind == "ready":
                self._launch_browser()
            elif event.kind == "authorized":
                self._stop_callback()
                self._begin_exchange(event.code)
                return
            else:
                self._stop_callback()
                self._state_nonce = None
                self._set_progress(event.message)
                return
        if self._callback is not None:
            self._callback_job = self.after(100, self._poll_callback)

    def _stop_callback(self) -> None:
        if self._callback is not None:
            self._callback.close()
            self._callback = None
        if self._callback_job is not None:
            self.after_cancel(self._callback_job)
            self._callback_job = None
        self._open_btn.configure(state="normal")

    def _on_copy_url(self) -> None:
        url = self._url_var.get()
        if not url:
            self._set_progress("Click Sign in first to "
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
            return None, "Click Sign in first, then paste the redirected address."
        try:
            response = callback_response(urlsplit(pasted).query, nonce)
        except ValueError as exc:
            return None, str(exc)
        return (response.code, None) if response.kind == "authorized" else (None, response.message)

    def _on_connect(self) -> None:
        if self._closed or self._exchange_result is not None:
            return  # already exchanging
        code, error = self._verify_and_extract(
            self._paste_var.get(), self._state_nonce)
        if error is not None:
            self._set_progress(error)
            return
        try:
            actual, expected = urlsplit(self._paste_var.get().strip()), urlsplit(self._redirect_uri or "")
            if (actual.scheme, actual.hostname, actual.port or 443, actual.path or "/") != (
                expected.scheme, expected.hostname, expected.port or 443, expected.path or "/"
            ) or actual.username is not None or actual.fragment:
                raise ValueError
        except ValueError:
            self._set_progress("The returned address does not match this sign-in's registered redirect URI.")
            return
        self._stop_callback()
        self._begin_exchange(code)

    def _begin_exchange(self, code: str) -> None:
        if not self._identity_matches():
            self.cancel()
            self._set_progress("App settings or authorization changed — start a fresh sign-in.")
            return
        creds = self._creds()
        redirect_uri = self._redirect_uri or _DEFAULT_REDIRECT_URI
        self._exchange_result = {}
        self._state_nonce = None
        self._paste_var.set("")
        try:
            self._connect_btn.configure(state="disabled")
            self._open_btn.configure(state="disabled")
        except tk.TclError:
            pass
        self._set_progress("Connecting to Schwab…")
        self._exchange_thread = threading.Thread(
            target=self._exchange_worker,
            args=(creds, redirect_uri, code, self._exchange_result),
            name="SchwabTokenExchange",
            daemon=True,
        )
        self._exchange_thread.start()
        self._poll_job = self.after(120, self._poll_exchange)

    @staticmethod
    def _exchange_worker(creds, redirect_uri: str, code: str, result: dict) -> None:
        """Only publish into this attempt's dict; no Tk object or disk writes."""
        try:
            response = exchange_code_for_tokens(creds, redirect_uri, code)
            cache = build_token_cache(response, creds=creds)
            result.update({"ok": True, "cache": cache})
        except (OSError, HTTPException, ValueError, TypeError, KeyError, OverflowError, TokenCacheError) as exc:
            result.update({"ok": False, "error": schwab_failure_result(exc).summary})

    def _poll_exchange(self) -> None:
        self._poll_job = None
        if self._closed or self._exchange_result is None:
            return
        result = self._exchange_result
        if not result:
            alive = (self._exchange_thread is not None
                     and self._exchange_thread.is_alive())
            if alive:
                self._poll_job = self.after(120, self._poll_exchange)
                return
            # Thread gone without a result — treat as a soft failure.
            result = {"ok": False, "error": "exchange ended unexpectedly"}
        self._exchange_result = None
        try:
            self._connect_btn.configure(state="normal")
            self._open_btn.configure(state="normal")
        except tk.TclError:
            pass
        if result.get("ok"):
            cache = result["cache"]
            if not self._identity_matches() or not cache_matches_credentials(cache, self._creds()):
                self._set_progress("App credentials changed — tokens were not saved. Sign in again.")
                return
            try:
                save_token_cache(cache, expected_generation=self._exchange_generation)
            except TokenCacheError as exc:
                self._set_progress("Connection not saved: " + schwab_failure_result(exc).summary)
                self._refresh_status()
                return
            self._set_progress(
                "Connected ✓ — tokens saved. Access token refreshes "
                "automatically; the refresh token lasts ~7 days.")
            self._notify_connection_changed()
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
        self.cancel()
        try:
            clear_token_cache()
        except TokenCacheError:
            messagebox.showerror(
                "Disconnect Schwab",
                "Could not remove all token files. Check file access and try again.",
                parent=self,
            )
            return
        self._state_nonce = None
        self._refresh_status()
        self._set_progress("Disconnected — local tokens removed.")
        self._notify_connection_changed()

    def _notify_connection_changed(self) -> None:
        if self._on_connection_changed is not None:
            try:
                self._on_connection_changed()
            except Exception:  # noqa: BLE001 - external UI hook; persisted auth already succeeded
                self._set_progress("Tokens updated, but the live connection could not be updated. Restart the app.")

    def cancel(self) -> None:
        if self._closed:
            return
        self._stop_callback()
        self._cancel_exchange()
        self._set_progress("Sign-in cancelled. Saved tokens are unchanged.")

    def _cancel_exchange(self) -> None:
        self._exchange_result = None
        self._state_nonce = None
        self._authorization_credentials = None
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
        self._paste_var.set("")
        self._url_var.set("")
        self._connect_btn.configure(state="normal")
        self._open_btn.configure(state="normal")

    def destroy(self) -> None:
        if not self._closed:
            self._stop_callback()
            self._cancel_exchange()
            self._closed = True
        super().destroy()

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            self._closed = True
            if self._callback is not None:
                self._callback.close()
            self._exchange_result = None
            for job in (self._poll_job, self._callback_job):
                if job is not None:
                    try:
                        self.after_cancel(job)
                    except tk.TclError:
                        pass
            self._poll_job = self._callback_job = None


__all__ = ["SchwabConnectPanel"]
