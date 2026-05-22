"""Settings adapter for ChartStack.

Thin read-only facade over :mod:`tradinglab.settings`. Owns the
defaults table for the ``chartstack.*`` key namespace so the
settings dialog and the ChartStack code agree on a single source of
truth.

Why a separate module? Three reasons:

* Tests can import ``settings_adapter`` without paying the cost of
  the full panel module (matplotlib + Tk).
* The defaults table is a quick reference for spec writers; living
  in its own file makes diffs obvious.
* Card-count + binding-mode parsing happen here, not scattered
  across the panel and controller, so the clamping rule
  (``MIN..MAX``) lives in one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .binding import BindingMode


DEFAULTS: dict[str, Any] = {
    "chartstack.enabled": False,             # M1 default; flips True at M3
    "chartstack.cards.count": 3,
    "chartstack.cards.max": 6,
    "chartstack.cards.min": 3,
    "chartstack.binding.mode": "HYBRID",
    "chartstack.status_preset": "auto-by-phase",
    "chartstack.alerts.audio_muted": False,
    "chartstack.alerts.rvol_1m": 2.5,
    "chartstack.alerts.rvol_5m": 1.8,
    "chartstack.alerts.atr_expansion": 1.8,
    "chartstack.popout.size": "600x400",
    "chartstack.visible": True,
    "chartstack.card_width_px": 220,
    "chartstack.card_min_height_px": 96,
    "chartstack.sparkline_bar_count": 60,
    # M4 visual polish toggles. Each overlay is individually
    # togglable so a trader who finds the screen too busy can drop
    # any one without losing the others.
    "chartstack.show_vwap": True,
    "chartstack.show_pmh_pml": True,
    "chartstack.show_last_candles": True,
    "chartstack.volume_stroke_encoding": True,
}


def get(key: str) -> Any:
    """Return the live setting value, falling back to :data:`DEFAULTS`."""
    # `settings` is a top-level module on the `tradinglab` package,
    # not on `tradinglab.gui` — route through the top-level package.
    from ... import settings as _settings
    if key in DEFAULTS:
        return _settings.get(key, DEFAULTS[key])
    return _settings.get(key)


def is_enabled() -> bool:
    """Return whether the ChartStack panel is enabled in this session."""
    return bool(get("chartstack.enabled"))


def card_count() -> int:
    """Return the configured card count, clamped to ``[min, max]``."""
    raw = get("chartstack.cards.count")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = int(DEFAULTS["chartstack.cards.count"])
    lo = int(get("chartstack.cards.min"))
    hi = int(get("chartstack.cards.max"))
    if lo > hi:  # defensive — bad user override shouldn't crash
        lo, hi = hi, lo
    return max(lo, min(hi, n))


def binding_mode() -> BindingMode:
    """Return the configured :class:`BindingMode`, defaulting to ``HYBRID``."""
    from .binding import BindingMode
    raw = get("chartstack.binding.mode")
    if isinstance(raw, BindingMode):
        return raw
    if isinstance(raw, str):
        try:
            return BindingMode[raw.upper()]
        except KeyError:
            pass
    return BindingMode.HYBRID


__all__ = ["DEFAULTS", "get", "is_enabled", "card_count", "binding_mode"]
