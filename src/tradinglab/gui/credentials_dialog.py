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
import tkinter as tk
from tkinter import messagebox, ttk

from ._modal_base import BaseModalDialog, protect_combobox_wheel
from .colors import MUTED_GREY

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

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(
            parent,
            title="Configure Credentials",
            geometry_key="dlg.credentials",
            default_geometry="600x660",
            resizable=(True, True),
        )

        self._entries: dict[str, tk.Entry] = {}
        self._show_vars: dict[str, tk.BooleanVar] = {}
        # For dropdown (choice) fields: display↔stored-value maps.
        self._choice_value_by_display: dict[str, dict[str, str]] = {}
        self._choice_display_by_value: dict[str, dict[str, str]] = {}
        self._build_widgets()
        self._populate_from_environment()
        protect_combobox_wheel(self)
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
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        # Section headers keyed by env-var prefix so we don't hardcode
        # the section boundary positions.
        section_for_prefix = {
            "SCHWAB_":  "Schwab",
            "ALPACA_":  "Alpaca",
            "POLYGON_": "Polygon",
        }
        last_section = None
        row = 0
        for env_name, label, is_secret in _visible_fields():
            section = next((v for k, v in section_for_prefix.items()
                            if env_name.startswith(k)), "")
            if section != last_section:
                if last_section is not None:
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
                combo = ttk.Combobox(frm, width=40, values=displays,
                                     state="readonly")
                combo.grid(row=row, column=1, columnspan=2, sticky="we", pady=2)
                self._entries[env_name] = combo
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

            entry = ttk.Entry(frm, width=42)
            if is_secret:
                entry.configure(show="*")
            entry.grid(row=row, column=1, sticky="we", pady=2)
            self._entries[env_name] = entry

            if is_secret:
                show_var = tk.BooleanVar(value=False)
                self._show_vars[env_name] = show_var
                def _toggle(_e=entry, _v=show_var):
                    _e.configure(show="" if _v.get() else "*")
                ttk.Checkbutton(frm, text="show", variable=show_var,
                                command=_toggle).grid(
                                    row=row, column=2, sticky="w",
                                    padx=(6, 0), pady=2)
            row += 1

        # Status label.
        self._status_var = tk.StringVar(value=self._initial_status_text())
        ttk.Label(frm, textvariable=self._status_var, foreground=MUTED_GREY
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

    # ---- helpers -------------------------------------------------------

    def _populate_from_environment(self) -> None:
        """Pre-fill entries from current ``os.environ`` so existing values are visible."""
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

    def _on_save(self) -> None:
        values = self._collect()
        if not values:
            confirm = messagebox.askyesno(
                "Configure Credentials",
                "All fields are empty. Save an empty configuration "
                "(this clears any previously-saved credentials)?",
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
            for k, v in values.items():
                os.environ[k] = v
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
        for k, v in values.items():
            os.environ[k] = v
        self._close_and_refresh()

    def _close_and_refresh(self) -> None:
        """Refresh the in-process credentials cache and dismiss."""
        try:
            from ..data import credentials as _creds
            _creds.reload()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()

    def _on_cancel(self) -> None:
        self.destroy()


def open_credentials_dialog(parent: tk.Misc) -> CredentialsDialog | None:
    """Open the credentials dialog as a modal child of ``parent``.

    Returns the :class:`CredentialsDialog` instance (or ``None`` if
    Tk is not available). The dialog blocks the parent via
    ``grab_set`` and destroys itself on Save / Cancel.
    """
    try:
        dlg = CredentialsDialog(parent)
        parent.wait_window(dlg)
        return dlg
    except tk.TclError:
        return None


__all__ = [
    "CredentialsDialog",
    "open_credentials_dialog",
    "prime_environment_from_dpapi",
]
