from __future__ import annotations

import threading
from typing import Any

from tradinglab.core.thread_guard import TkThreadViolation, tk_thread_check_disabled
from tradinglab.drawings import DrawingStore, make_hline_drawing
from tradinglab.indicators.config import IndicatorConfig, IndicatorManager


def _run_in_thread(fn) -> tuple[Any, BaseException | None]:
    captured: dict[str, Any] = {"result": None, "exc": None}

    def _target() -> None:
        try:
            captured["result"] = fn()
        except BaseException as exc:  # noqa: BLE001
            captured["exc"] = exc

    worker = threading.Thread(target=_target, name="worker-thread")
    worker.start()
    worker.join(timeout=2.0)
    assert not worker.is_alive(), "worker thread hung"
    return captured["result"], captured["exc"]


def _indicator(period: int = 20) -> IndicatorConfig:
    return IndicatorConfig(
        kind_id="sma",
        params={"length": period},
        display_name=f"SMA({period})",
    )


def test_indicator_manager_add_off_thread() -> None:
    manager = IndicatorManager()
    _, exc = _run_in_thread(lambda: manager.add(_indicator()))
    assert isinstance(exc, TkThreadViolation)


def test_indicator_manager_remove_off_thread() -> None:
    manager = IndicatorManager()
    cfg = manager.add(_indicator())
    _, exc = _run_in_thread(lambda: manager.remove(cfg.id))
    assert isinstance(exc, TkThreadViolation)


def test_indicator_manager_update_off_thread() -> None:
    manager = IndicatorManager()
    cfg = manager.add(_indicator())
    _, exc = _run_in_thread(lambda: manager.update(cfg.id, display_name="Renamed"))
    assert isinstance(exc, TkThreadViolation)


def test_indicator_manager_clear_off_thread() -> None:
    manager = IndicatorManager()
    manager.add(_indicator())
    _, exc = _run_in_thread(manager.clear)
    assert isinstance(exc, TkThreadViolation)


def test_indicator_manager_load_dict_off_thread() -> None:
    manager = IndicatorManager()
    payload = {
        "active_configs": [_indicator().to_dict()],
        "presets": {},
        "active_preset": None,
    }
    _, exc = _run_in_thread(lambda: manager.load_dict(payload))
    assert isinstance(exc, TkThreadViolation)


def test_indicator_manager_ok_on_main_thread() -> None:
    manager = IndicatorManager()
    cfg = manager.add(_indicator())
    assert len(manager) == 1
    assert manager.remove(cfg.id) is True
    assert len(manager) == 0


def test_indicator_manager_ok_with_check_disabled() -> None:
    manager = IndicatorManager()

    def _work() -> int:
        with tk_thread_check_disabled():
            cfg = manager.add(_indicator())
            assert manager.remove(cfg.id) is True
            return len(manager)

    result, exc = _run_in_thread(_work)
    assert exc is None
    assert result == 0


def test_drawing_store_add_off_thread() -> None:
    store = DrawingStore(autosave=False)
    drawing = make_hline_drawing("AMD", 100.0)
    _, exc = _run_in_thread(lambda: store.add(drawing))
    assert isinstance(exc, TkThreadViolation)


def test_drawing_store_ok_on_main_thread() -> None:
    store = DrawingStore(autosave=False)
    drawing = make_hline_drawing("AMD", 100.0)
    added = store.add(drawing)
    assert added.id == drawing.id
    assert len(store.list("AMD")) == 1
