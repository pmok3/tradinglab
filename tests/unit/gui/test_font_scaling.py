"""Tests for the ``font-scaling`` audit.

Adds a UI scale multiplier (85% / 100% / 115% / 130%) to the
Settings dialog so users with hi-DPI displays, presbyopia, or a
personal preference can dial the chrome up or down without
re-launching. Applied live by re-running
:func:`configure_named_fonts` with a scale argument; persisted to
``settings.json["ui_scale"]``.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from tradinglab.gui.named_fonts import (
    DEFAULT_SIZE,
    DEFAULT_UI_SCALE,
    UI_SCALES,
    clamp_ui_scale,
)

# ---------------------------------------------------------------------------
# UI_SCALES — supported scale multipliers
# ---------------------------------------------------------------------------

def test_ui_scales_includes_default():
    assert DEFAULT_UI_SCALE in UI_SCALES


def test_ui_scales_is_sorted():
    """Sorted scales make the combobox / Spinbox UI predictable."""
    assert list(UI_SCALES) == sorted(UI_SCALES)


def test_ui_scales_covers_accessibility_range():
    """At least one entry below 1.0 (compact) and one >= 1.15 (a11y)."""
    assert any(s < 1.0 for s in UI_SCALES)
    assert any(s >= 1.15 for s in UI_SCALES)


def test_default_is_100_percent():
    assert DEFAULT_UI_SCALE == 1.0


# ---------------------------------------------------------------------------
# clamp_ui_scale — input sanitization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", list(UI_SCALES))
def test_clamp_passes_through_valid_scales(value):
    assert clamp_ui_scale(value) == value


def test_clamp_picks_nearest_for_in_between_value():
    # 1.07 is between 1.0 and 1.15; nearer to 1.0.
    assert clamp_ui_scale(1.07) == 1.0
    # 1.10 is between 1.0 and 1.15 (equidistant) — min() picks first.
    # Both choices are valid; just verify it's one of them.
    assert clamp_ui_scale(1.10) in (1.0, 1.15)


def test_clamp_caps_at_max_for_huge_values():
    assert clamp_ui_scale(10.0) == UI_SCALES[-1]


def test_clamp_floors_at_min_for_tiny_values():
    assert clamp_ui_scale(0.1) == UI_SCALES[0]


def test_clamp_handles_nan():
    assert clamp_ui_scale(float("nan")) == DEFAULT_UI_SCALE


def test_clamp_handles_inf():
    # +Inf is non-finite → default fallback. -Inf same.
    assert clamp_ui_scale(float("inf")) == DEFAULT_UI_SCALE
    assert clamp_ui_scale(float("-inf")) == DEFAULT_UI_SCALE


def test_clamp_handles_negative():
    assert clamp_ui_scale(-1.0) == DEFAULT_UI_SCALE
    assert clamp_ui_scale(-0.5) == DEFAULT_UI_SCALE


def test_clamp_handles_zero():
    assert clamp_ui_scale(0.0) == DEFAULT_UI_SCALE


def test_clamp_handles_garbage_strings():
    assert clamp_ui_scale("not-a-number") == DEFAULT_UI_SCALE  # type: ignore[arg-type]


def test_clamp_handles_none():
    assert clamp_ui_scale(None) == DEFAULT_UI_SCALE  # type: ignore[arg-type]


def test_clamp_handles_numeric_string():
    """settings.json sometimes round-trips numbers as strings."""
    assert clamp_ui_scale("1.15") == 1.15  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# configure_named_fonts — scale wiring (without a real Tk root)
# ---------------------------------------------------------------------------

def test_configure_named_fonts_signature_accepts_scale():
    """The function must accept a keyword-only ``scale`` arg."""
    import inspect

    from tradinglab.gui import named_fonts
    sig = inspect.signature(named_fonts.configure_named_fonts)
    assert "scale" in sig.parameters
    # Default must be the documented DEFAULT_UI_SCALE.
    assert sig.parameters["scale"].default == DEFAULT_UI_SCALE


def test_current_ui_scale_helper_exists():
    """Settings dialog needs to read current scale at open."""
    from tradinglab.gui.named_fonts import current_ui_scale
    assert callable(current_ui_scale)


def test_configure_named_fonts_with_real_root_applies_scale(monkeypatch):
    """End-to-end through a Mock root + nametofont stub."""
    from tradinglab.gui import named_fonts as nf

    nf._reset_for_tests()

    # Track every font.configure() call.
    configured: list[dict] = []

    class _FakeFont:
        def __init__(self, name: str) -> None:
            self.name = name

        def cget(self, _attr: str) -> str:
            return "normal"

        def configure(self, **kwargs) -> None:
            configured.append({"name": self.name, **kwargs})

    fake_fonts = {}

    def fake_nametofont(name: str, root=None):  # noqa: ARG001
        f = fake_fonts.setdefault(name, _FakeFont(name))
        return f

    monkeypatch.setattr(
        "tradinglab.gui.named_fonts.tkfont.nametofont", fake_nametofont)
    # Force the function past the macOS short-circuit even when
    # running on a CI macOS box: ensure _PROPORTIONAL_FAMILY is set.
    if not nf._PROPORTIONAL_FAMILY:
        monkeypatch.setattr(nf, "_PROPORTIONAL_FAMILY", "TestFamily")
        monkeypatch.setattr(nf, "_FIXED_FAMILY", "TestFixed")

    class _FakeRoot:
        pass

    # Apply with 1.15x scale.
    nf.configure_named_fonts(_FakeRoot(), scale=1.15)

    # Check every font got the scaled size.
    sizes = [c["size"] for c in configured]
    expected_default = int(round(DEFAULT_SIZE * 1.15))
    assert expected_default in sizes, (
        f"Expected at least one configured font at size "
        f"{expected_default} (DEFAULT_SIZE * 1.15); got {sizes}")


def test_configure_named_fonts_clamps_out_of_range_scale(monkeypatch):
    from tradinglab.gui import named_fonts as nf

    nf._reset_for_tests()
    configured: list[dict] = []

    class _FakeFont:
        def cget(self, _attr: str) -> str:
            return "normal"

        def configure(self, **kwargs) -> None:
            configured.append(kwargs)

    def fake_nametofont(name: str, root=None):  # noqa: ARG001
        return _FakeFont()

    monkeypatch.setattr(
        "tradinglab.gui.named_fonts.tkfont.nametofont", fake_nametofont)
    if not nf._PROPORTIONAL_FAMILY:
        monkeypatch.setattr(nf, "_PROPORTIONAL_FAMILY", "TestFamily")
        monkeypatch.setattr(nf, "_FIXED_FAMILY", "TestFixed")

    class _FakeRoot:
        pass

    # 100x scale should clamp to max (1.30), not actually 100x.
    nf.configure_named_fonts(_FakeRoot(), scale=100.0)
    sizes = {c["size"] for c in configured}
    # Maximum possible: DEFAULT_SIZE * 1.30 (or FIXED_SIZE * 1.30).
    max_expected = int(round(max(DEFAULT_SIZE, 10) * UI_SCALES[-1]))
    assert all(s <= max_expected for s in sizes), (
        f"Scale clamp failed: got sizes {sizes}, max should be "
        f"{max_expected}")


def test_configure_named_fonts_re_applies_when_scale_changes(monkeypatch):
    """Calling with a different scale should re-write fonts (not
    a no-op like the original idempotency promise)."""
    from tradinglab.gui import named_fonts as nf

    nf._reset_for_tests()
    call_count = [0]

    class _FakeFont:
        def cget(self, _attr: str) -> str:
            return "normal"

        def configure(self, **_kwargs) -> None:
            call_count[0] += 1

    def fake_nametofont(name: str, root=None):  # noqa: ARG001
        return _FakeFont()

    monkeypatch.setattr(
        "tradinglab.gui.named_fonts.tkfont.nametofont", fake_nametofont)
    if not nf._PROPORTIONAL_FAMILY:
        monkeypatch.setattr(nf, "_PROPORTIONAL_FAMILY", "TestFamily")
        monkeypatch.setattr(nf, "_FIXED_FAMILY", "TestFixed")

    class _FakeRoot:
        pass

    # First call at default — should configure all fonts.
    nf.configure_named_fonts(_FakeRoot(), scale=1.0)
    count_after_first = call_count[0]
    assert count_after_first > 0

    # Same scale — should NOT re-configure (idempotency holds).
    nf.configure_named_fonts(_FakeRoot(), scale=1.0)
    assert call_count[0] == count_after_first, (
        "Same-scale re-call should be a no-op")

    # Different scale — SHOULD re-configure.
    nf.configure_named_fonts(_FakeRoot(), scale=1.15)
    assert call_count[0] > count_after_first, (
        "Different-scale re-call must re-write fonts so the "
        "user's preview / settings change takes effect immediately")


def test_configure_minimum_size_floor(monkeypatch):
    """Even at 0.85x the smallest font must remain readable (≥ 6 px)."""
    from tradinglab.gui import named_fonts as nf

    nf._reset_for_tests()
    sizes_seen: list[int] = []

    class _FakeFont:
        def cget(self, _attr: str) -> str:
            return "normal"

        def configure(self, **kwargs) -> None:
            if "size" in kwargs:
                sizes_seen.append(int(kwargs["size"]))

    def fake_nametofont(name: str, root=None):  # noqa: ARG001
        return _FakeFont()

    monkeypatch.setattr(
        "tradinglab.gui.named_fonts.tkfont.nametofont", fake_nametofont)
    if not nf._PROPORTIONAL_FAMILY:
        monkeypatch.setattr(nf, "_PROPORTIONAL_FAMILY", "TestFamily")
        monkeypatch.setattr(nf, "_FIXED_FAMILY", "TestFixed")

    class _FakeRoot:
        pass

    # Smallest supported scale.
    nf.configure_named_fonts(_FakeRoot(), scale=UI_SCALES[0])
    assert all(s >= 6 for s in sizes_seen), (
        "Smallest supported scale must keep fonts ≥ 6 px so the "
        "chrome doesn't become unreadable")


# ---------------------------------------------------------------------------
# ChartApp wiring — source pin
# ---------------------------------------------------------------------------

APP_SRC = (Path(__file__).resolve().parents[3]
           / "src" / "tradinglab" / "app.py").read_text(encoding="utf-8")
DIALOGS_SRC = (Path(__file__).resolve().parents[3]
               / "src" / "tradinglab" / "gui" / "dialogs.py").read_text(
                   encoding="utf-8")


def test_chartapp_loads_ui_scale_setting():
    assert '"ui_scale"' in APP_SRC, (
        "ChartApp must read 'ui_scale' from settings.json")
    assert "self._ui_scale" in APP_SRC, (
        "ChartApp must store the scale on _ui_scale")


def test_chartapp_calls_configure_with_scale_kwarg():
    """The constructor must pass the loaded scale to
    configure_named_fonts so the chrome respects the persisted
    preference from the first widget on."""
    # Search ChartApp.__init__ region.
    init_idx = APP_SRC.find("def __init__")
    end_idx = APP_SRC.find("\n    def ", init_idx + 1)
    init_body = APP_SRC[init_idx:end_idx]
    assert "configure_named_fonts(self" in init_body
    assert "scale=" in init_body, (
        "configure_named_fonts must be called with an explicit "
        "scale kwarg so the user's preference applies before "
        "any widget is constructed")


def test_chartapp_defines_set_ui_scale():
    assert "def set_ui_scale" in APP_SRC, (
        "ChartApp.set_ui_scale setter must exist")
    start = APP_SRC.find("def set_ui_scale")
    end = APP_SRC.find("\n    def ", start + 1)
    body = APP_SRC[start:end] if end != -1 else APP_SRC[start:]
    assert "configure_named_fonts" in body, (
        "set_ui_scale must call configure_named_fonts to re-apply "
        "the scale to every named Tk font immediately")
    assert "_settings.set" in body, (
        "set_ui_scale must persist via _settings.set")


def test_chartapp_clamps_loaded_ui_scale():
    """Defense in depth: a corrupted settings.json must not push
    the chrome to an unreadable size at launch."""
    assert "clamp_ui_scale" in APP_SRC or "_clamp_ui_scale" in APP_SRC, (
        "ChartApp must clamp the loaded ui_scale via "
        "clamp_ui_scale so corrupted settings can't break the UI")


# ---------------------------------------------------------------------------
# Settings dialog wiring — source pin
# ---------------------------------------------------------------------------

def test_dialog_has_ui_scale_combobox():
    assert "_ui_scale_var" in DIALOGS_SRC, (
        "Settings dialog must define a _ui_scale_var Tk var")
    assert "UI scale" in DIALOGS_SRC, (
        "Settings dialog must label the control 'UI scale'")


def test_dialog_persists_ui_scale_on_ok():
    start = DIALOGS_SRC.find("def _on_ok")
    end = DIALOGS_SRC.find("\n    def ", start + 1)
    body = DIALOGS_SRC[start:end] if end != -1 else DIALOGS_SRC[start:]
    assert "set_ui_scale" in body, (
        "Settings dialog _on_ok must call set_ui_scale to persist")


def test_dialog_reverts_ui_scale_on_cancel():
    start = DIALOGS_SRC.find("def _on_cancel")
    end = DIALOGS_SRC.find("\n    def ", start + 1)
    body = DIALOGS_SRC[start:end] if end != -1 else DIALOGS_SRC[start:]
    assert "_ui_scale_initial" in body, (
        "Cancel must restore the dialog-open scale snapshot")


def test_dialog_format_and_parse_round_trip():
    from tradinglab.gui.dialogs import _SettingsDialog
    for scale in UI_SCALES:
        rendered = _SettingsDialog._format_ui_scale(scale)
        parsed = _SettingsDialog._parse_ui_scale(rendered)
        assert math.isclose(parsed, scale, abs_tol=1e-6), (
            f"Round-trip failed for {scale}: rendered={rendered!r} "
            f"parsed={parsed}")


def test_dialog_parse_handles_garbage():
    from tradinglab.gui.dialogs import _SettingsDialog
    assert _SettingsDialog._parse_ui_scale("") == 1.0
    assert _SettingsDialog._parse_ui_scale("not a percent") == 1.0
    assert _SettingsDialog._parse_ui_scale(None) == 1.0  # type: ignore[arg-type]


def test_dialog_format_handles_garbage():
    from tradinglab.gui.dialogs import _SettingsDialog
    assert _SettingsDialog._format_ui_scale(float("nan")) in ("0%", "100%")
    assert _SettingsDialog._format_ui_scale(None) == "100%"  # type: ignore[arg-type]
