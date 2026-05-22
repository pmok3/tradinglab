"""Colors, themes, interval lookup tables, and small cross-module helpers."""

from __future__ import annotations

from datetime import datetime

# Candlestick body / wick colors.
#
# Two palettes are available:
#
# 1. **Default** (TradingView-ish teal/red): the most common look,
#    matches every screenshot in the README + chart docs.
# 2. **Color-blind-safe** (Okabe-Ito): orange ``#e69f00`` for bull,
#    sky-blue ``#56b4e9`` for bear. Picked from the Okabe-Ito
#    qualitative palette (well-known accessibility reference) so
#    the bull/bear distinction reads cleanly for deuteranopia /
#    protanopia / tritanopia. Bull = warm orange, bear = cool
#    blue keeps the "rising = warm / falling = cool" mental
#    model that traders unconsciously rely on.
#
# The active palette is selected at module-import time from
# ``settings.get("use_colorblind_palette", False)``. Toggling the
# setting at runtime requires a relaunch to fully propagate
# (most call sites read the constant once and cache it). The
# Settings dialog surfaces that limitation as a "Relaunch
# required to fully apply" hint. Audit ``color-blind-palette``.
_DEFAULT_BULL_COLOR = "#26a69a"  # teal-green
_DEFAULT_BEAR_COLOR = "#ef5350"  # coral-red
_COLORBLIND_BULL_COLOR = "#e69f00"  # Okabe-Ito orange
_COLORBLIND_BEAR_COLOR = "#56b4e9"  # Okabe-Ito sky-blue


def _resolve_initial_palette() -> tuple[str, str]:
    """Pick the bull/bear palette based on user setting.

    Defensive against ``settings`` not being importable yet
    (extremely rare — only happens during interpreter teardown
    or in test harnesses that monkey-patch ``sys.modules``).
    Falls back to the default palette on any error.
    """
    try:
        from . import settings as _settings
        if bool(_settings.get("use_colorblind_palette", False)):
            return (_COLORBLIND_BULL_COLOR, _COLORBLIND_BEAR_COLOR)
    except Exception:  # noqa: BLE001
        pass
    return (_DEFAULT_BULL_COLOR, _DEFAULT_BEAR_COLOR)


BULL_COLOR, BEAR_COLOR = _resolve_initial_palette()


LIGHT_THEME: dict = {
    "win_bg": "#f0f0f0",
    "fig_bg": "#fafafa",
    "ax_bg": "#ffffff",
    "text": "#111111",
    "grid": "#cccccc",
    "spine": "#888888",
    "tree_bg": "#ffffff",
    "tree_fg": "#111111",
    "bull_row_bg": "#b2dfdb",
    "bull_row_fg": "#004d40",
    "bear_row_bg": "#ffcdd2",
    "bear_row_fg": "#5a1816",
    "tooltip_bg": "#ffffff",
    "tooltip_fg": "#111111",
    "watermark": "#c8c8c8",
    # Soft vertical bands painted behind pre/post-market candles. Different
    # hues so the user can tell morning and evening sessions apart at a
    # glance — cool blue for pre-market (before open), warm amber for
    # post-market (after close).
    "pre_shade": "#4a6fa5",
    "post_shade": "#c07a2e",
    "crosshair": "#555555",
    # Foreground for disabled menu/button text. Picked from the GitHub
    # "muted" greys so the disabled label still reads against either
    # palette without the Windows-default etched/embossed look that
    # appears blurry on dark backgrounds. Audit ``menu-disabled-fg``.
    "text_disabled": "#8b949e",
}

#: Sentinel key returned as the first row of :func:`build_ttk_style_spec`.
#: Keeps ``_apply_theme`` free of hard-coded widget names so palette tweaks
#: stay in this file.
TTK_ROOT_STYLE = "."


