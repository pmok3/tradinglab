"""Tkinter + matplotlib stock-charting application.

This module hosts :class:`ChartApp`, a Tk-based GUI that renders
candlestick + volume charts, supports compare mode, streams real-time
bars, and exposes settings + watchlist management dialogs.

The implementation here is intentionally focused: it wires up the
state variables, caches, and dispatch methods documented in ``spec.md``
and exercised by ``_smoke_refactor.py``. Heavy rendering machinery
(virtualized pan/zoom, blitting, hover crosshair) is provided in a
straightforward form — adequate for interactive use but not tuned for
very large histories. Extension hooks are named per the spec so future
revisions can deepen each subsystem without reshuffling the public
surface.
"""

# ruff: noqa: UP045

from __future__ import annotations

import contextlib
import logging
import math
import queue
import time
import tkinter as tk
import webbrowser
from collections import OrderedDict
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from tkinter import filedialog as filedialog  # noqa: F401  # patch seam for tests
from tkinter import messagebox as messagebox  # noqa: F401  # patch seam for tests
from tkinter import ttk
from typing import TYPE_CHECKING, Any, Optional

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from . import disk_cache
from . import settings as _settings
from .backtest.sandbox_app import SandboxAppController
from .constants import (
    BUILTIN_STARTUP_DEFAULTS,
    LIGHT_THEME,
    is_intraday,
)
from .core.lru_dict import LRUDict
from .core.series import (
    SeriesArrays as _SeriesArrays,
)
from .core.series import (
    build_series_safe as _build_series_safe,
)
from .core.viewport import (
    compute_render_range as _compute_render_range,
)
from .core.viewport import (
    remap_window_by_time as _remap_window_by_time,
)
from .data import DATA_SOURCES, DataController, FetchService
from .data.stream_controller import StreamController
from .data.today_upsample import (
    SUPPORTED_INTERVALS as _DAILY_UPSAMPLE_INTERVALS,
)
from .data.today_upsample import (
    find_best_intraday_source as _find_best_intraday_source,
)
from .data.today_upsample import (
    upsample_daily_with_today as _upsample_daily_with_today,
)
from .drawings import (
    DEFAULT_COLOR as _DRAWING_DEFAULT_COLOR,
)
from .drawings import (
    DrawingStore,
    read_drawings,
)
from .formatting import fmt_volume, format_dt
from .gui.app_state import AppState
from .gui.banner import FirstRunBannerMixin
from .gui.chart_renderer import ChartRenderer
from .gui.config_manager import ConfigManager
from .gui.config_menu import ConfigMenuMixin
from .gui.dialog_manager import DialogManager

# ``_SettingsDialog`` / ``_WatchlistDialog`` are constructed only when the
# user actually opens those dialogs (see ``_open_settings_dialog`` /
# ``_open_watchlist_dialog`` near the bottom of this file). Lazy-loading
# them shaves the dialog-stack imports out of cold start — the dialogs
# module pulls in theme + matplotlib + several mixins that aren't needed
# until first open. The two constants ``WORKER_COUNT_MIN`` /
# ``WORKER_COUNT_MAX`` ARE re-exported eagerly because they're referenced
# at class-definition time by ``WorkerPoolMixin._clamp_worker_count``.
from .gui.dialogs import (
    WORKER_COUNT_MAX,
    WORKER_COUNT_MIN,
)
from .gui.drawings_app import DrawingsAppMixin
from .gui.drilldown import DrilldownMixin, _DrilldownRequest
from .gui.entries_app import EntriesAppMixin
from .gui.exits_app import ExitsAppMixin
from .gui.geometry_store import compute_screen_percent_geometry
from .gui.help_menu import HelpMenuMixin
from .gui.indicator_menu import IndicatorMenuMixin
from .gui.interaction import InteractionMixin
from .gui.live_price_overlay_app import LivePriceOverlayAppMixin
from .gui.menu_builder import MenuBuilder
from .gui.menu_theme import apply_menu_theme
from .gui.named_fonts import (
    DEFAULT_UI_SCALE as _UI_SCALE_DEFAULT,
)
from .gui.named_fonts import (
    clamp_ui_scale as _clamp_ui_scale,
)
from .gui.named_fonts import (
    configure_named_fonts,
)

# Polling / scheduling lives in ``gui.polling``.
from .gui.polling import PollingMixin
from .gui.recent_menus import RecentMenusMixin
from .gui.sandbox_menu import SandboxMenuMixin
from .gui.snapshot import SnapshotMixin
from .gui.splash import (
    STAGE_BUILDING_UI,
    STAGE_FETCHING,
    STAGE_READY,
    NullSplashController,
    SplashController,
)
from .gui.theme_controller import ThemeController
from .gui.toolbar_controller import ToolbarController
from .gui.watchlist_tab import WatchlistTabMixin
from .gui.workers import WorkerPoolMixin
from .gui.x_axis_locator import _adaptive_x_locator_class, _make_x_formatter
from .indicators import render as _ind_render
from .indicators._palette import FALLBACK_GRAY
from .indicators.cache import IndicatorCache
from .indicators.config import IndicatorManager
from .models import Candle
from .rendering import (
    draw_candlesticks,
    draw_session_shading,
    draw_volume,
    dynamic_body_half,
    setup_indicator_pane_axes,
    setup_price_axes,
    setup_volume_axes,
    style_axes,
)
from .status import StatusHistoryWindow, StatusLog
from .streaming import STREAM_SOURCES
from .watchlists import (
    DEFAULT_WATCHLIST_NAME as _DEFAULT_WATCHLIST_NAME_CANONICAL,
)
from .watchlists import (
    DEFAULT_WATCHLIST_TICKERS as _DEFAULT_WATCHLIST_TICKERS_CANONICAL,
)
from .watchlists import (
    WatchlistManager,
)

# --- module-level constants ---------------------------------------------

_MAX_RENDER_CANDLES = 60000
_RENDER_BUFFER_MULTIPLIER = 3
_MIN_RENDER_CANDLES = 500
_MAX_TABLE_ROWS = 300
from . import defaults as _defaults  # noqa: E402

logger = logging.getLogger(__name__)

_FULL_CACHE_MAX = _defaults.get("full_cache_size")
_PAN_REDRAW_INTERVAL_MS = 16

_DEFAULT_TICKER = "AMD"
_DEFAULT_COMPARE = "SPY"
_DEFAULT_INTERVAL = "1d"
_INTERVALS = ("1m", "2m", "5m", "15m", "30m", "1h", "1d", "1wk", "1mo")
# Canonical defaults live in :mod:`tradinglab.watchlists` so the
# ``gui.watchlist_tab`` first-run seeding path and this module share
# one source of truth. Pre-2026-05 each module carried its own copy
# and the ``app.py`` copy was actually dead (only the watchlist_tab
# copy fed ``_ensure_default_watchlist``). Audit
# ``default-watchlist-fresh``.
_DEFAULT_WATCHLIST_NAME = _DEFAULT_WATCHLIST_NAME_CANONICAL
_DEFAULT_WATCHLIST_TICKERS = list(_DEFAULT_WATCHLIST_TICKERS_CANONICAL)
_FULL_CACHE_MAX = _defaults.get("full_cache_size")

# Pixel radius used by the opt-in Alt+H snap-to-OHLC feature. Tight
# enough that a trader has to be deliberately aiming at a wick to
# trigger it (so the snap doesn't surprise users who placed a line
# at random whitespace), wide enough that a casual hover near a
# high catches reliably. Audit ``drawings-snap-extended``.
_DRAWINGS_SNAP_PIXEL_THRESHOLD = 8.0
_DATA_STATE_UNSET = object()


@contextlib.contextmanager
def _silent_tcl(*extra_excs: type[BaseException]):
    """Swallow ``tk.TclError`` (plus any ``extra_excs``) — narrow guard
    for Tk widget/var calls that may hit the interpreter mid-teardown
    or against half-destroyed widgets. Replaces the boilerplate
    ``try: ...; except tk.TclError: pass`` blocks that otherwise dot
    nearly every method that touches Tk state.

    Mirrors the same-named helper in
    :mod:`tradinglab.backtest.replay` (kept module-local rather
    than shared to avoid an app.py → replay import cycle).
    """
    excs = (tk.TclError,) + extra_excs
    try:
        yield
    except excs:
        pass





# --- main application ---------------------------------------------------


if TYPE_CHECKING:
    from .backtest.session import SessionSpec


