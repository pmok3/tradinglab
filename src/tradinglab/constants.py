"""Colors, themes, interval lookup tables, and small cross-module helpers."""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, field
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

#: Canonical *pale* bull/bear tints — the desaturated companions of the
#: bull/bear hue used by the MACD 4-class histogram (falling-above-zero /
#: rising-below-zero) and the light-theme watchlist row backgrounds. Kept
#: here so they live in exactly ONE place; every consumer derives its
#: Okabe-Ito variant from these via :func:`sentiment_recolor`. Audit
#: ``color-blind-palette-audit``.
_BULL_TINT_PALE = "#b2dfdb"  # pale teal-green
_BEAR_TINT_PALE = "#ffcdd2"  # pale coral-red


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


# ---------------------------------------------------------------------------
# Directional-sentiment palette plumbing (audit ``color-blind-palette-audit``)
# ---------------------------------------------------------------------------
# Every color that encodes *market direction* (bull/bear, up/down, gain/loss,
# rising/falling, MFE/MAE) must follow the Okabe-Ito toggle. The toggle
# mutates ``BULL_COLOR`` / ``BEAR_COLOR`` above at runtime; the helpers here
# are the single chokepoint every directional color routes through so a
# green/red literal can be recoloured to the active bull/bear hue without a
# relaunch. Status colors (error/warn/info/ok) are a DIFFERENT semantic axis
# and intentionally do NOT route through here.


def colorblind_palette_active() -> bool:
    """True when the Okabe-Ito color-blind palette is the live palette.

    Reads the live ``BULL_COLOR`` (which the setter mutates) rather than
    the persisted setting, so it reflects the in-session toggle state.
    """
    return BULL_COLOR == _COLORBLIND_BULL_COLOR


def _hex_to_rgb01(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0,
            int(h[4:6], 16) / 255.0)


def _rgb01_to_hex(r: float, g: float, b: float) -> str:
    def _clamp(v: float) -> int:
        return max(0, min(255, round(v * 255)))
    return f"#{_clamp(r):02x}{_clamp(g):02x}{_clamp(b):02x}"


def recolor_to_hue(base_hex: str, hue_source_hex: str) -> str:
    """Return ``base_hex`` recoloured to ``hue_source_hex``'s hue.

    Preserves ``base_hex``'s lightness AND saturation (HLS space) and
    borrows only the *hue* from ``hue_source_hex``. This lets a carefully
    tuned green/red *tint* (a pale row background, a faded histogram
    class) be swung onto the Okabe-Ito orange/blue hue while keeping its
    original tone, so the result still reads as a subtle tint rather than
    a saturated slab. Invalid hex inputs fall through to ``base_hex``.
    """
    try:
        br, bg, bb = _hex_to_rgb01(base_hex)
        sr, sg, sb = _hex_to_rgb01(hue_source_hex)
    except (ValueError, IndexError):
        return base_hex
    _bh, bl, bs = colorsys.rgb_to_hls(br, bg, bb)
    sh, _sl, _ss = colorsys.rgb_to_hls(sr, sg, sb)
    nr, ng, nb = colorsys.hls_to_rgb(sh, bl, bs)
    return _rgb01_to_hex(nr, ng, nb)


def sentiment_recolor(base_hex: str, *, bullish: bool) -> str:
    """Map a directional green/red color onto the active bull/bear hue.

    When the default palette is active this is a **pass-through** (returns
    ``base_hex`` unchanged) so the default appearance stays pixel-exact.
    When the Okabe-Ito palette is active, ``base_hex`` is recoloured to the
    live ``BULL_COLOR`` (``bullish=True``) or ``BEAR_COLOR`` hue, preserving
    its lightness/saturation. This is the one function every directional
    color reference should funnel through.
    """
    if not colorblind_palette_active():
        return base_hex
    return recolor_to_hue(base_hex, BULL_COLOR if bullish else BEAR_COLOR)


def macd_histogram_palette() -> tuple[str, str, str, str]:
    """Live 4-class MACD histogram palette, Okabe-Ito-aware.

    Order ``(rising_above, falling_above, rising_below, falling_below)``
    zero — i.e. ``(strong_bull, weak_bull, weak_bear, strong_bear)``.
    Derived from the live ``BULL_COLOR`` / ``BEAR_COLOR`` plus the pale
    tints so the whole histogram follows the palette toggle.
    """
    return (
        BULL_COLOR,
        sentiment_recolor(_BULL_TINT_PALE, bullish=True),
        sentiment_recolor(_BEAR_TINT_PALE, bullish=False),
        BEAR_COLOR,
    )