def build_ttk_style_spec(theme: dict) -> list:
    """Return a declarative ``ttk.Style`` spec for the given theme.

    Each entry is a ``(style_name, configure_kwargs, map_kwargs)`` tuple
    consumed by ``ChartApp._apply_ttk_style``. Keeping the spec as plain
    data (rather than a sequence of ``style.configure`` calls) means light
    vs. dark mode differ only in palette: the widget topology lives here.

    ``map_kwargs`` may be empty (``{}``) when a widget doesn't need any
    per-state overrides.
    """
    fg = theme["text"]
    bg = theme["win_bg"]
    ax_bg = theme["ax_bg"]
    tree_bg = theme["tree_bg"]
    tree_fg = theme["tree_fg"]
    spine = theme["spine"]
    return [
        (TTK_ROOT_STYLE,
         dict(background=bg, foreground=fg, fieldbackground=ax_bg),
         {}),
        ("TFrame",
         dict(background=bg),
         {}),
        ("TLabel",
         dict(background=bg, foreground=fg),
         {}),
        ("TButton",
         dict(background=ax_bg, foreground=fg),
         dict(background=[("active", spine)],
              foreground=[("active", fg)])),
        # Destructive variant: idle is red-on-axBg in both themes so the
        # button reads as "danger" without being a wall of red; hover /
        # press inverts to white-on-red for an unambiguous commit cue.
        # Used by the PANIC: Flatten All button and the toolbar Reset
        # View button.
        ("Destructive.TButton",
         dict(background=ax_bg, foreground="#cc3333"),
         dict(background=[("active", "#cc3333"), ("pressed", "#a92929")],
              foreground=[("active", "#ffffff"), ("pressed", "#ffffff")])),
        ("TCheckbutton",
         dict(background=bg, foreground=fg),
         dict(background=[("active", bg)],
              foreground=[("active", fg)])),
        ("TEntry",
         dict(fieldbackground=ax_bg, foreground=fg, insertcolor=fg),
         {}),
        ("TCombobox",
         dict(fieldbackground=ax_bg, background=ax_bg,
              foreground=fg, arrowcolor=fg),
         dict(fieldbackground=[("readonly", ax_bg)],
              foreground=[("readonly", fg)],
              background=[("readonly", ax_bg)])),
        ("TNotebook",
         dict(background=bg, borderwidth=0),
         {}),
        ("TNotebook.Tab",
         dict(background=ax_bg, foreground=fg, padding=(8, 3)),
         dict(background=[("selected", bg)],
              foreground=[("selected", fg)])),
        # Body rows: only map the ``selected`` state. Adding ``active``/
        # ``hover`` here would override per-row bull/bear
        # ``tag_configure`` colors (state maps beat tag styles), so
        # leave row hover to native behavior and let the tags own
        # the per-row tint.
        ("Treeview",
         dict(background=tree_bg, foreground=tree_fg,
              fieldbackground=tree_bg, bordercolor=spine),
         dict(background=[("selected", spine)],
              foreground=[("selected", fg)])),
        # Heading hover/active/pressed fall back to the OS default
        # (light grey) without an explicit map, flashing through dark
        # mode. Pin every state to the palette spine color.
        ("Treeview.Heading",
         dict(background=ax_bg, foreground=fg, bordercolor=spine),
         dict(background=[("active", spine), ("pressed", spine),
                          ("hover", spine)],
              foreground=[("active", fg), ("pressed", fg),
                          ("hover", fg)])),
        # ttk container widgets that were previously falling back to
        # the OS default palette (which renders light-grey on dark
        # mode). Without these, the Entries / Watchlist tabs and any
        # other panel that uses ``ttk.LabelFrame`` / ``ttk.PanedWindow``
        # / ``ttk.Scrollbar`` look unthemed in dark mode. Audit
        # ``ttk-container-dark``.
        ("TLabelframe",
         dict(background=bg, bordercolor=spine),
         {}),
        ("TLabelframe.Label",
         dict(background=bg, foreground=fg),
         {}),
        ("TPanedwindow",
         dict(background=bg),
         {}),
        # ``TPanedwindow`` separator widget used by the clam theme.
        # Painting the sash with the ``spine`` colour gives a subtle
        # but visible divider line.
        ("Sash",
         dict(background=spine, sashthickness=4),
         {}),
        ("TScrollbar",
         dict(background=ax_bg, troughcolor=bg,
              bordercolor=spine, arrowcolor=fg),
         dict(background=[("active", spine), ("pressed", spine)],
              arrowcolor=[("active", fg), ("pressed", fg)])),
        ("TSpinbox",
         dict(fieldbackground=ax_bg, background=ax_bg,
              foreground=fg, arrowcolor=fg, insertcolor=fg),
         dict(fieldbackground=[("readonly", ax_bg)],
              foreground=[("readonly", fg)])),
    ]