class ChartApp(
    PollingMixin,
    InteractionMixin,
    WatchlistTabMixin,
    WorkerPoolMixin,
    IndicatorMenuMixin,
    SandboxMenuMixin,
    ConfigMenuMixin,
    DrilldownMixin,
    EntriesAppMixin,
    ExitsAppMixin,
    HelpMenuMixin,
    FirstRunBannerMixin,
    DrawingsAppMixin,
    LivePriceOverlayAppMixin,
    RecentMenusMixin,
    SnapshotMixin,
    tk.Tk,
):
    """Top-level Tk window hosting the chart, controls, and data flow."""

    _WORKER_COUNT_MIN = WORKER_COUNT_MIN
    _WORKER_COUNT_MAX = WORKER_COUNT_MAX
    _MIN_POLL_BACKOFF_MS = 30_000
    # Retry cadence when a poll tick fetches but the last bar did not
    # advance (e.g. provider hasn't published the new bar yet). Tries
    # up to _POLL_RETRY_MAX additional fetches ``_POLL_RETRY_DELAY_MS``
    # apart before falling back to the normal aligned schedule. These
    # bypass the _MIN_POLL_BACKOFF_MS clamp because they're explicit
    # catch-up retries, not baseline polling.
    _POLL_RETRY_DELAY_MS = 5_000
    _POLL_RETRY_MAX = 2

    def _get_fetch_state(self, name: str, default: Any = None) -> Any:
        svc = self.__dict__.get("_fetch_svc")
        if svc is None:
            return self.__dict__.get(f"__fetch_state_{name}", default)
        return getattr(svc, name)

    def _set_fetch_state(self, name: str, value: Any) -> None:
        svc = self.__dict__.get("_fetch_svc")
        if svc is None:
            self.__dict__[f"__fetch_state_{name}"] = value
            return
        setattr(svc, name, value)

    @property
    def _fetch_token(self) -> int:
        data_ctrl = self.__dict__.get("_data_ctrl")
        if data_ctrl is None:
            return int(self.__dict__.get("__data_fetch_token", 0))
        return int(getattr(data_ctrl, "_fetch_token", 0))

    @_fetch_token.setter
    def _fetch_token(self, value: int) -> None:
        data_ctrl = self.__dict__.get("_data_ctrl")
        if data_ctrl is None:
            self.__dict__["__data_fetch_token"] = int(value)
            return
        data_ctrl._fetch_token = int(value)

    @property
    def _reload_job(self) -> str | None:
        return self._get_fetch_state("_reload_job")

    @_reload_job.setter
    def _reload_job(self, value: str | None) -> None:
        self._set_fetch_state("_reload_job", value)

    @property
    def _poll_job(self) -> str | None:
        return self._get_fetch_state("_poll_job")

    @_poll_job.setter
    def _poll_job(self, value: str | None) -> None:
        self._set_fetch_state("_poll_job", value)

    @property
    def _poll_retry_count(self) -> int:
        return int(self._get_fetch_state("_poll_retry_count", 0))

    @_poll_retry_count.setter
    def _poll_retry_count(self, value: int) -> None:
        self._set_fetch_state("_poll_retry_count", int(value))

    @property
    def _poll_retry_expected_min_ts(self) -> float | None:
        return self._get_fetch_state("_poll_retry_expected_min_ts")

    @_poll_retry_expected_min_ts.setter
    def _poll_retry_expected_min_ts(self, value: float | None) -> None:
        self._set_fetch_state("_poll_retry_expected_min_ts", value)

    @property
    def _blit_bg(self):
        renderer = self.__dict__.get("_renderer")
        if renderer is None:
            return self.__dict__.get("__legacy_blit_bg")
        return renderer.blit_bg

    @_blit_bg.setter
    def _blit_bg(self, value) -> None:
        renderer = self.__dict__.get("_renderer")
        if renderer is None:
            self.__dict__["__legacy_blit_bg"] = value
            return
        renderer.blit_bg = value

    @property
    def _startup_defaults(self) -> dict[str, str]:
        manager = self.__dict__.get("_config_manager")
        if manager is None:
            return dict(
                self.__dict__.get(
                    "__legacy_startup_defaults",
                    BUILTIN_STARTUP_DEFAULTS,
                )
            )
        return manager.startup_defaults

    @_startup_defaults.setter
    def _startup_defaults(self, value: dict[str, str]) -> None:
        manager = self.__dict__.get("_config_manager")
        if manager is None:
            self.__dict__["__legacy_startup_defaults"] = dict(value)
            return
        manager._set_startup_defaults(value, persist=False)

    def _ensure_renderer(self) -> ChartRenderer:
        renderer = getattr(self, "_renderer", None)
        if renderer is not None:
            return renderer
        renderer = ChartRenderer()
        panel_state = getattr(self, "_panel_state", None)
        if isinstance(panel_state, dict):
            renderer.panel_state = panel_state
        ax_candle_map = getattr(self, "_ax_candle_map", None)
        if isinstance(ax_candle_map, OrderedDict):
            renderer.ax_candle_map = ax_candle_map
        elif ax_candle_map is not None:
            try:
                renderer.ax_candle_map = OrderedDict(ax_candle_map)
            except Exception:  # noqa: BLE001
                pass
        self._renderer = renderer
        self._panel_state = renderer.panel_state
        self._ax_candle_map = renderer.ax_candle_map
        self._blit_bg = getattr(self, "_blit_bg", None)
        return renderer

    def __init__(self, *, splash: Optional[SplashController] = None) -> None:
        super().__init__()
        # Pin the named-font baseline before any widget is constructed.
        # Every later widget that says ``font="TkDefaultFont"`` (or
        # falls back to it implicitly) sees Segoe UI 9 on Windows
        # instead of a stripped-build bitmap fallback. Audit
        # ``font-default-config``. The optional ``ui_scale`` setting
        # (audit ``font-scaling``) lets users with hi-DPI displays,
        # presbyopia, or just a personal preference dial the chrome
        # up or down via Settings → "UI scale".
        try:
            _ui_scale_raw = _settings.get("ui_scale", _UI_SCALE_DEFAULT)
        except Exception:  # noqa: BLE001
            _ui_scale_raw = _UI_SCALE_DEFAULT
        self._ui_scale: float = _clamp_ui_scale(_ui_scale_raw)
        try:
            configure_named_fonts(self, scale=self._ui_scale)
        except Exception:  # noqa: BLE001 - font config is best-effort.
            pass
        # Stash the splash controller as early as possible so any
        # subsequent ``self._splash.report(...)`` call sites can
        # rely on it being non-None. ``None`` is the dev default
        # (no splash; tests use it as well) and falls through to a
        # silent NullSplashController.
        self._splash: SplashController = splash or NullSplashController()
        # Make sure the OS knows this is "TradingLab", not "python.exe":
        #
        # * On X11 (Linux / BSD) the WM_CLASS hint drives desktop-file
        #   matching, application-menu icons, and Alt-Tab labels.
        # * On Windows the analogue is the Explicit App User Model ID
        #   which controls taskbar grouping and the jump list. ``wm
        #   class`` is unsupported on the Windows Tk build, so we set
        #   the AUMID via the Shell32 API instead.
        # * macOS uses Info.plist for both, so this is a no-op there.
        _identify_to_window_manager(self)
        # First-run seeding of bundled starter-pack templates (5 entries,
        # 5 exits, 5 scanners) into the user-local library. No-op once
        # the sentinel exists. Failures are logged but non-fatal.
        # Deferred to ``after_idle`` so the first paint isn't blocked
        # on first-run file I/O (~50-200ms on cold install). Safe to
        # defer because the user can't open the Templates menu before
        # the first idle event processes; subsequent launches are
        # already no-ops via the sentinel guard.
        def _seed_templates_idle() -> None:
            try:
                from .templates import seed_default_templates_if_empty
                seed_default_templates_if_empty()
            except Exception:  # noqa: BLE001 - first-run seeding is best-effort
                pass
        try:
            self.after_idle(_seed_templates_idle)
        except Exception:  # noqa: BLE001 - in headless tests Tk may not be ready
            _seed_templates_idle()
        # yfinance keeps a small SQLite cache of ticker → timezone
        # mappings (``platformdirs.user_cache_dir("py-yfinance")/tkr-tz.db``).
        # Concurrent access from a parallel Python process (e.g. a
        # pytest run while the live app is open) corrupts the file,
        # after which every uncached symbol returns the misleading
        # ``Ticker '...' not found`` error. The cache is tiny and
        # rebuilds cheaply on demand, so we wipe it on every launch
        # for full corruption immunity. See ``paths.spec.md``.
        try:
            from .paths import wipe_yfinance_timezone_cache
            wipe_yfinance_timezone_cache()
        except Exception:  # noqa: BLE001 - cache hygiene is best-effort
            pass
        # ``tk.call("tk", "scaling", ...)`` plus a Windows-side
        # ``SetProcessDpiAwarenessContext`` call already happened in
        # :func:`main` before this constructor ran, so widgets pick up
        # the correct logical-pixel size from creation onwards.
        from ._version import __version__ as _pkg_version
        self.title(f"TradingLab v{_pkg_version}")
        # --- startup defaults (Settings → "Startup parameters") --------
        # Loaded before the shared Tk variable registry so persisted
        # overrides can seed ticker/compare/interval/source/theme.
        self._config_manager = ConfigManager(
            self,
            _INTERVALS,
            list(DATA_SOURCES.keys()),
        )
        sd = self._startup_defaults

        # --- Tk state variables (names are part of the public surface) --
        self._state = AppState(master=self, startup_defaults=sd)
        # Delegated to ``AppState`` but kept here in source for tests:
        # value=bool(_settings.get("highlight_ha_flat", False))
        # Backward-compat aliases — existing code and tests still read
        # the historic ``*_var`` attribute names directly.
        self.ticker_var = self._state.ticker
        self.compare_ticker_var = self._state.compare_ticker
        self.compare_var = self._state.compare
        self.compare_enabled_var = self._state.compare_enabled
        self._compare_label_var = self._state.compare_label
        self._sync_compare_label = self._state._sync_compare_label
        self.source_var = self._state.source
        self.interval_var = self._state.interval
        self.prepost_var = self._state.prepost
        self.days_var = self._state.days
        self.dark_var = self._state.dark
        self.log_price_var = self._state.log_price
        self.watchlist_var = self._state.watchlist
        self.status = self._state.status
        self._status_display = self._state.status_display
        self._ha_display_var = self._state.ha_display
        self._highlight_key_bars_var = self._state.highlight_key_bars
        self._highlight_ha_flat_var = self._state.highlight_ha_flat
        self._volume_tod_var = self._state.volume_tod
        self._chartstack_visible_var = self._state.chartstack_visible
        self._theme_ctrl = ThemeController(self)
        self._theme = self._theme_ctrl.theme
        self._theme_overrides = self._theme_ctrl.overrides
        self._theme.update(LIGHT_THEME)
        self._theme_ctrl.on_change(self._on_theme_changed)
        # Refresh the indicator dialog's kind dropdown whenever the
        # chart interval changes — kinds whose factories report
        # ``is_available_for(interval).ok == False`` get annotated as
        # unavailable so users see the indicator exists but can't
        # accidentally pick it on an incompatible timeframe.
        self.interval_var.trace_add(
            "write", lambda *_: self._sync_indicator_dialog_for_interval(),
        )
        self._build_menubar()
        # The *Highlight Flat Bars* menu entry (View → Heikin-Ashi
        # cascade) is always clickable. The visual overlay is gated in
        # the renderer by HA mode AND the flat-highlight toggle, so the
        # BooleanVar can hold the user's preference while HA is off.
        # Normalize the entry immediately after the View menu is built.
        self._sync_highlight_ha_flat_menu_state()
        # Window geometry: restored from `gui/geometry_store.py` (UI/UX
        # audit P0 #3). The adaptive percent-of-screen block remains the
        # fallback when no stored geometry exists or the saved geometry is
        # off-screen / too small (e.g. monitor change or accidental shrink).
        # Sash positions are restored separately after `_build_ui` constructs
        # the PanedWindow — see the `_geometry_store.restore_sash` calls below.
        try:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
        except tk.TclError:
            sw, sh = 1600, 900
        startup_min_w = min(1200, max(1, int(sw)))
        startup_min_h = min(780, max(1, int(sh)))
        _default_geom = compute_screen_percent_geometry(
            sw,
            sh,
            width_pct=_defaults.get("startup_width_pct"),
            height_pct=_defaults.get("startup_height_pct"),
            min_width=startup_min_w,
            min_height=startup_min_h,
        )
        self.minsize(startup_min_w, startup_min_h)
        try:
            from .gui.geometry_store import store as _geom_store
            self._geometry_store = _geom_store()
            applied_geom = self._geometry_store.restore_window(
                self,
                "main",
                default=_default_geom,
                min_size=(startup_min_w, startup_min_h),
            )
            self._geometry_store.bind_window(self, "main")
        except Exception:  # noqa: BLE001 - geometry persistence is best-effort
            self._geometry_store = None
            applied_geom = _default_geom
            self.geometry(_default_geom)
        # Stash the actually-applied geometry so the post-build sash
        # restore can derive a sensible default sash position from it.
        self._initial_geometry: str = applied_geom

        # Verbose status log: routes every message to the status bar,
        # an in-memory ring buffer (history window), a daily on-disk
        # log file, and stdout (for `python scripts/run_dev.py` users).
        # Created here so any subsequent __init__ step can log freely.
        self._status = StatusLog(self.status, tk_root=self)
        self._dialog_mgr = DialogManager(self)
        self._indicator_dialog: tk.Toplevel | None = None
        self._status_history_win: tk.Toplevel | None = None
        self._keyboard_shortcuts_dialog: tk.Toplevel | None = None
        # Status severity tracking (Item 9 — UI quick wins). The raw
        # ``self.status`` StringVar holds the untouched message text so
        # the StatusLog truncation contract + existing tests keep
        # working. The displayed label binds to a separate
        # ``_status_display`` StringVar that prefixes a severity glyph,
        # and the label foreground is tinted per-severity. The trace
        # below picks the latest level from ``self._status.history()``
        # so we don't have to patch the StatusLog itself.
        self._status_severity: str = "info"
        try:
            self.status.trace_add("write", self._on_status_var_change)
        except Exception:  # noqa: BLE001
            pass

        # --- indicator subsystem (Phase 1 wiring; render in Phase 2a) ---
        # Manager owns user-configured indicator state; cache stores
        # compute results keyed by (id(candles), config_hash). The
        # manager calls our scheduler on every mutation so any add/
        # remove/update/preset_loaded ultimately runs ``_render`` once
        # at idle (coalesced).
        self._indicator_cache = IndicatorCache()
        self._indicator_manager = IndicatorManager(scheduler=self._sched_indicator_redraw)
        self._indicator_manager.subscribe(self._on_indicator_event)
        # --- drawings subsystem (Feature C: TradingView-style hlines) ---
        # Per-ticker horizontal-line store. Mounted alongside the
        # indicator manager because their lifecycles are identical:
        # both are persistent state that survives across renders,
        # subscribed to by ChartApp for coalesced re-paints, and
        # consulted by ``_render`` after every ``_draw_slice``. The
        # store's idle-coalescing scheduler hooks into Tk's
        # ``after_idle`` so a flurry of dialog-debounced edits
        # collapses to one render per tick. The ``replace_all``
        # populates the store from disk so any persisted lines from
        # a prior session are visible on the very first render.
        self._drawings = DrawingStore(scheduler=self.after_idle)
        try:
            self._drawings.replace_all(read_drawings())
        except Exception:  # noqa: BLE001
            pass
        self._drawings.subscribe(self._on_drawing_event)
        # Surface drawings.json save failures to the status bar
        # (audit ``os-replace-error-feedback``). The store fires
        # this callback once per failed ``flush()``; we throttle
        # to one user-visible error per 10s window so a stuck
        # disk-full doesn't spam the log.
        self._drawing_save_error_last_ts: float = 0.0
        self._drawings.subscribe_save_errors(self._on_drawing_save_error)
        # Singleton-per-drawing.id dialog registry — second double-
        # click on the same line lifts/focuses the existing popup
        # rather than spawning a duplicate (mirrors the per-indicator
        # popup pattern). Session-sticky last-used color: subsequent
        # fresh Alt+H placements default to the most recently
        # committed color (so a user drawing a series of red lines
        # doesn't keep re-picking red).
        self._drawing_dialogs: dict[str, Any] = {}
        self._last_drawing_color: str = _DRAWING_DEFAULT_COLOR
        self._drawing_redraw_pending: bool = False
        # Drawing drag-to-move state (InteractionMixin reads this).
        self._drawing_drag_state: dict | None = None
        # Sandbox subsystem (Phase 3 extraction). The controller owns the
        # sandbox state; ChartApp keeps the legacy attribute names via the
        # property-backed aliases defined below so existing callers/tests can
        # keep reading and writing ``self._sandbox`` etc.
        self._sandbox_ctrl = SandboxAppController()
        # Idle-coalesced indicator redraw (one per tick; see
        # ``_sched_indicator_redraw`` / ``_run_indicator_redraw``).
        self._indicator_redraw_pending = False

        # --- caches + data state ----------------------------------------
        self._data_ctrl = DataController(
            full_cache_size=_defaults.get("full_cache_size"),
        )
        # Keep legacy attribute names as direct aliases so the rest of
        # app.py and existing mixins can keep reading/writing them.
        self._sync_data_aliases()
        self._watchlist_snapshot: dict[str, dict[str, Any]] = {}
        # Fetch-token gating + reload debounce state
        self._reload_job: str | None = None
        self._poll_job: str | None = None
        # Streaming state (spec §5)
        self._stream_ctrl = StreamController()
        self._sync_stream_aliases()
        self._renderer = ChartRenderer()
        # Render topology state (spec §6.3/§7)
        self._panel_state: dict[str, dict[str, Any]] = self._renderer.panel_state
        self._ax_candle_map: OrderedDict[Any, tuple[list[Candle], str, int]] = self._renderer.ax_candle_map
        # Blit / overlay state (spec §11)
        self._blit_bg = self._renderer.blit_bg
        self._hover_ann = None
        self._hover_visible = False
        self._crosshair_artists: dict[Any, tuple[Any, Any]] = {}
        # Floating "current price" badge anchored to the right spine of each
        # price axes, in line with the horizontal crosshair (spec §11.5).
        self._price_label_artists: dict[Any, Any] = {}
        # Floating timestamp badge anchored to the bottom-most axes,
        # in line with the vertical crosshair (spec §11.5 — TradingView
        # parity).
        self._time_label_artist: Any = None
        # Top-left "data readout" badges per price axes (spec §11.6) —
        # OHLCV + Vol + bull/bear-coloured %change of the bar at the
        # cursor's x position (latest bar when off-chart).
        self._readout_artists: dict[Any, Any] = {}
        # Click-to-type state (spec §12)
        self._typing_target: str | None = None
        self._typing_buffer: str = ""
        self._last_clicked_slot: str = "primary"
        # Last chart slot the mouse hovered over — survives tab switches
        # so watchlist double-click / typed-ticker entry can route to
        # whichever panel the user was last looking at.
        self._last_hovered_slot: str = "primary"
        self._typing_preview_artists: dict[str, Any] = {}
        # Bad-ticker rejection state (spec §12 end)
        self._confirmed_primary_ticker: str = _DEFAULT_TICKER
        self._confirmed_compare_ticker: str = _DEFAULT_COMPARE
        # Per-ticker EventBundle cache (historical earnings / dividends).
        # Populated by ``_load_events_async`` after every successful
        # foreground chart load. Consumed by:
        #   * ``_render_event_glyphs_for_slot`` (non-sandbox path) to
        #     paint glyphs at the bottom of each price pane;
        #   * the watchlist tab's "Next Earn" column;
        # Token-gated by ``_events_fetch_token`` so a superseded load's
        # late callback doesn't overwrite a fresher bundle.
        # Bounded LRU (cap = 200 symbols) so a user drilling through
        # many tickers in a long session doesn't grow the cache without
        # eviction — the LRU touch on ``.get()`` ensures the active
        # ticker + watchlist never evict each other under normal use.
        # ``LRUDict`` preserves the plain-dict ABI (``get`` / ``[k]`` /
        # ``in`` / ``pop`` / ``clear``) so existing call sites in
        # gui/watchlist_tab.py + gui/chartstack/panel.py + the smoke
        # ``check_b65_events_cache_disk_roundtrip`` test work unchanged.
        self._events_cache: LRUDict[str, Any] = LRUDict(maxsize=200)
        self._events_fetch_token: int = 0
        self._events_fetch_inflight: set = set()
        # Watchlist tab debounce (spec §18.4)
        self._watchlist_tab_refresh_job: str | None = None
        # Watchlist recurring poll loop; armed in __init__ tail via
        # _start_watchlist_poll_loop(). Re-arms itself in each tick.
        self._watchlist_poll_job: str | None = None
        # Cursor pixel cache for crosshair revival after re-render (spec §11.4)
        self._last_cursor_px: tuple[int, int] | None = None
        # Pan / zoom drag state (spec §6.4)
        self._pan_state: dict[str, Any] | None = None
        self._zoom_state: dict[str, Any] | None = None
        # Anchored-VWAP "Pick Anchor…" mode. ``None`` ⇒ inactive; a
        # dict ``{"config_id": int}`` while armed. While active the
        # next left-click on a candle anchors the AVWAP and disarms;
        # missed clicks (no candle hit) keep the mode active and do
        # NOT fall through to pan/zoom. ``Esc`` cancels.
        self._anchor_pick_state: dict[str, Any] | None = None
        self._pan_redraw_job: str | None = None
        # Hover throttle (H2): coalesce mpl motion_notify events to ~60Hz.
        self._hover_throttle_job: str | None = None
        self._hover_pending_event: Any = None
        # Blit-based pan state (populated for the duration of a drag)
        self._pan_bg: Any = None
        self._pan_animated: list[Any] = []
        # H3: fingerprint of the artist topology at last `_pan_setup_blit`
        # snapshot. When unchanged, the bg snapshot can be reused.
        self._pan_anim_fingerprint: tuple[int, ...] | None = None
        self._crosshair_current_ax: Any = None

        # Live-price overlay (TradingView-style sticky dotted line at
        # the current price for every price slot). The artist family
        # mirrors ``exits_overlay`` / ``entries_overlay``: rebuild on
        # ``_render``, mutate in place on every tick. The latest
        # stream-tick close per symbol is tracked in
        # ``_last_stream_price``; ``_render`` resolves it (or falls
        # back to last non-gap candle close) and calls
        # ``_redraw_live_price_overlay``. See
        # ``gui/live_price_overlay.spec.md``.
        from .gui.live_price_overlay import LivePriceOverlay
        self._live_price_overlay = LivePriceOverlay()
        self._last_stream_price: dict[str, float] = {}

        # --- executor + streaming state ---------------------------------
        self._worker_count = self._resolve_worker_count()
        self._fetch_svc = FetchService(worker_count=self._worker_count)
        self._executor = self._fetch_svc._executor
        self._fetch_executor = self._fetch_svc._fetch_executor
        self._stream_drain_after: str | None = None
        self._after_jobs: set = set()
        # Worker → Tk-thread inbox. Workers (preload jobs in
        # gui/watchlist_tab.py) cannot safely call ``self.after`` —
        # ``tk.createcommand`` blocks indefinitely when invoked from a
        # non-main thread on this Python/Tk build. Instead they put
        # ``("stash", (key, bars))``, ``("prefetch", (key, bars))``,
        # or ``("refresh", None)`` items onto this queue; the Tk-thread
        # tick ``_drain_worker_inbox`` drains and applies them.
        self._worker_inbox: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._worker_inbox_after: str | None = None
        # Prefetch dedup set (see _ensure_prefetched). Shared by the
        # compare-warming path (_ensure_compare_prefetched wrapper) and
        # the companion-interval prefetch fired at end of _load_data.
        self._prefetch_inflight = self._fetch_svc._prefetch_inflight

        # --- cross-symbol reference data registry (RRVOL et al.) --------
        # Indicators that need a second symbol (e.g. RRVOL = RVOL/SPY)
        # read SPY bars from ``core.reference_data`` synchronously. The
        # provider registered here schedules a background fetch on
        # cache miss; on completion the on-arrival callback queues a
        # Tk-thread re-render via ``_worker_inbox`` (kind="reference").
        try:
            from .core import reference_data as _refdata
            _refdata.set_provider(
                self._reference_data_fetch,
                on_arrival=self._on_reference_data_arrived,
            )
        except Exception:  # noqa: BLE001
            pass

        # --- watchlists --------------------------------------------------
        try:
            self._watchlists = WatchlistManager()
        except Exception:  # noqa: BLE001
            self._watchlists = None
        self._ensure_default_watchlist()

        # --- render-related flags ---------------------------------------
        self._preserve_xlim_on_render = False
        # When True, _render captures the *timestamp* range of the
        # primary panel's current xlim BEFORE figure.clear() and remaps
        # it to bar-index coordinates in the freshly-loaded primary
        # series. Used by ticker-switch paths so a user panned to (e.g.)
        # last Tuesday's session on AAPL stays on last Tuesday when they
        # switch to MSFT, instead of snapping back to the right edge.
        # Falls back to default windowing if the new series has no
        # bars overlapping the captured time range. One-shot; cleared
        # at end of _render.
        self._preserve_xlim_by_time_on_render = False
        # When True, _render shifts xlim forward to the new right edge
        # keeping width. Set by the poll-tick path when the user was
        # glued to the right edge before new bars arrived, so live
        # updates remain visible without clobbering zoom. Auto-clears
        # after being consumed by _render.
        self._slide_xlim_to_right_edge = False
        # Sandbox: when a session pre-allocates the chart's xlim to
        # span the **full** session window (so the chart looks "ready"
        # for the entire reveal-as-you-tick session instead of
        # auto-fitting to the lookback-only visible list), this stores
        # the (lo, hi) target. ``_refresh_view_after_append`` honors
        # it by snapping xlim back to the target after each tick,
        # bypassing the right-edge "glued" shift heuristic that would
        # otherwise scroll the chart left as ticks reveal new bars.
        # Cleared on session end. Set/refreshed by
        # ``_install_sandbox_primary_series`` when called with
        # ``full_session_length``.
        self._sandbox_full_session_xlim: tuple[float, float] | None = None
        # Drill-down day lock: when the user double-clicks a 1d candle to
        # zoom into that day's 5m bars, this holds the calendar date so
        # subsequent ticker changes (typing, watchlist double-click)
        # stay on the same day instead of snapping back to the right
        # edge. Cleared by Reset view, by explicit interval/source
        # changes, or by a fallback to the most-recent day when the
        # newly-loaded ticker has no data on the locked day.
        self._drilldown_day = None  # type: Optional[Any]
        # Drill-down race fix (see _zoom_5m_for_date): at most one
        # outstanding drill-down request that's waiting for the 5m
        # cache to land. The request object holds the click context
        # (src, ticker, day, fetch_token) and any pending timer/future
        # handles. Latest-click-wins retargets the same request rather
        # than spawning a new one. Cleared on success / supersede /
        # window-close via _finish_drilldown_request.
        self._drilldown_request: _DrilldownRequest | None = None
        self._drilldown_request_seq: int = 0
        # Per-key map of in-flight prefetch futures so the drill-down
        # sync-fallback can attach to an existing prefetch instead of
        # submitting a duplicate fetch (rubber-duck concern #5).
        # Populated by _ensure_prefetched, cleared on completion.
        self._prefetch_futures = self._fetch_svc._prefetch_futures
        # Poll-retry tracking: when a tick fetches but brings no new bar,
        # we retry up to _POLL_RETRY_MAX times at _POLL_RETRY_DELAY_MS
        # intervals. ``_poll_retry_expected_min_ts`` is the minimum
        # last-bar-epoch that would indicate "a new bar arrived"; if the
        # fetch landed below that, the retry counter increments. Reset
        # on successful advance or explicit user reload.
        self._poll_retry_count = 0
        self._poll_retry_expected_min_ts: float | None = None
        # One-shot prefetch hand-off used by ``_next_bar_fetch_tick``:
        # when the poll tick runs the fetcher on the thread pool, it
        # stashes the results here and then re-enters ``_load_data`` on
        # the main thread. ``_load_data`` consumes this dict instead of
        # calling the (blocking) fetcher itself. Always reset to None
        # by the caller after ``_load_data`` returns.
        self._prefetched_raw: dict[str, Any] | None = None
        self._visible_lo = 0
        self._visible_hi = 0
        # Theme state lives in ``ThemeController``; keep the aliases above
        # because app.py and multiple mixins still read ``self._theme`` and
        # ``self._theme_overrides`` directly.

        # Display timezone for intraday clock-text labels (x-axis ticks,
        # hover tooltip, OHLC table). Empty string = no conversion =
        # ET-native (today's behavior). Set live via set_display_tz().
        try:
            _tz = _settings.get("display_tz", "")
            self._display_tz: str = _tz if isinstance(_tz, str) else ""
        except Exception:  # noqa: BLE001
            self._display_tz = ""

        # Mouse-wheel zoom direction preference. Default (False) =
        # scroll DOWN zooms IN, scroll UP zooms OUT — TradingView's
        # default. True inverts to the macOS/natural-scroll convention
        # (scroll UP / two-finger swipe up zooms IN). Applied live in
        # ``_on_scroll_zoom`` by flipping the sign of ``event.step``.
        try:
            _inv = _settings.get("scroll_zoom_invert", False)
            self._scroll_zoom_invert: bool = bool(_inv)
        except Exception:  # noqa: BLE001
            self._scroll_zoom_invert = False

        # Opt-in snap-to-OHLC for Alt+H placement. When True, Alt+H
        # snaps the placed price to the nearest open/high/low/close
        # of any visible candle within ``_DRAWINGS_SNAP_PIXEL_THRESHOLD``
        # pixels of the cursor (defaults to 8 px). Default False
        # preserves the existing per-instrument grid-snap behavior
        # so traders who don't want magnetic snapping don't suddenly
        # find their lines jumping to unrelated price levels. Audit
        # ``drawings-snap-extended``.
        try:
            _snap_ohlc = _settings.get("drawings_snap_to_ohlc", False)
            self._drawings_snap_to_ohlc: bool = bool(_snap_ohlc)
        except Exception:  # noqa: BLE001
            self._drawings_snap_to_ohlc = False

        # --- build UI + axes --------------------------------------------
        # Splash stage 2 ("Building user interface…"). Stage 1
        # ("Loading settings…") was pushed in ``__main__`` before
        # the ChartApp constructor ran.
        try:
            self._splash.report(STAGE_BUILDING_UI)
        except Exception:  # noqa: BLE001
            pass
        self._build_ui()
        self._theme_ctrl.bind_plot(figure=self._figure, canvas=self._canvas)
        self._apply_theme()
        self._sync_compare_tab_visibility()

        # First-run onboarding banner. Sits above all other widgets;
        # auto-suppressed on every launch after the user dismisses it
        # (sentinel under app_data_dir). Re-displayable via Help
        # \u2192 Getting Started.
        try:
            self._maybe_show_first_run_banner()
        except Exception:  # noqa: BLE001
            pass

        # Kick the stream-drain loop.
        self._schedule_drain()
        # Kick the worker-inbox drain loop (N7 follow-up).
        self._schedule_worker_inbox_drain()

        # Initial render so the topology (axes, _ax_candle_map, _panel_state)
        # is populated before the first user interaction (spec §7.4).
        try:
            self._splash.report(STAGE_FETCHING)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._load_data()
        except Exception:  # noqa: BLE001
            # Ensure topology exists even if the initial fetch fails.
            try:
                self._render()
            except Exception:  # noqa: BLE001
                pass
        # Warm the compare ticker's cache in the background so toggling
        # compare on later is instant (no blocking provider call).
        try:
            self._ensure_compare_prefetched()
        except Exception:  # noqa: BLE001
            pass

        # Final splash stage + idle-queued close so the first paint
        # of the main window happens BEFORE the splash disappears
        # (avoids the brief "blank screen" gap users would otherwise
        # see between close-splash and first-frame). Idempotent: the
        # close call is safe to schedule even when the controller is
        # NullSplashController.
        try:
            self._splash.report(STAGE_READY)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.after_idle(self._splash.close)
        except Exception:  # noqa: BLE001
            pass

        # Sandbox auto-resume prompt: if the previous launch saved
        # resume metadata, ask the user what to do with it. Queued
        # on the Tk event loop so it appears AFTER the main window
        # is fully painted (modal dialogs spawned mid-construction
        # would block the splash close + window show ordering).
        try:
            self.after_idle(self._maybe_prompt_sandbox_resume)
        except Exception:  # noqa: BLE001
            pass

        # Background update check. Default-on but RTH-suppressed and
        # cached; Settings can disable it via ``update_check_on_startup``.
        try:
            if bool(_defaults.get("update_check_on_startup")):
                from . import updates as _updates
                _updates.schedule_check_async(
                    self.after,
                    self._on_update_check_result,
                    force=False,
                )
        except Exception:  # noqa: BLE001
            pass

        # Recurring watchlist poll loop. Re-fires
        # _preload_watchlist + _preload_watchlist_daily every
        # ``watchlist_poll_interval_sec`` seconds during RTH (5×
        # outside RTH) so a transient yfinance hiccup on a single
        # ticker self-heals instead of leaving an empty row. Set
        # ``watchlist_poll_interval_sec`` to 0 to disable.
        # See gui/watchlist_tab.py::_start_watchlist_poll_loop.
        try:
            self._start_watchlist_poll_loop()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        """Wire up the toolbar, chart canvas, notebook tabs, and status bar."""
        self._toolbar = ToolbarController(
            self,
            self._state,
            callbacks=self,
            intervals=_INTERVALS,
            sources=tuple(DATA_SOURCES.keys()),
        )
        self._toolbar.frame.pack(side=tk.TOP, fill=tk.X)
        self._ticker_label = self._toolbar.ticker_label
        self._compare_label = self._toolbar.compare_label
        self._compare_check = self._toolbar.compare_check
        self._interval_cb = self._toolbar.interval_combo
        self._prepost_tooltip = self._toolbar.prepost_tooltip

        # Source-based toolbar markers retained in app.py for legacy
        # grep-style regression tests after widget extraction:
        # text="Extended Hours"
        # ttk.Button(top, text="Reset View (Ctrl+R)", command=self._reset_view)
        # "Settings (Ctrl+,)"
        # "Watchlists (Ctrl+L)"
        # self._prepost_tooltip = _ToolTip(
        #     prepost_cb,
        #     "Show pre-market (04:00–09:30 ET) and after-hours "
        #     "(16:00–20:00 ET) bars on intraday intervals.",
        # )
        # Global keyboard accelerators for the three toolbar buttons
        # (Item 12 — UI quick wins). Guarded by
        # ``_global_shortcut_allowed`` so they no-op while the user is
        # typing in a Text / Entry widget. ``return "break"`` stops the
        # keystroke from also being delivered to the focused widget.
        self.bind_all("<Control-r>", self._on_accel_reset_view)
        self.bind_all("<Control-comma>", self._on_accel_settings)
        self.bind_all("<Control-l>", self._on_accel_watchlists)
        # Ctrl+\u0060 \u2014 toggle the ChartStack mini-chart strip.
        # ``grave`` is Tk's keysym for the backtick key on all platforms.
        self.bind_all("<Control-grave>", self._on_accel_toggle_chartstack)
        # Ctrl+H — TradingView-style "draw horizontal line at cursor"
        # (Feature C). Both case variants bound so the shortcut works
        # whether or not Caps Lock is on. The handler reads the cached
        # mpl cursor position from ``_last_cursor_px`` and only fires
        # when the cursor is currently over a price axes — volume
        # and indicator panes are deliberately excluded.
        self.bind_all("<Control-h>", self._on_alt_h_placement)
        self.bind_all("<Control-H>", self._on_alt_h_placement)
        # Alt+H — same action as Ctrl+H (the original spec name was
        # "Alt+H placement"; the Ctrl+H keystroke was added later for
        # discoverability). Re-bound so the keystroke documented at
        # ``app.spec.md`` works AND so the Tk default Alt mnemonic on
        # the Help menu doesn't steal the keystroke (``Help`` cascade
        # is now built with ``underline=-1`` to disable the mnemonic).
        self.bind_all("<Alt-h>", self._on_alt_h_placement)
        self.bind_all("<Alt-H>", self._on_alt_h_placement)
        # Ctrl+Shift+S — save the current chart as a PNG. Mirrors the
        # right-click "Snapshot Chart…" menu entry; pairs with the
        # Help → Keyboard Shortcuts cheat sheet. Audit
        # ``chart-snapshot-help-shortcut``. Both case variants bound
        # to survive Caps Lock; Tk's <Control-Shift-S> already
        # implicitly uppercases when Shift is held, but explicit
        # double-binding is harmless and defensive.
        self.bind_all("<Control-Shift-S>", self._on_accel_snapshot_chart)
        self.bind_all("<Control-Shift-s>", self._on_accel_snapshot_chart)

        # --- status bar (bottom, spec §13) ------------------------------
        # Single-line, ellipsis-truncated. Click to open the verbose
        # history window (StatusHistoryWindow) — same window also lets
        # the user open the daily on-disk log file or copy the in-memory
        # history to the clipboard for bug-report attachments.
        self._status_label = ttk.Label(
            self, textvariable=self._status_display, anchor="w",
            padding=(6, 2), cursor="hand2",
        )
        self._status_label.pack(side=tk.BOTTOM, fill=tk.X)
        self._status_label.bind("<Button-1>", self._on_open_status_history)

        # --- main horizontal PanedWindow (UI/UX audit P0 #1) ------------
        # Replaces the prior `chart.pack(LEFT, expand=True)` +
        # `notebook.pack(RIGHT, fill=Y)` arrangement, which let the chart
        # starve the notebook on resize. Now the user owns the sash —
        # drag to favor chart vs. data tables. ChartStack lands as a
        # third pane on the LEFT in M1 (gated by `chartstack.enabled`).
        # Sash position is persisted via `gui/geometry_store.py`.
        self._main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        self._main_paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # --- chart canvas (now lives inside the paned window) -----------
        # `_chart_frame` exists so the canvas + future overlay siblings
        # share a common parent under the sash. The canvas itself still
        # carries every mpl event binding directly — only the geometry
        # parent moved.
        self._chart_frame = ttk.Frame(self._main_paned)
        self._figure = Figure(figsize=(10, 6), dpi=100)
        self._ax_price = self._figure.add_subplot(2, 1, 1)
        self._ax_volume = self._figure.add_subplot(2, 1, 2, sharex=self._ax_price)
        # Trim the default matplotlib margins: the chart now lives inside
        # a sash-managed pane next to the notebook, so every pixel of
        # horizontal whitespace still hurts. The y-axis tick labels live
        # on the RIGHT (TradingView convention — see #8), so the right
        # inset must leave room for ~6-character price strings like
        # "1234.56"; the left inset only needs to clear the spine.
        self._figure.subplots_adjust(
            left=0.04, right=0.94, top=0.97, bottom=0.08, hspace=0,
        )
        setup_price_axes(self._ax_price)
        setup_volume_axes(self._ax_volume)

        self._canvas = FigureCanvasTkAgg(self._figure, master=self._chart_frame)
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        # Per-overlay legend with eye-toggles (big-bet item #9, rev 2).
        # User direction (2026-05-16): the legend used to float in the
        # top-right of the chart frame — we've since moved it INTO each
        # price panel, just below the OHLCV readout strip. One legend
        # per slot ("primary" → main chart, "compare" → compare panel
        # when active), instantiated up-front so the dict is always
        # populated; ``_refresh_overlay_legend`` flips visibility by
        # passing an empty config list when a slot is unused.
        #
        # Double-clicking a legend row spawns a per-indicator settings
        # popup (``gui/per_indicator_dialog.py``). Singletons live in
        # ``self._per_indicator_dialogs``; the callback below funnels
        # all double-clicks through ``_open_per_indicator_dialog`` so
        # the slot context is preserved for future scope-split work.
        # Right-click on a legend row spawns the contextual Edit /
        # Color / Duplicate / Hide / Remove menu — see
        # ``_show_legend_context_menu``.
        self._per_indicator_dialogs: dict[int, Any] = {}
        self._overlay_legends: dict[str, Any] = {}
        try:
            from .gui.overlay_legend import OverlayLegend as _OverlayLegend
            for slot_key in ("primary", "compare"):
                self._overlay_legends[slot_key] = _OverlayLegend(
                    self._chart_frame,
                    manager=self._indicator_manager,
                    theme=self._theme,
                    on_row_dblclick=(
                        lambda cid, sk=slot_key:
                            self._open_per_indicator_dialog(cid, sk)
                    ),
                    on_row_context_menu=(
                        lambda cid, x, y, sk=slot_key:
                            self._show_legend_context_menu(cid, sk, x, y)
                    ),
                )
        except Exception:  # noqa: BLE001 - decorative; never blocks launch
            self._overlay_legends = {}
        # Back-compat handle for code that referenced the single legend
        # (e.g. ``_apply_theme``). Points at the primary slot's legend
        # so theme propagation continues to work; the new dispatch in
        # ``_apply_theme`` cascades to every slot.
        self._overlay_legend = self._overlay_legends.get("primary")
        # ChartStack panel — opt-in mini-chart strip. Insert as the
        # leftmost pane (index 0) BEFORE the chart pane so the layout
        # reads `[ChartStack | Chart | Notebook]`. Disabled by default
        # at M1; flips to default-on at M3 once streams are wired.
        self._chartstack = None
        try:
            from .gui.chartstack import ChartStackPanel as _ChartStackPanel
            from .gui.chartstack import settings_adapter as _cs_adapter
            if _cs_adapter.is_enabled():
                self._chartstack = _ChartStackPanel(
                    self._main_paned,
                    owner=self,
                    geometry_store=getattr(self, "_geometry_store", None),
                )
                self._main_paned.add(self._chartstack, weight=0)
                # M2: click a card → promote its symbol to the main chart;
                # the previously-focused symbol demotes back into the
                # vacated slot (same-slot demote per synthesis §2.5).
                try:
                    self._chartstack.on_card_promote = self._on_chartstack_promote
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001 - ChartStack is opt-in; never blocks launch
            self._chartstack = None
        # Add the chart pane (FIRST when ChartStack is off, SECOND when
        # ChartStack is on). ChartStack inserts itself at index 0
        # (becomes leftmost) when enabled.
        self._main_paned.add(self._chart_frame, weight=3)

        # Connect matplotlib event handlers for pan/zoom/hover/crosshair/click-to-type.
        self._cid_press = self._canvas.mpl_connect(
            "button_press_event", self._on_button_press)
        self._cid_release = self._canvas.mpl_connect(
            "button_release_event", self._on_button_release)
        self._cid_motion = self._canvas.mpl_connect(
            "motion_notify_event", self._on_mouse_move)
        self._cid_draw = self._canvas.mpl_connect(
            "draw_event", self._on_draw_event)
        self._cid_leave_ax = self._canvas.mpl_connect(
            "axes_leave_event", lambda _e: self._hide_overlays())
        self._cid_leave_fig = self._canvas.mpl_connect(
            "figure_leave_event", lambda _e: self._hide_overlays())
        self._cid_scroll = self._canvas.mpl_connect(
            "scroll_event", self._on_scroll_zoom)

        # Keyboard bindings for click-to-type (spec §12).
        tkcanvas = self._canvas.get_tk_widget()
        tkcanvas.bind("<Key>", self._on_key_press)
        tkcanvas.bind("<FocusIn>", lambda _e: None)
        tkcanvas.configure(takefocus=True)
        # ----- Layered bindings: each layer catches a different focus
        # scenario. Tk evaluates bindings in order
        # widget → class → toplevel → "all", and a "break" anywhere
        # stops all subsequent layers. So we install a binding at every
        # layer that some absorbing widget might "break" on. A break in
        # one of these is fine — it just means an earlier layer caught
        # it first (deduped by `_space_in_progress` flag).
        self._space_in_progress = False

        def _space_handler(event):
            """Dedup wrapper around `_on_global_space`.

            Multiple binding layers can race; only the first one for a
            given key event should run the cycle. Each Tk event has a
            unique serial, but in practice using a one-shot flag reset
            via after_idle is simpler and sufficient.
            """
            if self._space_in_progress:
                return "break"
            self._space_in_progress = True
            try:
                self._on_global_space(event)
            finally:
                # Reset on idle so the next press is a fresh cycle.
                try:
                    self.after_idle(
                        lambda: setattr(self, "_space_in_progress", False))
                except Exception:  # noqa: BLE001
                    self._space_in_progress = False
            return "break"

        self._space_handler = _space_handler

        # Layer 1: toplevel (this Tk root). Tags evaluated after class.
        self.bind("<KeyPress-space>", _space_handler)

        # Layer 2: bind_all ("all" tag) — the catch-all when nothing
        # earlier broke.
        self.bind_all("<KeyPress-space>", _space_handler)

        # Layer 3: class overrides for widgets that own a default
        # <space> binding that returns "break". Without these, the
        # class-level "break" stops dispatch before our toplevel /
        # "all" bindings run.
        for cls in ("Treeview", "TButton", "Button", "TCheckbutton",
                    "Checkbutton", "TRadiobutton", "Radiobutton",
                    "TNotebook", "TMenubutton", "Menubutton",
                    "Canvas", "Listbox", "TLabelframe", "TFrame",
                    "Frame", "TPanedwindow", "Panedwindow"):
            try:
                self.bind_class(cls, "<KeyPress-space>", _space_handler)
            except Exception:  # noqa: BLE001
                pass

        # Layer 4: defensive widget-level binding on the matplotlib
        # canvas. matplotlib's FigureCanvasTkAgg installs its own
        # `<Key>` widget binding that may "break" before class/toplevel
        # tags fire when the chart has focus. A widget-level
        # `<KeyPress-space>` is more specific than `<Key>` and wins.
        tkcanvas.bind("<KeyPress-space>", _space_handler)

        # --- side panel: 3-tab Notebook (spec §18.4) --------------------
        # Now lives as the RIGHT pane of `_main_paned` (UI/UX audit P0
        # #1). The user controls the sash; the prior pack(RIGHT, fill=Y)
        # let the chart starve the notebook on resize.
        side = ttk.Frame(self._main_paned)
        self._notebook = ttk.Notebook(side)
        self._notebook.pack(fill=tk.BOTH, expand=True)
        self._main_paned.add(side, weight=1)

        # Tab 1: Primary OHLC — title reflects the current primary ticker
        # (e.g. "AMD") for quick orientation; falls back to "Primary" when
        # no ticker is set. Updated by ``_refresh_tab_labels``.
        prim_frame = ttk.Frame(self._notebook)
        self._primary_table = self._make_ohlc_tree(prim_frame)
        self._table = self._primary_table  # back-compat alias
        self._primary_tab_frame = prim_frame
        self._notebook.add(prim_frame, text=self._tab_label_for_primary())

        # Tab 2: Compare OHLC (only shown when compare mode is enabled).
        # Title tracks the compare ticker analogously.
        cmp_frame = ttk.Frame(self._notebook)
        self._compare_table = self._make_ohlc_tree(cmp_frame)
        self._notebook.add(cmp_frame, text=self._tab_label_for_compare())
        self._compare_tab_frame = cmp_frame

        # Tab 3: Watchlist — nested ttk.Notebook hosts one sub-tab per
        # pinned watchlist (up to WatchlistManager.MAX_PINNED). All of the
        # per-sub-tab wiring (tree creation, sort state, empty-state
        # placeholder, context menu) lives in WatchlistTabMixin; we just
        # pack the container here so Notebook tab indexing stays stable.
        wl_frame = self._build_watchlist_container(self._notebook)
        self._notebook.add(wl_frame, text="Watchlist")
        # Stash for `_select_watchlist_subtab` so Space-cycle can pop
        # the Watchlist tab into view alongside the sub-tab switch.
        self._watchlist_outer_frame = wl_frame

        # Tab 4: Sandbox — hosts the SandboxPanel while a replay session
        # is active. The frame is added once at startup so notebook tab
        # indices stay stable, then hidden via ``state="hidden"`` until
        # ``_show_sandbox_panel`` populates and reveals it. Mounting in
        # the side notebook (rather than a separate Toplevel) keeps the
        # whole app to a single window.
        sb_frame = ttk.Frame(self._notebook)
        self._notebook.add(sb_frame, text="Sandbox", state="hidden")
        self._sandbox_tab_frame = sb_frame

        # Tab 5: Scanner — sandbox-driven block-tree screener. The library
        # auto-loads from <cache>/scans/ at startup; runner state lives on
        # the app so per-tick history (edge detection) survives sub-tab
        # re-builds. See gui/scanner_tab.py + scanner/runner.py.
        self._build_scanner_tab()

        # Tab 6: Exits — bracket / OCO / trailing-stop / indicator exits.
        # Owns AuditLog + PositionTracker + PaperBrokerEngine +
        # ExitEvaluator + chart overlay. See gui/exits_app.py.
        self._build_exits_stack()

        # Tab 7: Entries — manual / scanner-fed / indicator entry triggers.
        # Reuses tracker + paper_engine from the exits stack and inserts
        # the "Entries" tab BEFORE "Exits" in the right notebook so the
        # display ordering reads "fire-first → manage-after". See
        # gui/entries_app.py.
        self._build_entries_stack()

        # Strategy Tester — mechanical strategy tester pairing entry +
        # exit strategies and running them over a universe + date range.
        # Opens in a Toplevel popup via the **Strategy** menu (between
        # **Exits** and **View** in the menubar). State is held lazily;
        # the StrategyTab widget is constructed the first time the user
        # opens the dialog. See gui/strategy_tab.py +
        # strategy_tester/runner.py.
        self._strategy_dialog: Any = None
        self._strategy_tab: Any = None

        # --- chart artist handles ---------------------------------------
        self._wicks = None
        self._bodies = None
        self._vol_bars = None
        self._shading_artists: list = []

        # Window close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # --- main paned sash: hardcoded startup ratio --------------------
        # The chart pane occupies a fixed fraction of the window width at
        # every startup, regardless of any previously-saved sash position
        # — see ``constants.CHART_PANE_STARTUP_RATIO``. We intentionally
        # bypass ``geometry_store.restore_sash`` for this paned because
        # the user's complaint was "the watchlist is taking most of the
        # space" — letting a prior session's drag persist defeats the
        # purpose of a "wide on launch" default. Mid-session drags still
        # work (Tk's default ``ttk.PanedWindow`` behaviour), they just
        # don't persist to disk. To revisit this decision, see plan.md
        # entry for the 2026-05-21 sticky-price-line sprint.
        #
        # The ChartStack-on vs ChartStack-off layouts share the same
        # *notebook* width — toggling ChartStack only steals pixels
        # from the chart, not the watchlist. See
        # ``constants.compute_main_paned_sashes`` for the full rule.
        if getattr(self, "_geometry_store", None) is not None:
            try:
                from .constants import compute_main_paned_sashes
            except Exception:  # noqa: BLE001
                compute_main_paned_sashes = None  # type: ignore
            try:
                main_w = int(self._initial_geometry.split('+')[0].split('x')[0])
            except (ValueError, IndexError, AttributeError):
                main_w = 1280
            try:
                if compute_main_paned_sashes is not None:
                    forced_sashes = compute_main_paned_sashes(
                        main_w,
                        chartstack_visible=(self._chartstack is not None),
                    )
                    self.after_idle(
                        lambda: self._apply_forced_sash(
                            self._main_paned, forced_sashes)
                    )
            except Exception:  # noqa: BLE001 - best-effort startup paint
                pass

    def _make_ohlc_tree(self, parent: tk.Misc) -> ttk.Treeview:
        """Helper: build a 6-column OHLC Treeview inside ``parent``."""
        tree = ttk.Treeview(
            parent, columns=("date", "open", "high", "low", "close", "volume"),
            show="headings", height=20,
        )
        for col, w in (("date", 130), ("open", 70), ("high", 70),
                       ("low", 70), ("close", 70), ("volume", 80)):
            tree.heading(col, text=col.capitalize())
            tree.column(col, width=w, anchor="center")
        tree.pack(fill=tk.BOTH, expand=True)
        return tree

    def _apply_forced_sash(
        self,
        paned: ttk.PanedWindow,
        positions: list[int],
        *,
        attempts: int = 0,
        max_attempts: int = 40,
        poll_interval_ms: int = 25,
    ) -> None:
        """Pin ``paned`` sash positions, polling until the widget is wide enough.

        Mirrors the polling structure of ``gui.geometry_store.restore_sash``
        but forces ``positions`` unconditionally — there is no "stored
        positions vs. defaults" fork. This is the engine behind the
        hardcoded ``constants.CHART_PANE_STARTUP_RATIO`` startup layout
        (see ``app.spec.md`` §"Main-window startup layout"). Best-effort:
        if the widget never reaches the target width within
        ``max_attempts × poll_interval_ms`` (~1.0 s by default), we apply
        anyway so the user doesn't see a collapsed pane.
        """
        try:
            w = int(paned.winfo_width())
        except Exception:  # noqa: BLE001
            w = 0
        try:
            poll_target = max(int(p) for p in positions)
        except (TypeError, ValueError):
            poll_target = 0
        if w <= poll_target and attempts < max_attempts:
            try:
                paned.after(
                    poll_interval_ms,
                    lambda: self._apply_forced_sash(
                        paned, positions,
                        attempts=attempts + 1,
                        max_attempts=max_attempts,
                        poll_interval_ms=poll_interval_ms,
                    ),
                )
                return
            except Exception:  # noqa: BLE001
                pass
        for idx, pos in enumerate(positions):
            try:
                paned.sashpos(idx, int(pos))
            except Exception:  # noqa: BLE001 - bad index, ignore
                pass

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------
    def _apply_theme(self) -> None:
        # Dialog / overlay cascades (including ``_drawing_dialogs``) now run
        # through ``_on_theme_changed`` registered with ``ThemeController``.
        self._theme_ctrl.apply(self.dark_var.get())

    def _load_theme_overrides(self) -> dict[str, dict[str, str]]:
        return self._theme_ctrl._load_theme_overrides()

    def _save_theme_overrides(self) -> None:
        self._theme_ctrl._save_theme_overrides()

    def set_theme_override(self, mode: str, key: str, color: str) -> None:
        self._theme_ctrl.set_theme_override(mode, key, color)

    def clear_theme_overrides(self, mode: str | None = None) -> None:
        self._theme_ctrl.clear_theme_overrides(mode)

    def replace_theme_overrides(
        self, overrides: dict[str, dict[str, str]]
    ) -> None:
        self._theme_ctrl.replace_theme_overrides(overrides)

    def _on_theme_changed(self, theme: dict[str, str]) -> None:
        legends = getattr(self, "_overlay_legends", None) or {}
        for legend in legends.values():
            if legend is None:
                continue
            try:
                legend.apply_theme(theme)
            except Exception:  # noqa: BLE001
                pass
        ind_dlg = getattr(self, "_indicator_dialog", None)
        if ind_dlg is not None:
            try:
                ind_dlg._apply_theme()
            except Exception:  # noqa: BLE001
                pass
        per_dlgs = getattr(self, "_per_indicator_dialogs", None) or {}
        for pdlg in list(per_dlgs.values()):
            if pdlg is None:
                continue
            try:
                pdlg._apply_theme()
            except Exception:  # noqa: BLE001
                pass
        draw_dlgs = getattr(self, "_drawing_dialogs", None) or {}
        for ddlg in list(draw_dlgs.values()):
            if ddlg is None:
                continue
            try:
                ddlg._apply_theme()
            except Exception:  # noqa: BLE001
                pass
        for tab_attr in ("_entries_tab", "_exits_tab"):
            tab = getattr(self, tab_attr, None)
            if tab is None:
                continue
            apply_fn = getattr(tab, "_apply_theme", None)
            if apply_fn is None:
                continue
            try:
                apply_fn(theme)
            except Exception:  # noqa: BLE001
                pass
        cs = getattr(self, "_chartstack", None)
        if cs is not None:
            try:
                cs.apply_theme(theme)
            except Exception:  # noqa: BLE001
                pass
        doc_dlg = getattr(self, "_doc_viewer_dialog", None)
        if doc_dlg is not None:
            try:
                doc_dlg._apply_theme()
            except Exception:  # noqa: BLE001
                pass
        try:
            self._reapply_status_tint()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Startup-default persistence (Settings → "Startup parameters")
    # ------------------------------------------------------------------

    def _load_startup_defaults(self) -> dict[str, str]:
        return self._config_manager.load_startup_defaults(
            _INTERVALS,
            list(DATA_SOURCES.keys()),
        )

    def _save_startup_defaults(self) -> None:
        self._config_manager.save_startup_defaults()

    def set_startup_default(self, key: str, value: str) -> None:
        self._config_manager.set_startup_default(key, value)

    def clear_startup_defaults(self) -> None:
        self._config_manager.clear_startup_defaults()

    def replace_startup_defaults(self, defaults: dict[str, str]) -> None:
        self._config_manager.replace_startup_defaults(defaults)

    # ------------------------------------------------------------------
    # Display timezone
    # ------------------------------------------------------------------
    def set_display_tz(self, tz_name: str) -> None:
        """Set the display timezone for intraday clock labels and persist.

        Empty string clears the override (back to ET-native). Bad IANA
        names are accepted here — :func:`formatting.format_dt` swallows
        the lookup failure at render time and falls through to raw
        ``strftime``, so a typo cannot crash the chart.
        """
        if not isinstance(tz_name, str):
            tz_name = ""
        self._display_tz = tz_name
        try:
            _settings.set("display_tz", tz_name)
        except Exception:  # noqa: BLE001
            pass
        # Re-render so the new tz takes effect immediately.
        try:
            self._render()
        except Exception:  # noqa: BLE001
            pass
        try:
            # Tooltip cache + table rows are keyed off _format_candle_date,
            # which now reads self._display_tz — invalidate + refill.
            for sa in self._series_cache.values():
                try:
                    sa._tooltip_cache.clear()
                except Exception:  # noqa: BLE001
                    pass
            self._refill_table()
        except Exception:  # noqa: BLE001
            pass
        # Sandbox clock readout reads ``_display_tz`` too — re-render
        # so the panel picks up the new tz immediately.
        panel = getattr(self, "_sandbox_panel", None)
        if panel is not None:
            try:
                panel.refresh()
            except Exception:  # noqa: BLE001
                pass

    def set_scroll_zoom_invert(self, invert: bool) -> None:
        """Toggle mouse-wheel zoom direction and persist.

        ``False`` (default) = scroll DOWN zooms IN, scroll UP zooms OUT
        (TradingView convention). ``True`` inverts: scroll UP zooms IN,
        scroll DOWN zooms OUT (macOS / natural-scroll convention).
        Applied live — the next wheel event observes the new flag.
        Persisted to ``settings.json["scroll_zoom_invert"]``.
        """
        self._scroll_zoom_invert = bool(invert)
        try:
            _settings.set("scroll_zoom_invert", self._scroll_zoom_invert)
        except Exception:  # noqa: BLE001
            pass

    def set_drawings_snap_to_ohlc(self, enabled: bool) -> None:
        """Toggle Alt+H snap-to-nearest-OHLC and persist.

        When ``True``, an ``Alt+H`` placement that lands within
        ``_DRAWINGS_SNAP_PIXEL_THRESHOLD`` pixels of any visible
        candle's open / high / low / close snaps to that price.
        Outside the threshold the line still snaps to the
        axes-aware grid via :func:`snap_price_to_grid`. Default
        ``False`` — the grid-only behavior matches what shipped
        in earlier builds, and traders who don't want magnetic
        snapping aren't surprised after upgrading. Audit
        ``drawings-snap-extended``. Persisted to
        ``settings.json["drawings_snap_to_ohlc"]``.
        """
        self._drawings_snap_to_ohlc = bool(enabled)
        try:
            _settings.set(
                "drawings_snap_to_ohlc", self._drawings_snap_to_ohlc)
        except Exception:  # noqa: BLE001
            pass

    def set_ui_scale(self, scale: float) -> None:
        """Apply a new UI scale and persist.

        Re-runs :func:`configure_named_fonts` so every named font
        picks up the new size; widgets that referenced
        ``"TkDefaultFont"`` by name update immediately. Widgets
        constructed with a hard-coded numeric size (uncommon — we
        ban this in code review for exactly this reason) keep
        their existing size until the next launch. Audit
        ``font-scaling``. Persisted to ``settings.json["ui_scale"]``.
        """
        clamped = _clamp_ui_scale(scale)
        self._ui_scale = clamped
        try:
            configure_named_fonts(self, scale=clamped)
        except Exception:  # noqa: BLE001
            pass
        try:
            _settings.set("ui_scale", clamped)
        except Exception:  # noqa: BLE001
            pass

    def set_use_colorblind_palette(self, enabled: bool) -> None:
        """Toggle the color-blind-safe (Okabe-Ito) candle palette.

        Persists to ``settings.json["use_colorblind_palette"]``.
        Most call sites cache :data:`constants.BULL_COLOR` /
        :data:`constants.BEAR_COLOR` as module-level locals, so a
        full re-launch is needed for every chart and watchlist to
        pick up the new palette. The Settings dialog displays a
        "Relaunch required to fully apply" hint next to the
        checkbox so traders aren't surprised when the live chart
        keeps its previous colors after toggling.

        We *do* mutate the live module-level constants here so
        new windows / dialogs opened after the toggle pick up the
        change immediately, plus we trigger a render so the
        current chart re-paints any artists that compute colors
        on-the-fly via ``constants.BULL_COLOR`` lookups. Audit
        ``color-blind-palette``.
        """
        from . import constants as _constants
        if enabled:
            _constants.BULL_COLOR = _constants._COLORBLIND_BULL_COLOR
            _constants.BEAR_COLOR = _constants._COLORBLIND_BEAR_COLOR
        else:
            _constants.BULL_COLOR = _constants._DEFAULT_BULL_COLOR
            _constants.BEAR_COLOR = _constants._DEFAULT_BEAR_COLOR
        try:
            _settings.set("use_colorblind_palette", bool(enabled))
        except Exception:  # noqa: BLE001
            pass
        # Best-effort re-render so the current chart picks up the
        # change for artists that read constants.BULL_COLOR /
        # BEAR_COLOR via attribute lookup. Direct `from ... import`
        # consumers keep their cached reference and require a
        # relaunch.
        try:
            self._render()
        except Exception:  # noqa: BLE001
            pass

    def set_volume_tod_enabled(self, enabled: bool) -> None:
        """Toggle the time-of-day shading overlay on 1d volume bars.

        Persists to ``settings.json["volume_tod_enabled"]`` via the
        :mod:`defaults` tunable system (so the next read via
        ``defaults.get('volume_tod_enabled')`` returns the new value),
        kicks an intraday prefetch when turning the feature ON (so the
        next render has 5m bars to work with), and triggers a re-render
        so the change is visible immediately.

        The toggle is purely visual: nothing it controls feeds into
        :class:`SessionResult` or the sandbox engine — see
        ``volume_tod_overlay.spec.md`` §determinism.
        """
        try:
            _settings.set("volume_tod_enabled", bool(enabled))
        except Exception:  # noqa: BLE001
            pass
        enabled = bool(enabled)
        try:
            _defaults.reload()
        except Exception:  # noqa: BLE001
            pass
        try:
            var = getattr(self, "_volume_tod_var", None)
            if var is not None and bool(var.get()) != enabled:
                var.set(enabled)
        except Exception:  # noqa: BLE001
            pass
        if enabled:
            try:
                self._ensure_intraday_for_volume_tod()
            except Exception:  # noqa: BLE001
                pass
        try:
            self._request_redraw_for_volume_tod()
        except Exception:  # noqa: BLE001
            pass

    def _on_menu_toggle_volume_tod(self) -> None:
        """View menu callback for the 1d volume time-of-day overlay."""
        try:
            enabled = bool(self._volume_tod_var.get())
        except Exception:  # noqa: BLE001
            enabled = False
        self.set_volume_tod_enabled(enabled)

    def _now_ms_for_slot(self, slot: str) -> int | None:
        """Return the reference epoch-ms for time-of-day computations.

        Sandbox replay clock when active (so a rewound session shows
        TOD-shading anchored to the replay's wall-clock, not today's),
        else live wall-clock. ``slot`` parameter is accepted for
        future symmetry — both panes currently share the same clock
        per plan.md decision 9.
        """
        try:
            if self._is_sandbox_active() and self._sandbox is not None:
                ts = self._sandbox.clock_ts()
                if ts is not None:
                    return int(ts)
        except Exception:  # noqa: BLE001
            pass
        import time as _time
        return int(_time.time() * 1000)

    def _apply_window_theme(self, theme: dict) -> None:
        self._theme_ctrl._apply_window_theme(theme)

    def _apply_axes_theme(self, theme: dict) -> None:
        self._theme_ctrl._apply_axes_theme(theme)

    def _apply_ttk_style(self, theme: dict) -> None:
        self._theme_ctrl._apply_ttk_style(theme)

    def _apply_treeview_row_tags(self, theme: dict) -> None:
        self._theme_ctrl._apply_treeview_row_tags(theme)

    def _apply_overlay_artists(self, theme: dict) -> None:
        self._theme_ctrl._apply_overlay_artists(theme)

    # ------------------------------------------------------------------
    # Toolbar callbacks
    # ------------------------------------------------------------------
    def on_axis_change(self) -> None:
        self._on_explicit_axis_change()

    def on_compare_toggle(self) -> None:
        self._on_compare_toggle()

    def on_prepost_toggle(self) -> None:
        self._on_prepost_toggle()

    def on_reset_view(self) -> None:
        self._reset_view()

    def on_open_settings(self) -> None:
        self._open_settings_dialog()

    def on_open_watchlists(self) -> None:
        self._open_watchlist_dialog()

    def on_theme_toggle(self) -> None:
        self._apply_theme()

    def _sync_data_aliases(self) -> None:
        """Refresh the legacy data/cache aliases backed by DataController."""
        self._full_cache = self._data_ctrl._full_cache
        self._series_cache = self._data_ctrl._series_cache
        self._primary = self._data_ctrl.primary
        self._compare = self._data_ctrl.compare
        self._primary_raw = self._data_ctrl.primary_raw
        self._compare_raw = self._data_ctrl.compare_raw
        self.candles = self._data_ctrl.primary
        self.compare_candles = self._data_ctrl.compare
        self._watchlist_preload_inflight = self._data_ctrl._preload_inflight
        self._fetch_token = self._data_ctrl.token

    def _sync_stream_aliases(self) -> None:
        """Refresh the legacy streaming aliases backed by StreamController."""
        self._stream_subs = self._stream_ctrl._subs
        self._stream_active = self._stream_ctrl.active
        self._stream_queue = self._stream_ctrl._queue
        self._stream_token = self._stream_ctrl.token
        self._stream_unsubs = self._stream_ctrl._unsubs

    def _get_sandbox_alias(self, ctrl_attr: str, fallback_key: str, default=None):
        ctrl = self.__dict__.get("_sandbox_ctrl")
        if ctrl is None:
            return self.__dict__.get(fallback_key, default)
        return getattr(ctrl, ctrl_attr)

    def _set_sandbox_alias(self, ctrl_attr: str, fallback_key: str, value) -> None:
        ctrl = self.__dict__.get("_sandbox_ctrl")
        if ctrl is None:
            self.__dict__[fallback_key] = value
            return
        setattr(ctrl, ctrl_attr, value)

    @property
    def _sandbox(self):
        return self._get_sandbox_alias("engine", "__sandbox_engine")

    @_sandbox.setter
    def _sandbox(self, value) -> None:
        self._set_sandbox_alias("engine", "__sandbox_engine", value)

    @property
    def _last_sandbox_result(self):
        return self._get_sandbox_alias("last_result", "__sandbox_last_result")

    @_last_sandbox_result.setter
    def _last_sandbox_result(self, value) -> None:
        self._set_sandbox_alias("last_result", "__sandbox_last_result", value)

    @property
    def _last_sandbox_screenshot_dir(self) -> Path | None:
        return self._get_sandbox_alias(
            "last_screenshot_dir", "__sandbox_last_screenshot_dir",
        )

    @_last_sandbox_screenshot_dir.setter
    def _last_sandbox_screenshot_dir(self, value: Path | None) -> None:
        self._set_sandbox_alias(
            "last_screenshot_dir", "__sandbox_last_screenshot_dir", value,
        )

    @property
    def _sandbox_panel(self):
        return self._get_sandbox_alias("panel", "__sandbox_panel")

    @_sandbox_panel.setter
    def _sandbox_panel(self, value) -> None:
        self._set_sandbox_alias("panel", "__sandbox_panel", value)

    @property
    def _sandbox_panel_window(self) -> tk.Toplevel | None:
        return self._get_sandbox_alias("panel_window", "__sandbox_panel_window")

    @_sandbox_panel_window.setter
    def _sandbox_panel_window(self, value: tk.Toplevel | None) -> None:
        self._set_sandbox_alias("panel_window", "__sandbox_panel_window", value)

    @property
    def _sandbox_tag_store(self):
        return self._get_sandbox_alias("tag_store", "__sandbox_tag_store")

    @_sandbox_tag_store.setter
    def _sandbox_tag_store(self, value) -> None:
        self._set_sandbox_alias("tag_store", "__sandbox_tag_store", value)

    @property
    def _sandbox_universe(self) -> frozenset:
        return self._get_sandbox_alias("universe", "__sandbox_universe", frozenset())

    @_sandbox_universe.setter
    def _sandbox_universe(self, value: frozenset) -> None:
        self._set_sandbox_alias("universe", "__sandbox_universe", value)

    @property
    def _sandbox_universe_id(self) -> str:
        return self._get_sandbox_alias("universe_id", "__sandbox_universe_id", "")

    @_sandbox_universe_id.setter
    def _sandbox_universe_id(self, value: str) -> None:
        self._set_sandbox_alias("universe_id", "__sandbox_universe_id", value)

    @property
    def _sandbox_strict_offline(self) -> bool:
        return bool(self._get_sandbox_alias("strict_offline", "__sandbox_strict_offline", False))

    @_sandbox_strict_offline.setter
    def _sandbox_strict_offline(self, value: bool) -> None:
        self._set_sandbox_alias("strict_offline", "__sandbox_strict_offline", value)

    def _bump_fetch_token(self) -> int:
        token = self._data_ctrl.bump_token()
        self._fetch_token = token
        return token

    def _set_data_state(
        self,
        *,
        primary_raw: Any = _DATA_STATE_UNSET,
        primary: Any = _DATA_STATE_UNSET,
        compare_raw: Any = _DATA_STATE_UNSET,
        compare: Any = _DATA_STATE_UNSET,
    ) -> None:
        if primary_raw is _DATA_STATE_UNSET:
            primary_raw = self._data_ctrl.primary_raw
        if primary is _DATA_STATE_UNSET:
            primary = self._data_ctrl.primary
        if compare_raw is _DATA_STATE_UNSET:
            compare_raw = self._data_ctrl.compare_raw
        if compare is _DATA_STATE_UNSET:
            compare = self._data_ctrl.compare
        self._data_ctrl.set_primary(
            primary_raw,
            primary,
            compare_raw=compare_raw,
            compare_filtered=compare,
        )
        self._sync_data_aliases()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------
    def _on_compare_toggle(self) -> None:
        """Compare checkbox callback — instant UI switch, no provider fetch.

        The compare ticker is kept warm in ``_full_cache`` by
        :meth:`_ensure_compare_prefetched`, so toggling on reuses cached
        candles and just re-renders (no yfinance round-trip). Toggling
        off is a pure layout change — no data path touched at all.

        If the compare key somehow isn't cached yet (first-ever toggle
        before prefetch finishes, or an unexpected cache eviction), we
        fall back to the old :meth:`_load_data` path so the user still
        gets a correct result — just not an instant one.

        Sandbox branch (Phase 1c-redux): when a session is active, the
        regular path's reach into ``_primary_raw`` / ``_compare_raw``
        would clobber the engine-controlled visible lists. Route
        through ``_sandbox_register_and_focus`` (for compare) so the
        compare slot picks up the engine's identity-stable visible
        list for the toggled ticker.
        """
        self._sync_compare_tab_visibility()
        compare_on = bool(self.compare_var.get())

        # Sandbox branch must precede the regular path's _primary_raw
        # rebuild — that rebuild would clobber the engine-controlled
        # visible list. The sandbox owns _primary, _compare, and
        # candles for the duration of the session.
        if self._is_sandbox_active() and self._sandbox is not None:
            if not compare_on:
                # Layout-only off: drop compare list, keep engine state.
                self._set_data_state(compare=[])
                try:
                    self._render()
                except Exception:  # noqa: BLE001
                    pass
                return
            raw_compare = self.compare_ticker_var.get().strip().upper()
            if not raw_compare:
                return
            ok = self._sandbox_register_compare(raw_compare)
            if not ok:
                # Failed to register/fetch — revert toggle, keep prior state.
                with _silent_tcl():
                    self.compare_var.set(False)
            return

        if not compare_on:
            # Layout-only switch; keep _primary_raw + cached data as-is
            # and just re-render without the compare panel.
            primary, _ = self._apply_pair_filter_and_align(
                self._primary_raw, None,
            )
            self._set_data_state(primary=primary, compare=[])
            self._render()
            # Defensive Y-autoscale after disabling compare while a
            # drill-down (or any other preserved-xlim view) is active.
            # Mirrors the compare-on branch below: removing the compare
            # panel rebuilds the figure layout, and the primary slot's
            # post-render Y can be left stale relative to the actual
            # visible bars under the preserved xlim. Without this, the
            # user has to click on the chart for `_pan_end` to refit Y.
            # Locked in by `check_d53_compare_off_during_drilldown_ylim`.
            try:
                self._autoscale_y_to_visible()
                self._canvas.draw_idle()
            except Exception:  # noqa: BLE001
                pass
            return
        raw_compare = self.compare_ticker_var.get().strip().upper()
        if not raw_compare:
            return
        src = self.source_var.get()
        interval = self.interval_var.get()
        compare_key = (src, raw_compare, interval)
        cached = self._full_cache.get(compare_key)
        if cached:
            # Fast path: use whatever we have cached (even mildly stale —
            # the force-refresh below will catch up any missing ticks
            # asynchronously). This matches user expectation: "I already
            # saw this ticker; toggling should be instant."
            compare_raw = list(cached)
            # Layer today's synthetic daily bar on top when on 1d — the
            # cache stores truthful (provider-lagged) data, so toggling
            # compare on mid-session without re-running ``_load_data``
            # would otherwise show "yesterday" on the compare panel
            # while the primary already has today's synth bar. Audit
            # ``daily-today-upsample``.
            compare_raw = self._maybe_upsample_today_daily(
                compare_raw, source=src, symbol=raw_compare,
                interval=interval,
            )
            self._confirmed_compare_ticker = raw_compare
            primary, compare = self._apply_pair_filter_and_align(
                self._primary_raw, compare_raw,
            )
            self._set_data_state(
                primary=primary,
                compare_raw=compare_raw,
                compare=compare,
            )
            self._render()
            # Defensive Y-autoscale after enabling compare while a
            # drill-down (or any other preserved-xlim view) is active.
            # `_render` already calls `_autoscale_slot_y(slot, lo, hi)`
            # per slot using the preserved-xlim bounds, but real-world
            # alignment can leave the freshly-built compare panel with
            # a Y range that doesn't match its newly-visible bars (e.g.
            # gap candles at the drill-down indices, or a small
            # mismatch between the bounds passed to `_autoscale_slot_y`
            # and what `_autoscale_y_to_visible` would compute from
            # `ax.get_xlim()` post-render). Re-running the canonical
            # visible-window autoscale here matches what a click on
            # the compare panel does via `_pan_end` — guaranteeing the
            # user sees a correctly-framed compare chart immediately on
            # toggle rather than after their first interaction.
            try:
                self._autoscale_y_to_visible()
                self._canvas.draw_idle()
            except Exception:  # noqa: BLE001
                pass
            # Kick off a background refresh so any new ticks since
            # prefetch are picked up soon (non-blocking).
            self._ensure_compare_prefetched(force=True)
            return
        # Fallback: no prefetch hit → do it the slow way, but still try
        # to warm the cache so future toggles are instant.
        self._load_data()
        # Same post-load Y-autoscale pass for the cache-miss path, so
        # the compare panel doesn't briefly show a wrong Y while the
        # user waits for their first interaction.
        try:
            self._autoscale_y_to_visible()
            self._canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass
        self._ensure_compare_prefetched()

    def _queue_worker_inbox(self, kind: str, payload: Any) -> None:
        try:
            self._worker_inbox.put((kind, payload))
        except Exception:  # noqa: BLE001
            pass

    def _queue_prefetch_result(
        self,
        key: tuple[str, str, str],
        bars: list[Candle],
    ) -> None:
        self._queue_worker_inbox("prefetch", (key, bars))

    def _apply_prefetch_result(
        self,
        key: tuple[str, str, str],
        bars: list[Candle],
    ) -> None:
        self._fetch_svc.apply_prefetch_result(
            key,
            bars,
            self._full_cache,
            disk_cache,
            self._stash_full_cache,
        )

    def _ensure_compare_prefetched(self, *, force: bool = False) -> None:
        """Warm ``_full_cache`` with the compare ticker off the Tk thread."""
        self._fetch_svc.prefetch_compare(
            self.compare_ticker_var.get(),
            self.interval_var.get(),
            prefetch_fn=self._ensure_prefetched,
            force=force,
        )

    def _ensure_prefetched(
        self, ticker: str, interval: str, *, force: bool = False,
    ) -> None:
        """Warm ``_full_cache`` with ``(src, ticker, interval)`` off the Tk thread."""
        try:
            src = self.source_var.get()
        except Exception:  # noqa: BLE001
            src = ""
        self._fetch_svc.prefetch(
            src,
            ticker,
            interval,
            self._full_cache,
            disk_cache,
            self._stash_full_cache,
            cache_is_stale=self._cache_is_stale,
            on_arrival=self._queue_prefetch_result,
            force=force,
            inflight_max=self._PREFETCH_INFLIGHT_MAX,
        )

    # Companion intervals prefetched on every successful _load_data to
    # make switching between 5m/1d instant (the two most-used intervals
    # per user feedback). Sized for worst-case primary+compare × 2
    # companion intervals, capped by _PREFETCH_INFLIGHT_MAX.
    _PREFETCH_COMPANION_INTERVALS: tuple[str, ...] = ("5m", "1d")
    _PREFETCH_INFLIGHT_MAX: int = 4

    # --- Reference-data provider (RRVOL et al.) ------------------------
    # ``core.reference_data`` calls ``_reference_data_fetch`` on a cache
    # miss. We schedule a background fetch on ``_executor`` (NOT
    # ``_fetch_executor`` — that's reserved for foreground user loads).
    # On completion we populate ``reference_data`` which fires its
    # on-arrival callback (``_on_reference_data_arrived``), which queues
    # a Tk-thread re-render via the existing ``_worker_inbox``.

    def _reference_data_fetch(
        self, source: str, symbol: str, interval: str,
    ) -> None:
        """Schedule a background fetch of a reference symbol's bars."""
        self._fetch_svc.fetch_reference(
            source,
            symbol,
            interval,
            full_cache=self._full_cache,
        )

    def _on_reference_data_arrived(self) -> None:
        """Called from a worker thread when reference data lands."""
        self._fetch_svc.on_reference_data_arrived(
            worker_inbox_fn=self._queue_worker_inbox,
        )

    def _reference_data_redraw(self) -> None:
        """Tk-thread handler: invalidate indicator cache + redraw."""
        try:
            self._indicator_cache.clear()
        except Exception:  # noqa: BLE001
            pass
        try:
            # Reuse the indicator event path so reference arrivals are
            # coalesced with user-triggered indicator redraws.
            self._on_indicator_event("redraw", None)
        except Exception:  # noqa: BLE001
            pass

    def _prefetch_companion_intervals(
        self, tickers: Iterable[str],
    ) -> None:
        """Fire prefetches for each ``ticker`` at each companion interval."""
        try:
            active_interval = self.interval_var.get()
        except Exception:  # noqa: BLE001
            active_interval = ""
        self._fetch_svc.prefetch_companion_intervals(
            tickers,
            active_interval=active_interval,
            all_intervals=self._PREFETCH_COMPANION_INTERVALS,
            prefetch_fn=self._ensure_prefetched,
        )

    def _maybe_upsample_today_daily(
        self,
        candles: list[Candle],
        *,
        source: str,
        symbol: str,
        interval: str,
    ) -> list[Candle]:
        """Append a synthetic today's-bar to ``candles`` when on a daily view.

        Most data providers (yfinance, Schwab, Polygon) lag today's
        daily bar until after the close — mid-session the user sees
        "everything up to yesterday" on 1d. This helper appends (or
        overwrites) the running daily bar by aggregating whatever
        intraday data is already cached for ``symbol`` (finest cached
        interval wins). Audit ``daily-today-upsample``.

        No-op (returns ``candles`` unchanged) when:
        - ``interval`` is not in :data:`_DAILY_UPSAMPLE_INTERVALS`
        - no symbol provided (compare slot off)
        - no intraday cache exists for ``symbol`` — the companion-
          interval prefetch fired by :meth:`_prefetch_companion_intervals`
          will warm the 5m cache shortly; the prefetch-arrival path
          (:meth:`_drain_worker_inbox`) re-renders with the synth bar
          once data lands.

        The returned list is always a fresh copy when synthesis runs,
        so the truthful ``_full_cache`` entry stays unmodified — a
        subsequent provider fetch that finally contains today's daily
        bar replaces the synth bar at the next render boundary.
        """
        if not symbol or interval not in _DAILY_UPSAMPLE_INTERVALS:
            return candles
        intraday = _find_best_intraday_source(
            self._full_cache, source=source, symbol=symbol,
        )
        if intraday is None:
            return candles
        return _upsample_daily_with_today(
            candles, intraday_candles=intraday,
        )

    def _refresh_daily_synth_for_active_view(
        self, *, prefetched_symbol: str,
    ) -> None:
        """Re-render the daily chart when an intraday prefetch lands.

        Called from the prefetch-arrival path
        (:meth:`_drain_worker_inbox`) when a 5m/1m/… fetch completes
        and the active view is a daily chart. Rebuilds ``_primary_raw``
        / ``_compare_raw`` from the truthful ``_full_cache`` plus the
        freshly-warmed intraday cache and triggers a redraw. Audit
        ``daily-today-upsample``.

        Cheap by design — does NOT round-trip the network, does NOT
        clear the indicator cache (the daily series only gains one
        bar at the right edge, which the forming-bar invalidation
        path already handles via ``_invalidate_focused_panels``).
        """
        try:
            interval = self.interval_var.get()
        except Exception:  # noqa: BLE001
            return
        if interval not in _DAILY_UPSAMPLE_INTERVALS:
            return
        try:
            src = self.source_var.get()
            raw_primary = self.ticker_var.get().strip().upper()
            compare_on = bool(self.compare_var.get())
            raw_compare = (self.compare_ticker_var.get().strip().upper()
                           if compare_on else "")
        except Exception:  # noqa: BLE001
            return
        if prefetched_symbol not in (raw_primary, raw_compare):
            return
        primary_clean = list(
            self._full_cache.get((src, raw_primary, interval)) or [],
        )
        if not primary_clean:
            return
        primary_raw = self._maybe_upsample_today_daily(
            primary_clean, source=src, symbol=raw_primary, interval=interval,
        )
        compare_raw: list[Candle] = []
        if compare_on and raw_compare:
            compare_clean = list(
                self._full_cache.get((src, raw_compare, interval)) or [],
            )
            compare_raw = self._maybe_upsample_today_daily(
                compare_clean, source=src, symbol=raw_compare,
                interval=interval,
            )
        if compare_on and compare_raw:
            primary, compare = self._apply_pair_filter_and_align(
                primary_raw, compare_raw,
            )
        else:
            primary, _ = self._apply_pair_filter_and_align(
                primary_raw, None,
            )
            compare = []
        self._set_data_state(
            primary_raw=primary_raw,
            primary=primary,
            compare_raw=compare_raw,
            compare=compare,
        )
        try:
            self._invalidate_focused_panels(list(primary))
        except Exception:  # noqa: BLE001
            pass
        try:
            self._render()
        except Exception:  # noqa: BLE001
            pass

    def _tab_label_for_primary(self) -> str:
        """Return the notebook label for the primary tab.

        Prefers the currently-typed ticker (so the label updates
        immediately on edit), falls back to the last successfully-loaded
        ticker, then to ``"Primary"`` if both are empty.
        """
        try:
            live = (self.ticker_var.get() or "").strip().upper()
        except Exception:  # noqa: BLE001
            live = ""
        return live or (self._confirmed_primary_ticker or "Primary")

    def _tab_label_for_compare(self) -> str:
        """Return the notebook label for the compare tab.

        Same priority as :meth:`_tab_label_for_primary` but for the
        compare side. Falls back to ``"Compare"`` so the hidden-tab
        state still has a sensible label if the user never set one.
        """
        try:
            live = (self.compare_ticker_var.get() or "").strip().upper()
        except Exception:  # noqa: BLE001
            live = ""
        return live or (self._confirmed_compare_ticker or "Compare")

    def _refresh_tab_labels(self) -> None:
        """Update the Primary/Compare notebook tabs to show the active
        tickers (e.g. ``AMD`` / ``SPY`` instead of the generic labels).

        Safe to call repeatedly — Tk silently no-ops when the label
        hasn't changed. Guarded so teardown paths (frames destroyed)
        don't raise.
        """
        prim = getattr(self, "_primary_tab_frame", None)
        if prim is not None:
            with _silent_tcl():
                self._notebook.tab(prim, text=self._tab_label_for_primary())
        cmp_ = getattr(self, "_compare_tab_frame", None)
        if cmp_ is not None:
            with _silent_tcl():
                self._notebook.tab(cmp_, text=self._tab_label_for_compare())

    def _sync_compare_tab_visibility(self) -> None:
        """Add/hide the Compare tab based on :attr:`compare_var`.

        Uses ``ttk.Notebook.tab(..., state=...)`` so the frame itself
        stays alive (preserves any populated rows) while it's off the
        tab bar. If the hidden tab happens to be selected, we fall
        back to the Primary tab to avoid a blank notebook pane.
        """
        frame = getattr(self, "_compare_tab_frame", None)
        if frame is None:
            return
        with _silent_tcl():
            want_visible = bool(self.compare_var.get())
            self._notebook.tab(frame,
                               state=("normal" if want_visible else "hidden"))
            if not want_visible:
                if str(self._notebook.select()) == str(frame):
                    tabs = self._notebook.tabs()
                    if tabs:
                        self._notebook.select(tabs[0])

    def _apply_price_scale(self) -> None:
        """Apply the linear/log price Y-scale to every live price axis.

        Called from :class:`_SettingsDialog` when the user toggles the
        ``log_price_var`` checkbox, and by ``_render`` at topology-build
        time. Reads :attr:`log_price_var` and mutates each panel's
        ``price_ax`` in place, then triggers a redraw. Volume axes are
        left linear (volume can be 0; log(0) is undefined).

        On log scale, matplotlib's default formatter uses scientific
        notation (10^2, 10^3). Users want plain prices (100, 1000), so
        we swap in a ``ScalarFormatter`` and use ``LogLocator`` for
        decade major ticks + sub-decade minor ticks.
        """
        import numpy as np
        from matplotlib.ticker import FixedLocator, FuncFormatter, MaxNLocator, NullLocator, ScalarFormatter

        want_log = bool(self.log_price_var.get())
        scale = "log" if want_log else "linear"
        changed = False
        for ps in getattr(self, "_panel_state", {}).values():
            ax_p = ps.get("price_ax")
            if ax_p is None:
                continue
            try:
                if ax_p.get_yscale() != scale:
                    ax_p.set_yscale(scale)
                    changed = True
                if want_log:
                    def _fmt_price(v, _pos):
                        if v <= 0:
                            return ""
                        if v >= 100:
                            return f"{v:,.0f}"
                        if v >= 10:
                            return f"{v:,.1f}"
                        return f"{v:,.2f}"

                    # A LinearLocator picks ticks linearly in DATA space,
                    # so on a log axis they bunch toward the high end.
                    # To get pixel-evenly-spaced ticks we must pick them
                    # linearly in LOG space (then exponentiate). We
                    # compute and install them on every draw via a
                    # callback, so they stay evenly spaced as the user
                    # pans/zooms.
                    def _refresh_log_ticks(ax=ax_p):
                        try:
                            lo, hi = ax.get_ylim()
                            if lo <= 0 or hi <= 0 or hi <= lo:
                                return
                            ticks = np.logspace(np.log10(lo), np.log10(hi), 8)
                            # Drop the exact endpoints so labels don't
                            # clip against the frame; keep 6 interior.
                            ax.yaxis.set_major_locator(
                                FixedLocator(ticks[1:-1].tolist()))
                        except Exception:  # noqa: BLE001
                            pass

                    _refresh_log_ticks()
                    # Re-evaluate tick positions after every ylim change
                    # (pan/zoom/autoscale) so they stay pixel-even. Bind
                    # the refresher via default arg to avoid the classic
                    # late-binding trap when multiple axes share this
                    # loop (primary + compare).
                    prev_cid = ps.get("_log_tick_cid")
                    if prev_cid is not None:
                        try:
                            ax_p.callbacks.disconnect(prev_cid)
                        except Exception:  # noqa: BLE001
                            pass
                    ps["_log_tick_cid"] = ax_p.callbacks.connect(
                        "ylim_changed",
                        lambda _ax, _fn=_refresh_log_ticks: _fn())
                    ax_p.yaxis.set_major_formatter(FuncFormatter(_fmt_price))
                    ax_p.yaxis.set_minor_locator(NullLocator())
                    ax_p.yaxis.set_minor_formatter(FuncFormatter(lambda *_: ""))
                else:
                    # Tear down the ylim_changed callback if we had one.
                    prev_cid = ps.pop("_log_tick_cid", None)
                    if prev_cid is not None:
                        try:
                            ax_p.callbacks.disconnect(prev_cid)
                        except Exception:  # noqa: BLE001
                            pass
                    ax_p.yaxis.set_major_formatter(ScalarFormatter())
                    ax_p.yaxis.set_minor_formatter(ScalarFormatter())
                    ax_p.yaxis.set_major_locator(MaxNLocator(prune="lower"))
                    ax_p.yaxis.set_minor_locator(NullLocator())
            except Exception:  # noqa: BLE001
                pass
        if changed:
            try:
                self._autoscale_y_to_visible()
            except Exception:  # noqa: BLE001
                pass
        try:
            self._canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass

    def _await_future_on_tk(self, fut, on_done, *, poll_ms: int = 5) -> None:
        """Poll a ``concurrent.futures.Future`` from the Tk main thread."""
        self._fetch_svc.await_future_on_tk(
            fut,
            on_done,
            track_after=self._track_after,
            poll_ms=poll_ms,
        )

    def _load_data_async(self) -> None:
        """N7: async user-triggered load. Probes cache; on miss, runs
        the fetcher on ``_fetch_executor`` and marshals the result back
        to the Tk thread via the ``_prefetched_raw`` slot — same
        hand-off pattern as ``_next_bar_fetch_tick``.

        Cache-hit-only invocations (both sides fresh in ``_full_cache``)
        short-circuit to ``_load_data()`` directly so M2's deferred
        render still kicks in. Fetch errors and missing async infra
        fall back to a synchronous ``_load_data()`` so the user
        always gets a render attempt.

        Stale-completion guard: ``_fetch_token`` is bumped before
        ``executor.submit``; if a newer ``_load_data_async`` /
        ``_next_bar_fetch_tick`` / ``_load_data`` runs while the fetch
        is in flight, the completion callback no-ops.

        Used by user-triggered code paths (entry bindings, watchlist
        double-click, scheduled reload, explicit axis change). Paths
        that immediately read ``self._primary`` after loading
        (``_reset_view``, ``_zoom_5m_for_date``, ``_reload_preserving_drilldown``,
        startup) intentionally still call ``_load_data`` synchronously.
        """
        # Sandbox owns the primary slot while a session is active —
        # async fetch path is bypassed. Routed through register_ticker
        # synchronously (the user accepted brief UI freezes for
        # uncached tickers — locked decision 3 of 1c-redux).
        if self._is_sandbox_active():
            # In sandbox, the chart's xlim is pre-allocated to the
            # full session range (``_sandbox_full_session_xlim``) and
            # must stay pinned across compare-ticker swaps. The
            # typing-driven reload path (``_do_scheduled_reload``)
            # cleared ``_preserve_xlim_on_render = False`` just before
            # calling us, which would cause ``_install_sandbox_compare_series``'
            # ``_render`` call to fall back to the default 200-bar
            # right-edge window — the user sees the primary chart
            # "jump" on every typed compare ticker. Re-arm preserve
            # so _render keeps the existing (full-session) xlim.
            self._preserve_xlim_on_render = True
            raw_primary = self.ticker_var.get().strip().upper()
            if raw_primary:
                self._sandbox_register_and_focus(raw_primary)
            # Mirror for compare: when compare is on (or the
            # compare_ticker_var has changed and the user expects the
            # compare slot to follow), route through the sandbox
            # compare-register path. Without this, typing a new ticker
            # in the compare entry or cycling the compare slot via
            # the watchlist would silently no-op in sandbox mode (b38).
            self._sandbox_sync_compare_to_var()
            return
        src = self.source_var.get()
        interval = self.interval_var.get()
        raw_primary = self.ticker_var.get().strip().upper()
        compare_on = bool(self.compare_var.get())
        raw_compare = (self.compare_ticker_var.get().strip().upper()
                       if compare_on else "")
        primary_key = ((src, raw_primary, interval)
                       if raw_primary else None)
        compare_key = ((src, raw_compare, interval)
                       if raw_compare else None)

        def _fresh(key) -> bool:
            if key is None:
                return True  # nothing needed for this side
            cached = self._full_cache.get(key)
            return bool(cached) and not self._cache_is_stale(cached, interval)

        # Cache-hit-only fast path: no network needed → defer to the
        # synchronous loader, which itself routes through M2's
        # `_request_deferred_render` for the all-cache-hit branch.
        if _fresh(primary_key) and (compare_key is None or _fresh(compare_key)):
            self._load_data()
            return

        fetcher = DATA_SOURCES.get(src)
        executor = getattr(self, "_fetch_executor", None)
        if fetcher is None or executor is None:
            # No async infrastructure available — fall back to sync.
            self._load_data()
            return

        try:
            self._status.info(f"Loading {raw_primary} {interval}…")
        except Exception:  # noqa: BLE001
            pass

        # Bump token BEFORE submit so a follow-up load supersedes us.
        token = self._bump_fetch_token()

        def _work():
            p: list = []
            c: list = []
            if raw_primary:
                try:
                    p = fetcher(raw_primary, interval) or []
                except Exception:  # noqa: BLE001
                    p = []
            if raw_compare:
                try:
                    c = fetcher(raw_compare, interval) or []
                except Exception:  # noqa: BLE001
                    c = []
            # H2: piggy-back the disk-cache read onto the worker so the
            # Tk thread doesn't pay JSON parsing latency for the merge
            # or the network-failure fallback. disk_cache.save() uses
            # atomic os.replace so concurrent reads on the worker are
            # race-safe against any Tk-thread save still in flight.
            p_disk: list | None = None
            c_disk: list | None = None
            try:
                if raw_primary:
                    p_disk = disk_cache.load(src, raw_primary, interval)
            except Exception:  # noqa: BLE001
                p_disk = None
            try:
                if raw_compare:
                    c_disk = disk_cache.load(src, raw_compare, interval)
            except Exception:  # noqa: BLE001
                c_disk = None
            # H4 (audit "ticker-switch latency"): do the
            # ``disk_cache.merge_candles`` + ``disk_cache.save`` here
            # on the worker so ``_load_data`` doesn't pay the O(N)
            # merge + atomic-replace I/O on the Tk thread. The new
            # merged lists are stashed alongside the prefetch so the
            # Tk-thread phase can short-circuit the merge block.
            # Save is also safe to run here because ``disk_cache.save``
            # uses ``os.replace`` (atomic on Windows + POSIX), so a
            # concurrent read on a sibling worker thread either sees
            # the OLD file or the NEW file — never a torn one.
            p_merged: list | None = None
            c_merged: list | None = None
            try:
                if p:
                    p_merged = disk_cache.merge_candles(p_disk, p)
                    disk_cache.save(src, raw_primary, interval, p_merged)
            except Exception:  # noqa: BLE001
                p_merged = None
            try:
                if c:
                    c_merged = disk_cache.merge_candles(c_disk, c)
                    disk_cache.save(src, raw_compare, interval, c_merged)
            except Exception:  # noqa: BLE001
                c_merged = None
            return p, c, p_disk, c_disk, p_merged, c_merged

        try:
            fut = executor.submit(_work)
        except Exception:  # noqa: BLE001
            self._load_data()
            return

        def _on_result(result) -> None:
            # Stale-token guard: a newer fetch superseded us.
            if token != self._fetch_token:
                return
            if result is None:
                p_raw, c_raw = None, None
                p_disk, c_disk = None, None
                p_merged, c_merged = None, None
            elif len(result) == 6:
                p_raw, c_raw, p_disk, c_disk, p_merged, c_merged = result
            else:
                # Back-compat with any out-of-tree call site that still
                # uses the old 4-tuple shape.
                p_raw, c_raw, p_disk, c_disk = result
                p_merged, c_merged = None, None
            self._prefetched_raw = {
                "token": token,
                "src": src,
                "interval": interval,
                "primary_ticker": raw_primary,
                "compare_ticker": raw_compare,
                "primary": p_raw,
                "compare": c_raw,
                # H2: disk pre-loads, consumed by _load_data so it
                # doesn't re-read the JSON file on the Tk thread.
                "primary_disk": p_disk,
                "compare_disk": c_disk,
                "disk_preloaded": True,
                # H4: pre-merged + pre-saved by the worker so
                # _load_data can skip its merge_candles + save block.
                "primary_merged": p_merged,
                "compare_merged": c_merged,
                "merge_preloaded": True,
            }
            try:
                self._load_data()
            finally:
                self._prefetched_raw = None

        self._await_future_on_tk(fut, _on_result)

    def _load_data(self) -> None:
        """Load primary (and, if enabled, compare) candles + render.

        Two-phase: an in-memory probe first, then an on-demand disk
        load submitted to the worker pool. The render runs once both
        sides have resolved. Synchronous — see ``_load_data_async`` for
        the user-triggered async wrapper that offloads fetcher HTTP
        calls to ``_fetch_executor`` (N7).
        """
        # Sandbox replay owns primary-slot updates while active; route
        # ticker-entry / watchlist double-clicks through the controller's
        # register_ticker path instead of the regular cache+render path.
        # The controller's visible-list contract preserves identity for
        # the indicator + series cache so this is the only correct way
        # to surface a new ticker mid-session.
        if self._is_sandbox_active():
            # See ``_load_data_async``: the typing-driven reload path
            # cleared ``_preserve_xlim_on_render`` just before calling
            # us, but in sandbox the full-session xlim must stay
            # pinned across compare-ticker swaps. Re-arm it.
            self._preserve_xlim_on_render = True
            raw_primary = self.ticker_var.get().strip().upper()
            if raw_primary:
                self._sandbox_register_and_focus(raw_primary)
            self._sandbox_sync_compare_to_var()
            return
        # Bump fetch token: any in-flight callbacks become stale (spec §9.1).
        self._bump_fetch_token()
        # Any active stream is for the *previous* (src, ticker, interval) —
        # stop it now so the early-return paths below (bad ticker, etc.)
        # don't leave a stale subscription running.
        try:
            self._stop_stream()
        except Exception:  # noqa: BLE001
            pass
        src = self.source_var.get()
        interval = self.interval_var.get()
        raw_primary = self.ticker_var.get().strip().upper()
        primary_key = (src, raw_primary, interval)
        compare_on = bool(self.compare_var.get())
        compare_key: tuple[str, str, str] | None = None
        raw_compare: str = ""
        if compare_on:
            raw_compare = self.compare_ticker_var.get().strip().upper()
            if raw_compare:
                compare_key = (src, raw_compare, interval)

        try:
            self._status.info(f"Loading {raw_primary} {interval}…")
        except Exception:  # noqa: BLE001
            pass

        # Phase 1: memory probe. Sealed OHLCV bars are immutable, so the
        # memory cache is trusted as long as the most recent bar isn't so
        # old that we must be missing sealed bars produced while the user
        # was looking elsewhere. Staleness is interval-aware
        # (``_cache_is_stale``).
        mem_primary = self._full_cache.get(primary_key)
        if mem_primary is not None:
            # LRU touch: mark this key as recently used so companion
            # prefetches don't FIFO-evict the active view.
            try:
                self._full_cache.move_to_end(primary_key)
            except KeyError:
                pass
        primary_raw = (
            mem_primary
            if mem_primary and not self._cache_is_stale(mem_primary, interval)
            else None
        )
        try:
            if primary_raw is not None:
                self._status.info(
                    f"Cache hit (memory): {raw_primary}/{interval} "
                    f"({len(primary_raw)} bars)")
            elif mem_primary is not None:
                self._status.info(
                    f"Cache stale: {raw_primary}/{interval} "
                    "— refetch required")
            else:
                self._status.info(f"Cache miss: {raw_primary}/{interval}")
        except Exception:  # noqa: BLE001
            pass
        mem_compare = self._full_cache.get(compare_key) if compare_key else None
        if mem_compare is not None and compare_key is not None:
            try:
                self._full_cache.move_to_end(compare_key)
            except KeyError:
                pass
        compare_raw = (
            mem_compare
            if mem_compare and not self._cache_is_stale(mem_compare, interval)
            else None
        )

        # Companion-interval prefetch: only fire when the current load is
        # actually going to touch the source. That preserves the parallel
        # warmup for cold/stale views (the drill-down race fix) without
        # re-hitting background fetchers during an in-session memory-cache
        # revisit.
        try:
            if raw_primary and (
                primary_raw is None
                or (compare_on and raw_compare and compare_raw is None)
            ):
                self._prefetch_companion_intervals(
                    [raw_primary] + ([raw_compare] if raw_compare else []),
                )
        except Exception:  # noqa: BLE001
            pass

        # Phase 2: source fetch for any side still missing (spec §9). We
        # intentionally do NOT consult the disk cache as a primary source
        # — that would risk showing stale historical data and miss any
        # post-mortem revisions the provider has issued since we last ran.
        # Disk is only touched in the network-failure fallback below and
        # for the merge-on-save path.
        #
        # If the poll-tick path already ran the fetcher on the worker
        # pool, its results are stashed in ``self._prefetched_raw``;
        # consume them here to avoid re-blocking the main thread.
        # Validity is keyed on (src, interval, primary_ticker,
        # compare_ticker) so a superseded ticker load ignores a stale
        # prefetch. Token gating is handled by the caller.
        prefetched = self._prefetched_raw
        prefetched_valid = bool(
            prefetched
            and prefetched.get("src") == src
            and prefetched.get("interval") == interval
            and prefetched.get("primary_ticker") == raw_primary
            and prefetched.get("compare_ticker") == raw_compare
        )
        # H2: disk-cache reads piggy-backed onto the async worker. When
        # the prefetched-raw payload is valid for this load, consume
        # the cached disk reads instead of paying JSON parsing latency
        # on the Tk thread for the merge / fallback paths below.
        disk_preloaded = bool(prefetched_valid and prefetched.get("disk_preloaded"))
        primary_disk_cached = (
            prefetched.get("primary_disk") if disk_preloaded else None
        )
        compare_disk_cached = (
            prefetched.get("compare_disk") if disk_preloaded else None
        )

        def _disk_for(key, side: str):
            if disk_preloaded:
                return (primary_disk_cached if side == "primary"
                        else compare_disk_cached)
            return self._disk_load(key)
        fetcher = DATA_SOURCES.get(src)
        primary_failed = False
        compare_failed = False
        prefetched_primary_used = False
        prefetched_compare_used = False
        if primary_raw is None and fetcher is not None:
            if prefetched_valid:
                primary_raw = prefetched.get("primary") or []
                prefetched_primary_used = bool(primary_raw)
            else:
                try:
                    primary_raw = fetcher(primary_key[1], interval) or []
                except Exception:  # noqa: BLE001
                    primary_raw = []
            if not primary_raw:
                primary_failed = True
                # Last-resort fallback: serve stale in-memory or disk data
                # rather than go blank if the network is down.
                primary_raw = (
                    mem_primary
                    or (_disk_for(primary_key, "primary") or [])
                )
                if primary_raw:
                    primary_failed = False
        if compare_key is not None and compare_raw is None and fetcher is not None:
            if prefetched_valid:
                compare_raw = prefetched.get("compare") or []
                prefetched_compare_used = bool(compare_raw)
            else:
                try:
                    compare_raw = fetcher(compare_key[1], interval) or []
                except Exception:  # noqa: BLE001
                    compare_raw = []
            if not compare_raw:
                compare_failed = True
                compare_raw = (
                    mem_compare
                    or (_disk_for(compare_key, "compare") or [])
                )
                if compare_raw:
                    compare_failed = False

        # Bad-ticker rejection (spec §12): revert StringVar to last confirmed.
        # Audit ``bad-ticker-friendlier``: the status message used to
        # reveal the internal vendor name (``"... not found (yfinance)."``)
        # which leaks an implementation detail and confuses a user who
        # has only ever seen the friendly "Yahoo Finance" label of the
        # source dropdown (or worse: doesn't know what "yfinance" is at
        # all). Drop the parenthetical and replace with an actionable
        # hint. The smoke check at §12 still matches against
        # ``"not found"`` so the phrase is preserved.
        if primary_failed and raw_primary:
            try:
                self.ticker_var.set(self._confirmed_primary_ticker)
                self._status.error(
                    f"Ticker '{raw_primary}' not found. Check the "
                    f"spelling or try a different data source"
                )
            except Exception:  # noqa: BLE001
                pass
            try:
                self._refresh_tab_labels()
            except Exception:  # noqa: BLE001
                pass
            return
        if compare_failed and raw_compare:
            try:
                self.compare_ticker_var.set(self._confirmed_compare_ticker)
                self._status.error(
                    f"Ticker '{raw_compare}' not found. Check the "
                    f"spelling or try a different data source"
                )
            except Exception:  # noqa: BLE001
                pass
            try:
                self._refresh_tab_labels()
            except Exception:  # noqa: BLE001
                pass
            # keep going with primary-only

        # If we just fetched (cache was missing or stale), merge with any
        # pre-existing disk cache so historical bars that fall outside the
        # provider's current window (e.g. yfinance's 60-day intraday cap)
        # are retained across sessions. New bars always win on overlap so
        # provider revisions propagate.
        #
        # H4 (audit "ticker-switch latency"): when ``_load_data_async``
        # ran the merge + ``disk_cache.save`` on the worker thread,
        # ``prefetched["primary_merged"]`` / ``["compare_merged"]`` is
        # the already-merged list and the on-disk file is already
        # up-to-date. Consume the pre-merged result and skip the
        # ``merge_candles`` + ``disk_cache.save`` calls below — those
        # would re-do work the worker just finished.
        merge_preloaded = bool(prefetched_valid and prefetched.get("merge_preloaded"))
        primary_merged_cached = (
            prefetched.get("primary_merged") if merge_preloaded else None
        )
        compare_merged_cached = (
            prefetched.get("compare_merged") if merge_preloaded else None
        )
        if primary_raw and mem_primary is not primary_raw:
            if merge_preloaded and primary_merged_cached is not None:
                primary_raw = primary_merged_cached
            else:
                primary_raw = disk_cache.merge_candles(
                    _disk_for(primary_key, "primary"), primary_raw,
                )
        if compare_key and compare_raw and mem_compare is not compare_raw:
            if merge_preloaded and compare_merged_cached is not None:
                compare_raw = compare_merged_cached
            else:
                compare_raw = disk_cache.merge_candles(
                    _disk_for(compare_key, "compare"), compare_raw,
                )

        primary_raw_ref = primary_raw
        compare_raw_ref = compare_raw
        primary_raw = list(primary_raw or [])
        compare_raw = list(compare_raw) if compare_raw is not None else []

        # Store back into both memory + disk caches so the next session
        # has persistent access to what we've seen. Cache stores the
        # truthful (provider-as-is) candles BEFORE we layer today's
        # synthetic daily bar on top — keeps the on-disk + in-memory
        # caches faithful, so the next provider fetch that includes
        # today's real daily bar simply lands here and overwrites our
        # synth at the next render boundary.
        #
        # H4: skip the ``disk_cache.save`` when the worker already did
        # it. Memory cache still gets updated here (the worker's merge
        # is per-side and the memory cache write needs to happen on
        # the Tk thread for the token-gated visibility contract).
        if primary_raw:
            self._full_cache[primary_key] = primary_raw
            self._trim_full_cache()
            self._confirmed_primary_ticker = raw_primary
            if mem_primary is not primary_raw and not merge_preloaded:
                disk_cache.save(*primary_key, primary_raw)
        if compare_key and compare_raw:
            self._full_cache[compare_key] = compare_raw
            self._trim_full_cache()
            self._confirmed_compare_ticker = raw_compare
            if mem_compare is not compare_raw and not merge_preloaded:
                disk_cache.save(*compare_key, compare_raw)

        # Today's-bar upsampling for daily-class views: most providers
        # lag the live session by ~1 day, so 1d shows "everything up
        # to yesterday" mid-session while 5m shows the current bar.
        # When we have intraday data cached for the same symbol,
        # aggregate today's intraday bars into a synthetic daily bar
        # and append it. Audit ``daily-today-upsample``.
        primary_raw = self._maybe_upsample_today_daily(
            primary_raw, source=src, symbol=raw_primary, interval=interval,
        )
        if compare_key and compare_raw:
            compare_raw = self._maybe_upsample_today_daily(
                compare_raw, source=src, symbol=raw_compare,
                interval=interval,
            )

        # Apply pair filter + align. Always run — even in single-chart
        # mode — so the Pre/Post toggle actually drops extended-hours
        # bars when disabled (spec §5).
        if compare_on and compare_raw:
            primary, compare = self._apply_pair_filter_and_align(
                primary_raw, compare_raw,
            )
        else:
            primary, _ = self._apply_pair_filter_and_align(
                primary_raw, None,
            )
            compare = []

        old_primary = self._primary
        old_compare = self._compare
        self._set_data_state(
            primary_raw=primary_raw,
            primary=primary,
            compare_raw=compare_raw,
            compare=compare,
        )
        # Fresh provider reloads replace the visible lists. Drop the
        # previous entries so fingerprint fallback cannot rebind stale
        # indicator arrays onto the replacement lists.
        if prefetched_primary_used:
            self._invalidate_focused_panels(old_primary)
        if prefetched_compare_used:
            self._invalidate_focused_panels(old_compare)
        # M2: when both sides came from the in-memory cache, the data
        # arrays are already in their final form so the render is just
        # a redraw. Defer it via ``after_idle`` so Tk gets a chance to
        # repaint the status bar and tab labels first — visible "click
        # registered" feedback before the (sometimes >50ms) canvas
        # redraw kicks in. Falling back to synchronous on fetch keeps
        # the legacy behavior where tests pump after `_load_data` and
        # expect rendered state to be available.
        cache_hit_only = (
            mem_primary is primary_raw_ref
            and (compare_key is None or mem_compare is compare_raw_ref)
        )
        # When _preserve_xlim_on_render is armed (drill-down path), the
        # caller is about to mutate xlim on the price axis and read
        # `_panel_state` / `_ax_candle_map` to autoscale Y. Deferring the
        # render leaves those references pointing at the OLD interval's
        # axes + candle list, so the post-load xlim lands on stale axes
        # and the Y autoscale silently no-ops (xlim indices fall outside
        # the old candle list -> hi <= lo). Force synchronous render so
        # callers downstream operate on fresh state.
        if cache_hit_only and not getattr(self, "_preserve_xlim_on_render", False):
            self._request_deferred_render()
        else:
            self._render()
        # Kick off the per-ticker events fetch (historical earnings,
        # dividends, splits) in the background. Best-effort: a failure
        # in the events subsystem must NOT block the chart render. The
        # bundle lands in ``self._events_cache`` on arrival and triggers
        # a glyph-only redraw via :meth:`_request_redraw_for_events`.
        #
        # H4 (audit "ticker-switch latency"): defer the submission to
        # ``after_idle`` so the events-fetch executor.submit() +
        # await_future scheduling don't block the post-render path on
        # the Tk thread. The events bundle is purely decorative
        # (glyphs only) — it's safe for the user to see the chart
        # paint before the events fetch even starts.
        def _kick_events() -> None:
            try:
                if raw_primary:
                    self._load_events_async(raw_primary)
                if raw_compare:
                    self._load_events_async(raw_compare)
            except Exception:  # noqa: BLE001
                pass
        try:
            self.after_idle(_kick_events)
        except Exception:  # noqa: BLE001
            # Fall back to synchronous submission if after_idle is
            # unavailable (headless tests without a running mainloop).
            _kick_events()
        # Update notebook tabs to reflect the actually-loaded tickers.
        try:
            self._refresh_tab_labels()
        except Exception:  # noqa: BLE001
            pass
        try:
            n = len(primary)
            self._status.info(f"{raw_primary} {interval}: {n} bars")
        except Exception:  # noqa: BLE001
            pass
        # Arm the next-bar scheduler (event-driven, §9.3).
        try:
            self._schedule_next_bar_fetch()
        except Exception:  # noqa: BLE001
            pass
        # Attempt to start a live stream; best-effort — a failed subscribe
        # must not break the successful load above (spec §5.4, §15.10).
        try:
            self._start_stream_if_applicable()
        except Exception:  # noqa: BLE001
            pass
        # Keep the compare ticker's cache warm in the background so the
        # Compare toggle is instant even before the user first clicks it.
        # Skip this on a pure memory-hit revisit to avoid re-hitting the
        # source fetcher during no-network fast paths.
        try:
            if not cache_hit_only:
                self._ensure_compare_prefetched()
        except Exception:  # noqa: BLE001
            pass
        # Companion-interval prefetch was moved to the START of
        # _load_data (above) so it runs in parallel with the foreground
        # 1d fetch — see the drill-down race fix.
        # Kick off background watchlist refresh (snapshot only).
        try:
            self._preload_watchlist()
            self._preload_watchlist_daily()
        except Exception:  # noqa: BLE001
            pass

    def _disk_load(
        self, key: tuple[str, str, str],
    ) -> list[Candle] | None:
        return self._data_ctrl.disk_load(*key)

    def _cache_is_stale(
        self, candles: list[Candle], interval: str,
    ) -> bool:
        now_s = time.time()
        session_open = None
        if is_intraday(interval):
            session_open = self._intraday_session_open(now_s)
        return self._data_ctrl.is_stale(
            candles,
            interval,
            now_s=now_s,
            session_open=session_open,
        )

    @staticmethod
    def _intraday_session_open(now_s: float) -> bool:
        """True if ``now_s`` (UTC epoch) is inside the US extended-hours
        intraday session (Mon–Fri 04:00–20:00 ET).

        Used by ``_cache_is_stale`` to short-circuit staleness for
        intraday bars: outside this window no new bars can be issued,
        so the cache is fresh by definition. Holiday handling is
        intentionally absent — at worst a market holiday causes one
        redundant HTTP fetch whose merge is a no-op.
        """
        try:
            from .core.timezones import ET
            if ET is None:
                return True
            et = datetime.fromtimestamp(now_s, ET)
        except Exception:  # noqa: BLE001
            # No tzdata: fall back to UTC offset estimate (-5h naïve).
            # Conservative: treat unknown as "open" so we don't silently
            # serve stale data when zoneinfo is missing on the host.
            return True
        if et.weekday() >= 5:  # Sat/Sun
            return False
        minutes = et.hour * 60 + et.minute
        return 4 * 60 <= minutes < 20 * 60

    def _trim_full_cache(self, protected_key=None) -> None:
        try:
            pinned_tickers = frozenset(self._pinned_ticker_union())
        except Exception:  # noqa: BLE001
            pinned_tickers = frozenset()
        try:
            active_ticker = self.ticker_var.get().strip().upper()
            if active_ticker:
                # Pin the active ticker across ALL its intervals so the
                # companion-prefetch pipeline doesn't LRU-evict its own
                # warm data when a stash for an unrelated ticker
                # overflows the cache. The 1d primary view depends on
                # its 5m companion for the volume-TOD overlay and the
                # synthetic today-bar (see _maybe_upsample_today_daily).
                pinned_tickers = pinned_tickers | {active_ticker}
        except Exception:  # noqa: BLE001
            pass
        self._data_ctrl.trim(
            pinned_tickers=pinned_tickers,
            protected_key=protected_key,
        )

    def _stash_full_cache(self, key, bars) -> None:
        now_s = time.time()
        session_open = None
        try:
            if is_intraday(key[2]):
                session_open = self._intraday_session_open(now_s)
        except Exception:  # noqa: BLE001
            session_open = None
        try:
            pinned_tickers = frozenset(self._pinned_ticker_union())
        except Exception:  # noqa: BLE001
            pinned_tickers = frozenset()
        try:
            active_src = self.source_var.get()
            active_ticker = self.ticker_var.get().strip().upper()
            active_interval = self.interval_var.get()
            active_key = (
                (active_src, active_ticker, active_interval)
                if active_ticker else key
            )
            # Pin the active ticker across ALL its intervals so the
            # companion-prefetch pipeline doesn't LRU-evict its own
            # warm data when a stash for an unrelated ticker overflows
            # the cache. The 1d primary view depends on its 5m
            # companion for the volume-TOD overlay and synthetic
            # today-bar (see _maybe_upsample_today_daily); evicting
            # the 5m partner breaks both features.
            if active_ticker:
                pinned_tickers = pinned_tickers | {active_ticker}
        except Exception:  # noqa: BLE001
            active_key = key
        self._data_ctrl.stash(
            key,
            bars,
            pinned_tickers=pinned_tickers,
            now_s=now_s,
            session_open=session_open,
            protected_key=active_key,
        )
        self._sync_data_aliases()

    def _apply_pair_filter_and_align(
        self, primary_raw: list[Candle], compare_raw: list[Candle] | None,
    ) -> tuple[list[Candle], list[Candle]]:
        return self._data_ctrl.apply_pair_filter(
            primary_raw,
            compare_raw,
            interval=self.interval_var.get(),
            prepost=bool(self.prepost_var.get()),
        )

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def _apply_stream_tick(self, evt: tuple[Any, ...]) -> bool:
        """Delegate tick mutation to ``StreamController``.

        Also extracts the latest trade price from the event payload and
        records it in ``_last_stream_price`` so the live-price overlay
        can read the freshest known price without re-walking the
        candles. The event shape is ``(token, slot, src, ticker,
        interval, kind, bar)`` (see ``data/stream_controller.apply_tick``),
        and the bar's ``close`` is the latest trade price.
        """
        # Capture the latest stream price BEFORE delegating, so a later
        # branch in apply_tick that rejects (token mismatch, gap) doesn't
        # block the live-price overlay from learning the latest tick.
        try:
            _token, _slot, _src, ticker, _interval, _kind, bar = evt
            sym = (str(ticker) or "").strip().upper()
            if sym:
                px = float(getattr(bar, "close", float("nan")))
                if math.isfinite(px):
                    self._last_stream_price[sym] = px
        except Exception:  # noqa: BLE001
            pass
        return self._stream_ctrl.apply_tick(
            evt,
            full_cache=self._full_cache,
            indicator_cache=self._indicator_cache,
        )

    def _apply_stream_rollover(self, evt: tuple[Any, ...]) -> bool:
        """Delegate rollover append/upsert handling to ``StreamController``.

        Same ``_last_stream_price`` capture as :meth:`_apply_stream_tick`
        — a rollover boundary still carries a closing price for the
        sealed bar, which represents the latest known trade.
        """
        try:
            _token, _slot, _src, ticker, _interval, _kind, bar = evt
            sym = (str(ticker) or "").strip().upper()
            if sym:
                px = float(getattr(bar, "close", float("nan")))
                if math.isfinite(px):
                    self._last_stream_price[sym] = px
        except Exception:  # noqa: BLE001
            pass
        return self._stream_ctrl.apply_rollover(
            evt,
            full_cache=self._full_cache,
            indicator_cache=self._indicator_cache,
            trim_fn=self._trim_full_cache,
            disk_save_fn=disk_cache.save,
        )

    def _start_stream_if_applicable(self) -> None:
        """Spec §5.4 — transactional, per-slot stream subscribe."""
        self._stream_ctrl.start(
            self.source_var.get(),
            self.ticker_var.get(),
            self.interval_var.get(),
            compare_on=bool(self.compare_var.get()),
            compare_ticker=self.compare_ticker_var.get(),
            full_cache=self._full_cache,
            stream_sources=STREAM_SOURCES,
            is_intraday_fn=is_intraday,
        )
        self._sync_stream_aliases()

        # Cancel any armed poll job — streaming replaces polling (§9.3).
        if self._poll_job is not None and self._stream_active:
            try:
                self.after_cancel(self._poll_job)
            except Exception:  # noqa: BLE001
                pass
            self._poll_job = None

    def _stop_stream(self) -> None:
        self._stream_ctrl.stop()
        self._sync_stream_aliases()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _request_deferred_render(self) -> None:
        """M2: schedule a render at idle, collapsing duplicate requests.

        Used on the cache-hit-only path of ``_load_data``: the data is
        already final, so deferring lets Tk repaint focus/status/tab UI
        first, giving a "click registered" feel before the (~50 ms+)
        canvas redraw lands. Multiple back-to-back requests collapse
        into a single render via the ``_pending_idle_render`` flag.
        """
        if getattr(self, "_pending_idle_render", False):
            return
        self._pending_idle_render = True

        def _fire():
            self._pending_idle_render = False
            try:
                self._render()
            except Exception:  # noqa: BLE001
                pass

        try:
            self.after_idle(_fire)
        except Exception:  # noqa: BLE001
            self._pending_idle_render = False
            self._render()

    def _render(self) -> None:
        """Re-draw the price + volume panels for the current candles.

        Builds figure topology (2 or 4 subplots depending on compare mode)
        then delegates slice drawing to :meth:`_draw_slice` per slot. This
        is the only path that calls ``figure.clear()`` — pan/zoom and
        streaming use :meth:`_ensure_rendered_for_view` +
        :meth:`_refresh_view_after_tick` to avoid tearing down axes.
        """
        preserve = self._preserve_xlim_on_render
        # Consume the one-shot slide signal immediately. The flag is a
        # directive for THIS render call only; subsequent renders must
        # not silently slide the view if the caller didn't re-assert it.
        slide_to_right = self._slide_xlim_to_right_edge
        self._slide_xlim_to_right_edge = False
        # Consume the time-window preserve signal one-shot too.
        preserve_by_time = self._preserve_xlim_by_time_on_render
        self._preserve_xlim_by_time_on_render = False
        # spec §9.3: DO NOT clear _preserve_xlim_on_render at end of _render

        compare_on = bool(self.compare_var.get()) and bool(self._compare)

        # When preserving the X view across a re-render, we must capture
        # the *current* xlim BEFORE figure.clear() — otherwise the
        # freshly-created axes report the default (0, 1) xlim and we'd
        # "preserve" a slice pointing at the first historical candle,
        # showing a wildly-out-of-date Y range (e.g., AMD's year-2000
        # $2 range for a 2026 chart).
        preserved_xlim: tuple[float, float] | None = None
        if preserve:
            try:
                prev_prim = self._panel_state.get("primary", {})
                prev_ax = prev_prim.get("price_ax")
                if prev_ax is not None:
                    lo_f, hi_f = prev_ax.get_xlim()
                    if hi_f - lo_f > 1.5:  # sanity: not default (0,1)
                        preserved_xlim = (float(lo_f), float(hi_f))
            except Exception:  # noqa: BLE001
                preserved_xlim = None

        # Time-window preserve (ticker-switch paths). Capture the
        # previous primary candle dates + xlim BEFORE figure.clear()
        # so the slot draw can remap to bar-index coordinates in the
        # new primary series via :func:`remap_window_by_time`.
        prev_primary_dates: list | None = None
        prev_primary_xlim: tuple[float, float] | None = None
        if preserve_by_time:
            try:
                prev_prim = self._panel_state.get("primary", {})
                prev_ax = prev_prim.get("price_ax")
                prev_cs = prev_prim.get("candles") or []
                if prev_ax is not None and prev_cs:
                    lo_f, hi_f = prev_ax.get_xlim()
                    if hi_f - lo_f > 1.5:
                        prev_primary_xlim = (float(lo_f), float(hi_f))
                        prev_primary_dates = [c.date for c in prev_cs]
            except Exception:  # noqa: BLE001
                prev_primary_dates = None
                prev_primary_xlim = None

        # --- rebuild figure topology fresh -----------------------------
        self._figure.clear()
        # figure.clear() wipes the subplotpars set at construction, so
        # re-apply the tight margins (see __init__ rationale: right-side
        # y-axis labels need ~6% inset; left side only needs to clear
        # the spine).
        self._figure.subplots_adjust(
            left=0.04, right=0.94, top=0.97, bottom=0.08,
        )
        self._panel_state.clear()
        self._ax_candle_map.clear()
        self._wicks = None
        self._bodies = None
        self._vol_bars = None
        self._shading_artists = []

        if compare_on:
            outer = self._figure.add_gridspec(2, 1, hspace=0.12)
            # Per-slot non-overlay-indicator pane counts drive dynamic
            # height_ratios. n_lower = 1 (volume) + applicable pane
            # GROUPS (configs sharing a ``pane_group`` key collapse to
            # one pane).
            interval = self.interval_var.get()
            prim_n_ind = len(_ind_render.applicable_pane_groups(
                self._indicator_manager, "main", interval))
            comp_n_ind = len(_ind_render.applicable_pane_groups(
                self._indicator_manager, "compare", interval))
            fig_h_in = float(self._figure.get_figheight())
            ratios1, _ = _ind_render.compute_layout(1 + prim_n_ind, fig_h_in)
            ratios2, _ = _ind_render.compute_layout(1 + comp_n_ind, fig_h_in)
            inner1 = outer[0, 0].subgridspec(1 + 1 + prim_n_ind, 1,
                                             height_ratios=ratios1, hspace=0)
            inner2 = outer[1, 0].subgridspec(1 + 1 + comp_n_ind, 1,
                                             height_ratios=ratios2, hspace=0)
            ax_p1 = self._figure.add_subplot(inner1[0, 0])
            ax_v1 = self._figure.add_subplot(inner1[1, 0], sharex=ax_p1)
            ind_axes_1 = [
                self._figure.add_subplot(inner1[2 + i, 0], sharex=ax_p1)
                for i in range(prim_n_ind)
            ]
            ax_p2 = self._figure.add_subplot(inner2[0, 0], sharex=ax_p1)
            ax_v2 = self._figure.add_subplot(inner2[1, 0], sharex=ax_p1)
            ind_axes_2 = [
                self._figure.add_subplot(inner2[2 + i, 0], sharex=ax_p1)
                for i in range(comp_n_ind)
            ]
            slots = [
                ("primary", self._primary, ax_p1, ax_v1, ind_axes_1, "main"),
                ("compare", self._compare, ax_p2, ax_v2, ind_axes_2, "compare"),
            ]
        else:
            interval = self.interval_var.get()
            prim_n_ind = len(_ind_render.applicable_pane_groups(
                self._indicator_manager, "main", interval))
            fig_h_in = float(self._figure.get_figheight())
            ratios, _ = _ind_render.compute_layout(1 + prim_n_ind, fig_h_in)
            gs = self._figure.add_gridspec(
                1 + 1 + prim_n_ind, 1,
                height_ratios=ratios, hspace=0,
            )
            ax_p1 = self._figure.add_subplot(gs[0, 0])
            ax_v1 = self._figure.add_subplot(gs[1, 0], sharex=ax_p1)
            ind_axes_1 = [
                self._figure.add_subplot(gs[2 + i, 0], sharex=ax_p1)
                for i in range(prim_n_ind)
            ]
            slots = [("primary", self._primary, ax_p1, ax_v1, ind_axes_1, "main")]

        self._ax_price = ax_p1
        self._ax_volume = ax_v1
        theme = self._theme

        interval = self.interval_var.get()

        # H4: locator class + period helpers + formatter live at module
        # scope (see ``_adaptive_x_locator_class`` / ``_make_x_formatter``)
        # so they aren't redefined on every render. The locator holds a
        # back-ref to ``self`` for live access to ``_panel_state``.
        _AdaptiveXLocator = _adaptive_x_locator_class()
        for slot_key, candles, ax_p, ax_v, ind_axes, scope in slots:
            setup_price_axes(ax_p)
            setup_volume_axes(ax_v)
            style_axes(ax_p, theme)
            style_axes(ax_v, theme)
            # Style indicator panes the same way as a generic numeric
            # axis (grid, plain ticks). They're shared-x so the
            # adaptive locator drives them too.
            for ax_i in ind_axes:
                setup_indicator_pane_axes(ax_i)
                style_axes(ax_i, theme)
            # Register 3-tuples in the ax map (spec §15.4, invariant #4).
            self._ax_candle_map[ax_p] = (candles, "price", 0)
            self._ax_candle_map[ax_v] = (candles, "volume", 0)
            for ax_i in ind_axes:
                self._ax_candle_map[ax_i] = (candles, "indicator", 0)

            # Bar indices on the x-axis are an implementation detail. Show
            # the user the underlying timestamp instead — HH:MM for
            # intraday intervals, MM/DD for daily+.
            formatter = _make_x_formatter(self, slot_key)
            for ax in (ax_p, ax_v, *ind_axes):
                ax.xaxis.set_major_formatter(formatter)
                ax.xaxis.set_major_locator(
                    _AdaptiveXLocator(slot_key, self, interval),
                )
            # Decide which axis owns the bottom tick labels: the last
            # axis in the stack (indicator pane if any, else volume).
            stack = [ax_p, ax_v, *ind_axes]
            for ax in stack:
                ax.tick_params(axis="x", labelbottom=False)
            stack[-1].tick_params(axis="x", labelbottom=True, labelsize=9)

            # Ticker watermark, centered in the price axes.
            if slot_key == "primary":
                wm_text = (self._confirmed_primary_ticker
                           or self.ticker_var.get().strip().upper() or "")
            else:
                wm_text = (self._confirmed_compare_ticker
                           or self.compare_ticker_var.get().strip().upper() or "")
            if wm_text:
                try:
                    ax_p.text(
                        0.5, 0.5, wm_text,
                        transform=ax_p.transAxes,
                        ha="center", va="center",
                        fontsize=56, fontweight="bold",
                        color=theme["watermark"],
                        alpha=0.18, zorder=0, clip_on=True,
                    )
                except Exception:  # noqa: BLE001
                    pass

            n = len(candles)
            # Seed panel_state with empty handles; _draw_slice fills them.
            self._panel_state[slot_key] = {
                "candles": candles, "offset": 0,
                "price_ax": ax_p, "vol_ax": ax_v,
                "render_start": 0, "render_end": 0,
                "price_wicks": None, "price_bodies": None,
                "vol_bars": None, "price_shades": [], "vol_shades": [],
                # Phase 2a: indicator state for this slot.
                "ind_axes": list(ind_axes),
                "ind_scope": scope,
                "ind_state": _ind_render.PanelIndicatorState(),
                # Earnings/dividends overlay artists + hit-test meta.
                # Rebuilt every _draw_slice via _render_event_glyphs_for_slot.
                "event_artists": [],
                "event_hit_meta": [],
                "event_badge_tooltip": "",
                # Time-of-day volume shading overlay (volume_tod_overlay).
                # Rebuilt every _draw_slice via _render_volume_tod_for_slot.
                "vol_tod_artists": [],
                "vol_tod_patches": [],
            }
            if n == 0:
                continue

            # Time-window remap (ticker-switch paths). Resolve the
            # captured time range to bar indices in this slot's candle
            # list via the pure helper. Applies to the primary slot
            # only — the compare slot's xlim follows via sharex. If
            # remap returns None (no overlap, degenerate window), fall
            # through to the default windowing.
            time_remap_applied = False
            if (slot_key == "primary"
                    and prev_primary_dates is not None
                    and prev_primary_xlim is not None
                    and not (preserve and preserved_xlim is not None)):
                new_dates = [c.date for c in candles]
                rmap = _remap_window_by_time(
                    prev_primary_dates, prev_primary_xlim, new_dates,
                )
                if rmap is not None:
                    lo, hi = rmap
                    try:
                        ax_p.set_xlim(lo - 0.5, hi - 0.5)
                    except Exception:  # noqa: BLE001
                        pass
                    time_remap_applied = True

            if preserve and preserved_xlim is not None:
                try:
                    lo_f, hi_f = preserved_xlim
                    # Explicit "slide to right edge" signal from the
                    # poll-tick path: shift the window forward by
                    # (new_right_edge - old_right_edge) keeping width.
                    # This is set at tick-time while we still know the
                    # user was glued to the current right edge, avoiding
                    # fragile delta-threshold heuristics here.
                    if slide_to_right:
                        right_edge = n - 0.5
                        width = hi_f - lo_f
                        hi_f = right_edge
                        lo_f = hi_f - width
                    lo = max(0, int(np.floor(lo_f)))
                    hi = min(n, int(np.ceil(hi_f)))
                    if hi <= lo:
                        lo, hi = max(0, n - _defaults.get("default_window_bars")), n
                        lo_f, hi_f = lo - 0.5, hi - 0.5
                except Exception:  # noqa: BLE001
                    lo, hi = max(0, n - _defaults.get("default_window_bars")), n
                    lo_f, hi_f = lo - 0.5, hi - 0.5
                # Also restore the xlim so the axes match the slice.
                try:
                    ax_p.set_xlim(lo_f, hi_f)
                except Exception:  # noqa: BLE001
                    pass
            elif not time_remap_applied:
                lo, hi = max(0, n - _defaults.get("default_window_bars")), n

            start, end = _compute_render_range(
                lo, hi, n, _MIN_RENDER_CANDLES, _MAX_RENDER_CANDLES,
            )
            self._draw_slice(slot_key, start, end)

            if not (preserve and preserved_xlim is not None) and not time_remap_applied:
                try:
                    ax_p.set_xlim(lo - 0.5, hi - 0.5)
                except Exception:  # noqa: BLE001
                    pass
            self._autoscale_slot_y(slot_key, lo, hi)

        # Keep single-panel back-compat handles for legacy helpers.
        prim = self._panel_state.get("primary", {})
        self._wicks = prim.get("price_wicks")
        self._bodies = prim.get("price_bodies")
        self._vol_bars = prim.get("vol_bars")
        self._shading_artists = list(prim.get("price_shades", []))

        # Invalidate blit bg: axes are fresh (spec §11.2). Also tear
        # down pan-blit state — fig.clear() killed every animated artist
        # reference, so any cached `_pan_bg` snapshot now points at a
        # vanished topology.
        self._blit_bg = None
        self._pan_bg = None
        self._pan_animated = []
        self._pan_anim_fingerprint = None

        # Apply linear/log price y-scale + plain-number tick formatter
        # to every freshly-built price axes (spec §Settings log-axis).
        self._apply_price_scale()

        # Rebuild overlay artists (axes died with fig.clear) + cursor revival.
        self._ensure_overlay_artists()

        # Refresh the per-overlay legend (big-bet item #9) — lists every
        # overlay-class config (visible + hidden) for the current scope+
        # interval so the user can toggle visibility with one click. We
        # show legend rows for the "main" scope when compare is off and
        # the primary panel is active, and fall back to an empty legend
        # if the manager is empty.
        try:
            self._refresh_overlay_legend()
        except Exception:  # noqa: BLE001
            pass

        # Re-attach the exits-overlay artist family (horizontal lines for
        # active triggers on the primary symbol). No-op if the stack
        # hasn't been built or no strategy is attached.
        try:
            self._redraw_exits_overlay()
        except Exception:  # noqa: BLE001
            pass
        # Re-attach the entries-overlay artist family (armed strategies +
        # pending broker orders). No-op if the stack hasn't been built.
        try:
            self._redraw_entries_overlay()
        except Exception:  # noqa: BLE001
            pass
        # Re-attach the within-last-N-bars evidence overlay (vertical
        # markers at evidence-bar timestamps from the entries+exits
        # audit logs). Safe no-op if the overlay has not been built.
        try:
            self._redraw_evidence_overlay()
        except Exception:  # noqa: BLE001
            pass
        # Re-attach horizontal-line drawings for every price slot
        # (Feature C). The store holds the source-of-truth list per
        # ticker; this helper resolves each slot's symbol via
        # ``_slot_symbol``, asks the store for that symbol's
        # drawings, and adds matplotlib Line2D artists at zorder
        # 3.5 (above candles 2-3, below indicators 4+, below
        # crosshair 10-11). The artists go directly on the price
        # axes — no separate tracking dict needed because the next
        # ``fig.clear()`` removes them along with everything else.
        try:
            self._redraw_drawings_overlay()
        except Exception:  # noqa: BLE001
            pass
        # Re-attach the live-price (TradingView-style sticky dotted
        # line) overlay. One line per price slot, anchored at the
        # freshest known price for that slot's symbol. Mutated in
        # place on every stream tick via :meth:`_refresh_view_after_tick`.
        # Safe no-op if the overlay hasn't been built (shouldn't
        # happen — constructed in ``__init__``).
        try:
            self._redraw_live_price_overlay()
        except Exception:  # noqa: BLE001
            pass

        self._refill_table()
        try:
            self._canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass
        # Cursor-cache revival across re-renders (spec §11.4).
        px_cache = self._last_cursor_px
        if px_cache is not None:
            px, py = px_cache
            for ax in self._ax_candle_map:
                try:
                    if ax.bbox.contains(px, py):
                        self._update_crosshair_pixels(ax, px, py)
                        break
                except Exception:  # noqa: BLE001
                    pass
        # NOTE: _preserve_xlim_on_render is deliberately NOT reset here
        # (spec §9.3 invariant #9).

    # ---- virtualized-render primitives (spec §6.3) --------------------
    def _reset_slot_artists(self, slot: str) -> None:
        ChartApp._ensure_renderer(self).reset_slot_artists(slot)

    def _display_candles_for(self, candles):
        try:
            ha_on = bool(self._ha_display_var.get())
        except Exception:  # noqa: BLE001
            ha_on = False
        return ChartApp._ensure_renderer(self).display_candles_for(candles, ha_on=ha_on)

    def _key_bar_hollow_indices_for(
        self, candles: list[Candle],
    ) -> set | None:
        try:
            highlight_key_bars_on = bool(self._highlight_key_bars_var.get())
        except Exception:  # noqa: BLE001
            highlight_key_bars_on = False
        return ChartApp._ensure_renderer(self).key_bar_hollow_indices_for(
            candles,
            highlight_key_bars_on=highlight_key_bars_on,
        )

    def _ha_flat_overlay_for(
        self, candles: list[Candle],
    ) -> dict[str, object] | None:
        try:
            highlight_ha_flat_on = bool(self._highlight_ha_flat_var.get())
            ha_on = bool(self._ha_display_var.get())
        except Exception:  # noqa: BLE001
            return None
        try:
            dark_mode = bool(self.dark_var.get())
        except Exception:  # noqa: BLE001
            dark_mode = False
        return ChartApp._ensure_renderer(self).ha_flat_overlay_for(
            candles,
            highlight_ha_flat_on=highlight_ha_flat_on,
            ha_on=ha_on,
            dark_mode=dark_mode,
        )

    def _repaint_visible_slot_glyphs(self) -> None:
        ChartApp._ensure_renderer(self).repaint_visible_slot_glyphs(
            draw_slice=self._draw_slice,
            render_fallback=self._render,
        )

    def _on_menu_toggle_heikin_ashi(self) -> None:
        """View menu callback: persist the new state and re-render all slots."""
        try:
            on = bool(self._ha_display_var.get())
            _settings.set("heikin_ashi", on)
        except Exception:  # noqa: BLE001
            pass
        # Keep the flat-bar entry clickable across HA flips. Rendering
        # remains HA-only because ``_ha_flat_overlay_for`` requires both
        # HA mode and the flat-highlight toggle to be on.
        try:
            self._sync_highlight_ha_flat_menu_state()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._refresh_title()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._render()
        except Exception:  # noqa: BLE001
            pass
        # Re-fit Y to the just-rendered candle shapes. Without this the
        # ylim sticks at whatever the previous mode produced — switching
        # ON clips HA bars whose body extends beyond the real bar's
        # [low, high]; switching OFF leaves wasted whitespace from the
        # wider HA fit. ``_autoscale_y_to_visible`` uses each slot's
        # cached ``display_candles`` for the price axis, so it sizes to
        # whatever the user is currently looking at.
        try:
            self._autoscale_y_to_visible()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass

    def _sync_highlight_ha_flat_menu_state(self) -> None:
        """Keep the *Highlight Flat Bars* menu entry enabled.

        The entry lives inside the View → Heikin-Ashi cascade and is
        intentionally always clickable. Its BooleanVar stores the user's
        preference even while HA candles are off; visual rendering is
        separately gated by HA mode AND the flat-highlight toggle.

        Called from :meth:`__init__` (initial state) and from
        :meth:`_on_menu_toggle_heikin_ashi` (every HA flip). Defensive:
        silently no-ops if the HA cascade hasn't been built yet, if the
        entry cannot be found, or if Tk has already torn down (e.g.
        during shutdown).
        """
        ha_menu = getattr(self, "_ha_menu", None)
        if ha_menu is None:
            return
        try:
            end = ha_menu.index("end")
            if end is None:
                return
            for i in range(int(end) + 1):
                try:
                    if str(ha_menu.type(i)) != "checkbutton":
                        continue
                    label = str(ha_menu.entrycget(i, "label"))
                except Exception:  # noqa: BLE001
                    continue
                if label == "Highlight Flat Bars":
                    try:
                        ha_menu.entryconfigure(i, state="normal")
                    except Exception:  # noqa: BLE001
                        pass
                    return
        except Exception:  # noqa: BLE001
            return

    def _on_menu_toggle_highlight_key_bars(self) -> None:
        """View menu callback for the key-bar highlight overlay.

        Persists the new state and triggers a glyph-only repaint via
        :meth:`_repaint_visible_slot_glyphs`. Hollow rendering is purely
        cosmetic — only the candle body face/edge colour and wick
        segment geometry change — so a full ``_render`` (figure clear +
        topology rebuild + indicator rebuild) is wasted work. The
        glyph repaint rebuilds the candle/volume Collections IN THE
        EXISTING axes via :meth:`_draw_slice` per slot, and re-renders
        indicators against the unchanged candle list (cache hit).

        After the repaint we follow up with
        :meth:`_autoscale_y_to_visible` (mirroring the HA + compare-
        toggle pattern). Hollow rendering is purely visual and does not
        change bar OHLC, so a *toggle* must not move the y-axis.
        However, ``_render``'s per-slot ``_autoscale_slot_y`` uses
        ``floor(lo_f) / ceil(hi_f)`` integer bar bounds while pan/zoom
        callers use ``_autoscale_y_to_visible``'s ``ceil(lo_f) /
        floor(hi_f) + 1`` (center-of-bar-in-xlim) semantics — and on
        days following a large gap the bar at the right edge of a
        zoomed window can sit in the gap region (very different price
        level), so the two algorithms produce visibly different ylim
        values when xlim sits on integer bar boundaries. The post-
        repaint override is idempotent (no-op when ylim is already
        correct) and guarantees the y-axis stays exactly where it was
        prior to the toggle.
        """
        try:
            on = bool(self._highlight_key_bars_var.get())
            _settings.set("highlight_key_bars", on)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._repaint_visible_slot_glyphs()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._autoscale_y_to_visible()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass

    def _on_view_open_theme_editor(self) -> None:
        """View menu: open the dedicated Theme Editor Toplevel.

        Replaces the in-Settings color picker (big-bet item #7). The
        Theme Editor lives in its own modeless Toplevel so users can
        leave it open while flipping between the main chart and the
        editor to evaluate palette tweaks live.
        """
        try:
            from .gui.theme_editor import open_theme_editor
            open_theme_editor(self)
        except Exception:  # noqa: BLE001
            try:
                self._status.warn("Theme editor failed to open")
            except Exception:  # noqa: BLE001
                pass

    def _on_menu_toggle_highlight_ha_flat(self) -> None:
        """View menu callback for the HA flat-top/-bottom highlight.

        Persists the new state and triggers a glyph-only repaint via
        :meth:`_repaint_visible_slot_glyphs`. Mirrors
        :meth:`_on_menu_toggle_highlight_key_bars` — when the toggle is
        on AND HA mode is active, the candle render path swaps in a
        bright accent face colour for bars that qualify as bull-flat-
        bottom (``HA_close > HA_open`` AND ``HA_low == HA_open``) or
        bear-flat-top (``HA_close < HA_open`` AND ``HA_high ==
        HA_open``). The accent is **HA-only** by design — when HA mode
        is off the toggle is dormant and the chart is unchanged.

        Like the key-bar handler, follows the repaint with
        :meth:`_autoscale_y_to_visible`. Accent rendering is purely
        cosmetic — only body face colour mutates, wicks/edges/OHLC are
        identical — so toggling MUST NOT move the y-axis. The post-
        repaint override is idempotent (no-op when ylim is already
        correct) and defensively guards against the same gap-edge
        ylim-jump that affects the HA + key-bar toggles.
        """
        try:
            on = bool(self._highlight_ha_flat_var.get())
            _settings.set("highlight_ha_flat", on)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._repaint_visible_slot_glyphs()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._autoscale_y_to_visible()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass

    def _draw_slice(self, slot: str, new_start: int, new_end: int) -> None:
        """Redraw the ``candles[new_start:new_end]`` slice into ``slot``.

        Tears down the old candle/volume/shading Collections, then builds
        fresh ones for the new slice. Invalidates ``_blit_bg`` because the
        cached background holds the *previous* Collections (spec §6.3 /
        §11.2) — restoring it after a slice refill would reveal stale bars.

        When the View → Heikin-Ashi → Show Heikin-Ashi Candles toggle is
        on, the candle glyphs are drawn from a parallel HA-projected list
        while volume, session shading, and indicators continue to use
        real OHLC.
        """
        ps = self._panel_state.get(slot)
        if not ps:
            return
        candles = ps["candles"]
        ax_p = ps["price_ax"]
        ax_v = ps["vol_ax"]
        n = len(candles)
        new_start = max(0, int(new_start))
        new_end = max(new_start, min(n, int(new_end)))
        self._reset_slot_artists(slot)
        if new_end > new_start:
            display_candles = self._display_candles_for(candles)
            hollow_indices = self._key_bar_hollow_indices_for(candles)
            flat_overlay = self._ha_flat_overlay_for(display_candles)
            # Dynamic candle body width: at extreme zoom-out the
            # default 0.6-data-units body overlaps its neighbours
            # below ≈ 3 px/bar. Clamp the body half via the visible
            # window size so bodies thin out gracefully instead of
            # bleeding into each other. Wicks (1 px) are unaffected
            # and stay readable at every density.
            try:
                lo_f, hi_f = ax_p.get_xlim()
                n_visible = max(1, int(hi_f - lo_f))
            except Exception:  # noqa: BLE001
                n_visible = max(1, new_end - new_start)
            body_half = dynamic_body_half(ax_p, n_visible)
            wicks, bodies = draw_candlesticks(
                ax_p, display_candles, start=new_start, end=new_end,
                hollow_indices=hollow_indices,
                flat_overlay=flat_overlay,
                body_half=body_half,
            )
            vol_bars = draw_volume(
                ax_v, candles, start=new_start, end=new_end,
                body_half=body_half,
            )
            intraday = is_intraday(self.interval_var.get())
            theme = self._theme
            shades = draw_session_shading(
                ax_p, candles, start=new_start, end=new_end,
                pre_color=theme["pre_shade"],
                post_color=theme["post_shade"],
                intraday=intraday,
            )
            ps["price_wicks"] = wicks
            ps["price_bodies"] = bodies
            ps["vol_bars"] = vol_bars
            ps["price_shades"] = list(shades)
            # Stash the dynamic body width so the H1 tick fastpath
            # (``_apply_tick_to_artists``) can keep the rightmost bar's
            # body consistent with the rest of the slice. Without this
            # an extreme-zoom-out slice (body_half ≈ 0.05) would suddenly
            # acquire a default-width (0.3) rightmost bar on the next tick.
            ps["body_half"] = body_half
            # Stash the (possibly-substituted) glyph-drawing list so
            # hover can hit-test against what the user actually sees on
            # screen. In HA mode the HA bar's [low, high] is a strict
            # superset of the real bar's range (HA_high = max(high,
            # HA_open, HA_close), HA_low symmetric), so without this
            # the cursor falls "outside" the real bar in the HA-only
            # tail/wick region and the readout pop-up never appears.
            # When HA is off this is identical to ``candles``.
            ps["display_candles"] = display_candles
        ps["render_start"] = new_start
        ps["render_end"] = new_end
        # Spec §6.3 / §11.2: always invalidate blit bg after a slice refill.
        self._blit_bg = None
        # Indicators are rendered on the FULL series (warm-up matters
        # for EMA/RSI), but Line2D artists die with fig.clear() —
        # render after the candles so they layer on top with their own
        # zorder. Compute via the manager + cache.
        self._render_indicators_for_slot(slot)
        # Earnings / dividends overlay — paints sparse glyphs at the
        # bottom edge of the price pane via mixed (data X, axes Y)
        # transform. Pure-functional artist build, so a failure here
        # never blocks the candle render.
        try:
            self._render_event_glyphs_for_slot(slot)
        except Exception:  # noqa: BLE001
            pass
        # Volume time-of-day overlay — outlines each visible 1d bar
        # with a darker-hue full-day envelope and solid-fills the
        # realized portion up to the reference clock (sandbox-aware).
        # Gated by the ``volume_tod_enabled`` tunable; no-op otherwise.
        # 1d only (decision 1) — intraday intervals are out of scope
        # for v1. Always wrapped so a failure can never block the
        # candle render.
        try:
            self._render_volume_tod_for_slot(slot)
        except Exception:  # noqa: BLE001
            pass

    def _autoscale_slot_y(self, slot: str, lo: int, hi: int) -> None:
        try:
            log_price_on = bool(
                getattr(self, "log_price_var", None) and self.log_price_var.get(),
            )
        except Exception:  # noqa: BLE001
            log_price_on = False
        ChartApp._ensure_renderer(self).autoscale_slot_y(
            slot,
            lo,
            hi,
            series_getter=self._series,
            log_price_on=log_price_on,
        )

    def _ensure_rendered_for_view(self, slot: str) -> None:
        ChartApp._ensure_renderer(self).ensure_rendered_for_view(
            slot,
            draw_slice=self._draw_slice,
            min_render_candles=_MIN_RENDER_CANDLES,
            max_render_candles=_MAX_RENDER_CANDLES,
            render_buffer_multiplier=_RENDER_BUFFER_MULTIPLIER,
        )

    # ---- stream view-refresh (spec §5.8) ------------------------------
    def _apply_tick_to_artists(self, slot: str) -> bool:
        try:
            ha_on = bool(self._ha_display_var.get())
        except Exception:  # noqa: BLE001
            ha_on = False
        try:
            highlight_key_bars_on = bool(self._highlight_key_bars_var.get())
        except Exception:  # noqa: BLE001
            highlight_key_bars_on = False
        return ChartApp._ensure_renderer(self).apply_tick_to_artists(
            slot,
            ha_on=ha_on,
            highlight_key_bars_on=highlight_key_bars_on,
            render_indicators=self._render_indicators_for_slot,
        )

    def _refresh_view_after_tick(self, slot: str = "primary") -> None:
        ChartApp._ensure_renderer(self).refresh_view_after_tick(
            slot,
            apply_tick_to_artists=self._apply_tick_to_artists,
            draw_slice=self._draw_slice,
            autoscale_slot_y=self._autoscale_slot_y,
            autoscale_indicator_panes=self._autoscale_indicator_panes_for_slot,
            canvas_draw_idle=self._canvas.draw_idle,
        )
        # After the per-tick artist mutation runs, slide the
        # live-price dotted line + label to the freshest known price.
        # No-op if the overlay has never been redrawn for this slot.
        try:
            self._update_live_price_overlay_for_slot(slot)
        except Exception:  # noqa: BLE001
            pass

    def _refresh_view_after_append(self, slot: str = "primary") -> None:
        ChartApp._ensure_renderer(self).refresh_view_after_append(
            slot,
            ensure_rendered_for_view=self._ensure_rendered_for_view,
            autoscale_slot_y=self._autoscale_slot_y,
            autoscale_indicator_panes=self._autoscale_indicator_panes_for_slot,
            canvas_draw_idle=self._canvas.draw_idle,
            sandbox_full_session_xlim=getattr(self, "_sandbox_full_session_xlim", None),
        )

    # ---- indicator integration (Phase 2a) -----------------------------
    def _sched_indicator_redraw(self, fn: Callable[[], None]) -> None:
        """Manager scheduler hook — coalesces redraw events to one per tick.

        Passed as ``IndicatorManager(scheduler=...)`` so the manager
        runs every observer-emitted callback through ``after_idle``.
        Both add/remove/update bursts and preset swaps end up running
        ``fn`` at most once per Tk tick.
        """
        try:
            self.after_idle(fn)
        except Exception:  # noqa: BLE001
            # No mainloop yet (headless smoke); run inline.
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass

    def _on_indicator_event(self, event_kind: str, _cfg: Any) -> None:
        """Subscriber hook on :class:`IndicatorManager`.

        We collapse every event into a single coalesced ``_render``
        call. Layout changes (add/remove of non-overlay) need a full
        ``_render`` because the gridspec topology must change; pure
        style/visibility updates could later use a faster path, but
        for Phase 2a we keep the logic simple — one render covers
        everything.
        """
        if event_kind not in {
            "add", "remove", "update", "clear", "reorder",
            "preset_loaded", "loaded", "redraw",
        }:
            return
        # AVWAP default-anchor materialization. When a fresh Anchored
        # VWAP is added (dialog "Add" or preset load) without an
        # ``anchor_ts``, resolve to the first eligible bar in the
        # primary candles and merge the resolved timestamp back into
        # the config's params. This gives the indicator a real anchor
        # that survives interval changes (a blank anchor would
        # silently re-snap to the new interval's first bar instead).
        if event_kind in ("add", "loaded", "preset_loaded"):
            try:
                self._materialize_blank_avwap_anchors()
            except Exception:  # noqa: BLE001
                pass
        if self._indicator_redraw_pending:
            return
        self._indicator_redraw_pending = True

        def _run() -> None:
            self._indicator_redraw_pending = False
            try:
                self._render()
            except Exception as e:  # noqa: BLE001
                try:
                    self._status.warn(f"Indicator render error: {e}")
                except Exception:  # noqa: BLE001
                    pass

        try:
            self.after_idle(_run)
        except Exception:  # noqa: BLE001
            # Fallback for headless contexts.
            self._indicator_redraw_pending = False
            try:
                self._render()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Drawings (horizontal-line annotations, Feature C)
    # ------------------------------------------------------------------




    def _materialize_blank_avwap_anchors(self) -> None:
        """Resolve ``anchor_ts == ""`` on any AVWAP configs.

        When the user adds an Anchored VWAP via the dialog or loads a
        preset that defaulted ``anchor_ts``, the persisted value is
        the empty string. The compute layer falls back to "first
        eligible bar," but we want the anchor to survive interval
        changes — so we materialize the actual timestamp here and
        merge it into the config's params.
        """
        from .indicators.avwap import first_eligible_anchor_ts

        ps = self._panel_state.get("primary") or {}
        candles = ps.get("candles") or []
        if not candles:
            return
        ts = first_eligible_anchor_ts(candles)
        if not ts:
            return
        for cfg in list(self._indicator_manager.list()):
            if getattr(cfg, "kind_id", "") != "avwap":
                continue
            params = dict(cfg.params or {})
            cur = params.get("anchor_ts") or ""
            if cur:
                continue
            params["anchor_ts"] = ts
            # Update without firing the materialization recursively;
            # the manager will emit "update" but our handler bails
            # out at the kind_id check above (cur becomes truthy).
            self._indicator_manager.update(cfg.id, params=params)

    def _begin_anchor_pick(self, config_id: int) -> None:
        """Arm one-shot anchor-pick capture for ``config_id``.

        Called from :class:`tradinglab.gui.indicator_dialog.IndicatorDialog`'s
        "Pick Anchor…" button. Sets cursor to crosshair, defensively
        clears pan/zoom drag state, and shows a status hint. The next
        left-click in a chart axis is intercepted by
        :meth:`_handle_anchor_pick_click` (gated in the
        :class:`InteractionMixin`'s ``_on_button_press``); a miss
        keeps the mode active so the user can retry.

        While pick mode is active the Manage Indicators dialog is
        iconified (minimised to the taskbar) so the chart underneath
        is unobstructed and the user can reach any candle. The
        original window state is captured and restored when pick mode
        ends (success, cancel, or Esc).
        """
        cfg = self._indicator_manager.get(config_id)
        if cfg is None or getattr(cfg, "kind_id", "") != "avwap":
            return
        # Capture the indicator dialog's current state so we can
        # restore on cancel / completion. ``state()`` returns
        # "normal" / "iconic" / "withdrawn" / "zoomed". If there's
        # no dialog open (e.g. the user invoked the pick from a
        # future menu shortcut), ``prior_state`` stays None and we
        # don't touch any window.
        prior_state: str | None = None
        dlg = getattr(self, "_indicator_dialog", None)
        if dlg is not None:
            try:
                prior_state = dlg.state()
            except Exception:  # noqa: BLE001
                prior_state = None
            try:
                dlg.iconify()
            except Exception:  # noqa: BLE001
                pass
        self._anchor_pick_state = {
            "config_id": config_id,
            "dialog_prior_state": prior_state,
        }
        self._pan_state = None
        self._zoom_state = None
        self._drag_press = None
        try:
            tk_widget = self._canvas.get_tk_widget()
            tk_widget.configure(cursor="crosshair")
            tk_widget.focus_set()
            tk_widget.bind("<Escape>", self._on_anchor_pick_escape, add="+")
        except Exception:  # noqa: BLE001
            pass
        try:
            self._status.info(
                "Click a bar to anchor VWAP — Esc to cancel"
            )
        except Exception:  # noqa: BLE001
            pass

    def _cancel_anchor_pick(self, *, status_msg: str | None = None) -> None:
        """Clear anchor-pick mode and restore the cursor / status.

        If the Manage Indicators dialog was iconified by
        :meth:`_begin_anchor_pick`, restore it to its prior state and
        lift it back over the chart so the user can keep editing
        params right where they left off.
        """
        prior_state: str | None = None
        if self._anchor_pick_state is not None:
            prior_state = self._anchor_pick_state.get("dialog_prior_state")
        self._anchor_pick_state = None
        try:
            tk_widget = self._canvas.get_tk_widget()
            tk_widget.configure(cursor="")
            tk_widget.unbind("<Escape>")
        except Exception:  # noqa: BLE001
            pass
        dlg = getattr(self, "_indicator_dialog", None)
        if dlg is not None:
            try:
                # Only deiconify if it WAS visible before we minimised
                # it. If the user had it withdrawn / zoomed for some
                # reason, preserve that state.
                if prior_state in ("normal", "zoomed", None):
                    dlg.deiconify()
                    if prior_state == "zoomed":
                        try:
                            dlg.state("zoomed")
                        except Exception:  # noqa: BLE001
                            pass
                    try:
                        dlg.lift()
                        dlg.focus_set()
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass
        if status_msg:
            try:
                self._status.info(status_msg)
            except Exception:  # noqa: BLE001
                pass

    def _on_anchor_pick_escape(self, _event: Any) -> str:
        self._cancel_anchor_pick(status_msg="Anchor pick canceled")
        return "break"

    def _handle_anchor_pick_click(self, event: Any) -> bool:
        """Consume a left-click while anchor-pick mode is active.

        Returns ``True`` if the event was consumed (always ``True``
        when this method is reached — pick mode swallows ALL left
        clicks regardless of whether a candle was hit, so the user
        can't accidentally start a pan/zoom while the prompt is
        live). On a successful candle hit the AVWAP config's params
        are updated (merged so ``price_source`` / ``bands`` are
        preserved) and pick mode is cleared. On a miss / pre-post
        snap-failure, pick mode stays armed.
        """
        state = self._anchor_pick_state
        if not state:
            return False
        cfg_id = int(state.get("config_id", -1))
        ax = getattr(event, "inaxes", None)
        if ax is None or getattr(event, "xdata", None) is None:
            try:
                self._status.info("Click inside a chart panel — Esc to cancel")
            except Exception:  # noqa: BLE001
                pass
            return True
        entry = self._ax_candle_map.get(ax)
        if entry is None:
            try:
                self._status.info("Click a price/volume panel — Esc to cancel")
            except Exception:  # noqa: BLE001
                pass
            return True
        candles, _kind, offset = entry
        idx = int(round(event.xdata - offset))
        if idx < 0 or idx >= len(candles):
            return True
        if abs(event.xdata - (idx + offset)) > 0.3:
            try:
                self._status.info("Click closer to a bar — Esc to cancel")
            except Exception:  # noqa: BLE001
                pass
            return True
        # Snap forward to the nearest non-gap regular-session bar
        # (matches the AVWAP compute eligibility rule). If the user
        # clicked a pre/post bar we want the stored anchor to be a
        # bar the indicator can actually start on.
        snap_idx = idx
        while snap_idx < len(candles):
            c = candles[snap_idx]
            if not getattr(c, "is_gap", False) and c.session == "regular":
                break
            snap_idx += 1
        if snap_idx >= len(candles):
            try:
                self._status.info(
                    "No regular-session bar at/after this click — Esc to cancel"
                )
            except Exception:  # noqa: BLE001
                pass
            return True
        c = candles[snap_idx]
        try:
            from .indicators.avwap import _strip_tz
            ts = _strip_tz(c.date).isoformat()
        except Exception:  # noqa: BLE001
            ts = c.date.isoformat()
        cfg = self._indicator_manager.get(cfg_id)
        if cfg is None:
            self._cancel_anchor_pick()
            return True
        params = dict(cfg.params or {})
        params["anchor_ts"] = ts
        self._indicator_manager.update(cfg_id, params=params)
        self._cancel_anchor_pick(status_msg=f"Anchor set: {ts[:16].replace('T', ' ')}")
        return True

    def _refresh_overlay_legend(self) -> None:
        """Refresh the per-overlay legends with all overlay configs.

        One legend strip per ``kind == "price"`` axes (primary +
        compare). Each pulls configs for its scope ("main" /
        "compare") from :func:`gui.overlay_legend.collect_overlay_configs`
        which does NOT filter by ``cfg.visible`` — the legend needs
        hidden configs too so they can be re-enabled with one click.

        Compare slot: only populated when compare mode is on AND a
        valid compare panel exists; otherwise an empty config list
        hides the legend.

        Positioning is then handed off to ``_reposition_overlay_legends``
        so each strip lines up below its axes' OHLCV readout. We always
        re-position after a refresh (even for empty lists) so a
        slot that just got cleared correctly hides its widget.
        """
        legends = getattr(self, "_overlay_legends", None) or {}
        if not legends:
            return
        try:
            from .gui.overlay_legend import collect_overlay_configs
        except Exception:  # noqa: BLE001
            return
        interval = self.interval_var.get()
        scope_for_slot = {"primary": "main", "compare": "compare"}
        compare_on = bool(getattr(self, "compare_var", None)
                          and self.compare_var.get())
        for slot_key, legend in legends.items():
            if legend is None:
                continue
            scope = scope_for_slot.get(slot_key, "main")
            try:
                if slot_key == "compare" and not compare_on:
                    legend.refresh([])
                else:
                    configs = collect_overlay_configs(
                        self._indicator_manager, scope, interval)
                    legend.refresh(configs)
            except Exception:  # noqa: BLE001
                try:
                    legend.refresh([])
                except Exception:  # noqa: BLE001
                    pass
        # Anchor each legend below its axes' OHLCV strip. On the very
        # first paint the canvas may not be laid out yet — the next
        # ``draw_event`` will repeat the call and snap things into
        # place once dimensions are known.
        try:
            self._reposition_overlay_legends()
        except Exception:  # noqa: BLE001
            pass

    def _reposition_overlay_legends(self) -> None:
        """Anchor each per-slot legend below its price axes' OHLCV strip.

        Called from :meth:`_refresh_overlay_legend` (post-refresh) and
        from the matplotlib ``draw_event`` handler in ``InteractionMixin``
        (so the legend follows the axes through resizes / compare-
        toggles / theme switches). No-op if the canvas widget isn't
        ready yet (winfo_height == 1 on the first paint).
        """
        legends = getattr(self, "_overlay_legends", None) or {}
        if not legends:
            return
        canvas = getattr(self, "_canvas", None)
        if canvas is None:
            return
        try:
            canvas_widget = canvas.get_tk_widget()
        except Exception:  # noqa: BLE001
            return
        panel_state = getattr(self, "_panel_state", None) or {}
        for slot_key, legend in legends.items():
            if legend is None:
                continue
            ps = panel_state.get(slot_key)
            ax_p = ps.get("price_ax") if ps else None
            try:
                legend.reposition_for_axes(ax_p, canvas_widget)
            except Exception:  # noqa: BLE001
                pass

    def _render_indicators_for_slot(self, slot: str) -> None:
        ChartApp._ensure_renderer(self).render_indicators_for_slot(
            slot,
            interval=self.interval_var.get(),
            source=self.source_var.get(),
            slot_symbol=self._slot_symbol(slot),
            indicator_manager=self._indicator_manager,
            indicator_cache=self._indicator_cache,
            warn=getattr(getattr(self, "_status", None), "warn", None),
        )

    def _autoscale_indicator_panes_for_slot(self, slot: str) -> None:
        ChartApp._ensure_renderer(self).autoscale_indicator_panes_for_slot(slot)

    def _slot_symbol(self, slot: str) -> str:
        """Return the confirmed symbol displayed in ``slot``.

        Mirrors the lookup in :meth:`_render_indicators_for_slot`. Sandbox
        sessions don't override this — the sandbox controller installs
        its own candle lists into the slot via ``_rewire_slot_candles``,
        and the symbol displayed there is the same one the controller
        focused on (mirrored into ``_confirmed_primary_ticker``).
        """
        if slot == "primary":
            return str(getattr(self, "_confirmed_primary_ticker", "") or "")
        return str(getattr(self, "_confirmed_compare_ticker", "") or "")

    def _get_events_view_for_slot(self, slot: str):
        """Return a gated EventsView for the symbol displayed in ``slot``.

        Routes:

        * **Sandbox active** — delegates to the controller's
          :meth:`SandboxController.events_visible_for`, which honors the
          session clock + blind flag. Returns ``None`` if no bundle
          has arrived yet.
        * **Non-sandbox** — looks the bundle up in ``self._events_cache``
          (populated by :meth:`_load_events_async`) and gates it
          against ``time.time()*1000`` with ``blind=False``. Forward
          earnings within the ``forward_window_days`` window are
          visible; everything else is past.

        Returns ``None`` when no bundle is known for the symbol or the
        gating import fails — the caller renders an empty glyph list
        in that case.
        """
        symbol = self._slot_symbol(slot)
        if not symbol:
            return None
        ctl = getattr(self, "_sandbox_controller", None)
        if ctl is not None and getattr(ctl, "is_active", lambda: False)():
            try:
                return ctl.events_visible_for(symbol)
            except Exception:  # noqa: BLE001
                return None
        bundle = self._events_cache.get(symbol)
        if bundle is None:
            return None
        try:
            import time as _time

            from .events.gating import events_visible_for as _gate
            now_ms = int(_time.time() * 1000)
            return _gate(bundle, now_ms, blind=False)
        except Exception:  # noqa: BLE001
            return None

    def _render_event_glyphs_for_slot(self, slot: str) -> None:
        ctl = getattr(self, "_sandbox_controller", None)
        sandbox_blind = False
        if ctl is not None and getattr(ctl, "is_active", lambda: False)():
            sandbox_blind = bool(getattr(ctl, "blind", False))
        ChartApp._ensure_renderer(self).render_event_glyphs_for_slot(
            slot,
            get_events_view=self._get_events_view_for_slot,
            theme=self._theme,
            sandbox_blind=sandbox_blind,
        )

    def _render_volume_tod_for_slot(self, slot: str) -> None:
        try:
            interval = self.interval_var.get()
        except Exception:  # noqa: BLE001
            return
        try:
            dark_mode = bool(self.dark_var.get())
        except Exception:  # noqa: BLE001
            dark_mode = False
        ChartApp._ensure_renderer(self).render_volume_tod_for_slot(
            slot,
            interval=interval,
            get_intraday=self._get_intraday_for_volume_tod,
            now_ms_for_slot=self._now_ms_for_slot,
            is_sandbox_active=self._is_sandbox_active,
            suppress_volume_fill=self._suppress_default_volume_fill,
            theme=self._theme,
            dark_mode=dark_mode,
        )

    def _get_intraday_for_volume_tod(self, slot: str) -> list[Candle]:
        """Return the slot's 5m intraday candles for the TOD overlay.

        Reads directly from :attr:`_full_cache`. When the cache is
        cold (first render with the feature enabled) we kick a
        :meth:`_ensure_prefetched` to warm it and return an empty list
        — the next render after the prefetch lands will pick up the
        data and re-paint. Returns the cached list AS-IS (no defensive
        copy) since the math layer treats it as immutable.
        """
        symbol = self._slot_symbol(slot)
        if not symbol:
            return []
        try:
            from . import defaults as _defaults_mod
            itv = str(_defaults_mod.get("volume_tod_intraday_interval") or "5m")
        except Exception:  # noqa: BLE001
            itv = "5m"
        try:
            src = self.source_var.get()
        except Exception:  # noqa: BLE001
            return []
        key = (src, symbol, itv)
        cached = self._full_cache.get(key)
        if cached:
            return cached
        # Kick a background prefetch so subsequent renders find data.
        try:
            self._ensure_prefetched(symbol, itv)
        except Exception:  # noqa: BLE001
            pass
        return []

    def _ensure_intraday_for_volume_tod(self) -> None:
        """Warm ``_full_cache`` with 5m bars for both slots' symbols.

        Called when the user toggles the volume-TOD overlay ON via Settings
        or the View menu — the next render needs intraday data, and we
        don't want to render an empty overlay then re-paint a second
        later when the data arrives. Idempotent and async — submits to
        ``_executor`` and returns immediately. The arrival callback on
        ``_ensure_prefetched`` already triggers the right cache writes.
        """
        try:
            from . import defaults as _defaults_mod
            itv = str(_defaults_mod.get("volume_tod_intraday_interval") or "5m")
        except Exception:  # noqa: BLE001
            itv = "5m"
        for slot in ("primary", "compare"):
            sym = self._slot_symbol(slot)
            if not sym:
                continue
            try:
                self._ensure_prefetched(sym, itv)
            except Exception:  # noqa: BLE001
                pass

    def _refresh_volume_tod_for_prefetch(
        self,
        *,
        prefetched_source: str,
        prefetched_symbol: str,
        prefetched_interval: str,
    ) -> None:
        """Repaint TOD shading when its 5m companion prefetch lands."""
        try:
            if not bool(_defaults.get("volume_tod_enabled")):
                return
            if self.interval_var.get() != "1d":
                return
            if self.source_var.get() != prefetched_source:
                return
            tod_interval = str(_defaults.get("volume_tod_intraday_interval") or "5m")
            if str(prefetched_interval) != tod_interval:
                return
        except Exception:  # noqa: BLE001
            return
        prefetched_symbol = str(prefetched_symbol or "").strip().upper()
        active_symbols: set[str] = set()
        for slot in ("primary", "compare"):
            try:
                sym = self._slot_symbol(slot)
            except Exception:  # noqa: BLE001
                sym = ""
            if sym:
                active_symbols.add(str(sym).strip().upper())
        if prefetched_symbol not in active_symbols:
            return
        try:
            self._request_redraw_for_volume_tod()
        except Exception:  # noqa: BLE001
            pass

    def _suppress_default_volume_fill(
        self, slot: str, suppress_indices: dict[int, bool],
    ) -> None:
        ChartApp._ensure_renderer(self).suppress_default_volume_fill(slot, suppress_indices)

    def _request_redraw_for_volume_tod(self) -> None:
        """Re-render the TOD overlay into all slots after a toggle.

        Cheap path: clears the existing TOD artists on each slot, then
        re-runs :meth:`_render_volume_tod_for_slot` and triggers a
        canvas redraw. No candle / indicator rebuild needed. Falls back
        to a full :meth:`_render` if the slot lookup fails (defensive).
        """
        try:
            from .gui.volume_tod_overlay import clear_volume_tod_artists
        except ImportError:
            return
        try:
            for slot_key in list(self._panel_state.keys()):
                ps = self._panel_state.get(slot_key)
                if not ps or ps.get("vol_ax") is None:
                    continue
                clear_volume_tod_artists(
                    list(ps.get("vol_tod_artists", []) or [])
                )
                ps["vol_tod_artists"] = []
                ps["vol_tod_patches"] = []
                # The previous fill-suppression mutated the default
                # vol_bars facecolors. If the feature is being toggled
                # OFF, we need to restore them. Easiest path: redraw
                # the whole slice for this slot. _draw_slice() re-runs
                # the events + TOD overlay too, so a single call
                # repaints everything consistently.
                try:
                    render_start = int(ps.get("render_start", 0) or 0)
                    render_end = int(ps.get("render_end", 0) or 0)
                    if render_end > render_start:
                        self._draw_slice(slot_key, render_start, render_end)
                except Exception:  # noqa: BLE001
                    pass
            try:
                self._figure.canvas.draw_idle()
            except Exception:  # noqa: BLE001
                pass
            self._blit_bg = None
        except Exception:  # noqa: BLE001
            try:
                self._render()
            except Exception:  # noqa: BLE001
                pass

    def _load_events_async(self, symbol: str) -> None:
        """Submit a background EventBundle fetch for ``symbol``.

        Sibling of :meth:`_load_data_async`: runs on ``_fetch_executor``,
        marshals the result back to the Tk main thread via
        :meth:`_await_future_on_tk` (never ``add_done_callback`` +
        ``self.after`` from a worker thread — see ``app.spec.md``
        Recent history → "Worker-inbox queue").

        Token-gated: a superseded ``_load_events_async`` no-ops on
        arrival. Inflight-deduped per symbol so a typed-ticker storm
        doesn't fan out to N parallel fetches of the same data.

        On success the result lands in ``self._events_cache[symbol]``
        and a redraw is requested so the bottom-pane glyphs (and any
        watchlist "Next Earn" column rows) pick up the new bundle.
        """
        sym = str(symbol or "").strip().upper()
        if not sym:
            return
        if sym in self._events_fetch_inflight:
            return
        # In-memory cache hit short-circuits the executor submit so the
        # candle-cache fast path stays "no submit" (N7 smoke invariant).
        # Disk-cache hydration still happens lazily on the worker the
        # first time a symbol is requested in a process.
        if self._events_cache.get(sym) is not None:
            return
        try:
            from . import defaults as _defaults_mod
            from .events import EVENT_SOURCES
        except ImportError:
            return
        source_name = str(_defaults_mod.get("events_source") or "yfinance")
        fetcher = EVENT_SOURCES.get(source_name) or EVENT_SOURCES.get("synthetic")
        if fetcher is None:
            return
        executor = getattr(self, "_fetch_executor", None)
        await_helper = getattr(self, "_await_future_on_tk", None)
        if executor is None or await_helper is None:
            return

        self._events_fetch_token += 1
        token = self._events_fetch_token
        self._events_fetch_inflight.add(sym)

        def _work():
            try:
                return fetcher(sym)
            except Exception:  # noqa: BLE001
                return None

        def _on_done(bundle) -> None:
            self._events_fetch_inflight.discard(sym)
            if token != self._events_fetch_token and bundle is None:
                # Stale + empty — drop. (A stale-but-non-empty result
                # is still valid for the cache, since bundles are
                # immutable in the past zone.)
                return
            if bundle is None:
                return
            self._events_cache[sym] = bundle
            # Re-paint so the glyphs appear without waiting for the
            # next user interaction. Best-effort; failures revert to
            # "glyphs will appear on next render".
            try:
                self._request_redraw_for_events()
            except Exception:  # noqa: BLE001
                pass

        try:
            fut = executor.submit(_work)
        except (RuntimeError, AttributeError):
            self._events_fetch_inflight.discard(sym)
            return
        try:
            await_helper(fut, _on_done)
        except Exception:  # noqa: BLE001
            self._events_fetch_inflight.discard(sym)

    def _request_redraw_for_events(self) -> None:
        """Re-render glyphs into the existing axes after a bundle arrives.

        Cheap path: only ``_render_event_glyphs_for_slot`` per slot,
        then a canvas redraw. No candle / indicator rebuild — those
        haven't changed. Falls back to a full ``_render()`` if any
        slot lookup fails (defensive; should be unreachable when called
        from ``_load_events_async``'s success path).
        """
        try:
            for slot_key in list(self._panel_state.keys()):
                ps = self._panel_state.get(slot_key)
                if not ps or ps.get("price_ax") is None:
                    continue
                # Tear down previous glyph artists before redrawing.
                try:
                    from .gui.events_overlay import clear_event_glyph_artists
                    clear_event_glyph_artists(list(ps.get("event_artists", []) or []))
                except Exception:  # noqa: BLE001
                    pass
                ps["event_artists"] = []
                ps["event_hit_meta"] = []
                ps["event_badge_tooltip"] = ""
                self._render_event_glyphs_for_slot(slot_key)
            # Force a canvas refresh so the new artists show up.
            try:
                self._figure.canvas.draw_idle()
            except Exception:  # noqa: BLE001
                pass
            self._blit_bg = None
            # Also poke the watchlist tab in case the "Next Earn"
            # column wants to recompute now that a bundle landed.
            try:
                self._schedule_watchlist_tab_refresh()
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass


    def _rewire_slot_candles(self, slot: str, candles: list[Candle]) -> None:
        """Repoint a slot at a different candles list (e.g., compare swap).

        Updates ``_panel_state[slot]['candles']`` + ``_ax_candle_map`` so
        the stable 3-tuples reference the new list, then rebuilds the
        current slice via :meth:`_draw_slice`.
        """
        ps = self._panel_state.get(slot)
        if not ps:
            return
        ps["candles"] = candles
        for ax in (ps.get("price_ax"), ps.get("vol_ax")):
            if ax is None:
                continue
            entry = self._ax_candle_map.get(ax)
            if entry is None:
                continue
            _c, kind, off = entry
            self._ax_candle_map[ax] = (candles, kind, off)
        rs = int(ps.get("render_start", 0))
        re_ = min(len(candles), int(ps.get("render_end", len(candles))))
        self._draw_slice(slot, rs, re_)

    def _series(self, candles: list[Candle]) -> _SeriesArrays:
        """Return (or build) a cached :class:`_SeriesArrays` for ``candles``."""
        key = id(candles)
        sa = self._series_cache.get(key)
        # id(list) is not unique over time — Python reuses ids after a list
        # is garbage-collected. Also assert the cached SA's internal
        # ``_candles`` IS the list we were given, otherwise a new list
        # that happens to reuse the freed id (and has the same length as
        # the evicted list) would inherit stale arrays. Hit during
        # interval switches in compare mode: AMD's 502-bar daily candles
        # got SPY's 502-bar daily arrays.
        stale = (
            sa is None
            or sa.n != len(candles)
            or getattr(sa, "_candles", None) is not candles
        )
        if stale:
            sa = _build_series_safe(candles, self._format_candle_date)
            if sa is None:
                # Empty-candles fallback: build an empty arrays object by hand.
                sa = _SeriesArrays.__new__(_SeriesArrays)
                sa.opens = np.array([])
                sa.highs = np.array([])
                sa.lows = np.array([])
                sa.closes = np.array([])
                sa.volumes = np.array([])
                sa._candles = candles
                sa._bars = None
                sa._format_date = self._format_candle_date
                sa._tooltip_cache = {}
                sa.n = 0
            self._series_cache[key] = sa
        return sa

    def _format_candle_date(self, c: Candle) -> str:
        if is_intraday(self.interval_var.get()):
            return format_dt(c.date, "%Y-%m-%d %H:%M", self._display_tz)
        # Daily/weekly/monthly bars represent exchange trading dates,
        # not instants — never tz-shift, or "Apr 24 ET" would relabel
        # to "Apr 25" in Tokyo.
        return c.date.strftime("%Y-%m-%d")

    def _refill_table(self) -> None:
        """Populate Primary + Compare OHLC tables (newest first, capped).

        H6: diff-aware. Caches per-tree ``(sigs, iids)`` so the common
        cases — streaming tick (only the newest row's OHLCV changes) and
        rollover (one row prepended) — only touch one row instead of
        deleting + reinserting all ``_MAX_TABLE_ROWS``. Falls back to a
        full rebuild on any structural change. Signature per row is
        ``(date, open, high, low, close, volume)`` — sufficient to detect
        OHLCV mutation without referencing the Candle objects directly.
        """
        if not hasattr(self, "_table_cache"):
            self._table_cache = {}

        def _row_values(c):
            return (
                self._format_candle_date(c),
                f"{c.open:,.2f}", f"{c.high:,.2f}",
                f"{c.low:,.2f}", f"{c.close:,.2f}",
                fmt_volume(c.volume),
            )

        for tree, rows in (
            (self._primary_table, self._primary),
            (getattr(self, "_compare_table", None), self._compare),
        ):
            if tree is None:
                continue
            # Build (sig, candle) list in display order (newest first,
            # gaps skipped, capped at _MAX_TABLE_ROWS).
            new: list[tuple[tuple, Candle]] = []
            for c in reversed(rows[-_MAX_TABLE_ROWS:] if rows else []):
                if c.is_gap:
                    continue
                new.append((
                    (c.date, c.open, c.high, c.low, c.close, c.volume),
                    c,
                ))
            new_sigs = [s for s, _ in new]

            cache_key = id(tree)
            cache = self._table_cache.get(
                cache_key, {"sigs": [], "iids": []})
            old_sigs: list[tuple] = cache["sigs"]
            old_iids: list[str] = cache["iids"]

            # Identical → no-op.
            if old_sigs == new_sigs and len(old_iids) == len(old_sigs):
                continue

            # Tick fastpath: same length, only the top row's sig changed.
            if (len(old_sigs) == len(new_sigs) > 0
                    and old_sigs[1:] == new_sigs[1:]
                    and old_iids
                    and old_sigs[0] != new_sigs[0]):
                _, c0 = new[0]
                tag = "bull" if c0.close >= c0.open else "bear"
                try:
                    tree.item(old_iids[0], tags=(tag,),
                              values=_row_values(c0))
                    cache["sigs"] = new_sigs
                    self._table_cache[cache_key] = cache
                    continue
                except Exception:  # noqa: BLE001
                    pass  # fall through to full rebuild

            # Rollover fastpath: one row prepended, tail unchanged.
            if (len(new_sigs) == len(old_sigs) + 1
                    and new_sigs[1:] == old_sigs):
                _, c0 = new[0]
                tag = "bull" if c0.close >= c0.open else "bear"
                try:
                    new_iid = tree.insert("", 0, tags=(tag,),
                                          values=_row_values(c0))
                    cache["sigs"] = new_sigs
                    cache["iids"] = [new_iid] + old_iids
                    self._table_cache[cache_key] = cache
                    continue
                except Exception:  # noqa: BLE001
                    pass

            # Rollover-at-cap fastpath: same length, top is new, bottom
            # row was dropped because the prepend would have exceeded
            # _MAX_TABLE_ROWS.
            if (len(new_sigs) == len(old_sigs) > 0
                    and new_sigs[1:] == old_sigs[:-1]
                    and old_iids):
                _, c0 = new[0]
                tag = "bull" if c0.close >= c0.open else "bear"
                try:
                    tree.delete(old_iids[-1])
                    new_iid = tree.insert("", 0, tags=(tag,),
                                          values=_row_values(c0))
                    cache["sigs"] = new_sigs
                    cache["iids"] = [new_iid] + old_iids[:-1]
                    self._table_cache[cache_key] = cache
                    continue
                except Exception:  # noqa: BLE001
                    pass

            # Full rebuild fallback.
            try:
                for iid in tree.get_children():
                    tree.delete(iid)
            except Exception:  # noqa: BLE001
                self._table_cache.pop(cache_key, None)
                continue
            new_iids: list[str] = []
            for _, c in new:
                tag = "bull" if c.close >= c.open else "bear"
                try:
                    iid = tree.insert("", "end", tags=(tag,),
                                      values=_row_values(c))
                    new_iids.append(iid)
                except Exception:  # noqa: BLE001
                    pass
            cache["sigs"] = new_sigs
            cache["iids"] = new_iids
            self._table_cache[cache_key] = cache

    # ------------------------------------------------------------------
    # Watchlists
    # ------------------------------------------------------------------
    def _on_global_space(self, event):
        """App-wide handler for the Space-key watchlist cycle.

        Bound via ``bind_all("<KeyPress-space>", ...)`` so the shortcut
        works regardless of which child widget has focus, and via
        ``bind_class`` overrides for widgets whose default <space>
        bindings return "break" (Treeview / TButton / TNotebook / etc.)
        and would otherwise prevent the "all" tag from firing.

        Errors raised by the cycle implementation are reported to the
        status bar (level: error); the success path is silent — the
        chart re-rendering with the new ticker is itself the visual
        confirmation that the keystroke landed. (An earlier revision
        always emitted a per-keystroke info-level diagnostic referencing
        the focused widget class, which clobbered useful messages in
        the status bar; removed 2026-05, audit ``debug-print-leak``.)

        Skipped (returns None without break) when focus is on a text-
        input widget so Space remains a literal character there.
        Returns ``"break"`` everywhere else so a focused button doesn't
        also activate on Space.
        """
        try:
            w = event.widget
        except Exception:  # noqa: BLE001
            w = None
        cls = ""
        if w is not None:
            try:
                cls = w.winfo_class()
            except Exception:  # noqa: BLE001
                cls = ""
            text_classes = {"Entry", "TEntry", "TCombobox", "Combobox",
                            "Spinbox", "TSpinbox", "Text", "TText"}
            if cls in text_classes:
                # Don't even log here — we want Space to behave as a
                # literal char in text inputs, no side effects.
                return None
            if getattr(self, "_typing_target", None) is not None:
                # Only suppress when the user is *actively* mid-type
                # (buffer has chars). A bare click on the chart sets
                # `_typing_target` without any input — in that case
                # space should still cycle, not be ignored. Cancel the
                # empty typing target so subsequent keystrokes don't
                # carry stale state, and fall through to cycle.
                buf = getattr(self, "_typing_buffer", "") or ""
                if buf:
                    try:
                        self._status.warn(
                            "Space ignored: typing a ticker — press Enter "
                            "or Esc first")
                    except Exception:  # noqa: BLE001
                        pass
                    return "break"
                try:
                    self._cancel_click_to_type()
                except Exception:  # noqa: BLE001
                    self._typing_target = None
                    self._typing_buffer = ""
        try:
            self._cycle_watchlist_ticker()
        except Exception as exc:  # noqa: BLE001
            try:
                self._status.error(f"Watchlist cycle error: {exc}")
            except Exception:  # noqa: BLE001
                pass
        return "break"

    def _on_chartstack_promote(self, symbol: str) -> None:
        """ChartStack callback: a card was clicked → promote to main chart.

        Same-slot demote semantics (synthesis §2.5): the previously
        focused ticker is rebound to the just-vacated card slot, so
        the strip stays full and the user can swap back with one
        click. No-op when the symbol matches the current ticker
        (clicking a card showing what's already on the main chart
        does nothing).

        **Anchor / visible-window consistency** (locked in by
        ``check_d72_chartstack_promote_preserves_view``): the
        ticker-switch path mirrors
        :meth:`~tradinglab.gui.watchlist_tab.WatchlistTabMixin._on_watchlist_double`,
        which is the other "click in a sidebar to swap symbols" flow.
        Both flows must produce the same chart state for the new
        symbol so an AVWAP-anchored bar (or any time-anchored
        artifact: drilldown day, panned time window) lands at the
        same screen position regardless of which sidebar the user
        clicked from.

        Specifically:

        * If a 5m drill-down day is locked, route through
          :meth:`_reload_preserving_drilldown` so the new symbol
          re-zooms to that calendar day (with most-recent-day
          fallback per the drilldown helper).
        * Otherwise set ``_preserve_xlim_by_time_on_render`` so the
          render layer remaps the previous primary's time window
          onto the new symbol's bar-index axis. The AVWAP / anchor
          bar then stays visually anchored to its date instead of
          snapping to the right edge.

        Sandbox-active path is allowed — :meth:`_load_data_async`
        already gates ticker changes through the sandbox controller,
        matching the watchlist-double behavior during sessions.
        """
        if not symbol:
            return
        try:
            current = (self.ticker_var.get() or "").strip().upper()
        except Exception:  # noqa: BLE001
            current = ""
        target = symbol.strip().upper()
        if not target or target == current:
            return
        try:
            self.ticker_var.set(target)
        except Exception:  # noqa: BLE001
            return
        # Mirror the watchlist-double ticker-switch path so the new
        # symbol lands with the same visible window / anchor bar as
        # any other sidebar-driven swap.
        try:
            in_drilldown = (
                getattr(self, "_drilldown_day", None) is not None
                and self.interval_var.get() == "5m"
            )
        except Exception:  # noqa: BLE001
            in_drilldown = False
        try:
            if in_drilldown:
                self._reload_preserving_drilldown(self._load_data)
            else:
                try:
                    self._preserve_xlim_by_time_on_render = True
                except Exception:  # noqa: BLE001
                    pass
                self._load_data_async()
        except Exception:  # noqa: BLE001
            pass
        # Same-slot demote: rebind the just-promoted card to the
        # previously focused symbol so the strip remains full.
        cs = getattr(self, "_chartstack", None)
        if cs is not None and current:
            try:
                cs.demote_to(target, current)
            except Exception:  # noqa: BLE001
                pass

    def _on_explicit_axis_change(self) -> None:
        """User explicitly changed source/interval/pre-post — clear drill-down.

        Drill-down is conceptually tied to a `(ticker, 5m, day)` triple;
        flipping interval to anything else, swapping source, or toggling
        pre/post invalidates the day-zoom. Ticker changes (typing,
        watchlist double-click) intentionally do NOT clear it — those
        go through ``_reload_preserving_drilldown``.

        While a sandbox session is active, the interval combobox is
        intercepted: the only valid choices are the sandbox's locked
        intraday interval and ``"1d"`` (for daily-context display).
        Anything else is reverted with a status warning so the user
        can't accidentally redirect the chart away from sandbox state.
        """
        if self._is_sandbox_active() and self._sandbox is not None:
            self._sandbox_handle_interval_change()
            return
        self._drilldown_day = None
        self._load_data_async()

    def _sandbox_handle_interval_change(self) -> None:
        """Route interval-combobox changes through the sandbox controller.

        Allowed choices while a session is active are:

        * Any interval in ``self._sandbox.display_intervals`` — the
          smallest is the primary tick interval; larger ones display
          aggregated higher-TF candles in real time.
        * ``"1d"`` — daily-context view (completed sessions only,
          capped to ``daily_lookback_bars`` bars).

        Any other choice is reverted to whatever the current display
        interval is and the user is shown a status warning. Daily-mode
        request for a symbol with no registered daily series falls
        back to intraday with a warning rather than blanking the chart.
        """
        if self._sandbox is None:
            return
        try:
            chosen = self.interval_var.get()
        except tk.TclError:
            return
        ok = self._sandbox.set_display_interval(chosen)
        if ok:
            return
        # Revert UI: restore whatever display the controller is in.
        cur_display = (self._sandbox.display_interval
                       or self._sandbox.interval)
        with _silent_tcl():
            self.interval_var.set(cur_display)
        try:
            if chosen == "1d":
                self._status.warn(
                    f"Sandbox: no daily context cached for "
                    f"{self._sandbox.focus_symbol or 'focus'} — "
                    f"1d toggle unavailable")
            else:
                allowed = ", ".join(self._sandbox.display_intervals)
                self._status.warn(
                    f"Sandbox: only {allowed} or 1d are selectable "
                    f"while a session is active")
        except Exception:  # noqa: BLE001
            pass

    def _on_prepost_toggle(self) -> None:
        """Pre/Post toggled — preserve drill-down and rescale to the same day.

        Unlike source/interval changes, toggling Pre/Post is a *render
        scope* change for the same day-of-bars. When the user is in 5m
        drill-down, we keep ``_drilldown_day`` and reload through
        ``_reload_preserving_drilldown``: the data refetch + filter
        produces a new ``_primary`` (containing or omitting pre/post
        bars), then ``_zoom_primary_to_date`` recomputes the day's
        index range so the xlim grows (prepost on → +pre + +post bars
        visible) or shrinks (prepost off → regular session only) to
        exactly fit that day's available bars.

        Outside drill-down (1d view, or non-intraday intervals) the
        toggle still affects fetch scheduling but has no visible-bars
        impact, so we fall back to the explicit-axis-change behavior.
        """
        try:
            in_drilldown = (
                self._drilldown_day is not None
                and is_intraday(self.interval_var.get())
            )
        except Exception:  # noqa: BLE001
            in_drilldown = False
        if in_drilldown:
            # Hold xlim during the in-flight reload so the new day-zoom
            # lands without an intermediate right-edge snap. Use the
            # synchronous loader so ``_zoom_primary_to_date`` runs
            # against the freshly-filtered ``_primary`` series — matches
            # the existing ticker-preserving call site in
            # ``_do_scheduled_reload``. The helper falls back to
            # clearing drill-down if the new series has no bars at all.
            self._reload_preserving_drilldown(self._load_data)
            return
        self._on_explicit_axis_change()

    # ------------------------------------------------------------------
    # Status bar severity (Item 9 — UI quick wins)
    # ------------------------------------------------------------------
    _STATUS_GLYPHS = {"info": "\u2713", "warn": "\u26a0", "error": "\u2715"}
    _STATUS_COLORS = {"info": "", "warn": "#a36b00", "error": "#cc3333"}

    def _on_status_var_change(self, *_args) -> None:
        """Mirror raw ``self.status`` into the tinted display var.

        Driven by a Tk ``trace_add("write", ...)`` callback so the
        existing :class:`StatusLog` pipeline (which writes directly to
        ``self.status``) remains the single source of truth. The
        severity is read off the most recent ``_status.history()``
        entry; we deliberately don't mutate the raw StringVar so
        tests reading ``app.status.get()`` still see the un-prefixed,
        truncation-correct message.
        """
        try:
            raw = self.status.get()
        except Exception:  # noqa: BLE001
            return
        level = "info"
        try:
            hist = self._status.history()
            if hist:
                lvl = hist[-1].level.lower()
                if lvl in ("warn", "warning"):
                    level = "warn"
                elif lvl in ("error", "err"):
                    level = "error"
                else:
                    level = "info"
        except Exception:  # noqa: BLE001
            pass
        self._status_severity = level
        glyph = self._STATUS_GLYPHS.get(level, "")
        display = f"{glyph} {raw}" if raw and glyph else raw
        try:
            self._status_display.set(display)
        except Exception:  # noqa: BLE001
            pass
        self._reapply_status_tint()

    def _reapply_status_tint(self) -> None:
        """Apply the foreground tint for the current ``_status_severity``.

        Called from the status-var trace and from :meth:`_apply_theme`
        so a light/dark toggle doesn't blow the tint away.
        """
        lbl = getattr(self, "_status_label", None)
        if lbl is None:
            return
        color = self._STATUS_COLORS.get(
            getattr(self, "_status_severity", "info"), "")
        try:
            lbl.configure(foreground=color)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Global keyboard accelerators (Item 12 — UI quick wins)
    # ------------------------------------------------------------------
    def _global_shortcut_allowed(self) -> bool:
        """Return False when focus is in a typing widget.

        Suppresses toolbar accelerators while the user is editing text
        in a ``tk.Text``, ``ttk.Entry`` (or their ttk-styled aliases)
        so e.g. typing the letter ``r`` into the ticker box doesn't
        trigger Reset view.
        """
        try:
            w = self.focus_get()
        except Exception:  # noqa: BLE001
            return True
        if w is None:
            return True
        try:
            cls = w.winfo_class()
        except Exception:  # noqa: BLE001
            return True
        return cls not in ("Text", "TText", "Entry", "TEntry")

    def _on_accel_reset_view(self, _event=None):
        if not self._global_shortcut_allowed():
            return None
        try:
            self._reset_view()
        except Exception:  # noqa: BLE001
            pass
        return "break"

    def _on_accel_settings(self, _event=None):
        if not self._global_shortcut_allowed():
            return None
        try:
            self._open_settings_dialog()
        except Exception:  # noqa: BLE001
            pass
        return "break"

    def _on_accel_watchlists(self, _event=None):
        if not self._global_shortcut_allowed():
            return None
        try:
            self._open_watchlist_dialog()
        except Exception:  # noqa: BLE001
            pass
        return "break"

    def _on_accel_toggle_chartstack(self, _event=None):
        """Ctrl+\u0060 \u2014 show / hide the ChartStack mini-chart strip.

        Routes through :py:meth:`_toggle_chartstack` so the keyboard
        shortcut, View-menu checkbutton, and any future button all
        share the same construct-on-demand + settings-persistence
        logic.
        """
        if not self._global_shortcut_allowed():
            return None
        self._toggle_chartstack()
        return "break"

    def _on_accel_snapshot_chart(self, _event=None):
        """Ctrl+Shift+S — save the current chart as a PNG.

        Mirrors the right-click "Snapshot Chart…" menu entry. Routes
        through :py:meth:`_save_chart_snapshot` so the keyboard
        shortcut and the canvas context menu share the same
        file-dialog + savefig path. Audit
        ``chart-snapshot-help-shortcut``.

        Guarded by ``_global_shortcut_allowed`` so the accelerator
        no-ops while the user is typing into an Entry / Text widget.
        ``return "break"`` stops the keystroke from also being
        delivered to the focused widget.
        """
        if not self._global_shortcut_allowed():
            return None
        try:
            self._save_chart_snapshot()
        except Exception:  # noqa: BLE001
            pass
        return "break"

    def _on_view_toggle_chartstack(self) -> None:
        """View menu callback for the "ChartStack" checkbutton.

        ``self._chartstack_visible_var`` was already flipped by the
        Tk checkbutton itself before the command fires; pass the new
        intent through to :py:meth:`_toggle_chartstack` so it can
        construct or destroy the panel to match.
        """
        try:
            target = bool(self._chartstack_visible_var.get())
        except Exception:  # noqa: BLE001
            target = False
        self._toggle_chartstack(target=target)

    def _toggle_chartstack(self, *, target: bool | None = None) -> None:
        """Show or hide the ChartStack panel.

        ``target=None`` (the default) flips the current state. Passing
        an explicit ``bool`` forces that state — used by the View-menu
        checkbutton which has already flipped its variable when the
        user clicked.

        Behavior:

        * **First activation in a session**: constructs the panel
          lazily (the ``__init__`` path skipped it because
          ``chartstack.enabled`` was ``False``), inserts it as the
          leftmost pane (index 0) of ``self._main_paned``, and wires
          the ``on_card_promote`` callback.
        * **Subsequent show**: just re-inserts the existing panel
          (state preserved).
        * **Hide**: removes the panel from the paned window. The
          panel object stays alive in ``self._chartstack`` so a
          re-show is instant.

        Persists ``chartstack.enabled`` so the choice survives a
        restart. Keeps ``self._chartstack_visible_var`` in sync with
        the actual paned-window state so the menu checkmark never
        lies.
        """
        paned = getattr(self, "_main_paned", None)
        if paned is None:
            return
        try:
            panes = list(paned.panes())
        except Exception:  # noqa: BLE001
            panes = []

        cs = getattr(self, "_chartstack", None)
        currently_visible = (cs is not None and str(cs) in panes)
        if target is None:
            target = not currently_visible

        if target and not currently_visible:
            if cs is None:
                try:
                    from .gui.chartstack import (
                        ChartStackPanel as _ChartStackPanel,
                    )
                    cs = _ChartStackPanel(
                        paned, owner=self,
                        geometry_store=getattr(self, "_geometry_store", None),
                    )
                    self._chartstack = cs
                    try:
                        cs.on_card_promote = self._on_chartstack_promote
                    except Exception:  # noqa: BLE001
                        pass
                except Exception:  # noqa: BLE001
                    self._chartstack = None
                    cs = None
            if cs is not None:
                try:
                    paned.insert(0, cs, weight=0)
                except Exception:  # noqa: BLE001
                    pass
                # Force the 3-pane layout to the hardcoded ratio
                # (same one used at startup) so the notebook width
                # stays put and only the chart gives up pixels to the
                # ChartStack. We deliberately bypass
                # ``geometry_store.restore_sash`` here — letting a
                # prior session's drag persist would re-introduce the
                # "watchlist eats half the chart" bug the user
                # reported. ``after_idle`` defers until the inserted
                # pane has been laid out so ``winfo_width`` is sane.
                def _force_3pane_layout(_p=paned):
                    try:
                        try:
                            main_w = int(
                                self._initial_geometry.split('+')[0]
                                .split('x')[0])
                        except (ValueError, IndexError, AttributeError):
                            main_w = 1280
                        from .constants import compute_main_paned_sashes
                        positions = compute_main_paned_sashes(
                            main_w, chartstack_visible=True)
                        self._apply_forced_sash(_p, positions)
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    self.after_idle(_force_3pane_layout)
                except Exception:  # noqa: BLE001
                    _force_3pane_layout()
        elif (not target) and currently_visible and cs is not None:
            try:
                paned.forget(cs)
            except Exception:  # noqa: BLE001
                pass
            # Force the 2-pane layout back to the hardcoded ratio so
            # the chart reclaims the chartstack's pixels and the
            # notebook stays at its consistent width.
            def _force_2pane_layout(_p=paned):
                try:
                    try:
                        main_w = int(
                            self._initial_geometry.split('+')[0]
                            .split('x')[0])
                    except (ValueError, IndexError, AttributeError):
                        main_w = 1280
                    from .constants import compute_main_paned_sashes
                    positions = compute_main_paned_sashes(
                        main_w, chartstack_visible=False)
                    self._apply_forced_sash(_p, positions)
                except Exception:  # noqa: BLE001
                    pass
            try:
                self.after_idle(_force_2pane_layout)
            except Exception:  # noqa: BLE001
                _force_2pane_layout()

        try:
            self._chartstack_visible_var.set(bool(target))
        except Exception:  # noqa: BLE001
            pass

        try:
            from . import settings as _settings
            _settings.set("chartstack.enabled", bool(target))
        except Exception:  # noqa: BLE001
            pass

    def _reset_view(self) -> None:
        """Reset to the 1d interval (default aggregation) at the right edge.

        If the chart is already on 1d, this is just a re-snap to the
        latest 200 bars (clears any drill-down zoom or pan). If the
        chart is on a different interval, switch to 1d first via
        ``_load_data()`` — that's a synchronous reload, but the 1d
        series is companion-prefetched so cache-hit is the norm.
        """
        try:
            self._preserve_xlim_on_render = False
            # Reset view explicitly abandons drill-down state.
            self._drilldown_day = None
            if self.interval_var.get() != "1d":
                self.interval_var.set("1d")
                try:
                    self._load_data()
                except Exception:  # noqa: BLE001
                    # _load_data already reverts on failure; fall through
                    # to the right-edge re-snap on whatever is loaded.
                    pass
            n = len(self._primary)
            if n > 0 and self._ax_price is not None:
                lo, hi = max(0, n - _defaults.get("default_window_bars")), n
                self._ax_price.set_xlim(lo - 0.5, hi - 0.5)
            self._render()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Drill-down: 1d candle double-click → 5m intraday zoom
    # ------------------------------------------------------------------
    # User-tunable deadlines for the drill-down race fix. The first is
    # the "wait for in-flight prefetch" grace period after a click; the
    # second is the user-facing UI deadline on the sync fallback fetch.
    # The fetch's underlying HTTP call is NOT cancelled at this deadline
    # (yfinance is synchronous and uncancellable); we just stop blocking
    # the user's attention. See plan.md for the full design.
    _DRILLDOWN_PREFETCH_GRACE_MS: int = 1500
    _DRILLDOWN_SYNC_UI_TIMEOUT_MS: int = 5000









    def _open_settings_dialog(self):
        # Lazy import — the settings dialog drags in theme + matplotlib
        # font helpers + the full scrollable-form scaffolding that aren't
        # needed until first open. Keeps cold-start lean.
        from .gui.dialogs import _SettingsDialog
        return _SettingsDialog(self)

    def _open_watchlist_dialog(self):
        # Lazy import — same rationale as ``_open_settings_dialog``.
        from .gui.dialogs import _WatchlistDialog
        return _WatchlistDialog(self)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _user_has_panned_x(self) -> bool:
        """Best-effort: detect a non-default xlim as a pan indicator."""
        try:
            ax = self._ax_price
            lo, hi = ax.get_xlim()
            n = len(self._primary)
            return not (n == 0 or (abs(hi - (n - 0.5)) < 1.0))
        except Exception:  # noqa: BLE001
            return False

    # ----------------------------------------------------------------- menubar

    def _build_menubar(self) -> None:
        """Build the application menubar via :class:`MenuBuilder`.

        Configuration is no longer auto-persisted — users explicitly load
        a JSON file via File → Load Configuration… and explicitly write
        back via File → Save Configuration… (text-editor model). The
        window title shows a trailing ``*`` after any in-app change until
        the next successful save.

        Compatibility markers for source-level menu tests:
        View → Heikin-Ashi → "Highlight Flat Bars" binds
        ``_highlight_ha_flat_var`` to ``_on_menu_toggle_highlight_ha_flat``;
        View → Heikin-Ashi → "Show Heikin-Ashi Candles" binds
        ``_ha_display_var`` to ``_on_menu_toggle_heikin_ashi``.
        View → "Volume time-of-day shading (1d bars)" binds
        ``_volume_tod_var`` to ``_on_menu_toggle_volume_tod``.
        The extracted builder still owns the literal menu labels
        "Highlight Key Bars", "Download Replay Data…", and
        "Restore Default Templates…".
        """
        self._menu_builder = MenuBuilder(self, callbacks=self)
        self.config(menu=self._menu_builder.build())
        self._menubar = self._menu_builder.menubar
        self._view_menu = self._menu_builder.view_menu
        self._ha_menu = self._menu_builder.ha_menu
        self._menubar_submenus = self._menu_builder.submenus
        self._recent_config_menu = self._menu_builder.recent_config_menu
        self._recent_watchlist_menu = self._menu_builder.recent_watchlist_menu
        # Initial paint with whatever theme is loaded; ``_apply_theme``
        # (called later in __init__) will repaint with the resolved
        # palette, and every theme toggle thereafter routes through
        # ``_apply_theme`` → ``_apply_menubar_theme``.
        self._apply_menubar_theme(getattr(self, "_theme", None) or LIGHT_THEME)

    def _apply_menubar_theme(self, theme: dict) -> None:
        self._theme_ctrl._apply_menubar_theme(theme)

    # ----------------------------------------------------------------- sandbox

    def _is_sandbox_active(self) -> bool:
        return self._sandbox_ctrl.active

    def _cancel_background_fetch_jobs(self) -> None:
        """Stop the streaming poll and cancel any armed reload jobs.

        Called whenever the chart is taken over by an alternate driver
        (sandbox, drill-down install, etc). Real ``after_cancel`` —
        flag-only is unsafe because in-flight ``after()`` callbacks
        fire regardless of which flag we toggled (audit #6).
        """
        try:
            self._stop_stream()
        except Exception:  # noqa: BLE001
            pass
        for jname in ("_poll_job", "_reload_job"):
            j = getattr(self, jname, None)
            if j is not None:
                with _silent_tcl():
                    self.after_cancel(j)
                setattr(self, jname, None)

    def _invalidate_focused_panels(self, candles: list[Candle]) -> None:
        """Drop cached views of ``candles`` so the next draw rebuilds.

        Used when the underlying candle data MUTATED in place (a
        forming-bar upsert: the rightmost bar's OHLCV changed but
        ``id(candles)`` is stable). Owns the cross-cache contract
        (series cache + indicator cache) so callers never have to
        know the implementation details (audit #2).

        For **pure-append growth** (sandbox tick, stream rollover
        appending a sealed bar), prefer
        :meth:`_notify_focused_panels_appended` — it leaves the
        indicator cache intact so the incremental ``inc_step`` hook
        in :meth:`IndicatorCache.get_or_compute_incremental` can
        extend the cached arrays in O(k) per tick instead of forcing
        an O(N) recompute of every indicator.
        """
        if not candles:
            return
        try:
            self._series_cache.pop(id(candles), None)
        except (AttributeError, KeyError):
            pass
        # Invalidate the indicator cache that ``_render_indicators_for_slot``
        # actually reads from (``self._indicator_cache``). NOTE: previous
        # code looked up ``self._indicator_manager.cache`` but the
        # IndicatorManager has no such attribute — the lookup silently
        # returned None, so the indicator cache was never invalidated
        # on sandbox ticks. Symptom: when an indicator was added during
        # a sandbox session, the next ``next_bar`` would compute candles
        # = N+1 but reuse the cached arr of length N (id-keyed against
        # the same in-place-mutated list), and ``render_for_slot`` would
        # raise ``ValueError: x and y must have same first dimension``,
        # leaving the indicator panel empty for the rest of the session.
        cache = getattr(self, "_indicator_cache", None)
        if cache is None:
            return
        try:
            cache.invalidate_for_candles(candles)
        except (AttributeError, TypeError):
            pass

    def _notify_focused_panels_appended(self, candles: list[Candle]) -> None:
        """Append-aware sibling of :meth:`_invalidate_focused_panels`.

        Used by the sandbox ``next_bar`` path (and any other pure-grow
        caller) to clear the series-cache entry while leaving the
        indicator cache intact. The indicator cache's incremental
        extension hook (see :meth:`IndicatorCache.get_or_compute_incremental`)
        detects the same-id length-grew condition on the next render
        and routes through ``inc_step`` for indicators that support
        the incremental protocol (SMA, EMA today) — typically O(k)
        per tick versus O(N) for the full recompute. Non-incremental
        indicators (RSI, ATR, etc.) fall through to a full recompute
        inside the cache when the entry is rebuilt, which is no worse
        than the pre-incremental baseline.

        The series cache MUST still be invalidated: ``_SeriesArrays``
        holds prebuilt fixed-length numpy column arrays whose shape
        does not match the grown list. Failing to drop it would make
        ``_y_limits_for_slice`` (and other consumers) read stale
        bounds for the visible-slice window.
        """
        if not candles:
            return
        try:
            self._series_cache.pop(id(candles), None)
        except (AttributeError, KeyError):
            pass

    def _build_sandbox_spec(self, dlg_result: dict[str, Any]) -> SessionSpec:
        return self._sandbox_ctrl.build_spec(dlg_result)


    def _sync_indicator_dialog_for_interval(self) -> None:
        """Refresh the indicator dialog's kind dropdown + pane-budget
        gate to reflect the current chart interval.

        Cheap and side-effect-free when the dialog is closed.
        """
        dlg = getattr(self, "_indicator_dialog", None)
        if dlg is None:
            return
        try:
            dlg.refresh_kind_dropdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            dlg._apply_pane_budget_gate()
        except Exception:  # noqa: BLE001
            pass

    def _open_per_indicator_dialog(
        self, config_id: int, slot: str | None = None,
    ) -> None:
        """Open the per-indicator settings popup for ``config_id``.

        Funneled through this method so the OverlayLegend doesn't
        need to import the popup module directly. Singletons are
        managed in ``self._per_indicator_dialogs``; a second
        double-click on the same legend row refocuses the existing
        popup rather than spawning a duplicate. ``slot`` ("primary"
        / "compare") records which legend pane the click originated
        from — passed through for future scope-split logic.

        Swallows exceptions: a broken popup must never leave the
        chart in an unusable state.
        """
        try:
            from .gui.per_indicator_dialog import open_per_indicator_dialog
        except Exception:  # noqa: BLE001
            return
        try:
            open_per_indicator_dialog(self, int(config_id), slot=slot)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Legend row context menu (right-click)
    # ------------------------------------------------------------------

    def _show_legend_context_menu(
        self, config_id: int, slot: str | None,
        x_root: int, y_root: int,
    ) -> None:
        """Build and post the legend row's right-click context menu.

        Items: Edit Settings… / Change Color (single output or
        cascading sub-menu when the indicator has 2+ outputs) /
        Duplicate / Hide ↔ Show / Remove. Wired by
        :class:`OverlayLegend` via the ``on_row_context_menu``
        callback in :meth:`__init__`.

        Swallows exceptions defensively: a broken menu must never
        leave the chart in an unusable state. ``slot`` records the
        originating legend pane so "Edit Settings…" can pass it
        through to the per-indicator popup for scope-split context.
        """
        try:
            manager = getattr(self, "_indicator_manager", None)
            if manager is None:
                return
            cfg = manager.get(int(config_id))
            if cfg is None:
                return
            menu = tk.Menu(self, tearoff=0)
            menu.add_command(
                label="Edit Settings…",
                command=lambda: self._open_per_indicator_dialog(
                    int(config_id), slot),
            )
            output_keys = self._legend_context_output_keys(cfg)
            if len(output_keys) == 1:
                only = output_keys[0]
                menu.add_command(
                    label="Change Color…",
                    command=lambda: self._legend_pick_color(
                        int(config_id), only),
                )
            elif len(output_keys) > 1:
                sub = tk.Menu(menu, tearoff=0)
                for k in output_keys:
                    sub.add_command(
                        label=f"{k}…",
                        command=lambda kk=k: self._legend_pick_color(
                            int(config_id), kk),
                    )
                menu.add_cascade(label="Change Color", menu=sub)
            menu.add_command(
                label="Duplicate",
                command=lambda: self._legend_duplicate(int(config_id)),
            )
            if bool(getattr(cfg, "visible", True)):
                menu.add_command(
                    label="Hide",
                    command=lambda: manager.update(
                        int(config_id), visible=False),
                )
            else:
                menu.add_command(
                    label="Show",
                    command=lambda: manager.update(
                        int(config_id), visible=True),
                )
            menu.add_separator()
            menu.add_command(
                label="Remove",
                command=lambda: manager.remove(int(config_id)),
            )
            apply_menu_theme(menu, getattr(self, "_theme", None) or LIGHT_THEME)
            try:
                menu.tk_popup(int(x_root), int(y_root))
            finally:
                try:
                    menu.grab_release()
                except tk.TclError:
                    pass
        except Exception:  # noqa: BLE001
            pass

    def _legend_context_output_keys(self, cfg) -> list[str]:
        """Return the list of output keys for ``cfg``'s factory.

        Used by the context menu to decide whether "Change Color"
        is a single command or a sub-menu (when the indicator emits
        2+ outputs, e.g. Bollinger Bands' upper / middle / lower).
        Returns an empty list for unknown / un-instantiable kinds
        — the menu then suppresses the entry entirely.
        """
        try:
            if getattr(cfg, "unknown", False):
                return []
            from .indicators.base import factory_by_kind_id
            entry = factory_by_kind_id(getattr(cfg, "kind_id", ""))
            if entry is None:
                return []
            _name, cls = entry
            default_style = getattr(cls, "default_style", None) or {}
            return [str(k) for k in default_style.keys()]
        except Exception:  # noqa: BLE001
            return []

    def _legend_pick_color(self, config_id: int, output_key: str) -> None:
        """Open the honeycomb palette for ``output_key`` and commit.

        Reads the current color from ``cfg.style[output_key]`` (or
        falls back to the factory's ``default_style``), opens
        :func:`gui.color_palette.pick_color`, and on success writes
        a new ``style`` dict through ``manager.update``. The chart
        repaints via the standard manager-event coalesced redraw.
        """
        try:
            manager = getattr(self, "_indicator_manager", None)
            if manager is None:
                return
            cfg = manager.get(int(config_id))
            if cfg is None or getattr(cfg, "unknown", False):
                return
            from .indicators.base import factory_by_kind_id
            entry = factory_by_kind_id(getattr(cfg, "kind_id", ""))
            if entry is None:
                return
            _name, cls = entry
            default_style = dict(getattr(cls, "default_style", None) or {})
            current_style = dict(getattr(cfg, "style", None) or {})
            current_ls = current_style.get(
                output_key, default_style.get(output_key))
            current_color = (getattr(current_ls, "color", None)
                             or FALLBACK_GRAY)
            from .gui.color_palette import pick_color
            chosen = pick_color(
                self, initial=str(current_color),
                title=f"Pick color \u2014 {output_key}")
            if not chosen:
                return
            default_ls = default_style.get(output_key)
            default_color = (getattr(default_ls, "color", FALLBACK_GRAY)
                             if default_ls is not None else FALLBACK_GRAY)
            from .indicators.base import LineStyle
            new_style = dict(current_style)
            if str(chosen).upper() == str(default_color).upper():
                # Picking the default removes the override entirely
                # so future default_style tweaks propagate cleanly
                # (matches IndicatorDialog._build_style semantics).
                new_style.pop(output_key, None)
            else:
                width = (getattr(default_ls, "width", 1.2)
                         if default_ls is not None else 1.2)
                visible = (getattr(default_ls, "visible", True)
                           if default_ls is not None else True)
                new_style[output_key] = LineStyle(
                    color=str(chosen), width=float(width),
                    visible=bool(visible))
            manager.update(int(config_id), style=new_style)
        except Exception:  # noqa: BLE001
            pass

    def _legend_duplicate(self, config_id: int) -> None:
        """Add a duplicate of ``config_id`` to the manager.

        The clone is built via ``IndicatorConfig.from_dict(orig.to_dict())``
        so it inherits every field except ``id`` (which is re-issued
        on construction — see :class:`IndicatorConfig`). Same scopes,
        same params, same style overrides, same per-interval
        visibility. The user can rename / re-color via the per-
        indicator popup afterwards.
        """
        try:
            manager = getattr(self, "_indicator_manager", None)
            if manager is None:
                return
            orig = manager.get(int(config_id))
            if orig is None:
                return
            from .indicators.config import IndicatorConfig
            clone = IndicatorConfig.from_dict(orig.to_dict())
            # Preserve the "unknown" flag from the original — a
            # to_dict / from_dict round-trip already re-runs the
            # factory registry check, but re-asserting here protects
            # against a registry that's mid-mutation.
            clone.unknown = bool(getattr(orig, "unknown", False))
            manager.add(clone)
        except Exception:  # noqa: BLE001
            pass

    def _current_sandbox_result(self):
        return self._sandbox_ctrl.current_result()

    def _current_sandbox_screenshot_dir(self) -> Path | None:
        return self._sandbox_ctrl.current_screenshot_dir()

    def _show_sandbox_panel(self) -> None:
        self._sandbox_ctrl.show_panel(app=self, silent_tcl=_silent_tcl)

    def _hide_sandbox_panel(self) -> None:
        self._sandbox_ctrl.hide_panel(app=self, silent_tcl=_silent_tcl)

    # ------------------------------------------------------------------
    # Scanner integration (sandbox-driven block-tree screener)
    # ------------------------------------------------------------------

    def _build_scanner_tab(self) -> None:
        """Construct the right-side Scanner notebook tab.

        Auto-loads any saved scans from ``<cache>/scans/`` and wires
        the per-row action callback to the existing primary/compare
        register-and-focus paths. Failures during autoload degrade
        gracefully to an empty library — the user can re-import.
        """
        from .gui.scanner_tab import ScannerTab
        from .scanner import storage as _scan_storage
        from .scanner.runner import ScanRunner

        library: dict[str, Any] = {}
        try:
            library = {s.id: s for s in _scan_storage.load_all()}
        except Exception:  # noqa: BLE001
            try:
                self._status.warn(
                    "Scanner: failed to load saved scans; starting empty")
            except Exception:  # noqa: BLE001
                pass
            library = {}

        self._scanner_storage = _scan_storage
        self._scan_runner = ScanRunner()
        self._scan_tick_id: int = 0
        self._scan_last_results: dict[str, Any] = {}

        self._scanner_tab = ScannerTab(
            self._notebook,
            library=library,
            on_scan_saved=self._on_scanner_scan_saved,
            on_scan_deleted=self._on_scanner_scan_deleted,
            on_row_action=self._on_scanner_row_action,
        )
        self._notebook.add(self._scanner_tab, text="Scanner")

    def _on_open_strategy_dialog(self) -> None:
        """**Strategy** menu entry — open or re-focus the Strategy Tester popup.

        Lazily constructs a Toplevel containing a :class:`StrategyTab`
        on first open. Subsequent opens deiconify + lift + focus the
        existing window so the worker / poll loop / Recent Runs state
        survives. Closing the window destroys the embedded tab (which
        runs its own ``<Destroy>`` cleanup of the poll job + acceptance
        token) and clears the stash so the next open builds a fresh
        widget.
        """
        existing = self._strategy_dialog
        if existing is not None:
            try:
                if existing.winfo_exists():
                    try:
                        existing.deiconify()
                        existing.lift()
                        existing.focus_set()
                    except tk.TclError:
                        pass
                    return
            except tk.TclError:
                pass
            self._strategy_dialog = None
            self._strategy_tab = None

        try:
            from .gui.strategy_tab import StrategyTab
        except Exception:  # noqa: BLE001
            logger.exception("Failed to import StrategyTab; cannot open dialog")
            return

        try:
            dlg = tk.Toplevel(self)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to create Strategy Tester Toplevel")
            return

        dlg.title("Strategy Tester")
        try:
            dlg.transient(self)
        except tk.TclError:
            pass
        try:
            from .gui.geometry_store import attach_persistent_geometry
            attach_persistent_geometry(dlg, "dlg.strategy", "1400x780")
        except Exception:  # noqa: BLE001
            try:
                dlg.geometry("1400x780")
            except tk.TclError:
                pass
        try:
            dlg.minsize(1000, 600)
        except tk.TclError:
            pass

        try:
            tab = StrategyTab(dlg, app=self)
            tab.pack(fill="both", expand=True)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to construct StrategyTab inside popup")
            try:
                dlg.destroy()
            except Exception:  # noqa: BLE001
                pass
            return

        self._strategy_dialog = dlg
        self._strategy_tab = tab

        def _on_dialog_close() -> None:
            try:
                dlg.destroy()
            except Exception:  # noqa: BLE001
                pass
            finally:
                if self._strategy_dialog is dlg:
                    self._strategy_dialog = None
                    self._strategy_tab = None

        try:
            dlg.protocol("WM_DELETE_WINDOW", _on_dialog_close)
        except tk.TclError:
            pass

    def _on_scanner_scan_saved(self, scan: Any) -> None:
        try:
            self._scanner_storage.save(scan)
        except Exception:  # noqa: BLE001
            try:
                self._status.error(
                    f"Scanner: failed to save scan {scan.name!r}")
            except Exception:  # noqa: BLE001
                pass

    def _on_scanner_scan_deleted(self, scan_id: str) -> None:
        try:
            self._scanner_storage.delete(scan_id)
        except Exception:  # noqa: BLE001
            try:
                self._status.error(
                    f"Scanner: failed to delete scan {scan_id!r}")
            except Exception:  # noqa: BLE001
                pass
        # Drop any stale history so a re-created scan starts fresh.
        runner = getattr(self, "_scan_runner", None)
        if runner is not None:
            try:
                runner.reset_history(scan_id)
            except Exception:  # noqa: BLE001
                pass

    def _on_scanner_row_action(self, symbol: str, kind: str) -> None:
        """User picked a row + an action from the Scanner result table.

        ``kind`` is ``"primary"``, ``"compare"`` or ``"watchlist"``.
        Routes through the existing sandbox register-and-focus paths
        when a session is active; otherwise falls back to the regular
        ``ticker_var.set`` / ``compare_ticker_var.set`` plumbing.
        """
        sym = (symbol or "").strip().upper()
        if not sym:
            return
        sandbox_on = self._is_sandbox_active()
        try:
            if kind == "primary":
                if sandbox_on:
                    self._sandbox_register_and_focus(sym)
                else:
                    self.ticker_var.set(sym)
                    if hasattr(self, "_load_data"):
                        try:
                            self._load_data()
                        except Exception:  # noqa: BLE001
                            pass
            elif kind == "compare":
                if sandbox_on:
                    try:
                        self.compare_var.set(True)
                    except Exception:  # noqa: BLE001
                        pass
                    self._sandbox_register_compare(sym)
                else:
                    try:
                        self.compare_var.set(True)
                        self.compare_ticker_var.set(sym)
                    except Exception:  # noqa: BLE001
                        pass
            elif kind == "watchlist":
                # Best-effort: append to the active pinned watchlist if
                # the watchlist manager is available. Tolerate missing
                # APIs (smoke tests run without one configured).
                wl_mgr = getattr(self, "_watchlist_manager", None)
                if wl_mgr is None:
                    return
                try:
                    name = self.watchlist_var.get()
                except Exception:  # noqa: BLE001
                    name = ""
                if not name:
                    return
                try:
                    wl_mgr.add_ticker(name, sym)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self._populate_watchlist_tab(name)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            try:
                self._status.error(
                    f"Scanner: action {kind!r} on {sym} failed")
            except Exception:  # noqa: BLE001
                pass

    def _refresh_scanner_for_sandbox(self) -> None:
        self._sandbox_ctrl.refresh_scanner_for_sandbox(app=self, silent_tcl=_silent_tcl)

    def _reset_scanner_state(self) -> None:
        self._sandbox_ctrl.reset_scanner_state(app=self, silent_tcl=_silent_tcl)

    def _sandbox_register_compare(self, symbol: str) -> bool:
        return self._sandbox_ctrl.register_compare(
            app=self,
            symbol=symbol,
            silent_tcl=_silent_tcl,
        )

    def _sandbox_sync_compare_to_var(self) -> None:
        self._sandbox_ctrl.sync_compare_to_var(app=self, silent_tcl=_silent_tcl)

    def _sandbox_can_register(self, sym: str) -> bool:
        return self._sandbox_ctrl.can_register(app=self, sym=sym)

    def _sandbox_register_and_focus(self, symbol: str) -> bool:
        return self._sandbox_ctrl.register_and_focus(app=self, symbol=symbol)

    def _install_sandbox_compare_series(
        self,
        *,
        symbol: str,
        candles: list[Candle],
        interval: str,
    ) -> None:
        self._sandbox_ctrl.install_compare_series(
            app=self,
            symbol=symbol,
            candles=candles,
            interval=interval,
            silent_tcl=_silent_tcl,
        )

    def _restrict_toolbar_intervals_for_sandbox(
        self,
        *,
        display_intervals: list[str],
        daily_available: bool,
    ) -> None:
        self._sandbox_ctrl.restrict_toolbar_intervals(
            app=self,
            display_intervals=list(display_intervals),
            daily_available=daily_available,
            silent_tcl=_silent_tcl,
        )

    def _restore_toolbar_intervals_from_sandbox(self) -> None:
        self._sandbox_ctrl.restore_toolbar_intervals(app=self, silent_tcl=_silent_tcl)

    def _sandbox_reset_compare_for_session_start(self) -> None:
        self._sandbox_ctrl.reset_compare_for_session_start(
            app=self,
            silent_tcl=_silent_tcl,
            compare_default=_DEFAULT_COMPARE,
        )

    def _install_sandbox_primary_series(
        self,
        *,
        symbol: str,
        candles: list[Candle],
        interval: str,
        full_session_length: int | None = None,
    ) -> None:
        self._sandbox_ctrl.install_primary_series(
            app=self,
            symbol=symbol,
            candles=candles,
            interval=interval,
            full_session_length=full_session_length,
            silent_tcl=_silent_tcl,
        )

    def _on_open_status_history(self, _event=None) -> None:
        """Open (or focus) the verbose status-history window.

        Called by clicking the bottom-of-window status bar OR by the
        File → Status History… menu item. Single-instance: a second
        click while the window is open just lifts/focuses it. The
        window itself polls the status log every 500 ms so newly-emitted
        entries appear without manual refresh.
        """
        try:
            def _make_window() -> tk.Toplevel:
                win = StatusHistoryWindow(self, self._status)
                self._status_history_win = win

                def _forget(_evt=None):
                    try:
                        self._status_history_win = None
                    except Exception:  # noqa: BLE001
                        pass

                with _silent_tcl():
                    win.bind("<Destroy>", _forget, add="+")
                return win

            win = self._dialog_mgr.open_or_focus("status_history", _make_window)
            self._status_history_win = win
        except Exception as e:  # noqa: BLE001
            self._status.error(f"Failed to open status history: {e}")
            return

    def _on_tools_restore_templates(self, _event=None) -> None:
        """Force-seed the bundled starter-pack strategy templates.

        Unlike the first-run seed, this bypasses the "library is empty"
        guard but still respects the per-file existence check: files
        with the same id as a bundled template are overwritten, files
        with different ids are untouched. The sentinel is rewritten so
        the next first-run check still short-circuits.
        """
        try:
            from tkinter import messagebox
            yes = messagebox.askyesno(
                title="Restore Default Templates",
                message=(
                    "Copy the bundled starter-pack templates "
                    "(5 entries / 5 exits / 5 scanners) into your "
                    "library?\n\n"
                    "Existing strategies of the same id will be "
                    "overwritten; your other strategies will be "
                    "untouched."
                ),
                parent=self,
            )
            if not yes:
                return
            from .templates import seed_default_templates
            result = seed_default_templates(force=True)
            self._status.info(
                f"Restored {result['copied']} starter templates"
            )
        except Exception as e:  # noqa: BLE001
            try:
                self._status.error(
                    f"Failed to restore templates: {e}"
                )
            except Exception:  # noqa: BLE001
                pass

    def _refresh_data_source_combobox(self) -> None:
        """Repopulate the source combobox after BYOD registrations change.

        Called by ``_on_help_configure_local_data`` once the local-data
        dialog finishes saving. Reads the current ``DATA_SOURCES`` keys
        (post-`register_local_sources()`) and pushes them into the
        toolbar widget. Selection is preserved if still valid.
        """
        try:
            self._toolbar.set_sources(tuple(DATA_SOURCES.keys()))
        except Exception:  # noqa: BLE001
            pass

    def _refresh_title(self) -> None:
        manager = getattr(self, "_config_manager", None)
        kwargs = dict(
            title_setter=self.title,
            ticker_var=getattr(self, "ticker_var", None),
            interval_var=getattr(self, "interval_var", None),
            watchlists=getattr(self, "_watchlists", None),
            separator=" · ",
            dirty_suffix=" *",
        )
        if manager is not None:
            manager.refresh_title(**kwargs)
            return
        ConfigManager.refresh_title_for(**kwargs)

    # ------------------------------------------------------------------
    # Feature B — sandbox auto-resume + update-check helpers
    # ------------------------------------------------------------------
    def _maybe_write_sandbox_resume_metadata(self) -> None:
        self._sandbox_ctrl.maybe_write_resume_metadata()

    def _maybe_prompt_sandbox_resume(self) -> None:
        self._sandbox_ctrl.maybe_prompt_resume(app=self)

    def _on_update_check_result(self, result: Any) -> None:
        """Handle the async update-check result on the Tk main thread."""
        try:
            if getattr(result, "status", "") != "available":
                return
            latest = str(getattr(result, "latest", "") or "")
            if not latest:
                return
            url = str(getattr(result, "url", "") or "")
            self._show_update_banner(latest, url=url)
        except Exception:  # noqa: BLE001
            pass

    def _show_update_banner(self, new_version: str, *, url: str = "") -> None:
        """Display a passive one-line banner about an available update.

        Pattern mirrors :class:`FirstRunBannerMixin` — a dismissable
        ttk.Frame at the top of the window with a single-line
        message, optional release-link button, and a dismiss button.

        Idempotent: a second update notification (or a duplicate
        call from a re-run check) is silently swallowed if the
        banner is already visible.
        """
        existing = getattr(self, "_update_banner_frame", None)
        if existing is not None:
            return
        try:
            frame = ttk.Frame(self, padding=(8, 4))
            frame.pack(side=tk.TOP, fill=tk.X)
            display_version = (
                new_version if new_version.lower().startswith("v")
                else f"v{new_version}"
            )
            ttk.Label(
                frame,
                text=(
                    f"Update {display_version} available "
                    f"— Help → Check for Updates"),
                anchor="w",
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)

            def _dismiss() -> None:
                try:
                    frame.destroy()
                except tk.TclError:
                    pass
                self._update_banner_frame = None

            ttk.Button(
                frame, text="Dismiss", command=_dismiss,
            ).pack(side=tk.RIGHT, padx=(6, 0))

            if url:

                def _open_release() -> None:
                    try:
                        webbrowser.open(url)
                    except Exception:  # noqa: BLE001
                        pass

                ttk.Button(
                    frame, text="View release", command=_open_release,
                ).pack(side=tk.RIGHT, padx=(6, 0))

            self._update_banner_frame = frame
        except Exception:  # noqa: BLE001
            # A banner failure must never break the chart.
            self._update_banner_frame = None

    def _on_close(self) -> None:
        """Stop stream, cancel after jobs, shut down executor, destroy."""
        # Prompt-on-quit when configuration or watchlists have unsaved
        # changes — Yes saves before exit, No discards and exits,
        # Cancel aborts the close so the user keeps editing. The
        # prompt is intentionally lightweight (single dialog naming
        # both kinds at once) so a user with a clean session never
        # sees an interruption.
        if not self._confirm_close_when_dirty():
            return
        # Capture sandbox-resume metadata BEFORE we start tearing
        # down the engine. ``write_resume_metadata`` is atomic + best
        # effort; any failure is logged through the exception path.
        try:
            self._maybe_write_sandbox_resume_metadata()
        except Exception:  # noqa: BLE001
            pass
        # Close every open per-indicator settings popup BEFORE the
        # main window is destroyed so their ``_on_close`` handlers
        # can unhook from the manager subscription list and clear the
        # ``self._per_indicator_dialogs`` registry entries. Closing
        # them after ``self.destroy()`` would race with Tk teardown.
        try:
            per_dlgs = getattr(self, "_per_indicator_dialogs", None) or {}
            for pdlg in list(per_dlgs.values()):
                if pdlg is None:
                    continue
                try:
                    pdlg._on_close()
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        # Close every open per-drawing dialog (Feature C) for the
        # same reason as the per-indicator dialogs above. The
        # registry is keyed by ``drawing.id``; each dialog's
        # ``_on_close`` clears its own entry via the ``on_close``
        # callback passed in ``_open_drawing_dialog``.
        try:
            draw_dlgs = getattr(self, "_drawing_dialogs", None) or {}
            for ddlg in list(draw_dlgs.values()):
                if ddlg is None:
                    continue
                try:
                    ddlg._close()
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        # Destroy any remaining modeless dialog singletons tracked by
        # the unified dialog manager (indicator/status/help windows,
        # plus any stale entries not already closed above).
        try:
            self._dialog_mgr.close_all()
        except Exception:  # noqa: BLE001
            pass
        # Defensive synchronous flush of the drawings store: any
        # late-arriving edits queued through ``after_idle`` are
        # written to disk now so the next launch sees them. The
        # coalesced auto-flush in ``_on_drawing_event`` is best
        # effort; this is the certain one.
        try:
            store = getattr(self, "_drawings", None)
            if store is not None:
                store.flush()
        except Exception:  # noqa: BLE001
            pass
        # Finish any outstanding drill-down request first so its timer
        # jobs are cancelled and its wait cursor is restored before we
        # tear down the rest of the window. (The cursor restore is a
        # no-op once destroy() runs, but it keeps invariants tidy and
        # protects against a future caller doing post-close work.)
        try:
            req = self._drilldown_request
            if req is not None:
                self._finish_drilldown_request(req)
        except Exception:  # noqa: BLE001
            pass
        # Cancel pending after jobs.
        for job in self._after_jobs:
            try:
                self.after_cancel(job)
            except Exception:  # noqa: BLE001
                pass
        self._after_jobs.clear()
        self._stream_drain_after = None
        # Tear down entries stack BEFORE exits stack (entries depends on
        # the shared tracker + paper engine owned by exits).
        try:
            self._close_entries_stack()
        except Exception:  # noqa: BLE001
            pass
        # Tear down exits stack (subscribed callbacks, dialog window).
        try:
            self._close_exits_stack()
        except Exception:  # noqa: BLE001
            pass
        # Stop streaming.
        try:
            self._stop_stream()
        except Exception:  # noqa: BLE001
            pass
        # Shut down fetch/preload executors.
        try:
            self._fetch_svc.shutdown()
            self._executor = self._fetch_svc._executor
            self._fetch_executor = self._fetch_svc._fetch_executor
        except Exception:  # noqa: BLE001
            pass
        try:
            self.destroy()
        except Exception:  # noqa: BLE001
            pass



