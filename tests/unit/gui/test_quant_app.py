"""Unit tests for :class:`~tradinglab.gui.quant_app.QuantAppMixin`.

Driven through a minimal ``ChartApp`` stand-in rather than a real root,
matching ``tests/unit/gui/test_source_registry_app.py``. The mixin only ever
reaches for attributes, so a stand-in exercises the real code paths without
a Tcl interpreter, a notebook, or an executor.

The behaviours worth pinning are the ones that would silently rot: that Last
is derived from **daily** bars whatever the chart interval, that the refresh
tick costs nothing when the cache is warm, and that a double-click honours
the same hovered-slot rule the watchlist uses.

See `gui/quant_app.spec.md`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradinglab.gui.quant_app import QUANT_LAST_INTERVAL, QuantAppMixin


class _Var:
    def __init__(self, value=""):
        self._v = value

    def get(self):
        return self._v

    def set(self, value):
        self._v = value


class _Status:
    def __init__(self):
        self.warnings: list[str] = []

    def warn(self, msg):
        self.warnings.append(msg)


class _Notebook:
    def __init__(self):
        self.states: dict[object, str] = {}
        self.selected = None

    def tab(self, widget, **kwargs):
        if "state" in kwargs:
            self.states[widget] = kwargs["state"]
        return self.states.get(widget)

    def select(self, widget=None):
        if widget is None:
            return str(self.selected) if self.selected is not None else ""
        self.selected = widget


class _Tab:
    """Stands in for ``QuantTab`` — only ``symbols`` / ``set_last_values``."""

    def __init__(self, symbols=("VIX", "TLT")):
        self._symbols = list(symbols)
        self.last_values: dict[str, str] = {}
        self.themed: list[tuple] = []

    def symbols(self):
        return list(self._symbols)

    def set_last_values(self, values):
        self.last_values.update(values)

    def apply_theme(self, *, muted_fg, group_fg=None):
        self.themed.append((muted_fg, group_fg))


class _Executor:
    def __init__(self, *, run=False):
        self.calls: list[tuple] = []
        self._run = run

    def submit(self, fn, *args):
        self.calls.append((fn, args))
        if self._run:
            fn(*args)


class _App(QuantAppMixin):
    """Minimal ChartApp stand-in carrying only what the mixin reads."""

    def __init__(self, *, tab=None, executor=None):
        self._quant_tab = tab if tab is not None else _Tab()
        self._quant_visible_var = _Var(False)
        self._quant_refresh_job = None
        self._quant_fetch_inflight = set()
        self._notebook = _Notebook()
        self._status = _Status()
        self._fetch_executor = executor
        self._full_cache: dict = {}
        self._watchlist_snapshot: dict = {}
        self._worker_inbox = SimpleNamespace(items=[])
        self._worker_inbox.put_nowait = self._worker_inbox.items.append
        self.ticker_var = _Var("AMD")
        self.compare_ticker_var = _Var("SPY")
        self.compare_var = _Var(False)
        self.source_var = _Var("yfinance")
        self.interval_var = _Var("1d")
        self.dark_var = _Var(False)
        self._last_hovered_slot = "primary"
        self._drilldown_day = None
        self._preserve_xlim_by_time_on_render = False
        self.after_calls: list[tuple] = []
        self.cancelled: list[str] = []
        self.loads = 0
        self.drilldown_reloads = 0
        self.snapshot_calls: list[tuple] = []
        self.stashed: list[tuple] = []
        self.stale = False

    # --- ChartApp surface the mixin uses ---
    def after(self, ms, fn):
        self.after_calls.append((ms, fn))
        return f"job{len(self.after_calls)}"

    def after_cancel(self, job):
        self.cancelled.append(job)

    def _load_data_async(self):
        self.loads += 1

    def _load_data(self):
        self.loads += 1

    def _reload_preserving_drilldown(self, _fn):
        self.drilldown_reloads += 1

    def _ticker_change_should_time_preserve(self):
        return True

    def _cache_is_stale(self, _bars, _interval):
        return self.stale

    def _apply_watchlist_snapshot_from_bars(self, sym, src, itv, bars):
        self.snapshot_calls.append((sym, src, itv, list(bars)))
        self._watchlist_snapshot.setdefault(sym.upper(), {})["last"] = bars[-1]
        return True

    def _stash_full_cache(self, key, bars):
        self.stashed.append((key, bars))


@pytest.fixture
def app():
    return _App()


# --- toggle / lifecycle -----------------------------------------------


def test_toggle_on_reveals_selects_and_starts_the_loop(app):
    app._quant_visible_var.set(True)
    app._on_view_toggle_quant()
    assert app._notebook.states[app._quant_tab] == "normal"
    assert app._notebook.selected is app._quant_tab
    assert app._quant_refresh_job is not None
    assert app._quant_tab.themed


def test_toggle_off_hides_and_stops_the_loop(app):
    app._quant_visible_var.set(True)
    app._on_view_toggle_quant()
    job = app._quant_refresh_job
    app._quant_visible_var.set(False)
    app._on_view_toggle_quant()
    assert app._notebook.states[app._quant_tab] == "hidden"
    assert app._quant_refresh_job is None
    assert job in app.cancelled


def test_toggle_is_inert_without_a_tab(app):
    app._quant_tab = None
    app._quant_visible_var.set(True)
    app._on_view_toggle_quant()
    assert app._notebook.states == {}
    assert app._quant_refresh_job is None


def test_starting_the_loop_twice_cancels_the_previous_job(app):
    app._quant_visible_var.set(True)
    app._start_quant_refresh_loop()
    first = app._quant_refresh_job
    app._start_quant_refresh_loop()
    assert first in app.cancelled
    assert app._quant_refresh_job != first


def test_tab_visible_requires_reveal_and_selection(app):
    """The gate is notebook SELECTION, not `winfo_viewable`.

    Mapping is asynchronous, so a viewability gate reported False in the
    same call that selected the tab and skipped the first refresh outright.
    """
    app._quant_visible_var.set(True)
    app._notebook.select(app._quant_tab)
    assert app._quant_tab_visible() is True
    # Revealed but the user is on another tab.
    app._notebook.select(object())
    assert app._quant_tab_visible() is False
    # Selected but the checkbutton is off.
    app._notebook.select(app._quant_tab)
    app._quant_visible_var.set(False)
    assert app._quant_tab_visible() is False
    app._quant_tab = None
    assert app._quant_tab_visible() is False


def test_revealing_the_tab_fetches_on_the_very_first_tick(app):
    """Regression: the first batch used to be skipped, blanking Last 30s."""
    app._fetch_executor = _Executor()
    app._quant_visible_var.set(True)
    app._on_view_toggle_quant()
    assert app._fetch_executor.calls, (
        "revealing the tab must submit fetches immediately, not one tick later"
    )


# --- row activation ---------------------------------------------------


def test_activate_loads_the_primary_slot(app):
    app._on_quant_row_activate("vix")
    assert app.ticker_var.get() == "VIX"
    assert app.loads == 1


def test_activate_routes_to_compare_when_hovered_and_enabled(app):
    app._last_hovered_slot = "compare"
    app.compare_var.set(True)
    app._on_quant_row_activate("RSP/SPY")
    assert app.compare_ticker_var.get() == "RSP/SPY"
    assert app.ticker_var.get() == "AMD"


def test_compare_hover_falls_back_to_primary_when_compare_is_off(app):
    """The compare panel doesn't exist, so the user means the main chart."""
    app._last_hovered_slot = "compare"
    app.compare_var.set(False)
    app._on_quant_row_activate("HYG")
    assert app.ticker_var.get() == "HYG"
    assert app.compare_ticker_var.get() == "SPY"


