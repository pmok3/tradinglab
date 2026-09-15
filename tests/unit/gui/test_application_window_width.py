"""Mapped width checks for project-owned ad-hoc windows, not native OS dialogs."""
from __future__ import annotations

from contextlib import nullcontext

import pytest

from tests._application_window_cases import (
    POPUP_CASES,
    case_parameters,
    isolate_geometry,
    popup_probe,
    settle,
)
from tests._window_width import assert_window_width, enlarged_fonts, mapped_window


@pytest.mark.parametrize("case", case_parameters(POPUP_CASES))
@pytest.mark.parametrize("font_size", [None, 16], ids=["normal-font", "large-font"])
def test_application_popup_width(case, font_size, root, monkeypatch, tmp_path):
    isolate_geometry(monkeypatch, tmp_path, case, "default")
    fonts = enlarged_fonts(root, size=font_size) if font_size else nullcontext()
    failures = []
    with mapped_window(root), fonts, popup_probe(case, root, monkeypatch, tmp_path) as probe:
        with mapped_window(probe.window):
            for state in probe.states():
                settle(probe.window)
                try:
                    assert_window_width(probe.window)
                except AssertionError as exc:
                    failures.append(f"{state}: {exc}")
                if not case.natural_size:
                    width, _height = probe.window.minsize()
                    probe.window.geometry(f"{width}x{max(800, probe.window.winfo_height())}")
                    settle(probe.window)
                    try:
                        assert_window_width(probe.window)
                    except AssertionError as exc:
                        failures.append(f"{state} minimum: {exc}")
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("case", case_parameters(tuple(c for c in POPUP_CASES if c.geometry_key)))
@pytest.mark.parametrize("font_size", [None, 16], ids=["normal-font", "large-font"])
def test_application_popup_restored_narrow_width(case, font_size, root, monkeypatch, tmp_path):
    isolate_geometry(monkeypatch, tmp_path, case, "saved")
    fonts = enlarged_fonts(root, size=font_size) if font_size else nullcontext()
    failures = []
    with mapped_window(root), fonts, popup_probe(case, root, monkeypatch, tmp_path) as probe:
        with mapped_window(probe.window):
            for state in probe.states():
                settle(probe.window)
                try:
                    assert_window_width(probe.window)
                except AssertionError as exc:
                    failures.append(f"{state} restored: {exc}")
    assert not failures, "\n".join(failures)
