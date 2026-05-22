"""Tests for ``ChartApp._on_global_space``.

The global Space-key handler used to emit a ``"Space received (focus
class: …)"`` status info message on every keystroke for diagnostic
reasons. That noise drowned out legitimate status messages (a single
key roll on a watchlist could clobber a vendor warning the user
actually needed to see). The diagnostic was removed in 2026-05
(audit finding ``debug-print-leak``). These tests pin that removal
without instantiating Tk.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest


def _load_method():
    from tradinglab.app import ChartApp

    return ChartApp._on_global_space


class _RecordingStatus:
    """Captures every call routed through :meth:`_status.info/warn/error`."""

    def __init__(self) -> None:
        self.info_calls: list[str] = []
        self.warn_calls: list[str] = []
        self.error_calls: list[str] = []

    def info(self, msg: str) -> None:
        self.info_calls.append(str(msg))

    def warn(self, msg: str) -> None:
        self.warn_calls.append(str(msg))

    def error(self, msg: str) -> None:
        self.error_calls.append(str(msg))


class _FakeWidget:
    def __init__(self, cls: str = "Frame") -> None:
        self._cls = cls

    def winfo_class(self) -> str:
        return self._cls


class _FakeEvent:
    def __init__(self, widget: Any) -> None:
        self.widget = widget


class _StubApp:
    """Minimal stand-in for ChartApp covering ``_on_global_space``."""

    def __init__(
        self,
        *,
        focus_class: str = "Frame",
        typing_target: Any = None,
        typing_buffer: str = "",
        cycle_raises: bool = False,
    ) -> None:
        self._status = _RecordingStatus()
        self._typing_target = typing_target
        self._typing_buffer = typing_buffer
        self._focus_widget = _FakeWidget(focus_class)
        self.cycle_calls = 0
        self.cancel_click_calls = 0
        self._cycle_raises = cycle_raises

    def _cycle_watchlist_ticker(self) -> None:
        self.cycle_calls += 1
        if self._cycle_raises:
            raise RuntimeError("boom")

    def _cancel_click_to_type(self) -> None:
        self.cancel_click_calls += 1
        self._typing_target = None
        self._typing_buffer = ""


@pytest.fixture
def space_handler():
    return _load_method()


class TestNoDebugPing:
    """The handler must not emit any ``info``-level status on success."""

    def test_silent_on_plain_chart_focus(
        self, space_handler: Any,
    ) -> None:
        stub = _StubApp(focus_class="Frame")
        event = _FakeEvent(stub._focus_widget)

        result = space_handler(stub, event)

        assert result == "break"
        assert stub.cycle_calls == 1
        # The audit finding: no "Space received" debug status.
        assert stub._status.info_calls == [], (
            f"Removed diagnostic should not fire — got: "
            f"{stub._status.info_calls!r}"
        )

    def test_silent_on_button_focus(
        self, space_handler: Any,
    ) -> None:
        stub = _StubApp(focus_class="TButton")
        event = _FakeEvent(stub._focus_widget)

        space_handler(stub, event)

        assert stub._status.info_calls == []

    def test_silent_on_treeview_focus(
        self, space_handler: Any,
    ) -> None:
        stub = _StubApp(focus_class="Treeview")
        event = _FakeEvent(stub._focus_widget)

        space_handler(stub, event)

        assert stub._status.info_calls == []

    def test_silent_on_unknown_class(
        self, space_handler: Any,
    ) -> None:
        # A widget whose winfo_class() raises must not poison the path
        # with a fallback "(focus class: unknown)" message.
        class _BrokenWidget:
            def winfo_class(self) -> str:
                raise RuntimeError("no display")

        stub = _StubApp()
        event = _FakeEvent(_BrokenWidget())

        space_handler(stub, event)

        assert stub._status.info_calls == []

    def test_no_focus_class_token_string_anywhere(
        self, space_handler: Any,
    ) -> None:
        """Belt-and-braces: also check warn/error in case future
        refactors move the diagnostic into a different log level.
        """
        stub = _StubApp(focus_class="Frame")
        event = _FakeEvent(stub._focus_widget)

        space_handler(stub, event)

        all_msgs = (stub._status.info_calls
                    + stub._status.warn_calls
                    + stub._status.error_calls)
        for msg in all_msgs:
            assert "focus class" not in msg.lower(), (
                f"'focus class' diagnostic shape leaked back in: {msg!r}"
            )
            assert "space received" not in msg.lower(), (
                f"'Space received' diagnostic shape leaked back in: "
                f"{msg!r}"
            )


class TestTextInputBypass:
    """Space in a text-input widget must remain a literal character."""

    @pytest.mark.parametrize(
        "cls",
        ["Entry", "TEntry", "TCombobox", "Combobox", "Spinbox",
         "TSpinbox", "Text", "TText"],
    )
    def test_text_class_returns_none_no_cycle(
        self, space_handler: Any, cls: str,
    ) -> None:
        stub = _StubApp(focus_class=cls)
        event = _FakeEvent(stub._focus_widget)

        result = space_handler(stub, event)

        assert result is None
        assert stub.cycle_calls == 0
        assert stub._status.info_calls == []


class TestCyclePathStillWorks:
    """Removing the diagnostic must not break the actual cycle."""

    def test_cycle_called_once_on_success(
        self, space_handler: Any,
    ) -> None:
        stub = _StubApp()
        event = _FakeEvent(stub._focus_widget)

        space_handler(stub, event)

        assert stub.cycle_calls == 1

    def test_cycle_error_routes_to_status_error(
        self, space_handler: Any,
    ) -> None:
        stub = _StubApp(cycle_raises=True)
        event = _FakeEvent(stub._focus_widget)

        result = space_handler(stub, event)

        assert result == "break"
        assert stub.cycle_calls == 1
        assert len(stub._status.error_calls) == 1
        assert "Watchlist cycle error" in stub._status.error_calls[0]


class TestTypingBufferGuards:
    """Active mid-type must suppress; empty typing target cancels."""

    def test_active_typing_blocks_with_warn(
        self, space_handler: Any,
    ) -> None:
        stub = _StubApp(typing_target="primary", typing_buffer="AAP")
        event = _FakeEvent(stub._focus_widget)

        result = space_handler(stub, event)

        assert result == "break"
        # Cycle blocked
        assert stub.cycle_calls == 0
        # User-visible warn fired (NOT the removed debug ping)
        assert len(stub._status.warn_calls) == 1
        assert "typing" in stub._status.warn_calls[0].lower()
        # Removed diagnostic still gone
        assert stub._status.info_calls == []

    def test_empty_typing_buffer_cancels_then_cycles(
        self, space_handler: Any,
    ) -> None:
        stub = _StubApp(typing_target="primary", typing_buffer="")
        event = _FakeEvent(stub._focus_widget)

        result = space_handler(stub, event)

        assert result == "break"
        assert stub.cancel_click_calls == 1
        assert stub.cycle_calls == 1
        assert stub._status.info_calls == []


class TestSourceShapeRegression:
    """Source-level guard: ``Space received`` literal must not return."""

    def test_no_space_received_string_in_handler_source(self) -> None:
        import inspect

        from tradinglab.app import ChartApp

        src = inspect.getsource(ChartApp._on_global_space)
        assert "Space received" not in src, (
            "Regression: ``Space received`` diagnostic ping was "
            "re-introduced into ChartApp._on_global_space. The 2026-05 "
            "fix (audit debug-print-leak) deliberately removed it; "
            "any future diagnostic should be DEBUG-level, not "
            "user-visible status."
        )
        assert "focus class:" not in src, (
            "Regression: ``focus class:`` diagnostic literal was "
            "re-introduced into ChartApp._on_global_space."
        )