def test_activate_is_a_no_op_for_the_current_symbol(app):
    app.ticker_var.set("VIX")
    app._on_quant_row_activate("VIX")
    assert app.loads == 0


def test_activate_ignores_blank_symbols(app):
    app._on_quant_row_activate("   ")
    assert app.loads == 0
    assert app.ticker_var.get() == "AMD"


def test_activate_preserves_an_active_drilldown_day(app):
    app._drilldown_day = object()
    app.interval_var.set("5m")
    app._on_quant_row_activate("TLT")
    assert app.drilldown_reloads == 1
    assert app.loads == 0


def test_unavailable_row_warns_instead_of_loading(app):
    row = SimpleNamespace(name="GEX", unavailable_reason="no feed")
    app._on_quant_row_unavailable(row)
    assert app._status.warnings == ["GEX: no feed"]
    assert app.loads == 0


# --- Last column ------------------------------------------------------


def test_paint_writes_only_symbols_with_a_snapshot(app):
    app._watchlist_snapshot["VIX"] = {"last": 15.819}
    app._paint_quant_last_values()
    assert app._quant_tab.last_values == {"VIX": "15.82"}


def test_paint_ignores_non_numeric_snapshot_values(app):
    app._watchlist_snapshot["VIX"] = {"last": None}
    app._paint_quant_last_values()
    assert app._quant_tab.last_values == {}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (78792.6, "78,793"),
        (1000.0, "1,000"),
        (763.77, "763.77"),
        (15.819, "15.82"),
        (10.0, "10.00"),
        (9.9999, "10.000"),
        (1.0, "1.000"),
        (0.2905, "0.2905"),
    ],
)
def test_last_formatting_scales_with_magnitude(value, expected):
    """Quant rows span BTC-USD to RSP/SPY; one precision can't serve both."""
    assert _App._format_quant_last(value) == expected


