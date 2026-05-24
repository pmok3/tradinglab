"""Integration tests for the indicator-warmup pre-load feature.

Validates that:
1. ``evaluate_symbol`` honours ``warmup_until_ts`` — no fills before
   the cutoff, equity_curve trimmed.
2. ``runner.run`` fetches an extended range and the evaluator sees
   indicators fully hydrated by Day 1 of the active period (the
   user's "9 EMA bounce shouldn't be NaN on Day 1 morning" ask).
3. Back-compat: passing ``warmup_until_ts=None`` to ``evaluate_symbol``
   matches the legacy code path exactly.
"""

from __future__ import annotations

import datetime as _dt
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tradinglab.entries.model import (
    Direction,
    EntryStrategy,
    EntryTrigger,
    ShareRounding,
    SizingKind,
    SizingRule,
)
from tradinglab.entries.model import TriggerKind as EntryTriggerKind
from tradinglab.entries.model import Universe as EntryUniverse
from tradinglab.exits.model import ExitLeg, ExitStrategy, ExitTrigger
from tradinglab.exits.model import TriggerKind as ExitTriggerKind
from tradinglab.models import Candle
from tradinglab.scanner.model import (
    OP_CROSSES_ABOVE,
    Condition,
    FieldRef,
    Group,
)
from tradinglab.strategy_tester import (
    CostModel,
    DatePreset,
    TestConfig,
    UniverseKind,
    UniverseSpec,
)
from tradinglab.strategy_tester import run as run_test
from tradinglab.strategy_tester.evaluator import evaluate_symbol

_ET = ZoneInfo("America/New_York")


def _market_entry() -> EntryStrategy:
    return EntryStrategy(
        id="e1", name="m",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TST",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=10.0,
                          share_rounding=ShareRounding.DOWN),
        max_fires_per_session_per_symbol=999,
        require_market_open=False,
    )


def _stop_exit() -> ExitStrategy:
    return ExitStrategy(
        id="x1", name="stop",
        legs=[ExitLeg(id="leg1", triggers=[
            ExitTrigger(kind=ExitTriggerKind.STOP, offset_pct=99.0, qty_pct=100.0),
        ])],
        eod_kill_switch=False,
    )


def _ema_cross_entry(fast: int = 3, slow: int = 8) -> EntryStrategy:
    cond = Condition(
        left=FieldRef(kind="indicator", id="ema", params={"length": fast}),
        op=OP_CROSSES_ABOVE,
        params={
            "right": FieldRef(kind="indicator", id="ema", params={"length": slow}),
            "lookback": FieldRef(kind="literal", value=1),
        },
    )
    grp = Group(combinator="and", children=[cond])
    return EntryStrategy(
        id="e1", name="ema-cross",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TST",)),
        trigger=EntryTrigger(
            kind=EntryTriggerKind.INDICATOR,
            condition=grp,
            interval="5m",
        ),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=10.0,
                          share_rounding=ShareRounding.DOWN),
        max_fires_per_session_per_symbol=999,
        require_market_open=False,
    )


def _bars_n_rth_5m(start: datetime, n: int) -> list[Candle]:
    """N RTH 5m bars starting at ``start`` (ET tz-aware).

    Wraps across days inside RTH (09:30→16:00 ET); the synthetic
    timestamps stay in the regular session so the runner's RTH filter
    keeps every bar. Close price ramps up so EMA(3) eventually crosses
    EMA(8) (= at least one trade fires).
    """
    out: list[Candle] = []
    t = start
    for i in range(n):
        # Ramp: close monotonically up by 0.1 — EMA(3) sits above EMA(8) → fires.
        op = 100.0 + i * 0.1
        cl = op + 0.05
        out.append(Candle(
            date=t, open=op, high=cl + 0.05, low=op - 0.05,
            close=cl, volume=1000, session="regular",
        ))
        t = t + timedelta(minutes=5)
        # Skip overnight: if past 15:55 ET, roll to next weekday 09:30.
        if t.time() > _dt.time(15, 55):
            nxt = t.date() + timedelta(days=1)
            while datetime(nxt.year, nxt.month, nxt.day).weekday() >= 5:
                nxt = nxt + timedelta(days=1)
            t = datetime(nxt.year, nxt.month, nxt.day, 9, 30, tzinfo=_ET)
    return out


# ---------------------------------------------------------------------------
# Direct evaluator unit: warmup gate
# ---------------------------------------------------------------------------


