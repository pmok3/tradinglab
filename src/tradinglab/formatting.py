"""Small display-formatting helpers."""

from __future__ import annotations

from .core.timezones import get_zoneinfo


def fmt_volume(v: float) -> str:
    """Format a share/contract volume as a short human-readable string.

    Standardized on ``.2f`` precision across all magnitudes (B/M/K) so
    successive intervals don't visually "snap" by a decimal place when
    a level boundary is crossed — e.g. a stock with ``999_500``
    average volume formatting as ``"999.5K"`` (1-decimal) and then
    flipping to ``"1.00M"`` (2-decimal) the next bar is jarring. The
    sub-1K branch keeps ``.0f`` because partial shares are not a thing.
    Audit ``volume-formatter-unify``.
    """
    if v >= 1_000_000_000:
        return f"{v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v / 1_000:.2f}K"
    return f"{v:.0f}"


def format_dt(dt, fmt: str, tz_name: str = "") -> str:
    """Format ``dt`` with ``fmt`` after optionally converting to ``tz_name``.

    Empty ``tz_name`` (the default) or a naive ``dt`` short-circuits to a
    plain ``strftime`` — so existing call sites that pass nothing keep
    today's behavior. A bad IANA name (or missing tzdata on Windows)
    silently falls back to raw ``strftime`` rather than blow up the
    render path.

    Used only at intraday display sites (x-axis ``%H:%M`` ticks, hover
    tooltip, OHLC table rows). Daily/weekly/monthly bars are not
    converted because a daily bar represents an exchange trading date,
    not an instant — shifting "Apr 24 ET" to Tokyo time would relabel
    it "Apr 25", which is semantically wrong.
    """
    if tz_name and getattr(dt, "tzinfo", None) is not None:
        try:
            zone = get_zoneinfo(tz_name)
            if zone is not None:
                dt = dt.astimezone(zone)
        except Exception:  # noqa: BLE001
            pass
    return dt.strftime(fmt)