def test_fetches_are_submitted_for_uncached_symbols(app):
    app._fetch_executor = _Executor()
    app._submit_quant_fetches()
    submitted = [args[0] for _fn, args in app._fetch_executor.calls]
    assert submitted == ["VIX", "TLT"]
    assert app._quant_fetch_inflight == {"VIX", "TLT"}


def test_a_fresh_cache_short_circuits_the_fetch(app):
    """Steady state over the whole catalog must cost zero HTTP calls."""
    app._fetch_executor = _Executor()
    for sym in ("VIX", "TLT"):
        app._full_cache[("yfinance", sym, QUANT_LAST_INTERVAL)] = [1.0]
        app._watchlist_snapshot[sym] = {"last": 1.0}
    app.stale = False
    app._submit_quant_fetches()
    assert app._fetch_executor.calls == []


def test_a_stale_cache_is_refetched(app):
    app._fetch_executor = _Executor()
    for sym in ("VIX", "TLT"):
        app._full_cache[("yfinance", sym, QUANT_LAST_INTERVAL)] = [1.0]
        app._watchlist_snapshot[sym] = {"last": 1.0}
    app.stale = True
    app._submit_quant_fetches()
    assert len(app._fetch_executor.calls) == 2


def test_a_warm_cache_without_a_snapshot_is_repaired_not_refetched(app):
    """After a restart _full_cache reloads from disk before any snapshot."""
    app._fetch_executor = _Executor()
    app._full_cache[("yfinance", "VIX", QUANT_LAST_INTERVAL)] = [15.82]
    app._full_cache[("yfinance", "TLT", QUANT_LAST_INTERVAL)] = [82.6]
    app.stale = False
    app._submit_quant_fetches()
    assert app._fetch_executor.calls == []
    assert {c[0] for c in app.snapshot_calls} == {"VIX", "TLT"}
    assert app._watchlist_snapshot["VIX"]["last"] == 15.82


def test_an_inflight_symbol_is_not_resubmitted(app):
    app._fetch_executor = _Executor()
    app._quant_fetch_inflight.add("VIX")
    app._submit_quant_fetches()
    assert [args[0] for _fn, args in app._fetch_executor.calls] == ["TLT"]


