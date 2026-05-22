"""Canonical registry of every user-tweakable default in the application.

This module is the single place to look for "what's the default for X" and
the single place users override behavior without editing source code.

How overrides work
------------------
At import time, :func:`_load_overrides` reads ``settings.json`` (the same
file written by the in-app Settings dialog and the lightweight
:mod:`tradinglab.settings` API). Per-key validators normalize and
range-check each override; bad/missing values silently fall back to the
built-in default. The merged result is cached in :data:`_resolved` for the
lifetime of the process — defaults are not re-read mid-session.

Public API
----------
- :data:`TUNABLES` — ordered tuple of every recognized key, its default,
  type tag, validator, and a human-readable description. The Settings
  dialog renders rows from this table; the README "Configuration" section
  is generated from it as well.
- :func:`get(key)` — resolved value (override if valid, else builtin).
- :func:`describe(key)` — `(default, kind, description)` triple for docs.
- :func:`reload()` — force a re-read of ``settings.json``. Mainly for tests.
- :func:`as_markdown_table()` — render the catalog as a Markdown table.

User workflow
-------------
1. Open ``%LOCALAPPDATA%\\tradinglab\\settings.json`` (Win),
   ``~/Library/Application Support/tradinglab/settings.json`` (macOS),
   or ``~/.local/share/tradinglab/settings.json`` (Linux).
2. Add ``"key": value`` for any tunable below.
3. Restart the app. (Tunables are resolved once at boot; live-mutation
   would require wiring each consumer to re-read on demand and isn't
   worth the complexity for these values.)

Adding a new tunable
--------------------
1. Append a :class:`Tunable` to the :data:`TUNABLES` tuple below.
2. Replace the inline literal in the consumer with ``defaults.get("key")``.
3. Mention it in :file:`defaults.spec.md` and :file:`README.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from . import settings as _settings

# ---------------------------------------------------------------------------
# Validator factories — all return (ok, normalized_value)
# ---------------------------------------------------------------------------

def _v_int(min_: Optional[int] = None, max_: Optional[int] = None) -> Callable[[Any], Tuple[bool, Any]]:
    def check(v: Any) -> Tuple[bool, Any]:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False, None
        try:
            iv = int(v)
        except Exception:  # noqa: BLE001
            return False, None
        if min_ is not None and iv < min_:
            return False, None
        if max_ is not None and iv > max_:
            return False, None
        return True, iv
    return check


def _v_float(min_: Optional[float] = None, max_: Optional[float] = None) -> Callable[[Any], Tuple[bool, Any]]:
    def check(v: Any) -> Tuple[bool, Any]:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False, None
        fv = float(v)
        if min_ is not None and fv < min_:
            return False, None
        if max_ is not None and fv > max_:
            return False, None
        return True, fv
    return check


def _v_bool(v: Any) -> Tuple[bool, Any]:
    if isinstance(v, bool):
        return True, v
    return False, None


def _v_str(allow_empty: bool = True) -> Callable[[Any], Tuple[bool, Any]]:
    def check(v: Any) -> Tuple[bool, Any]:
        if not isinstance(v, str):
            return False, None
        if not allow_empty and not v:
            return False, None
        return True, v
    return check


def _v_dict(v: Any) -> Tuple[bool, Any]:
    if isinstance(v, dict):
        return True, v
    return False, None


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Tunable:
    key: str
    default: Any
    kind: str            # short type tag for docs ("int", "float", "bool", "str", "dict")
    description: str
    validator: Callable[[Any], Tuple[bool, Any]]
    is_user_facing: bool = True   # exposed in the example config + Save Configuration


TUNABLES: Tuple[Tunable, ...] = (
    # --- User-facing settings (round-trip through the loaded config file) ---
    Tunable("display_tz", "", "str",
            "IANA timezone (e.g. 'America/Los_Angeles') applied to intraday timestamps. "
            "Empty = display in Eastern Time (market local).",
            _v_str(allow_empty=True)),
    Tunable("scroll_zoom_invert", False, "bool",
            "Mouse-wheel zoom direction. False = scroll DOWN zooms IN (TradingView). "
            "True = scroll UP zooms IN (macOS natural-scroll).",
            _v_bool),
    Tunable("theme_overrides", {}, "dict",
            "Per-theme color overrides. Sparse merge over the built-in light/dark themes. "
            "Schema: {'light': {key: '#hex', ...}, 'dark': {...}}.",
            _v_dict),
    Tunable("startup_defaults", {}, "dict",
            "Per-key startup overrides. Recognized keys: ticker, compare, interval, source, theme. "
            "Sparse merge over BUILTIN_STARTUP_DEFAULTS in constants.py.",
            _v_dict),
    Tunable("default_window_bars", 200, "int",
            "Number of bars in the right-edge default window (Reset view, fresh load, ticker change). "
            "Larger = more history visible; smaller = more zoomed-in by default.",
            _v_int(min_=10, max_=5000)),
    Tunable("price_top_pad_frac", 0.12, "float",
            "Top headroom on price axes as a fraction of the data span. Reserves space for the always-on "
            "top-left OHLCV readout strip so the highest bar can't collide with it.",
            _v_float(min_=0.0, max_=1.0)),
    Tunable("price_bot_pad_frac", 0.05, "float",
            "Bottom padding on price axes as a fraction of the data span.",
            _v_float(min_=0.0, max_=1.0)),

    # --- Internal perf knobs (NOT included in the example config or
    # surfaced in Save Configuration; advanced users can still set them
    # in a hand-edited config file, but they're undocumented in the UI).
    Tunable("full_cache_size", 16, "int",
            "LRU memory-cache size for fetched (candles, meta) tuples.",
            _v_int(min_=2, max_=256), is_user_facing=False),
    Tunable("hover_throttle_ms", 16, "int",
            "Coalescing window (ms) for hover/crosshair updates. 16 ≈ 60 Hz.",
            _v_int(min_=0, max_=200), is_user_facing=False),
    Tunable("scroll_zoom_factor_per_step", 1.15, "float",
            "Per-notch zoom factor for mouse-wheel zoom (~15% per click at 1.15).",
            _v_float(min_=1.01, max_=2.0), is_user_facing=False),
    Tunable("scroll_zoom_step_clamp", 2.0, "float",
            "Max |event.step| per wheel event to neutralise high-precision trackpads.",
            _v_float(min_=0.1, max_=20.0), is_user_facing=False),
    Tunable("scroll_zoom_min_bars", 3.0, "float",
            "Floor on the visible-bar count when zooming in.",
            _v_float(min_=1.0, max_=100.0), is_user_facing=False),

    # --- Indicators (Phase 1 wiring; UI consumers land in Phase 2/3) ---
    Tunable("indicators", {}, "dict",
            "Indicator state. Schema: "
            "{'presets': {name: [config_dict, ...], ...}, "
            "'active_preset': name|null, "
            "'active_configs': [config_dict, ...]}.",
            _v_dict),
    Tunable("custom_indicators_enabled", False, "bool",
            "Load custom indicator *.py files from the user "
            "drop-in folder at startup. Default False because the "
            "files run with full app privileges.",
            _v_bool),
    Tunable("indicator_last_preset_per_ticker", {}, "dict",
            "Map of ticker symbol -> last-used preset name. Populated "
            "automatically when the user switches presets while "
            "viewing a ticker.",
            _v_dict),

    # --- Historical earnings & dividends ---------------------------------
    # Glyphs at the bottom edge of the price pane (TradingView-style),
    # plus journal proximity flagging. See backtest/replay.spec.md and
    # events/ subpackage for the full design.
    Tunable("show_earnings", True, "bool",
            "Display historical earnings glyphs at the bottom edge of the price pane.",
            _v_bool),
    Tunable("show_dividends", True, "bool",
            "Display historical ex-dividend / split / spinoff glyphs at the bottom "
            "edge of the price pane.",
            _v_bool),
    Tunable("show_upcoming_events", True, "bool",
            "Display upcoming earnings as a right-edge relative-count badge "
            "('Earn T-2 AMC'). Absolute dates are redacted in blind mode regardless "
            "of this setting.",
            _v_bool),
    Tunable("earnings_window_days", 10, "int",
            "Trading-day window for the journal earnings-proximity tag. A trade is "
            "auto-tagged 'earnings_pre_print' or 'earnings_post_print' when its entry "
            "falls within this many trading days of a print.",
            _v_int(min_=1, max_=60)),
    Tunable("events_source", "yfinance", "str",
            "Which EVENT_SOURCES key to fetch from. Mirrors the data source registry; "
            "future Schwab / Polygon / Alpaca event providers register conditionally on "
            "credentials.",
            _v_str(allow_empty=False)),
    Tunable("pre_earnings_warn_in_journal", True, "bool",
            "Show a passive inline notice at the top of the pre-trade journal when "
            "entering within the earnings_window_days proximity. No extra click.",
            _v_bool),

    # Internal — not surfaced in the example config
    Tunable("events_fetch_ttl_seconds", 43200, "int",
            "TTL on cached upcoming-earnings rows (mutable zone). 12h default. "
            "Past prints with non-NaN actuals are treated as immutable and never re-fetched.",
            _v_int(min_=60, max_=86400 * 7), is_user_facing=False),
    Tunable("events_hover_hit_px", 8, "int",
            "Pixel radius for event-glyph hover hit-test.",
            _v_int(min_=2, max_=40), is_user_facing=False),

    # --- Volume time-of-day shading (1d bars only) -----------------------
    # Outline = full-day envelope, solid fill = realized portion up to
    # the current time-of-day (sandbox clock when active, else wall
    # clock). Lets the trader visually compare "volume at 10am ET" across
    # multiple historical days without a numeric overlay. OFF by default.
    # See gui/volume_tod_overlay.py + spec.md partner for the full design.
    Tunable("volume_tod_enabled", False, "bool",
            "Shade 1d volume bars by time-of-day: full-day outline envelope + "
            "solid fill for the portion of the trading session that has elapsed "
            "(relative to the sandbox replay clock when active, else wall-clock). "
            "Off by default — purely visual, no impact on sandbox determinism.",
            _v_bool),
    Tunable("volume_tod_median_lookback_days", 20, "int",
            "Trading-day lookback for the prior-day median full-day-volume tick "
            "drawn on each shaded bar. The tick is a neutral horizontal line at "
            "the median height — a reference for 'is this day above or below "
            "typical full-day volume'.",
            _v_int(min_=1, max_=252)),

    Tunable("volume_tod_rth_only", True, "bool",
            "Restrict the time-of-day cumulative to RTH (09:30-16:00 ET). "
            "v1 architecture is RTH-only; this knob exists for forward-compat "
            "with a future 'include extended hours' branch.",
            _v_bool, is_user_facing=False),
    Tunable("volume_tod_intraday_interval", "5m", "str",
            "Intraday source granularity for the TOD cumulative. Must be a "
            "valid INTRADAY interval; smaller = finer slot resolution but "
            "more network/cache pressure.",
            _v_str(allow_empty=False), is_user_facing=False),

    # --- Sandbox -------------------------------------------------------
    # The master-clock anchor for replay sessions. SPY is the
    # conventional default because it ticks every regular-hours
    # minute and never gates a day for missing data, but advanced
    # users (futures traders, FX dabblers, single-name specialists)
    # want their own benchmark. Audit ``sandbox-ref-symbol``.
    Tunable("sandbox_reference_symbol", "SPY", "str",
            "Master-clock anchor ticker for sandbox replay sessions. "
            "The bar timestamps from this symbol drive the replay "
            "clock; tradeable tickers are loaded on top mid-session. "
            "Must be a liquid name with continuous intraday coverage "
            "from your selected data source (SPY/QQQ for US equities, "
            "ES=F for futures, EURUSD=X for FX).",
            _v_str(allow_empty=False)),
    Tunable("sandbox_skip_detailed_journal", False, "bool",
            "Skip the mandatory pre-trade journal AND the mandatory "
            "post-trade review modals during sandbox replay. Submitted "
            "orders are stamped with a placeholder thesis "
            "(\"(skipped)\") and an empty review string so the "
            "SessionResult still serialises cleanly; the trader keeps "
            "the rapid scalp-practice loop without losing the option "
            "to journal manually after the fact. Off by default — "
            "the journaling discipline is the whole point of sandbox "
            "for most users. Audit ``mandatory-journal-skip``.",
            _v_bool),

    # --- Startup -------------------------------------------------------
    # Whether the PyInstaller splash should appear at launch.
    # Mirrors the ``TRADINGLAB_NO_SPLASH`` env var / ``--no-splash``
    # CLI flag but gives end users running the frozen .exe a
    # discoverable Settings checkbox instead. Env var / CLI flag
    # still win — they short-circuit before the tunable is even
    # consulted, so the test harness keeps a single off-switch.
    # Audit ``settings-splash-disable``.
    Tunable("splash_enabled", True, "bool",
            "Show the PyInstaller splash screen at startup of the "
            "frozen executable. Off = launch goes straight to the "
            "main window with no overlay. Has no effect in dev mode "
            "(``python -m tradinglab``) — the dev-mode launcher "
            "never builds a splash regardless of this setting.",
            _v_bool),

    # Persisted worker-pool size. ``0`` is the sentinel "auto-detect
    # via ``os.cpu_count()`` (clamped to [1, 64])"; any positive
    # value overrides the auto-detect for the lifetime of the
    # setting, including across app launches. Hardware-dependent so
    # we default to auto-detect — users who explicitly bump the
    # slider in Settings opt into persistence. Audit
    # ``workers-persisted``.
    Tunable("worker_count", 0, "int",
            "Background worker-thread pool size. ``0`` = auto-detect "
            "from ``os.cpu_count()`` (clamped to [1, 64]). Any "
            "positive value overrides the auto-detect for this "
            "machine and persists across launches. Re-launch is not "
            "required — the Settings slider live-swaps the executor.",
            _v_int(min_=0, max_=64)),

    # Maximum number of watchlists that can be pinned as sub-tabs in
    # the Watchlist notebook. Hardcoded 5 historically; the audit
    # asked for either raising the default OR making it user-
    # configurable. We make it configurable (default 5 stays so
    # nothing about existing UX changes unless the user opts in)
    # and let it grow up to 20 for power users with many curated
    # ticker sets. Audit ``pinned-watchlist-cap``.
    Tunable("watchlist_max_pinned", 5, "int",
            "Maximum number of pinned watchlists shown as sub-tabs "
            "in the Watchlist notebook. Default 5 — raise to keep "
            "more curated lists one click away. New value applies "
            "to subsequently-constructed ``WatchlistManager`` "
            "instances (i.e. on next launch).",
            _v_int(min_=1, max_=20)),

    # --- Watchlist polling -------------------------------------------
    # Background poll loop that re-runs the watchlist preload pipeline
    # (last-price + 1d snapshots) so the watchlist Last/Change/Pct
    # columns stay live without the user having to switch charts.
    # Fires every ``watchlist_poll_interval_sec`` seconds during regular
    # US trading hours (09:30–16:00 ET); slowed by
    # ``watchlist_poll_offhours_multiplier`` outside that window. The
    # existing cache-fresh + in-flight-dedup short-circuits keep
    # network load minimal — a steady-state RTH poll on a 5-ticker
    # watchlist with all caches fresh is zero HTTP calls.
    # Set ``watchlist_poll_interval_sec`` to 0 to disable polling
    # entirely (the chart-load preload path still runs).
    # Audit ``watchlist-poll-loop``.
    Tunable("watchlist_poll_interval_sec", 60, "int",
            "Watchlist background poll interval, in seconds. "
            "Re-runs the per-ticker last-price + 1d snapshot fetches "
            "so Last/Change/Pct stay live across the pinned "
            "watchlists. Default 60. Set to 0 to disable. Outside "
            "regular trading hours (09:30–16:00 ET) the effective "
            "interval is multiplied by "
            "``watchlist_poll_offhours_multiplier``.",
            _v_int(min_=0, max_=3600)),
    Tunable("watchlist_poll_offhours_multiplier", 5.0, "float",
            "Multiplier applied to ``watchlist_poll_interval_sec`` "
            "outside regular trading hours (before 09:30 ET, after "
            "16:00 ET, weekends, and US market holidays — handled "
            "approximately by weekend-only check). Default 5.0 "
            "(i.e. a 60s RTH poll becomes 300s off-hours). Set to "
            "1.0 to disable the off-hours slowdown.",
            _v_float(min_=1.0, max_=60.0)),

    # --- BYOD (Bring Your Own Data) ----------------------------------
    # User-supplied historical bars loaded from CSV files on disk.
    # Each "root" is a directory whose top-level subfolders are
    # original sources (yfinance/, polygon/, alpaca/, ...). Each
    # subfolder becomes one entry in the source-selector combobox
    # named "<root-name>-<subfolder>". Files inside a subfolder are
    # flat: <TICKER>_<INTERVAL>.csv with strict canonical schema
    # ``timestamp,open,high,low,close,volume`` (lowercase headers,
    # ISO-8601 timestamps with explicit timezone offset). See
    # docs/LOCAL_DATA.md for the full schema and round-trip workflow.
    # Configure via Tools → Configure Local Data… and export the
    # current disk cache via Tools → Export Bars to CSV….
    Tunable("local_data", {"enabled": False, "roots": []}, "dict",
            "Local CSV data source configuration (BYOD). Schema: "
            "{'enabled': bool, 'roots': [{'name': str, 'path': str}, ...]}. "
            "Each root's top-level subfolders become source-selector "
            "combobox entries named '<root-name>-<subfolder>'. See "
            "docs/LOCAL_DATA.md.",
            _v_dict),
)


# Index by key for O(1) lookup.
_BY_KEY: Dict[str, Tunable] = {t.key: t for t in TUNABLES}

# Resolved cache: populated on first get(), invalidated by reload().
_resolved: Optional[Dict[str, Any]] = None


def _load_overrides() -> Dict[str, Any]:
    """Read settings.json and return validated overrides keyed by tunable name.

    Bad / missing keys are silently dropped (caller falls back to built-in
    default). Unknown keys in settings.json are preserved by ``settings.set``
    on subsequent writes — we just don't look them up here.
    """
    out: Dict[str, Any] = {}
    try:
        raw = _settings.load()
    except Exception:  # noqa: BLE001
        return out
    for t in TUNABLES:
        if t.key not in raw:
            continue
        ok, norm = t.validator(raw[t.key])
        if ok:
            out[t.key] = norm
    return out


def _ensure_loaded() -> Dict[str, Any]:
    global _resolved
    if _resolved is None:
        overrides = _load_overrides()
        merged: Dict[str, Any] = {t.key: t.default for t in TUNABLES}
        merged.update(overrides)
        _resolved = merged
    return _resolved


def get(key: str) -> Any:
    """Return the resolved value for ``key`` (override if valid, else default).

    Raises KeyError if ``key`` is not a registered tunable — this is intentional
    so that typos in consumer code surface immediately rather than silently
    returning None.
    """
    if key not in _BY_KEY:
        raise KeyError(f"defaults.get(): unknown tunable {key!r}. "
                       f"Add it to TUNABLES in defaults.py or fix the typo.")
    return _ensure_loaded()[key]


def describe(key: str) -> Tuple[Any, str, str]:
    """Return ``(default, kind, description)`` for ``key``."""
    t = _BY_KEY[key]
    return (t.default, t.kind, t.description)


def reload() -> None:
    """Drop the cache and re-read ``settings.json`` on next get()."""
    global _resolved
    _resolved = None


def as_markdown_table() -> str:
    """Render the catalog as a GitHub-Flavored Markdown table for README.md."""
    rows = ["| Key | Type | Default | Description |", "|---|---|---|---|"]
    for t in TUNABLES:
        default_repr = "null" if t.default is None else repr(t.default)
        # Escape pipes inside descriptions so they don't break the table.
        desc = t.description.replace("|", "\\|")
        rows.append(f"| `{t.key}` | `{t.kind}` | `{default_repr}` | {desc} |")
    return "\n".join(rows)


def user_facing_keys() -> Tuple[str, ...]:
    """Return the curated subset of keys exported in the example config and
    written by File → Save Configuration… (perf knobs are excluded)."""
    return tuple(t.key for t in TUNABLES if t.is_user_facing)


def example_payload(*, with_comments: bool = True) -> Dict[str, Any]:
    """Build the dict written to ``config/example_config.json``.

    Includes every user-facing tunable at its built-in default, plus
    inline ``_comment_<key>`` annotations explaining each one when
    ``with_comments=True``. JSON has no comment syntax — these are
    stripped on import (any key starting with ``_`` is ignored by
    :func:`tradinglab.settings.import_from_file`).
    """
    out: Dict[str, Any] = {}
    if with_comments:
        out["_comment"] = (
            "tradinglab configuration file. Load via File -> Load Configuration. "
            "Keys starting with underscore are documentation-only and ignored on load. "
            "Delete any key to fall back to its built-in default."
        )
    for t in TUNABLES:
        if not t.is_user_facing:
            continue
        if with_comments:
            out[f"_comment_{t.key}"] = t.description
        out[t.key] = t.default
    return out

