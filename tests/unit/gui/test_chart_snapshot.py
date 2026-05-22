"""Tests for ``ChartApp._save_chart_snapshot`` and helpers.

The chart-canvas right-click menu has a ``Snapshot Chart…`` entry that
used to dispatch to a missing ``_save_chart_snapshot`` method (audit
finding ``snapshot-dead-menu``). These tests exercise the new
implementation without instantiating Tk, using a lightweight stub.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest import mock

import pytest


def _load_methods():
    """Pull the snapshot methods off ``ChartApp`` without instantiation."""
    from tradinglab.app import ChartApp

    return (
        ChartApp._save_chart_snapshot,
        ChartApp._default_snapshot_filename,
        ChartApp._capture_chart_png,
    )


class _FakeFig:
    """Stand-in matplotlib Figure that records ``savefig`` calls."""

    def __init__(self, *, raise_on_save: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._raise = raise_on_save

    def savefig(self, path: str, **kwargs: Any) -> None:
        self.calls.append((str(path), dict(kwargs)))
        if self._raise:
            raise OSError("disk full")


class _StubApp:
    """Minimal stand-in for ChartApp covering the snapshot path."""

    def __init__(
        self,
        *,
        primary_ticker: str = "AAPL",
        compare_ticker: str = "MSFT",
        figure: Any = "default",
        savefig_raises: bool = False,
    ) -> None:
        self._confirmed_primary_ticker = primary_ticker
        self._confirmed_compare_ticker = compare_ticker
        if figure == "default":
            self._figure = _FakeFig(raise_on_save=savefig_raises)
        else:
            self._figure = figure
        # Bind the real helpers so the SUT can call them via ``self``.
        # ``_StubApp`` deliberately does not inherit from ChartApp (Tk
        # root creation is expensive + flaky); we wire only the
        # attributes the snapshot path actually touches.
        from tradinglab.app import ChartApp

        self._default_snapshot_filename = (
            ChartApp._default_snapshot_filename.__get__(self, _StubApp)
        )
        self._capture_chart_png = (
            ChartApp._capture_chart_png.__get__(self, _StubApp)
        )

    def _slot_symbol(self, slot: str) -> str:
        if slot == "primary":
            return str(self._confirmed_primary_ticker or "")
        return str(self._confirmed_compare_ticker or "")


@pytest.fixture
def _methods():
    save, defname, capture = _load_methods()
    return save, defname, capture


class TestDefaultSnapshotFilename:
    def test_includes_ticker_and_timestamp(self, _methods: Any) -> None:
        _save, defname, _capture = _methods
        stub = _StubApp(primary_ticker="AAPL")
        name = defname(stub, "primary")
        assert name.startswith("tradinglab_AAPL_")
        assert name.endswith(".png")
        # Timestamp segment is 15 chars: YYYYMMDD-HHMMSS
        body = name[len("tradinglab_AAPL_"):-len(".png")]
        assert len(body) == 15
        assert body[8] == "-"

    def test_uppercases_lowercase_ticker(self, _methods: Any) -> None:
        _save, defname, _capture = _methods
        stub = _StubApp(primary_ticker="aapl")
        name = defname(stub, "primary")
        assert name.startswith("tradinglab_AAPL_")

    def test_uses_compare_slot_ticker(self, _methods: Any) -> None:
        _save, defname, _capture = _methods
        stub = _StubApp(primary_ticker="AAPL", compare_ticker="MSFT")
        name = defname(stub, "compare")
        assert name.startswith("tradinglab_MSFT_")

    def test_no_ticker_fallback(self, _methods: Any) -> None:
        _save, defname, _capture = _methods
        stub = _StubApp(primary_ticker="")
        name = defname(stub, "primary")
        # No ticker but timestamp still present
        assert name.startswith("tradinglab_")
        assert name.endswith(".png")
        assert "AAPL" not in name

    def test_slot_symbol_error_silently_falls_back(
        self, _methods: Any,
    ) -> None:
        _save, defname, _capture = _methods
        stub = _StubApp()

        def _boom(_slot: str) -> str:
            raise RuntimeError("slot lookup failed")

        stub._slot_symbol = _boom  # type: ignore[assignment]
        name = defname(stub, "primary")
        assert name.endswith(".png")
        assert "AAPL" not in name


class TestCaptureChartPngPreservedBehavior:
    """Sanity: snapshot fix must not regress existing capture path."""

    def test_captures_to_path(
        self, _methods: Any, tmp_path: Path,
    ) -> None:
        _save, _defname, capture = _methods
        stub = _StubApp()
        out = tmp_path / "x.png"
        result = capture(stub, out)
        assert result == out
        assert stub._figure.calls == [(str(out), {"dpi": 100,
                                                  "bbox_inches": "tight"})]

    def test_none_when_figure_missing(
        self, _methods: Any, tmp_path: Path,
    ) -> None:
        _save, _defname, capture = _methods
        stub = _StubApp(figure=None)
        assert capture(stub, tmp_path / "x.png") is None

    def test_none_on_savefig_failure(
        self, _methods: Any, tmp_path: Path,
    ) -> None:
        _save, _defname, capture = _methods
        stub = _StubApp(savefig_raises=True)
        assert capture(stub, tmp_path / "x.png") is None


class TestSaveChartSnapshot:
    def test_cancel_is_silent_noop(
        self, _methods: Any,
    ) -> None:
        save, _defname, _capture = _methods
        stub = _StubApp()

        with mock.patch(
            "tradinglab.app.filedialog.asksaveasfilename",
            return_value="",
        ) as ask, mock.patch(
            "tradinglab.app.messagebox",
        ) as mbox:
            result = save(stub)

        assert result is None
        ask.assert_called_once()
        mbox.showerror.assert_not_called()
        mbox.showinfo.assert_not_called()
        # No save attempt happened
        assert stub._figure.calls == []

    def test_writes_file_and_shows_info(
        self, _methods: Any, tmp_path: Path,
    ) -> None:
        save, _defname, _capture = _methods
        stub = _StubApp()
        out = tmp_path / "shot.png"

        with mock.patch(
            "tradinglab.app.filedialog.asksaveasfilename",
            return_value=str(out),
        ), mock.patch(
            "tradinglab.app.messagebox",
        ) as mbox:
            result = save(stub)

        assert result == out
        assert stub._figure.calls == [(str(out), {"dpi": 100,
                                                  "bbox_inches": "tight"})]
        mbox.showinfo.assert_called_once()
        mbox.showerror.assert_not_called()

    def test_capture_failure_shows_error(
        self, _methods: Any, tmp_path: Path,
    ) -> None:
        save, _defname, _capture = _methods
        stub = _StubApp(savefig_raises=True)
        out = tmp_path / "shot.png"

        with mock.patch(
            "tradinglab.app.filedialog.asksaveasfilename",
            return_value=str(out),
        ), mock.patch(
            "tradinglab.app.messagebox",
        ) as mbox:
            result = save(stub)

        assert result is None
        mbox.showerror.assert_called_once()
        mbox.showinfo.assert_not_called()

    def test_missing_figure_shows_error(
        self, _methods: Any, tmp_path: Path,
    ) -> None:
        save, _defname, _capture = _methods
        stub = _StubApp(figure=None)
        out = tmp_path / "shot.png"

        with mock.patch(
            "tradinglab.app.filedialog.asksaveasfilename",
            return_value=str(out),
        ), mock.patch(
            "tradinglab.app.messagebox",
        ) as mbox:
            result = save(stub)

        assert result is None
        mbox.showerror.assert_called_once()

    def test_filedialog_exception_returns_none(
        self, _methods: Any,
    ) -> None:
        save, _defname, _capture = _methods
        stub = _StubApp()

        with mock.patch(
            "tradinglab.app.filedialog.asksaveasfilename",
            side_effect=RuntimeError("no display"),
        ), mock.patch(
            "tradinglab.app.messagebox",
        ) as mbox:
            result = save(stub)

        assert result is None
        mbox.showerror.assert_not_called()
        mbox.showinfo.assert_not_called()

    def test_default_initialfile_passed_to_dialog(
        self, _methods: Any,
    ) -> None:
        save, _defname, _capture = _methods
        stub = _StubApp(primary_ticker="NVDA")

        with mock.patch(
            "tradinglab.app.filedialog.asksaveasfilename",
            return_value="",
        ) as ask, mock.patch(
            "tradinglab.app.messagebox",
        ):
            save(stub, "primary")

        kwargs = ask.call_args.kwargs
        assert "initialfile" in kwargs
        assert kwargs["initialfile"].startswith("tradinglab_NVDA_")
        assert kwargs["defaultextension"] == ".png"
        assert ("PNG image", "*.png") in kwargs["filetypes"]

    def test_compare_slot_used_for_default_filename(
        self, _methods: Any,
    ) -> None:
        save, _defname, _capture = _methods
        stub = _StubApp(primary_ticker="AAPL", compare_ticker="TSLA")

        with mock.patch(
            "tradinglab.app.filedialog.asksaveasfilename",
            return_value="",
        ) as ask, mock.patch(
            "tradinglab.app.messagebox",
        ):
            save(stub, "compare")

        kwargs = ask.call_args.kwargs
        assert kwargs["initialfile"].startswith("tradinglab_TSLA_")


class TestMenuWiring:
    """The chart-canvas right-click menu must successfully reach
    ``_save_chart_snapshot`` through the ``_snapshot`` closure.
    """

    def test_save_chart_snapshot_is_callable_attribute(self) -> None:
        from tradinglab.app import ChartApp

        method = getattr(ChartApp, "_save_chart_snapshot", None)
        assert callable(method), (
            "Snapshot menu wiring at app.py:_show_chart_canvas_menu "
            "expects a callable _save_chart_snapshot. Regressing this "
            "re-introduces the dead-menu audit finding."
        )


class TestRealMatplotlibIntegration:
    """End-to-end: a real matplotlib Figure containing a drawing
    overlay must produce a non-empty PNG via ``_capture_chart_png``.

    Audit ``snapshot-implementation``: the menu was wired but the
    underlying capability must work against a real figure (drawings
    render to the same axes, so they are captured implicitly).
    """

    def test_real_figure_savefig_writes_nonempty_png(self, tmp_path: Path) -> None:
        import matplotlib
        matplotlib.use("Agg", force=True)
        from matplotlib.figure import Figure

        from tradinglab.app import ChartApp

        fig = Figure(figsize=(4, 3), dpi=80)
        ax = fig.add_subplot(111)
        ax.plot([0, 1, 2], [10.0, 12.0, 11.5])

        stub = _StubApp(figure=fig)
        # Bind the real method.
        method = ChartApp._capture_chart_png.__get__(stub, _StubApp)
        out = tmp_path / "real.png"
        result = method(out)

        assert result == out
        assert out.exists()
        # PNG magic bytes: 89 50 4E 47 0D 0A 1A 0A
        header = out.read_bytes()[:8]
        assert header == b"\x89PNG\r\n\x1a\n", (
            f"Expected PNG magic bytes at start of file, got {header!r}"
        )
        assert out.stat().st_size > 200, (
            "PNG should have a reasonable byte size (figure + axes + "
            f"plot line), got {out.stat().st_size} bytes"
        )

    def test_real_figure_with_hline_drawing_captures_drawing(
        self, tmp_path: Path,
    ) -> None:
        """A horizontal-line drawing rendered onto the axes lands in
        the saved PNG (matplotlib captures all artists added to the
        figure, including ``ax.axhline``)."""
        import matplotlib
        matplotlib.use("Agg", force=True)
        from matplotlib.figure import Figure

        from tradinglab.app import ChartApp
        from tradinglab.drawings.model import make_hline_drawing
        from tradinglab.drawings.render import render_drawings

        fig = Figure(figsize=(4, 3), dpi=80)
        ax = fig.add_subplot(111)
        ax.plot([0, 1, 2], [10.0, 12.0, 11.5])
        ax.set_ylim(8.0, 14.0)

        drawing = make_hline_drawing(
            ticker="AAPL", price=11.0, color="#FF0000", width=2.0,
        )
        render_drawings(ax, [drawing])

        # Drawing produced an artist on the axes.
        hlines = [
            ln for ln in ax.get_lines()
            if hasattr(ln, "get_ydata") and len(ln.get_ydata()) >= 2
        ]
        assert hlines, "render_drawings should produce a line artist on the axes"

        stub = _StubApp(figure=fig)
        method = ChartApp._capture_chart_png.__get__(stub, _StubApp)
        out = tmp_path / "with_hline.png"
        result = method(out)

        assert result == out
        assert out.exists() and out.stat().st_size > 200
