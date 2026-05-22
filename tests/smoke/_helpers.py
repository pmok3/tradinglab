"""Shared helpers for the smoke test suite.

This module is intentionally NOT named ``test_*`` so pytest does not
collect it. Both the legacy mega-test (``test_smoke_full.py``) and the
new per-feature subset files import their helpers + ``_stub_yfinance``
from here so there is exactly one source of truth.

The ``app`` fixture lives in ``conftest.py``; helpers here are pure
functions that take an already-built ``ChartApp`` and pump events,
synthesize matplotlib events, etc.
"""
from __future__ import annotations

import os
import tempfile
import time
from datetime import datetime, timedelta

# --------------------------------------------------------------------- env --
#
# Redirect persistence into a throwaway tempdir before any tradinglab
# import happens. Mirrors the original module-level setup in
# ``test_smoke_full.py`` so a smoke run never overwrites the user's real
# %LOCALAPPDATA%/tradinglab cache.
#
# Idempotent: if conftest.py already set the cache dir, leave it alone.
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if "TRADINGLAB_CACHE_DIR" not in os.environ:
    os.environ["TRADINGLAB_CACHE_DIR"] = tempfile.mkdtemp(
        prefix="tradinglab_smoke_")


# --------------------------------------------------- deterministic candles --

def _fake_candles(n: int, start_price: float = 100.0, step_min: int = 5,
                  session_pattern: str = "regular"):
    """Produce deterministic fake intraday candles for tests."""
    from tradinglab.models import Candle
    out = []
    t = datetime(2026, 4, 20, 9, 30)
    price = start_price
    for i in range(n):
        op = price
        hi = price + 0.5
        lo = price - 0.5
        cl = price + (0.2 if i % 2 == 0 else -0.2)
        price = cl
        sess = session_pattern if session_pattern != "mix" else (
            "pre" if i < 5 else ("post" if i >= n - 5 else "regular")
        )
        out.append(Candle(date=t, open=op, high=hi, low=lo, close=cl,
                          volume=1000 + i, session=sess))
        t = t + timedelta(minutes=step_min)
    return out


def _stub_yfinance():
    """Replace the live yfinance fetcher with a deterministic in-memory fake.

    Also redirects the historical-events ``"yfinance"`` source to the
    synthetic generator so that any sandbox session started during smoke
    runs doesn't hit the live yfinance network in its events prefetch
    (which would saturate ``_fetch_executor`` and starve the bars
    fetcher under test).
    """
    from tradinglab import data as _data_pkg

    def fake_fetch(ticker: str, interval: str):
        return _fake_candles(150, start_price=100.0 + (hash(ticker) % 50),
                             step_min=5, session_pattern="mix")

    _data_pkg.DATA_SOURCES["yfinance"] = fake_fetch

    try:
        from tradinglab import events as _events_pkg
        _events_pkg.EVENT_SOURCES["yfinance"] = _events_pkg.fetch_synthetic_events
    except Exception:  # noqa: BLE001
        pass

    return fake_fetch


# --------------------------------------------------------- Tk event pumps --

def _pump(app, seconds: float = 0.3) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            app.update()
        except Exception:
            break
        time.sleep(0.02)


def _pump_until(app, predicate, timeout: float = 2.0,
                poll: float = 0.02) -> bool:
    """Pump Tk events until ``predicate()`` returns truthy or timeout fires."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            app.update()
        except Exception:
            return False
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(poll)
    try:
        return bool(predicate())
    except Exception:
        return False


# ---------------------------------------------------- pixel-sanity helpers --

def _count_candle_pixels(app) -> int:
    """Count green-leaning + red-leaning pixels in the canvas raster."""
    import numpy as np
    arr = np.asarray(app._canvas.buffer_rgba())
    flat = arr.reshape(-1, 4).astype(int)
    g = ((flat[:, 1] - flat[:, 0]) > 20) & (flat[:, 1] > 80)
    r = ((flat[:, 0] - flat[:, 1]) > 20) & (flat[:, 0] > 80)
    return int(g.sum() + r.sum())


def _assert_canvas_has_candles(app, msg: str, min_pixels: int = 5000) -> None:
    """Assert the visible canvas still shows candles."""
    n = _count_candle_pixels(app)
    if n < min_pixels:
        raise AssertionError(
            f"{msg}: canvas appears blank "
            f"(candle pixels = {n}, expected ≥ {min_pixels})"
        )


# ---------------------------------------------- mpl event synthesizers ----

def _make_event(app, name: str, ax, xdata: float, ydata: float,
                button=1, dblclick: bool = False):
    """Build a matplotlib MouseEvent at the given data coordinates."""
    from matplotlib.backend_bases import MouseEvent
    disp = ax.transData.transform((xdata, ydata))
    e = MouseEvent(name, app._canvas, disp[0], disp[1],
                   button=button, dblclick=dblclick)
    e.inaxes = ax
    e.xdata = xdata
    e.ydata = ydata
    e.key = None
    return e


def _press(app, ax, x, y, dblclick=False):
    app._on_button_press(_make_event(app, "button_press_event", ax, x, y,
                                     dblclick=dblclick))


def _release(app, ax, x, y):
    app._on_button_release(_make_event(app, "button_release_event", ax, x, y))


def _hover(app, ax, x, y):
    app._dispatch_hover(_make_event(app, "motion_notify_event", ax, x, y,
                                    button=None))


def _scroll(app, ax, x, y, step):
    """Synthesize a scroll_event at (x,y) data coords with given step."""
    from matplotlib.backend_bases import MouseEvent
    disp = ax.transData.transform((x, y))
    e = MouseEvent("scroll_event", app._canvas, disp[0], disp[1])
    e.inaxes = ax
    e.xdata = x
    e.ydata = y
    e.button = "up" if step > 0 else "down"
    e.step = step
    e.key = None
    app._on_scroll_zoom(e)