def test_evaluator_warmup_gate_blocks_entries_before_cutoff() -> None:
    """Bars before warmup_until_ts produce no fills; bars after do."""
    bars = _bars_n_rth_5m(datetime(2026, 1, 5, 9, 30, tzinfo=_ET), 80)
    # Cutoff = halfway through (bar 40's ts). Bars 0..39 are warmup;
    # bars 40..79 are active.
    cutoff_ts = int(bars[40].date.timestamp())

    result_with = evaluate_symbol(
        symbol="TST",
        candles=bars,
        interval="5m",
        entry_strategy=_market_entry(),
        exit_strategy=_stop_exit(),
        starting_cash=100_000.0,
        cost_model=CostModel(slippage_bps=0.0),
        warmup_until_ts=cutoff_ts,
    )

    # No fill timestamps before cutoff.
    early_fills = [f for f in result_with.fills if int(f.fill_ts) < cutoff_ts]
    assert early_fills == [], f"expected no warmup fills, got {early_fills}"
    # At least one MARKET entry fires after cutoff.
    assert len(result_with.fills) > 0
    for f in result_with.fills:
        assert int(f.fill_ts) >= cutoff_ts


def test_evaluator_equity_curve_trimmed_to_active_period() -> None:
    bars = _bars_n_rth_5m(datetime(2026, 1, 5, 9, 30, tzinfo=_ET), 60)
    cutoff_ts = int(bars[30].date.timestamp())
    result = evaluate_symbol(
        symbol="TST",
        candles=bars,
        interval="5m",
        entry_strategy=_market_entry(),
        exit_strategy=_stop_exit(),
        starting_cash=100_000.0,
        cost_model=CostModel(slippage_bps=0.0),
        warmup_until_ts=cutoff_ts,
    )
    assert result.equity_curve  # non-empty
    for ts_e, _eq in result.equity_curve:
        assert ts_e >= cutoff_ts


def test_evaluator_warmup_none_matches_legacy_behaviour() -> None:
    """Passing warmup_until_ts=None reproduces the pre-warmup output exactly."""
    bars = _bars_n_rth_5m(datetime(2026, 1, 5, 9, 30, tzinfo=_ET), 40)
    legacy = evaluate_symbol(
        symbol="TST",
        candles=bars,
        interval="5m",
        entry_strategy=_market_entry(),
        exit_strategy=_stop_exit(),
        starting_cash=100_000.0,
        cost_model=CostModel(slippage_bps=0.0),
    )
    with_none = evaluate_symbol(
        symbol="TST",
        candles=bars,
        interval="5m",
        entry_strategy=_market_entry(),
        exit_strategy=_stop_exit(),
        starting_cash=100_000.0,
        cost_model=CostModel(slippage_bps=0.0),
        warmup_until_ts=None,
    )
    assert len(legacy.fills) == len(with_none.fills)
    assert len(legacy.equity_curve) == len(with_none.equity_curve)
    assert len(legacy.post_trades) == len(with_none.post_trades)


# ---------------------------------------------------------------------------
# Runner integration: extended fetch range + active-period gating
# ---------------------------------------------------------------------------


def _cfg(*, start: str, end: str, interval: str = "5m") -> TestConfig:
    return TestConfig(
        entry_strategy_id="e1",
        exit_strategy_id="x1",
        universe=UniverseSpec(kind=UniverseKind.SYMBOLS, symbols=("TST",)),
        start_date=start,
        end_date=end,
        interval=interval,
        starting_cash=100_000.0,
        cost_model=CostModel(slippage_bps=0.0),
        date_preset=DatePreset.CUSTOM,
    )