def is_app_macd_palette(palette: tuple[str, ...]) -> bool:
    """True when ``palette`` is the app's standard MACD histogram palette.

    Recognises BOTH the default green/red 4-tuple and the Okabe-Ito
    orange/blue variant. The histogram renderer uses this to decide
    whether to swap a class's ``histogram_palette`` for the live,
    palette-aware :func:`macd_histogram_palette` (so the app's own MACD
    follows the color-blind toggle) or to honour a genuinely custom
    4-tuple a future indicator might pin.
    """
    p = tuple(c.lower() for c in palette)
    default = (
        _DEFAULT_BULL_COLOR, _BULL_TINT_PALE,
        _BEAR_TINT_PALE, _DEFAULT_BEAR_COLOR,
    )
    okabe = (
        _COLORBLIND_BULL_COLOR,
        recolor_to_hue(_BULL_TINT_PALE, _COLORBLIND_BULL_COLOR),
        recolor_to_hue(_BEAR_TINT_PALE, _COLORBLIND_BEAR_COLOR),
        _COLORBLIND_BEAR_COLOR,
    )
    return p == tuple(c.lower() for c in default) or \
        p == tuple(c.lower() for c in okabe)


def bull_row_bg(theme: dict) -> str:
    """Watchlist/table bull-row background tint for ``theme``, palette-aware."""
    return sentiment_recolor(
        theme.get("bull_row_bg", BULL_COLOR), bullish=True)


