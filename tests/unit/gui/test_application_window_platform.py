"""Exercise smoke platform routing without simulating a macOS Tk interpreter."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests._application_window_cases import HEAVY_CASES
from tests.smoke import test_smoke_window_width as window_width


class _ProbeStarted(RuntimeError):
    pass


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
@pytest.mark.parametrize("case", HEAVY_CASES, ids=lambda case: case.name)
def test_only_headless_macos_transient_cases_skip_before_setup(case, platform, monkeypatch, tmp_path):
    monkeypatch.setattr(window_width, "sys", SimpleNamespace(platform=platform))

    def start_probe(*_args, **_kwargs):
        raise _ProbeStarted("width setup reached")

    monkeypatch.setattr(window_width, "isolate_geometry", start_probe)
    guarded = platform == "darwin" and case.name in {"settings", "performance", "strategy"}
    expected = pytest.skip.Exception if guarded else _ProbeStarted
    message = "transient.*headless macOS" if guarded else "width setup reached"
    with pytest.raises(expected, match=message):
        window_width.check_w0_application_window_width(
            None, case, "default", None, monkeypatch, tmp_path,
        )
