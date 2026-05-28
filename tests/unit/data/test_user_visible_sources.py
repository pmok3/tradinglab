"""Pin the internal-source UI-hiding contract.

The synthetic / synthetic-stream data sources are registered into
``DATA_SOURCES`` so smoke tests, sandbox replay, and offline
scaffolding can dispatch through them programmatically — but they MUST
NOT be surfaced in user-facing dropdowns (toolbar source combobox,
Settings → Startup parameters source dropdown).

This test pins:

1. Synthetic sources are present in ``DATA_SOURCES``.
2. Synthetic sources are FILTERED OUT of ``user_visible_sources()``.
3. ``is_internal_source`` returns True for them, False for yfinance.
4. The first user-visible source is ``yfinance`` (default selection).
5. The ``internal=True`` flag round-trips through repeat registration.
6. Plain re-registration without ``internal=True`` clears the flag
   (matches the docstring contract — tests that re-register without
   the flag are explicitly opting the source back into the UI).
7. ``AppState._resolve_source`` demotes an internal source name to
   the first user-visible source (handles old settings.json files
   that hand-edited ``source="synthetic"`` when it was still
   selectable).
"""
from __future__ import annotations

import pytest

from tradinglab.data import (
    DATA_SOURCES,
    is_internal_source,
    register_source,
    user_visible_sources,
)


def test_synthetic_sources_present_in_registry() -> None:
    """Smoke tests, sandbox replay, and the strategy_tester's offline
    fetcher all dispatch through ``DATA_SOURCES[<key>]`` directly.
    The synthetic entries must stay there even though they're hidden
    from the UI."""
    assert "synthetic" in DATA_SOURCES
    assert "synthetic-stream" in DATA_SOURCES


def test_synthetic_sources_hidden_from_user_visible_list() -> None:
    visible = user_visible_sources()
    assert "synthetic" not in visible, (
        "synthetic data source must not appear in any user-facing "
        "dropdown — register it with internal=True")
    assert "synthetic-stream" not in visible, (
        "synthetic-stream data source must not appear in any user-"
        "facing dropdown — register it with internal=True")


def test_is_internal_source_predicate() -> None:
    assert is_internal_source("synthetic") is True
    assert is_internal_source("synthetic-stream") is True
    assert is_internal_source("yfinance") is False
    assert is_internal_source("nonexistent") is False


def test_yfinance_is_first_user_visible_source() -> None:
    """The toolbar combobox / Startup parameters dropdown default to
    the first user-visible source. After hiding synthetic / synthetic-
    stream, the first entry must be yfinance."""
    visible = user_visible_sources()
    assert visible, "user_visible_sources() must return a non-empty list"
    assert visible[0] == "yfinance"


def test_user_visible_sources_preserves_insertion_order() -> None:
    """Order matters because the first entry is the default selection."""
    visible = user_visible_sources()
    # Every visible source must appear in the same order as in DATA_SOURCES.
    visible_idx = [list(DATA_SOURCES).index(s) for s in visible]
    assert visible_idx == sorted(visible_idx), (
        f"user_visible_sources order must match DATA_SOURCES insertion "
        f"order; got {visible} -> indices {visible_idx}")


def test_internal_flag_round_trips_on_repeat_registration() -> None:
    """Idempotent re-registration with internal=True preserves the flag."""
    original_fn = DATA_SOURCES.get("synthetic")
    assert original_fn is not None
    try:
        replacement = lambda t, i: None  # noqa: E731
        register_source("synthetic", replacement, internal=True)
        assert is_internal_source("synthetic") is True
        assert "synthetic" not in user_visible_sources()
        assert DATA_SOURCES["synthetic"] is replacement
    finally:
        # Restore the real fetcher for downstream tests.
        register_source("synthetic", original_fn, internal=True)


def test_plain_reregistration_clears_internal_flag() -> None:
    """Documented behaviour: re-register without internal=True flips the
    flag off. Tests that use this path are explicitly opting the source
    back into the UI; they MUST restore the flag in their cleanup."""
    original_fn = DATA_SOURCES.get("synthetic")
    assert original_fn is not None
    try:
        replacement = lambda t, i: None  # noqa: E731
        register_source("synthetic", replacement)  # no internal=True
        assert is_internal_source("synthetic") is False
        assert "synthetic" in user_visible_sources()
    finally:
        # Restore the internal flag + the real fetcher.
        register_source("synthetic", original_fn, internal=True)
    # Sanity: cleanup restored the contract.
    assert is_internal_source("synthetic") is True
    assert "synthetic" not in user_visible_sources()


def test_resolve_source_demotes_internal_value() -> None:
    """``AppState._resolve_source`` reads ``startup_defaults["source"]``
    from settings.json. An old user might still have ``source="synthetic"``
    persisted from before the UI-hide; the resolver must demote that to
    the first user-visible source rather than honour the internal
    selection."""
    from tradinglab.gui.app_state import AppState

    # Internal source value → demoted to first user-visible.
    assert AppState._resolve_source({"source": "synthetic"}) == "yfinance"
    assert AppState._resolve_source({"source": "synthetic-stream"}) == "yfinance"
    # Unregistered value → same demotion.
    assert AppState._resolve_source({"source": "no_such_source"}) == "yfinance"
    # Empty / missing → same demotion.
    assert AppState._resolve_source({}) == "yfinance"
    assert AppState._resolve_source({"source": ""}) == "yfinance"
    # Valid user-visible source is honoured.
    assert AppState._resolve_source({"source": "yfinance"}) == "yfinance"


def test_synthetic_still_dispatchable_via_dict_access() -> None:
    """Confirm that hiding the source from the UI does not break
    programmatic dispatch — smoke tests + sandbox replay + offline
    helpers all read ``DATA_SOURCES[name]`` directly."""
    synthetic_fn = DATA_SOURCES["synthetic"]
    assert callable(synthetic_fn)
    synthetic_stream_fn = DATA_SOURCES["synthetic-stream"]
    assert callable(synthetic_stream_fn)
