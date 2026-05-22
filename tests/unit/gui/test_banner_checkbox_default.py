"""Regression test for the ``banner-checkbox-default`` audit.

The reviewer flagged that clicking the close button on the
first-run banner silently silenced the banner forever because
the "Don't show again" checkbox defaulted to checked. That's
the classic "infer destructive intent from a navigational click"
anti-pattern: a user who just wants to peek under the banner ends
up never seeing the onboarding tips again.

After the fix the checkbox defaults to **unchecked**. Closing the
banner without ticking the box keeps the sentinel unwritten, so
the banner reappears on the next launch. The user has to opt in
to permanent silence.

These tests avoid real Tk so they're robust against the
intermittent ``init.tcl`` flake on this Windows runner. The
two behaviors that matter are:
  1. The literal ``tk.IntVar(...)`` default in the source must be 0.
  2. ``_dismiss_first_run_banner`` consults the var: unchecked
     skips the sentinel write, checked persists it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tradinglab.gui import banner as banner_mod
from tradinglab.gui.banner import FirstRunBannerMixin


class _FakeVar:
    """Mirror ``tk.IntVar`` just enough for ``_dismiss_first_run_banner``."""

    def __init__(self, value: int = 0) -> None:
        self._value = int(value)

    def get(self) -> int:
        return self._value

    def set(self, value: int) -> None:
        self._value = int(value)


@pytest.fixture(autouse=True)
def _sandbox_data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(banner_mod, "_sentinel_path",
                        lambda: tmp_path / ".first_run_dismissed")
    return tmp_path


def test_source_literal_default_is_unchecked():
    """The literal ``value=0`` in ``_build_first_run_banner`` is the
    contract surface — a future refactor that flips it back to 1 must
    be caught here."""
    src = Path(banner_mod.__file__).read_text(encoding="utf-8")
    assert "tk.IntVar(master=frame, value=0)" in src, (
        "Default state of 'Don't show again' must be unchecked "
        "(IntVar value=0) so a casual close click doesn't silently "
        "silence onboarding (audit banner-checkbox-default).")
    assert "tk.IntVar(master=frame, value=1)" not in src, (
        "Old default 'value=1' (checked) must be gone — that's the "
        "regression banner-checkbox-default exists to prevent.")


def test_dismiss_with_unchecked_var_skips_sentinel(_sandbox_data_root):
    """Path the user actually walks: open banner, click X, never tick."""

    class Stub(FirstRunBannerMixin):
        pass

    stub = Stub()
    stub._first_run_banner = None
    stub._banner_dont_show_var = _FakeVar(value=0)  # default

    stub._dismiss_first_run_banner()

    sentinel = _sandbox_data_root / ".first_run_dismissed"
    assert not sentinel.exists(), (
        "Closing the banner with the default-unchecked checkbox must "
        "NOT persist dismissal — the user only clicked X.")


def test_dismiss_with_checked_var_writes_sentinel(_sandbox_data_root):
    """Opt-in path: user explicitly ticks the box before closing."""

    class Stub(FirstRunBannerMixin):
        pass

    stub = Stub()
    stub._first_run_banner = None
    stub._banner_dont_show_var = _FakeVar(value=1)  # user opted in

    stub._dismiss_first_run_banner()

    sentinel = _sandbox_data_root / ".first_run_dismissed"
    assert sentinel.exists(), (
        "When the user explicitly ticks 'Don't show again', the "
        "sentinel must be written so the banner never returns.")


def test_dismiss_legacy_stub_without_var_still_persists(_sandbox_data_root):
    """Hosts that drive ``_dismiss_first_run_banner`` without ever
    building the widget (legacy test stubs) keep the pre-existing
    "always persist" behavior so existing unit tests don't break.
    """

    class Stub(FirstRunBannerMixin):
        pass

    stub = Stub()
    stub._first_run_banner = None
    stub._banner_dont_show_var = None  # never built

    stub._dismiss_first_run_banner()

    sentinel = _sandbox_data_root / ".first_run_dismissed"
    assert sentinel.exists(), (
        "Legacy stub path (no IntVar built) must persist as before so "
        "other unit tests' Stub mixins keep working.")


def test_docs_mention_default_unchecked():
    """Module-level docstring and visual contract section spell out the
    default. Future readers should not have to read code to learn it."""
    src = Path(banner_mod.__file__).read_text(encoding="utf-8")
    assert "default unchecked" in src.lower(), (
        "Module docstring should document the default-unchecked "
        "checkbox contract so future readers know the design intent.")


def test_spec_mentions_default_unchecked():
    """The companion .spec.md must agree with the code's default."""
    spec_path = Path(banner_mod.__file__).with_suffix(".spec.md")
    assert spec_path.exists()
    text = spec_path.read_text(encoding="utf-8")
    assert "default unchecked" in text.lower(), (
        "banner.spec.md must say the checkbox default is unchecked.")
    # Sanity: the old phrasing ('default checked') must not survive.
    assert "default checked" not in text.lower(), (
        "Old 'default checked' wording must be gone from banner.spec.md.")
