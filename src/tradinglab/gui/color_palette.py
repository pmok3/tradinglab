"""Color picker — themed Windows-ChooseColor look-alike.

Modal :class:`ThemedColorChooser` that mirrors the Win32 ChooseColor
dialog (the OS native chooser opened by
:func:`tkinter.colorchooser.askcolor`) but follows the app's
light/dark theme — the *only* visible difference from the OS chooser
is the chrome background colour.

Audit tag: ``themed-color-chooser``.

Why this exists
---------------
The native Win32 ChooseColor is a legacy ``COMMDLG`` that has a
hardcoded light-grey background and does NOT honour the Windows
10/11 dark-mode setting. Calling ``colorchooser.askcolor`` to pick
an indicator colour in TradingLab's dark mode left the user staring
at a bright-grey panel mid-app. This module re-implements the same
layout (Basic colours grid, Custom colours grid with persistence,
H×S pad, L slider, H/S/L + R/G/B numeric fields, hex entry,
``Color | Solid`` split preview, Add-to-Custom button, OK/Cancel)
but paints all chrome via the existing
:mod:`tradinglab.gui.native_theme` helpers — so light mode looks
like the OS chooser, dark mode looks like the app.

Public API (unchanged from earlier revisions)
---------------------------------------------
* :func:`pick_color` — opens the chooser modally, blocks until
  dismissed, returns the chosen ``"#rrggbb"`` hex or ``None``.
* :func:`_normalise` — module-level hex canonicaliser (`#RRGGBB`
  lower-cased; `#RGB` expanded; empty → :data:`DEFAULT_COLOR`;
  X11 color names returned unchanged so callers like
  ``tradinglab.drawings`` can keep using ``"red"`` etc.).
* :data:`DEFAULT_COLOR` — ``"#888888"`` (matches
  :class:`tradinglab.indicators.base.LineStyle` default).

Persistent state
----------------
Custom-colours slots persist as a 16-entry JSON list at
``app_data_dir() / "custom_colors.json"`` (created on first use;
corrupt JSON / missing file degrade to all-white). The file is
read once at dialog construction and rewritten on every
"Add to Custom Colors" click via :func:`core.io_helpers.atomic_write_json`.
"""

from __future__ import annotations

import colorsys
import logging
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import numpy as np

from ..core.io_helpers import atomic_write_json, read_json
from ..paths import app_data_dir
from ._modal_base import BaseModalDialog, protect_combobox_wheel
from .native_theme import (
    apply_canvas_theme,
    current_theme,
)

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Default mid-gray used when callers pass an empty / falsy initial.
#: Matches :class:`tradinglab.indicators.base.LineStyle` default.
DEFAULT_COLOR = "#888888"

#: 48 fixed swatches — mirrors the Win32 ChooseColor "Basic colors"
#: grid (8 columns × 6 rows). Order is left-to-right top-to-bottom.
_BASIC_COLORS: tuple[str, ...] = (
    # Row 1: warm/saturated
    "#ff8080", "#ffff80", "#80ff80", "#00ff80",
    "#80ffff", "#0080ff", "#ff80c0", "#ff80ff",
    # Row 2: bright pure
    "#ff0000", "#ffff00", "#80ff00", "#00ff40",
    "#00ffff", "#0080c0", "#8080c0", "#ff00ff",
    # Row 3: mid
    "#804040", "#ff8040", "#00ff00", "#008080",
    "#004080", "#8080ff", "#800040", "#ff0080",
    # Row 4: dark
    "#800000", "#ff8000", "#008000", "#008040",
    "#0000ff", "#0000a0", "#800080", "#8000ff",
    # Row 5: muted/dark
    "#400000", "#804000", "#004000", "#004040",
    "#000080", "#000040", "#400040", "#400080",
    # Row 6: neutral
    "#000000", "#808000", "#808040", "#808080",
    "#408080", "#c0c0c0", "#400040", "#ffffff",
)

#: Number of slots in the user-fillable custom-colours grid
#: (2 rows × 8 columns matching Win32 ChooseColor).
_CUSTOM_SLOTS: int = 16

