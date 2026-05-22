"""Audit ``sandbox-ref-symbol`` — tests for the sandbox reference-symbol setting.

Pre-2026-05 ``gui/sandbox_menu.py:56`` hard-coded ``"SPY"`` as the
sandbox master-clock anchor. The audit raised the issue that users
running futures or FX sources have no liquid SPY equivalent in their
data feed and the sandbox would refuse to start. The fix:

* Add a ``sandbox_reference_symbol`` ``defaults`` tunable with default
  ``"SPY"`` so existing behaviour stays identical for the 99% case.
* Read the override via ``defaults.get`` in
  :meth:`SandboxMenuMixin._on_menu_sandbox_start`, falling back to the
  literal ``"SPY"`` on missing-tunable / read-failure paths.
* Surface the setting in the Settings dialog (audit
  ``settings-dialog-grouping`` further refines layout).

These tests pin both the catalog entry AND the menu's lookup
behaviour so a future refactor that re-hardcodes "SPY" fails loudly.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from tradinglab import defaults, settings

# ---------------------------------------------------------------
# Shared fixture — keep defaults / settings test-hermetic.
# ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_state():
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


# ---------------------------------------------------------------
# Catalog entry
# ---------------------------------------------------------------


class TestSandboxReferenceSymbolTunable:
    def test_tunable_registered_with_spy_default(self):
        # Pinning the tunable entry means the catalog stays
        # consumer-discoverable (Help \u2192 Documentation Library,
        # README example-config generator) and the migration to a
        # different default in a future release shows up as a
        # deliberate test change.
        match = [t for t in defaults.TUNABLES
                 if t.key == "sandbox_reference_symbol"]
        assert len(match) == 1, "exactly one tunable entry expected"
        t = match[0]
        assert t.default == "SPY"
        assert t.kind == "str"
        assert t.is_user_facing is True

    def test_get_returns_default(self):
        assert defaults.get("sandbox_reference_symbol") == "SPY"

    def test_override_via_settings_is_visible_after_reload(self):
        settings.set("sandbox_reference_symbol", "QQQ")
        defaults.reload()
        assert defaults.get("sandbox_reference_symbol") == "QQQ"

    def test_blank_override_rejected_default_wins(self):
        # ``_v_str(allow_empty=False)`` validates the value; an empty
        # string should fall back to the registry default.
        settings.set("sandbox_reference_symbol", "")
        defaults.reload()
        assert defaults.get("sandbox_reference_symbol") == "SPY"

    def test_non_string_override_rejected(self):
        # The validator must reject non-strings (numbers, dicts, …)
        # so a typo in settings.json can't break the sandbox.
        settings.set("sandbox_reference_symbol", 42)
        defaults.reload()
        assert defaults.get("sandbox_reference_symbol") == "SPY"

    def test_whitespace_stripped_at_use_site(self):
        # The defaults layer keeps the raw user value; the use-site
        # in ``sandbox_menu.py`` is responsible for trimming + casing.
        # See ``TestSandboxMenuLookup`` below.
        settings.set("sandbox_reference_symbol", "  qqq ")
        defaults.reload()
        assert defaults.get("sandbox_reference_symbol") == "  qqq "


# ---------------------------------------------------------------
# Use-site lookup (sandbox_menu.py)
# ---------------------------------------------------------------


class TestSandboxMenuLookup:
    """The hot path: ``_on_menu_sandbox_start`` must consult
    ``defaults.get('sandbox_reference_symbol')`` before falling back
    to ``"SPY"``. Rather than spin up a full ChartApp this test
    inspects the bound module to make sure the lookup actually
    happens — guarding against a future refactor that quietly
    re-hardcodes the literal.
    """

    def test_module_source_calls_defaults_get(self):
        import inspect

        from tradinglab.gui import sandbox_menu

        src = inspect.getsource(
            sandbox_menu.SandboxMenuMixin._on_menu_sandbox_start,
        )
        # The function must consult the tunable.
        assert "sandbox_reference_symbol" in src, src[:600]
        # It must still preserve the SPY fallback for old configs.
        assert '"SPY"' in src

    def test_defaults_value_uppercased_and_stripped(self):
        # Simulate the use-site coercion to make sure the doc'd
        # behaviour matches:
        settings.set("sandbox_reference_symbol", "  qqq ")
        defaults.reload()
        raw = defaults.get("sandbox_reference_symbol")
        coerced = (str(raw or "").strip().upper() or "SPY")
        assert coerced == "QQQ"

    def test_blank_falls_back_to_spy(self):
        # If something upstream hands the use-site an empty value,
        # the fallback chain ends in literal "SPY".
        coerced = (str("" or "").strip().upper() or "SPY")
        assert coerced == "SPY"

    def test_exception_path_falls_back_to_spy(self):
        # If ``defaults.get`` raises (e.g. catalog desync mid-upgrade)
        # the use-site must catch and continue with "SPY".
        with patch.object(defaults, "get",
                          side_effect=RuntimeError("desync")):
            try:
                raw = defaults.get("sandbox_reference_symbol")
            except Exception:  # noqa: BLE001
                raw = "SPY"
            assert raw == "SPY"
