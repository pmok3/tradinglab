"""Unit tests for :mod:`tradinglab.gui.banner`.

The banner module's pure helpers (``is_first_run``,
``write_dismissal_sentinel``, ``clear_dismissal_sentinel``) read and
write a zero-byte sentinel at ``paths.app_data_dir() /
".first_run_dismissed"``. To keep tests hermetic we redirect
``tradinglab.paths.app_data_dir`` to ``tmp_path`` in an autouse
fixture; the banner's ``_sentinel_path`` re-imports the function on
every call, so attribute-patching the module suffices.

The :class:`FirstRunBannerMixin` is exercised via a tiny ``Stub``
subclass with ``_build_first_run_banner`` swapped for a recording
``Mock``. That avoids any Tk dependency while still letting us assert
the mixin's three documented behaviors:

* ``_maybe_show_first_run_banner`` is a no-op when the sentinel exists.
* ``_force_show_first_run_banner`` clears the sentinel and builds the
  banner.
* ``_dismiss_first_run_banner`` writes the sentinel and destroys the
  active widget.
"""
from __future__ import annotations

import pathlib
from unittest.mock import Mock

import pytest

from tradinglab import paths as paths_module
from tradinglab.gui.banner import (
    FirstRunBannerMixin,
    clear_dismissal_sentinel,
    is_first_run,
    write_dismissal_sentinel,
)


_SENTINEL_NAME = ".first_run_dismissed"


@pytest.fixture(autouse=True)
def _sandbox_data_root(tmp_path, monkeypatch):
    """Redirect ``paths.app_data_dir`` into the per-test ``tmp_path``.

    ``banner._sentinel_path`` performs ``from ..paths import app_data_dir``
    on every invocation, so patching the attribute on the module is
    sufficient — no module-state cache needs clearing.
    """
    monkeypatch.setattr(paths_module, "app_data_dir", lambda: tmp_path)
    return tmp_path


def test_is_first_run_lifecycle(_sandbox_data_root):
    sentinel = _sandbox_data_root / _SENTINEL_NAME

    assert not sentinel.exists()
    assert is_first_run() is True

    write_dismissal_sentinel()
    assert sentinel.is_file()
    assert is_first_run() is False

    clear_dismissal_sentinel()
    assert not sentinel.exists()
    assert is_first_run() is True


def test_is_first_run_swallows_oserror(monkeypatch):
    """A filesystem ``OSError`` must fail closed (no nag banner)."""

    def _boom(self):  # noqa: ARG001 - signature matches Path.is_file
        raise OSError("simulated permission error")

    monkeypatch.setattr(pathlib.Path, "is_file", _boom)

    assert is_first_run() is False


def test_first_run_banner_mixin_maybe_force_dismiss(_sandbox_data_root):
    class Stub(FirstRunBannerMixin):
        """Minimal mixin host; no Tk root required."""

    sentinel = _sandbox_data_root / _SENTINEL_NAME

    # --- 1. Sentinel exists → _maybe_show_first_run_banner is a no-op ---
    write_dismissal_sentinel()
    assert sentinel.is_file()

    stub = Stub()
    stub._first_run_banner = None
    build_mock = Mock()
    # Instance-level override shadows the class method, so the mixin's
    # ``self._build_first_run_banner(target)`` call routes to the mock.
    stub._build_first_run_banner = build_mock

    parent_sentinel = object()
    stub._maybe_show_first_run_banner(parent=parent_sentinel)

    build_mock.assert_not_called()
    assert stub._first_run_banner is None
    assert sentinel.is_file(), "no-op path must not touch the sentinel"

    # --- 2. _force_show clears sentinel AND creates the banner ---
    stub._force_show_first_run_banner(parent=parent_sentinel)

    assert not sentinel.exists(), "force-show must remove the sentinel"
    build_mock.assert_called_once_with(parent_sentinel)

    # --- 3. _dismiss writes sentinel AND destroys the active widget ---
    widget = Mock()
    stub._first_run_banner = widget

    stub._dismiss_first_run_banner()

    assert sentinel.is_file(), "dismiss must persist the sentinel"
    widget.destroy.assert_called_once_with()
    assert stub._first_run_banner is None


# ---------------------------------------------------------------
# Banner content (audit ``alt-h-discoverability``)
# ---------------------------------------------------------------

class TestBannerCopy:
    """The first-run banner is the single most-seen onboarding
    surface; if it doesn't mention an under-discoverable hotkey
    the hotkey may as well not exist. These tests pin the
    content so a future copy edit can't quietly delete the
    mention without a deliberate spec change."""

    def test_banner_mentions_chartstack_hotkey(self):
        from tradinglab.gui.banner import _BANNER_TEXT

        # Existing call-out (kept after audit ``alt-h-discoverability``).
        assert "Ctrl+\u0060" in _BANNER_TEXT or "Ctrl+`" in _BANNER_TEXT
        assert "ChartStack" in _BANNER_TEXT

    def test_banner_mentions_alt_h_horizontal_line(self):
        # Audit ``alt-h-discoverability``: Ctrl+H must be advertised
        # in the first-run banner so new users discover the drawing
        # hotkey without reading docs.
        from tradinglab.gui.banner import _BANNER_TEXT

        assert "Ctrl+H" in _BANNER_TEXT
        # The text must explain what Alt+H actually does, not just
        # name the chord.
        lowered = _BANNER_TEXT.lower()
        assert "horizontal line" in lowered

    def test_banner_text_starts_with_welcome(self):
        # Sanity: the banner must still read like a welcome,
        # not a feature dump.
        from tradinglab.gui.banner import _BANNER_TEXT

        assert _BANNER_TEXT.lower().startswith("welcome")

    def test_onboarding_doc_documents_alt_h(self):
        # The Help \u2192 Getting Started target (docs/ONBOARDING.md)
        # is the deeper reference. Pin a mention of Alt+H so the
        # banner can confidently point users there.
        from pathlib import Path

        repo = Path(__file__).resolve().parents[3]
        doc = repo / "docs" / "ONBOARDING.md"
        if not doc.is_file():
            pytest.skip(f"{doc} not present in this checkout")
        text = doc.read_text(encoding="utf-8")
        assert "Ctrl+H" in text or "Alt+H" in text