#: Initial colour for each custom slot before the user defines one
#: (matches Win32 ChooseColor's empty-white slots).
_DEFAULT_CUSTOM_SLOT: str = "#ffffff"

#: Saved-custom-colours filename inside :func:`app_data_dir`.
_CUSTOM_FILENAME: str = "custom_colors.json"

# ---- Visual sizes (pixels) ----
_SWATCH_W: int = 24
_SWATCH_H: int = 20
_SWATCH_GAP: int = 2
_BASIC_COLS: int = 8
_BASIC_ROWS: int = 6
_CUSTOM_COLS: int = 8
_CUSTOM_ROWS: int = 2
_PAD_W: int = 240
_PAD_H: int = 200
_SLIDER_W: int = 22
_SLIDER_H: int = 200
_PREVIEW_W: int = 90
_PREVIEW_H: int = 40


# ---------------------------------------------------------------------------
# Hex helpers
# ---------------------------------------------------------------------------


def _normalise(color: str) -> str:
    """Normalise a hex colour string to lower-case ``"#rrggbb"``.

    * ``"#RRGGBB"`` → lower-cased.
    * ``"#RGB"`` short-form → expanded then lower-cased.
    * Empty / falsy input → :data:`DEFAULT_COLOR`.
    * Anything else (e.g. an X11 colour name like ``"red"``) is
      returned unchanged so Tk can still resolve it downstream —
      matches the original ``HexColorPalette._normalise`` contract
      pinned by ``tests/unit/test_hex_case_constants.py``.
    """
    s = (color or "").strip()
    if not s:
        return DEFAULT_COLOR
    if s.startswith("#") and len(s) == 7:
        return "#" + s[1:].lower()
    if s.startswith("#") and len(s) == 4:
        r, g, b = s[1], s[2], s[3]
        return ("#" + r + r + g + g + b + b).lower()
    return s


def _resolve_to_hex(parent: tk.Misc, color: str) -> str:
    """Best-effort resolve any Tk colour string to ``"#rrggbb"``.

    Used to normalise the dialog's ``initial`` arg: ``_normalise``
    preserves X11 names like ``"red"`` for back-compat with callers
    such as :mod:`tradinglab.drawings`, but the dialog itself needs
    a concrete hex to seed its R/G/B/H/S/L fields. Falls through to
    :data:`DEFAULT_COLOR` if Tk can't resolve.
    """
    s = _normalise(color)
    if s.startswith("#") and len(s) == 7:
        return s
    try:
        # `winfo_rgb` returns 16-bit-per-channel ints.
        r16, g16, b16 = parent.winfo_rgb(s)
        return f"#{r16 >> 8:02x}{g16 >> 8:02x}{b16 >> 8:02x}"
    except tk.TclError:
        return DEFAULT_COLOR


def _hex_to_rgb(hexstr: str) -> tuple[int, int, int]:
    """Parse ``"#rrggbb"`` (already normalised) into ``(r, g, b)`` 0-255."""
    s = _normalise(hexstr)
    if not (s.startswith("#") and len(s) == 7):
        return (0x88, 0x88, 0x88)
    return (int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16))


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    """Build ``"#rrggbb"`` from clamped 0-255 ints."""
    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))
    return f"#{r:02x}{g:02x}{b:02x}"


def _rgb_to_hsl(r: int, g: int, b: int) -> tuple[int, int, int]:
    """RGB 0-255 → H 0-359, S 0-100, L 0-100 (rounded)."""
    h, light, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    return (
        int(round(h * 360)) % 360,
        int(round(s * 100)),
        int(round(light * 100)),
    )


