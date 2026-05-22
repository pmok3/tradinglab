"""Tests for ``ChartApp._on_alt_h_placement`` focus suppression.

The Alt+H drawing-placement shortcut is bound on the root via
``bind_all`` so it fires regardless of focus. That global reach is
exactly what we want when the user is hovering the chart — but it
also meant Alt+H would steal keystrokes from users mid-type in a
ticker Entry (and would race with the Windows Alt+H menu mnemonic
for a menubar `Help` cascade). The 2026-05 fix (audit
``alt-h-entry-suppression``) added a text-input-class allowlist
that returns ``None`` without placing a line — mirroring the
text-class bypass in :meth:`_on_global_space`.
"""

from __future__ import annotations

from typing import Any

import pytest


def _load_method():
    from tradinglab.app import ChartApp

    return ChartApp._on_alt_h_placement


class _FakeWidget:
    def __init__(self, cls: str = "Frame") -> None:
        self._cls = cls

    def winfo_class(self) -> str:
        return self._cls


class _FakeEvent:
    def __init__(self, widget: Any) -> None:
        self.widget = widget


class _RecordingStore:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, drawing: Any) -> None:
        self.added.append(drawing)


class _FakeBbox:
    def __init__(self, contains_result: bool) -> None:
        self._contains = contains_result

    def contains(self, _x: float, _y: float) -> bool:
        return self._contains


class _FakeTransform:
    def inverted(self) -> _FakeTransform:
        return self

    def transform(self, _px: tuple[float, float]) -> tuple[float, float]:
        return (0.0, 123.45)


class _FakeAxes:
    def __init__(self, contains: bool = True) -> None:
        self.bbox = _FakeBbox(contains)
        self.transData = _FakeTransform()


class _StubApp:
    """Minimal stand-in for ChartApp covering the Alt+H path."""

    def __init__(
        self,
        *,
        slot_ticker: str = "AAPL",
        cursor_px: tuple[float, float] | None = (100.0, 200.0),
        axes_contains_cursor: bool = True,
        fallback_px: tuple[float, float] | None = None,
    ) -> None:
        self._drawings = _RecordingStore()
        self._last_cursor_px = cursor_px
        self._panel_state = {
            "primary": {"price_ax": _FakeAxes(axes_contains_cursor)},
        }
        self._slot_ticker = slot_ticker
        self._last_drawing_color = "#2962FF"
        self._drawings_snap_to_ohlc = False
        self._fallback_px = fallback_px
        self.fallback_calls = 0

    def _slot_symbol(self, _slot: str) -> str:
        return self._slot_ticker

    def _resolve_cursor_px_fallback(
        self,
    ) -> tuple[float, float] | None:
        self.fallback_calls += 1
        return self._fallback_px

    def _compute_snapped_drawing_price(
        self,
        _ax: Any,
        _slot_key: str,
        y_data: float,
        _y_pixel: float,
    ) -> float:
        # Stub mirrors the real helper's contract: grid-snap with
        # no OHLC magnet (snap-to-OHLC is disabled in this fixture).
        return round(float(y_data), 2)


@pytest.fixture
def alt_h():
    return _load_method()


_TEXT_CLASSES = ["Entry", "TEntry", "TCombobox", "Combobox", "Spinbox",
                 "TSpinbox", "Text", "TText"]


class TestTextInputBypass:
    """Alt+H must NOT place a line when focus is on a text input."""

    @pytest.mark.parametrize("cls", _TEXT_CLASSES)
    def test_text_class_returns_none_no_line(
        self, alt_h: Any, cls: str,
    ) -> None:
        stub = _StubApp()
        event = _FakeEvent(_FakeWidget(cls))

        result = alt_h(stub, event)

        assert result is None, (
            f"Alt+H over a {cls} widget must return None (not 'break') "
            "so the keystroke continues to bubble — got {result!r}"
        )
        assert stub._drawings.added == [], (
            f"Alt+H over a {cls} widget placed a line: "
            f"{stub._drawings.added!r}"
        )


