"""Named-font baseline configuration.

By default Tk's ``TkDefaultFont`` / ``TkTextFont`` / ``TkMenuFont``
/ ``TkHeadingFont`` / etc. resolve to whatever the X11 X-resources
or Windows display settings happen to pick. On a fresh Windows 10
install that's usually "Segoe UI 9", which looks fine; on legacy
systems, X11 desktops with a quirky `.Xresources`, or some
container images, it falls back to bitmap monospace fonts that
read as a 1990s shareware app.

We avoid that by explicitly configuring the named fonts at app
startup so every Tk widget that says ``font="TkDefaultFont"`` or
``font=("TkDefaultFont", N, "bold")`` lands on a known
proportional sans family at a known size. Fixed-pitch widgets
(text panes, log views) use ``TkFixedFont`` which we also pin.

Family selection
----------------
Windows: ``Segoe UI`` (matches File Explorer, the taskbar, every
other modern Win32 app) for proportional; ``Consolas`` for fixed.

macOS: leave alone — Tk on Aqua picks ``.AppleSystemUIFont``
automatically, which already matches everything else on the OS.

Linux / *BSD: ``DejaVu Sans`` for proportional, ``DejaVu Sans Mono``
for fixed. These ship with virtually every major distribution and
match the GNOME / KDE default look. If they're missing, Tk silently
falls back to whatever's closest, which is OK.

Sizes & UI scale
----------------
Single source of truth in ``DEFAULT_SIZE`` / ``FIXED_SIZE``. A
"UI scale" multiplier (``0.85`` / ``1.0`` / ``1.15`` / ``1.30``)
lets users with hi-DPI displays, mid-stage presbyopia, or just a
preference for larger text dial the whole chrome up or down in
one toggle. Audit ``font-scaling``. The scale is applied at
``configure_named_fonts(root, scale=…)`` time; the resulting
``size`` field is ``round(DEFAULT_SIZE * scale)``. Multiple calls
with different scales re-write the fonts (idempotency was a
launch-time-only invariant; a settings-driven re-configure is
intentional). Out-of-band scales clamp to the nearest valid
multiplier (``UI_SCALES``) so a settings.json corruption can't
push the chrome to an unusable size.

Public API
----------
``configure_named_fonts(root, scale=1.0)`` — called once from
:meth:`ChartApp.__init__` immediately after ``super().__init__()``
so widget construction sees the configured fonts. Re-callable
to re-scale at runtime.

``UI_SCALES`` — the supported scale-multiplier tuple (sorted).
``DEFAULT_UI_SCALE`` — ``1.0``. ``clamp_ui_scale(value)`` —
rounds to the nearest allowed scale; non-finite or out-of-range
inputs fall back to ``DEFAULT_UI_SCALE``.
"""
from __future__ import annotations

import math
import sys
import tkinter as tk
import tkinter.font as tkfont

DEFAULT_SIZE = 9
FIXED_SIZE = 10

# Supported UI scale multipliers. Picked to cover the three
# common needs:
#   0.85 — denser layout for laptops, 4k displays at native DPI.
#   1.0  — default (what every existing screenshot expects).
#   1.15 — modest accessibility bump for users with mild visual
#          fatigue / mid-stage presbyopia.
#   1.30 — significant bump for users who genuinely need the
#          chrome to be readable from across the room or for
#          large-font hi-DPI setups where Windows scaling alone
#          isn't enough. Audit ``font-scaling``.
UI_SCALES: tuple[float, ...] = (0.85, 1.0, 1.15, 1.30)
DEFAULT_UI_SCALE: float = 1.0


