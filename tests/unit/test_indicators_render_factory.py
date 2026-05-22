"""Unit tests for ``indicators.render.factory_by_kind_id`` and
``applicable_overlay_configs`` — bug A1 (latent tuple-unpack swallowed
silently).

The base ``factory_by_kind_id`` is typed to return
``Optional[Tuple[str, IndicatorFactory]]``. The render-module wrapper
exposes just the factory. Historically the wrapper caught
``TypeError`` and ``IndexError`` silently — any future regression of
the base contract would degrade quietly to "no indicators render"
with zero diagnostic. The fix replaces the silent except with an
explicit shape check + WARNING log so the diagnostic is preserved
without hiding the failure.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest


from tradinglab.indicators import render as _render


# --- factory_by_kind_id wrapper -----------------------------------------

def test_factory_by_kind_id_returns_class_for_known_kind():
    cls = _render.factory_by_kind_id("sma")
    assert cls is not None
    # The base form returns a (display_name, class) tuple; the wrapper
    # peels off the class.
    assert hasattr(cls, "params_schema") or hasattr(cls, "name")


def test_factory_by_kind_id_returns_none_for_unknown_kind():
    assert _render.factory_by_kind_id("definitely-not-an-indicator") is None


def test_factory_by_kind_id_logs_warning_on_broken_contract(
    monkeypatch, caplog,
):
    # Patch the underlying base function to return a malformed value.
    def broken_factory(_kind_id):
        return "not-a-tuple"
    monkeypatch.setattr(_render, "_factory_by_kind_id_raw", broken_factory)
    caplog.set_level(logging.WARNING, logger="tradinglab.indicators.render")
    result = _render.factory_by_kind_id("anything")
    assert result is None
    # Must surface a diagnostic — silent degradation is the regression
    # this test guards against.
    assert any(
        "non-tuple" in rec.message.lower()
        or "factory_by_kind_id" in rec.message
        for rec in caplog.records
    )


def test_factory_by_kind_id_handles_one_element_tuple(monkeypatch, caplog):
    monkeypatch.setattr(
        _render, "_factory_by_kind_id_raw",
        lambda _k: ("display_name_only",),
    )
    caplog.set_level(logging.WARNING, logger="tradinglab.indicators.render")
    assert _render.factory_by_kind_id("anything") is None
    assert any(
        "factory_by_kind_id" in rec.message for rec in caplog.records
    )


def test_factory_by_kind_id_handles_empty_tuple(monkeypatch, caplog):
    monkeypatch.setattr(
        _render, "_factory_by_kind_id_raw", lambda _k: tuple(),
    )
    caplog.set_level(logging.WARNING, logger="tradinglab.indicators.render")
    assert _render.factory_by_kind_id("anything") is None


def test_factory_by_kind_id_passes_tuple_through_correctly(monkeypatch):
    sentinel_cls = object()
    monkeypatch.setattr(
        _render, "_factory_by_kind_id_raw",
        lambda _k: ("display_name", sentinel_cls),
    )
    assert _render.factory_by_kind_id("anything") is sentinel_cls


def test_factory_by_kind_id_passes_longer_tuple_through(monkeypatch):
    # The base form is typed as 2-tuple but the wrapper should be
    # forward-compatible with any tuple of length >= 2.
    sentinel_cls = object()
    monkeypatch.setattr(
        _render, "_factory_by_kind_id_raw",
        lambda _k: ("display_name", sentinel_cls, "extra"),
    )
    assert _render.factory_by_kind_id("anything") is sentinel_cls


# --- applicable_overlay_configs debug logging ---------------------------

class _StubConfig:
    """Minimal duck-typed IndicatorConfig for the test."""

    def __init__(self, kind_id, display_name, unknown=False):
        self.kind_id = kind_id
        self.display_name = display_name
        self.unknown = unknown


class _StubManager:
    def __init__(self, configs):
        self._configs = configs

    def applicable(self, _scope, _interval):
        return list(self._configs)


def test_applicable_overlay_configs_drops_unknown_kind(monkeypatch, caplog):
    # Real kind ("sma") + ghost kind that was removed from the registry.
    mgr = _StubManager([
        _StubConfig("sma", "SMA(20)"),
        _StubConfig("removed-plugin", "Removed Indicator"),
    ])
    monkeypatch.setattr(
        _render, "_factory_by_kind_id_raw",
        lambda k: ("SMA", _render._factory_by_kind_id_raw.__wrapped__("sma")[1])
        if k == "sma" and False  # pragma: no cover
        else None if k == "removed-plugin"
        else None,
    )
    caplog.set_level(logging.DEBUG, logger="tradinglab.indicators.render")
    # We don't care about the SMA result here — just that the loop
    # doesn't crash on the missing-factory entry and that the missing
    # one is logged at DEBUG.
    out = _render.applicable_overlay_configs(mgr, "main", "1d")
    # SMA was forcibly returned as None above; both should be dropped.
    assert out == []
    # The "removed-plugin" config should have produced a debug log.
    assert any(
        "removed-plugin" in rec.message and "no factory" in rec.message
        for rec in caplog.records
    )


def test_applicable_overlay_configs_skips_unknown_flag():
    """A config whose ``unknown`` flag is set is skipped without lookup."""
    mgr = _StubManager([_StubConfig("sma", "SMA", unknown=True)])
    out = _render.applicable_overlay_configs(mgr, "main", "1d")
    assert out == []