class TestNormalPlacement:
    """Alt+H must still work when focus is NOT on a text widget."""

    @pytest.mark.parametrize(
        "cls", ["Frame", "TFrame", "TButton", "Treeview", "Canvas",
                "TNotebook", "Toplevel", "Tk"],
    )
    def test_non_text_class_places_line(
        self, alt_h: Any, cls: str,
    ) -> None:
        stub = _StubApp()
        event = _FakeEvent(_FakeWidget(cls))

        result = alt_h(stub, event)

        assert result == "break"
        assert len(stub._drawings.added) == 1
        drawing = stub._drawings.added[0]
        assert getattr(drawing, "ticker", None) == "AAPL"
        assert pytest.approx(getattr(drawing, "price", None), 0.001) == 123.45

    def test_no_event_argument_still_places_line(
        self, alt_h: Any,
    ) -> None:
        # Some callers (e.g. accelerator-driven invocations) may pass
        # ``None``. Bypass MUST NOT fire on a None event.
        stub = _StubApp()
        result = alt_h(stub, None)
        assert result == "break"
        assert len(stub._drawings.added) == 1

    def test_widget_with_broken_winfo_class_falls_through(
        self, alt_h: Any,
    ) -> None:
        class _Broken:
            def winfo_class(self) -> str:
                raise RuntimeError("no display")

        stub = _StubApp()
        event = _FakeEvent(_Broken())
        # Broken widget can't be classified → cls=="" → not in the
        # text allowlist → normal placement proceeds.
        result = alt_h(stub, event)
        assert result == "break"
        assert len(stub._drawings.added) == 1


class TestPlacementGuardsUnchanged:
    """Guards that existed pre-fix must still hold."""

    def test_no_drawings_store_returns_break_no_line(
        self, alt_h: Any,
    ) -> None:
        stub = _StubApp()
        stub._drawings = None  # type: ignore[assignment]
        event = _FakeEvent(_FakeWidget("Frame"))
        # Store is None → returns "break" silently.
        result = alt_h(stub, event)
        assert result == "break"

    def test_no_cursor_returns_break_no_line(
        self, alt_h: Any,
    ) -> None:
        stub = _StubApp(cursor_px=None, fallback_px=None)
        event = _FakeEvent(_FakeWidget("Frame"))
        result = alt_h(stub, event)
        assert result == "break"
        assert stub._drawings.added == []
        # When the motion-event cache is empty, the handler MUST consult
        # the winfo_pointerxy fallback (the recent regression fix).
        assert stub.fallback_calls == 1

    def test_cursor_outside_axes_returns_break_no_line(
        self, alt_h: Any,
    ) -> None:
        stub = _StubApp(axes_contains_cursor=False)
        event = _FakeEvent(_FakeWidget("Frame"))
        result = alt_h(stub, event)
        assert result == "break"
        assert stub._drawings.added == []

    def test_empty_ticker_returns_break_no_line(
        self, alt_h: Any,
    ) -> None:
        stub = _StubApp(slot_ticker="")
        event = _FakeEvent(_FakeWidget("Frame"))
        result = alt_h(stub, event)
        assert result == "break"
        assert stub._drawings.added == []


class TestCursorFallback:
    """When ``_last_cursor_px`` is None the handler falls back to ``winfo_pointerxy``."""

    def test_fallback_px_used_when_cursor_cache_is_none(
        self, alt_h: Any,
    ) -> None:
        # Cache None but fallback succeeds → line is placed using the
        # fallback pixel coordinates.
        stub = _StubApp(cursor_px=None, fallback_px=(100.0, 200.0))
        event = _FakeEvent(_FakeWidget("Frame"))
        result = alt_h(stub, event)
        assert result == "break"
        assert stub.fallback_calls == 1
        assert len(stub._drawings.added) == 1
        assert getattr(stub._drawings.added[0], "ticker", None) == "AAPL"

    def test_fallback_not_consulted_when_cursor_cache_present(
        self, alt_h: Any,
    ) -> None:
        # Cache populated → fallback is bypassed (no winfo_pointerxy lookup).
        stub = _StubApp(cursor_px=(50.0, 60.0), fallback_px=(999.0, 999.0))
        event = _FakeEvent(_FakeWidget("Frame"))
        result = alt_h(stub, event)
        assert result == "break"
        assert stub.fallback_calls == 0
        assert len(stub._drawings.added) == 1


class TestAllowlistSyncWithSpaceHandler:
    """The text-input allowlist must stay in sync with `_on_global_space`."""

    def test_allowlists_identical(self) -> None:
        import inspect

        from tradinglab.app import ChartApp

        space_src = inspect.getsource(ChartApp._on_global_space)
        alth_src = inspect.getsource(ChartApp._on_alt_h_placement)
        # Both should mention the same set literal. We look for the
        # literal "TCombobox" and "TText" — distinctive enough that
        # divergence would be obvious.
        for marker in ("TEntry", "TCombobox", "TSpinbox", "TText"):
            assert marker in space_src, (
                f"_on_global_space dropped {marker} from its text-class "
                "allowlist — both handlers must stay in sync."
            )
            assert marker in alth_src, (
                f"_on_alt_h_placement dropped {marker} from its "
                "text-class allowlist — both handlers must stay in sync."
            )
