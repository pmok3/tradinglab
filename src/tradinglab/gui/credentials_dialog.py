"""Credentials configuration dialog (DPAPI-backed on Windows).

Replaces the dev-only flow of hand-editing ``.env``. End users open
Help \u2192 "Configure Credentials…" and fill in three sections —
Schwab, Alpaca, Polygon — with hide/show password fields. On save:

* **Windows**: serialize to JSON, DPAPI-encrypt with the current
  user's master key, atomic-write to
  ``%LOCALAPPDATA%\\TradingLab\\credentials.dat``.
* **macOS / Linux**: refuse to persist (we don't implement Keychain
  / libsecret in this iteration) and inform the user. They can
  still configure via env vars / dotenv on dev installs.

The dialog never persists plaintext to disk. Loading on next launch
reads the DPAPI blob, decrypts, and injects into ``os.environ``
**before** :func:`tradinglab.data.credentials.get_credentials`
gets its first call — see :func:`prime_environment_from_dpapi`.

Why ``os.environ`` injection
----------------------------
The existing :mod:`tradinglab.data.credentials` module reads
env vars + dotenv. Injecting DPAPI-decrypted values into
``os.environ`` before any vendor module imports keeps the
"credentials live as env vars at runtime" contract intact, with
zero changes to call sites (Schwab / Alpaca / Polygon constructors
all read ``os.environ.get(...)``). The trade-off: a crash dump
that captures the process environment can leak the secret; we
accept this because (a) DPAPI already prevents persistence leaks,
and (b) every Python process has the same exposure when env vars
hold secrets.
"""
from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk
from typing import Any

from ..data import verify
from ._modal_base import (
    BaseModalDialog,
    make_scrollable_form,
    protect_combobox_wheel,
)
from .colors import ERROR_RED, MUTED_GREY, OK_GREEN, WARN_AMBER

# Map (env_var -> dialog field). The label is what the user sees;
# ``is_secret`` controls whether the entry uses ``show="*"``.
_FIELDS = [
    # Schwab
    ("SCHWAB_APP_KEY",       "Schwab App Key",       True),
    ("SCHWAB_APP_SECRET",    "Schwab App Secret",    True),
    ("SCHWAB_REDIRECT_URI",  "Schwab Redirect URI",  False),
    # Alpaca
    ("ALPACA_API_KEY_ID",    "Alpaca API Key ID",    True),
    ("ALPACA_API_SECRET_KEY","Alpaca API Secret Key",True),
    ("ALPACA_TIER",          "Alpaca data plan",     False),
    ("ALPACA_ADJUSTMENT",    "Alpaca Adjustment (raw / split / all)", False),
    # Polygon
    ("POLYGON_API_KEY",      "Polygon API Key",      True),
]


# Fields rendered as a constrained read-only DROPDOWN instead of a free-text
# entry. Maps env var → ordered list of (display label, stored value). The
# Alpaca plan selector is the single source of truth for the feed + rate
# budget (see data.credentials / data.alpaca_source): ``free`` → IEX + 200
# req/min (real-time delayed 15 min — no live updates), ``paid`` → SIP +
# unlimited req/min (real-time). Replaces the old free-text
# ``ALPACA_FEED`` field so plan and feed can't disagree (the #1 misconfig:
# paid+iex → silently partial volume; free+sip → 403s). Chosen over a
# checkbox per the tier-UX council.
_CHOICE_FIELDS: dict[str, list[tuple[str, str]]] = {
    "ALPACA_TIER": [
        ("Free — IEX feed (15-min delayed), 200 req/min", "free"),
        ("Paid — SIP feed (real-time), unlimited req/min", "paid"),
    ],
}

# Muted helper text rendered under a choice field.
_CHOICE_HELP: dict[str, str] = {
    "ALPACA_TIER": (
        "Paid uses full-volume real-time SIP data with unlimited requests. "
        "Free uses IEX only (200 req/min) and its real-time data is delayed "
        "15 minutes — so the chart won't live-update on Free, and volume "
        "indicators (RVOL/RRVOL) may be understated."
    ),
}


# Dialog sections: ``(env-var prefix, section heading, vendor key)``.
# Single source of truth for the section headers, for which sections get a
# "Test connection" row, and for the field-edit → invalidate-stale-result
# binding. The vendor key must match the name a source registers with
# :func:`tradinglab.data.verify.register_verifier`.
_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("SCHWAB_",  "Schwab",  "schwab"),
    ("ALPACA_",  "Alpaca",  "alpaca"),
    ("POLYGON_", "Polygon", "polygon"),
)