def ttk_combobox_listbox_options(theme: dict) -> dict:
    """Return option-database keys for the readonly Combobox popdown.

    The dropdown list under a readonly ``ttk.Combobox`` is a plain Tk
    ``Listbox`` driven by the option database (not ``ttk.Style``). Colors
    must be pushed via ``root.option_add`` or the popdown will render in
    OS-default white in dark mode.
    """
    return {
        "*TCombobox*Listbox.background": theme["ax_bg"],
        "*TCombobox*Listbox.foreground": theme["text"],
        "*TCombobox*Listbox.selectBackground": theme["spine"],
        "*TCombobox*Listbox.selectForeground": theme["text"],
    }


DARK_THEME: dict = {
    "win_bg": "#1e1e1e",
    "fig_bg": "#1e1e1e",
    "ax_bg": "#2b2b2b",
    "text": "#dcdcdc",
    "grid": "#444444",
    "spine": "#666666",
    "tree_bg": "#2b2b2b",
    "tree_fg": "#dcdcdc",
    "bull_row_bg": "#2a524d",
    "bull_row_fg": "#a7f3e4",
    "bear_row_bg": "#5a2d2d",
    "bear_row_fg": "#ffc2be",
    "tooltip_bg": "#2b2b2b",
    "tooltip_fg": "#dcdcdc",
    "watermark": "#5a5a5a",
    "pre_shade": "#8ab4f8",
    "post_shade": "#e8a95c",
    "crosshair": "#ffffff",
    # Foreground for disabled menu/button text. See ``LIGHT_THEME``
    # comment — dark-palette counterpart from the GitHub "muted" greys.
    "text_disabled": "#6e7681",
}


#: Canonical mapping from user-facing theme mode name to the base palette.
#: Lets callers look up a palette by mode string instead of ternary-ing
#: on ``dark_var.get()``.
DEFAULT_THEMES: dict = {
    "light": LIGHT_THEME,
    "dark": DARK_THEME,
}


#: Palette slots exposed to end-user customization in the Settings dialog.
#: Each entry is ``(theme_key, display_label)``. Intentionally a curated
#: subset of the ~16 theme keys — the ones with the highest visual
#: impact — so the dialog stays approachable. Non-listed keys always
#: take their base-theme value, both when the user hasn't overridden
#: them and when the override dict contains arbitrary unknown keys.
CUSTOMIZABLE_THEME_KEYS: list = [
    ("win_bg", "Window background"),
    ("ax_bg", "Chart background"),
    ("text", "Text color"),
    ("grid", "Gridlines"),
    ("bull_row_bg", "Bull row tint"),
    ("bear_row_bg", "Bear row tint"),
]


def resolve_theme(mode: str, overrides: dict | None) -> dict:
    """Return the effective palette for ``mode`` after applying user overrides.

    ``mode`` is ``"light"`` or ``"dark"``. ``overrides`` is the nested
    ``{mode: {key: color}}`` dict persisted in ``settings.json`` under
    the ``"theme_overrides"`` key; may be ``None`` or missing the mode.

    Merges shallowly onto a **copy** of the base palette. Only keys
    present in :data:`CUSTOMIZABLE_THEME_KEYS` are honored, so a
    hand-edited settings file can't inject arbitrary (and possibly
    mistyped) keys into downstream consumers' palette dicts.
    Non-string values are silently discarded for the same reason.
    """
    base = DEFAULT_THEMES.get(mode, LIGHT_THEME)
    mode_overrides = (overrides or {}).get(mode) or {}
    if not isinstance(mode_overrides, dict) or not mode_overrides:
        return dict(base)
    allowed = {k for k, _ in CUSTOMIZABLE_THEME_KEYS}
    merged = dict(base)
    for k, v in mode_overrides.items():
        if k in allowed and isinstance(v, str):
            merged[k] = v
    return merged


# --- startup defaults (Settings → "Startup parameters") -----------------
#
# Hard-coded fallbacks if no user override is present. ``interval`` is
# ``"1d"`` because daily candles are the most-used timeframe for a fresh
# session; the historical 5m default was a holdover from initial dev.
# ``ticker`` is AMD — a liquid, well-known mid/large-cap that the
# primary maintainer uses as their day-to-day reference symbol. New
# users with no opinion can change it in Settings → Startup parameters.
BUILTIN_STARTUP_DEFAULTS: dict = {
    "ticker": "AMD",
    "compare": "SPY",
    "interval": "1d",
    "source": "yfinance",
    "theme": "light",
}

