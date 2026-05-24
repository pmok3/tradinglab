"""Unit tests for strategy_tester.evaluator."""

from __future__ import annotations

from datetime import datetime, timedelta

from tradinglab.entries.model import (
    Direction,
    EntryStrategy,
    EntryTrigger,
    ShareRounding,
    SizingKind,
    SizingRule,
)
from tradinglab.entries.model import (
    TriggerKind as EntryTriggerKind,
)
from tradinglab.entries.model import (
    Universe as EntryUniverse,
)
from tradinglab.exits.model import (
    ExitLeg,
    ExitStrategy,
    ExitTrigger,
)
from tradinglab.exits.model import (
    TriggerKind as ExitTriggerKind,
)
from tradinglab.models import Candle
from tradinglab.strategy_tester import (
    CostModel,
    UnsupportedTriggerKind,
    evaluate_symbol,
)


def _ramp_candles(n: int = 30, start: float = 100.0, step: float = 1.0) -> list[Candle]:
    """Linear ramp — close grows by ``step`` per bar so every trigger touches predictably."""
    out: list[Candle] = []
    t = datetime(2026, 1, 1, 9, 30)
    price = start
    for i in range(n):
        op = price
        cl = price + step
        hi = max(op, cl) + 0.2
        lo = min(op, cl) - 0.2
        out.append(Candle(date=t, open=op, high=hi, low=lo, close=cl,
                          volume=1000 + i, session="regular"))
        price = cl
        t = t + timedelta(minutes=5)
    return out


def _market_long_strategy() -> EntryStrategy:
    return EntryStrategy(
        id="entry-test",
        name="Market Long",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TEST",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY, qty=5.0,
            share_rounding=ShareRounding.DOWN,
        ),
        max_fires_per_session_per_symbol=1,
    )


def _stop_5pct_exit() -> ExitStrategy:
    return ExitStrategy(
        id="exit-test",
        name="5% stop",
        legs=[
            ExitLeg(
                id="leg-stop",
                triggers=[
                    ExitTrigger(
                        kind=ExitTriggerKind.STOP,
                        offset_pct=5.0,
                        qty_pct=100.0,
                    ),
                ],
            ),
        ],
        eod_kill_switch=False,
    )


def test_market_entry_fires_on_first_eligible_bar() -> None:
    entry = _market_long_strategy()
    exit_strat = _stop_5pct_exit()
    candles = _ramp_candles(n=30)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(slippage_bps=0.0, commission_per_trade=0.0),
    )
    # At least one fill (the entry).
    assert len(result.fills) >= 1
    assert result.fills[0].side.value == "buy"
    assert result.fills[0].quantity == 5.0


def test_eod_kill_switch_flattens_at_end() -> None:
    entry = _market_long_strategy()
    exit_strat = _stop_5pct_exit()
    # Force kill-switch on
    exit_strat.eod_kill_switch = True
    candles = _ramp_candles(n=30)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(slippage_bps=0.0, commission_per_trade=0.0),
    )
    # Net position should be zero after the EOD sweep.
    pos = sum(
        (f.quantity if f.side.value == "buy" else -f.quantity)
        for f in result.fills
    )
    assert pos == 0.0