#: Verification status → (glyph, colour) for the result line. ``forbidden``
#: / ``rate_limited`` / ``network_error`` render **amber, not red**: in all
#: three the credentials themselves may be perfectly fine, and a red ✗ would
#: send the user off re-copying a key that was never the problem.
_STATUS_STYLE: dict[str, tuple[str, str]] = {
    verify.STATUS_OK:                  ("\u2713", OK_GREEN),
    verify.STATUS_NOT_CONFIGURED:      ("\u2014", MUTED_GREY),
    verify.STATUS_INVALID_CREDENTIALS: ("\u2717", ERROR_RED),
    verify.STATUS_FORBIDDEN:           ("\u26a0", WARN_AMBER),
    verify.STATUS_RATE_LIMITED:        ("\u26a0", WARN_AMBER),
    verify.STATUS_NETWORK_ERROR:       ("\u26a0", WARN_AMBER),
    verify.STATUS_UNSUPPORTED:         ("\u2014", MUTED_GREY),
    verify.STATUS_ERROR:               ("\u2717", ERROR_RED),
}

#: Tk poll interval for the verification worker, in ms. Mirrors the
#: strategy-tester export poll. See :meth:`CredentialsDialog._poll_verify`
#: for why we poll instead of calling ``after(0, ...)`` from the worker.
_VERIFY_POLL_MS = 100


def _vendor_for_env(env_name: str) -> str | None:
    """Vendor key owning ``env_name`` (``"ALPACA_API_KEY_ID"`` → ``"alpaca"``)."""
    for prefix, _section, vendor in _SECTIONS:
        if env_name.startswith(prefix):
            return vendor
    return None


def _managed_env_names() -> tuple[str, ...]:
    """Every env var this dialog owns.

    Used to **clear** the variables a user emptied. ``_collect`` only reports
    non-empty fields, so without an explicit managed list a cleared field
    would leave its stale `os.environ` entry behind — and since
    ``credentials._resolve`` consults `os.environ` first, the deleted key
    would keep resolving for the rest of the session.
    """
    return tuple(env_name for env_name, _label, _secret in _FIELDS)


def _has_credential_values(values: dict[str, str]) -> bool:
    """True if ``values`` holds any real credential (not just a dropdown).

    Choice fields (`ALPACA_TIER`) are read-only combos that ALWAYS report a
    value, so a plain ``if not values`` emptiness test can never be true and
    the "this clears your saved credentials" confirmation would never fire.
    """
    return any(k not in _CHOICE_FIELDS for k in values)


def _visible_fields() -> list[tuple[str, str, bool]]:
    """Return the credential fields to actually render in the dialog.

    Schwab fields are surfaced **unconditionally** so a user wiring up
    the integration can stash their App Key / Secret / Redirect URI
    ahead of the data fetcher landing. The
    :data:`tradinglab.data.schwab_source.SCHWAB_REGISTRATION_ENABLED`
    flag still gates whether the Schwab source is actually registered
    with the data layer; the credentials UI is just persistence — so
    saving them on a build that hasn't shipped the OAuth flow yet is
    harmless (the values sit in the DPAPI blob until the source
    starts reading them).
    """
    return list(_FIELDS)


def _credentials_path():
    """Resolve the DPAPI blob path lazily so tests can monkeypatch ``paths``."""
    from .. import paths as _paths
    return _paths.app_data_dir() / "credentials.dat"


# ---------------------------------------------------------------------------
# Environment priming (called by main() before credentials.get_credentials())
# ---------------------------------------------------------------------------


