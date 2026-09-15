"""Hosted display setup must be explicit and must not weaken GUI assertions."""
from __future__ import annotations

import ctypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.unit.test_gui_coverage_workflow import _job, _steps
from tools import configure_test_display as display

ROOT = Path(__file__).resolve().parents[2]


class FakeDisplay:
    def __init__(self, width=1024, height=768, *, failure=False):
        self.actual = width, height
        self.calls = []
        self.failure = failure

    def size(self):
        return self.actual

    def resize(self, width, height):
        self.calls.append((width, height))
        if self.failure:
            raise RuntimeError("Unsupported mode")
        self.actual = width, height


def test_sets_and_verifies_real_resolution():
    desktop = FakeDisplay()
    assert display.configure_display(desktop, 1920, 1080) == (1920, 1080)
    assert desktop.calls == [(1920, 1080)]


def test_already_correct_desktop_is_not_reconfigured():
    desktop = FakeDisplay(1920, 1080)
    assert display.configure_display(desktop, 1920, 1080) == desktop.actual
    assert not desktop.calls


def test_readonly_probe_fails_small_screen_without_modifying_it():
    desktop = FakeDisplay()
    with pytest.raises(RuntimeError, match="actual desktop is 1024x768"):
        display.configure_display(desktop, 1920, 1080, check_only=True)
    assert not desktop.calls


def test_readonly_probe_accepts_larger_desktop_without_resizing():
    desktop = FakeDisplay(2560, 1440)
    assert display.configure_display(desktop, 1920, 1080, check_only=True) == (2560, 1440)
    assert not desktop.calls


def test_unsupported_mode_fails_instead_of_skipping_width_assertions():
    with pytest.raises(RuntimeError, match="Unsupported"):
        display.configure_display(FakeDisplay(failure=True), 1920, 1080)


def test_success_return_without_actual_display_change_is_rejected(monkeypatch):
    desktop = FakeDisplay()
    monkeypatch.setattr(desktop, "resize", lambda *_: None)
    times = iter([0, 6])
    monkeypatch.setattr(display.time, "monotonic", lambda: next(times))
    with pytest.raises(RuntimeError, match="actual desktop"):
        display.configure_display(desktop, 1920, 1080)


def test_win32_mode_has_fixed_width_windows_abi():
    assert ctypes.sizeof(display._DevMode) == 220
    assert display._DevMode.fields.offset == 72
    assert display._DevMode.width.offset == 172
    assert display._DevMode.height.offset == 176


@pytest.mark.parametrize("result", [1, -1, -2])
def test_native_mode_failure_is_not_a_success(monkeypatch, result):
    user32 = SimpleNamespace(
        EnumDisplaySettingsW=Mock(return_value=1),
        ChangeDisplaySettingsExW=Mock(return_value=result),
        GetSystemMetrics=Mock(side_effect=[1024, 768]),
    )
    monkeypatch.setattr(display.ctypes, "WinDLL", lambda *_a, **_k: user32, raising=False)
    desktop = display.WindowsDisplay()
    assert desktop.size() == (1024, 768)
    with pytest.raises(RuntimeError, match="Windows rejected"):
        desktop.resize(1920, 1080)
    assert user32.ChangeDisplaySettingsExW.call_count == 1, "Never apply a mode whose CDS_TEST failed"


def test_native_mode_test_precedes_session_only_apply(monkeypatch):
    flags = []
    def change(_device, pointer, _window, flag, _extra):
        mode = ctypes.cast(pointer, ctypes.POINTER(display._DevMode)).contents
        assert (mode.width, mode.height, mode.fields) == (1920, 1080, 0x180000)
        flags.append(flag)
        return 0
    user32 = SimpleNamespace(
        EnumDisplaySettingsW=Mock(return_value=1),
        ChangeDisplaySettingsExW=Mock(side_effect=change),
        GetSystemMetrics=Mock(),
    )
    monkeypatch.setattr(display.ctypes, "WinDLL", lambda *_a, **_k: user32, raising=False)
    display.WindowsDisplay().resize(1920, 1080)
    assert flags == [2, 0], "Do not use CDS_UPDATEREGISTRY or ask for a reboot"


def test_cli_refuses_to_change_a_local_desktop(monkeypatch):
    monkeypatch.setattr(display.sys, "platform", "win32")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(display, "WindowsDisplay", lambda: pytest.fail("must reject before accessing display"))
    with pytest.raises(SystemExit) as error:
        display.main([])
    assert error.value.code == 2


def test_cli_verifies_tk_and_records_actual_measurement_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(display.sys, "platform", "win32")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    env_file = tmp_path / "github-env"
    monkeypatch.setenv("GITHUB_ENV", str(env_file))
    monkeypatch.setattr(display, "WindowsDisplay", FakeDisplay)
    observed = []
    monkeypatch.setattr(display, "verify_tk_display", lambda w, h: observed.append((w, h)) or (w, h))
    assert display.main([]) == 0
    assert observed == [(1920, 1080)]
    assert env_file.read_text() == "TRADINGLAB_CI_DISPLAY=1920x1080\n"


def test_failed_tk_verification_does_not_publish_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(display.sys, "platform", "win32")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "github-env"))
    monkeypatch.setattr(display, "WindowsDisplay", FakeDisplay)
    def fail(*_):
        raise RuntimeError("Tk sees only 1024x768")
    monkeypatch.setattr(display, "verify_tk_display", fail)
    assert display.main([]) == 1
    assert not (tmp_path / "github-env").exists()


@pytest.mark.parametrize("job", ["unit", "coverage", "gui-coverage", "smoke"])
def test_every_windows_ci_gui_producer_sets_display_before_tests(job):
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    steps = _steps(_job(workflow, job))
    setup, = [step for step in steps if step.startswith("name: Configure GUI test desktop\n")]
    assert "run: python tools/configure_test_display.py" in setup
    assert "continue-on-error:" not in setup
    first_test = next(i for i, step in enumerate(steps) if "run:" in step and "pytest " in step)
    assert steps.index(setup) < first_test
    if job == "smoke":
        assert "if: runner.os == 'Windows'" in setup
        assert '--server-args="-screen 0 1920x1080x24"' in _job(workflow, job)
    else:
        assert "if:" not in setup


def test_release_native_builds_share_display_precondition_without_publishing():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    steps = _steps(_job(workflow, "build-windows"))
    setup, = [step for step in steps if step.startswith("name: Configure GUI test desktop\n")]
    assert "python tools/configure_test_display.py" in setup
    assert steps.index(setup) < next(i for i, step in enumerate(steps) if "python -m pytest " in step)
    assert 'default: "false"' in workflow


def test_changed_line_consumer_does_not_need_a_gui():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "configure_test_display" not in _job(workflow, "changed-line-coverage")