def test_empty_candles_returns_empty_session() -> None:
    entry = _market_long_strategy()
    exit_strat = _stop_5pct_exit()
    result = evaluate_symbol(
        symbol="TEST",
        candles=[],
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    assert result.fills == []
    assert result.equity_curve == []
    assert result.spec.tickers == ("TEST",)


def test_unsupported_entry_kind_raises() -> None:
    """The defensive ``_entry_unsupported`` fallback still raises
    :class:`UnsupportedTriggerKind` for a TriggerKind not in the
    handler registry. We simulate this by temporarily removing a kind
    from the registry — guards against future kinds added to the
    enum without a matching handler.
    """
    from tradinglab.strategy_tester import evaluator as st_eval

    entry = _market_long_strategy()
    exit_strat = _stop_5pct_exit()
    candles = _ramp_candles(n=5)

    saved_handler = st_eval._ENTRY_HANDLERS.pop(EntryTriggerKind.MARKET)
    try:
        try:
            evaluate_symbol(
                symbol="TEST",
                candles=candles,
                interval="5m",
                entry_strategy=entry,
                exit_strategy=exit_strat,
                starting_cash=100_000.0,
                cost_model=CostModel(),
            )
            raise AssertionError("expected UnsupportedTriggerKind")
        except UnsupportedTriggerKind as exc:
            assert exc.side == "entry"
    finally:
        st_eval._ENTRY_HANDLERS[EntryTriggerKind.MARKET] = saved_handler


def test_max_fires_per_symbol_one_only_one_entry() -> None:
    entry = _market_long_strategy()
    entry.max_fires_per_session_per_symbol = 1
    exit_strat = _stop_5pct_exit()
    exit_strat.eod_kill_switch = False
    candles = _ramp_candles(n=30)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    # Count BUY fills — should be exactly 1.
    buys = [f for f in result.fills if f.side.value == "buy"]
    assert len(buys) == 1


# ---------------------------------------------------------------------------
# INDICATOR trigger support (PR-1 / strategy_tester evaluator follow-up)
# ---------------------------------------------------------------------------


def _close_gt_threshold(threshold: float, *, interval: str = "5m") -> object:
    """Build a one-leaf scanner.Group: ``close > <threshold>``."""
    from tradinglab.scanner.model import (
        OP_GT,
        Condition,
        FieldRef,
        Group,
    )

    return Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef(kind="builtin", id="close"),
                op=OP_GT,
                params={
                    "right": FieldRef(kind="literal", value=float(threshold)),
                },
                interval=interval,
            ),
        ],
    )


def _indicator_long_strategy(condition: object) -> EntryStrategy:
    return EntryStrategy(
        id="entry-ind",
        name="Indicator Long",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TEST",)),
        trigger=EntryTrigger(
            kind=EntryTriggerKind.INDICATOR,
            condition=condition,  # type: ignore[arg-type]
            interval="5m",
        ),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY, qty=5.0,
            share_rounding=ShareRounding.DOWN,
        ),
        max_fires_per_session_per_symbol=1,
    )


def test_indicator_entry_fires_when_condition_becomes_true() -> None:
    """Ramp from 100 stepping +1; 'close > 105' fires once close crosses 105."""
    condition = _close_gt_threshold(105.0)
    entry = _indicator_long_strategy(condition)
    exit_strat = _stop_5pct_exit()
    exit_strat.eod_kill_switch = False
    candles = _ramp_candles(n=30)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    buys = [f for f in result.fills if f.side.value == "buy"]
    assert len(buys) == 1, f"expected exactly 1 BUY fill, got {len(buys)}"
    # Decision at bar close → fill at next bar's open. The fill price must
    # be at or above the bar where the condition first became true.
    # (Threshold=105; ramp closes pass 105 around bar 4 → fill on bar 5+.)
    assert buys[0].fill_price >= 105.0


def test_indicator_entry_fires_when_condition_authored_at_different_interval() -> None:
    """Regression: 0-trade bug reported on real $SPY runs.

    User authored an entry strategy in the GUI; the scanner.Condition
    default ``interval="5m"`` was preserved on save. They then ran
    the strategy tester at the default ``"1d"`` interval. Without
    interval normalization, the cross-interval gate in
    scanner.engine.evaluate_condition silently returns ``None`` (no
    BarsRegistry is wired in the headless path) -> zero fires across
    the entire universe.

    The fix normalizes all per-Condition / per-FieldRef intervals in
    the strategy's condition tree to the test's outer interval at
    evaluate_symbol time. This test exercises that path: the
    condition is created with interval="5m" but the test runs at
    interval="1d", and the strategy must still produce a fill.
    """
    # Condition's interval is "5m" but we'll evaluate at "1d".
    condition = _close_gt_threshold(105.0, interval="5m")
    entry = _indicator_long_strategy(condition)
    exit_strat = _stop_5pct_exit()
    exit_strat.eod_kill_switch = False
    candles = _ramp_candles(n=30)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="1d",   # different from the condition's authored interval
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    buys = [f for f in result.fills if f.side.value == "buy"]
    assert len(buys) == 1, (
        f"expected exactly 1 BUY fill after interval normalization, "
        f"got {len(buys)} -- this is the 0-trade regression"
    )


def test_indicator_entry_no_fire_when_condition_never_true() -> None:
    """Threshold above any close → no fills, no errors."""
    condition = _close_gt_threshold(99999.0)
    entry = _indicator_long_strategy(condition)
    exit_strat = _stop_5pct_exit()
    exit_strat.eod_kill_switch = False
    candles = _ramp_candles(n=30)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    buys = [f for f in result.fills if f.side.value == "buy"]
    assert buys == []