def _hsl_to_rgb(h: int, s: int, light: int) -> tuple[int, int, int]:
    """H 0-359, S 0-100, L 0-100 → RGB 0-255 (rounded)."""
    h_f = (h % 360) / 360.0
    s_f = max(0.0, min(1.0, s / 100.0))
    l_f = max(0.0, min(1.0, light / 100.0))
    r, g, b = colorsys.hls_to_rgb(h_f, l_f, s_f)
    return (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


# ---------------------------------------------------------------------------
# Custom-colours persistence
# ---------------------------------------------------------------------------


def _custom_colors_path() -> Path:
    """Return the on-disk path for saved custom colours.

    Wrapped in a function (not a module-level constant) so tests can
    monkey-patch this name to redirect to a ``tmp_path``.
    """
    return app_data_dir() / _CUSTOM_FILENAME


def _load_custom_colors() -> list[str]:
    """Load 16 custom-colour hex strings from disk.

    Missing file, unreadable file, or unparseable JSON → 16 default
    ``_DEFAULT_CUSTOM_SLOT`` slots. A list shorter than 16 is padded
    with default; longer is truncated.
    """
    raw = read_json(_custom_colors_path(), default=None,
                    log=_LOG, log_label="color_palette.custom")
    if not isinstance(raw, list):
        return [_DEFAULT_CUSTOM_SLOT] * _CUSTOM_SLOTS
    out = [_normalise(str(c)) if isinstance(c, str) else _DEFAULT_CUSTOM_SLOT
           for c in raw[:_CUSTOM_SLOTS]]
    while len(out) < _CUSTOM_SLOTS:
        out.append(_DEFAULT_CUSTOM_SLOT)
    return out


def _save_custom_colors(colors: list[str]) -> None:
    """Atomically persist a list of (up to) 16 colour hex strings."""
    safe = [_normalise(c) for c in colors][:_CUSTOM_SLOTS]
    while len(safe) < _CUSTOM_SLOTS:
        safe.append(_DEFAULT_CUSTOM_SLOT)
    try:
        atomic_write_json(_custom_colors_path(), safe)
    except OSError as exc:
        # Custom-colour persistence is convenience; never crash the
        # dialog if the disk write fails (e.g. read-only profile).
        _LOG.warning("could not persist custom colors: %s", exc)


# ---------------------------------------------------------------------------
# Sat × Hue pad gradient (numpy-vectorised, HSL @ L=0.5)
# ---------------------------------------------------------------------------


def _render_pad_pixels(width: int, height: int) -> np.ndarray:
    """Return a ``(height, width, 3)`` uint8 RGB array for the H×S pad.

    X axis = hue (0..360°); Y axis = saturation (0 top → 1 bottom);
    luminance fixed at 0.5 (matches the labelled HSL model exposed
    in the numeric fields).

    Pure-numpy implementation of HSL→RGB; no PIL, no per-pixel
    Python.
    """
    hues = np.linspace(0.0, 1.0, width, endpoint=False)
    sats = np.linspace(0.0, 1.0, height)
    # broadcast into (H, W) grids
    hg, sg = np.meshgrid(hues, sats)
    # HSL→RGB with L=0.5 (so C = S):
    c = sg
    h6 = hg * 6.0
    x = c * (1.0 - np.abs((h6 % 2.0) - 1.0))
    m = 0.5 - c / 2.0
    z = np.zeros_like(hg)
    r = np.where(h6 < 1, c, np.where(h6 < 2, x,
        np.where(h6 < 3, z, np.where(h6 < 4, z,
        np.where(h6 < 5, x, c)))))
    g = np.where(h6 < 1, x, np.where(h6 < 2, c,
        np.where(h6 < 3, c, np.where(h6 < 4, x, z))))
    b = np.where(h6 < 1, z, np.where(h6 < 2, z,
        np.where(h6 < 3, x, np.where(h6 < 4, c,
        np.where(h6 < 5, c, x)))))
    rgb = np.stack([(r + m), (g + m), (b + m)], axis=-1)
    return np.clip(rgb * 255.0, 0, 255).astype(np.uint8)


def _photoimage_put_data(rgb: np.ndarray) -> str:
    """Convert a ``(H, W, 3)`` uint8 array into a
    ``tk.PhotoImage.put`` data string of the form ``{#rrggbb …}
    {#rrggbb …}``.
    """
    # Vectorised hex formatting via str.join is ~50× faster than
    # a per-pixel %x formatting loop.
    hex_pixels = (np.char.add(np.char.add(
        np.char.zfill(np.array([f"{v:x}" for v in range(256)])[rgb[..., 0]], 2),
        np.char.zfill(np.array([f"{v:x}" for v in range(256)])[rgb[..., 1]], 2)),
        np.char.zfill(np.array([f"{v:x}" for v in range(256)])[rgb[..., 2]], 2)))
    rows = ["{" + " ".join("#" + cell for cell in row) + "}"
            for row in hex_pixels]
    return " ".join(rows)


# ---------------------------------------------------------------------------
# ThemedColorChooser
# ---------------------------------------------------------------------------


class ThemedColorChooser(BaseModalDialog):
    """Themed clone of the Windows ChooseColor dialog.

    Layout (left column = swatches, right column = pad/slider +
    fields + preview):

    ::

        +-- Basic colours -----+   +-- Color ---+--+
        | 8×6 grid             |   | H×S pad    |L |
        +----------------------+   |            |sl|
        +-- Custom colours ----+   +------------+--+
        | 8×2 grid             |   [Color | Solid ]
        +----------------------+   H:[] S:[] L:[]
        [Add to Custom Colors]     R:[] G:[] B:[]
                                   Hex: [_______]

        [OK]                                   [Cancel]
    """

    def __init__(self, parent: tk.Misc, initial: str = DEFAULT_COLOR,
                 title: str = "Pick a color") -> None:
        super().__init__(
            parent,
            title=title,
            geometry_key="dlg.color_palette",
            default_geometry="560x440",
            resizable=(False, False),
        )
        self.result: str | None = None
        self._theme = current_theme(parent)
        self._bg = self._theme.get("win_bg", "#f0f0f0")
        self._fg = self._theme.get("text", "#000000")
        self.configure(background=self._bg)

        # Internal HSL state (canonical source of truth between widgets).
        seeded = _resolve_to_hex(parent, initial)
        r, g, b = _hex_to_rgb(seeded)
        h, s, light = _rgb_to_hsl(r, g, b)
        self._current: str = seeded
        self._h: int = h
        self._s: int = s
        self._l: int = light
        # Re-entrancy guard so programmatic field updates don't loop.
        self._updating: bool = False

        # Loaded once at construction; saved on Add-to-Custom click.
        self._custom_colors: list[str] = _load_custom_colors()

        # PhotoImage handles must be retained as instance attrs or
        # Tk garbage-collects them and the canvases blank out.
        self._pad_img: tk.PhotoImage | None = None
        self._slider_img: tk.PhotoImage | None = None

        self._build_ui()
        self._refresh_all_widgets()
        # Wheel-guard the 6 ttk.Spinbox numeric fields per §7.11.
        protect_combobox_wheel(self)
        self._finalize_modal(primary=self._on_ok, cancel=self._on_cancel)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = tk.Frame(self, padx=10, pady=10, background=self._bg)
        outer.pack(fill="both", expand=True)

        body = tk.Frame(outer, background=self._bg)
        body.pack(side="top", fill="both", expand=True)

        # Left column: Basic + Custom swatch grids + Add button.
        left = tk.Frame(body, background=self._bg)
        left.pack(side="left", anchor="n")
        self._build_basic_grid(left)
        self._build_custom_grid(left)
        ttk.Button(left, text="Add to Custom Colors",
                   command=self._on_add_to_custom).pack(
            side="top", anchor="w", pady=(8, 0))

        # Right column: pad + slider + preview + fields.
        right = tk.Frame(body, background=self._bg)
        right.pack(side="left", anchor="n", padx=(14, 0))
        self._build_pad_and_slider(right)
        self._build_preview(right)
        self._build_fields(right)

        # Footer.
        bar = tk.Frame(outer, background=self._bg)
        bar.pack(side="bottom", fill="x", pady=(10, 0))
        self._cancel_btn = ttk.Button(bar, text="Cancel",
                                      command=self._on_cancel)
        self._cancel_btn.pack(side="right")
        self._ok_btn = ttk.Button(bar, text="OK", command=self._on_ok)
        self._ok_btn.pack(side="right", padx=(0, 6))

    def _build_basic_grid(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="Basic colors:",
                 background=self._bg, foreground=self._fg,
                 anchor="w").pack(side="top", fill="x")
        w = _BASIC_COLS * (_SWATCH_W + _SWATCH_GAP) + _SWATCH_GAP
        h = _BASIC_ROWS * (_SWATCH_H + _SWATCH_GAP) + _SWATCH_GAP
        cv = tk.Canvas(parent, width=w, height=h,
                       highlightthickness=0, bd=0, cursor="hand2")
        apply_canvas_theme(cv, self._theme)
        cv.pack(side="top", anchor="w", pady=(2, 0))
        self._basic_canvas = cv
        self._render_basic_grid()
        cv.bind("<Button-1>", lambda e: self._on_swatch_click(
            e, _BASIC_COLORS, _BASIC_COLS, _BASIC_ROWS))

    def _build_custom_grid(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="Custom colors:",
                 background=self._bg, foreground=self._fg,
                 anchor="w").pack(side="top", fill="x", pady=(8, 0))
        w = _CUSTOM_COLS * (_SWATCH_W + _SWATCH_GAP) + _SWATCH_GAP
        h = _CUSTOM_ROWS * (_SWATCH_H + _SWATCH_GAP) + _SWATCH_GAP
        cv = tk.Canvas(parent, width=w, height=h,
                       highlightthickness=0, bd=0, cursor="hand2")
        apply_canvas_theme(cv, self._theme)
        cv.pack(side="top", anchor="w", pady=(2, 0))
        self._custom_canvas = cv
        self._render_custom_grid()
        cv.bind("<Button-1>", lambda e: self._on_swatch_click(
            e, self._custom_colors, _CUSTOM_COLS, _CUSTOM_ROWS))

    def _build_pad_and_slider(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, background=self._bg)
        wrap.pack(side="top", anchor="w")
        cv = tk.Canvas(wrap, width=_PAD_W, height=_PAD_H,
                       highlightthickness=0, bd=0, cursor="crosshair")
        apply_canvas_theme(cv, self._theme)
        cv.pack(side="left", anchor="n")
        self._pad_canvas = cv
        cv.bind("<Button-1>", self._on_pad_drag)
        cv.bind("<B1-Motion>", self._on_pad_drag)

        slider = tk.Canvas(wrap, width=_SLIDER_W, height=_SLIDER_H,
                           highlightthickness=0, bd=0,
                           cursor="sb_v_double_arrow")
        apply_canvas_theme(slider, self._theme)
        slider.pack(side="left", anchor="n", padx=(6, 0))
        self._slider_canvas = slider
        slider.bind("<Button-1>", self._on_slider_drag)
        slider.bind("<B1-Motion>", self._on_slider_drag)

        # Pre-build the PhotoImages (pad is fixed; slider re-renders
        # when H/S change).
        self._pad_img = tk.PhotoImage(width=_PAD_W, height=_PAD_H)
        rgb = _render_pad_pixels(_PAD_W, _PAD_H)
        self._pad_img.put(_photoimage_put_data(rgb))
        cv.create_image(0, 0, anchor="nw", image=self._pad_img,
                        tags=("padimg",))
        self._slider_img = tk.PhotoImage(width=_SLIDER_W, height=_SLIDER_H)
        slider.create_image(0, 0, anchor="nw", image=self._slider_img,
                            tags=("sliderimg",))

    def _build_preview(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, background=self._bg)
        wrap.pack(side="top", anchor="w", pady=(8, 0))
        tk.Label(wrap, text="Color|Solid",
                 background=self._bg, foreground=self._fg,
                 anchor="w").pack(side="top", fill="x")
        prv = tk.Frame(wrap, background=self._bg)
        prv.pack(side="top", anchor="w", pady=(2, 0))
        # Two side-by-side frames; "Color" left = picked colour;
        # "Solid" right = nearest displayable colour (we just mirror
        # the picked colour today — Win32's distinction matters only
        # on 256-colour displays which we don't support).
        self._preview_color = tk.Frame(
            prv, width=_PREVIEW_W // 2, height=_PREVIEW_H,
            background=self._current, relief="solid",
            highlightthickness=1,
            highlightbackground=self._theme.get("spine", "#888"),
        )
        self._preview_color.pack_propagate(False)
        self._preview_color.pack(side="left")
        self._preview_solid = tk.Frame(
            prv, width=_PREVIEW_W // 2, height=_PREVIEW_H,
            background=self._current, relief="solid",
            highlightthickness=1,
            highlightbackground=self._theme.get("spine", "#888"),
        )
        self._preview_solid.pack_propagate(False)
        self._preview_solid.pack(side="left")

    def _build_fields(self, parent: tk.Frame) -> None:
        # 3×2 grid: H/S/L on left two cols, R/G/B on right two cols.
        wrap = tk.Frame(parent, background=self._bg)
        wrap.pack(side="top", anchor="w", pady=(10, 0))

        def _label(text: str, col: int, row: int) -> None:
            tk.Label(wrap, text=text, background=self._bg,
                     foreground=self._fg, width=5, anchor="e").grid(
                row=row, column=col, padx=(0, 2), pady=1, sticky="e")

        def _spin(from_: int, to_: int, col: int, row: int,
                  callback) -> ttk.Spinbox:
            sb = ttk.Spinbox(wrap, from_=from_, to=to_, width=5,
                             increment=1, command=callback,
                             justify="right")
            sb.grid(row=row, column=col, padx=(0, 8), pady=1, sticky="w")
            sb.bind("<Return>", lambda _e: callback())
            sb.bind("<FocusOut>", lambda _e: callback())
            return sb

        _label("Hue:", 0, 0)
        self._sb_h = _spin(0, 359, 1, 0, self._on_hsl_edit)
        _label("Sat:", 0, 1)
        self._sb_s = _spin(0, 100, 1, 1, self._on_hsl_edit)
        _label("Lum:", 0, 2)
        self._sb_l = _spin(0, 100, 1, 2, self._on_hsl_edit)
        _label("Red:", 2, 0)
        self._sb_r = _spin(0, 255, 3, 0, self._on_rgb_edit)
        _label("Green:", 2, 1)
        self._sb_g = _spin(0, 255, 3, 1, self._on_rgb_edit)
        _label("Blue:", 2, 2)
        self._sb_b = _spin(0, 255, 3, 2, self._on_rgb_edit)

        # Hex entry below the two columns.
        hex_wrap = tk.Frame(parent, background=self._bg)
        hex_wrap.pack(side="top", anchor="w", pady=(6, 0))
        tk.Label(hex_wrap, text="Hex:", background=self._bg,
                 foreground=self._fg, width=5, anchor="e").pack(
            side="left", padx=(0, 2))
        self._hex_entry = ttk.Entry(hex_wrap, width=10, justify="left")
        self._hex_entry.pack(side="left")
        self._hex_entry.bind("<Return>", lambda _e: self._on_hex_edit())
        self._hex_entry.bind("<FocusOut>", lambda _e: self._on_hex_edit())

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_basic_grid(self) -> None:
        self._basic_canvas.delete("swatch")
        for idx, color in enumerate(_BASIC_COLORS):
            col, row = idx % _BASIC_COLS, idx // _BASIC_COLS
            x = _SWATCH_GAP + col * (_SWATCH_W + _SWATCH_GAP)
            y = _SWATCH_GAP + row * (_SWATCH_H + _SWATCH_GAP)
            self._basic_canvas.create_rectangle(
                x, y, x + _SWATCH_W, y + _SWATCH_H,
                fill=color,
                outline=self._theme.get("spine", "#888"),
                tags=("swatch",),
            )

    def _render_custom_grid(self) -> None:
        self._custom_canvas.delete("swatch")
        for idx, color in enumerate(self._custom_colors):
            col, row = idx % _CUSTOM_COLS, idx // _CUSTOM_COLS
            x = _SWATCH_GAP + col * (_SWATCH_W + _SWATCH_GAP)
            y = _SWATCH_GAP + row * (_SWATCH_H + _SWATCH_GAP)
            self._custom_canvas.create_rectangle(
                x, y, x + _SWATCH_W, y + _SWATCH_H,
                fill=color,
                outline=self._theme.get("spine", "#888"),
                tags=("swatch",),
            )

    def _render_slider(self) -> None:
        """Re-paint the vertical Lum slider for the current (H, S).

        Pure-Python loop is fine — slider is only 22 × 200 px and
        re-renders only when H or S change.
        """
        if self._slider_img is None:
            return
        h_f = self._h / 360.0
        s_f = self._s / 100.0
        rows: list[str] = []
        for py in range(_SLIDER_H):
            # Top of slider = L=1 (white); bottom = L=0 (black).
            l_f = 1.0 - py / max(1, _SLIDER_H - 1)
            r, g, b = colorsys.hls_to_rgb(h_f, l_f, s_f)
            hex_str = (
                f"#{int(round(r * 255)):02x}"
                f"{int(round(g * 255)):02x}"
                f"{int(round(b * 255)):02x}"
            )
            rows.append("{" + " ".join([hex_str] * _SLIDER_W) + "}")
        self._slider_img.put(" ".join(rows))

    def _redraw_pad_marker(self) -> None:
        self._pad_canvas.delete("marker")
        cx = int(self._h / 360.0 * (_PAD_W - 1))
        cy = int(self._s / 100.0 * (_PAD_H - 1))
        ring_clr = "#ffffff" if self._l < 50 else "#000000"
        self._pad_canvas.create_oval(
            cx - 5, cy - 5, cx + 5, cy + 5,
            outline=ring_clr, width=2, tags=("marker",),
        )

    def _redraw_slider_marker(self) -> None:
        self._slider_canvas.delete("marker")
        y = int((1.0 - self._l / 100.0) * (_SLIDER_H - 1))
        spine = self._theme.get("text", "#000000")
        self._slider_canvas.create_polygon(
            0, y, 5, y - 4, 5, y + 4, fill=spine, tags=("marker",),
        )
        self._slider_canvas.create_polygon(
            _SLIDER_W, y, _SLIDER_W - 5, y - 4,
            _SLIDER_W - 5, y + 4, fill=spine, tags=("marker",),
        )

    def _refresh_all_widgets(self) -> None:
        """Repaint every dependent widget from the canonical
        ``_current``/``_h``/``_s``/``_l`` state.

        Marks ``self._updating`` so callbacks fired by programmatic
        ``set()`` calls on spinboxes don't re-enter the update logic.
        """
        self._updating = True
        try:
            r, g, b = _hex_to_rgb(self._current)
            for sb, val in ((self._sb_r, r), (self._sb_g, g),
                            (self._sb_b, b),
                            (self._sb_h, self._h),
                            (self._sb_s, self._s),
                            (self._sb_l, self._l)):
                sb.set(str(val))
            self._hex_entry.delete(0, "end")
            self._hex_entry.insert(0, self._current)
            self._preview_color.configure(background=self._current)
            self._preview_solid.configure(background=self._current)
            self._render_slider()
            self._redraw_pad_marker()
            self._redraw_slider_marker()
        finally:
            self._updating = False

    # ------------------------------------------------------------------
    # State mutation entry points
    # ------------------------------------------------------------------

    def _set_current_hex(self, hexstr: str) -> None:
        """Set state from a hex string and refresh widgets."""
        new = _normalise(hexstr)
        if not (new.startswith("#") and len(new) == 7):
            return
        self._current = new
        r, g, b = _hex_to_rgb(new)
        self._h, self._s, self._l = _rgb_to_hsl(r, g, b)
        self._refresh_all_widgets()

    def _set_current_rgb(self, r: int, g: int, b: int) -> None:
        self._current = _rgb_to_hex(r, g, b)
        self._h, self._s, self._l = _rgb_to_hsl(*_hex_to_rgb(self._current))
        self._refresh_all_widgets()

    def _set_current_hsl(self, h: int, s: int, light: int) -> None:
        self._h = max(0, min(359, int(h)))
        self._s = max(0, min(100, int(s)))
        self._l = max(0, min(100, int(light)))
        self._current = _rgb_to_hex(*_hsl_to_rgb(self._h, self._s, self._l))
        self._refresh_all_widgets()

    # ------------------------------------------------------------------
    # Widget event handlers
    # ------------------------------------------------------------------

    def _on_swatch_click(self, event: tk.Event, table: list,
                         cols: int, rows: int) -> None:
        col = (event.x - _SWATCH_GAP) // (_SWATCH_W + _SWATCH_GAP)
        row = (event.y - _SWATCH_GAP) // (_SWATCH_H + _SWATCH_GAP)
        if not (0 <= col < cols and 0 <= row < rows):
            return
        idx = row * cols + col
        if 0 <= idx < len(table):
            self._set_current_hex(table[idx])

    def _on_pad_drag(self, event: tk.Event) -> None:
        x = max(0, min(_PAD_W - 1, event.x))
        y = max(0, min(_PAD_H - 1, event.y))
        new_h = int(x / max(1, _PAD_W - 1) * 359)
        new_s = int(y / max(1, _PAD_H - 1) * 100)
        self._set_current_hsl(new_h, new_s, self._l)

    def _on_slider_drag(self, event: tk.Event) -> None:
        y = max(0, min(_SLIDER_H - 1, event.y))
        new_l = int((1.0 - y / max(1, _SLIDER_H - 1)) * 100)
        self._set_current_hsl(self._h, self._s, new_l)

    def _on_hsl_edit(self) -> None:
        if self._updating:
            return
        try:
            h = int(float(self._sb_h.get() or 0))
            s = int(float(self._sb_s.get() or 0))
            light = int(float(self._sb_l.get() or 0))
        except (ValueError, tk.TclError):
            return
        self._set_current_hsl(h, s, light)

    def _on_rgb_edit(self) -> None:
        if self._updating:
            return
        try:
            r = int(float(self._sb_r.get() or 0))
            g = int(float(self._sb_g.get() or 0))
            b = int(float(self._sb_b.get() or 0))
        except (ValueError, tk.TclError):
            return
        self._set_current_rgb(r, g, b)

    def _on_hex_edit(self) -> None:
        if self._updating:
            return
        s = self._hex_entry.get().strip()
        if not s:
            return
        new = _normalise(s)
        if not (new.startswith("#") and len(new) == 7):
            # Invalid input → revert to canonical state.
            self._refresh_all_widgets()
            return
        self._set_current_hex(new)

    def _on_add_to_custom(self) -> None:
        """Append the current color to the next custom slot.

        First fills any default-white slots from left to right, then
        scrolls the existing 16 colors left (drops the oldest) and
        appends the new color at the end. Persists immediately.
        """
        # Find the first slot still at default white.
        try:
            idx = self._custom_colors.index(_DEFAULT_CUSTOM_SLOT)
        except ValueError:
            # All 16 slots filled — drop oldest, shift left, append.
            self._custom_colors = self._custom_colors[1:] + [self._current]
        else:
            self._custom_colors[idx] = self._current
        _save_custom_colors(self._custom_colors)
        self._render_custom_grid()

    # ------------------------------------------------------------------
    # Modal close
    # ------------------------------------------------------------------

    def _on_ok(self) -> None:
        self.result = _normalise(self._current)
        self._dismiss()

    def _on_cancel(self) -> None:
        self.result = None
        self._dismiss()

    def _dismiss(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def pick_color(
    parent: tk.Misc,
    initial: str = DEFAULT_COLOR,
    title: str = "Pick a color",
) -> str | None:
    """Open the themed colour chooser modally; return the chosen hex.

    Blocks the calling thread until the user dismisses the chooser;
    must be invoked from the Tk main thread.

    Returns the normalised ``"#rrggbb"`` lower-case hex on OK, or
    ``None`` if the user cancels / closes the dialog.
    """
    dlg = ThemedColorChooser(parent, initial=initial, title=title)
    try:
        dlg.wait_window()
    except tk.TclError:
        return None
    return dlg.result


__all__ = [
    "DEFAULT_COLOR",
    "ThemedColorChooser",
    "pick_color",
]
