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

import os as _os


def _timeout_scale() -> float:
    """Return the multiplier applied to ``_pump`` / ``_pump_until`` timeouts.

    Read from ``TRADINGLAB_PYTEST_TIMEOUT_SCALE`` (default ``1.0``).
    CI sets this to ``5.0`` because the ``windows-latest`` runner is
    significantly slower than dev hardware on Tk-event-loop /
    async-fetch races (the dev box runs the same checks in ~half the
    wall-clock). The scale is applied uniformly inside the helpers so
    individual checks don't need to special-case CI.
    """
    try:
        return max(1.0, float(_os.environ.get("TRADINGLAB_PYTEST_TIMEOUT_SCALE", "1.0")))
    except (TypeError, ValueError):
        return 1.0


def _pump(app, seconds: float = 0.3) -> None:
    """Drive Tk events for ``seconds`` (NOT scaled by TIMEOUT_SCALE).

    Many checks use ``_pump(app, X)`` as a "wait briefly, then assert
    NOT done yet" probe. Scaling those would change semantics — the
    test would wait LONGER, by which time the work might actually be
    done. So ``_pump`` is fixed-wall-time on every host; only
    :func:`_pump_until` (which is "wait UP TO X for predicate") is
    scaled — extending an upper-bound wait can never falsify a passing
    test on dev hardware.
    """
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            app.update()
        except Exception:
            break
        time.sleep(0.02)


def _pump_until(app, predicate, timeout: float = 2.0,
                poll: float = 0.02) -> bool:
    """Pump Tk events until ``predicate()`` returns truthy or timeout fires.

    ``timeout`` is multiplied by :func:`_timeout_scale` so slow CI
    runners (and emulated environments) can satisfy timing-sensitive
    predicates without per-check tuning.
    """
    end = time.monotonic() + timeout * _timeout_scale()
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


def _assert_canvas_has_candles(app, msg: str, min_pixels: int = 400) -> None:
    """Assert the visible canvas still shows candles (i.e. is not blank).

    ``min_pixels`` is a deliberately loose "not blank" floor, not a pixel-exact
    snapshot. The real render canvas yields ~70k candle pixels; the headless
    ``xvfb`` CI canvas is far smaller. The topology-preserving fast path and the
    legacy ``figure.clear()`` rebuild draw bit-identical candles on a real
    canvas (verified: both paths report the same count locally), but on the tiny
    xvfb canvas the fast path's reused-axes repaint lands a few percent lower
    than the fresh-axes rebuild — a sub-pixel anti-aliasing / layout difference,
    NOT missing candles.

    The headless canvas SIZE is not fixed: it tracks how large the Tk window
    realizes under the runner's (window-manager-less) X server, which varies
    across GitHub-runner images. A healthy headless render has been seen at
    ~5k AND, on a later runner image, at ~900 candle pixels for the same chart
    — both clearly non-blank, vs a genuinely blank canvas at ~0. So the floor
    is kept LOW (a blank-detector, not a size assertion): it must sit far below
    the smallest healthy headless render yet far above ~0. ``check_d31`` /
    ``check_d32`` previously hard-coded ~3000 and went red when a runner image
    shrank the headless window — an environment change, not a regression.
    """
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


# ---------------------------------------- Tk widget interaction helpers ----
#
# These drive the ACTUAL widgets (invoke a Button's command, pick a menu
# entry, walk the widget tree) rather than calling the underlying handler
# methods directly — so a mis-wired ``command=`` / ``<<ComboboxSelected>>``
# binding is caught, not silently passed over. Used by
# ``test_smoke_gui_actions.py`` and available to any smoke check that
# wants to click a real button.

def _all_descendants(widget):
    """Depth-first iterator over every descendant widget of ``widget``."""
    for child in widget.winfo_children():
        yield child
        yield from _all_descendants(child)


def _find_widgets(root, *, cls=None, text=None):
    """Return descendants matching an optional widget ``cls`` and/or ``text``.

    ``text`` is a substring match against the widget's ``text`` option
    (Buttons / Labels / Checkbuttons). Widgets lacking a ``text`` option
    are skipped when ``text`` is given.
    """
    out = []
    for w in _all_descendants(root):
        if cls is not None and not isinstance(w, cls):
            continue
        if text is not None:
            try:
                if text not in str(w.cget("text")):
                    continue
            except Exception:  # noqa: BLE001
                continue
        out.append(w)
    return out


def _find_button(root, text):
    """First Button / Checkbutton / Menubutton whose text contains ``text``.

    Returns the widget or ``None``. Matches both ``ttk`` and classic ``tk``
    variants so it works across the mixed widget set in the app.
    """
    import tkinter as tk
    from tkinter import ttk
    btn_types = (ttk.Button, tk.Button, ttk.Checkbutton, tk.Checkbutton,
                 ttk.Menubutton, tk.Menubutton, ttk.Radiobutton, tk.Radiobutton)
    for w in _all_descendants(root):
        if isinstance(w, btn_types):
            try:
                if text in str(w.cget("text")):
                    return w
            except Exception:  # noqa: BLE001
                continue
    return None


def _click(widget):
    """Invoke a button/checkbutton/menubutton exactly as a mouse click would.

    Runs the widget's ``command`` callback (and toggles a Checkbutton's
    variable) via the Tk ``invoke`` command, returning its result.
    """
    return widget.invoke()


def _menu_invoke(menu, label, *, exact: bool = False) -> bool:
    """Invoke the first non-separator entry of ``menu`` matching ``label``.

    ``exact=False`` (default) does a substring match; ``exact=True`` requires
    the whole label to equal ``label`` (needed to pick ``"*"`` without also
    matching ``"**"``). Returns ``True`` if an entry was found and invoked.
    """
    try:
        end = menu.index("end")
    except Exception:  # noqa: BLE001
        return False
    if end is None:
        return False
    for i in range(end + 1):
        try:
            if menu.type(i) in ("separator", "tearoff"):
                continue
            lbl = str(menu.entrycget(i, "label"))
        except Exception:  # noqa: BLE001
            continue
        if (lbl == label) if exact else (label in lbl):
            menu.invoke(i)
            return True
    return False


def _submenu_of(menu, label):
    """Return the submenu (``tk.Menu``) of the cascade entry whose label
    contains ``label``, or ``None``."""
    try:
        end = menu.index("end")
    except Exception:  # noqa: BLE001
        return None
    if end is None:
        return None
    for i in range(end + 1):
        try:
            if menu.type(i) != "cascade":
                continue
            lbl = str(menu.entrycget(i, "label"))
        except Exception:  # noqa: BLE001
            continue
        if label in lbl:
            try:
                return menu.nametowidget(menu.entrycget(i, "menu"))
            except Exception:  # noqa: BLE001
                return None
    return None