def bear_row_bg(theme: dict) -> str:
    """Watchlist/table bear-row background tint for ``theme``, palette-aware."""
    return sentiment_recolor(
        theme.get("bear_row_bg", BEAR_COLOR), bullish=False)


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
    # Border color for clickable input widgets (TEntry / TCombobox /
    # TSpinbox). Distinct from ``spine`` (chart axis lines) because
    # chart spines can be subtle but input outlines MUST pop or users
    # can't see where to click. Picked for high contrast against
    # ``ax_bg``. Audit ``input-border-visible``.
    "input_border": "#7a7a7a",
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
    disabled_fg = theme.get("text_disabled", fg)
    bg = theme["win_bg"]
    ax_bg = theme["ax_bg"]
    tree_bg = theme["tree_bg"]
    tree_fg = theme["tree_fg"]
    spine = theme["spine"]
    chrome = dict(
        bordercolor=spine,
        lightcolor=spine,
        darkcolor=spine,
        selectbackground=spine,
        selectforeground=fg,
        troughcolor=bg,
    )
    field_chrome = dict(chrome, fieldbackground=ax_bg)
    flat_chrome = dict(chrome, borderwidth=1, relief="flat")
    # Input chrome — for clickable input widgets (TEntry / TCombobox /
    # TSpinbox) that need a visibly distinct outline so users can see
    # the tap target. Uses ``input_border`` (separate palette key) for
    # all three border layers + ``borderwidth=1, relief="solid"`` so the
    # outline reads as a clean 1px frame in both light and dark modes.
    # Falls back to ``spine`` if a theme dict omits ``input_border``
    # (back-compat for third-party / older theme palettes).
    # Audit ``input-border-visible``: regression of commit 536fe6c
    # which had collapsed the 3D bevel to a single ``spine`` color and
    # made input outlines invisible against the field background.
    input_border = theme.get("input_border", spine)
    input_chrome = dict(
        fieldbackground=ax_bg,
        bordercolor=input_border,
        lightcolor=input_border,
        darkcolor=input_border,
        selectbackground=spine,
        selectforeground=fg,
        troughcolor=bg,
        borderwidth=1,
        relief="solid",
    )
    selection_map = dict(
        selectbackground=[("disabled", spine), ("!disabled", spine)],
        selectforeground=[("disabled", disabled_fg), ("!disabled", fg)],
    )
    button_chrome_map = dict(
        **selection_map,
        bordercolor=[("disabled", spine), ("active", spine),
                     ("pressed", spine), ("alternate", spine),
                     ("focus", spine)],
        lightcolor=[("disabled", spine), ("active", spine),
                    ("pressed", spine), ("focus", spine)],
        darkcolor=[("disabled", spine), ("active", spine),
                   ("pressed", spine), ("focus", spine)],
    )
    return [
        (TTK_ROOT_STYLE,
         dict(background=bg, foreground=fg, **field_chrome),
         selection_map),
        ("TFrame",
         dict(background=bg, **chrome),
         selection_map),
        ("TLabel",
         dict(background=bg, foreground=fg, **chrome),
         dict(foreground=[("disabled", disabled_fg)], **selection_map)),
        ("TButton",
         dict(background=ax_bg, foreground=fg, **flat_chrome),
         dict(
             background=[("disabled", ax_bg), ("active", spine),
                         ("pressed", spine)],
             foreground=[("disabled", disabled_fg), ("active", fg),
                         ("pressed", fg)],
             **button_chrome_map,
         )),
        # Destructive variant: idle is red-on-axBg in both themes so the
        # button reads as "danger" without being a wall of red; hover /
        # press inverts to white-on-red for an unambiguous commit cue.
        # Used by the PANIC: Flatten All button and the toolbar Reset
        # View button.
        ("Destructive.TButton",
         dict(background=ax_bg, foreground="#cc3333", **flat_chrome),
         dict(
             background=[("disabled", ax_bg), ("active", "#cc3333"),
                         ("pressed", "#a92929")],
             foreground=[("disabled", disabled_fg), ("active", "#ffffff"),
                         ("pressed", "#ffffff")],
             bordercolor=[("disabled", spine), ("active", "#cc3333"),
                          ("pressed", "#a92929"), ("focus", spine)],
             lightcolor=[("disabled", spine), ("active", "#cc3333"),
                         ("pressed", "#a92929")],
             darkcolor=[("disabled", spine), ("active", "#cc3333"),
                        ("pressed", "#a92929")],
             **selection_map,
         )),
        ("TCheckbutton",
         dict(background=bg, foreground=fg, **chrome),
         dict(background=[("active", bg), ("pressed", bg)],
              foreground=[("disabled", disabled_fg), ("active", fg),
                          ("pressed", fg)],
              **selection_map)),
        # ``TRadiobutton`` mirrors ``TCheckbutton`` — pinned to the
        # window-background on every state so the LABEL never grows a
        # light-grey hover halo in dark mode. The radio indicator
        # circle is painted separately by the theme engine so it still
        # reacts to ``active``/``pressed`` (filled when selected, etc.);
        # we only suppress the label-bg sweep. Audit
        # ``radio-hover-dark``.
        ("TRadiobutton",
         dict(background=bg, foreground=fg, **chrome),
         dict(background=[("active", bg), ("pressed", bg)],
              foreground=[("disabled", disabled_fg), ("active", fg),
                          ("pressed", fg)],
              **selection_map)),
        ("TEntry",
         dict(foreground=fg, insertcolor=fg, **input_chrome),
         dict(fieldbackground=[("disabled", ax_bg), ("readonly", ax_bg)],
              foreground=[("disabled", disabled_fg), ("readonly", fg)],
              **selection_map)),
        ("TCombobox",
         dict(background=ax_bg, foreground=fg, arrowcolor=fg,
              insertcolor=fg, **input_chrome),
         dict(fieldbackground=[("disabled", ax_bg), ("readonly", ax_bg)],
              foreground=[("disabled", disabled_fg), ("readonly", fg)],
              background=[("disabled", ax_bg), ("readonly", ax_bg),
                          ("active", spine)],
              arrowcolor=[("disabled", disabled_fg), ("active", fg)],
              **selection_map)),
        ("TNotebook",
         dict(background=bg, borderwidth=0, **chrome),
         selection_map),
        ("TNotebook.Tab",
         dict(background=ax_bg, foreground=fg, padding=(8, 3), **chrome),
         dict(background=[("selected", bg), ("active", spine)],
              foreground=[("disabled", disabled_fg), ("selected", fg),
                          ("active", fg)],
              bordercolor=[("selected", spine), ("active", spine)],
              lightcolor=[("selected", spine), ("active", spine)],
              darkcolor=[("selected", spine), ("active", spine)],
              **selection_map)),
        # Body rows: only map the ``selected`` state. Adding ``active``/
        # ``hover`` here would override per-row bull/bear
        # ``tag_configure`` colors (state maps beat tag styles), so
        # leave row hover to native behavior and let the tags own
        # the per-row tint.
        ("Treeview",
         dict(background=tree_bg, foreground=tree_fg,
              fieldbackground=tree_bg, **chrome),
         dict(background=[("selected", spine)],
              foreground=[("selected", fg)],
              bordercolor=[("focus", spine)],
              lightcolor=[("focus", spine)],
              darkcolor=[("focus", spine)],
              **selection_map)),
        # Heading hover/active/pressed fall back to the OS default
        # (light grey) without an explicit map, flashing through dark
        # mode. Pin every state to the palette spine color.
        ("Treeview.Heading",
         dict(background=ax_bg, foreground=fg, **flat_chrome),
         dict(background=[("active", spine), ("pressed", spine),
                          ("hover", spine)],
              foreground=[("active", fg), ("pressed", fg),
                          ("hover", fg)],
              bordercolor=[("active", spine), ("pressed", spine),
                           ("hover", spine)],
              lightcolor=[("active", spine), ("pressed", spine),
                          ("hover", spine)],
              darkcolor=[("active", spine), ("pressed", spine),
                         ("hover", spine)],
              **selection_map)),
        # ttk container widgets that were previously falling back to
        # the OS default palette (which renders light-grey on dark
        # mode). Without these, the Entries / Watchlist tabs and any
        # other panel that uses ``ttk.LabelFrame`` / ``ttk.PanedWindow``
        # / ``ttk.Scrollbar`` look unthemed in dark mode. Audit
        # ``ttk-container-dark``.
        ("TLabelframe",
         dict(background=bg, **flat_chrome),
         selection_map),
        ("TLabelframe.Label",
         dict(background=bg, foreground=fg, **chrome),
         dict(foreground=[("disabled", disabled_fg)], **selection_map)),
        ("TPanedwindow",
         dict(background=bg, **chrome),
         selection_map),
        # ``TPanedwindow`` separator widget used by the clam theme.
        # Painting the sash with the ``spine`` colour gives a subtle
        # but visible divider line.
        ("Sash",
         dict(background=spine, sashthickness=4, **flat_chrome),
         selection_map),
        ("TScrollbar",
         dict(background=ax_bg, arrowcolor=fg, **flat_chrome),
         dict(background=[("disabled", ax_bg), ("active", spine),
                          ("pressed", spine)],
              arrowcolor=[("disabled", disabled_fg), ("active", fg),
                          ("pressed", fg)],
              bordercolor=[("disabled", spine), ("active", spine),
                           ("pressed", spine)],
              lightcolor=[("disabled", spine), ("active", spine),
                          ("pressed", spine)],
              darkcolor=[("disabled", spine), ("active", spine),
                         ("pressed", spine)],
              **selection_map)),
        ("TSpinbox",
         dict(background=ax_bg, foreground=fg, arrowcolor=fg,
              insertcolor=fg, **input_chrome),
         dict(fieldbackground=[("disabled", ax_bg), ("readonly", ax_bg)],
              foreground=[("disabled", disabled_fg), ("readonly", fg)],
              background=[("disabled", ax_bg), ("readonly", ax_bg),
                          ("active", spine)],
              arrowcolor=[("disabled", disabled_fg), ("active", fg)],
              **selection_map)),
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
    # Border color for clickable input widgets. See ``LIGHT_THEME``
    # comment — high contrast against ``ax_bg`` (#2b2b2b) so users can
    # see input tap targets at a glance.
    "input_border": "#9a9a9a",
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


# ---------------------------------------------------------------------------
# Built-in palette presets (light + dark + 6 popular community palettes)
# ---------------------------------------------------------------------------
# These are exposed to the user via the Theme Editor's Presets strip.
# Each entry covers all six CUSTOMIZABLE_THEME_KEYS so a switch is a
# complete repaint (no leftover slot from the previous preset). When
# the user clicks a preset, the Theme Editor swaps the active mode +
# replaces that mode's override dict atomically.
#
# Palette sources (community standards): Solarized — Ethan Schoonover;
# Nord — Arctic Ice Studio; Dracula — Zeno Rocha; Gruvbox — morhetz;
# Monokai — Wimer Hazenberg; Material Ocean — Material Theme team.
# Bull/bear-tint slots stay native-feeling within each palette family
# rather than literal "green/red" because some palettes (Solarized,
# Bloomberg) deliberately avoid the saturated chart-pattern combo.


@dataclass(frozen=True)
class ThemePreset:
    """One named built-in theme.

    ``label`` is the UI text (e.g. ``"Dracula"``); ``mode`` is
    ``"light"`` or ``"dark"`` and controls whether ``dark_var``
    flips when the preset is applied; ``overrides`` is the dict
    of ``CUSTOMIZABLE_THEME_KEYS`` colour values.
    """

    label: str
    mode: str
    overrides: dict = field(default_factory=dict)


# Bloomberg-terminal black + amber. The classic.
_BLOOMBERG_DARK: dict = {
    "win_bg": "#000000",
    "ax_bg": "#0a0a0a",
    "text": "#ffb000",
    "grid": "#3a2a00",
    "bull_row_bg": "#1f3a1a",
    "bear_row_bg": "#3a1a1a",
}

# Solarized Light — warm parchment background + dark teal text.
# Same colour theory as the dark variant; light backplate.
_SOLARIZED_LIGHT: dict = {
    "win_bg": "#fdf6e3",
    "ax_bg": "#eee8d5",
    "text": "#073642",
    "grid": "#93a1a1",
    "bull_row_bg": "#d5e8c4",
    "bear_row_bg": "#f0c7c7",
}

# Solarized Dark — Ethan Schoonover's 16-colour palette, dark variant.
_SOLARIZED_DARK: dict = {
    "win_bg": "#002b36",
    "ax_bg": "#073642",
    "text": "#93a1a1",
    "grid": "#586e75",
    "bull_row_bg": "#1f4d3a",
    "bear_row_bg": "#4d1f1f",
}

# Nord — Arctic Ice Studio's frost+aurora palette. Calm bluish dark.
_NORD_DARK: dict = {
    "win_bg": "#2e3440",
    "ax_bg": "#3b4252",
    "text": "#eceff4",
    "grid": "#4c566a",
    "bull_row_bg": "#3b5a4b",
    "bear_row_bg": "#5a3b3b",
}

# Dracula — Zeno Rocha's iconic dark palette. Deep purple+cyan.
_DRACULA_DARK: dict = {
    "win_bg": "#282a36",
    "ax_bg": "#1e1f29",
    "text": "#f8f8f2",
    "grid": "#44475a",
    "bull_row_bg": "#3a5a40",
    "bear_row_bg": "#5a3a40",
}

# Gruvbox Dark — morhetz's retro warm-brown palette.
_GRUVBOX_DARK: dict = {
    "win_bg": "#282828",
    "ax_bg": "#1d2021",
    "text": "#ebdbb2",
    "grid": "#504945",
    "bull_row_bg": "#3a4a2a",
    "bear_row_bg": "#5a2a2a",
}

# Monokai — Wimer Hazenberg's TextMate classic. Dark warm grey + green/pink.
_MONOKAI_DARK: dict = {
    "win_bg": "#272822",
    "ax_bg": "#1e1f1c",
    "text": "#f8f8f2",
    "grid": "#49483e",
    "bull_row_bg": "#3a5a2a",
    "bear_row_bg": "#5a2a3a",
}

# Material Ocean — Material Theme team's deep-blue palette. Like Nord but
# bluer and more saturated; popular VS Code / Atom theme.
_MATERIAL_OCEAN_DARK: dict = {
    "win_bg": "#0f111a",
    "ax_bg": "#1b1e2b",
    "text": "#a6accd",
    "grid": "#3b3f51",
    "bull_row_bg": "#1c3a3a",
    "bear_row_bg": "#3a1c2a",
}


#: Canonical preset registry. The Theme Editor reads this directly
#: and turns each entry into a button on the Presets strip. Order is
#: preserved in the UI so put new entries somewhere intentional.
#:
#: "Default Light" / "Default Dark" are sentinel presets — they don't
#: override anything; applying one just clears the target mode's
#: overrides + flips dark_var. The remaining entries replace the
#: target mode's overrides atomically.
PRESET_THEMES: tuple = (
    ThemePreset(label="Default Light",   mode="light", overrides={}),
    ThemePreset(label="Default Dark",    mode="dark",  overrides={}),
    ThemePreset(label="Bloomberg",       mode="dark",  overrides=_BLOOMBERG_DARK),
    ThemePreset(label="Solarized Light", mode="light", overrides=_SOLARIZED_LIGHT),
    ThemePreset(label="Solarized Dark",  mode="dark",  overrides=_SOLARIZED_DARK),
    ThemePreset(label="Nord",            mode="dark",  overrides=_NORD_DARK),
    ThemePreset(label="Dracula",         mode="dark",  overrides=_DRACULA_DARK),
    ThemePreset(label="Gruvbox Dark",    mode="dark",  overrides=_GRUVBOX_DARK),
    ThemePreset(label="Monokai",         mode="dark",  overrides=_MONOKAI_DARK),
    ThemePreset(label="Material Ocean",  mode="dark",  overrides=_MATERIAL_OCEAN_DARK),
)


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


# --- provider-aware fetch windows ------------------------------------------
#
# ``INTERVAL_PERIODS`` above encodes *yfinance*'s per-interval history limits
# (notably the ~60-day intraday cap). Deep-history vendors — Alpaca (IEX data
# reaches back to ~2016) and Polygon — have **no** such cap, so pinning them
# to yfinance's windows needlessly truncated both the daily history ("only
# to 2024") and drill-down reach ("yfinance ~60 day intraday limit" even with
# Alpaca selected). :func:`provider_lookback_days` is the single source of
# truth consumed by BOTH the Alpaca/Polygon fetchers (default fetch lookback)
# AND the drill-down reachability check (``gui/drilldown.py``) so the two can
# never disagree.
_DEEP_HISTORY_SOURCES: frozenset = frozenset({"alpaca", "polygon"})

# Trailing calendar-day windows for deep-history vendors, per interval.
# CRITICAL: these are sized so a single fetch stays ~1 API page (≤10k bars)
# and completes in ≲3s. The WHOLE intraday series is pulled UP FRONT on
# every interval switch / drill-down, and the drill-down sync path has a
# hard 5s UI deadline (gui/drilldown._DRILLDOWN_SYNC_UI_TIMEOUT_MS). A
# 2-year 5m window (~40k bars / 4 paginated pages / ~15s) blew that deadline
# and hung the 5m load entirely — do NOT enlarge these without also moving
# to a targeted per-day intraday fetch. Daily+ requests ~15y (fast: one
# page, ~0.6s); the vendor caps to whatever the plan holds (Alpaca ≈ 2016).
_DEEP_HISTORY_INTRADAY_DAYS: dict = {
    "1m": 20, "2m": 40, "5m": 120, "15m": 365, "30m": 730, "1h": 1460,
}
_DEEP_HISTORY_DAILY_DAYS: int = 5490  # ~15 years


def _period_to_days(period: str) -> int:
    """Convert a yfinance period string (``60d`` / ``2y`` / ``max``) to days."""
    p = str(period).strip().lower()
    try:
        if p.endswith("mo"):
            return int(p[:-2]) * 31
        if p.endswith("d"):
            return int(p[:-1])
        if p.endswith("y"):
            return int(p[:-1]) * 366
    except ValueError:
        pass
    return 11000  # "max" / unrecognised → a large bound (~30y)


def provider_lookback_days(source: str, interval: str) -> int:
    """Trailing calendar-day window an on-demand fetch of ``(source, interval)`` covers.

    Deep-history vendors (Alpaca / Polygon) return generous per-interval
    windows — they have no yfinance-style 60-day intraday cap. Every other
    source derives its window from :data:`INTERVAL_PERIODS` (the yfinance
    limits) so their behaviour is unchanged.
    """
    if source in _DEEP_HISTORY_SOURCES:
        if is_intraday(interval):
            return _DEEP_HISTORY_INTRADAY_DAYS.get(interval, 120)
        return _DEEP_HISTORY_DAILY_DAYS
    return _period_to_days(INTERVAL_PERIODS.get(interval, "60d"))


#: Bars per API page used to size the targeted intraday fetch window (~1 page
#: = 1 HTTP round trip). Round-trip time is dominated by the NUMBER of pages
#: (~0.6s each on the free IEX feed), NOT the bar count within a page — so we
#: size the window to ~1 real page to keep a drilldown fast (~0.6s/symbol).
#:
#: **Empirically 2,000, not 10,000.** Alpaca's docs advertise a 10,000-bar
#: ``limit``, but the free-tier IEX historical feed caps each response at
#: ~2,000 bars regardless (verified: same 1,732-bar window is 1 page/0.6s at
#: limit=10000 but 9 pages/3.5s at limit=200; a 179-day 5m window is ~9,500
#: bars = 5 pages = ~3s). Sizing to 10,000 made the 5m window ~179 days = 5
#: pages, and a compare drilldown fetched that twice → the ~10s hang. At
#: 2,000 the 5m window is ~35 days = 1 page. Providers with a larger real page
#: (paid SIP, Polygon) can pass their own value to :func:`targeted_window`.
#: We still SEND ``limit=10000`` on each request (see ``alpaca_source``) so a
#: paid feed returns bigger pages automatically.
DEFAULT_BARS_PER_PAGE = 2_000

#: Regular-session minutes (09:30-16:00 ET) — bars/day = _RTH_MINUTES / interval.
_RTH_MINUTES = 390


def page_span_days(interval: str, *, bars_per_page: int = DEFAULT_BARS_PER_PAGE) -> int:
    """Calendar-day span that ~1 API page of ``interval`` bars covers.

    Sized for the targeted intraday fetch (``docs/TARGETED_FETCH.md`` §4.2):
    RTH bars/day = 390 / ``interval_minutes``, trading-days = ``bars_per_page`` /
    that, × 7/5 for weekend slack. **Intraday only** — daily+ raises via
    ``interval_minutes`` (a page window is meaningless there; those use the
    full-history :func:`provider_lookback_days`).
    """
    mins = interval_minutes(interval)  # ValueError on daily+
    bars_per_rth_day = max(1, round(_RTH_MINUTES / mins))
    trading_days = bars_per_page / bars_per_rth_day
    return max(1, int(trading_days * 7 / 5))


def targeted_window(
    interval: str,
    day_ts: int,
    *,
    now_ts: int,
    data_start_ts: int | None = None,
    bars_per_page: int = DEFAULT_BARS_PER_PAGE,
) -> tuple[int, int]:
    """Half-open ``[start, end)`` epoch-second window (~1 page) around ``day_ts``.

    Centered on the clicked day, then boundary-aware (``docs/TARGETED_FETCH.md``
    §4.2): clamp ``end`` to ``now_ts`` (refilling backward) and, when known,
    clamp ``start`` to ``data_start_ts`` (refilling forward), so the page fills
    with real bars rather than wasting half on an empty side.
    """
    span_s = page_span_days(interval, bars_per_page=bars_per_page) * 86_400
    day = int(day_ts)
    now = int(now_ts)
    half = span_s // 2
    start = day - half
    end = day + half
    # Clamp the forward edge to now; refill the page backward.
    if end > now:
        end = now
        start = end - span_s
    # Clamp the backward edge to the known data start; refill forward.
    if data_start_ts is not None and start < int(data_start_ts):
        start = int(data_start_ts)
        end = min(now, start + span_s)
    if start < 0:
        start = 0
    if end <= start:
        end = start + span_s
    return (start, end)


# --- main-window pane layout -----------------------------------------------
#
# The golden ratio φ ≈ 1.618 and its inverse 1/φ ≈ 0.618. The defining
# identity is 1/φ == φ - 1, so the major (0.618) and minor (0.382)
# sections partition any length into the canonical golden split.
GOLDEN_RATIO: float = 1.6180339887498949
GOLDEN_RATIO_INVERSE: float = 0.6180339887498949

#
# Fraction of the main window the *chart* pane should occupy at every
# launch. The remainder ``(1 - CHART_PANE_STARTUP_RATIO)`` goes to the
# right-side notebook (Watchlist / Sandbox / Scanner / Entries / Exits).
# When the ChartStack panel is enabled and
# becomes the third (leftmost) pane, ``CHARTSTACK_PANE_STARTUP_WIDTH_PX``
# is carved off the left of the window first, and the remaining width
# is split between chart and notebook in the same chart:notebook ratio.
#
# Why the golden ratio: the chart is the user's primary focus, so it
# claims the golden *major* section (~61.8 %) and the notebook the
# golden *minor* (~38.2 %) — a deliberately balanced, aesthetically
# pleasing "unboxing" split. Earlier sprints used a flat 0.80 which
# gave the chart the lion's share but left the layout visually
# lopsided; the golden proportion reads as more harmonious while still
# keeping the chart dominant. On a 1920-wide monitor the notebook gets
# ~734 px — comfortably more than the 6-column OHLC tree needs, with no
# horizontal scrolling.
#
# This constant is applied at every startup (see ``app.py``
# ``_restore_main_paned_sashes``), so the chart always opens with the
# golden split even if a prior session's drag left the sash in an
# awkward position. Users who want a different split can drag the sash
# during a session — it just won't persist across launches.
CHART_PANE_STARTUP_RATIO: float = GOLDEN_RATIO_INVERSE

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
    notebook_width_px: int | None = None,
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

    ``notebook_width_px`` (audit ``watchlist-width-setting``): when a
    positive int is supplied it OVERRIDES the golden-ratio notebook
    width with that absolute pixel width — the user's saved watchlist
    width (the dragged divider position persisted via
    ``settings["layout.notebook_width_px"]`` on File → Save
    Configuration). ``None`` / non-positive falls back to the ratio.
    The override is still subject to the ``notebook_min_px`` floor and
    the ``chart_min_px`` floor (a saved width wider than the window
    still yields a usable chart, with the notebook giving up the
    excess — same defensive behaviour as the ratio path).
    """
    if notebook_width_px is not None:
        try:
            _nb_override = int(notebook_width_px)
        except (TypeError, ValueError):
            _nb_override = 0
    else:
        _nb_override = 0
    if _nb_override > 0:
        notebook_w = max(notebook_min_px, _nb_override)
    else:
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


def compute_toggle_sashes(
    main_w: int,
    notebook_left_x: int,
    *,
    chartstack_visible: bool,
    chartstack_w: int = CHARTSTACK_PANE_STARTUP_WIDTH_PX,
    chart_min_px: int = 200,
) -> list[int]:
    """Sash positions that PRESERVE the watchlist (notebook) column.

    Unlike :func:`compute_main_paned_sashes` (which derives the
    notebook width from a *ratio* of ``main_w`` and is used at
    startup, where there is no prior layout to honour), this helper
    is used by the **ChartStack toggle** path. It takes the
    chart|notebook boundary captured *before* the toggle and holds it
    fixed, so the watchlist does not move — only the chart pane
    resizes to absorb (or release) the ChartStack column on the left.

    Audit ``chartstack-toggle-preserves-notebook``. The previous
    toggle path recomputed the layout from a *stale*
    ``_initial_geometry`` width; on a window that had been resized /
    maximised since launch the resulting sash positions left the
    notebook filling ~half the screen. The fix is to (a) read the
    live paned width and (b) preserve the measured boundary verbatim
    — both of which this helper assumes its caller has done.

    Parameters
    ----------
    main_w
        The **live** paned width in pixels (``paned.winfo_width()``),
        used only to cap the boundary so the notebook can't be pushed
        off the right edge. ``0`` (widget not yet realised) disables
        the cap.
    notebook_left_x
        The absolute x-pixel of the chart|notebook sash captured
        before the toggle (``paned.sashpos(0)`` in 2-pane mode or
        ``paned.sashpos(1)`` in 3-pane mode).
    chartstack_visible
        The target state *after* the toggle. ``True`` → 3-pane
        (ChartStack shown); ``False`` → 2-pane (ChartStack hidden).

    Returns
    -------
    * 3-pane (CS on):  ``[chartstack_w, notebook_left_x]`` — chartstack
      spans ``[0, chartstack_w]``, chart ``[chartstack_w,
      notebook_left_x]``, notebook ``[notebook_left_x, main_w]``.
    * 2-pane (CS off): ``[notebook_left_x]`` — chart spans
      ``[0, notebook_left_x]``, notebook ``[notebook_left_x, main_w]``.

    Defensive: if holding the boundary would crush the chart below
    ``chart_min_px``, the boundary is nudged right just enough to keep
    the chart usable (the only case the watchlist gives up width).
    """
    boundary = int(notebook_left_x)
    if chartstack_visible:
        # Chart spans [chartstack_w, boundary] — keep it usable.
        boundary = max(boundary, int(chartstack_w) + int(chart_min_px))
    else:
        # Chart spans [0, boundary] — keep it usable.
        boundary = max(boundary, int(chart_min_px))
    if main_w and int(main_w) > 0:
        boundary = min(boundary, int(main_w))
    if chartstack_visible:
        return [int(chartstack_w), int(boundary)]
    return [int(boundary)]


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


def classify_session_arr(hours, minutes) -> list[str]:
    """Vectorized :func:`classify_session` over numpy hour + minute arrays.

    Returns a ``list[str]`` of session labels that is **bit-for-bit
    identical** to calling :func:`classify_session` element-by-element. The
    minute-of-day thresholds are duplicated here for a single vectorized
    pass — keep the two functions in lockstep (a change to the boundaries
    must update BOTH).

    Used by the data normalizers so large intraday fetches (multi-year 1m,
    intraday universe preloads) don't pay a per-bar Python call. numpy is
    imported lazily since this is called once per fetch, not per bar.
    """
    import numpy as np

    total = np.asarray(hours, dtype=np.int32) * 60 + np.asarray(minutes, dtype=np.int32)
    # Integer category codes (0=pre, 1=regular, 2=post) computed with two
    # vectorized masked assignments, then mapped back to THREE shared string
    # objects. Going via ``codes.tolist()`` (cached small-int refs) + tuple
    # indexing keeps every label a shared reference — a numpy ``"<U7"`` array
    # ``.tolist()`` would instead allocate one fresh ``str`` per bar.
    codes = np.zeros(total.shape, dtype=np.int8)
    codes[(total >= 9 * 60 + 30) & (total < 16 * 60)] = 1
    codes[(total >= 16 * 60) & (total < 20 * 60)] = 2
    _LABELS = ("pre", "regular", "post")
    return [_LABELS[c] for c in codes.tolist()]


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