# Drives the Settings dialog row order + labels. Choices for ``interval``
# and ``source`` are runtime-resolved (intervals from ``app._INTERVALS``,
# sources from ``data.DATA_SOURCES``) so this list stays declarative and
# doesn't import the data package.
STARTUP_DEFAULT_KEYS: list = [
    ("ticker",   "Default primary ticker"),
    ("compare",  "Default compare ticker"),
    ("interval", "Default interval"),
    ("source",   "Default data source"),
    ("theme",    "Default theme (light/dark)"),
]

_STARTUP_THEME_CHOICES = ("light", "dark")


def resolve_startup_defaults(
    overrides: dict | None, *,
    intervals: list | tuple | set | None = None,
    sources: list | tuple | set | None = None,
) -> dict:
    """Merge sparse ``overrides`` over :data:`BUILTIN_STARTUP_DEFAULTS`.

    Each override is validated against a per-key allow-list:

    * ``ticker`` / ``compare`` — any non-empty string (uppercased).
    * ``interval`` — must be in ``intervals`` when supplied; otherwise
      any non-empty string is accepted.
    * ``source`` — must be in ``sources`` when supplied; otherwise
      any non-empty string is accepted.
    * ``theme`` — must be ``"light"`` or ``"dark"``.

    Invalid or missing entries fall back to the builtin value. This is
    the same guard pattern used by :func:`resolve_theme` so a corrupt
    or hand-edited ``settings.json`` can't inject unsupported values
    into the chart's startup state.
    """
    base = dict(BUILTIN_STARTUP_DEFAULTS)
    if not isinstance(overrides, dict) or not overrides:
        return base
    valid_intervals = set(intervals) if intervals else None
    valid_sources = set(sources) if sources else None
    for key in BUILTIN_STARTUP_DEFAULTS.keys():
        v = overrides.get(key)
        if not isinstance(v, str) or not v:
            continue
        if key in ("ticker", "compare"):
            base[key] = v.strip().upper()
            continue
        if key == "interval":
            if valid_intervals is None or v in valid_intervals:
                base[key] = v
            continue
        if key == "source":
            if valid_sources is None or v in valid_sources:
                base[key] = v
            continue
        if key == "theme":
            if v in _STARTUP_THEME_CHOICES:
                base[key] = v
            continue
    return base


# Supported time intervals mapped to a yfinance period string that yields
# enough history while respecting yfinance's per-interval limits.
INTERVAL_PERIODS: dict = {
    "1m":  "7d",
    "2m":  "60d",
    "5m":  "60d",
    "15m": "60d",
    "30m": "60d",
    "1h":  "730d",
    "1d":  "2y",
    "1wk": "10y",
    "1mo": "max",
}


# --- main-window pane layout -----------------------------------------------
#
# Fraction of the main window the *chart* pane should occupy at every
# launch. The remainder ``(1 - CHART_PANE_STARTUP_RATIO)`` goes to the
# right-side notebook (Primary OHLC / Compare / Watchlist / Sandbox /
# Scanner / Entries / Exits). When the ChartStack panel is enabled and
# becomes the third (leftmost) pane, ``CHARTSTACK_PANE_STARTUP_WIDTH_PX``
# is carved off the left of the window first, and the remaining width
# is split between chart and notebook in the same chart:notebook ratio.
#
# Why a hardcoded constant rather than a saved-sash restore: the
# default 70% from earlier sprints left the watchlist eating ~30% on
# wide monitors which is visually unbalanced — the user's primary
# focus is the chart. 80% gives the chart the lion's share while
# still leaving ~384 px (on a 1920-wide monitor) for the watchlist —
# enough for the 6-column OHLC tree without horizontal scrolling.
#
# This constant is applied at every startup (see ``app.py``
# ``_restore_main_paned_sashes``), so the chart always opens wide
# even if a prior session's drag left the sash in an awkward
# position. Users who want a different split can drag the sash
# during a session — it just won't persist across launches.
CHART_PANE_STARTUP_RATIO: float = 0.80

