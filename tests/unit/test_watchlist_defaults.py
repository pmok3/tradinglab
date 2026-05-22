"""Unit tests for the canonical default-watchlist constants.

Audit ``default-watchlist-fresh``: pre-2026-05 the
``_DEFAULT_WATCHLIST_TICKERS`` constant was defined in both
``tradinglab.app`` (dead copy) and ``tradinglab.gui.watchlist_tab``
(live copy). The constants are now sourced from
``tradinglab.watchlists.DEFAULT_WATCHLIST_*`` (single source of
truth) and both modules re-export them.

These tests pin:
1. The canonical constants exist with the documented values.
2. Both consumer modules expose the same values (no drift).
3. The fresh-launch seeding path (``_ensure_default_watchlist`` —
   exercised via :class:`WatchlistManager`) populates the default
   tickers when the manager starts empty.
"""
from __future__ import annotations

import pytest

from tradinglab import watchlists as _wl
from tradinglab.watchlists import (
    DEFAULT_WATCHLIST_NAME,
    DEFAULT_WATCHLIST_TICKERS,
    WatchlistManager,
)


class TestCanonicalConstants:
    def test_default_name_value(self):
        assert DEFAULT_WATCHLIST_NAME == "Default"

    def test_default_tickers_value(self):
        # Exact value pinned: changing this is a deliberate UX
        # decision and should require updating the test (and
        # likely a user-facing release note).
        assert DEFAULT_WATCHLIST_TICKERS == (
            "AMD", "NVDA", "INTC", "AAPL", "MSFT",
        )

    def test_default_tickers_is_immutable(self):
        # Tuple → defends against accidental ``.append`` mutation
        # leaking back into other consumers (lists were the old
        # representation pre-fix).
        assert isinstance(DEFAULT_WATCHLIST_TICKERS, tuple)

    def test_default_tickers_are_uppercase_no_blanks(self):
        for t in DEFAULT_WATCHLIST_TICKERS:
            assert t == t.upper()
            assert t.strip() == t
            assert t  # non-empty

    def test_default_tickers_no_duplicates(self):
        assert len(DEFAULT_WATCHLIST_TICKERS) == len(set(DEFAULT_WATCHLIST_TICKERS))

    def test_exported_from_package(self):
        # Both constants reachable via the package re-export.
        assert _wl.DEFAULT_WATCHLIST_NAME == DEFAULT_WATCHLIST_NAME
        assert _wl.DEFAULT_WATCHLIST_TICKERS == DEFAULT_WATCHLIST_TICKERS


class TestConsumersUseCanonical:
    """The two historical duplicate-constant sites both source
    from :mod:`tradinglab.watchlists` now."""

    def test_watchlist_tab_consumes_canonical(self):
        from tradinglab.gui import watchlist_tab as wt
        # The module-local copy must mirror the canonical values
        # exactly (it's now a thin re-export).
        assert wt._DEFAULT_WATCHLIST_NAME == DEFAULT_WATCHLIST_NAME
        assert tuple(wt._DEFAULT_WATCHLIST_TICKERS) == DEFAULT_WATCHLIST_TICKERS

    def test_app_consumes_canonical(self):
        from tradinglab import app
        assert app._DEFAULT_WATCHLIST_NAME == DEFAULT_WATCHLIST_NAME
        assert tuple(app._DEFAULT_WATCHLIST_TICKERS) == DEFAULT_WATCHLIST_TICKERS


class TestFreshLaunchSeedsDefaults:
    """Verifies the first-run path that ``_ensure_default_watchlist``
    relies on: a brand-new :class:`WatchlistManager` is empty until
    the caller seeds it with the canonical defaults."""

    def test_fresh_manager_is_empty(self):
        mgr = WatchlistManager()
        assert mgr.list_names() == []
        assert mgr.pinned_names() == []

    def test_seed_with_default_tickers_populates_starter_list(self):
        mgr = WatchlistManager()
        # Mirrors the ``_ensure_default_watchlist`` body in
        # ``gui/watchlist_tab.py``: only seed when the manager
        # has no lists.
        if not mgr.list_names():
            mgr.create(DEFAULT_WATCHLIST_NAME, list(DEFAULT_WATCHLIST_TICKERS))
        assert mgr.list_names() == [DEFAULT_WATCHLIST_NAME]
        wl = mgr.get(DEFAULT_WATCHLIST_NAME)
        assert wl is not None
        assert tuple(wl.tickers) == DEFAULT_WATCHLIST_TICKERS

    def test_seed_then_pin_makes_default_first_pin(self):
        mgr = WatchlistManager()
        mgr.create(DEFAULT_WATCHLIST_NAME, list(DEFAULT_WATCHLIST_TICKERS))
        mgr.pin(DEFAULT_WATCHLIST_NAME)
        assert mgr.pinned_names() == [DEFAULT_WATCHLIST_NAME]

    def test_seed_idempotent_on_repeat(self):
        # The seeding helper checks ``not mgr.list_names()`` before
        # creating — calling it twice mustn't add a duplicate.
        mgr = WatchlistManager()
        for _ in range(2):
            if not mgr.list_names():
                mgr.create(DEFAULT_WATCHLIST_NAME, list(DEFAULT_WATCHLIST_TICKERS))
        assert mgr.list_names() == [DEFAULT_WATCHLIST_NAME]


class TestNoBackImportCycle:
    """The canonical defaults module must be lightweight enough
    to import without dragging in Tk. Audit
    ``default-watchlist-fresh`` lessons-learned: the original
    duplication existed BECAUSE ``gui.watchlist_tab`` couldn't
    back-import ``tradinglab.app``. The canonical home
    (``tradinglab.watchlists``) has no Tk dependency, so both
    consumers can share it."""

    def test_watchlists_package_imports_cleanly(self):
        import importlib

        m = importlib.import_module("tradinglab.watchlists")
        assert m.DEFAULT_WATCHLIST_NAME == "Default"
        assert m.DEFAULT_WATCHLIST_TICKERS[0] == "AMD"

    def test_watchlists_package_does_not_import_app(self):
        # The package must not back-import ``tradinglab.app``;
        # otherwise we'd reintroduce the very circular dependency
        # that forced the original duplication.
        import importlib

        m = importlib.import_module("tradinglab.watchlists")
        # Walk the module's attributes; none should expose
        # ``tradinglab.app`` as their declared module.
        for name, val in vars(m).items():
            if name.startswith("_"):
                continue
            decl = getattr(val, "__module__", "")
            assert decl != "tradinglab.app", (
                f"export {name!r} comes from tradinglab.app — "
                "this would reintroduce the back-import cycle"
            )