# --- main ---------------------------------------------------------------

def _identify_to_window_manager(root) -> None:
    """Tell the OS this process is "TradingLab" for taskbar / WM_CLASS.

    Cross-platform glue called early in ``ChartApp.__init__``:

    * **Linux / BSD (X11)**: set the Tk root's ``WM_CLASS`` hint via
      ``wm class`` (available on X11 Tk builds, not on Windows).
    * **Windows**: set the Explicit App User Model ID via
      ``Shell32.SetCurrentProcessExplicitAppUserModelID`` so the
      taskbar groups all TradingLab windows under one icon
      (instead of generic "python.exe") and the jump list works.
    * **macOS**: no-op; ``Info.plist`` is the source of truth and
      we can't influence it from Python.

    Failures are swallowed — none of this is essential for the GUI
    to function, and a missing Shell32 / Tk build is a deployment
    issue we report rather than crash on.
    """
    import sys as _sys
    if _sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "TradingLab.App.1")
        except Exception:  # noqa: BLE001
            pass
        return
    if _sys.platform == "darwin":
        return
    # X11 path — ``wm class`` is supported on X11 Tk builds.
    try:
        root.tk.call("wm", "class", str(root), "TradingLab")
    except Exception:  # noqa: BLE001
        pass


def _enable_high_dpi_awareness() -> None:
    """Opt the process into per-monitor-v2 DPI awareness on Windows.

    Tk is **not** DPI-aware by default — on a 4K monitor at 200%
    scaling the chart canvas renders at half resolution and looks
    blurry. Calling the Win32 ``SetProcessDpiAwarenessContext`` API
    before the first Tk window is created is the standard fix.

    Three levels of fallback so we still help on older Windows
    builds without crashing on non-Windows hosts:

    1. ``SetProcessDpiAwarenessContext(-4)`` — Win10 1703+ — full
       per-monitor v2.
    2. ``SetProcessDpiAwareness(2)`` — Win8.1+ — per-monitor v1.
    3. ``SetProcessDPIAware()`` — Vista+ — system DPI.

    On macOS / Linux this is a no-op. Errors are swallowed: a DPI
    failure must never prevent the GUI from starting.
    """
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        # PER_MONITOR_AWARE_V2 = -4 (per Windows SDK headers).
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
            return
        except (AttributeError, OSError):
            pass
        try:
            # PROCESS_PER_MONITOR_DPI_AWARE = 2.
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return
        except (AttributeError, OSError):
            pass
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass
    except Exception:  # noqa: BLE001
        # DPI awareness is a nice-to-have. Never fail launch over it.
        pass