def clamp_ui_scale(value: float) -> float:
    """Round ``value`` to the nearest entry in :data:`UI_SCALES`.

    Non-finite / non-numeric / out-of-range inputs fall back to
    :data:`DEFAULT_UI_SCALE`. Used by the loader so a corrupted
    ``settings.json["ui_scale"]`` (e.g. ``"large"``, ``None``,
    ``NaN``) can't make the chrome unreadable on startup. Audit
    ``font-scaling``.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return DEFAULT_UI_SCALE
    if not math.isfinite(v) or v <= 0:
        return DEFAULT_UI_SCALE
    # Cap extremes so an attacker-style 1e6 doesn't blow the
    # Tk font subsystem up.
    if v <= UI_SCALES[0]:
        return UI_SCALES[0]
    if v >= UI_SCALES[-1]:
        return UI_SCALES[-1]
    # Pick the closest allowed scale.
    return min(UI_SCALES, key=lambda s: abs(s - v))


if sys.platform.startswith("win"):
    _PROPORTIONAL_FAMILY = "Segoe UI"
    _FIXED_FAMILY = "Consolas"
elif sys.platform == "darwin":
    _PROPORTIONAL_FAMILY = ""
    _FIXED_FAMILY = ""
else:
    _PROPORTIONAL_FAMILY = "DejaVu Sans"
    _FIXED_FAMILY = "DejaVu Sans Mono"

# Names defined by the Tk core that we want to align with our
# baseline. Any widget that says ``font="TkDefaultFont"`` (or
# falls back to it implicitly) picks these up.
_PROPORTIONAL_NAMED_FONTS: tuple[str, ...] = (
    "TkDefaultFont",
    "TkTextFont",
    "TkMenuFont",
    "TkHeadingFont",
    "TkCaptionFont",
    "TkSmallCaptionFont",
    "TkIconFont",
    "TkTooltipFont",
)

_FIXED_NAMED_FONTS: tuple[str, ...] = (
    "TkFixedFont",
)

_CONFIGURED = False
_CURRENT_SCALE: float = DEFAULT_UI_SCALE


def configure_named_fonts(
    root: tk.Misc,
    *,
    scale: float = DEFAULT_UI_SCALE,
) -> None:
    """Apply the baseline font configuration to every named Tk font.

    Safe to call multiple times. First call sets the configured
    flag; subsequent calls with the same ``scale`` are no-ops, but
    a call with a *different* ``scale`` re-writes every font (the
    use case: the user just changed UI scale in Settings and the
    chrome should immediately reflect the new size — no relaunch
    required). Silently swallows ``TclError`` so a Tk build that's
    missing a named font (very old / stripped builds) doesn't take
    the whole app down. Audit ``font-scaling``.
    """
    global _CONFIGURED, _CURRENT_SCALE
    clamped = clamp_ui_scale(scale)
    if _CONFIGURED and clamped == _CURRENT_SCALE:
        return
    # macOS Tk uses the OS system font; touching it makes things
    # worse, not better. Leave the named fonts at their defaults.
    if not _PROPORTIONAL_FAMILY:
        _CONFIGURED = True
        _CURRENT_SCALE = clamped
        return
    prop_size = max(6, int(round(DEFAULT_SIZE * clamped)))
    fixed_size = max(6, int(round(FIXED_SIZE * clamped)))
    for name in _PROPORTIONAL_NAMED_FONTS:
        try:
            f = tkfont.nametofont(name, root=root)
        except tk.TclError:
            continue
        try:
            current_weight = f.cget("weight") or "normal"
            f.configure(family=_PROPORTIONAL_FAMILY,
                        size=prop_size,
                        weight=current_weight)
        except tk.TclError:
            continue
    for name in _FIXED_NAMED_FONTS:
        try:
            f = tkfont.nametofont(name, root=root)
        except tk.TclError:
            continue
        try:
            f.configure(family=_FIXED_FAMILY, size=fixed_size)
        except tk.TclError:
            continue
    _CONFIGURED = True
    _CURRENT_SCALE = clamped


def current_ui_scale() -> float:
    """Return the scale multiplier last applied via
    :func:`configure_named_fonts`. Used by the Settings dialog
    to seed its initial-state snapshot. Audit ``font-scaling``."""
    return _CURRENT_SCALE


def _reset_for_tests() -> None:
    """Test hook: clear the idempotency flag so a fresh root can
    re-configure. **Not** part of the public API.
    """
    global _CONFIGURED, _CURRENT_SCALE
    _CONFIGURED = False
    _CURRENT_SCALE = DEFAULT_UI_SCALE


__all__ = [
    "DEFAULT_SIZE",
    "DEFAULT_UI_SCALE",
    "FIXED_SIZE",
    "UI_SCALES",
    "clamp_ui_scale",
    "configure_named_fonts",
    "current_ui_scale",
]
