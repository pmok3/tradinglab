"""Color palette popup — advanced HSV picker (default) + honeycomb swatches.

A modal :class:`tkinter.Toplevel` for picking a hex color. Two views:

* **Advanced** *(default)* — a saturation/value gradient square plus a
  hue strip, a live preview swatch and a hex entry. This is the
  "real" picker most users want; it is shown by default.
* **Swatches** — the original 19-cell honeycomb + 6 grayscale row,
  reachable via the view toggle for quick palette picks.

Returns the chosen color hex (e.g. ``"#1f77b4"``) or ``None`` if the
user cancels.

Design notes
------------

* **No PIL.** The SV gradient is rendered with :class:`tkinter.PhotoImage`
  ``put`` data built from a numpy-vectorised HSV→RGB conversion. numpy is
  a core dependency; PIL is *not* bundled in the frozen ``.exe``.

* **Honeycomb geometry (Swatches view).** Cells are flat-top hexagons:
  center + two rings = 1 + 6 + 12 = 19 swatches. The 19/6 table lengths
  are pinned by the smoke suite and must not drift.

* **Buttons always visible.** The window is larger and resizable with a
  ``minsize`` floor so the OK / Cancel footer is never clipped (the old
  260×260 fixed window hid the footer).

* **Modal via ``wait_window``.** :func:`pick_color` blocks until the
  popup is dismissed and returns the chosen hex (or ``None``).
"""

from __future__ import annotations

import colorsys
import math
import tkinter as tk
from tkinter import colorchooser, ttk

import numpy as np

from ._modal_base import BaseModalDialog, protect_combobox_wheel
from .native_theme import apply_canvas_theme, current_theme

# 19-swatch honeycomb laid out as: index 0 = center; indices 1-6 =
# ring 1 in CW order starting at axial (1, 0); indices 7-18 = ring 2
# in CW order starting at axial (2, 0).
_HONEYCOMB_COLORS: tuple[str, ...] = (
    # Center
    "#ffffff",
    # Ring 1 — six pure hues at full saturation, anchored at red (E)
    "#e51d1d",  # red
    "#e5a41d",  # orange
    "#a4e51d",  # yellow-green
    "#1de5a4",  # teal
    "#1da4e5",  # blue
    "#a41de5",  # magenta-purple
    # Ring 2 — alternating light tint / dark shade pairs around the
    # outside, ordered CW from axial (2, 0) so each pair sits next
    # to its parent ring-1 hue.
    "#ff8080", "#a60000",      # red pair
    "#ffcc80", "#a66600",      # orange pair
    "#d9ff80", "#5c8000",      # yellow-green pair
    "#80ffcc", "#008055",      # teal pair
    "#80ccff", "#003d80",      # blue pair
    "#cc80ff", "#5c0080",      # purple pair
)

# Six grayscale swatches drawn as a row beneath the honeycomb so the
# user can pick a neutral without leaving the palette.
_GRAYSCALE_COLORS: tuple[str, ...] = (
    "#000000", "#333333", "#666666", "#999999", "#cccccc", "#ffffff",
)

# Hex cell radius (center → vertex), in pixels.
_HEX_RADIUS = 22

# Advanced-view gradient dimensions (pixels).
_SV_W = 220
_SV_H = 170
_HUE_W = 220
_HUE_H = 18


def _axial_to_pixel(q: int, r: int, size: float) -> tuple[float, float]:
    """Flat-top axial → pixel.

    See https://www.redblobgames.com/grids/hexagons/#hex-to-pixel —
    flat-top orientation is ``x = 1.5*size*q``, ``y = √3*size*(r + q/2)``.
    """
    x = 1.5 * size * q
    y = math.sqrt(3.0) * size * (r + q / 2.0)
    return x, y


def _ring_axials(n: int) -> list[tuple[int, int]]:
    """Axial coords of the cells that make up ring ``n``."""
    if n == 0:
        return [(0, 0)]
    dirs = [(-1, +1), (-1, 0), (0, -1), (+1, -1), (+1, 0), (0, +1)]
    cells: list[tuple[int, int]] = []
    q, r = n, 0
    for dq, dr in dirs:
        for _ in range(n):
            cells.append((q, r))
            q += dq
            r += dr
    return cells


