"""Sandbox start gates on the replay data being downloaded.

Sandbox replay is deliberately offline: the market feed registers symbols
from the disk cache and never fetches. A symbol that was never downloaded
is therefore blank in the watchlist and invisible to every scan for the
whole session — silently, one empty column at a time.

`_confirm_sandbox_data_ready` surfaces that up front and links to
`Sandbox → Download Replay Data…`. It must run **before** the controller
exists, because that dialog refuses to open while a session is active —
so a prompt raised mid-session could not actually link anywhere.

See ``gui/sandbox_menu.spec.md``.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import pytest

from tradinglab.gui.sandbox_menu import SandboxMenuMixin
from tradinglab.models import Candle


def _candles(n: int = 4) -> list[Candle]:
    out = []
    for i in range(n):
        d = _dt.datetime(2026, 6, 10, 14, 30, tzinfo=_dt.timezone.utc) \
            + _dt.timedelta(minutes=5 * i)
        out.append(Candle(date=d, open=10.0, high=11.0, low=9.0,
                          close=10.5, volume=100))
    return out


class _Status:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, m):
        self.messages.append(m)

    warn = info
    error = info


class _App(SandboxMenuMixin):
    def __init__(self, pinned: list[str]) -> None:
        self._status = _Status()
        self._pinned = list(pinned)
        self.prepare_opened = 0

    def _pinned_ticker_union(self):
        return list(self._pinned)

    def _on_menu_sandbox_prepare_universe(self) -> None:
        self.prepare_opened += 1


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr("tradinglab.disk_cache._cache_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "tradinglab.data.auto_source.resolve_auto_source",
        lambda **k: "yfinance")
    from tradinglab import disk_cache
    return disk_cache.save


@pytest.fixture()
def answer(monkeypatch):
    """Drive ``messagebox.askyesnocancel`` and record the prompt."""
    seen: dict[str, Any] = {"calls": 0, "prompt": ""}

    def _install(value):
        def _ask(title, message, **kw):  # noqa: ARG001
            seen["calls"] += 1
            seen["prompt"] = message
            return value
        monkeypatch.setattr(
            "tradinglab.gui.sandbox_menu.messagebox.askyesnocancel", _ask)
        return seen

    return _install


def _gate(app: _App, **kw):
    kw.setdefault("source", "yfinance")
    kw.setdefault("interval", "5m")
    return app._confirm_sandbox_data_ready(**kw)


# ---------------------------------------------------------------------------
# No prompt when there is nothing to warn about
# ---------------------------------------------------------------------------


def test_fully_cached_universe_starts_without_a_prompt(cache, answer):
    seen = answer(None)
    cache("yfinance", "AMD", "5m", _candles())
    cache("yfinance", "NVDA", "5m", _candles())
    app = _App(["AMD", "NVDA"])
    assert _gate(app, universe_symbols=("NVDA",)) is True
    assert seen["calls"] == 0


def test_empty_universe_starts_without_a_prompt(cache, answer):
    seen = answer(None)
    assert _gate(_App([])) is True
    assert seen["calls"] == 0


def test_auto_cached_symbols_count_as_downloaded(cache, answer):
    """Charted on Auto, replayed on the vendor Auto resolves to."""
    seen = answer(None)
    cache("Auto", "GLD", "5m", _candles())
    app = _App(["GLD"])
    assert _gate(app) is True
    assert seen["calls"] == 0


def test_unresolvable_source_does_not_block(cache, answer):
    seen = answer(None)
    app = _App(["AMD"])
    assert _gate(app, source="") is True
    assert seen["calls"] == 0


# ---------------------------------------------------------------------------
# The three answers
# ---------------------------------------------------------------------------


def test_yes_opens_the_downloader_and_aborts_the_start(cache, answer):
    answer(True)
    app = _App(["AMD", "GHOST"])
    assert _gate(app) is False
    assert app.prepare_opened == 1


def test_no_starts_anyway_with_a_warning(cache, answer):
    answer(False)
    app = _App(["GHOST"])
    assert _gate(app) is True
    assert app.prepare_opened == 0
    assert any("stay empty" in m for m in app._status.messages)


def test_cancel_aborts_without_opening_the_downloader(cache, answer):
    answer(None)
    app = _App(["GHOST"])
    assert _gate(app) is False
    assert app.prepare_opened == 0


def test_downloader_failure_still_aborts_cleanly(cache, answer):
    answer(True)
    app = _App(["GHOST"])

    def _boom():
        raise RuntimeError("dialog exploded")

    app._on_menu_sandbox_prepare_universe = _boom
    assert _gate(app) is False
    assert any("Could not open" in m for m in app._status.messages)


# ---------------------------------------------------------------------------
# What the prompt says
# ---------------------------------------------------------------------------


def test_prompt_names_the_missing_symbols_and_the_source(cache, answer):
    seen = answer(False)
    cache("yfinance", "AMD", "5m", _candles())
    app = _App(["AMD", "GHOST1", "GHOST2"])
    _gate(app)
    prompt = seen["prompt"]
    assert "2 of 3 symbols" in prompt
    assert "GHOST1" in prompt and "GHOST2" in prompt
    assert "AMD" not in prompt.split("Sandbox replay")[0]
    assert "yfinance" in prompt and "5m" in prompt
    assert "Download Replay Data" in prompt


def test_prompt_truncates_a_long_missing_list(cache, answer):
    seen = answer(False)
    app = _App([f"GHOST{i:02d}" for i in range(20)])
    _gate(app)
    assert "+12 more" in seen["prompt"]


def test_universe_symbols_are_checked_too(cache, answer):
    seen = answer(False)
    cache("yfinance", "AMD", "5m", _candles())
    app = _App(["AMD"])
    _gate(app, universe_symbols=("TSLA", "MSFT"))
    assert "2 of 3 symbols" in seen["prompt"]


def test_symbols_are_deduped_across_watchlist_and_universe(cache, answer):
    seen = answer(False)
    app = _App(["GHOST"])
    _gate(app, universe_symbols=("ghost", "GHOST"))
    assert "1 of 1 symbols" in seen["prompt"]


# ---------------------------------------------------------------------------
# Headless safety
# ---------------------------------------------------------------------------


def test_headless_tcl_error_never_blocks_the_session(cache, monkeypatch):
    import tkinter as tk

    def _raise(*a, **k):
        raise tk.TclError("no window manager")

    monkeypatch.setattr(
        "tradinglab.gui.sandbox_menu.messagebox.askyesnocancel", _raise)
    app = _App(["GHOST"])
    assert _gate(app) is True