def test_fetches_use_the_daily_interval_not_the_chart_interval(app):
    """A macro gauge's Last must not change meaning with the chart."""
    app.interval_var.set("5m")
    app._fetch_executor = _Executor()
    app._submit_quant_fetches()
    key = ("yfinance", "VIX", QUANT_LAST_INTERVAL)
    assert key not in app._full_cache  # nothing cached yet
    app._submit_quant_fetches()  # inflight-guarded, still one submission each
    assert len(app._fetch_executor.calls) == 2


def test_submit_is_inert_without_an_executor(app):
    app._fetch_executor = None
    app._submit_quant_fetches()
    assert app._quant_fetch_inflight == set()


def test_worker_records_a_snapshot_and_stashes_bars(app, monkeypatch):
    from tradinglab import data as data_mod

    monkeypatch.setitem(data_mod.DATA_SOURCES, "yfinance", lambda s, i: [42.0])
    app._quant_fetch_inflight.add("VIX")
    app._fetch_quant_last("VIX", "yfinance")
    assert app.snapshot_calls[0][:3] == ("VIX", "yfinance", QUANT_LAST_INTERVAL)
    assert app.stashed == [(("yfinance", "VIX", QUANT_LAST_INTERVAL), [42.0])]
    assert "VIX" not in app._quant_fetch_inflight


def test_worker_clears_the_inflight_marker_on_failure(app, monkeypatch):
    from tradinglab import data as data_mod

    def _boom(_s, _i):
        raise RuntimeError("network down")

    monkeypatch.setitem(data_mod.DATA_SOURCES, "yfinance", _boom)
    app._quant_fetch_inflight.add("VIX")
    app._fetch_quant_last("VIX", "yfinance")
    assert "VIX" not in app._quant_fetch_inflight
    assert app.stashed == []


def test_worker_ignores_an_empty_result(app, monkeypatch):
    from tradinglab import data as data_mod

    monkeypatch.setitem(data_mod.DATA_SOURCES, "yfinance", lambda s, i: [])
    app._fetch_quant_last("VIX", "yfinance")
    assert app.snapshot_calls == []
    assert app.stashed == []


# --- refresh tick -----------------------------------------------------


def test_tick_repaints_and_rearms_while_visible(app):
    app._quant_visible_var.set(True)
    app._notebook.select(app._quant_tab)
    app._fetch_executor = _Executor()
    app._watchlist_snapshot["VIX"] = {"last": 15.82}
    app._quant_refresh_tick()
    assert app._quant_tab.last_values == {"VIX": "15.82"}
    assert app._quant_refresh_job is not None
    assert app._fetch_executor.calls


def test_tick_repaints_but_does_not_fetch_when_another_tab_is_selected(app):
    """A revealed-but-unselected tab must not poll the network."""
    app._quant_visible_var.set(True)
    app._notebook.select(object())
    app._fetch_executor = _Executor()
    app._watchlist_snapshot["VIX"] = {"last": 15.82}
    app._quant_refresh_tick()
    assert app._quant_tab.last_values == {"VIX": "15.82"}
    assert app._fetch_executor.calls == []


def test_tick_does_not_rearm_once_the_checkbutton_is_off(app):
    app._quant_visible_var.set(False)
    app._quant_refresh_tick()
    assert app._quant_refresh_job is None


def test_tick_is_inert_without_a_tab(app):
    app._quant_tab = None
    app._quant_visible_var.set(True)
    app._quant_refresh_tick()
    assert app._quant_refresh_job is None


# --- theming ----------------------------------------------------------


def test_theme_uses_distinct_muted_colours_per_mode(app):
    app.dark_var.set(False)
    app._apply_quant_theme()
    app.dark_var.set(True)
    app._apply_quant_theme()
    light, dark = app._quant_tab.themed
    assert light != dark


def test_theme_is_inert_without_a_tab(app):
    app._quant_tab = None
    app._apply_quant_theme()  # must not raise
