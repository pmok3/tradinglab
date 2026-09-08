from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradinglab.data import auto_source
from tradinglab.data.credentials import SchwabCredentials
from tradinglab.streaming import registry


class Source:
    def __init__(self):
        self.closes = 0

    def close(self):
        self.closes += 1


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(registry, "STREAM_SOURCES", {})
    monkeypatch.setattr(registry, "QUOTE_SOURCES", {})
    monkeypatch.setattr(registry, "_registered_identity", None)
    monkeypatch.setattr(registry, "_oauth_disconnected", False)
    monkeypatch.setattr(registry, "register_stream", lambda name, source: registry.STREAM_SOURCES.update(
        {name: source}))
    monkeypatch.setattr(registry, "register_quote_source", lambda name, source: registry.QUOTE_SOURCES.update(
        {name: source}))
    monkeypatch.setattr(registry, "unregister_quote_source",
                        lambda name: registry.QUOTE_SOURCES.pop(name, None) is not None)
    monkeypatch.setattr(registry, "SchwabStreamSource", Source)
    credentials = [SchwabCredentials()]
    monkeypatch.setattr("tradinglab.data.credentials.get_credentials",
                        lambda: SimpleNamespace(schwab=credentials[0]))
    return credentials


def test_presence_refresh_preserves_singleton_and_quote_factory(isolated):
    assert not registry.reconcile_vendor_streams()
    isolated[0] = SchwabCredentials("key", "secret", "uri")
    assert registry.reconcile_vendor_streams()
    source = registry.STREAM_SOURCES["schwab-stream"]
    binding = registry.quote_registry_binding("schwab-quotes")
    assert not registry.reconcile_vendor_streams()
    assert registry.STREAM_SOURCES["schwab-stream"] is source
    assert registry.quote_registry_binding("schwab-quotes") == binding
    assert source.closes == 0
    isolated[0] = SchwabCredentials()
    assert registry.reconcile_vendor_streams()
    assert source.closes == 1
    assert registry.STREAM_SOURCES == registry.QUOTE_SOURCES == {}


def test_oauth_disconnect_stays_unregistered_until_reconnect(isolated):
    isolated[0] = SchwabCredentials("key", "secret")
    registry.reconcile_vendor_streams()
    old = registry.STREAM_SOURCES["schwab-stream"]
    registry.reconcile_vendor_streams(reset=True, oauth_connected=False)
    assert old.closes == 1
    assert not registry.reconcile_vendor_streams()
    assert not registry.STREAM_SOURCES
    registry.reconcile_vendor_streams(reset=True, oauth_connected=True)
    assert registry.STREAM_SOURCES["schwab-stream"] is not old


@pytest.mark.parametrize("interval", ["1m", "5m", "15m", "30m", "1h"])
def test_explicit_schwab_capability_mapping(interval):
    stream = Source()
    selection = registry.resolve_chart_stream("schwab", "AMD", interval, {"schwab-stream": stream})
    assert selection.source is stream
    assert selection.native_interval == "1m"


@pytest.mark.parametrize("ticker", ["AMD/NVDA", "AMD/10", "^VIX", "$SPX", "VIX", "I:VIX", "^MOVE"])
def test_unsupported_symbols_stay_nonstreaming(ticker):
    assert registry.resolve_chart_stream("schwab", ticker, "1m", {"schwab-stream": Source()}) is None


@pytest.mark.parametrize("ticker", ["COMP", "MOVE", "AMD"])
def test_real_equities_are_not_inferred_indices(ticker):
    assert registry.resolve_chart_stream("schwab", ticker, "1m", {"schwab-stream": Source()})


def test_auto_uses_existing_resolver_and_requires_matching_cache_provenance(monkeypatch):
    monkeypatch.setattr(auto_source, "resolve_auto_source", lambda: "schwab")
    monkeypatch.setattr(auto_source, "last_resolved_source", lambda: "yfinance")
    sources = {"schwab-stream": Source()}
    assert registry.resolve_chart_stream("Auto", "AMD", "1m", sources) is None
    monkeypatch.setattr(auto_source, "last_resolved_source", lambda: "schwab")
    assert registry.resolve_chart_stream("Auto", "AMD", "1m", sources).source is sources["schwab-stream"]


def test_unsupported_interval_and_legacy_direct_registration():
    stream = Source()
    assert registry.resolve_chart_stream("schwab", "AMD", "2h", {"schwab-stream": stream}) is None
    assert registry.resolve_chart_stream("fake", "AMD", "2h", {"fake": stream}).source is stream