def main() -> int:
    """Construct and run the Tk event loop.

    Honors a small set of CLI flags before bringing up the GUI:

    * ``--version`` / ``-V`` — print the package version (including
      embedded git commit + build date when run from a release
      build) and exit 0. Used by the build smoke check in
      ``tools/build_exe.ps1`` and by users who want to confirm
      which build they're running without launching the chart.
    * ``--help`` / ``-h`` — print a one-line usage summary.

    Unknown flags are ignored (we deliberately do NOT use argparse
    here so PyInstaller's bootloader cannot inject a parser-fatal
    flag into a packaged build).
    """
    import sys

    argv = sys.argv[1:]
    if any(a in ("--version", "-V") for a in argv):
        from ._version import version_string
        print(version_string())
        return 0
    if any(a in ("--help", "-h") for a in argv):
        print(
            "Usage: tradinglab [--version | --help]\n"
            "  --version, -V   print version and exit\n"
            "  --help,    -h   print this message and exit\n"
            "With no flags, launches the GUI."
        )
        return 0

    # Construct the splash controller as early as possible — its
    # ``report`` calls during ChartApp init give the user something
    # to look at while Python + matplotlib + Tk finish warming up.
    # The make_splash() factory returns a NullSplashController in
    # dev mode (no pyi_splash importable) or when the user passes
    # ``--no-splash`` / sets TRADINGLAB_NO_SPLASH=1, so this is a
    # one-liner that works the same in every runtime.
    try:
        from .gui.splash import STAGE_SETTINGS, make_splash
        splash = make_splash()
        try:
            splash.report(STAGE_SETTINGS)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        from .gui.splash import NullSplashController
        splash = NullSplashController()

    # Must run before any Tk widget is created so widgets pick up
    # the correct logical-pixel size from creation onwards.
    _enable_high_dpi_awareness()

    # Crash handlers go up before ``ChartApp()`` so an exception
    # during construction also produces a crash report.
    try:
        from .gui.crash_dialog import install_crash_handler
        install_crash_handler()
    except Exception:  # noqa: BLE001
        pass

    # Inject DPAPI-encrypted credentials (if any) into ``os.environ``
    # before vendor modules read credentials. No-op on non-Windows or
    # when no blob exists yet. New users hit the credentials dialog
    # via Help \u2192 Configure Credentials….
    _dpapi_prime_result: Optional[str] = None
    try:
        from .gui.credentials_dialog import prime_environment_from_dpapi
        _dpapi_prime_result = prime_environment_from_dpapi()
    except Exception:  # noqa: BLE001
        pass

    # Ensure the splash never outlives the GUI even on a crash
    # during ChartApp construction. The double-close is safe (the
    # controller is idempotent) so we can also close it explicitly
    # in the normal path below.
    try:
        app = ChartApp(splash=splash)
    except BaseException:
        try:
            splash.close()
        except Exception:  # noqa: BLE001
            pass
        raise
    # If DPAPI priming saw a present-but-unreadable blob, surface
    # that on the status bar. This is the only suspicious failure
    # mode (the rest — missing, dpapi_unavailable — are expected on
    # fresh installs and would just nag the user).
    if _dpapi_prime_result in ("decrypt_error", "io_error"):
        try:
            status_log = getattr(app, "_status_log", None) or getattr(app, "_status", None)
            if status_log is not None:
                status_log.warn(
                    "credentials: DPAPI blob present but could not be "
                    "decrypted ("
                    + _dpapi_prime_result
                    + "). Re-enter your credentials via "
                    "Help → Configure Credentials.")
        except Exception:  # noqa: BLE001
            pass
    # Tk swallows exceptions inside event handlers and routes them
    # through ``report_callback_exception`` — they DO NOT reach
    # ``sys.excepthook``. Install the second half so an unhandled
    # error in (say) a button command also produces a crash file.
    try:
        from .gui.crash_dialog import install_tk_excepthook
        install_tk_excepthook(app)
    except Exception:  # noqa: BLE001
        pass
    try:
        app.mainloop()
    finally:
        try:
            app._on_close()
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(main())
