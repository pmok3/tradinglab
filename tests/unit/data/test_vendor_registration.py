"""Unit tests for credential-gated vendor source registration.

Audit: ``credential-verification``. Pins
:func:`tradinglab.data.register_vendor_sources` — the fix for the
"saved a working Alpaca key but the source only appears after a restart"
gap. Before the extraction this block ran at package-import time only,
so the credentials dialog had no way to make a newly-entered key take
effect, and any "your credentials are valid" signal was misleading.

Mirrors ``test_init_local_data_registration.py`` for the BYOD path.
"""
from __future__ import annotations

import pytest

import tradinglab.data as tld
from tradinglab.data import base
from tradinglab.data.credentials import (
    AlpacaCredentials,
    Credentials,
    PolygonCredentials,
    SchwabCredentials,
)
from tradinglab.data.hybrid_source import HYBRID_SOURCE_NAME


def _creds(*, alpaca: bool = False, polygon: bool = False) -> Credentials:
    return Credentials(
        schwab=SchwabCredentials(),
        alpaca=AlpacaCredentials(
            api_key_id="k" if alpaca else None,
            api_secret_key="s" if alpaca else None,
        ),
        polygon=PolygonCredentials(api_key="p" if polygon else None),
    )


@pytest.fixture(autouse=True)
def _restore_registry():
    """Snapshot + restore the global registry and all capability tables.

    The dev machine may have real credentials configured, so these tests
    mutate live global state — without this fixture a failure would leak
    a wrong registry into every later test in the process.
    """
    sources = dict(base.DATA_SOURCES)
    internal = set(base._INTERNAL_SOURCES)
    ranged = set(base._RANGE_CAPABLE)
    pages = dict(base._PAGE_FETCHERS)
    yield
    base.DATA_SOURCES.clear()
    base.DATA_SOURCES.update(sources)
    base._INTERNAL_SOURCES.clear()
    base._INTERNAL_SOURCES.update(internal)
    base._RANGE_CAPABLE.clear()
    base._RANGE_CAPABLE.update(ranged)
    base._PAGE_FETCHERS.clear()
    base._PAGE_FETCHERS.update(pages)


class TestUnregisterSource:
    def test_removes_from_every_capability_table(self):
        base.register_source(
            "tmpvendor", lambda t, i: [], supports_range=True,
            page_fetcher=lambda t, i, **kw: [])
        assert base.source_supports_range("tmpvendor")
        assert base.source_supports_page("tmpvendor")

        assert base.unregister_source("tmpvendor") is True

        assert "tmpvendor" not in base.DATA_SOURCES
        # The whole point of the helper: a bare dict pop would leave these
        # reporting True for a source that can no longer be dispatched.
        assert not base.source_supports_range("tmpvendor")
        assert not base.source_supports_page("tmpvendor")

    def test_clears_the_internal_flag(self):
        base.register_source("tmpvendor", lambda t, i: [], internal=True)
        base.unregister_source("tmpvendor")
        assert not base.is_internal_source("tmpvendor")

    def test_absent_source_returns_false(self):
        assert base.unregister_source("never-registered") is False


class TestRegisterVendorSources:
    def test_alpaca_configured_registers_alpaca_and_hybrid(self, monkeypatch):
        monkeypatch.setattr(tld, "get_credentials",
                            lambda: _creds(alpaca=True))
        out = tld.register_vendor_sources()
        assert "alpaca" in out
        assert HYBRID_SOURCE_NAME in out
        assert "alpaca" in base.DATA_SOURCES
        assert base.source_supports_range("alpaca")
        assert base.source_supports_page("alpaca")

    def test_hybrid_requires_alpaca(self, monkeypatch):
        monkeypatch.setattr(tld, "get_credentials",
                            lambda: _creds(polygon=True))
        out = tld.register_vendor_sources()
        assert HYBRID_SOURCE_NAME not in out
        assert HYBRID_SOURCE_NAME not in base.DATA_SOURCES

    def test_polygon_configured_registers_polygon(self, monkeypatch):
        monkeypatch.setattr(tld, "get_credentials",
                            lambda: _creds(polygon=True))
        assert "polygon" in tld.register_vendor_sources()
        assert "polygon" in base.DATA_SOURCES

    def test_clearing_credentials_unregisters(self, monkeypatch):
        monkeypatch.setattr(tld, "get_credentials",
                            lambda: _creds(alpaca=True, polygon=True))
        tld.register_vendor_sources()
        assert "alpaca" in base.DATA_SOURCES

        # User clears every field and saves.
        monkeypatch.setattr(tld, "get_credentials", _creds)
        assert tld.register_vendor_sources() == []

        # A stale entry would sit in the dropdown failing every fetch.
        for key in ("alpaca", "polygon", HYBRID_SOURCE_NAME):
            assert key not in base.DATA_SOURCES
        assert not base.source_supports_range("alpaca")
        assert not base.source_supports_page("alpaca")

    def test_is_idempotent(self, monkeypatch):
        monkeypatch.setattr(tld, "get_credentials",
                            lambda: _creds(alpaca=True))
        first = tld.register_vendor_sources()
        second = tld.register_vendor_sources()
        assert first == second
        assert len(base.user_visible_sources()) == len(set(
            base.user_visible_sources()))

    def test_never_touches_builtin_or_internal_sources(self, monkeypatch):
        monkeypatch.setattr(tld, "get_credentials", _creds)
        tld.register_vendor_sources()
        # yfinance / Auto must survive a credential wipe, and the internal
        # synthetic sources must keep their hidden flag.
        assert "yfinance" in base.DATA_SOURCES
        assert "synthetic" in base.DATA_SOURCES
        assert base.is_internal_source("synthetic")
        assert "synthetic" not in base.user_visible_sources()

    def test_registration_uses_presence_not_a_network_probe(self, monkeypatch):
        # Gating must never depend on connectivity — a user configuring
        # offline still gets their source registered. If this ever calls
        # verify_vendor, startup becomes network-bound.
        called = []
        monkeypatch.setattr(tld, "get_credentials",
                            lambda: _creds(alpaca=True))
        monkeypatch.setattr(
            tld.verify, "verify_vendor",
            lambda *a, **k: called.append(a) or None)
        tld.register_vendor_sources()
        assert called == []
        assert "alpaca" in base.DATA_SOURCES

    def test_newly_registered_source_is_user_visible(self, monkeypatch):
        monkeypatch.setattr(tld, "get_credentials",
                            lambda: _creds(alpaca=True))
        tld.register_vendor_sources()
        visible = tld.user_visible_sources()
        # This is the payload of the whole fix: the toolbar combobox reads
        # user_visible_sources(), so the entry is selectable immediately.
        assert "alpaca" in visible
        assert HYBRID_SOURCE_NAME in visible
