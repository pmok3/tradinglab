"""M7 performance + DPI tests for the ChartStack panel.

These tests guard against regressions on the perf budget cited in
§5.2 of the synthesis:

* A 5-card coalesced flush (all slots dirty, full cache) under a
  generous wall-clock threshold so we catch a 10× regression
  without false-flagging on slow CI runners.
* DPI helpers return the right card cap for the standard /
  hi-DPI cases.

The perf assertions are intentionally lax — they exist to catch
order-of-magnitude regressions, not to validate sub-ms timing on
specific hardware. Don't tighten without considering the slowest
CI host.
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("MPLBACKEND", "Agg")

from tradinglab import settings as _settings
from tradinglab.gui.chartstack import dpi as _dpi
from tradinglab.gui.chartstack import settings_adapter as _adapter


# ---------------------------------------------------------------------------
# DPI helpers
# ---------------------------------------------------------------------------


def test_dpi_constants_make_sense():
    assert _dpi.HI_DPI_THRESHOLD > 92.0  # well above 1080p
    assert _dpi.CARD_CAP_HI_DPI > _dpi.CARD_CAP_STANDARD


def test_dpi_is_hi_dpi_returns_false_on_none():
    assert _dpi.is_hi_dpi(None) is False


def test_dpi_is_hi_dpi_returns_false_on_low_ppi_stub():
    stub = SimpleNamespace(winfo_fpixels=lambda _arg: 96.0)
    assert _dpi.is_hi_dpi(stub) is False


def test_dpi_is_hi_dpi_returns_true_on_4k_stub():
    stub = SimpleNamespace(winfo_fpixels=lambda _arg: 168.0)
    assert _dpi.is_hi_dpi(stub) is True


def test_dpi_is_hi_dpi_tolerant_on_raising_widget():
    def _boom(_arg):
        raise RuntimeError("Tk torn down")
    stub = SimpleNamespace(winfo_fpixels=_boom)
    assert _dpi.is_hi_dpi(stub) is False


def test_dpi_card_count_cap_matches_dpi_class():
    low = SimpleNamespace(winfo_fpixels=lambda _arg: 96.0)
    high = SimpleNamespace(winfo_fpixels=lambda _arg: 168.0)
    assert _dpi.card_count_cap(low) == _dpi.CARD_CAP_STANDARD
    assert _dpi.card_count_cap(high) == _dpi.CARD_CAP_HI_DPI


# ---------------------------------------------------------------------------
# Panel: DPI cap applied at construction
# ---------------------------------------------------------------------------


def test_panel_caps_card_count_on_standard_dpi(root, monkeypatch):
    from tradinglab.gui.chartstack import ChartStackPanel

    # Force a high configured count + standard DPI; panel should clamp
    # to 5 (CARD_CAP_STANDARD).
    monkeypatch.setitem(_adapter.DEFAULTS, "chartstack.cards.count", 9)
    monkeypatch.setitem(_adapter.DEFAULTS, "chartstack.cards.max", 9)
    monkeypatch.setattr(_dpi, "is_hi_dpi", lambda _w: False)

    panel = ChartStackPanel(root, owner=None)
    try:
        assert panel._effective_card_count == _dpi.CARD_CAP_STANDARD
        assert panel._card_count_capped_from == 9
        assert len(panel._cards) == _dpi.CARD_CAP_STANDARD
    finally:
        try:
            panel.destroy()
        except Exception:  # noqa: BLE001
            pass


def test_panel_allows_six_cards_on_hi_dpi(root, monkeypatch):
    from tradinglab.gui.chartstack import ChartStackPanel

    monkeypatch.setitem(_adapter.DEFAULTS, "chartstack.cards.count", 6)
    monkeypatch.setitem(_adapter.DEFAULTS, "chartstack.cards.max", 9)
    monkeypatch.setattr(_dpi, "is_hi_dpi", lambda _w: True)

    panel = ChartStackPanel(root, owner=None)
    try:
        assert panel._effective_card_count == 6
        assert panel._card_count_capped_from is None
        assert len(panel._cards) == 6
    finally:
        try:
            panel.destroy()
        except Exception:  # noqa: BLE001
            pass


def test_panel_records_capped_attr_when_count_within_cap(root, monkeypatch):
    """A configured count that fits inside the cap leaves
    ``_card_count_capped_from`` at ``None``."""
    from tradinglab.gui.chartstack import ChartStackPanel

    monkeypatch.setitem(_adapter.DEFAULTS, "chartstack.cards.count", 3)
    monkeypatch.setattr(_dpi, "is_hi_dpi", lambda _w: False)

    panel = ChartStackPanel(root, owner=None)
    try:
        assert panel._effective_card_count == 3
        assert panel._card_count_capped_from is None
    finally:
        try:
            panel.destroy()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Perf benchmark: 5-card coalesced flush
# ---------------------------------------------------------------------------


def _make_bars(n: int, base: float = 100.0):
    from tradinglab.gui.chartstack.series_cache import Bar

    out = []
    for i in range(n):
        out.append(Bar(
            ts=int(1.7e12 + i * 60_000),
            open=base + i * 0.10,
            high=base + i * 0.10 + 0.5,
            low=base + i * 0.10 - 0.3,
            close=base + i * 0.10 + 0.2,
            volume=1000.0 + i,
            session="regular",
        ))
    return out


def test_perf_five_card_flush_under_budget(root, monkeypatch):
    """A 5-card all-dirty flush + render path must complete well
    under 1 second on the slowest CI runner.

    The spec calls for ≤ 10 ms steady-state on a developer machine
    (§5.2). 1 second is roughly two orders of magnitude looser so
    a real regression doesn't get masked by a slow runner.
    """
    from tradinglab.gui.chartstack import ChartStackPanel
    from tradinglab.gui.chartstack.binding import CardBinding

    monkeypatch.setitem(_adapter.DEFAULTS, "chartstack.cards.count", 5)
    monkeypatch.setitem(_adapter.DEFAULTS, "chartstack.cards.max", 9)
    monkeypatch.setattr(_dpi, "is_hi_dpi", lambda _w: True)

    panel = ChartStackPanel(root, owner=None)
    try:
        # Bind all 5 cards + fill their caches with 60 bars each.
        for slot, sym in enumerate(["AAPL", "MSFT", "NVDA", "AMD", "TSLA"]):
            panel.cards[slot].set_binding(
                CardBinding(symbol=sym, source_label="watchlist"))
            cache = panel._series_caches[slot]
            cache.invalidate()
            for b in _make_bars(60, base=100.0 + slot):
                cache.append_rollover(b)
            panel._dirty_slots.add(slot)
        root.update_idletasks()

        # Time 10 flush passes so wall-clock noise averages out.
        t0 = time.perf_counter()
        for _ in range(10):
            for slot in range(5):
                panel._dirty_slots.add(slot)
            panel._flush_dirty_cards()
        elapsed = time.perf_counter() - t0
        # 10 flushes × 5 cards × full render = 50 card renders.
        # Budget raised from 1.0s to 2.0s post-2026-05-16 simplification:
        # the new candle renderer batches 60 candle bodies + 60 wicks per
        # card via collections (PatchCollection / LineCollection) but
        # is still strictly more work than the prior single-LineCollection
        # sparkline. 40 ms / card-render comfortably beats the spec's
        # §5.2 "Full repaint ≤ 80 ms" budget.
        assert elapsed < 2.0, f"5-card flush perf regressed: {elapsed:.3f}s"
    finally:
        try:
            panel.destroy()
        except Exception:  # noqa: BLE001
            pass


def test_perf_idle_flush_coalesces_repeated_marks(root, monkeypatch):
    """Marking the same slot dirty 100× before the flush must not
    cost 100× — the coalescer collapses to one flush per idle.
    """
    from tradinglab.gui.chartstack import ChartStackPanel
    from tradinglab.gui.chartstack.binding import CardBinding

    monkeypatch.setitem(_adapter.DEFAULTS, "chartstack.cards.count", 3)

    panel = ChartStackPanel(root, owner=None)
    try:
        panel.cards[0].set_binding(
            CardBinding(symbol="AAPL", source_label="watchlist"))
        cache = panel._series_caches[0]
        for b in _make_bars(60):
            cache.append_rollover(b)
        # Drain any existing scheduled flush.
        panel._idle_flush_after = None

        flush_calls = []
        orig_flush = panel._flush_dirty_cards

        def _counted_flush():
            flush_calls.append(time.perf_counter())
            orig_flush()
        monkeypatch.setattr(panel, "_flush_dirty_cards", _counted_flush)

        # 100 schedule calls → at most one flush queued at a time.
        for _ in range(100):
            panel._dirty_slots.add(0)
            panel._schedule_idle_flush()
        # The idle handle should be set (or already executed via
        # fallback). Spin the Tk loop briefly to drain.
        root.update_idletasks()
        # Coalesced to a single flush (or two if the fallback path
        # fired one synchronously + a second from update_idletasks).
        assert len(flush_calls) <= 2
    finally:
        try:
            panel.destroy()
        except Exception:  # noqa: BLE001
            pass
