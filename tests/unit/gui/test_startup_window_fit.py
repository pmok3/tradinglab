"""First-run "unboxing" startup window auto-fit.

`ChartApp._ensure_startup_window_fits` widens the main window on first
launch (no saved geometry) so every toolbar control is visible. These
tests exercise the method in isolation via a lightweight fake `self`,
avoiding a full (display-bound) ChartApp construction.
"""
from __future__ import annotations

from tradinglab.app import ChartApp


class _FakeFrame:
    def __init__(self, reqw: int) -> None:
        self._reqw = reqw

    def winfo_reqwidth(self) -> int:
        return self._reqw


class _FakeToolbar:
    def __init__(self, reqw: int) -> None:
        self.frame = _FakeFrame(reqw)


class _FakeApp:
    def __init__(self, *, stored: bool, reqw: int, geom: str, screen_w: int) -> None:
        self._has_stored_main_geometry = stored
        self._toolbar = _FakeToolbar(reqw)
        self._startup_screen_wh = (screen_w, 1080)
        self._geom = geom
        self._initial_geometry = geom
        self.geometry_calls: list[str] = []

    def update_idletasks(self) -> None:
        pass

    def geometry(self, new: str | None = None) -> str | None:
        if new is None:
            return self._geom
        self._geom = new
        self.geometry_calls.append(new)
        return None

    def winfo_screenwidth(self) -> int:
        return self._startup_screen_wh[0]


def _run(fake: _FakeApp) -> None:
    ChartApp._ensure_startup_window_fits(fake)  # type: ignore[arg-type]


def test_first_run_widens_to_toolbar_width():
    fake = _FakeApp(stored=False, reqw=1400, geom="1000x800+50+60", screen_w=1920)
    _run(fake)
    # needed = 1400 + 24 = 1424; recentred on a 1920px screen.
    assert fake.geometry_calls == ["1424x800+248+60"]
    assert fake._initial_geometry == "1424x800+248+60"


def test_first_run_clamps_to_screen_width():
    fake = _FakeApp(stored=False, reqw=2000, geom="1000x800+10+60", screen_w=1600)
    _run(fake)
    # needed 2024 clamps to the 1600px screen.
    assert fake.geometry_calls == ["1600x800+0+60"]


def test_first_run_no_change_when_already_wide_enough():
    fake = _FakeApp(stored=False, reqw=900, geom="1200x800+0+0", screen_w=1920)
    _run(fake)
    assert fake.geometry_calls == []
    assert fake._initial_geometry == "1200x800+0+0"


def test_saved_geometry_is_never_overridden():
    fake = _FakeApp(stored=True, reqw=4000, geom="1000x800+0+0", screen_w=1920)
    _run(fake)
    assert fake.geometry_calls == []
    assert fake._initial_geometry == "1000x800+0+0"


def test_degenerate_toolbar_width_is_ignored():
    fake = _FakeApp(stored=False, reqw=1, geom="1000x800+0+0", screen_w=1920)
    _run(fake)
    assert fake.geometry_calls == []