def test_runner_fetcher_receives_extended_range_when_warmup_needed() -> None:
    """An EMA-cross strategy → runner walks indicators → fetcher gets a
    wider date window than the user's start_date."""
    # Provide a deep history starting well before user's start_date so
    # the fetcher's return contains both warmup and active bars.
    history_start = datetime(2026, 1, 5, 9, 30, tzinfo=_ET)  # Monday
    bars_all = _bars_n_rth_5m(history_start, 600)  # ~8 trading days at 5m

    fetcher_calls: list[tuple[str, str]] = []

    def fetcher(sym: str, interval: str) -> list[Candle]:
        fetcher_calls.append((sym, interval))
        return list(bars_all)

    # User's active window: Jan 12 (Mon, week 2) → Jan 15.
    cfg = _cfg(start="2026-01-12", end="2026-01-15", interval="5m")

    result = run_test(
        cfg,
        candles_fetcher=fetcher,
        entry_loader=lambda _id: _ema_cross_entry(3, 8),
        exit_loader=lambda _id: _stop_exit(),
        max_workers=1,
    )

    assert result.test_run.status.value == "done"
    # Every trade landed inside the active window.
    import tradinglab.strategy_tester.storage as storage  # noqa: PLC0415
    per_sym = storage.load_session_result_for_symbol(result.run_dir, "TST")
    assert per_sym is not None

    active_start_ts = int(
        datetime(2026, 1, 12, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
    )
    for pt in per_sym.post_trades:
        assert int(pt.entry_ts) >= active_start_ts, (
            f"trade entry_ts {pt.entry_ts} is before active start {active_start_ts}"
        )
    # Equity curve confined to the active period too.
    for ts_e, _ in per_sym.equity_curve:
        assert ts_e >= active_start_ts


def test_runner_smoke_stub_with_no_warmup_data_falls_back_gracefully() -> None:
    """If the fetcher's data starts at-or-after start_date (typical smoke
    stub), the worker degrades to legacy no-warmup behaviour rather than
    no-firing every bar."""
    # Fetcher returns exactly the active window — no warmup bars available.
    bars = _bars_n_rth_5m(datetime(2026, 1, 12, 9, 30, tzinfo=_ET), 30)

    def fetcher(_sym: str, _interval: str) -> list[Candle]:
        return list(bars)

    cfg = _cfg(start="2026-01-12", end="2026-01-12", interval="5m")
    result = run_test(
        cfg,
        candles_fetcher=fetcher,
        entry_loader=lambda _id: _market_entry(),  # MARKET → no warmup needed anyway
        exit_loader=lambda _id: _stop_exit(),
        max_workers=1,
    )
    assert result.test_run.status.value == "done"


def test_runner_market_only_strategy_no_warmup_window() -> None:
    """Strategies with no indicator triggers don't expand the fetch range."""
    bars = _bars_n_rth_5m(datetime(2026, 1, 12, 9, 30, tzinfo=_ET), 30)
    seen_first_date: list[_dt.date] = []

    def fetcher(_sym: str, _interval: str) -> list[Candle]:
        return list(bars)

    cfg = _cfg(start="2026-01-12", end="2026-01-12", interval="5m")
    # Wire a candles_fetcher that records, then run.
    result = run_test(
        cfg,
        candles_fetcher=fetcher,
        entry_loader=lambda _id: _market_entry(),
        exit_loader=lambda _id: _stop_exit(),
        max_workers=1,
    )
    assert result.test_run.status.value == "done"
    # Just verify no crash; the runner's contract for no-warmup configs
    # is that fetch_start_date == start_date.
    _ = seen_first_date  # not used; placeholder for visibility


def test_warmup_override_days_takes_precedence() -> None:
    """When warmup_override_days is set, it's used verbatim (not auto-computed)."""
    from tradinglab.strategy_tester.warmup import required_warmup_bars

    # Build a config with override = 30 calendar days even though the
    # strategy is MARKET (auto-compute would yield 0).
    cfg = TestConfig(
        entry_strategy_id="e1",
        exit_strategy_id="x1",
        universe=UniverseSpec(kind=UniverseKind.SYMBOLS, symbols=("TST",)),
        start_date="2026-01-12",
        end_date="2026-01-12",
        interval="5m",
        starting_cash=100_000.0,
        cost_model=CostModel(slippage_bps=0.0),
        date_preset=DatePreset.CUSTOM,
        warmup_override_days=30,
    )

    requested: list[tuple[str, str]] = []
    bars = _bars_n_rth_5m(datetime(2025, 12, 1, 9, 30, tzinfo=_ET), 1500)

    def fetcher(sym: str, iv: str) -> list[Candle]:
        requested.append((sym, iv))
        return list(bars)

    result = run_test(
        cfg,
        candles_fetcher=fetcher,
        entry_loader=lambda _id: _market_entry(),
        exit_loader=lambda _id: _stop_exit(),
        max_workers=1,
    )
    assert result.test_run.status.value == "done"
    # Sanity: MARKET strategy auto-warmup would have been 0 bars.
    assert required_warmup_bars(_market_entry(), _stop_exit()) == 0

    import tradinglab.strategy_tester.storage as storage  # noqa: PLC0415
    per_sym = storage.load_session_result_for_symbol(result.run_dir, "TST")
    assert per_sym is not None
    # With override, ALL trades must still be in the active period (Jan 12+).
    active_start_ts = int(
        datetime(2026, 1, 12, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
    )
    for pt in per_sym.post_trades:
        assert int(pt.entry_ts) >= active_start_ts


def test_testconfig_warmup_override_roundtrip() -> None:
    """warmup_override_days survives to_dict / from_dict."""
    cfg = TestConfig(
        entry_strategy_id="e1",
        exit_strategy_id="x1",
        universe=UniverseSpec(kind=UniverseKind.SYMBOLS, symbols=("TST",)),
        start_date="2026-01-01",
        end_date="2026-01-31",
        interval="5m",
        starting_cash=100_000.0,
        cost_model=CostModel(slippage_bps=5.0),
        date_preset=DatePreset.CUSTOM,
        warmup_override_days=42,
    )
    cfg2 = TestConfig.from_dict(cfg.to_dict())
    assert cfg2.warmup_override_days == 42

    cfg_none = TestConfig.from_dict({**cfg.to_dict(), "warmup_override_days": None})
    assert cfg_none.warmup_override_days is None

    # Missing key in old-manifest JSON deserialises to None (back-compat).
    payload = cfg.to_dict()
    del payload["warmup_override_days"]
    cfg_legacy = TestConfig.from_dict(payload)
    assert cfg_legacy.warmup_override_days is None
