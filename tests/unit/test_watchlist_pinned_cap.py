"""Audit ``pinned-watchlist-cap`` — configurable Watchlist pin cap.

`gui/watchlist_tab.py:407-411` previously hardcoded
``WatchlistManager.MAX_PINNED == 5``. Power users with many
curated ticker sets asked to either raise the default or make it
configurable. The fix introduces a new
``watchlist_max_pinned`` Tunable (default 5, max 20) and seeds
``self.MAX_PINNED`` from it on each ``WatchlistManager.__init__``
so the value travels through Settings → "Pinned sub-tab cap"
without any caller having to change which attribute it reads.

The class-level ``WatchlistManager.MAX_PINNED = 5`` stays as a
safe fallback for tests + callers that read the attribute
without instantiating, but the instance attribute is the truth.

These tests pin:

* Tunable exists, default 5, max 20.
* Fresh ``WatchlistManager()`` picks up the tunable value.
* In-place attribute access (``mgr.MAX_PINNED``) sees the
  configured value, not the class fallback.
* ``mgr.pin`` still raises at the configured cap (not at 5
  if the user lifted it).
* Validator rejects out-of-range overrides; class default wins.
* A corrupt settings read falls back to the class default 5.
"""
from __future__ import annotations

import pytest

from tradinglab import defaults, settings
from tradinglab.watchlists import WatchlistManager
from tradinglab.watchlists.storage import Watchlist


@pytest.fixture(autouse=True)
def _isolate_settings():
    saved = dict(settings._store)
    saved_path = settings._loaded_path
    saved_dirty = settings._dirty

    settings._store.clear()
    settings._loaded_path = None
    settings._dirty = False
    defaults.reload()

    yield

    settings._store.clear()
    settings._store.update(saved)
    settings._loaded_path = saved_path
    settings._dirty = saved_dirty
    defaults.reload()


class TestPinnedCapTunable:
    def test_tunable_registered_with_default_five(self):
        match = [t for t in defaults.TUNABLES
                 if t.key == "watchlist_max_pinned"]
        assert len(match) == 1
        t = match[0]
        assert t.default == 5
        assert t.kind == "int"
        assert t.is_user_facing is True

    def test_default_get_returns_five(self):
        assert defaults.get("watchlist_max_pinned") == 5

    def test_validator_rejects_above_twenty(self):
        settings.set("watchlist_max_pinned", 9999)
        defaults.reload()
        # Validator rejected; class default wins.
        assert defaults.get("watchlist_max_pinned") == 5

    def test_validator_rejects_zero(self):
        settings.set("watchlist_max_pinned", 0)
        defaults.reload()
        assert defaults.get("watchlist_max_pinned") == 5


class TestManagerSeedsFromTunable:
    def test_fresh_manager_picks_up_persisted_cap(self):
        settings.set("watchlist_max_pinned", 12)
        defaults.reload()
        mgr = WatchlistManager()
        assert mgr.MAX_PINNED == 12

    def test_fresh_manager_default_is_five(self):
        # Brand-new manager + no override → matches the class default.
        mgr = WatchlistManager()
        assert mgr.MAX_PINNED == 5

    def test_existing_managers_are_unaffected_by_mid_session_change(self):
        # Audit decision: mid-session changes don't surprise an already-
        # constructed manager. The user must re-launch (or the app
        # rebuilds the manager) for the cap to take effect.
        mgr = WatchlistManager()
        assert mgr.MAX_PINNED == 5
        settings.set("watchlist_max_pinned", 12)
        defaults.reload()
        assert mgr.MAX_PINNED == 5
        # But a new manager built after the change sees it.
        mgr2 = WatchlistManager()
        assert mgr2.MAX_PINNED == 12

    def test_class_attribute_is_five_fallback(self):
        # Callers that read ``WatchlistManager.MAX_PINNED`` directly
        # (without instantiating) still get the safe fallback.
        assert WatchlistManager.MAX_PINNED == 5

    def test_corrupt_tunable_read_falls_back_to_class_default(self, monkeypatch):
        def _boom(_key):
            raise RuntimeError("settings.json is corrupt")
        monkeypatch.setattr(defaults, "get", _boom)
        mgr = WatchlistManager()
        assert mgr.MAX_PINNED == 5


class TestPinAtConfiguredCap:
    """The pin() guard must use the *instance* cap, not the class one."""

    def test_pin_raises_at_lifted_cap(self):
        settings.set("watchlist_max_pinned", 7)
        defaults.reload()
        mgr = WatchlistManager()
        # Create 8 watchlists; pin the first 7; pinning the 8th raises.
        for i in range(8):
            mgr.create(f"WL{i}", ["AAPL"])
        # First, clear the auto-seeded pin to ensure we control state.
        for n in list(mgr.pinned_names()):
            mgr.unpin(n)
        for i in range(7):
            mgr.pin(f"WL{i}")
        assert len(mgr.pinned_names()) == 7
        with pytest.raises(ValueError, match="7"):
            mgr.pin("WL7")

    def test_pin_still_works_below_default_cap_after_lowering(self):
        # Lower the cap below the class default 5 — pins above the new
        # cap must be rejected as expected.
        settings.set("watchlist_max_pinned", 3)
        defaults.reload()
        mgr = WatchlistManager()
        for i in range(4):
            mgr.create(f"WL{i}", ["AAPL"])
        for n in list(mgr.pinned_names()):
            mgr.unpin(n)
        for i in range(3):
            mgr.pin(f"WL{i}")
        with pytest.raises(ValueError, match="3"):
            mgr.pin("WL3")
