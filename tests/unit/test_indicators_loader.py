"""Unit tests for the custom-indicator drop-in folder loader.

Covers :func:`tradinglab.indicators.loader.discover_user_indicators`
behaviours documented in ``loader.spec.md``:

* Missing directories are not an error (empty result).
* Plugin namespace exposes ``__name__ = "tradinglab_plugin_<stem>"``
  and a ``__file__`` pointing at the source path.
* Per-file partial-failure rollback: a plugin that raised after
  registering classes has those registrations popped back out of the
  global :data:`INDICATORS` registry; cleanly-loaded sibling files are
  unaffected.
* ``register_globally=False`` captures registrations without polluting
  the global registry.

The :data:`INDICATORS` global lives in :mod:`tradinglab.indicators.base`
(re-exported via :mod:`tradinglab.indicators`). The autouse fixture
snapshots it before each test and restores it on teardown so plugin
pollution can never leak across tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tradinglab.indicators import base as _base
from tradinglab.indicators.loader import (
    DiscoveryResult,
    LoadedIndicator,
    LoadError,
    discover_user_indicators,
)


@pytest.fixture(autouse=True)
def _snapshot_indicators_registry():
    """Snapshot and restore the module-level indicator registries.

    Without this guard, a test that uses ``register_globally=True`` would
    leave entries behind for every subsequent test (and for the rest of
    the pytest session). The two dicts that :func:`register_indicator`
    mutates are :data:`base.INDICATORS` (display-name keyed) and
    :data:`base._BY_KIND_ID` (kind_id keyed). We snapshot both, replace
    the contents in-place on teardown, and assert nothing leaked.
    """
    indicators_snapshot = dict(_base.INDICATORS)
    by_kind_id_snapshot = dict(_base._BY_KIND_ID)
    try:
        yield
    finally:
        _base.INDICATORS.clear()
        _base.INDICATORS.update(indicators_snapshot)
        _base._BY_KIND_ID.clear()
        _base._BY_KIND_ID.update(by_kind_id_snapshot)


# ---------------------------------------------------------------------------
# 1. Missing directory
# ---------------------------------------------------------------------------


def test_discover_returns_empty_on_missing_dir(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    assert not missing.exists()

    result = discover_user_indicators(directory=missing)

    assert isinstance(result, DiscoveryResult)
    assert result.loaded == []
    assert result.errors == []


# ---------------------------------------------------------------------------
# 2. Plugin namespace exposes __name__ and __file__
# ---------------------------------------------------------------------------


def test_plugin_namespace_has_module_attrs(tmp_path: Path) -> None:
    """The exec namespace must surface ``__name__`` and ``__file__``.

    Per ``loader.spec.md``: ``__name__ = "tradinglab_plugin_<stem>"``
    and ``__file__`` is the absolute source path. ``LoadedIndicator``
    doesn't carry these directly, so the plugin smuggles them out by
    binding them as default arguments on the registered factory.
    """
    plugin = tmp_path / "myind.py"
    plugin.write_text(
        "def _factory(candles, _name=__name__, _file=__file__):\n"
        "    return {'name': _name, 'file': _file}\n"
        "register_indicator('custom_x', _factory)\n",
        encoding="utf-8",
    )

    result = discover_user_indicators(tmp_path, register_globally=False)

    assert result.errors == []
    records = [li for li in result.loaded if li.name == "custom_x"]
    assert len(records) == 1
    record = records[0]
    assert isinstance(record, LoadedIndicator)
    assert record.source_path == plugin

    smuggled = record.factory(candles=[])
    assert smuggled["name"] == "tradinglab_plugin_myind"
    smuggled_file = Path(smuggled["file"])
    assert smuggled_file == plugin
    # ``__file__`` must point at a path inside the scanned directory.
    assert tmp_path in smuggled_file.parents


# ---------------------------------------------------------------------------
# 3. Partial-failure rollback (per-file, per spec.md)
# ---------------------------------------------------------------------------


def test_partial_failure_rolls_back_globally_registered(tmp_path: Path) -> None:
    """A plugin that registers then raises has its names rolled back.

    Per ``loader.spec.md`` "Per-file partial-failure rollback": when a
    file raises *after* partially registering classes globally, the
    loader pops those registrations back out so the global registry
    stays consistent. Cleanly-loaded sibling files are unaffected
    (rollback is per-file, not cross-file).
    """
    # Sorted iteration → ``bad_partial.py`` runs before ``good_clean.py``.
    bad = tmp_path / "bad_partial.py"
    bad.write_text(
        "register_indicator('partial_ind', lambda c: {})\n"
        "raise RuntimeError('boom after partial registration')\n",
        encoding="utf-8",
    )
    good = tmp_path / "good_clean.py"
    good.write_text(
        "register_indicator('good_ind', lambda c: {})\n",
        encoding="utf-8",
    )

    snapshot_before = dict(_base.INDICATORS)
    assert "partial_ind" not in snapshot_before
    assert "good_ind" not in snapshot_before

    result = discover_user_indicators(tmp_path, register_globally=True)

    # The loader never re-raises; failures are surfaced via the errors list.
    assert len(result.errors) == 1
    err = result.errors[0]
    assert isinstance(err, LoadError)
    assert err.source_path == bad
    assert "RuntimeError" in err.error
    assert "boom after partial registration" in err.error
    assert "Traceback" in err.traceback_text

    # ``bad_partial.py`` partially registered ``partial_ind`` before
    # raising; the loader must pop it back out.
    assert "partial_ind" not in _base.INDICATORS

    # ``good_clean.py`` is a separate file and must still be loaded /
    # registered. A file in ``errors`` never appears in ``loaded``.
    loaded_names = {li.name for li in result.loaded}
    assert loaded_names == {"good_ind"}
    assert "good_ind" in _base.INDICATORS
    assert bad not in {li.source_path for li in result.loaded}

    # The only net delta from the pre-call snapshot is ``good_ind``.
    after = dict(_base.INDICATORS)
    delta = {k: v for k, v in after.items() if k not in snapshot_before}
    assert set(delta) == {"good_ind"}


# ---------------------------------------------------------------------------
# 4. register_globally=False is capture-only
# ---------------------------------------------------------------------------


def test_register_globally_false_is_capture_only(tmp_path: Path) -> None:
    plugin = tmp_path / "capture.py"
    plugin.write_text(
        "register_indicator('captured_only', lambda c: {})\n",
        encoding="utf-8",
    )

    assert "captured_only" not in _base.INDICATORS

    result = discover_user_indicators(tmp_path, register_globally=False)

    assert result.errors == []
    loaded_names = {li.name for li in result.loaded}
    assert "captured_only" in loaded_names
    # The whole point of register_globally=False: the global registry
    # is untouched.
    assert "captured_only" not in _base.INDICATORS