def test_indicator_entry_with_none_condition_silently_no_fire() -> None:
    """Trigger with kind=INDICATOR but condition=None should not error and not fire."""
    entry = _indicator_long_strategy(condition=None)  # type: ignore[arg-type]
    exit_strat = _stop_5pct_exit()
    candles = _ramp_candles(n=10)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    # No fires, no fills.
    assert [f for f in result.fills if f.side.value == "buy"] == []


def test_indicator_exit_fires_when_condition_true() -> None:
    """Open a position via MARKET entry, then exit via INDICATOR
    ``close > 110`` — ramp from 100 stepping +1 will trigger that around bar 10.
    """
    from tradinglab.exits.model import ExitLeg, ExitStrategy, ExitTrigger
    from tradinglab.exits.model import TriggerKind as ExitTriggerKind  # noqa: F811

    entry = _market_long_strategy()
    entry.max_fires_per_session_per_symbol = 1
    condition = _close_gt_threshold(110.0)
    exit_strat = ExitStrategy(
        id="exit-ind",
        name="Indicator Exit",
        legs=[
            ExitLeg(
                id="leg-ind",
                triggers=[
                    ExitTrigger(
                        kind=ExitTriggerKind.INDICATOR,
                        condition=condition,  # type: ignore[arg-type]
                        interval="5m",
                        qty_pct=100.0,
                    ),
                ],
            ),
        ],
        eod_kill_switch=False,
    )
    candles = _ramp_candles(n=30)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    buys = [f for f in result.fills if f.side.value == "buy"]
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert len(buys) == 1
    assert len(sells) == 1, f"expected exactly 1 SELL fill from INDICATOR exit, got {len(sells)}"


# ---------------------------------------------------------------------------
# TRAILING_STOP / TIME_OF_DAY / CHANDELIER / SCANNER_ALERT — wiring tests
# (see strategy_tester.evaluator — these reuse exits.spec pure functions.)
# ---------------------------------------------------------------------------


def _explicit_close_candles(closes: list[float], *, start: datetime | None = None) -> list[Candle]:
    """Build candles with explicit per-bar close prices.

    Bar i's open is the prior bar's close (Bar 0 opens at its own close).
    High/low are set ±0.5 around the open/close envelope so trailing-stop
    intrabar updates have wider extremes than the close.
    """
    out: list[Candle] = []
    t = start or datetime(2026, 1, 1, 9, 30)
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i > 0 else c
        op = float(prev)
        cl = float(c)
        hi = max(op, cl) + 0.5
        lo = min(op, cl) - 0.5
        out.append(
            Candle(date=t, open=op, high=hi, low=lo, close=cl,
                   volume=1000 + i, session="regular"),
        )
        t = t + timedelta(minutes=5)
    return out


def _trailing_5pct_exit() -> ExitStrategy:
    from tradinglab.exits.model import TrailUnit
    return ExitStrategy(
        id="exit-trail",
        name="5% trail",
        legs=[
            ExitLeg(
                id="leg-trail",
                triggers=[
                    ExitTrigger(
                        kind=ExitTriggerKind.TRAILING_STOP,
                        trail_unit=TrailUnit.PERCENT,
                        trail_value=5.0,
                        qty_pct=100.0,
                    ),
                ],
            ),
        ],
        eod_kill_switch=False,
    )


def test_trailing_stop_percent_long_fires_on_retrace() -> None:
    """MARKET long entry. Trailing 5% percent stop. Ramp up then drop hard."""
    entry = _market_long_strategy()
    exit_strat = _trailing_5pct_exit()
    # Bar 0: 100, 1: 102 (entry fills here at open=100), then ramp to 110, then crash.
    closes = [100, 102, 104, 106, 108, 110, 108, 104, 100, 95]
    candles = _explicit_close_candles(closes)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    buys = [f for f in result.fills if f.side.value == "buy"]
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert len(buys) == 1, f"expected 1 BUY entry fill, got {len(buys)}"
    assert len(sells) == 1, (
        f"expected exactly 1 SELL trailing-stop exit, got {len(sells)}: "
        f"all fills={[(f.side.value, f.price) for f in result.fills]}"
    )


def test_trailing_stop_no_fire_during_uptrend() -> None:
    """Steady ramp up — trailing 5% never touches; no exit fill."""
    entry = _market_long_strategy()
    exit_strat = _trailing_5pct_exit()
    candles = _ramp_candles(n=20)  # steady +1/bar ramp

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert sells == [], (
        f"expected no trailing-stop fire on monotone uptrend; got sells={sells}"
    )


