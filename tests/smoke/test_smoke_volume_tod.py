"""Per-feature smoke coverage for volume time-of-day shading."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.smoke._helpers import _pump, _pump_until


def _daily_and_intraday(symbol_days: int = 25):
    from tradinglab.models import Candle

    daily = []
    intraday = []
    base = datetime(2035, 1, 1, 12, 0, tzinfo=timezone.utc)
    for day in range(symbol_days):
        day_dt = base + timedelta(days=day)
        full_volume = 100_000 + (day * 1_000)
        daily.append(Candle(
            date=day_dt,
            open=100.0 + day,
            high=102.0 + day,
            low=99.0 + day,
            close=101.0 + day,
            volume=full_volume,
            session="regular",
        ))
        open_utc = day_dt.replace(hour=14, minute=30, second=0, microsecond=0)
        per_bar = full_volume / 78.0
        for i in range(78):
            intraday.append(Candle(
                date=open_utc + timedelta(minutes=5 * i),
                open=100.0 + day,
                high=101.0 + day,
                low=99.0 + day,
                close=100.5 + day,
                volume=per_bar,
                session="regular",
            ))
    return daily, intraday


def test_volume_tod_live_wall_clock_overlay_renders_from_warm_cache(app) -> None:
    """Live-mode 1d chart paints TOD patches when matching 5m cache exists."""
    from tradinglab import defaults as _defaults_mod

    source = "yfinance"
    symbol = "VTODLIVE"
    daily, intraday = _daily_and_intraday()
    primary_key = (source, symbol, "1d")
    intraday_key = (source, symbol, "5m")
    sentinel = object()
    old_cache = {
        primary_key: app._full_cache.get(primary_key, sentinel),
        intraday_key: app._full_cache.get(intraday_key, sentinel),
    }
    old_source = app.source_var.get()
    old_ticker = app.ticker_var.get()
    old_interval = app.interval_var.get()
    old_compare = bool(app.compare_var.get())
    old_compare_ticker = app.compare_ticker_var.get()
    old_enabled = bool(_defaults_mod.get("volume_tod_enabled"))
    old_is_sandbox_active = app._is_sandbox_active
    cutoff_ms = int(
        datetime(2035, 1, 25, 16, 0, tzinfo=timezone.utc).timestamp() * 1000,
    )

    try:
        app._full_cache[primary_key] = daily
        app._full_cache[intraday_key] = intraday
        app.source_var.set(source)
        app.ticker_var.set(symbol)
        app.interval_var.set("1d")
        app.compare_var.set(False)
        app._is_sandbox_active = lambda: False  # type: ignore[method-assign]
        with patch("time.time", return_value=cutoff_ms / 1000.0):
            assert app._now_ms_for_slot("primary") == cutoff_ms
            app.set_volume_tod_enabled(True)
            app._load_data()
            assert _pump_until(
                app,
                lambda: bool(
                    app._panel_state.get("primary", {}).get("vol_tod_patches"),
                ),
                timeout=3.0,
            )
        patches = app._panel_state["primary"]["vol_tod_patches"]
        assert patches, "expected live wall-clock volume TOD patches"
        assert any(
            p.has_intraday
            and p.full_day_volume > 0
            and (p.filled_height / p.full_day_volume) > 0
            for p in patches
        ), "expected at least one partially realized intraday-backed volume bar"
    finally:
        app._is_sandbox_active = old_is_sandbox_active  # type: ignore[method-assign]
        try:
            app.set_volume_tod_enabled(old_enabled)
        except Exception:  # noqa: BLE001
            pass
        for key, old_value in old_cache.items():
            if old_value is sentinel:
                app._full_cache.pop(key, None)
            else:
                app._full_cache[key] = old_value
        app.source_var.set(old_source)
        app.ticker_var.set(old_ticker)
        app.interval_var.set(old_interval)
        app.compare_ticker_var.set(old_compare_ticker)
        app.compare_var.set(old_compare)
        try:
            app._load_data()
            _pump(app, 0.1)
        except Exception:  # noqa: BLE001
            pass