def _hex_polygon_points(cx: float, cy: float, size: float) -> list[float]:
    """Return the 12-element flattened (x, y) point list for a flat-top
    hexagon centered at ``(cx, cy)`` with circumradius ``size``."""
    pts: list[float] = []
    for k in range(6):
        ang = math.radians(60.0 * k)
        pts.append(cx + size * math.cos(ang))
        pts.append(cy + size * math.sin(ang))
    return pts


# ---------------------------------------------------------------------------
# Pure HSV/hex helpers (testable without Tk)
# ---------------------------------------------------------------------------


def hsv_to_hex(h: float, s: float, v: float) -> str:
    """Convert HSV (each in ``[0, 1]``) to a ``"#rrggbb"`` lowercase hex."""
    r, g, b = colorsys.hsv_to_rgb(
        max(0.0, min(1.0, h)), max(0.0, min(1.0, s)), max(0.0, min(1.0, v)),
    )
    return f"#{int(round(r * 255)):02x}{int(round(g * 255)):02x}{int(round(b * 255)):02x}"


def hex_to_hsv(hexstr: str) -> tuple[float, float, float]:
    """Convert a hex color (``#rgb`` or ``#rrggbb``) to HSV in ``[0, 1]``."""
    s = (hexstr or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        s = "888888"
    try:
        r = int(s[0:2], 16) / 255.0
        g = int(s[2:4], 16) / 255.0
        b = int(s[4:6], 16) / 255.0
    except ValueError:
        r = g = b = 0.5333333333333333
    return colorsys.rgb_to_hsv(r, g, b)


def _sv_rgb_arrays(hue01: float, w: int, h: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorised SV gradient for a fixed hue.

    x → saturation ``0..1`` (left→right); y → value ``1..0`` (top→bottom).
    Returns three ``uint8`` ``(h, w)`` arrays (R, G, B).
    """
    s = np.linspace(0.0, 1.0, w, dtype=float)[None, :]
    v = np.linspace(1.0, 0.0, h, dtype=float)[:, None]
    hh = (hue01 * 6.0) % 6.0
    i = int(math.floor(hh)) % 6
    f = hh - math.floor(hh)
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    big_v = v * np.ones((1, w))
    if i == 0:
        r, g, b = big_v, t, p
    elif i == 1:
        r, g, b = q, big_v, p
    elif i == 2:
        r, g, b = p, big_v, t
    elif i == 3:
        r, g, b = p, q, big_v
    elif i == 4:
        r, g, b = t, p, big_v
    else:
        r, g, b = big_v, p, q

    def to_u8(a: np.ndarray) -> np.ndarray:
        return np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8)

    return to_u8(r), to_u8(g), to_u8(b)


def _put_data(r: np.ndarray, g: np.ndarray, b: np.ndarray) -> str:
    """Build a Tk ``PhotoImage.put`` data string from RGB ``(h, w)`` arrays."""
    h, w = r.shape
    rows: list[str] = []
    for y in range(h):
        ry, gy, by = r[y], g[y], b[y]
        cells = [f"#{int(ry[x]):02x}{int(gy[x]):02x}{int(by[x]):02x}" for x in range(w)]
        rows.append("{" + " ".join(cells) + "}")
    return " ".join(rows)


class HexColorPalette(BaseModalDialog):
    """Modal color picker — Advanced HSV + Swatches honeycomb side-by-side.

    Use :func:`pick_color` instead of constructing this class directly —
    it blocks via ``wait_window`` and returns the result.

    Layout (audit ``color-picker-side-by-side``): the historical
    Advanced-or-Swatches radio toggle is gone. Both panes are
    permanently visible: Advanced HSV picker on the left,
    Swatches honeycomb on the right, hex entry + preview swatch
    under the swatches column (the user's "more final pick" affordance).
    Dialog is wider (760×420) to accommodate both columns.
    """

    def __init__(self, parent: tk.Misc, initial: str = "#888888",
                 title: str = "Pick a color") -> None:
        super().__init__(
            parent,
            title=title,
            geometry_key="dlg.color_palette",
            default_geometry="760x420",
            resizable=(True, True),
        )
        try:
            self.minsize(720, 400)
        except tk.TclError:
            pass
        self.result: str | None = None
        self._theme = current_theme(parent)
        self.configure(background=self._theme.get("win_bg", "#f0f0f0"))
        self._initial: str = self._normalise(initial)
        self._current: str = self._initial
        self._hsv: tuple[float, float, float] = hex_to_hsv(self._initial)
        self._cell_items: list[tuple[int, str]] = []
        self._sv_image: tk.PhotoImage | None = None
        self._hue_image: tk.PhotoImage | None = None
        self._build_ui()
        protect_combobox_wheel(self)
        self._finalize_modal(primary=self._on_ok, cancel=self._on_cancel)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        bg = self._theme.get("win_bg", "#f0f0f0")
        fg = self._theme.get("text", "#000000")
        outer = tk.Frame(self, padx=10, pady=10, bg=bg)
        outer.pack(fill="both", expand=True)

        # Side-by-side body: Advanced HSV on left, Swatches on right.
        # Audit ``color-picker-side-by-side``.
        body = tk.Frame(outer, bg=bg)
        body.pack(side="top", fill="both", expand=True)
        self._advanced_frame = tk.Frame(body, bg=bg)
        self._swatches_frame = tk.Frame(body, bg=bg)
        # Pack the swatches column first so it lands on the right
        # (Tk's side="right" semantics); advanced fills the rest of
        # the row on the left.
        self._swatches_frame.pack(side="right", anchor="n", padx=(10, 0))
        self._advanced_frame.pack(side="left", fill="both", expand=True)
        self._build_advanced(self._advanced_frame, bg, fg)
        self._build_swatches(self._swatches_frame, bg, fg)

        # Footer — always visible OK / Cancel (+ System… escape hatch).
        bar = tk.Frame(outer, bg=bg)
        bar.pack(side="bottom", fill="x", pady=(10, 0))
        self._cancel_btn = ttk.Button(bar, text="Cancel", command=self._on_cancel)
        self._cancel_btn.pack(side="right")
        self._ok_btn = ttk.Button(bar, text="OK", command=self._on_ok)
        self._ok_btn.pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="System…", command=self._on_custom).pack(side="left")

    def _build_advanced(self, parent: tk.Frame, bg: str, fg: str) -> None:
        # Saturation/Value gradient square.
        sv = tk.Canvas(parent, width=_SV_W, height=_SV_H,
                       highlightthickness=0, bd=0, cursor="crosshair")
        apply_canvas_theme(sv, self._theme)
        sv.pack(side="top", anchor="w")
        self._sv_canvas = sv
        sv.bind("<Button-1>", self._on_sv_drag)
        sv.bind("<B1-Motion>", self._on_sv_drag)

        # Hue strip.
        hue = tk.Canvas(parent, width=_HUE_W, height=_HUE_H,
                        highlightthickness=0, bd=0, cursor="sb_h_double_arrow")
        apply_canvas_theme(hue, self._theme)
        hue.pack(side="top", anchor="w", pady=(6, 0))
        self._hue_canvas = hue
        hue.bind("<Button-1>", self._on_hue_drag)
        hue.bind("<B1-Motion>", self._on_hue_drag)

        self._render_hue()
        self._render_sv()
        self._draw_markers()

    def _build_swatches(self, parent: tk.Frame, bg: str, fg: str) -> None:
        size = float(_HEX_RADIUS)
        all_cells = (_ring_axials(0) + _ring_axials(1) + _ring_axials(2))
        xs: list[float] = []
        ys: list[float] = []
        for q, r in all_cells:
            x, y = _axial_to_pixel(q, r, size)
            xs.append(x)
            ys.append(y)
        margin = size + 4
        cw = int(max(xs) - min(xs) + 2 * margin)
        ch = int(max(ys) - min(ys) + 2 * margin)
        ox = -min(xs) + margin
        oy = -min(ys) + margin

        canvas = tk.Canvas(parent, width=cw, height=ch,
                           highlightthickness=0, bd=0)
        apply_canvas_theme(canvas, self._theme)
        canvas.pack(side="top")
        self._canvas = canvas

        idx = 0
        for n in (0, 1, 2):
            for (q, r) in _ring_axials(n):
                cx, cy = _axial_to_pixel(q, r, size)
                cx += ox
                cy += oy
                color = _HONEYCOMB_COLORS[idx]
                pts = _hex_polygon_points(cx, cy, size - 1.5)
                stroke = "#222222" if color.lower() == self._initial.lower() else "#777777"
                w = 3 if color.lower() == self._initial.lower() else 1
                cid = canvas.create_polygon(*pts, fill=color, outline=stroke, width=w)
                canvas.tag_bind(cid, "<Button-1>", lambda _e, c=color: self._on_pick(c))
                canvas.tag_bind(cid, "<Enter>",
                                lambda _e, i=cid: canvas.itemconfigure(i, width=2))
                canvas.tag_bind(
                    cid, "<Leave>",
                    lambda _e, i=cid, c=color: canvas.itemconfigure(
                        i, width=(3 if c.lower() == self._initial.lower() else 1),
                    ),
                )
                self._cell_items.append((cid, color))
                idx += 1

        gs_frame = tk.Frame(parent, bg=bg)
        gs_frame.pack(side="top", pady=(8, 0))
        tk.Label(gs_frame, text="Gray:", bg=bg, fg=fg).pack(side="left", padx=(0, 6))
        for color in _GRAYSCALE_COLORS:
            btn = tk.Frame(gs_frame, width=24, height=20, bg=color,
                           bd=1, relief="solid", cursor="hand2")
            btn.pack_propagate(False)
            btn.pack(side="left", padx=1)
            btn.bind("<Button-1>", lambda _e, c=color: self._on_pick(c))

        # Preview + hex entry row — mounted UNDER the swatches column
        # so the "final pick" affordances (the touch-friendly swatch
        # grid + the precise hex entry) sit together on the right of
        # the dialog. Audit ``color-picker-side-by-side``.
        row = tk.Frame(parent, bg=bg)
        row.pack(side="top", anchor="w", fill="x", pady=(8, 0))
        self._preview = tk.Frame(row, width=40, height=24, bg=self._current,
                                 bd=1, relief="solid")
        self._preview.pack_propagate(False)
        self._preview.pack(side="left")
        tk.Label(row, text="Hex:", bg=bg, fg=fg).pack(side="left", padx=(10, 4))
        self._hex_var = tk.StringVar(self, value=self._current)
        self._hex_entry = ttk.Entry(row, textvariable=self._hex_var, width=10)
        self._hex_entry.pack(side="left")
        self._hex_entry.bind("<Return>", lambda _e: self._on_hex_entry())
        self._hex_entry.bind("<FocusOut>", lambda _e: self._on_hex_entry())

    # ------------------------------------------------------------------
    # Advanced-view rendering
    # ------------------------------------------------------------------

    def _render_hue(self) -> None:
        try:
            img = tk.PhotoImage(master=self, width=_HUE_W, height=_HUE_H)
            hues = np.linspace(0.0, 1.0, _HUE_W, endpoint=False)
            cells = [hsv_to_hex(float(h), 1.0, 1.0) for h in hues]
            row = "{" + " ".join(cells) + "}"
            img.put(" ".join([row] * _HUE_H))
            self._hue_image = img
            self._hue_canvas.delete("hueimg")
            self._hue_canvas.create_image(0, 0, anchor="nw", image=img, tags="hueimg")
        except tk.TclError:
            pass

    def _render_sv(self) -> None:
        try:
            r, g, b = _sv_rgb_arrays(self._hsv[0], _SV_W, _SV_H)
            img = tk.PhotoImage(master=self, width=_SV_W, height=_SV_H)
            img.put(_put_data(r, g, b))
            self._sv_image = img
            self._sv_canvas.delete("svimg")
            self._sv_canvas.create_image(0, 0, anchor="nw", image=img, tags="svimg")
            self._sv_canvas.tag_lower("svimg")
        except tk.TclError:
            pass

    def _draw_markers(self) -> None:
        h, s, v = self._hsv
        try:
            mx = max(0, min(_SV_W - 1, int(round(s * (_SV_W - 1)))))
            my = max(0, min(_SV_H - 1, int(round((1.0 - v) * (_SV_H - 1)))))
            self._sv_canvas.delete("svmark")
            ring = "#ffffff" if v < 0.6 else "#000000"
            self._sv_canvas.create_oval(
                mx - 5, my - 5, mx + 5, my + 5,
                outline=ring, width=2, tags="svmark",
            )
            hx = max(0, min(_HUE_W - 1, int(round(h * (_HUE_W - 1)))))
            self._hue_canvas.delete("huemark")
            self._hue_canvas.create_rectangle(
                hx - 2, 0, hx + 2, _HUE_H, outline="#000000", width=1, tags="huemark",
            )
            self._hue_canvas.create_rectangle(
                hx - 1, 0, hx + 1, _HUE_H, outline="#ffffff", width=1, tags="huemark",
            )
        except tk.TclError:
            pass

    def _sync_advanced_widgets(self) -> None:
        try:
            self._preview.configure(bg=self._current)
        except tk.TclError:
            pass
        if self._hex_var.get().lower() != self._current:
            self._hex_var.set(self._current)

    def _set_hsv(self, h: float, s: float, v: float, *, render_sv: bool = False) -> None:
        self._hsv = (h, s, v)
        self._current = hsv_to_hex(h, s, v)
        if render_sv:
            self._render_sv()
        self._draw_markers()
        self._sync_advanced_widgets()

    def _set_current(self, color: str) -> None:
        self._current = self._normalise(color)
        self._hsv = hex_to_hsv(self._current)
        self._render_sv()
        self._draw_markers()
        self._sync_advanced_widgets()

    # ------------------------------------------------------------------
    # Advanced-view interactions
    # ------------------------------------------------------------------

    def _on_sv_drag(self, event: tk.Event) -> None:
        s = max(0.0, min(1.0, event.x / float(_SV_W - 1)))
        v = 1.0 - max(0.0, min(1.0, event.y / float(_SV_H - 1)))
        self._set_hsv(self._hsv[0], s, v, render_sv=False)

    def _on_hue_drag(self, event: tk.Event) -> None:
        h = max(0.0, min(1.0, event.x / float(_HUE_W - 1)))
        self._set_hsv(h, self._hsv[1], self._hsv[2], render_sv=True)

    def _on_hex_entry(self) -> None:
        raw = self._hex_var.get().strip()
        norm = self._normalise(raw)
        if len(norm) == 7 and norm.startswith("#"):
            self._set_current(norm)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_ok(self) -> None:
        self.result = self._normalise(self._current)
        self._dismiss()

    def _on_pick(self, color: str) -> None:
        self.result = self._normalise(color)
        self._dismiss()

    def _on_custom(self) -> None:
        try:
            res = colorchooser.askcolor(
                color=self._current, parent=self, title="Pick custom color",
            )
        except tk.TclError:
            res = (None, None)
        if res and res[1]:
            self._set_current(self._normalise(res[1]))

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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise(color: str) -> str:
        """Normalise to ``"#rrggbb"`` lower-case form when possible."""
        s = (color or "").strip()
        if not s:
            return "#888888"
        if s.startswith("#") and len(s) == 7:
            return "#" + s[1:].lower()
        if s.startswith("#") and len(s) == 4:
            r, g, b = s[1], s[2], s[3]
            return ("#" + r + r + g + g + b + b).lower()
        return s


def pick_color(parent: tk.Misc, initial: str = "#888888",
               title: str = "Pick a color") -> str | None:
    """Open the color palette modally and return the chosen color.

    Returns ``None`` if the user cancels (Esc / Cancel / WM close).
    Blocks the calling thread via ``wait_window`` — the caller must
    be on the Tk main thread.
    """
    dlg = HexColorPalette(parent, initial=initial, title=title)
    try:
        dlg.wait_window()
    except tk.TclError:
        return None
    return dlg.result