def prime_environment_from_dpapi() -> str:
    """Load DPAPI-stored credentials into :data:`os.environ`. No-op on non-Windows.

    Called from :func:`tradinglab.app.main` after the GUI mainloop
    starts up but BEFORE any vendor module reads credentials. Existing
    ``os.environ`` values are NOT overwritten — a shell ``$env:`` set
    in front of the launcher still wins, mirroring the dotenv contract.

    Returns a string sentinel describing the outcome so the caller
    can distinguish "boring miss" (first launch, no blob yet) from
    "suspicious" (blob is on disk but failed to decrypt — possibly
    tampered with or copied from a different machine):

    * ``"loaded"`` — blob decrypted and at least one env var was
      injected. Steady-state on every subsequent launch.
    * ``"missing"`` — no blob on disk yet. Normal on first launch
      and after the user clears credentials.
    * ``"dpapi_unavailable"`` — running on a platform without DPAPI
      (macOS / Linux). The user falls back to env-var-only mode.
    * ``"decrypt_error"`` — blob is present on disk but
      :func:`_dpapi.unprotect` rejected it. This is suspicious and
      the caller should surface it on the status bar.
    * ``"io_error"`` — could not read the blob file (permission /
      transient disk error). Treated like ``decrypt_error`` for
      reporting purposes.
    * ``"import_error"`` — :mod:`tradinglab._dpapi` could not be
      imported. Shouldn't happen in a packaged build but kept for
      defense in depth.

    Note: The pre-refactor signature returned ``bool``; tests that
    relied on truthy / falsy still work because every value except
    ``"loaded"`` is falsy via string truthiness only when compared
    to an empty string, and ``"loaded" == "loaded"`` is the explicit
    success check. Update tests accordingly.
    """
    try:
        from .. import _dpapi
    except ImportError:
        return "import_error"
    if not _dpapi.is_available():
        return "dpapi_unavailable"
    try:
        data = _dpapi.load_secrets_dict(_credentials_path())
    except _dpapi.DpapiError:
        return "decrypt_error"
    except OSError:
        return "io_error"
    if data is None:
        # ``load_secrets_dict`` returns ``None`` when the blob file
        # does not exist (first launch). An empty ``{}`` means the
        # file is present but encoded an empty mapping — treat that
        # the same as "no work to do" since nothing actionable is
        # there to inject.
        return "missing"
    if not data:
        return "missing"
    injected = 0
    for env_name, value in data.items():
        if not isinstance(env_name, str) or not env_name:
            continue
        if env_name in os.environ and os.environ.get(env_name):
            continue
        os.environ[env_name] = str(value)
        injected += 1
    return "loaded" if injected > 0 else "missing"


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class CredentialsDialog(BaseModalDialog):
    """Modal dialog with one row per credential field.

    Layout:
        ┌──────────────────────────────────────────────┐
        │ Section: Schwab                              │
        │   App Key      [................] [show]    │
        │   App Secret   [................] [show]    │
        │   Redirect URI [..............]              │
        │ Section: Alpaca                              │
        │   ...                                        │
        │ Section: Polygon                             │
        │   ...                                        │
        │                                              │
        │ (status / error line)                        │
        │                                              │
        │            [ Save & Close ] [ Cancel ]       │
        └──────────────────────────────────────────────┘
    """

    def __init__(
        self, parent: tk.Misc,
        on_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            parent,
            title="Configure Credentials",
            geometry_key="dlg.credentials",
            default_geometry="600x760",
            resizable=(True, True),
        )

        self._on_changed = on_changed
        self._entries: dict[str, tk.Entry] = {}
        # Field textvariables MUST be kept referenced. A ttk widget stores
        # only the Tcl variable *name*; if the Python ``StringVar`` is
        # collected, ``Variable.__del__`` unsets the Tcl variable and
        # silently destroys every trace on it — so the stale-verdict
        # invalidation would stop firing at an unpredictable GC boundary.
        self._field_vars: dict[str, tk.StringVar] = {}
        self._show_vars: dict[str, tk.BooleanVar] = {}
        # For dropdown (choice) fields: display↔stored-value maps.
        self._choice_value_by_display: dict[str, dict[str, str]] = {}
        self._choice_display_by_value: dict[str, dict[str, str]] = {}
        # Per-vendor "Test connection" state. ``_verify_boxes`` holds the
        # worker→Tk handoff dict for each in-flight probe (see _begin_verify).
        self._verify_buttons: dict[str, ttk.Button] = {}
        self._verify_status_vars: dict[str, tk.StringVar] = {}
        self._verify_status_labels: dict[str, ttk.Label] = {}
        self._verify_detail_vars: dict[str, tk.StringVar] = {}
        self._verify_boxes: dict[str, dict[str, Any]] = {}
        self._verify_threads: dict[str, threading.Thread] = {}
        self._verify_jobs: dict[str, str] = {}
        # Set while pre-filling entries so the textvariable traces don't
        # read a programmatic populate as a user edit.
        self._populating = False
        self._initial_values: dict[str, str] = {}
        self._form_canvas: tk.Canvas | None = None
        self._sources_before: tuple[str, ...] = self._current_sources()
        self._build_widgets()
        self._populate_from_environment()
        # §7.11: the form scrolls AND contains a Combobox, so wheel events
        # must be forwarded to the canvas instead of silently rotating the
        # Alpaca plan selector under the cursor.
        protect_combobox_wheel(self, scroll_target=self._form_canvas)
        self.bind("<Destroy>", self._on_destroy, add="+")
        # Guarantee the window can never open smaller than its content. The
        # dialog packs three sections (8 fields + a dropdown-with-help + a
        # multi-line status line + buttons) that overflowed the old fixed
        # 560x420 non-resizable window — the bottom (Polygon field, status,
        # buttons) was clipped with no way to enlarge. Deriving ``minsize``
        # from the *actual* laid-out request size makes it self-correcting
        # under any font / DPI scaling (Windows-on-ARM display scaling in
        # particular), and the WM clamps a stale-small persisted
        # ``dlg.credentials`` geometry back up to it. Resizable so the user
        # can still grow the window. Mirrors ``sandbox_dialog`` (see its
        # spec.md "Sizing" note). A small margin absorbs border/rounding.
        try:
            self.update_idletasks()
            req_w = self.winfo_reqwidth()
            req_h = self.winfo_reqheight()
            self.minsize(max(540, req_w + 16), max(480, req_h + 16))
        except tk.TclError:
            pass
        self._finalize_modal(primary=self._on_save, cancel=self._on_cancel)

    def _build_widgets(self) -> None:
        # Scrollable body. Three vendor sections plus the per-vendor
        # "Test connection" rows push the dialog past the small-laptop
        # safe height, so the form must scroll rather than clip its Save
        # button off-screen (pinned by tests/unit/gui/
        # test_dialog_scrollable_meta.py). The button row grids INSIDE the
        # scrollable frame, matching universe_prepare_dialog.
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)
        frm, self._form_canvas = make_scrollable_form(container)
        frm.configure(padding=12)

        # Section headers keyed by env-var prefix so we don't hardcode
        # the section boundary positions.
        section_for_prefix = {p: s for p, s, _v in _SECTIONS}
        last_section = None
        row = 0
        for env_name, label, is_secret in _visible_fields():
            section = next((v for k, v in section_for_prefix.items()
                            if env_name.startswith(k)), "")
            if section != last_section:
                if last_section is not None:
                    # Close out the previous vendor with its test row before
                    # the separator, so the control sits under the fields it
                    # actually verifies.
                    row = self._add_verify_row(frm, row, last_section)
                    ttk.Separator(frm, orient="horizontal").grid(
                        row=row, column=0, columnspan=3, sticky="ew", pady=(6, 6))
                    row += 1
                ttk.Label(frm, text=section, font=("TkDefaultFont", 10, "bold")
                          ).grid(row=row, column=0, columnspan=3,
                                 sticky="w", pady=(2, 4))
                row += 1
                last_section = section

            ttk.Label(frm, text=label + ":").grid(
                row=row, column=0, sticky="e", padx=(0, 6), pady=2)

            # Constrained dropdown fields (e.g. the Alpaca plan selector).
            choices = _CHOICE_FIELDS.get(env_name)
            if choices:
                displays = [d for d, _v in choices]
                var = tk.StringVar(master=self)
                combo = ttk.Combobox(frm, width=40, values=displays,
                                     state="readonly", textvariable=var)
                combo.grid(row=row, column=1, columnspan=2, sticky="we", pady=2)
                self._entries[env_name] = combo
                self._bind_invalidate(env_name, var)
                self._choice_value_by_display[env_name] = {
                    d: v for d, v in choices}
                self._choice_display_by_value[env_name] = {
                    v: d for d, v in choices}
                row += 1
                help_txt = _CHOICE_HELP.get(env_name)
                if help_txt:
                    ttk.Label(frm, text=help_txt, foreground=MUTED_GREY,
                              wraplength=360, justify="left").grid(
                                  row=row, column=1, columnspan=2, sticky="w",
                                  pady=(0, 4))
                    row += 1
                continue

            var = tk.StringVar(master=self)
            entry = ttk.Entry(frm, width=42, textvariable=var)
            if is_secret:
                entry.configure(show="*")
            entry.grid(row=row, column=1, sticky="we", pady=2)
            self._entries[env_name] = entry
            self._bind_invalidate(env_name, var)

            if is_secret:
                show_var = tk.BooleanVar(master=self, value=False)
                self._show_vars[env_name] = show_var
                def _toggle(_e=entry, _v=show_var):
                    _e.configure(show="" if _v.get() else "*")
                ttk.Checkbutton(frm, text="show", variable=show_var,
                                command=_toggle).grid(
                                    row=row, column=2, sticky="w",
                                    padx=(6, 0), pady=2)
            row += 1

        # Test row for the final section (the loop only closes a section
        # when the NEXT one starts).
        row = self._add_verify_row(frm, row, last_section)

        # Status label.
        self._status_var = tk.StringVar(master=self, value=self._initial_status_text())
        ttk.Label(frm, textvariable=self._status_var, foreground=MUTED_GREY,
                  wraplength=520, justify="left"
                  ).grid(row=row, column=0, columnspan=3, sticky="w",
                         pady=(8, 4))
        row += 1

        # Button row.
        btn_frame = ttk.Frame(frm)
        btn_frame.grid(row=row, column=0, columnspan=3, sticky="e",
                       pady=(6, 0))
        ttk.Button(btn_frame, text="Cancel", command=self._on_cancel
                   ).pack(side="right", padx=(6, 0))
        ttk.Button(btn_frame, text="Save & Close", command=self._on_save
                   ).pack(side="right")

    def _initial_status_text(self) -> str:
        if sys.platform == "win32":
            try:
                from .. import _dpapi
                if _dpapi.is_available():
                    return ("Values are encrypted with your Windows user account "
                            "(DPAPI) and stored under TradingLab\\credentials.dat.")
            except Exception:  # noqa: BLE001
                pass
        return ("Persistent credential storage is only implemented on Windows. "
                "Values are kept in-process only.")

    # ---- verification ("Test connection") ------------------------------

    def _add_verify_row(
        self, frm: ttk.Frame, row: int, section: str | None,
    ) -> int:
        """Append ``section``'s "Test connection" block. Returns the next row.

        No-op for a vendor without a registered verifier (Schwab today), so
        the dialog never offers a button that cannot answer.
        """
        vendor = next(
            (v for _p, s, v in _SECTIONS if s == section), None)
        if vendor is None or not verify.has_verifier(vendor):
            return row

        bar = ttk.Frame(frm)
        bar.grid(row=row, column=0, columnspan=3, sticky="we", pady=(4, 0))
        btn = ttk.Button(bar, text="Test connection",
                         command=lambda v=vendor: self._begin_verify(v))
        btn.pack(side="left")
        self._verify_buttons[vendor] = btn

        status_var = tk.StringVar(master=self, value="Not tested yet.")
        status_lbl = ttk.Label(bar, textvariable=status_var,
                               foreground=MUTED_GREY, wraplength=380,
                               justify="left")
        status_lbl.pack(side="left", padx=(8, 0))
        self._verify_status_vars[vendor] = status_var
        self._verify_status_labels[vendor] = status_lbl
        row += 1

        detail_var = tk.StringVar(master=self, value="")
        ttk.Label(frm, textvariable=detail_var, foreground=MUTED_GREY,
                  wraplength=520, justify="left").grid(
                      row=row, column=0, columnspan=3, sticky="w",
                      padx=(4, 0), pady=(0, 2))
        self._verify_detail_vars[vendor] = detail_var
        row += 1
        return row

    def _bind_invalidate(self, env_name: str, var: tk.StringVar) -> None:
        """Reset a vendor's verdict whenever one of its fields changes.

        Without this, a green "✓ Ready" would linger after the user pastes a
        different key — the single most misleading state this dialog could
        show.

        Traces the field's ``textvariable`` rather than binding
        ``<KeyRelease>``: a keyboard binding misses right-click → Paste (no
        key event fires at all) and programmatic updates, which is exactly
        how a user swaps in a fresh key copied off a vendor dashboard.

        The variable is retained in ``_field_vars`` for ALL fields, not just
        traced ones — see the note in ``__init__`` on GC destroying traces.
        """
        self._field_vars[env_name] = var
        vendor = _vendor_for_env(env_name)
        if vendor is None:
            return
        var.trace_add(
            "write", lambda *_a, v=vendor: self._invalidate_verify(v))

    def _invalidate_verify(self, vendor: str) -> None:
        """Drop a stale verdict after an edit (no-op while a probe is live)."""
        if self._populating:
            return
        if self._verify_boxes.get(vendor, {}).get("inflight"):
            return
        if vendor not in self._verify_status_vars:
            return
        self._set_verify_text(vendor, "Not tested yet.", MUTED_GREY, "")

    def _set_verify_text(
        self, vendor: str, text: str, color: str, detail: str,
    ) -> None:
        """Write the status + detail lines for ``vendor`` (Tk thread only)."""
        var = self._verify_status_vars.get(vendor)
        if var is not None:
            var.set(text)
        lbl = self._verify_status_labels.get(vendor)
        if lbl is not None:
            try:
                lbl.configure(foreground=color)
            except tk.TclError:
                pass
        detail_var = self._verify_detail_vars.get(vendor)
        if detail_var is not None:
            detail_var.set(detail)

    def _vendor_credentials_from_form(self, vendor: str) -> Any:
        """Build ``vendor``'s credential object from what is TYPED right now.

        Deliberately not ``get_credentials()``: the user must be able to
        paste a key, test it, fix a typo and re-test **without** committing
        anything to the DPAPI blob or mutating ``os.environ``. Goes through
        the shared :func:`~tradinglab.data.credentials.build_credentials`
        so the probe can't derive a different feed than the app will use.
        """
        from ..data.credentials import build_credentials
        values = self._collect()
        return getattr(build_credentials(values.get), vendor, None)

    def _begin_verify(self, vendor: str) -> None:
        """Kick off ``vendor``'s probe on a worker thread."""
        box = self._verify_boxes.get(vendor)
        if box is not None and box.get("inflight"):
            return

        creds = self._vendor_credentials_from_form(vendor)
        if creds is None:
            self._set_verify_text(
                vendor, "\u2014 No test available.", MUTED_GREY, "")
            return
        if not creds.is_configured():
            result = verify.not_configured(vendor)
            self._render_verify_result(vendor, result)
            return

        box = {"inflight": True, "done": False, "result": None}
        self._verify_boxes[vendor] = box
        btn = self._verify_buttons.get(vendor)
        if btn is not None:
            try:
                btn.configure(state="disabled", text="Testing…")
            except tk.TclError:
                pass
        self._set_verify_text(vendor, "Testing…", MUTED_GREY, "")

        def _work() -> None:
            # Runs OFF the Tk thread: touches nothing but the local box.
            try:
                box["result"] = verify.verify_vendor(vendor, creds)
            except BaseException as exc:  # noqa: BLE001 — never lose the box
                box["result"] = verify.result_from_exception(
                    exc, vendor=vendor)
            finally:
                box["done"] = True

        thread = threading.Thread(
            target=_work, name=f"CredVerify{vendor.title()}", daemon=True)
        self._verify_threads[vendor] = thread
        thread.start()
        self._schedule_verify_poll(vendor)

    def _schedule_verify_poll(self, vendor: str) -> None:
        try:
            self._verify_jobs[vendor] = self.after(
                _VERIFY_POLL_MS, lambda v=vendor: self._poll_verify(v))
        except tk.TclError:  # dialog already tearing down
            pass

    def _poll_verify(self, vendor: str) -> None:
        """Tk-thread poll for a finished probe.

        **Do not** replace this with ``self.after(0, ...)`` from the worker.
        Stock CPython on Windows ships a non-threaded Tcl; a cross-thread
        ``after`` raises ``RuntimeError("main thread is not in main loop")``
        and the callback is silently dropped, leaving the button stuck on
        "Testing…" forever. Same contract as the strategy-tester export
        poll — see ``gui/strategy_tab.py``.
        """
        self._verify_jobs.pop(vendor, None)
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return

        box = self._verify_boxes.get(vendor)
        if box is None:
            return
        if not box.get("done"):
            thread = self._verify_threads.get(vendor)
            if thread is not None and thread.is_alive():
                self._schedule_verify_poll(vendor)
                return
            # Worker vanished without publishing a result — don't hang the UI.
            box["result"] = verify.VerifyResult(
                status=verify.STATUS_ERROR, vendor=vendor,
                summary="The connection test stopped unexpectedly.")
            box["done"] = True

        box["inflight"] = False
        result = box.get("result")
        if result is not None:
            verify.record_result(result)
            self._render_verify_result(vendor, result)

    def _render_verify_result(
        self, vendor: str, result: verify.VerifyResult,
    ) -> None:
        """Paint a :class:`VerifyResult` into the vendor's status rows."""
        btn = self._verify_buttons.get(vendor)
        if btn is not None:
            try:
                btn.configure(state="normal", text="Test connection")
            except tk.TclError:
                pass
        glyph, color = _STATUS_STYLE.get(
            result.status, ("\u2014", MUTED_GREY))
        text = f"{glyph} {result.summary}"
        if result.latency_ms is not None and result.ok:
            text += f"  ({result.latency_ms:.0f} ms)"
        self._set_verify_text(vendor, text, color, result.detail)

    def _on_destroy(self, event: object = None) -> None:
        """Cancel pending poll jobs so a mid-probe close doesn't TclError.

        The worker threads are daemons writing only to their own dict, so
        they are safe to abandon — nothing they touch outlives this dialog.
        """
        if getattr(event, "widget", self) is not self:
            return  # child-widget <Destroy> bubbling up
        for job in list(self._verify_jobs.values()):
            try:
                self.after_cancel(job)
            except (tk.TclError, ValueError):
                pass
        self._verify_jobs.clear()

    # ---- helpers -------------------------------------------------------

    def _populate_from_environment(self) -> None:
        """Pre-fill entries from current ``os.environ`` so existing values are visible."""
        self._populating = True
        try:
            self._populate_fields()
        finally:
            self._populating = False
        # Snapshot so we can tell "user cleared this" from "it opened blank".
        self._initial_values = {
            name: entry.get().strip() for name, entry in self._entries.items()
        }

    def _populate_fields(self) -> None:
        for env_name, entry in self._entries.items():
            if env_name in self._choice_value_by_display:
                # Dropdown: map the stored env value → its display; default to
                # the first (safe) choice when unset/unrecognised.
                current = (os.environ.get(env_name, "") or "").lower()
                display = self._choice_display_by_value[env_name].get(current)
                if display is None:
                    display = next(iter(self._choice_value_by_display[env_name]))
                entry.set(display)  # ttk.Combobox
                continue
            current = os.environ.get(env_name, "")
            if current:
                entry.delete(0, tk.END)
                entry.insert(0, current)

    def _collect(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for env_name, entry in self._entries.items():
            value = entry.get().strip()
            if env_name in self._choice_value_by_display:
                # Map the selected display label back to its stored value.
                value = self._choice_value_by_display[env_name].get(value, "")
            if value:
                out[env_name] = value
        return out

    # ---- actions -------------------------------------------------------

    def _apply_to_environment(self, values: dict[str, str]) -> None:
        """Push ``values`` into ``os.environ`` **and remove what was cleared**.

        The removal half is essential and easy to miss: ``_collect`` only
        reports non-empty fields, and
        :func:`tradinglab.data.credentials._resolve` consults ``os.environ``
        before any file. So a key the user just deleted would keep resolving
        from the stale environment entry for the rest of the session — the
        source would stay in the dropdown and keep making authenticated
        requests with a credential the user believes they revoked.
        """
        for name in _managed_env_names():
            if name in values:
                os.environ[name] = values[name]
            else:
                os.environ.pop(name, None)

    def _on_save(self) -> None:
        values = self._collect()
        if not _has_credential_values(values):
            confirm = messagebox.askyesno(
                "Configure Credentials",
                "All credential fields are empty. Save an empty "
                "configuration (this clears any previously-saved "
                "credentials and removes their data sources)?",
                parent=self,
            )
            if not confirm:
                return

        if sys.platform != "win32":
            messagebox.showinfo(
                "Configure Credentials",
                "Persistent credential storage is only implemented on Windows "
                "in this build. The values you entered are now active for this "
                "session only (no on-disk persistence).",
                parent=self,
            )
            self._apply_to_environment(values)
            self._close_and_refresh()
            return
        try:
            from .. import _dpapi
        except ImportError as e:
            messagebox.showerror("Configure Credentials",
                                 f"Encryption module unavailable: {e}",
                                 parent=self)
            return
        if not _dpapi.is_available():
            messagebox.showerror(
                "Configure Credentials",
                "Windows DPAPI is unavailable on this host.",
                parent=self,
            )
            return

        try:
            _dpapi.save_secrets_dict(_credentials_path(), values)
        except _dpapi.DpapiError as e:
            messagebox.showerror(
                "Configure Credentials",
                f"Could not encrypt credentials:\n{e}",
                parent=self,
            )
            return
        except OSError as e:
            messagebox.showerror(
                "Configure Credentials",
                f"Could not write credentials file:\n{e}",
                parent=self,
            )
            return

        # Apply to current process too so the user doesn't have to
        # restart for the new values to take effect.
        self._apply_to_environment(values)
        self._close_and_refresh()

    def _current_sources(self) -> tuple[str, ...]:
        """User-visible source keys right now (``()`` if the registry errors)."""
        try:
            from ..data import user_visible_sources
            return tuple(user_visible_sources())
        except Exception:  # noqa: BLE001
            return ()

    def _report_source_changes(
        self, gained: list[str], lost: list[str],
    ) -> None:
        """Name the sources that just became (un)available. Silent if none.

        This is the concrete "your data source is ready for use" signal: it
        names the exact entries that just appeared in the toolbar's
        source dropdown, so the user knows the keys took effect AND where to
        go next. Only fires on a real change, so a no-op save never nags.
        """
        if not gained and not lost:
            return
        parts: list[str] = []
        if gained:
            parts.append(
                "These data sources are now ready to use — pick one from "
                "the source dropdown in the toolbar:\n  \u2022 "
                + "\n  \u2022 ".join(gained))
        if lost:
            parts.append(
                "No longer available (credentials were cleared):\n  \u2022 "
                + "\n  \u2022 ".join(lost))
        try:
            messagebox.showinfo(
                "Configure Credentials", "\n\n".join(parts), parent=self)
        except tk.TclError:
            pass

    def _warn_if_file_backed(self) -> None:
        """Explain a credential the user just cleared that is STILL active.

        ``credentials`` resolves from ``os.environ`` first, then a plaintext
        ``alpaca.txt`` / ``credentials.txt``, then a dev ``.env``. This dialog
        owns only the environment layer (and the DPAPI blob that primes it),
        so clearing a field cannot remove a key that a *file* is supplying.

        Without this message the user deletes the key, saves, and the source
        stubbornly remains — with no way to discover why. Naming the
        mechanism turns a mystery into a one-step fix.

        Fires **only when the user actually emptied a field that had
        content**. A setup where the fields were already blank at open
        (because the credential lives in a file, which
        ``_populate_from_environment`` does not read) is the steady state for
        those users — nagging them on every save would be noise.
        """
        try:
            from ..data.credentials import get_credentials
            creds = get_credentials()
        except Exception:  # noqa: BLE001
            return
        stuck: list[str] = []
        for prefix, section, vendor in _SECTIONS:
            fields = [n for n in self._entries if n.startswith(prefix)
                      and n not in _CHOICE_FIELDS]
            if not fields:
                continue
            if any(self._entries[n].get().strip() for n in fields):
                continue  # still populated — nothing was removed
            cleared = any(self._initial_values.get(n, "") for n in fields)
            if not cleared:
                continue  # already blank when the dialog opened
            vendor_creds = getattr(creds, vendor, None)
            if vendor_creds is not None and vendor_creds.is_configured():
                stuck.append(section)
        if not stuck:
            return
        try:
            messagebox.showwarning(
                "Configure Credentials",
                "These credentials are still active even though you cleared "
                "the fields:\n  \u2022 " + "\n  \u2022 ".join(stuck) + "\n\n"
                "They are being supplied by a file or a system environment "
                "variable, which this dialog cannot clear. Check for an "
                "alpaca.txt / credentials.txt next to the app or in the "
                "TradingLab data folder (Help \u2192 Reveal Data Folder), or "
                "an exported environment variable.",
                parent=self,
            )
        except tk.TclError:
            pass

    def _close_and_refresh(self) -> None:
        """Reload credentials, re-register vendor sources, then dismiss.

        The re-registration is the difference between "we saved your keys"
        and "your data source is ready to use". Vendor sources are gated on
        ``is_configured()`` inside ``tradinglab.data``, which historically
        ran at package-import time **only** — so a user who pasted a working
        Alpaca key saw nothing change until they restarted the app, with
        nothing in the UI hinting that a restart was required. Calling
        :func:`~tradinglab.data.register_vendor_sources` here closes that
        gap; ``on_changed`` then refreshes the toolbar combobox.
        """
        try:
            from ..data import credentials as _creds
            _creds.reload()
        except Exception:  # noqa: BLE001
            pass
        # Cached verdicts describe the PREVIOUS credentials — drop them so a
        # stale "verified" can't outlive the keys it was measured against.
        try:
            verify.clear_results()
        except Exception:  # noqa: BLE001
            pass
        try:
            from ..data import register_vendor_sources
            register_vendor_sources()
        except Exception:  # noqa: BLE001
            pass

        after = self._current_sources()
        gained = [s for s in after if s not in self._sources_before]
        lost = [s for s in self._sources_before if s not in after]
        self._report_source_changes(gained, lost)
        self._warn_if_file_backed()
        if self._on_changed is not None:
            try:
                self._on_changed()
            except Exception:  # noqa: BLE001
                pass
        self.destroy()

    def _on_cancel(self) -> None:
        self.destroy()


def open_credentials_dialog(
    parent: tk.Misc, on_changed: Callable[[], None] | None = None,
) -> CredentialsDialog | None:
    """Open the credentials dialog as a modal child of ``parent``.

    ``on_changed`` is invoked (on the Tk thread, after vendor sources have
    been re-registered) when the user saves, so the caller can refresh the
    toolbar source combobox — mirroring ``open_local_data_dialog``. Without
    it a newly-configured vendor would stay invisible until restart.

    Returns the :class:`CredentialsDialog` instance (or ``None`` if
    Tk is not available). The dialog blocks the parent via
    ``grab_set`` and destroys itself on Save / Cancel.
    """
    try:
        dlg = CredentialsDialog(parent, on_changed=on_changed)
        parent.wait_window(dlg)
        return dlg
    except tk.TclError:
        return None


__all__ = [
    "CredentialsDialog",
    "open_credentials_dialog",
    "prime_environment_from_dpapi",
]
