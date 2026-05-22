"""Unit tests for :mod:`tradinglab.gui.crash_dialog`.

Batch 4 of the test-coverage audit. Targets the pure helpers
(``_write_crash_file``, ``_prune_old_crash_files``, ``_handle_crash``,
``install_crash_handler``, ``install_tk_excepthook``). Only one test
needs a real ``tk.Tk`` root.

The production module reads its output directory from
``_logs_dir_or_fallback()`` (no ``crash_dir`` parameter exists), so we
redirect crash-file writes by monkeypatching that helper to return
``tmp_path``.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from tradinglab.gui import crash_dialog
from tradinglab.gui.crash_dialog import (
    MAX_CRASH_FILES_KEPT,
    _handle_crash,
    _prune_old_crash_files,
    _write_crash_file,
    install_crash_handler,
    install_tk_excepthook,
    reset_for_tests,
)

_FILENAME_RE = re.compile(r"^crash-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.txt$")


@pytest.fixture(autouse=True)
def _isolate_excepthook_and_module_state():
    """Snapshot/restore ``sys.excepthook`` and the module-level singletons."""
    saved_excepthook = sys.excepthook
    saved_prev = crash_dialog._PREV_EXCEPTHOOK
    # Force install_crash_handler() into "fresh" state for every test
    crash_dialog._PREV_EXCEPTHOOK = None
    try:
        yield
    finally:
        # Restore module state regardless of what the test did.
        crash_dialog._PREV_EXCEPTHOOK = saved_prev
        sys.excepthook = saved_excepthook
        # Defensive: explicit clear if the test left a wrapper in place.
        reset_for_tests()
        crash_dialog._PREV_EXCEPTHOOK = saved_prev
        sys.excepthook = saved_excepthook


@pytest.fixture
def redirect_logs_dir(monkeypatch, tmp_path):
    """Force ``_write_crash_file`` to write into ``tmp_path``."""
    monkeypatch.setattr(
        crash_dialog, "_logs_dir_or_fallback", lambda: tmp_path
    )
    return tmp_path


def test_write_crash_file_filename_and_content(redirect_logs_dir, tmp_path):
    try:
        raise ZeroDivisionError("synthetic crash for test")
    except ZeroDivisionError as exc:
        tb = exc.__traceback__
        exc_type = type(exc)
        exc_value = exc

    path = _write_crash_file(exc_type, exc_value, tb)

    txt_files = list(tmp_path.glob("*.txt"))
    assert len(txt_files) == 1, f"Expected exactly one crash file, got {txt_files}"
    assert path == txt_files[0]
    assert _FILENAME_RE.match(path.name), f"Filename {path.name!r} does not match crash-*.txt pattern"

    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()

    def has_line_starting_with(prefix: str) -> bool:
        return any(line.startswith(prefix) for line in lines)

    assert has_line_starting_with("Version:"), content
    assert has_line_starting_with("Python:"), content
    assert has_line_starting_with("Platform:"), content
    assert has_line_starting_with("Frozen:"), content
    assert any("Traceback" in line for line in lines), content
    assert "ZeroDivisionError" in content


def test_prune_old_crash_files_keeps_newest_N(tmp_path):
    n_extra = 5
    total = MAX_CRASH_FILES_KEPT + n_extra

    created: list[Path] = []
    for i in range(total):
        year = 2000 + i  # monotonically increasing → newer by name as i grows
        name = f"crash-{year:04d}-01-01T00-00-00.txt"
        p = tmp_path / name
        p.write_text("noop", encoding="utf-8")
        # Set mtimes in ascending order matching name order so the
        # newest-by-name file is also newest-by-mtime — the production
        # prune helper sorts by mtime.
        ts = 1_000_000_000 + i * 60
        os.utime(p, (ts, ts))
        created.append(p)

    # Also write a non-crash file: prune must NOT touch it.
    bystander = tmp_path / "not-a-crash.txt"
    bystander.write_text("keep me", encoding="utf-8")

    _prune_old_crash_files(tmp_path)

    remaining_crash = sorted(tmp_path.glob("crash-*.txt"))
    assert len(remaining_crash) == MAX_CRASH_FILES_KEPT, (
        f"Expected {MAX_CRASH_FILES_KEPT} crash files to remain, got {len(remaining_crash)}"
    )

    # The kept ones should be the lexicographically newest (= highest year).
    expected_kept = sorted(created)[-MAX_CRASH_FILES_KEPT:]
    assert [p.name for p in remaining_crash] == [p.name for p in expected_kept]

    assert bystander.exists(), "prune helper must not touch non-crash files"


def test_handle_crash_skips_keyboardinterrupt_and_systemexit(monkeypatch):
    write_mock = Mock()
    show_mock = Mock()
    monkeypatch.setattr(crash_dialog, "_write_crash_file", write_mock)
    monkeypatch.setattr(crash_dialog, "_show_dialog", show_mock)

    _handle_crash(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert write_mock.call_count == 0, "KeyboardInterrupt must not trigger crash write"

    _handle_crash(SystemExit, SystemExit(0), None)
    assert write_mock.call_count == 0, "SystemExit must not trigger crash write"

    # A genuine crash must trigger exactly one write.
    write_mock.return_value = Path("ignored")
    _handle_crash(RuntimeError, RuntimeError("oops"), None)
    assert write_mock.call_count == 1
    args, _kwargs = write_mock.call_args
    assert args[0] is RuntimeError
    assert isinstance(args[1], RuntimeError)


def test_install_crash_handler_idempotent_and_chains(monkeypatch):
    # Sentinel "original" excepthook so we can detect the chain call.
    original = Mock()
    monkeypatch.setattr(sys, "excepthook", original)

    # Avoid touching the filesystem / Tk during the chain test.
    monkeypatch.setattr(crash_dialog, "_write_crash_file", Mock(return_value=Path("x")))
    monkeypatch.setattr(crash_dialog, "_show_dialog", Mock())

    install_crash_handler()
    installed_after_first = sys.excepthook
    assert installed_after_first is not original, (
        "install_crash_handler() must replace sys.excepthook with a new wrapper"
    )
    assert crash_dialog._PREV_EXCEPTHOOK is original

    install_crash_handler()
    assert sys.excepthook is installed_after_first, (
        "install_crash_handler() must be idempotent — no second wrapper layer"
    )
    assert crash_dialog._PREV_EXCEPTHOOK is original, (
        "Second install must not overwrite the captured prior excepthook"
    )

    # Trigger the installed hook and verify chaining to the original.
    exc = RuntimeError("synthetic")
    sys.excepthook(RuntimeError, exc, None)

    assert original.call_count == 1, "Installed hook must chain to the prior excepthook"
    chained_args = original.call_args.args
    assert chained_args[0] is RuntimeError
    assert chained_args[1] is exc
    assert chained_args[2] is None

    # _handle_crash was exercised → _write_crash_file should have fired.
    assert crash_dialog._write_crash_file.call_count == 1


def test_install_tk_excepthook_sentinel(monkeypatch, tmp_path):
    pytest.importorskip("tkinter")
    import tkinter as tk

    monkeypatch.setattr(crash_dialog, "_logs_dir_or_fallback", lambda: tmp_path)
    monkeypatch.setattr(crash_dialog, "_show_dialog", Mock())

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk root unavailable in this environment: {exc}")
    root.withdraw()
    try:
        install_tk_excepthook(root)
        assert getattr(root, "_tradinglab_crash_hook_installed", False) is True
        first_hook = root.report_callback_exception

        install_tk_excepthook(root)
        assert root.report_callback_exception is first_hook, (
            "install_tk_excepthook must be idempotent — second call must not rebind"
        )

        # Invoke the hook directly; it must write a crash file under tmp_path.
        root.report_callback_exception(ValueError, ValueError("boom"), None)

        crash_files = list(tmp_path.glob("crash-*.txt"))
        assert len(crash_files) == 1, (
            f"Tk excepthook must produce a crash file, got {crash_files}"
        )
        assert "ValueError" in crash_files[0].read_text(encoding="utf-8")
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
