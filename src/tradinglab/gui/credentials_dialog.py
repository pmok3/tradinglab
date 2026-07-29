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
import time
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
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

#: Terse chip wording per status. Deliberately shorter than the full
#: ``verify`` summary, which still renders on the Test-connection row — the
#: chip has to fit beside the section heading.
_STATUS_LABELS: dict[str, str] = {
    verify.STATUS_OK:                  "Verified",
    verify.STATUS_NOT_CONFIGURED:      "Not configured",
    verify.STATUS_INVALID_CREDENTIALS: "Rejected",
    verify.STATUS_FORBIDDEN:           "Plan not entitled",
    verify.STATUS_RATE_LIMITED:        "Rate limited",
    verify.STATUS_NETWORK_ERROR:       "Unreachable",
    verify.STATUS_UNSUPPORTED:         "No test available",
    verify.STATUS_ERROR:               "Error",
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
    would leave its stale `os.environ` entry behind — and since ``os.environ``
    is the highest-precedence resolution layer, the deleted key would keep
    resolving for the rest of the session.
    """
    return tuple(env_name for env_name, _label, _secret in _FIELDS)


#: One-line "what does connecting this buy me?" blurb per vendor, shown in the
#: empty state. A new user opening the dialog otherwise sees eight unexplained
#: text boxes and no reason to fill any of them in.
_VENDOR_BLURB: dict[str, str] = {
    "alpaca": ("Alpaca — intraday bars. The paid plan adds the real-time SIP "
               "feed with full volume; the free plan is IEX-only and delayed "
               "15 minutes."),
    "polygon": "Polygon — deep historical intraday history.",
    "schwab": "Schwab — brokerage integration (OAuth flow not shipped yet).",
}


def _format_age(seconds: float) -> str:
    """Coarse 'how long ago' for a verification timestamp.

    Deliberately coarse: the exact second is noise, and the only decision the
    number drives is "is this recent enough to trust, or should I re-test?"
    """
    if seconds < 90:
        return "just now"
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{int(minutes)}m ago"
    hours = minutes / 60.0
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24.0
    if days < 30:
        return f"{int(days)}d ago"
    return "over a month ago"


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


def check_credential_store() -> str:
    """Report the health of the encrypted credential store. Injects nothing.

    Called from :func:`tradinglab.app.main` at startup. Before v2 this
    function was ``prime_environment_from_dpapi`` and its job was to decrypt
    the blob and push every value into ``os.environ`` so that
    ``data.credentials`` — which only knew how to read the environment —
    could see them.

    That indirection is gone: ``credentials._build_layers`` reads the store
    as its own resolution layer, ranked below a real shell export and above
    the plaintext files, which is exactly where priming placed it. Removing
    the injection keeps secrets out of the process environment, where they
    were reachable by crash dumps, subprocesses and any library that logs
    ``os.environ``.

    What remains is the *diagnostic*, which the caller still needs in order
    to distinguish a boring miss from a suspicious one:

    * ``"loaded"`` — store decrypted and holds at least one credential.
    * ``"missing"`` — no blob yet (first launch, or after a clear).
    * ``"dpapi_unavailable"`` — platform without DPAPI (macOS / Linux); the
      user falls back to environment-variable-only mode.
    * ``"decrypt_error"`` — blob present but :func:`_dpapi.unprotect`
      rejected it. Suspicious (tampered, or copied from another machine);
      the caller surfaces this on the status bar.
    * ``"io_error"`` — could not read the blob (permissions / transient
      disk error). Reported like ``decrypt_error``.
    * ``"import_error"`` — :mod:`tradinglab._dpapi` would not import.
      Shouldn't happen in a packaged build; kept for defence in depth.

    Also performs the one-time v1 → v2 schema migration, which is the only
    write this function makes.
    """
    try:
        from .. import _dpapi
    except ImportError:
        return "import_error"
    if not _dpapi.is_available():
        return "dpapi_unavailable"
    from ..data import credential_store
    try:
        raw = _dpapi.load_json_object(credential_store.store_path())
    except _dpapi.DpapiError:
        return "decrypt_error"
    except OSError:
        return "io_error"
    if not raw:
        # ``None`` (no file) and ``{}`` (present but empty) are both
        # "nothing actionable here".
        return "missing"
    try:
        credential_store.migrate_if_needed()
    except Exception:  # noqa: BLE001 - migration is best-effort
        pass
    records = credential_store.load_all()
    return "loaded" if any(r.has_values() for r in records.values()) else "missing"


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
        # Per-vendor header: state chip, provenance line, Remove button.
        self._vendor_state_vars: dict[str, tk.StringVar] = {}
        self._vendor_state_labels: dict[str, ttk.Label] = {}
        self._vendor_origin_vars: dict[str, tk.StringVar] = {}
        self._vendor_remove_buttons: dict[str, ttk.Button] = {}
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
        row = self._add_intro(frm, row)
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
                row = self._add_vendor_header(frm, row, section)
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

    def _add_intro(self, frm: ttk.Frame, row: int) -> int:
        """Explain what connecting a vendor buys you, when nothing is set up.

        A new user otherwise opens this dialog to eight unlabelled text boxes
        and no reason to fill any of them in — the app runs on yfinance and
        never mentions that better data is one paste away. Suppressed once a
        vendor is configured, so it never becomes noise for the steady state.
        """
        try:
            from ..data import credentials as _creds
            if _creds.get_credentials().configured_vendors():
                return row
        except Exception:  # noqa: BLE001
            return row

        blurbs = [_VENDOR_BLURB[v] for _p, _s, v in _SECTIONS
                  if v in _VENDOR_BLURB]
        text = ("TradingLab runs on free yfinance data out of the box. "
                "Connect a provider below for better intraday coverage:\n  \u2022 "
                + "\n  \u2022 ".join(blurbs))
        ttk.Label(frm, text=text, foreground=MUTED_GREY, wraplength=520,
                  justify="left").grid(row=row, column=0, columnspan=3,
                                       sticky="w", pady=(0, 10))
        return row + 1

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

    # ---- vendor header (state chip + provenance + remove) ---------------

    def _add_vendor_header(self, frm: ttk.Frame, row: int, section: str) -> int:
        """Render the state chip / provenance line / Remove button for a vendor.

        This is the answer to "am I configured, is it working, and where is it
        coming from?" — three questions the old flat form could not answer at
        all. Without it the user's only feedback was eight text boxes and a
        Save button.
        """
        vendor = next((v for _p, s, v in _SECTIONS if s == section), None)
        if vendor is None:
            return row

        bar = ttk.Frame(frm)
        bar.grid(row=row, column=0, columnspan=3, sticky="we", pady=(0, 2))

        state_var = tk.StringVar(master=self, value="")
        state_lbl = ttk.Label(bar, textvariable=state_var)
        state_lbl.pack(side="left")
        self._vendor_state_vars[vendor] = state_var
        self._vendor_state_labels[vendor] = state_lbl

        remove_btn = ttk.Button(
            bar, text="Remove", width=9,
            command=lambda v=vendor: self._on_remove_vendor(v))
        remove_btn.pack(side="right")
        self._vendor_remove_buttons[vendor] = remove_btn
        row += 1

        origin_var = tk.StringVar(master=self, value="")
        origin_lbl = ttk.Label(frm, textvariable=origin_var,
                               foreground=MUTED_GREY, wraplength=520,
                               justify="left")
        origin_lbl.grid(row=row, column=0, columnspan=3, sticky="w",
                        pady=(0, 4))
        self._vendor_origin_vars[vendor] = origin_var
        row += 1

        self._refresh_vendor_header(vendor)
        return row

    def _vendor_state_text(self, vendor: str) -> tuple[str, str]:
        """``(text, colour)`` for the vendor chip.

        Precedence mirrors what the user cares about: a verdict beats mere
        presence, because "configured" is not the same as "works" — the whole
        reason `data/verify.py` exists.
        """
        from ..data import credentials as _creds

        creds = _creds.get_credentials()
        vendor_creds = getattr(creds, vendor, None)
        configured = bool(vendor_creds is not None
                          and vendor_creds.is_configured())

        verdict = verify.known_status(vendor)
        if verdict is not None:
            status, checked_at, _summary = verdict
            glyph, colour = _STATUS_STYLE.get(
                status, ("\u2014", MUTED_GREY))
            label = _STATUS_LABELS.get(status, status)
            if checked_at:
                age = _format_age(max(0.0, time.time() - checked_at))
                return f"{glyph} {label} \u00b7 checked {age}", colour
            return f"{glyph} {label}", colour

        if configured:
            return "\u2014 Configured, not tested", MUTED_GREY
        return "\u2014 Not configured", MUTED_GREY

    def _refresh_vendor_header(self, vendor: str) -> None:
        """Repaint one vendor's chip + provenance line. Safe after teardown."""
        from ..data import credentials as _creds

        state_var = self._vendor_state_vars.get(vendor)
        if state_var is None:
            return
        try:
            text, colour = self._vendor_state_text(vendor)
            state_var.set(text)
            lbl = self._vendor_state_labels.get(vendor)
            if lbl is not None and lbl.winfo_exists():
                lbl.configure(foreground=colour)
        except tk.TclError:
            return
        except Exception:  # noqa: BLE001 - a chip must never break the dialog
            return

        origin_var = self._vendor_origin_vars.get(vendor)
        if origin_var is None:
            return
        try:
            origin = _creds.vendor_origin(vendor)
        except Exception:  # noqa: BLE001
            return
        if not origin.present:
            origin_var.set("")
        else:
            suffix = "" if origin.clearable else "  (this app cannot clear it)"
            origin_var.set(f"Source: {origin.describe()}{suffix}")

        btn = self._vendor_remove_buttons.get(vendor)
        if btn is not None and btn.winfo_exists():
            btn.configure(state="normal" if origin.present else "disabled")

    def _refresh_all_vendor_headers(self) -> None:
        for _p, _s, vendor in _SECTIONS:
            self._refresh_vendor_header(vendor)

    def _on_remove_vendor(self, vendor: str) -> None:
        """Delete one vendor's credentials, or explain why we cannot.

        The app only owns the encrypted store. A key supplied by a shell
        export or a plaintext file cannot be removed from here, and silently
        "succeeding" would be the old dead end again — so we name the source
        and point at it instead.
        """
        from ..data import credentials as _creds

        try:
            origin = _creds.vendor_origin(vendor)
        except Exception:  # noqa: BLE001
            return
        label = next((s for _p, s, v in _SECTIONS if v == vendor), vendor)

        if not origin.present:
            return

        if not origin.clearable:
            messagebox.showinfo(
                "Configure Credentials",
                f"{label} credentials come from {origin.describe()}, which "
                "this app does not manage.\n\n"
                "To remove them, delete or edit that source and restart "
                "TradingLab.",
                parent=self,
            )
            return

        if not messagebox.askyesno(
            "Configure Credentials",
            f"Remove the saved {label} credentials from the encrypted store?",
            parent=self,
        ):
            return

        try:
            from ..data import credential_store
            credential_store.clear_vendor(vendor)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Configure Credentials",
                                 f"Could not remove {label} credentials:\n{e}",
                                 parent=self)
            return

        # Drop the in-process verdict too, then blank the form fields so the
        # dialog does not still show a key the store no longer holds.
        verify.clear_results()
        prefix = next((p for p, _s, v in _SECTIONS if v == vendor), None)
        if prefix:
            for name, entry in self._entries.items():
                if not name.startswith(prefix) or name in _CHOICE_FIELDS:
                    continue
                try:
                    entry.delete(0, tk.END)
                except tk.TclError:
                    pass
        try:
            _creds.reload()
        except Exception:  # noqa: BLE001
            pass
        self._set_verify_text(vendor, "Not tested yet.", MUTED_GREY, "")
        self._refresh_vendor_header(vendor)

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
            # The chip reads the recorded verdict, so refresh after it lands.
            self._refresh_vendor_header(vendor)

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
        """Pre-fill from the resolved credential layers, not ``os.environ``.

        The environment is now just one layer among four. Reading it directly
        would render a stored (or file-backed) credential as an empty box —
        the "my keys vanished" failure that made clearing feel broken.
        """
        from ..data import credentials as _creds

        resolved = _creds.effective_values()
        for env_name, entry in self._entries.items():
            if env_name in self._choice_value_by_display:
                # Dropdown: map the stored env value → its display; default to
                # the first (safe) choice when unset/unrecognised.
                current = (resolved.get(env_name, "") or "").lower()
                display = self._choice_display_by_value[env_name].get(current)
                if display is None:
                    display = next(iter(self._choice_value_by_display[env_name]))
                entry.set(display)  # ttk.Combobox
                continue
            current = resolved.get(env_name, "")
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

    def _persist_to_store(self, values: dict[str, str]) -> None:
        """Write ``values`` into the encrypted per-vendor store.

        Replaces the old "push everything into ``os.environ``" step. Every
        data source resolves through ``credentials.get_credentials()``, and
        ``credentials._build_layers`` now reads the store as its own layer —
        so nothing needs the secrets to be in the process environment, and
        keeping them out of it removes a process-wide leak surface (crash
        dumps, subprocesses, any library that logs ``os.environ``).

        A vendor whose *credential* fields (everything except the read-only
        choice combos, which always report a value) are all empty is cleared
        outright rather than left as a metadata-only husk.
        """
        from ..data import credential_store

        for prefix, _section, vendor in _SECTIONS:
            subset = {n: v for n, v in values.items() if n.startswith(prefix)}
            has_secret = any(n not in _CHOICE_FIELDS and v
                             for n, v in subset.items())
            if has_secret:
                credential_store.save_vendor(vendor, subset)
            else:
                credential_store.clear_vendor(vendor)

    def _apply_session_only(self, values: dict[str, str]) -> None:
        """Fallback for platforms without DPAPI: values live in this process.

        Also the cleanup path for an upgrade — a pre-v2 build primed the blob
        into ``os.environ``, so a name the user just cleared must be popped or
        it would keep resolving from the stale entry for the rest of the
        session (``credentials`` consults ``os.environ`` first).
        """
        for name in _managed_env_names():
            if name in values:
                os.environ[name] = values[name]
            else:
                os.environ.pop(name, None)

    def _clear_primed_environment(self, values: dict[str, str]) -> None:
        """Drop managed names this process primed in a pre-v2 session.

        Only removes names the user did **not** just supply, and only ones
        that are absent from the store — a real shell export is left alone
        because we cannot distinguish it here, and it is documented to win.
        """
        for name in _managed_env_names():
            if name in values:
                continue
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
            self._apply_session_only(values)
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
            self._persist_to_store(values)
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

        # Secrets are NOT pushed into os.environ any more; the store is its
        # own resolution layer. Still drop anything a pre-v2 session primed,
        # or a cleared key would keep resolving from the stale entry.
        self._clear_primed_environment(values)
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
        """Offer to migrate plaintext credentials into the encrypted store.

        ``credentials`` resolves from ``os.environ`` first, then a plaintext
        ``alpaca.txt`` / ``credentials.txt``, then a dev ``.env``. This dialog
        owns the encrypted store and the environment layer, so a key supplied
        by a *file* cannot be removed from here.

        Rather than only naming the problem (the old behaviour, which left the
        user at a dead end), offer the one-click fix: import the file's values
        into the encrypted store and delete the plaintext. That also upgrades
        the at-rest protection from "cleartext in a folder" to DPAPI.

        Fires when a plaintext file is currently supplying values, whether or
        not the user just tried to clear a field — a packaged user who never
        touches the form still benefits from being told their keys are sitting
        in the clear.
        """
        from ..data import credentials as _creds

        try:
            files = _creds.plaintext_credential_files()
        except Exception:  # noqa: BLE001
            return
        if not files:
            return

        listing = "\n  \u2022 ".join(str(p) for p in files)
        secure = messagebox.askyesno(
            "Configure Credentials",
            "These credentials are stored in plain text:\n  \u2022 "
            + listing
            + "\n\nThey override anything saved here, so clearing a field "
            "above cannot remove them.\n\n"
            "Import them into the encrypted store and delete the plaintext "
            "file(s)?",
            parent=self,
        )
        if not secure:
            return
        self._migrate_plaintext_files(files)

    def _migrate_plaintext_files(self, files: list[Path]) -> None:
        """Import plaintext credential files into the store, then delete them.

        Import first, delete second, and only delete files that were fully
        imported — losing a key because the store write failed would be far
        worse than leaving a cleartext file on disk one more session.
        """
        from ..data import credential_store
        from ..data import credentials as _creds

        try:
            values = _creds._load_credential_txt_files()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Configure Credentials",
                                 f"Could not read the credential file(s):\n{e}",
                                 parent=self)
            return
        if not values:
            return

        try:
            for prefix, _section, vendor in _SECTIONS:
                subset = {n: v for n, v in values.items() if n.startswith(prefix)}
                if any(n not in _CHOICE_FIELDS and v for n, v in subset.items()):
                    existing = credential_store.get_vendor(vendor).fields
                    merged = {**existing, **subset}
                    credential_store.save_vendor(vendor, merged)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(
                "Configure Credentials",
                f"Could not save to the encrypted store:\n{e}\n\n"
                "Your plaintext file(s) were left untouched.",
                parent=self)
            return

        removed: list[str] = []
        failed: list[str] = []
        for path in files:
            try:
                Path(path).unlink()
                removed.append(str(path))
            except OSError as e:
                failed.append(f"{path} ({e})")

        try:
            _creds.reload()
        except Exception:  # noqa: BLE001
            pass
        self._refresh_all_vendor_headers()

        parts = ["Credentials are now stored encrypted with your Windows "
                 "user account."]
        if removed:
            parts.append("Deleted:\n  \u2022 " + "\n  \u2022 ".join(removed))
        if failed:
            parts.append("Could NOT delete (remove these by hand):\n  \u2022 "
                         + "\n  \u2022 ".join(failed))
        try:
            messagebox.showinfo("Configure Credentials", "\n\n".join(parts),
                                parent=self)
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
    "check_credential_store",
    "open_credentials_dialog",
]
