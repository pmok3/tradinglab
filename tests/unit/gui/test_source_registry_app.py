"""Unit tests for ``SourceRegistryAppMixin``.

The bug this pins: saving Alpaca credentials mid-session registered the new
vendor sources and refreshed the toolbar dropdown, but the chart — sitting on
**"Auto"** — kept drawing yfinance data until the app was restarted. "Auto" is a
delegating pseudo-source whose cache namespace is the opaque literal ``"Auto"``,
so the pre-save bars stayed in ``_full_cache`` and satisfied
``_load_data_async``'s cache-hit fast path.

Exercised without a Tk root by driving a minimal stand-in that mixes the mixin
in, matching ``tests/unit/gui/test_source_change_reresolve.py``.

See `gui/source_registry_app.spec.md` and `data/auto_source.spec.md`.
"""
from __future__ import annotations

from collections import OrderedDict

import pytest

from tradinglab.core.view_intent import ViewMode
from tradinglab.data import auto_source
from tradinglab.gui.source_registry_app import SourceRegistryAppMixin


class _Var:
    def __init__(self, value=""):
        self._v = value

    def get(self):
        return self._v

    def set(self, value):
        self._v = value


class _Toolbar:
    def __init__(self):
        self.sources = None

    def set_sources(self, sources):
        self.sources = tuple(sources)


class _View:
    def __init__(self):
        self.requests = []

    def request(self, mode, *, load_pending=False):
        self.requests.append((mode, load_pending))


class _Status:
    def __init__(self):
        self.messages = []

    def info(self, msg):
        self.messages.append(msg)


class _IndicatorCache:
    def __init__(self):
        self.cleared = 0

    def clear(self):
        self.cleared += 1


class _App(SourceRegistryAppMixin):
    """Minimal ChartApp stand-in carrying only what the mixin reads."""

    def __init__(self, *, source="Auto", sandbox=False, cache=None):
        self.source_var = _Var(source)
        self._toolbar = _Toolbar()
        self._view = _View()
        self._status = _Status()
        self._indicator_cache = _IndicatorCache()
        self._full_cache = OrderedDict(cache or {})
        self._sandbox = sandbox
        self.loads = 0

    def _is_sandbox_active(self):
        return self._sandbox

    def _load_data_async(self):
        self.loads += 1


@pytest.fixture(autouse=True)
def _restore_provenance():
    before = auto_source.last_resolved_source()
    yield
    auto_source.note_resolved_source(before)


@pytest.fixture
def _flip(monkeypatch):
    """Make Auto's resolution controllable: was ``was``, now ``now``."""
    def _apply(was, now):
        auto_source.note_resolved_source(was)
        monkeypatch.setattr(
            "tradinglab.gui.source_registry_app.resolve_auto_source",
            lambda: now)
    return _apply


# --------------------------------------------------------- the reload contract
def test_auto_flip_evicts_cache_and_reloads(_flip):
    _flip("yfinance", "yfinance+alpaca")
    app = _App(cache={
        ("Auto", "AAPL", "1d"): ["stale"],
        ("Auto", "SPY", "5m"): ["stale"],
    })

    assert app._reload_if_auto_source_changed() is True
    assert app._full_cache == {}
    assert app.loads == 1
    assert app._indicator_cache.cleared == 1


def test_reload_preserves_the_calendar_window_not_the_bar_index(_flip):
    """Two providers return different-length series, so preserving the bar
    INDEX window jumps the view to a different day (same reasoning as the
    source-only branch of ``_on_explicit_axis_change``)."""
    _flip("yfinance", "alpaca")
    app = _App()

    app._reload_if_auto_source_changed()
    assert app._view.requests == [(ViewMode.KEEP_DATES, True)]


def test_status_names_both_providers(_flip):
    _flip("yfinance", "yfinance+alpaca")
    app = _App()

    app._reload_if_auto_source_changed()
    assert len(app._status.messages) == 1
    msg = app._status.messages[0]
    assert "yfinance+alpaca" in msg and "yfinance" in msg


def test_provenance_is_updated_so_the_check_is_idempotent(_flip):
    _flip("yfinance", "yfinance+alpaca")
    app = _App()

    assert app._reload_if_auto_source_changed() is True
    assert auto_source.last_resolved_source() == "yfinance+alpaca"
    assert app._reload_if_auto_source_changed() is False
    assert app.loads == 1


# ------------------------------------------------------------- no-op behaviour
def test_unchanged_resolution_is_a_noop(_flip):
    _flip("yfinance", "yfinance")
    app = _App(cache={("Auto", "AAPL", "1d"): ["fresh"]})

    assert app._reload_if_auto_source_changed() is False
    assert app.loads == 0
    assert ("Auto", "AAPL", "1d") in app._full_cache


def test_explicit_source_choice_is_never_overridden(_flip):
    """A user pinned to plain yfinance asked for yfinance. Saving Alpaca keys
    must not silently move them onto another provider."""
    _flip("yfinance", "yfinance+alpaca")
    app = _App(source="yfinance", cache={("yfinance", "AAPL", "1d"): ["keep"]})

    assert app._reload_if_auto_source_changed() is False
    assert app.loads == 0
    assert app._full_cache[("yfinance", "AAPL", "1d")] == ["keep"]


def test_sandbox_session_is_left_alone(_flip):
    """Replay owns the primary slot — never yank its data mid-session."""
    _flip("yfinance", "yfinance+alpaca")
    app = _App(sandbox=True, cache={("Auto", "AAPL", "1d"): ["replay"]})

    assert app._reload_if_auto_source_changed() is False
    assert app.loads == 0
    assert app._full_cache[("Auto", "AAPL", "1d")] == ["replay"]


def test_no_baseline_means_no_reload(_flip):
    """Nothing has resolved yet, so there is no provenance to contradict."""
    _flip(None, "yfinance+alpaca")
    app = _App()

    assert app._reload_if_auto_source_changed() is False
    assert app.loads == 0
    assert auto_source.last_resolved_source() == "yfinance+alpaca"


# ------------------------------------------------------------------- eviction
def test_eviction_only_touches_the_auto_namespace():
    app = _App(cache={
        ("Auto", "AAPL", "1d"): ["stale"],
        ("yfinance", "AAPL", "1d"): ["keep"],
        ("alpaca", "AAPL", "5m"): ["keep"],
    })

    app._drop_auto_source_cache()
    assert set(app._full_cache) == {
        ("yfinance", "AAPL", "1d"), ("alpaca", "AAPL", "5m")}


# ------------------------------------------------------- combobox resync entry
def test_refresh_pushes_visible_sources_and_reconciles_auto(monkeypatch, _flip):
    monkeypatch.setattr(
        "tradinglab.gui.source_registry_app.user_visible_sources",
        lambda: ["yfinance", "Auto", "alpaca", "yfinance+alpaca"])
    _flip("yfinance", "yfinance+alpaca")
    app = _App()

    app._refresh_data_source_combobox()
    assert app._toolbar.sources == (
        "yfinance", "Auto", "alpaca", "yfinance+alpaca")
    assert app.loads == 1


def test_refresh_still_resyncs_when_the_auto_reconcile_fails(monkeypatch, _flip):
    """A UI resync must never raise into the dialog's ``on_changed``."""
    monkeypatch.setattr(
        "tradinglab.gui.source_registry_app.user_visible_sources",
        lambda: ["yfinance", "Auto"])
    _flip("yfinance", "yfinance+alpaca")
    app = _App()

    def _boom():
        raise RuntimeError("load exploded")

    app._load_data_async = _boom
    app._refresh_data_source_combobox()
    assert app._toolbar.sources == ("yfinance", "Auto")