#: Width in pixels reserved for the ChartStack card column when the
#: 3-pane layout is active. Matches ``chartstack.card_width_px`` so
#: the column is sized to comfortably show one card width.
CHARTSTACK_PANE_STARTUP_WIDTH_PX: int = 220


def compute_main_paned_sashes(
    main_w: int,
    *,
    chartstack_visible: bool,
    notebook_min_px: int = 280,
    chart_min_px: int = 200,
) -> list[int]:
    """Compute cumulative sash x-positions for ``app._main_paned``.

    Returns the list of sash positions in left-to-right cumulative
    pixels (the format ``PanedWindow.sashpos(i, x)`` expects):

    * 2-pane (CS off): ``[chart_w]``  — order: ``[chart | notebook]``
    * 3-pane (CS on):  ``[cs_w, cs_w + chart_w]``  — order:
      ``[chartstack | chart | notebook]``

    **Invariant (the point of this helper):** the notebook column has
    the *same absolute width* in both modes:

    .. code-block:: text

        notebook_w = max(notebook_min_px,
                         int(main_w * (1 - CHART_PANE_STARTUP_RATIO)))

    Toggling ChartStack on/off therefore does NOT rebalance the
    watchlist column — it only steals
    ``CHARTSTACK_PANE_STARTUP_WIDTH_PX`` of pixels from the chart.
    Previously the toggle path used a ``restore_sash`` default that
    carved the notebook from the *remaining* width, which on the
    first toggle-on shrank the chart by ~30 % and on subsequent
    toggles surfaced whatever drift the user's prior drag had
    persisted to ``geometry.json``. Both behaviours are now bypassed.

    ``chart_min_px`` is a defensive floor: on absurdly narrow windows
    the helper sacrifices notebook width before chart width so the
    chart stays usable.
    """
    notebook_w = max(notebook_min_px,
                     main_w - int(main_w * CHART_PANE_STARTUP_RATIO))
    if chartstack_visible:
        cs_w = CHARTSTACK_PANE_STARTUP_WIDTH_PX
        chart_w = main_w - cs_w - notebook_w
        if chart_w < chart_min_px:
            chart_w = chart_min_px
            notebook_w = max(0, main_w - cs_w - chart_w)
        return [cs_w, cs_w + chart_w]
    chart_w = main_w - notebook_w
    if chart_w < chart_min_px:
        chart_w = chart_min_px
    return [chart_w]


# Intervals that represent intraday aggregations. Pre-market / post-market
# sessions only exist at these granularities; for daily+ bars the concept
# is meaningless (one bar already spans the whole trading day).
INTRADAY_INTERVALS = frozenset({"1m", "2m", "5m", "15m", "30m", "1h"})


def is_intraday(interval: str) -> bool:
    """Return True if ``interval`` produces intraday (sub-daily) bars."""
    return interval in INTRADAY_INTERVALS


# US equity session boundaries (Eastern time). Pre: 04:00–09:30, regular:
# 09:30–16:00, post: 16:00–20:00. Anything outside is classified as "pre"
# (overnight counts as next-day pre-market for simplicity).
def classify_session(hour: int, minute: int) -> str:
    """Classify a wall-clock time (US Eastern) into pre/regular/post."""
    minutes = hour * 60 + minute
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "regular"
    if 16 * 60 <= minutes < 20 * 60:
        return "post"
    return "pre"


def interval_minutes(interval: str) -> int:
    """Return ``interval`` as an integer number of minutes.

    Only defined for intraday intervals (``1m``/``2m``/.../``1h``).
    Raises ``ValueError`` otherwise — daily+ timeframes don't have a
    fixed minute count and callers that reach this on a daily interval
    are almost certainly buggy.
    """
    if interval.endswith("m"):
        return int(interval[:-1])
    if interval.endswith("h"):
        return int(interval[:-1]) * 60
    raise ValueError(f"Not an intraday interval: {interval}")


def floor_to_interval(when: datetime, step_min: int) -> datetime:
    """Floor ``when`` down to the nearest ``step_min``-minute boundary.

    Used wherever we need to line a timestamp up with exchange bar
    boundaries (e.g. 5m bars open at :00/:05/:10). Seconds and
    microseconds are always zeroed.
    """
    total = when.hour * 60 + when.minute
    floored = (total // step_min) * step_min
    return when.replace(
        hour=floored // 60, minute=floored % 60, second=0, microsecond=0
    )
