"""Behavioral coverage for the package and PyInstaller-style entry points.

The real entry file runs against stub modules: no GUI, singleton lock, or
child process is started. ``run_path`` supplies the package-less script
context used by PyInstaller; it does not exercise the frozen bootloader.
The shared coverage config excludes the ``__main__`` guard, so its reported
statement count is not a measure of the startup behavior exercised here.
"""
from __future__ import annotations

import importlib
import runpy
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, call, sentinel

import pytest

import tradinglab

_ENTRY_POINT = Path(tradinglab.__file__).with_name("__main__.py")


@pytest.fixture
def dependencies(monkeypatch):
    calls = Mock()
    calls.main.return_value = 0
    calls.single_instance_guard.return_value = (True, sentinel.handle)
    exports = {
        "multiprocessing": {"freeze_support": calls.freeze_support},
        "tradinglab._single_instance": {
            "single_instance_guard": calls.single_instance_guard,
            "release_single_instance": calls.release_single_instance,
        },
        "tradinglab.app": {"main": calls.main},
    }
    for name, attributes in exports.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
        if name.startswith("tradinglab."):
            monkeypatch.setattr(tradinglab, name.rsplit(".", 1)[1], module, raising=False)

    # Track even an initially absent cache entry so fresh imports cannot leak
    # references to the stubs into subsequent tests.
    monkeypatch.setitem(sys.modules, "tradinglab.__main__", None)
    monkeypatch.delitem(sys.modules, "tradinglab.__main__")
    monkeypatch.setattr(tradinglab, "__main__", None, raising=False)
    return calls


@pytest.fixture(params=["package", "top-level-script"])
def execute_entrypoint(request):
    if request.param == "package":
        return lambda: runpy.run_module("tradinglab", run_name="__main__")
    return lambda: runpy.run_path(str(_ENTRY_POINT), run_name="__main__")


@pytest.mark.parametrize("return_code", [0, 7, None])
def test_main_return_code_propagates_and_releases_handle(
    dependencies, execute_entrypoint, return_code,
):
    dependencies.main.return_value = return_code

    with pytest.raises(SystemExit) as exited:
        execute_entrypoint()

    assert exited.value.code == return_code
    assert dependencies.mock_calls == [
        call.freeze_support(),
        call.single_instance_guard(),
        call.main(),
        call.release_single_instance(sentinel.handle),
    ]


def test_duplicate_instance_exits_zero_without_main_or_release(dependencies, execute_entrypoint):
    dependencies.single_instance_guard.return_value = (False, None)

    with pytest.raises(SystemExit) as exited:
        execute_entrypoint()

    assert exited.value.code == 0
    assert dependencies.mock_calls == [
        call.freeze_support(),
        call.single_instance_guard(),
    ]


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_main_exception_propagates_and_releases_handle(
    dependencies, execute_entrypoint, error_type,
):
    error = error_type("main stopped")
    dependencies.main.side_effect = error

    with pytest.raises(error_type) as raised:
        execute_entrypoint()

    assert raised.value is error
    assert dependencies.mock_calls == [
        call.freeze_support(),
        call.single_instance_guard(),
        call.main(),
        call.release_single_instance(sentinel.handle),
    ]


def test_unavailable_lock_backend_still_launches_and_releases_none(dependencies, execute_entrypoint):
    dependencies.single_instance_guard.return_value = (True, None)

    with pytest.raises(SystemExit) as exited:
        execute_entrypoint()

    assert exited.value.code == 0
    assert dependencies.mock_calls == [
        call.freeze_support(),
        call.single_instance_guard(),
        call.main(),
        call.release_single_instance(None),
    ]


def test_freeze_support_child_exit_precedes_guard_and_main(dependencies, execute_entrypoint):
    child_exit = SystemExit(0)
    dependencies.freeze_support.side_effect = child_exit

    with pytest.raises(SystemExit) as exited:
        execute_entrypoint()

    assert exited.value is child_exit
    assert dependencies.mock_calls == [call.freeze_support()]


def test_guard_exception_does_not_launch_or_release(dependencies, execute_entrypoint):
    error = RuntimeError("guard stopped")
    dependencies.single_instance_guard.side_effect = error

    with pytest.raises(RuntimeError) as raised:
        execute_entrypoint()

    assert raised.value is error
    assert dependencies.mock_calls == [
        call.freeze_support(),
        call.single_instance_guard(),
    ]


def test_import_does_not_start_and_preserves_console_main(dependencies):
    dependencies.main.return_value = 23

    module = importlib.import_module("tradinglab.__main__")

    assert dependencies.mock_calls == []
    assert module.main is dependencies.main
    assert module.main() == 23
    assert dependencies.mock_calls == [call.main()]
