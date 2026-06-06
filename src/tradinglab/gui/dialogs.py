"""Modal Tk dialogs owned by :class:`tradinglab.app.ChartApp`.

These are pure leaf widgets — they read state from the parent app, call a
handful of public methods on it, and never import the app module at load
time (use :data:`TYPE_CHECKING` for type annotations).

Split out of ``app.py`` to keep that file focused on the chart + data
orchestration.
"""

from __future__ import annotations

import tkinter as tk
from copy import deepcopy
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import TYPE_CHECKING

from ..constants import (
    BUILTIN_STARTUP_DEFAULTS,
    INTERVAL_PERIODS,
    STARTUP_DEFAULT_KEYS,
)
from ..data import user_visible_sources
from ..watchlists import (
    WatchlistManager,
)
from ..watchlists import (
    import_from_file as _import_watchlists_from_file,
)
from ._modal_base import BaseModalDialog, make_scrollable_form, protect_combobox_wheel
from .colors import MUTED_GREY
from .native_theme import apply_listbox_theme, current_theme

if TYPE_CHECKING:
    from ..app import ChartApp

# Worker-count clamp bounds. Lifted out of ``ChartApp`` so the Settings
# dialog can reference them without importing the app module.
WORKER_COUNT_MIN = 1
WORKER_COUNT_MAX = 64


def _prompt_string(parent: tk.Misc, title: str, prompt: str,
                   initial: str = "") -> str | None:
    """Thin wrapper around :func:`simpledialog.askstring` for test seams."""
    return simpledialog.askstring(title, prompt, initialvalue=initial,
                                  parent=parent)


# --- settings dialog ----------------------------------------------------

