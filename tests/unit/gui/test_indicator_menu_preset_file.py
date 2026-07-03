"""Tests for file-based indicator-preset Save/Load handlers.

Covers ``IndicatorMenuMixin._on_menu_save_indicator_preset_to_file`` and
``_on_menu_load_indicator_preset_from_file`` — the Save-As / open
file-dialog path that lets a preset live in a durable / portable
user-chosen location (audit ``indicator-save-location``).

The handlers are exercised against a minimal host stub exposing the two
attributes the mixin reads (``_indicator_manager`` + ``_status``); the
file dialogs + messagebox are monkeypatched (no Tk root needed).
"""
from __future__ import annotations

import tkinter.filedialog as _fd
import tkinter.messagebox as _mb
from pathlib import Path

from tradinglab.gui.indicator_menu import IndicatorMenuMixin
from tradinglab.indicators import preset_store
from tradinglab.indicators.config import IndicatorConfig, IndicatorManager


class _Status:
    def __init__(self) -> None:
        self.msgs: list[tuple[str, str]] = []

    def info(self, m: str) -> None:
        self.msgs.append(("info", m))

    def warn(self, m: str) -> None:
        self.msgs.append(("warn", m))

    def error(self, m: str) -> None:
        self.msgs.append(("error", m))


class _Host(IndicatorMenuMixin):
    """Minimal ChartApp stand-in exposing the attrs the mixin reads."""

    def __init__(self) -> None:
        self._indicator_manager = IndicatorManager(scheduler=lambda cb=None: None)
        self._status = _Status()


def _cfg(length: int) -> IndicatorConfig:
    return IndicatorConfig(kind_id="ema", params={"length": length})


# ---------------------------------------------------------------------------
# Save Preset to File…
# ---------------------------------------------------------------------------


def test_save_to_file_writes_active_set(tmp_path: Path, monkeypatch) -> None:
    host = _Host()
    host._indicator_manager.add(_cfg(9))
    host._indicator_manager.add(_cfg(21))
    dest = tmp_path / "layout.json"
    monkeypatch.setattr(_fd, "asksaveasfilename", lambda *a, **k: str(dest))
    host._on_menu_save_indicator_preset_to_file()
    assert dest.exists()
    out = preset_store.import_preset_from_file(dest)
    assert out is not None and len(out) == 2
    assert any(level == "info" for level, _ in host._status.msgs)


def test_save_to_file_empty_set_warns_no_dialog(tmp_path: Path, monkeypatch) -> None:
    host = _Host()
    opened = {"dialog": False}

    def _flag(*a, **k):
        opened["dialog"] = True
        return ""

    monkeypatch.setattr(_fd, "asksaveasfilename", _flag)
    host._on_menu_save_indicator_preset_to_file()
    assert opened["dialog"] is False  # nothing to save → no dialog
    assert any(level == "warn" for level, _ in host._status.msgs)


def test_save_to_file_cancelled_is_noop(tmp_path: Path, monkeypatch) -> None:
    host = _Host()
    host._indicator_manager.add(_cfg(9))
    monkeypatch.setattr(_fd, "asksaveasfilename", lambda *a, **k: "")
    host._on_menu_save_indicator_preset_to_file()
    assert not any(level == "info" for level, _ in host._status.msgs)


def test_save_to_file_write_error_shows_error(tmp_path: Path, monkeypatch) -> None:
    host = _Host()
    host._indicator_manager.add(_cfg(9))
    monkeypatch.setattr(_fd, "asksaveasfilename", lambda *a, **k: str(tmp_path / "x.json"))
    monkeypatch.setattr(preset_store, "export_preset_to_file", lambda *a, **k: False)
    errors = {"n": 0}
    monkeypatch.setattr(_mb, "showerror", lambda *a, **k: errors.__setitem__("n", 1))
    host._on_menu_save_indicator_preset_to_file()
    assert errors["n"] == 1


# ---------------------------------------------------------------------------
# Load Preset from File…
# ---------------------------------------------------------------------------


def test_load_from_file_replaces_active_set(tmp_path: Path, monkeypatch) -> None:
    host = _Host()
    host._indicator_manager.add(_cfg(200))  # pre-existing — must be replaced
    src = tmp_path / "in.json"
    preset_store.export_preset_to_file(src, [_cfg(9).to_dict(), _cfg(21).to_dict()])
    monkeypatch.setattr(_fd, "askopenfilename", lambda *a, **k: str(src))
    host._on_menu_load_indicator_preset_from_file()
    configs = host._indicator_manager.to_dict()["active_configs"]
    lengths = sorted(c["params"]["length"] for c in configs)
    assert lengths == [9, 21]  # 200 replaced


def test_load_from_file_bad_shows_error_keeps_state(tmp_path: Path, monkeypatch) -> None:
    host = _Host()
    host._indicator_manager.add(_cfg(50))
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(_fd, "askopenfilename", lambda *a, **k: str(bad))
    errors = {"n": 0}
    monkeypatch.setattr(_mb, "showerror", lambda *a, **k: errors.__setitem__("n", errors["n"] + 1))
    host._on_menu_load_indicator_preset_from_file()
    assert errors["n"] == 1
    # State untouched on a failed load.
    assert len(host._indicator_manager.to_dict()["active_configs"]) == 1


def test_load_from_file_cancelled_is_noop(tmp_path: Path, monkeypatch) -> None:
    host = _Host()
    host._indicator_manager.add(_cfg(50))
    monkeypatch.setattr(_fd, "askopenfilename", lambda *a, **k: "")
    host._on_menu_load_indicator_preset_from_file()
    assert len(host._indicator_manager.to_dict()["active_configs"]) == 1