def test_trailing_stop_dollar_unit() -> None:
    """Trailing $2 dollar stop fires on a $2 retrace below HWM."""
    from tradinglab.exits.model import TrailUnit
    entry = _market_long_strategy()
    exit_strat = ExitStrategy(
        id="exit-trail-dollar",
        name="$2 trail",
        legs=[
            ExitLeg(
                id="leg-trail-dollar",
                triggers=[
                    ExitTrigger(
                        kind=ExitTriggerKind.TRAILING_STOP,
                        trail_unit=TrailUnit.DOLLAR,
                        trail_value=2.0,
                        qty_pct=100.0,
                    ),
                ],
            ),
        ],
        eod_kill_switch=False,
    )
    # Peak HWM around 110.5; trail = 108.5. Drop close to 107 hits it.
    closes = [100, 102, 104, 106, 108, 110, 108, 107, 100]
    candles = _explicit_close_candles(closes)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert len(sells) == 1, f"expected 1 $-trail exit fill, got {len(sells)}"


def _time_of_day_exit(cutoff: str | None) -> ExitStrategy:
    return ExitStrategy(
        id="exit-tod",
        name="time-of-day exit",
        legs=[
            ExitLeg(
                id="leg-tod",
                triggers=[
                    ExitTrigger(
                        kind=ExitTriggerKind.TIME_OF_DAY,
                        time_of_day=cutoff,
                        qty_pct=100.0,
                    ),
                ],
            ),
        ],
        eod_kill_switch=False,
    )


def test_time_of_day_fires_at_or_after_cutoff() -> None:
    """Cutoff 09:55. Bars at 5m starting 09:30. Position opens at bar 1 (09:35).
    First bar at/after 09:55 is bar 5 (09:55 exactly).
    """
    entry = _market_long_strategy()
    exit_strat = _time_of_day_exit("09:55")
    candles = _ramp_candles(n=15)  # 09:30, 09:35, ..., 10:40

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert len(sells) == 1, f"expected 1 TIME_OF_DAY sell, got {len(sells)}"


def test_time_of_day_no_fire_before_cutoff() -> None:
    """Cutoff 23:55. Bars never get there; no exit fill."""
    entry = _market_long_strategy()
    exit_strat = _time_of_day_exit("23:55")
    candles = _ramp_candles(n=10)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert sells == [], (
        f"expected no TIME_OF_DAY fire before cutoff; got {sells}"
    )


def test_time_of_day_malformed_no_fire() -> None:
    """time_of_day=None on a TIME_OF_DAY trigger — silently no-fire (no crash)."""
    entry = _market_long_strategy()
    exit_strat = _time_of_day_exit(None)
    candles = _ramp_candles(n=10)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    # entry still fires; exit must NOT fire
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert sells == []


def _chandelier_exit(*, atr_period: int = 3, lookback: int = 10, multiplier: float = 2.0) -> ExitStrategy:
    return ExitStrategy(
        id="exit-chand",
        name="chandelier exit",
        legs=[
            ExitLeg(
                id="leg-chand",
                triggers=[
                    ExitTrigger(
                        kind=ExitTriggerKind.CHANDELIER,
                        chandelier_lookback=lookback,
                        chandelier_atr_period=atr_period,
                        chandelier_multiplier=multiplier,
                        chandelier_ma_type="SMA",
                        qty_pct=100.0,
                    ),
                ],
            ),
        ],
        eod_kill_switch=False,
    )


def test_chandelier_long_fires_after_atr_warmup() -> None:
    """3-period ATR. Position opens, holds enough bars to warm up ATR,
    then a deep retrace touches the chandelier stop."""
    entry = _market_long_strategy()
    exit_strat = _chandelier_exit(atr_period=3, lookback=10, multiplier=2.0)
    # 15 bars: rise from 100 to 120, then crash to 90.
    # ATR period=3 means stop computed by ~bar 4 (relative to entry@bar1).
    closes = [100, 105, 110, 115, 118, 120, 120, 120, 120, 110, 100, 90, 85, 80, 75]
    candles = _explicit_close_candles(closes)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    buys = [f for f in result.fills if f.side.value == "buy"]
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert len(buys) == 1
    assert len(sells) == 1, (
        f"expected chandelier to fire on retrace; fills={[(f.side.value, f.price) for f in result.fills]}"
    )