class _SettingsDialog(BaseModalDialog):
    """Modal-ish dialog for editing worker-pool size and other settings.

    Migrated to :class:`BaseModalDialog` — the base class owns
    ``transient`` / ``grab_set`` / ESC+Return keybindings / geometry
    persistence via ``_finalize_modal``. Cancel-revert semantics are
    preserved via the :meth:`_on_cancel` override (restores the
    ``_overrides_initial`` / ``_startup_initial`` snapshots BEFORE
    destroying).
    """

    def __init__(self, parent: ChartApp) -> None:
        super().__init__(
            parent,
            title="Settings",
            geometry_key="dlg.settings",
            default_geometry="720x640",
        )
        self._parent_app = parent
        # Cap dialog height so it stays usable on 1080p screens; the
        # scrollable inner frame below handles overflow.
        screen_h = self.winfo_screenheight()
        self.maxsize(900, max(400, screen_h - 120))

        # Snapshot the override dict at dialog-open so Cancel can revert
        # every color picker in one shot. ``deepcopy`` is overkill for
        # two shallow dicts but makes the invariant ("Cancel fully
        # restores the starting state") obvious at the call site.
        self._overrides_initial = deepcopy(parent._theme_overrides)
        # Same idea for startup defaults so Cancel reverts every Combobox
        # / Entry field together.
        self._startup_initial = deepcopy(parent._startup_defaults)

        # Scrollable container: the standard Canvas + Scrollbar + inner
        # ttk.Frame skeleton lives in :func:`_modal_base.make_scrollable_form`
        # — see audit item #5. The inner frame is themed with padding so
        # the LabelFrame groups don't hug the left edge of the canvas.
        # Stash the host canvas so the post-layout combobox-wheel guard
        # can forward scrolls into it (CLAUDE.md §7.11 — without the
        # guard, wheel-over-Combobox / wheel-over-Spinbox silently
        # mutates the widget value because the ttk class binding wins
        # over our bind_all).
        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True)
        frm, canvas = make_scrollable_form(outer)
        frm.configure(padding=12)
        self._form_canvas = canvas

        # ────────────────────────────────────────────────────────
        # Settings are grouped into ttk.LabelFrame sections so the
        # dialog reads more like a System Preferences pane than a
        # 14-row scroll. Audit ``settings-dialog-grouping``. Tk
        # var names and event handlers are unchanged — only the
        # widget parents and grid coordinates differ. Each section
        # owns its own internal row counter so future additions
        # can land in one group without renumbering siblings.
        # ────────────────────────────────────────────────────────

        # ── Performance ─────────────────────────────────────────
        perf_frame = ttk.LabelFrame(frm, text="Performance", padding=8)
        perf_frame.grid(row=0, column=0, columnspan=2, sticky="ew")

        import os
        total = os.cpu_count() or 1
        ttk.Label(perf_frame, text="Worker threads:").grid(
            row=0, column=0, sticky="w")
        self._worker_var = tk.IntVar(value=parent._worker_count)
        ttk.Spinbox(
            perf_frame,
            from_=WORKER_COUNT_MIN,
            to=WORKER_COUNT_MAX,
            textvariable=self._worker_var,
            width=6,
        ).grid(row=0, column=1, padx=6)
        ttk.Label(
            perf_frame,
            text=(f"(system has {total} logical thread"
                  f"{'s' if total != 1 else ''} available)"),
            foreground=MUTED_GREY,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # ── Display & Appearance ────────────────────────────────
        display_frame = ttk.LabelFrame(
            frm, text="Display & Appearance", padding=8)
        display_frame.grid(row=1, column=0, columnspan=2, sticky="ew",
                           pady=(12, 0))

        # Dark-mode toggle lives here (not on the toolbar) — theme is
        # applied live, and reverted on Cancel.
        self._dark_initial = bool(parent.dark_var.get())
        self._dark_var = tk.BooleanVar(value=self._dark_initial)
        ttk.Checkbutton(
            display_frame, text="Dark mode", variable=self._dark_var,
            command=self._on_dark_toggle,
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        # Logarithmic price Y-axis. Useful for long-term charts where
        # large percentage moves at low prices get visually compressed
        # under a linear scale. Applied live; reverted on Cancel.
        self._log_initial = bool(parent.log_price_var.get())
        self._log_var = tk.BooleanVar(value=self._log_initial)
        ttk.Checkbutton(
            display_frame, text="Logarithmic price axis",
            variable=self._log_var,
            command=self._on_log_toggle,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # Mouse-wheel zoom direction preference. Default (unchecked) =
        # scroll DOWN zooms in, UP zooms out (TradingView). Checked =
        # inverted (macOS / natural-scroll). Applied live; reverted on
        # Cancel via ``_on_cancel`` restoring ``_scroll_invert_initial``.
        self._scroll_invert_initial = bool(
            getattr(parent, "_scroll_zoom_invert", False))
        self._scroll_invert_var = tk.BooleanVar(
            value=self._scroll_invert_initial)
        ttk.Checkbutton(
            display_frame,
            text="Invert scroll-zoom direction (scroll up to zoom in)",
            variable=self._scroll_invert_var,
            command=self._on_scroll_invert_toggle,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # Volume time-of-day shading (1d bars only). When enabled, each
        # 1d volume bar gets a darker-hue full-day outline plus a solid
        # fill from the baseline up to the time-of-day cumulative
        # (sandbox-aware). Off by default — purely visual, no engine
        # impact. Live-preview + cancel-revert handled in _on_cancel /
        # _on_ok via the parent's `set_volume_tod_enabled` method.
        from .. import defaults as _defaults_mod
        try:
            self._vol_tod_initial = bool(
                _defaults_mod.get("volume_tod_enabled"))
        except Exception:  # noqa: BLE001
            self._vol_tod_initial = False
        self._vol_tod_var = tk.BooleanVar(value=self._vol_tod_initial)
        ttk.Checkbutton(
            display_frame,
            text="Volume time-of-day shading (1d bars)",
            variable=self._vol_tod_var,
            command=self._on_volume_tod_toggle,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # UI scale (font-size multiplier for chrome). Lets users
        # with hi-DPI screens / presbyopia / preference dial the
        # whole UI up or down without re-launching. Live-preview
        # via ``configure_named_fonts(scale=…)``; cancel reverts.
        # Audit ``font-scaling``.
        from .named_fonts import UI_SCALES as _UI_SCALES
        self._ui_scale_choices = _UI_SCALES
        self._ui_scale_initial = float(
            getattr(parent, "_ui_scale", 1.0) or 1.0)
        self._ui_scale_var = tk.StringVar(
            value=self._format_ui_scale(self._ui_scale_initial))
        scale_frame = ttk.Frame(display_frame)
        scale_frame.grid(row=4, column=0, columnspan=2, sticky="w",
                         pady=(8, 0))
        ttk.Label(scale_frame, text="UI scale:").grid(
            row=0, column=0, sticky="w")
        ttk.Combobox(
            scale_frame,
            textvariable=self._ui_scale_var,
            values=[self._format_ui_scale(s) for s in _UI_SCALES],
            state="readonly",
            width=8,
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        scale_frame.bind_all(
            "<<ComboboxSelected>>", self._on_ui_scale_changed, add="+")
        ttk.Label(
            scale_frame,
            text=("Multiplier for menus, dialogs, and tab labels. "
                  "Chart axis labels follow the chart's own settings."),
            foreground=MUTED_GREY, wraplength=480, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # Color-blind-safe candle palette (Okabe-Ito orange/blue).
        # The candle renderers resolve constants.BULL_COLOR /
        # BEAR_COLOR via live attribute lookup, so the toggle handler's
        # set_use_colorblind_palette() re-renders the chart and re-tags
        # the watchlist immediately — no relaunch needed for those
        # surfaces. Audit ``color-blind-palette``.
        try:
            from .. import settings as _settings_mod
            self._colorblind_initial = bool(
                _settings_mod.get("use_colorblind_palette", False))
        except Exception:  # noqa: BLE001
            self._colorblind_initial = False
        self._colorblind_var = tk.BooleanVar(value=self._colorblind_initial)
        cb_frame = ttk.Frame(display_frame)
        cb_frame.grid(row=5, column=0, columnspan=2, sticky="w",
                      pady=(8, 0))
        ttk.Checkbutton(
            cb_frame,
            text="Use color-blind-safe palette (Okabe-Ito)",
            variable=self._colorblind_var,
            command=self._on_colorblind_toggle,
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            cb_frame,
            text=("Replaces the default green/red candle colors with "
                  "Okabe-Ito orange/blue so the bull/bear distinction "
                  "reads cleanly for deuteranopia / protanopia / "
                  "tritanopia. The chart and watchlists update "
                  "immediately."),
            foreground=MUTED_GREY, wraplength=480, justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        # ── Drawings ────────────────────────────────────────────
        drawings_frame = ttk.LabelFrame(frm, text="Drawings", padding=8)
        drawings_frame.grid(row=2, column=0, columnspan=2, sticky="ew",
                            pady=(12, 0))

        # Opt-in "snap Alt+H to nearest OHLC". When enabled, Alt+H
        # placement within ~8 pixels of any visible candle's
        # open/high/low/close locks the line to that price. Off
        # by default (audit ``drawings-snap-extended``) — magnetic
        # snapping is the kind of thing that surprises traders
        # who didn't ask for it, so this is opt-in.
        self._snap_ohlc_initial = bool(
            getattr(parent, "_drawings_snap_to_ohlc", False))
        self._snap_ohlc_var = tk.BooleanVar(value=self._snap_ohlc_initial)
        ttk.Checkbutton(
            drawings_frame,
            text="Snap horizontal lines to nearest OHLC (Ctrl+H)",
            variable=self._snap_ohlc_var,
            command=self._on_snap_ohlc_toggle,
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        # Sandbox reference (master-clock anchor) symbol. SPY is the
        # convention but advanced users may need a different liquid
        # benchmark (QQQ, ES=F, EURUSD=X, …). Persists via
        # ``defaults.set(...)`` on OK; reverted via the
        # ``self._sandbox_ref_initial`` snapshot on Cancel. Audit
        # ``sandbox-ref-symbol``.
        try:
            self._sandbox_ref_initial = str(
                _defaults_mod.get("sandbox_reference_symbol") or "SPY",
            )
        except Exception:  # noqa: BLE001
            self._sandbox_ref_initial = "SPY"
        self._sandbox_ref_var = tk.StringVar(value=self._sandbox_ref_initial)
        sandbox_frame = ttk.LabelFrame(
            frm, text="Sandbox", padding=8)
        sandbox_frame.grid(row=3, column=0, columnspan=2, sticky="ew",
                           pady=(12, 0))
        ttk.Label(sandbox_frame, text="Reference symbol:").grid(
            row=0, column=0, sticky="w")
        ttk.Entry(
            sandbox_frame, textvariable=self._sandbox_ref_var, width=10,
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(
            sandbox_frame,
            text=("Master-clock anchor for replay sessions. SPY by "
                  "default; switch to QQQ / ES=F / EURUSD=X as your "
                  "data source requires."),
            foreground=MUTED_GREY, wraplength=480, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # Skip-detailed-journal toggle. Off by default — the journal
        # discipline is the whole point of sandbox for most users —
        # but rapid scalp-practice users want fewer modals. Audit
        # ``mandatory-journal-skip``.
        try:
            self._skip_journal_initial = bool(
                _defaults_mod.get("sandbox_skip_detailed_journal"))
        except Exception:  # noqa: BLE001
            self._skip_journal_initial = False
        self._skip_journal_var = tk.BooleanVar(
            value=self._skip_journal_initial)
        ttk.Checkbutton(
            sandbox_frame,
            text="Skip detailed journal (rapid scalp-practice mode)",
            variable=self._skip_journal_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(
            sandbox_frame,
            text=("Bypasses the mandatory pre-trade form and "
                  "post-trade review modal. Orders are stamped with "
                  "a \"(skipped)\" thesis so you can still tell "
                  "them apart later."),
            foreground=MUTED_GREY, wraplength=480, justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # Startup parameters: persist user preferences for the values
        # used the next time the app is launched. These don't apply
        # *live* to the running session — that would be jarring — they
        # just tell the next ``ChartApp.__init__`` what to seed the Tk
        # vars with. Use "Use current chart" to capture the running
        # session's selections in one click.
        self._startup_vars: dict[str, tk.StringVar] = {}
        startup_frame = ttk.LabelFrame(
            frm, text="Startup parameters", padding=8)
        startup_frame.grid(row=4, column=0, columnspan=2, sticky="ew",
                           pady=(12, 0))
        self._build_startup_defaults_section(startup_frame)

        # Display timezone: applied live to intraday clock labels.
        # Free-form entry (state="normal") so users can type any IANA
        # name; the dropdown is just a curated shortcut. Empty string
        # = no conversion (today's behavior).
        self._tz_initial = getattr(parent, "_display_tz", "") or ""
        self._tz_var = tk.StringVar(value=self._tz_initial)
        tz_frame = ttk.LabelFrame(
            frm, text="Display timezone", padding=8)
        tz_frame.grid(row=5, column=0, columnspan=2, sticky="ew",
                      pady=(12, 0))
        ttk.Label(tz_frame, text="IANA name:").grid(
            row=0, column=0, sticky="w")
        ttk.Combobox(
            tz_frame, textvariable=self._tz_var,
            values=[
                "", "America/New_York", "America/Chicago",
                "America/Denver", "America/Los_Angeles", "UTC",
                "Europe/London", "Europe/Berlin",
                "Asia/Tokyo", "Asia/Singapore", "Australia/Sydney",
            ],
            state="normal", width=22,
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(
            tz_frame,
            text=("Empty = ET-native. Applied live to intraday clock "
                  "labels; daily bars stay date-anchored."),
            foreground=MUTED_GREY,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # Theme customization moved to View → Theme… (big-bet item #7).
        # ``_swatch_buttons`` is kept for backwards compatibility with
        # tests that reach into the Settings dialog for swatches; the
        # buttons live in :class:`ThemeEditorDialog` now. We expose a
        # quick-access button that opens the dedicated editor so users
        # who reach for Settings out of habit can still get there in
        # one click.
        self._swatch_buttons: dict[str, dict[str, tk.Button]] = {
            "light": {},
            "dark": {},
        }
        theme_hint = ttk.LabelFrame(
            frm, text="Theme customization", padding=8)
        theme_hint.grid(row=6, column=0, columnspan=2, sticky="ew",
                        pady=(12, 0))
        ttk.Label(
            theme_hint,
            text=("Per-slot colors + presets now live in their own "
                  "editor — open it via View \u2192 Theme…"),
            wraplength=520, justify="left",
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            theme_hint, text="Open Theme Editor…",
            command=self._on_open_theme_editor,
        ).pack(side="right", padx=(8, 0))

        # Watchlist pin cap. Persisted via the
        # ``watchlist_max_pinned`` Tunable; applies to managers
        # constructed after the next launch (the live manager keeps
        # its current cap so a mid-session change doesn't strand
        # already-pinned lists). Audit ``pinned-watchlist-cap``.
        try:
            self._wl_cap_initial = int(
                _defaults_mod.get("watchlist_max_pinned"))
        except Exception:  # noqa: BLE001
            self._wl_cap_initial = 5
        self._wl_cap_var = tk.IntVar(value=self._wl_cap_initial)
        wl_frame = ttk.LabelFrame(frm, text="Watchlist", padding=8)
        wl_frame.grid(row=7, column=0, columnspan=2, sticky="ew",
                      pady=(12, 0))
        ttk.Label(wl_frame, text="Pinned sub-tab cap:").grid(
            row=0, column=0, sticky="w")
        ttk.Spinbox(
            wl_frame, from_=1, to=20, width=6,
            textvariable=self._wl_cap_var,
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(
            wl_frame,
            text=("Maximum number of watchlists shown as sub-tabs "
                  "in the Watchlist notebook. Re-launch to apply."),
            foreground=MUTED_GREY, wraplength=480, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=8, column=0, columnspan=2, pady=(12, 0), sticky="e")
        ttk.Button(btns, text="Cancel", command=self._on_cancel).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="Save and Close", command=self._on_ok).pack(side=tk.RIGHT)

        # Block wheel-over-Combobox / wheel-over-Spinbox from silently
        # mutating values (see ``protect_combobox_wheel`` docstring and
        # CLAUDE.md §7.11). Settings is built once in __init__ with no
        # partial widget rebuilds, so a single call after every widget
        # exists is sufficient. Idempotent — safe if a future refactor
        # adds a rebuild handler that re-calls this method. Must run
        # BEFORE ``_finalize_modal`` so the walker sees every widget.
        try:
            protect_combobox_wheel(self, scroll_target=self._form_canvas)
        except tk.TclError:
            pass

        # BaseModalDialog: wires WM_DELETE_WINDOW + ESC → _on_cancel
        # (override below restores snapshots) and Enter → _on_ok.
        self._finalize_modal(primary=self._on_ok, cancel=self._on_cancel)

    def _on_open_theme_editor(self) -> None:
        """Open the dedicated Theme Editor Toplevel from Settings."""
        try:
            from .theme_editor import open_theme_editor
            open_theme_editor(self._parent_app)
        except Exception:  # noqa: BLE001
            pass

    def _build_startup_defaults_section(self, parent: tk.Widget) -> None:
        """Build the Startup parameters editor.

        One row per entry in :data:`STARTUP_DEFAULT_KEYS`. Tickers use
        free-form Entries (uppercased on commit by the resolver);
        interval/source/theme use readonly Comboboxes whose option lists
        are pulled from the same runtime sources the loader validates
        against, so the user can't pick something the loader will drop.
        """
        intervals = list(INTERVAL_PERIODS.keys())
        # Use the user-visible source list so the Settings dropdown
        # mirrors the toolbar combobox — synthetic / synthetic-stream
        # are dispatchable programmatically (smoke tests, sandbox
        # replay) but never user-selectable.
        sources = user_visible_sources()
        choices_by_key = {
            "interval": intervals,
            "source": sources,
            "theme": ["light", "dark"],
        }
        sd = self._parent_app._startup_defaults
        for r, (key, label) in enumerate(STARTUP_DEFAULT_KEYS):
            ttk.Label(parent, text=label + ":").grid(
                row=r, column=0, sticky="w", pady=2)
            var = tk.StringVar(value=sd.get(key, BUILTIN_STARTUP_DEFAULTS[key]))
            self._startup_vars[key] = var
            choices = choices_by_key.get(key)
            if choices:
                cb = ttk.Combobox(
                    parent, textvariable=var, values=choices,
                    state="readonly", width=12,
                )
                cb.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=2)
            else:
                ttk.Entry(parent, textvariable=var, width=14).grid(
                    row=r, column=1, sticky="w", padx=(8, 0), pady=2)
        # Convenience controls.
        btn_row = len(STARTUP_DEFAULT_KEYS)
        btnf = ttk.Frame(parent)
        btnf.grid(row=btn_row, column=0, columnspan=2, sticky="w",
                  pady=(8, 0))
        ttk.Button(
            btnf, text="Use current chart",
            command=self._on_capture_current_as_default,
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            btnf, text="Reset to builtins",
            command=self._on_reset_startup_defaults,
        ).pack(side=tk.LEFT)
        ttk.Label(
            parent,
            text=("Applied next launch — does not change the current "
                  "session."),
            foreground=MUTED_GREY,
        ).grid(row=btn_row + 1, column=0, columnspan=2, sticky="w",
               pady=(4, 0))

        # Splash-on-startup toggle. Audit ``settings-splash-disable``:
        # previously the only way to suppress the splash was the
        # ``TRADINGLAB_NO_SPLASH`` env var or ``--no-splash`` CLI
        # flag — neither discoverable to end users running the
        # frozen .exe. Persists via the ``splash_enabled`` Tunable
        # in ``defaults.py``; consumed by
        # :func:`tradinglab.gui.splash.make_splash`. The env-var
        # / CLI flag still wins over the Settings preference so
        # the test harness keeps a single off-switch.
        try:
            from .. import defaults as _defaults_mod
            self._splash_initial = bool(
                _defaults_mod.get("splash_enabled"))
        except Exception:  # noqa: BLE001
            self._splash_initial = True
        self._splash_var = tk.BooleanVar(value=self._splash_initial)
        ttk.Checkbutton(
            parent, text="Show splash screen on startup",
            variable=self._splash_var,
        ).grid(row=btn_row + 2, column=0, columnspan=2, sticky="w",
               pady=(8, 0))
        ttk.Label(
            parent,
            text=("Only affects frozen builds; dev mode never "
                  "shows a splash. Re-launch to apply."),
            foreground=MUTED_GREY,
        ).grid(row=btn_row + 3, column=0, columnspan=2, sticky="w",
               pady=(2, 0))

        # Startup update check. Default-on now that the repo has a
        # public GitHub Releases channel; the networking layer is RTH-
        # suppressed and cached. The endpoint URL is intentionally NOT
        # user-facing — it falls back to the env var / built-in GitHub
        # Releases default (see updates._resolve_url).
        try:
            self._update_check_initial = bool(
                _defaults_mod.get("update_check_on_startup"))
        except Exception:  # noqa: BLE001
            self._update_check_initial = True
        self._update_check_var = tk.BooleanVar(
            value=self._update_check_initial)
        ttk.Checkbutton(
            parent,
            text="Check for updates on startup",
            variable=self._update_check_var,
        ).grid(row=btn_row + 4, column=0, columnspan=2, sticky="w",
               pady=(8, 0))
        ttk.Label(
            parent,
            text=("Runs in the background, never during regular trading "
                  "hours, and only shows a banner when a newer release exists."),
            foreground=MUTED_GREY, wraplength=480, justify="left",
        ).grid(row=btn_row + 5, column=0, columnspan=2, sticky="w",
               pady=(2, 0))

    def _on_capture_current_as_default(self) -> None:
        """Pull the running session's primary/compare/interval/source/
        theme into the dialog's startup-default Tk vars."""
        app = self._parent_app
        try:
            self._startup_vars["ticker"].set(app.ticker_var.get() or "")
        except Exception:  # noqa: BLE001
            pass
        try:
            self._startup_vars["compare"].set(
                app.compare_ticker_var.get() or "")
        except Exception:  # noqa: BLE001
            pass
        try:
            self._startup_vars["interval"].set(app.interval_var.get() or "")
        except Exception:  # noqa: BLE001
            pass
        try:
            self._startup_vars["source"].set(app.source_var.get() or "")
        except Exception:  # noqa: BLE001
            pass
        try:
            self._startup_vars["theme"].set(
                "dark" if app.dark_var.get() else "light")
        except Exception:  # noqa: BLE001
            pass

    def _on_reset_startup_defaults(self) -> None:
        """Reset every Tk var to its builtin default; persistence happens on OK."""
        for key, _label in STARTUP_DEFAULT_KEYS:
            self._startup_vars[key].set(BUILTIN_STARTUP_DEFAULTS[key])

    def _commit_startup_defaults(self) -> None:
        """Push the dialog's Tk vars into the parent's persisted dict."""
        for key, _label in STARTUP_DEFAULT_KEYS:
            try:
                self._parent_app.set_startup_default(
                    key, self._startup_vars[key].get())
            except Exception:  # noqa: BLE001
                pass

    def _on_dark_toggle(self) -> None:
        """Preview the theme change live as the user toggles the box."""
        try:
            self._parent_app.dark_var.set(self._dark_var.get())
            self._parent_app._apply_theme()
        except Exception:  # noqa: BLE001
            pass

    def _on_log_toggle(self) -> None:
        """Preview the log/linear price scale live as the user toggles."""
        try:
            self._parent_app.log_price_var.set(self._log_var.get())
            self._parent_app._apply_price_scale()
        except Exception:  # noqa: BLE001
            pass

    def _on_scroll_invert_toggle(self) -> None:
        """Apply scroll-zoom direction live; persist on OK only.

        Mutates ``parent._scroll_zoom_invert`` directly (no
        ``set_scroll_zoom_invert`` here) so Cancel can revert by
        restoring ``_scroll_invert_initial`` without an extra
        ``settings.json`` write. ``_on_ok`` does the persistent write.
        """
        try:
            self._parent_app._scroll_zoom_invert = bool(
                self._scroll_invert_var.get())
        except Exception:  # noqa: BLE001
            pass

    def _on_snap_ohlc_toggle(self) -> None:
        """Apply Alt+H snap-to-OHLC live; persist on OK only.

        Mirrors ``_on_scroll_invert_toggle``: writes the new flag
        directly to ``parent._drawings_snap_to_ohlc`` so the next
        Alt+H observes it; Cancel reverts by restoring
        ``_snap_ohlc_initial`` without a ``settings.json`` write.
        ``_on_ok`` calls the parent's setter to persist. Audit
        ``drawings-snap-extended``.
        """
        try:
            self._parent_app._drawings_snap_to_ohlc = bool(
                self._snap_ohlc_var.get())
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _format_ui_scale(value: float) -> str:
        """Render a UI-scale multiplier as ``"100%"`` / ``"115%"`` /
        etc. for the combobox display. Inverse:
        :meth:`_parse_ui_scale`. Audit ``font-scaling``."""
        try:
            return f"{int(round(float(value) * 100))}%"
        except (TypeError, ValueError):
            return "100%"

    @staticmethod
    def _parse_ui_scale(text: str) -> float:
        """Parse the combobox label (e.g. ``"115%"``) back into the
        numeric multiplier. Returns ``1.0`` on parse failure.
        Audit ``font-scaling``."""
        try:
            cleaned = (text or "").strip().rstrip("%").strip()
            return float(cleaned) / 100.0
        except (TypeError, ValueError):
            return 1.0

    def _on_ui_scale_changed(self, _event=None) -> None:
        """Live-preview a new UI-scale multiplier. The
        ``<<ComboboxSelected>>`` binding is global, so we filter
        to events that came from *our* combobox by checking that
        the selected text round-trips through :meth:`_parse_ui_scale`
        to one of our supported scales. Audit ``font-scaling``.
        """
        try:
            raw = self._ui_scale_var.get()
        except Exception:  # noqa: BLE001
            return
        scale = self._parse_ui_scale(raw)
        if scale not in self._ui_scale_choices:
            # Some other combobox fired; ignore.
            return
        try:
            self._parent_app.set_ui_scale(scale)
        except Exception:  # noqa: BLE001
            pass

    def _on_colorblind_toggle(self) -> None:
        """Apply the color-blind-safe palette LIVE; persist on OK.

        Like ``_on_volume_tod_toggle`` this writes through the
        parent's setter (which mutates the live module-level
        constants AND triggers a re-render). Cancel reverts via
        another live setter call in ``_on_cancel`` so the chart
        snaps back to whatever the user saw before opening the
        dialog. Audit ``color-blind-palette``.
        """
        try:
            self._parent_app.set_use_colorblind_palette(
                bool(self._colorblind_var.get()))
        except Exception:  # noqa: BLE001
            pass

    def _on_volume_tod_toggle(self) -> None:
        """Apply the volume-TOD overlay live; persist on OK only.

        Live-preview pattern matches ``_on_scroll_invert_toggle`` /
        ``_on_log_toggle``: writes the new boolean directly to the
        :mod:`defaults` tunable system via the parent's setter, which
        reloads defaults + triggers a redraw. Cancel reverts by
        re-applying the dialog-open snapshot in ``_on_cancel``.

        Persistence to ``settings.json`` happens twice — once here (the
        parent's ``set_volume_tod_enabled`` writes) and once in
        ``_on_ok`` (redundant but harmless; matches existing pattern
        for other live-preview toggles). Cancel rewrites the original
        value, restoring the persisted state to its pre-dialog form.
        """
        try:
            self._parent_app.set_volume_tod_enabled(
                bool(self._vol_tod_var.get())
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_cancel(self) -> None:
        """Revert any live previews so Cancel truly undoes changes."""
        try:
            if self._parent_app.dark_var.get() != self._dark_initial:
                self._parent_app.dark_var.set(self._dark_initial)
                self._parent_app._apply_theme()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._parent_app.log_price_var.get() != self._log_initial:
                self._parent_app.log_price_var.set(self._log_initial)
                self._parent_app._apply_price_scale()
        except Exception:  # noqa: BLE001
            pass
        # Scroll-zoom invert was applied live (in-memory only); reset
        # the runtime flag back to the dialog-open value. Persistence
        # never happened during preview, so no settings.json rewrite.
        try:
            if (getattr(self._parent_app, "_scroll_zoom_invert", False)
                    != self._scroll_invert_initial):
                self._parent_app._scroll_zoom_invert = (
                    self._scroll_invert_initial)
        except Exception:  # noqa: BLE001
            pass
        # Snap-to-OHLC was applied live (in-memory only); reset back
        # to the dialog-open value. Audit ``drawings-snap-extended``.
        try:
            if (getattr(self._parent_app, "_drawings_snap_to_ohlc", False)
                    != self._snap_ohlc_initial):
                self._parent_app._drawings_snap_to_ohlc = (
                    self._snap_ohlc_initial)
        except Exception:  # noqa: BLE001
            pass
        # UI scale was applied LIVE via the parent's set_ui_scale (which
        # re-runs configure_named_fonts immediately so the user sees
        # the new size). Cancel must restore the dialog-open scale by
        # re-applying it, which performs another live reconfigure +
        # settings.json write so the persisted value matches what the
        # user saw before opening the dialog. Audit ``font-scaling``.
        try:
            current_scale = float(
                getattr(self._parent_app, "_ui_scale", 1.0) or 1.0)
            if current_scale != self._ui_scale_initial:
                self._parent_app.set_ui_scale(self._ui_scale_initial)
        except Exception:  # noqa: BLE001
            pass
        # Color-blind palette was applied LIVE via the parent's
        # set_use_colorblind_palette (which mutates constants +
        # triggers a re-render). Cancel reverts by calling the
        # setter again with the dialog-open value. Audit
        # ``color-blind-palette``.
        try:
            from .. import settings as _settings_mod
            current_cb = bool(
                _settings_mod.get("use_colorblind_palette", False))
            if current_cb != self._colorblind_initial:
                self._parent_app.set_use_colorblind_palette(
                    self._colorblind_initial)
        except Exception:  # noqa: BLE001
            pass
        # Volume time-of-day shading was applied LIVE via the parent's
        # set_volume_tod_enabled (which writes settings.json + reloads
        # defaults + triggers a redraw). Cancel must restore the
        # dialog-open value, which performs another live write + redraw
        # so the visual state matches what the user saw before opening
        # the dialog.
        try:
            from .. import defaults as _defaults_mod
            current = bool(_defaults_mod.get("volume_tod_enabled"))
            if current != self._vol_tod_initial:
                self._parent_app.set_volume_tod_enabled(
                    self._vol_tod_initial
                )
        except Exception:  # noqa: BLE001
            pass
        # Restore theme overrides to the dialog-open snapshot. This
        # also triggers a re-apply so live-previewed color picks are
        # undone without extra state tracking.
        try:
            if (self._parent_app._theme_overrides
                    != self._overrides_initial):
                self._parent_app.replace_theme_overrides(
                    self._overrides_initial)
        except Exception:  # noqa: BLE001
            pass
        # Startup defaults aren't applied live, but if the user clicked
        # "Use current chart" / "Reset to builtins" the dialog Tk vars
        # diverge from the parent's persisted dict — Cancel should
        # discard those edits, not commit them. Restoring the parent
        # dict keeps ``settings.json`` consistent with what the user
        # saw before opening the dialog.
        try:
            if self._parent_app._startup_defaults != self._startup_initial:
                self._parent_app.replace_startup_defaults(
                    self._startup_initial)
        except Exception:  # noqa: BLE001
            pass
        # Display tz was applied live in _on_ok-only path? No — only
        # _on_ok commits it, so Cancel just discards the dialog Tk var.
        self.destroy()

    def _on_ok(self) -> None:
        try:
            count = int(self._worker_var.get())
        except (tk.TclError, ValueError):
            count = self._parent_app._worker_count
        try:
            self._parent_app.set_worker_count(count)
        except Exception:  # noqa: BLE001
            pass
        # Commit startup-default edits. Theme/dark/log were already
        # applied live; nothing else to commit.
        try:
            self._commit_startup_defaults()
        except Exception:  # noqa: BLE001
            pass
        # Commit display timezone (only if changed, to avoid an
        # unnecessary _render() round-trip).
        try:
            new_tz = self._tz_var.get() or ""
            if new_tz != self._tz_initial:
                self._parent_app.set_display_tz(new_tz)
        except Exception:  # noqa: BLE001
            pass
        # Commit scroll-zoom direction. Live preview already mutated
        # the runtime flag; this call writes settings.json only when
        # the value actually differs from the dialog-open snapshot.
        try:
            new_inv = bool(self._scroll_invert_var.get())
            if new_inv != self._scroll_invert_initial:
                self._parent_app.set_scroll_zoom_invert(new_inv)
        except Exception:  # noqa: BLE001
            pass
        # Commit Alt+H snap-to-OHLC. Same live-preview pattern: the
        # runtime flag was already mutated; this call writes
        # settings.json only when the value actually changed. Audit
        # ``drawings-snap-extended``.
        try:
            new_snap = bool(self._snap_ohlc_var.get())
            if new_snap != self._snap_ohlc_initial:
                self._parent_app.set_drawings_snap_to_ohlc(new_snap)
        except Exception:  # noqa: BLE001
            pass
        # Commit UI scale. The live-preview call already applied the
        # new scale to every named font; OK just persists the choice
        # to settings.json. We always call the setter even if scale
        # equals initial (defensive — keeps settings.json in sync with
        # the current state). Audit ``font-scaling``.
        try:
            new_scale = self._parse_ui_scale(self._ui_scale_var.get())
            self._parent_app.set_ui_scale(new_scale)
        except Exception:  # noqa: BLE001
            pass
        # Commit sandbox reference symbol. Writes settings.json only
        # when the value actually changed; uppercases + strips so a
        # user-typed " spy " behaves the same as the canonical "SPY".
        # Audit ``sandbox-ref-symbol``.
        try:
            from .. import defaults as _defaults_mod
            from .. import settings as _settings_mod
            new_ref = (self._sandbox_ref_var.get() or "").strip().upper()
            if new_ref and new_ref != self._sandbox_ref_initial.strip().upper():
                _settings_mod.set("sandbox_reference_symbol", new_ref)
                _defaults_mod.reload()
            # Commit skip-detailed-journal toggle. Audit
            # ``mandatory-journal-skip``.
            new_skip = bool(self._skip_journal_var.get())
            if new_skip != self._skip_journal_initial:
                _settings_mod.set("sandbox_skip_detailed_journal", new_skip)
                _defaults_mod.reload()
            # Commit splash-on-startup toggle. Audit
            # ``settings-splash-disable``.
            new_splash = bool(self._splash_var.get())
            if new_splash != self._splash_initial:
                _settings_mod.set("splash_enabled", new_splash)
                _defaults_mod.reload()
            # Commit update-check startup preferences.
            new_update_check = bool(self._update_check_var.get())
            if new_update_check != self._update_check_initial:
                _settings_mod.set("update_check_on_startup", new_update_check)
                _defaults_mod.reload()
            # Commit watchlist pin cap. Audit ``pinned-watchlist-cap``.
            try:
                new_wl_cap = int(self._wl_cap_var.get())
            except (tk.TclError, ValueError):
                new_wl_cap = self._wl_cap_initial
            new_wl_cap = max(1, min(20, new_wl_cap))
            if new_wl_cap != self._wl_cap_initial:
                _settings_mod.set("watchlist_max_pinned", new_wl_cap)
                _defaults_mod.reload()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()


# --- watchlist dialog ---------------------------------------------------

class _WatchlistDialog(BaseModalDialog):
    """CRUD + import/export dialog for named watchlists.

    Migrated to :class:`BaseModalDialog` — the base class owns
    ``transient`` / ``grab_set`` / ESC+Return keybindings / geometry
    persistence via ``_finalize_modal``. Close semantics (rebuild
    pinned sub-tabs when pin state changed) live in :meth:`_on_close`,
    wired as the ``cancel`` callback so WM_DELETE / ESC both route
    through it.
    """

    def __init__(self, parent: ChartApp) -> None:
        super().__init__(
            parent,
            title="Watchlists",
            geometry_key="dlg.watchlists",
            default_geometry="720x500",
        )
        self._parent_app = parent
        self._mgr: WatchlistManager | None = parent._watchlists

        frm = ttk.Frame(self, padding=8)
        frm.pack(fill=tk.BOTH, expand=True)

        # Names pane on the left: a two-column Treeview ``[Pinned?][Name]``
        # so the user can see which lists are already pinned (showing
        # in sub-tabs) and toggle pin status without leaving the dialog.
        left = ttk.Frame(frm)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        ttk.Label(left, text="Watchlists").pack(anchor="w")
        self._names = ttk.Treeview(
            left,
            columns=("pin", "name"),
            show="headings", height=10, selectmode="browse",
        )
        self._names.heading("pin", text="Pin")
        self._names.heading("name", text="Name")
        self._names.column("pin", width=40, anchor="center", stretch=False)
        self._names.column("name", width=180, anchor="w")
        self._names.pack(fill=tk.Y, expand=False)
        self._names.bind("<<TreeviewSelect>>", self._on_select_name)
        # Track whether pin state changed so we can rebuild sub-tabs on close.
        self._pin_dirty: bool = False

        name_btns = ttk.Frame(left)
        name_btns.pack(fill=tk.X, pady=4)
        ttk.Button(name_btns, text="New", command=self._on_new).pack(side=tk.LEFT)
        ttk.Button(name_btns, text="Rename", command=self._on_rename).pack(side=tk.LEFT)
        ttk.Button(name_btns, text="Delete", command=self._on_delete).pack(side=tk.LEFT)

        pin_btns = ttk.Frame(left)
        pin_btns.pack(fill=tk.X, pady=(0, 4))
        self._pin_btn = ttk.Button(
            pin_btns, text="Pin", command=self._on_pin)
        self._pin_btn.pack(side=tk.LEFT)
        self._unpin_btn = ttk.Button(
            pin_btns, text="Unpin", command=self._on_unpin)
        self._unpin_btn.pack(side=tk.LEFT)

        # Tickers on the right.
        right = ttk.Frame(frm)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(right, text="Tickers").pack(anchor="w")
        self._tickers = tk.Listbox(right, height=12, exportselection=False)
        apply_listbox_theme(self._tickers, current_theme(parent))
        self._tickers.pack(fill=tk.BOTH, expand=True)

        t_btns = ttk.Frame(right)
        t_btns.pack(fill=tk.X, pady=4)
        ttk.Button(t_btns, text="Add", command=self._on_add_ticker).pack(side=tk.LEFT)
        ttk.Button(t_btns, text="Remove", command=self._on_remove_ticker).pack(side=tk.LEFT)
        # Pack Export first so it sits rightmost; Import then sits to
        # its left, giving the visual order [Import…] [Export…] which
        # matches the audit's recommendation.
        ttk.Button(t_btns, text="Export…", command=self._on_export).pack(side=tk.RIGHT)
        ttk.Button(t_btns, text="Import…", command=self._on_import).pack(side=tk.RIGHT)

        # Button row. The "Save and Close" button mirrors the
        # Manage-Indicators dialog paradigm: a single click both
        # persists the current watchlist set to disk and dismisses the
        # dialog. The legacy "Close" button is preserved so users who
        # used the dialog purely as a viewer (or who want to discard
        # an in-flight change by exiting before pressing Save) keep
        # that affordance.
        btn_row = ttk.Frame(frm)
        btn_row.pack(side=tk.BOTTOM, anchor="e", pady=(8, 0))
        ttk.Button(btn_row, text="Close", command=self._on_close).pack(
            side=tk.RIGHT)
        ttk.Button(
            btn_row, text="Save and Close",
            command=self._on_save_and_close,
        ).pack(side=tk.RIGHT, padx=(0, 6))

        self._refresh_names()
        # BaseModalDialog: wires WM_DELETE_WINDOW + ESC → _on_close
        # (rebuilds pinned sub-tabs if pin state changed) and
        # Enter → _on_save_and_close. Both routes pass through the
        # pin-rebuild path so a user dismissing with any gesture sees
        # an up-to-date sub-tab strip.
        self._finalize_modal(
            primary=self._on_save_and_close, cancel=self._on_close,
        )

    # --- lifecycle -----------------------------------------------------
    def _on_close(self) -> None:
        """Close the dialog; rebuild pinned sub-tabs if pins changed."""
        if self._pin_dirty:
            try:
                self._parent_app._rebuild_watchlist_subtabs()
            except Exception:  # noqa: BLE001
                pass
        self.destroy()

    def _on_save_and_close(self) -> None:
        """Persist watchlists to disk, then close.

        Mirrors the Manage Indicators dialog's primary action. The
        save routes through ``ChartApp._on_menu_save_watchlists`` so
        the recent-files list, loaded-path tracking, and dirty-title
        suffix all stay consistent with ``File -> Save Watchlists``.

        If the watchlist set has never been saved before, the
        underlying ``ConfigManager.save_watchlists`` falls through to
        ``save_watchlists_as`` which prompts for a destination. When
        the user cancels that prompt, no path is recorded and we
        stay open so the in-flight changes aren't lost.
        """
        mgr = self._mgr
        had_path_before = False
        try:
            had_path_before = bool(mgr and mgr.loaded_path())
        except Exception:  # noqa: BLE001
            had_path_before = False
        try:
            self._parent_app._on_menu_save_watchlists()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Save Watchlists",
                f"Could not save:\n\n{exc}",
                parent=self,
            )
            return
        if not had_path_before:
            # save fell through to save_watchlists_as; if the user
            # cancelled the file picker, no path was recorded — keep
            # the dialog open so they can retry.
            try:
                saved = bool(mgr and mgr.loaded_path())
            except Exception:  # noqa: BLE001
                saved = False
            if not saved:
                return
        self._on_close()

    # --- helpers -------------------------------------------------------
    def _refresh_names(self) -> None:
        """Rebuild the names Treeview: one row per watchlist, with a
        check mark in the pin column for currently-pinned entries.
        Preserves the currently-selected name across refresh when
        possible.
        """
        prev = self._selected_name()
        for iid in self._names.get_children():
            self._names.delete(iid)
        if self._mgr is None:
            self._update_pin_buttons()
            return
        pinned = set(self._mgr.pinned_names())
        for name in self._mgr.list_names():
            mark = "✓" if name in pinned else ""
            # iid = name (names are unique).
            self._names.insert("", "end", iid=name, values=(mark, name))
        # Restore or default selection.
        if prev and prev in self._mgr.list_names():
            self._names.selection_set(prev)
            self._names.focus(prev)
        elif self._names.get_children():
            first = self._names.get_children()[0]
            self._names.selection_set(first)
            self._names.focus(first)
        self._on_select_name()
        self._update_pin_buttons()

    def _selected_name(self) -> str | None:
        sel = self._names.selection()
        if not sel:
            return None
        # iid is the name.
        return sel[0]

    def _update_pin_buttons(self) -> None:
        """Enable/disable Pin / Unpin buttons based on selection + cap."""
        if self._mgr is None:
            try:
                self._pin_btn.state(["disabled"])
                self._unpin_btn.state(["disabled"])
            except Exception:  # noqa: BLE001
                pass
            return
        name = self._selected_name()
        pinned = self._mgr.pinned_names()
        at_cap = len(pinned) >= self._mgr.MAX_PINNED
        is_pinned = name in pinned if name else False
        try:
            if not name or is_pinned or at_cap:
                self._pin_btn.state(["disabled"])
            else:
                self._pin_btn.state(["!disabled"])
            if not name or not is_pinned:
                self._unpin_btn.state(["disabled"])
            else:
                self._unpin_btn.state(["!disabled"])
        except Exception:  # noqa: BLE001
            pass

    def _on_select_name(self, _event=None) -> None:
        self._tickers.delete(0, tk.END)
        name = self._selected_name()
        self._update_pin_buttons()
        if not name or self._mgr is None:
            return
        wl = self._mgr.get(name)
        if wl is None:
            return
        for t in wl.tickers:
            self._tickers.insert(tk.END, t)

    # --- name actions --------------------------------------------------
    def _on_new(self) -> None:
        if self._mgr is None:
            return
        name = _prompt_string(self, "New Watchlist", "Name:")
        if not name:
            return
        try:
            self._mgr.create(name, [])
        except ValueError as e:
            messagebox.showerror("Watchlists", str(e), parent=self)
            return
        self._refresh_names()

    def _on_rename(self) -> None:
        if self._mgr is None:
            return
        old = self._selected_name()
        if not old:
            return
        new = _prompt_string(self, "Rename Watchlist", "New name:", initial=old)
        if not new or new == old:
            return
        try:
            self._mgr.rename(old, new)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Watchlists", str(e), parent=self)
            return
        # Manager preserves pin position across rename, so mark dirty
        # iff the renamed list was pinned (sub-tab label must update).
        if old in self._mgr.pinned_names() or new in self._mgr.pinned_names():
            self._pin_dirty = True
        self._refresh_names()

    def _on_delete(self) -> None:
        if self._mgr is None:
            return
        name = self._selected_name()
        if not name:
            return
        if not messagebox.askyesno("Watchlists",
                                    f"Delete watchlist '{name}'?",
                                    parent=self):
            return
        was_pinned = name in self._mgr.pinned_names()
        self._mgr.delete(name)
        if was_pinned:
            self._pin_dirty = True
        self._refresh_names()

    # --- pin actions ---------------------------------------------------
    def _on_pin(self) -> None:
        if self._mgr is None:
            return
        name = self._selected_name()
        if not name:
            return
        try:
            self._mgr.pin(name)
        except ValueError as e:
            messagebox.showerror("Watchlists", str(e), parent=self)
            return
        except KeyError:
            return
        self._pin_dirty = True
        self._refresh_names()

    def _on_unpin(self) -> None:
        if self._mgr is None:
            return
        name = self._selected_name()
        if not name:
            return
        if name not in self._mgr.pinned_names():
            return
        self._mgr.unpin(name)
        self._pin_dirty = True
        self._refresh_names()

    # --- ticker actions -----------------------------------------------
    def _on_add_ticker(self) -> None:
        if self._mgr is None:
            return
        name = self._selected_name()
        if not name:
            return
        t = _prompt_string(self, "Add Ticker", "Symbol:")
        if not t:
            return
        self._mgr.add_ticker(name, t)
        self._on_select_name()

    def _on_remove_ticker(self) -> None:
        if self._mgr is None:
            return
        name = self._selected_name()
        if not name:
            return
        sel = self._tickers.curselection()
        if not sel:
            return
        t = self._tickers.get(sel[0])
        self._mgr.remove_ticker(name, t)
        self._on_select_name()

    # --- import / export ----------------------------------------------
    def _on_import(self) -> None:
        if self._mgr is None:
            return
        path = filedialog.askopenfilename(
            parent=self, title="Import watchlists",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            incoming, _imported_pinned = _import_watchlists_from_file(path)
            # Merge into current set (preserves existing watchlists;
            # incoming entries with the same name overwrite). For a full
            # replace, use File -> Load Watchlists from the main menu.
            # Imported pins are surfaced to ``import_watchlists`` so any
            # pins the file declared get appended (de-duped, capped at
            # MAX_PINNED) — earlier behaviour dropped them silently
            # which surprised users who'd carefully ordered their pins
            # in the exported file.
            self._mgr.import_watchlists(
                incoming, mode="merge", pinned=_imported_pinned,
            )
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Watchlists", str(e), parent=self)
            return
        self._refresh_names()

    def _on_export(self) -> None:
        if self._mgr is None:
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Export watchlists",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        try:
            # Route through the manager so loaded_path / dirty tracking
            # stays consistent with File -> Save Watchlists.
            self._mgr.save_to_file(path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Watchlists", str(e), parent=self)
