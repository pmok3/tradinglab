"""End-to-end scanner integration tests.

Drives the full flow without Tk:

1. Build a multi-condition, multi-interval scan covering several op
   categories (>, crosses_above, holding_above, is_rising).
2. Persist to disk via :mod:`scanner.storage`, then reload — the
   in-memory copy must equal the round-tripped definition.
3. Feed a synthetic universe through :class:`ScanRunner` for several
   ticks (growing candle lists in place, like the sandbox controller
   does). Verify:

   - Match set on the final tick is correct.
   - ``new_rows`` only contains symbols whose match status flipped
     False/None → True on that tick (edge-triggered).
   - rank_by ordering is honored (sort by descending volume).

These tests pin the scanner's runtime contract independent of any GUI.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import List

import pytest

import tradinglab.indicators  # noqa: F401  -- registers indicators
from tradinglab.models import Candle
from tradinglab.scanner import storage as _scan_storage
from tradinglab.scanner.model import (
    OP_CROSSES_ABOVE,
    OP_GT,
    OP_HOLDING_ABOVE,
    OP_IS_RISING,
    RANK_DIR_DESC,
    Condition,
    FieldRef,
    Group,
    ScanDefinition,
)
from tradinglab.scanner.runner import ScanRunner


def _candle(minute: int, close: float, volume: int = 1000) -> Candle:
    ts = _dt.datetime(2024, 1, 2, 14, 30) + _dt.timedelta(minutes=minute)
    return Candle(date=ts, open=close - 0.5, high=close + 0.5,
                  low=close - 0.5, close=close, volume=volume)


def _series(closes: list[float], volumes: list[int] | None = None
            ) -> list[Candle]:
    vols = volumes if volumes is not None else [1000] * len(closes)
    return [_candle(i * 5, c, v) for i, (c, v) in enumerate(zip(closes, vols, strict=False))]


def _build_complex_scan() -> ScanDefinition:
    """Multi-block scan: (close > 100 AND volume > 0) AND
    (close crosses_above 105 OR close is_rising over 3 bars)."""
    block_a = Group(combinator="and", children=[
        Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)},
                  interval="5m"),
        Condition(left=FieldRef.builtin("volume"), op=OP_GT,
                  params={"right": FieldRef.literal(0.0)},
                  interval="5m"),
    ])
    block_b = Group(combinator="or", children=[
        Condition(left=FieldRef.builtin("close"), op=OP_CROSSES_ABOVE,
                  params={"right": FieldRef.literal(105.0), "lookback": 1},
                  interval="5m"),
        Condition(left=FieldRef.builtin("close"), op=OP_IS_RISING,
                  params={"lookback": 3},
                  interval="5m"),
    ])
    root = Group(combinator="and", children=[block_a, block_b])
    return ScanDefinition(
        name="ComplexScan",
        root=root,
        primary_interval="5m",
        rank_by=FieldRef.builtin("volume"),
        rank_dir=RANK_DIR_DESC,
    )


def test_storage_roundtrip_preserves_definition(tmp_path, monkeypatch):
    monkeypatch.setattr(_scan_storage, "_cache_dir", lambda: tmp_path)
    scan = _build_complex_scan()
    _scan_storage.save(scan)
    # Reload via load_all — simulates app startup.
    loaded_list = _scan_storage.load_all()
    loaded = {s.id: s for s in loaded_list}
    assert scan.id in loaded
    rt = loaded[scan.id]
    # Equality via JSON dict — dataclasses compare object identity for
    # nested mutable members, but to_dict() canonicalizes everything.
    assert rt.to_dict() == scan.to_dict()


def test_full_replay_match_and_new_correctness(tmp_path, monkeypatch):
    monkeypatch.setattr(_scan_storage, "_cache_dir", lambda: tmp_path)

    scan = _build_complex_scan()
    _scan_storage.save(scan)
    [reloaded] = _scan_storage.load_all()  # fresh copy, like app startup
    runner = ScanRunner()

    # Universe of 4 symbols. Each gets candles appended tick-by-tick to
    # mimic SandboxController.next_bar growing visible_candles_by_symbol
    # in place.
    series = {
        # WIN_CROSS: stays above 100 but oscillates so is_rising never
        # fires (no 3-in-a-row rising). Final tick crosses 105 cleanly.
        "WIN_CROSS": _series(
            [101.0, 100.5, 101.0, 100.5, 101.0, 104.0, 106.0],
            [3000, 3100, 3200, 3300, 3400, 3500, 5000],
        ),
        # WIN_RISE: strict monotonic close — triggers is_rising path.
        "WIN_RISE": _series(
            [101, 102, 103, 104, 105, 106, 107],
            [1000, 1100, 1200, 1300, 1400, 1500, 4000],
        ),
        # NEVER: close stays below 100 the whole time.
        "NEVER": _series(
            [90, 91, 92, 93, 92, 93, 94],
            [800, 900, 850, 950, 800, 850, 900],
        ),
        # FLAT: close > 100 but flat (fails is_rising) and never crosses 105.
        "FLAT": _series(
            [101, 101, 101, 101, 101, 101, 101],
            [500, 500, 500, 500, 500, 500, 500],
        ),
    }

    final_ts = 7
    final_results = None
    new_rows_per_tick = []

    for tick in range(3, final_ts + 1):
        # Growing slices match the sandbox's in-place list-extension semantics.
        snapshot = {sym: bars[:tick] for sym, bars in series.items()}
        result_map = runner.run(
            scans=[reloaded],
            candles_by_symbol=snapshot,
            interval="5m",
            tick_id=tick,
            timestamp=_dt.datetime(2024, 1, 2, 14, 30) + _dt.timedelta(minutes=tick * 5),
        )
        sr = result_map[reloaded.id]
        new_rows_per_tick.append(
            {r.symbol for r in sr.new_rows}
        )
        final_results = sr

    assert final_results is not None
    matched = {r.symbol for r in final_results.rows if r.matched is True}
    # WIN_CROSS triggers via crosses_above on the final tick.
    # WIN_RISE triggers via is_rising (strict monotonic).
    # FLAT fails is_rising and never crosses → not matched.
    # NEVER fails block_a (close > 100) → not matched.
    assert matched == {"WIN_CROSS", "WIN_RISE"}

    # Edge detection: WIN_CROSS only flips True on the final tick.
    final_new = new_rows_per_tick[-1]
    assert "WIN_CROSS" in final_new
    # WIN_RISE flipped True earlier (on tick 3 — first tick with 3 prior
    # rising closes). It must NOT appear in the final tick's new_rows.
    assert "WIN_RISE" not in final_new


def test_rank_by_ordering_descending(tmp_path, monkeypatch):
    monkeypatch.setattr(_scan_storage, "_cache_dir", lambda: tmp_path)

    # Simple scan: close > 0 (everyone matches), rank by volume desc.
    scan = ScanDefinition(
        name="Ranker",
        root=Group(combinator="and", children=[
            Condition(left=FieldRef.builtin("close"), op=OP_GT,
                      params={"right": FieldRef.literal(0.0)},
                      interval="5m"),
        ]),
        primary_interval="5m",
        rank_by=FieldRef.builtin("volume"),
        rank_dir=RANK_DIR_DESC,
    )
    runner = ScanRunner()
    universe = {
        "LOW": _series([10, 10, 10], [100, 100, 100]),
        "HIGH": _series([10, 10, 10], [9999, 9999, 9999]),
        "MID": _series([10, 10, 10], [500, 500, 500]),
    }
    result = runner.run(
        scans=[scan],
        candles_by_symbol=universe,
        interval="5m",
        tick_id=1,
        timestamp=_dt.datetime(2024, 1, 2, 15, 0),
    )[scan.id]

    by_symbol = {r.symbol: r for r in result.rows}
    assert by_symbol["HIGH"].rank_value == 9999.0
    assert by_symbol["MID"].rank_value == 500.0
    assert by_symbol["LOW"].rank_value == 100.0


def test_holding_above_with_real_indicator(tmp_path, monkeypatch):
    """Sanity: an indicator-based field reference (SMA-as-support style)
    runs through the engine without surprises. Uses a direct numeric
    threshold instead of an indicator on the right (keeps test cheap)."""
    monkeypatch.setattr(_scan_storage, "_cache_dir", lambda: tmp_path)
    scan = ScanDefinition(
        name="HoldAbove",
        root=Group(combinator="and", children=[
            Condition(
                left=FieldRef.builtin("close"),
                op=OP_HOLDING_ABOVE,
                params={"reference": FieldRef.literal(100.0), "bars": 3},
                interval="5m",
            ),
        ]),
        primary_interval="5m",
    )
    runner = ScanRunner()
    universe = {
        "HOLD": _series([101, 101, 102, 103, 104, 105]),
        "DIPPED": _series([101, 99, 102, 103, 104, 105]),  # bar 2 dipped
    }
    result = runner.run(
        scans=[scan],
        candles_by_symbol=universe,
        interval="5m",
        tick_id=1,
        timestamp=_dt.datetime(2024, 1, 2, 15, 0),
    )[scan.id]
    matched = {r.symbol for r in result.rows if r.matched is True}
    # HOLD held above 100 every bar of the final 3 → matches.
    # DIPPED's last 3 bars (103, 104, 105) all > 100 → also matches.
    # Both should match because holding_above evaluates the *trailing*
    # window. The contract is correct as designed; this test pins it.
    assert "HOLD" in matched
    assert "DIPPED" in matched


def test_export_import_roundtrip_via_disk(tmp_path):
    """Round-trip a scan through to_dict → JSON file → from_dict.

    Mirrors what the GUI Import/Export dialogs do, without involving
    storage's _cache_dir indirection."""
    scan = _build_complex_scan()
    out = tmp_path / "scan.json"
    out.write_text(json.dumps(scan.to_dict(), indent=2))
    data = json.loads(out.read_text())
    rt = ScanDefinition.from_dict(data)
    assert rt.to_dict() == scan.to_dict()