def test_chandelier_does_not_fire_during_warmup() -> None:
    """Even a big drop in the first 2 bars (before ATR warms up) must not fire."""
    entry = _market_long_strategy()
    exit_strat = _chandelier_exit(atr_period=10, lookback=10, multiplier=2.0)
    # Position opens at bar 1; ATR period 10 means no stop until ~bar 11.
    # Sharp drop at bar 3 — must NOT fire (still warming up).
    closes = [100, 102, 50, 50, 50, 50, 50]
    candles = _explicit_close_candles(closes)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert sells == [], (
        f"chandelier fired during ATR warmup (bad); fills={[(f.side.value, f.price) for f in result.fills]}"
    )


# ---------------------------------------------------------------------------
# SCANNER_ALERT entry — load Scan from storage and evaluate per bar with
# edge-trigger semantics (False/None → True transition fires).
# ---------------------------------------------------------------------------


def _save_scan_with_condition(scan_id: str, condition_threshold: float, monkeypatch, tmp_path) -> str:
    """Persist a 1-condition Scan to a tmp scans dir; return its id."""
    from tradinglab.scanner import storage as scanner_storage
    from tradinglab.scanner.model import (
        OP_GT,
        Condition,
        FieldRef,
        Group,
        ScanDefinition,
    )

    monkeypatch.setattr(scanner_storage, "scans_dir", lambda: tmp_path)
    scan = ScanDefinition(
        id=scan_id,
        name="test-scan",
        root=Group(
            combinator="and",
            children=[
                Condition(
                    left=FieldRef(kind="builtin", id="close"),
                    op=OP_GT,
                    params={
                        "right": FieldRef(kind="literal", value=float(condition_threshold)),
                    },
                    interval="5m",
                ),
            ],
        ),
        primary_interval="5m",
    )
    scanner_storage.save(scan)
    return scan.id


def _scanner_alert_long_strategy(scanner_id: str) -> EntryStrategy:
    return EntryStrategy(
        id="entry-scan",
        name="Scanner-alert Long",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TEST",)),
        trigger=EntryTrigger(
            kind=EntryTriggerKind.SCANNER_ALERT,
            scanner_id=scanner_id,
        ),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY, qty=5.0,
            share_rounding=ShareRounding.DOWN,
        ),
        max_fires_per_session_per_symbol=1,
    )


def test_scanner_alert_entry_fires_on_new_match(monkeypatch, tmp_path) -> None:
    """A SCANNER_ALERT entry whose scan condition transitions False→True fires."""
    scan_id = _save_scan_with_condition("test-scan-fires", 105.0, monkeypatch, tmp_path)
    entry = _scanner_alert_long_strategy(scan_id)
    exit_strat = _stop_5pct_exit()
    exit_strat.eod_kill_switch = False
    candles = _ramp_candles(n=30)  # close > 105 around bar 5

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    buys = [f for f in result.fills if f.side.value == "buy"]
    assert len(buys) == 1, (
        f"expected SCANNER_ALERT to fire on close>105 transition; got buys={buys}"
    )


def test_scanner_alert_entry_no_fire_when_already_matching(monkeypatch, tmp_path) -> None:
    """If the scan condition is True from bar 0 (no transition), no fire."""
    scan_id = _save_scan_with_condition("test-scan-no-trans", 50.0, monkeypatch, tmp_path)
    entry = _scanner_alert_long_strategy(scan_id)
    exit_strat = _stop_5pct_exit()
    candles = _ramp_candles(n=10)  # closes start at 101 — always > 50

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    buys = [f for f in result.fills if f.side.value == "buy"]
    assert buys == [], (
        f"expected no fire when scan matches from bar 0 (no transition); got {buys}"
    )


def test_scanner_alert_entry_missing_scanner_no_fire(monkeypatch, tmp_path) -> None:
    """If scanner_id references a non-existent scan, the run completes
    cleanly with zero fires (logged, but no exception)."""
    from tradinglab.scanner import storage as scanner_storage
    monkeypatch.setattr(scanner_storage, "scans_dir", lambda: tmp_path)
    entry = _scanner_alert_long_strategy("does-not-exist-scan-id")
    exit_strat = _stop_5pct_exit()
    candles = _ramp_candles(n=10)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    assert result.fills == [], (
        f"expected silent no-fire when scanner_id missing; got {result.fills}"
    )
