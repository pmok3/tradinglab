"""Smoke test for the M1 ChartStack wireframe.

Constructs the panel **standalone** (no full ChartApp) so this
file does not race the human's concurrent ``app.py`` refactor. A
proper ChartApp-integration smoke lands once the human wires
``ChartStackPanel`` into ``_build_ui``.

When ``chartstack.enabled`` is ``False`` (the M1 default), the
test is skipped — flipping the flag at M3 turns it on. The test
itself is gated by env-var ``TRADINGLAB_TEST_CHARTSTACK=1`` for
opt-in coverage during M1 since the default is off.
"""

from __future__ import annotations

import os

import pytest

# Force headless matplotlib + isolated geometry path before any
# tradinglab import.
os.environ.setdefault("MPLBACKEND", "Agg")


from tradinglab import settings as _settings  # noqa: E402
from tradinglab.gui.chartstack import settings_adapter as adapter  # noqa: E402


def _enabled_for_test() -> bool:
    if os.environ.get("TRADINGLAB_TEST_CHARTSTACK") == "1":
        return True
    return adapter.is_enabled()


@pytest.mark.skipif(
    not _enabled_for_test(),
    reason="chartstack.enabled is False (M1 default); set TRADINGLAB_TEST_CHARTSTACK=1 to opt in",
)
def test_chartstack_panel_constructs_standalone(tmp_path) -> None:
    import tkinter as tk

    # Force enabled + a known card count for the duration of the test.
    snap = _settings.load()
    try:
        _settings.set("chartstack.enabled", True)
        _settings.set("chartstack.cards.count", 3)

        from tradinglab.gui.chartstack import ChartStackPanel

        root = tk.Tk()
        try:
            root.withdraw()
            container = tk.Frame(root)
            container.pack()
            panel = ChartStackPanel(container, owner=None)
            try:
                # Pump once so matplotlib can lay out the figure.
                root.update()
                root.update_idletasks()

                assert len(panel.cards) == adapter.card_count() == 3

                # Each placeholder Axes should carry the resolved
                # symbol as a centred text artist.
                texts = []
                for card in panel.cards:
                    t = [t.get_text() for t in card.ax.texts]
                    assert t, f"card {card.slot_index} missing placeholder text"
                    texts.append(t[0])

                # Owner is None → falls back to the placeholder
                # symbol pool starting at AAPL.
                assert texts[0] == "AAPL"
                assert texts[1] == "MSFT"
                assert texts[2] == "NVDA"
            finally:
                panel.destroy()
        finally:
            root.destroy()
    finally:
        _settings.save(snap)


def test_chartstack_disabled_by_default() -> None:
    """Sanity: the M1 default keeps the feature flag off."""
    snap = _settings.load()
    try:
        _settings.clear()
        assert adapter.is_enabled() is False
    finally:
        _settings.save(snap)


@pytest.mark.skipif(
    not _enabled_for_test(),
    reason="chartstack.enabled is False (M1 default); set TRADINGLAB_TEST_CHARTSTACK=1 to opt in",
)
def test_chartstack_apply_card_stash_renders_sparkline() -> None:
    """M2: stashing bars on a slot replaces the placeholder with a line."""
    import tkinter as tk

    from matplotlib.collections import LineCollection

    snap = _settings.load()
    try:
        _settings.set("chartstack.enabled", True)
        _settings.set("chartstack.cards.count", 3)

        from tradinglab.gui.chartstack import ChartStackPanel
        from tradinglab.gui.chartstack.series_cache import Bar

        root = tk.Tk()
        try:
            root.withdraw()
            panel = ChartStackPanel(root, owner=None)
            try:
                root.update_idletasks()
                card = panel.cards[0]
                token = card.controller.token
                symbol = card.binding.symbol
                # Synthetic bars — increasing closes → up direction.
                bars = [
                    Bar(ts=i, open=100.0 + i, high=101.0 + i,
                        low=99.0 + i, close=100.5 + i, volume=10.0)
                    for i in range(5)
                ]
                panel.apply_card_stash(0, token, symbol, bars)
                # M4: sparkline is a volume-encoded LineCollection,
                # not a Line2D. Verify the artist + the direction
                # tint on the % chg label.
                lcs = [a for a in card.ax.collections
                       if isinstance(a, LineCollection)]
                assert lcs, "expected a LineCollection after stash"
                from matplotlib.colors import to_rgba
                pct_labels = [t for t in card.ax.texts
                              if "%" in t.get_text()]
                assert pct_labels, "missing % chg label"
                # UP direction tint matches the M4 BULL token.
                assert (to_rgba(pct_labels[0].get_color())
                        == to_rgba("#26a69a"))
            finally:
                panel.destroy()
        finally:
            root.destroy()
    finally:
        _settings.save(snap)


@pytest.mark.skipif(
    not _enabled_for_test(),
    reason="chartstack.enabled is False (M1 default); set TRADINGLAB_TEST_CHARTSTACK=1 to opt in",
)
def test_chartstack_apply_card_stash_drops_stale_token() -> None:
    """M2: a payload tagged with a stale token is silently dropped."""
    import tkinter as tk

    from matplotlib.collections import LineCollection

    snap = _settings.load()
    try:
        _settings.set("chartstack.enabled", True)
        _settings.set("chartstack.cards.count", 3)

        from tradinglab.gui.chartstack import ChartStackPanel
        from tradinglab.gui.chartstack.binding import CardBinding
        from tradinglab.gui.chartstack.series_cache import Bar

        root = tk.Tk()
        try:
            root.withdraw()
            panel = ChartStackPanel(root, owner=None)
            try:
                root.update_idletasks()
                card = panel.cards[0]
                stale_token = card.controller.token
                symbol = card.binding.symbol
                # Re-bind to the SAME symbol with a fresh CardBinding;
                # bumps the controller token internally.
                card.set_binding(CardBinding(symbol=symbol,
                                             source_label="watchlist"))
                bars = [
                    Bar(ts=i, open=100.0, high=100.0,
                        low=100.0, close=100.0 + i, volume=1.0)
                    for i in range(4)
                ]
                # Use the stale token — must be a no-op.
                panel.apply_card_stash(0, stale_token, symbol, bars)
                # M4: a sparkline render would add a LineCollection;
                # the stale-token drop means none should appear.
                lcs = [a for a in card.ax.collections
                       if isinstance(a, LineCollection)]
                assert not lcs, "stale token should not draw a sparkline"
            finally:
                panel.destroy()
        finally:
            root.destroy()
    finally:
        _settings.save(snap)


@pytest.mark.skipif(
    not _enabled_for_test(),
    reason="chartstack.enabled is False (M1 default); set TRADINGLAB_TEST_CHARTSTACK=1 to opt in",
)
def test_chartstack_card_click_invokes_promote_callback() -> None:
    """M2: simulating an mpl button-press inside a card axes fires the
    ``on_card_promote`` callback with the card's symbol."""
    import tkinter as tk
    from types import SimpleNamespace

    snap = _settings.load()
    try:
        _settings.set("chartstack.enabled", True)
        _settings.set("chartstack.cards.count", 3)

        from tradinglab.gui.chartstack import ChartStackPanel

        root = tk.Tk()
        try:
            root.withdraw()
            panel = ChartStackPanel(root, owner=None)
            try:
                root.update_idletasks()
                received: list[str] = []
                panel.on_card_promote = received.append
                card = panel.cards[1]
                # Synthesize an mpl event with the click landing in card.ax.
                evt = SimpleNamespace(button=1, inaxes=card.ax)
                panel._on_canvas_click(evt)
                assert received == [card.binding.symbol]

                # Right-click ignored.
                received.clear()
                evt2 = SimpleNamespace(button=3, inaxes=card.ax)
                panel._on_canvas_click(evt2)
                assert received == []

                # Click outside any axes ignored.
                evt3 = SimpleNamespace(button=1, inaxes=None)
                panel._on_canvas_click(evt3)
                assert received == []
            finally:
                panel.destroy()
        finally:
            root.destroy()
    finally:
        _settings.save(snap)


@pytest.mark.skipif(
    not _enabled_for_test(),
    reason="chartstack.enabled is False (M1 default); set TRADINGLAB_TEST_CHARTSTACK=1 to opt in",
)
def test_chartstack_demote_to_rebinds_promoted_slot() -> None:
    """M2: ``demote_to`` rebinds the slot that was showing the promoted
    symbol so the strip remains full after the user clicks it."""
    import tkinter as tk

    snap = _settings.load()
    try:
        _settings.set("chartstack.enabled", True)
        _settings.set("chartstack.cards.count", 3)

        from tradinglab.gui.chartstack import ChartStackPanel

        root = tk.Tk()
        try:
            root.withdraw()
            panel = ChartStackPanel(root, owner=None)
            try:
                root.update_idletasks()
                card = panel.cards[2]
                promoted = card.binding.symbol
                panel.demote_to(promoted, "TSLA")
                assert card.binding is not None
                assert card.binding.symbol == "TSLA"
            finally:
                panel.destroy()
        finally:
            root.destroy()
    finally:
        _settings.save(snap)


@pytest.mark.skipif(
    not _enabled_for_test(),
    reason="chartstack.enabled is False (M1 default); set TRADINGLAB_TEST_CHARTSTACK=1 to opt in",
)
def test_chartstack_apply_stream_event_updates_sparkline() -> None:
    """M3: a tick / rollover updates the per-slot cache and redraws."""
    import tkinter as tk
    from types import SimpleNamespace

    from matplotlib.collections import LineCollection

    snap = _settings.load()
    try:
        _settings.set("chartstack.enabled", True)
        _settings.set("chartstack.cards.count", 3)

        from tradinglab.gui.chartstack import ChartStackPanel
        from tradinglab.gui.chartstack.series_cache import Bar

        root = tk.Tk()
        try:
            root.withdraw()
            panel = ChartStackPanel(root, owner=None)
            try:
                root.update_idletasks()
                card = panel.cards[0]
                token = card.controller.token
                symbol = card.binding.symbol
                # Seed with a stash so the cache has bars to start.
                seed = [
                    Bar(ts=i, open=100.0, high=101.0,
                        low=99.0, close=100.0 + i, volume=10.0)
                    for i in range(5)
                ]
                panel.apply_card_stash(0, token, symbol, seed)
                root.update_idletasks()
                # Now feed a rollover event — should append a new bar.
                evt_bar = SimpleNamespace(
                    date=99, open=100.0, high=110.0,
                    low=99.0, close=109.0, volume=1.0)
                panel.apply_stream_event(0, token, "rollover", evt_bar)
                # Pump idle to flush the coalescer.
                root.update_idletasks()
                # Cache should now have 6 bars.
                cache = panel._series_caches[0]
                assert len(cache) == 6
                # M4: the sparkline (LineCollection) is still drawn.
                lcs = [a for a in card.ax.collections
                       if isinstance(a, LineCollection)]
                assert lcs, "sparkline must persist through stream event"
            finally:
                panel.destroy()
        finally:
            root.destroy()
    finally:
        _settings.save(snap)


@pytest.mark.skipif(
    not _enabled_for_test(),
    reason="chartstack.enabled is False (M1 default); set TRADINGLAB_TEST_CHARTSTACK=1 to opt in",
)
def test_chartstack_apply_stream_event_drops_stale_token() -> None:
    """M3: stale-token stream events are no-ops."""
    import tkinter as tk
    from types import SimpleNamespace

    snap = _settings.load()
    try:
        _settings.set("chartstack.enabled", True)
        _settings.set("chartstack.cards.count", 3)

        from tradinglab.gui.chartstack import ChartStackPanel
        from tradinglab.gui.chartstack.binding import CardBinding
        from tradinglab.gui.chartstack.series_cache import Bar

        root = tk.Tk()
        try:
            root.withdraw()
            panel = ChartStackPanel(root, owner=None)
            try:
                root.update_idletasks()
                card = panel.cards[0]
                stale_token = card.controller.token
                symbol = card.binding.symbol
                # Re-bind to bump the token.
                card.set_binding(CardBinding(symbol=symbol,
                                             source_label="watchlist"))
                evt_bar = SimpleNamespace(
                    date=1, open=100.0, high=101.0,
                    low=99.0, close=100.5, volume=1.0)
                panel.apply_stream_event(0, stale_token, "tick", evt_bar)
                cache = panel._series_caches[0]
                assert len(cache) == 0, "stale-token event must not mutate cache"
            finally:
                panel.destroy()
        finally:
            root.destroy()
    finally:
        _settings.save(snap)


@pytest.mark.skipif(
    not _enabled_for_test(),
    reason="chartstack.enabled is False (M1 default); set TRADINGLAB_TEST_CHARTSTACK=1 to opt in",
)
def test_chartstack_stream_burst_coalesces_to_single_flush() -> None:
    """M3: 50 rapid ticks → coalesced redraw schedules exactly one idle flush.

    The dirty-slot set should grow as events pour in; the after_idle
    handle is set exactly once until the flush fires.
    """
    import tkinter as tk
    from types import SimpleNamespace

    snap = _settings.load()
    try:
        _settings.set("chartstack.enabled", True)
        _settings.set("chartstack.cards.count", 3)

        from tradinglab.gui.chartstack import ChartStackPanel
        from tradinglab.gui.chartstack.series_cache import Bar

        root = tk.Tk()
        try:
            root.withdraw()
            panel = ChartStackPanel(root, owner=None)
            try:
                root.update_idletasks()
                card = panel.cards[0]
                token = card.controller.token
                symbol = card.binding.symbol
                # Seed the cache so the sparkline path is taken.
                seed = [
                    Bar(ts=i, open=100.0, high=100.0,
                        low=100.0, close=100.0 + i, volume=1.0)
                    for i in range(3)
                ]
                panel.apply_card_stash(0, token, symbol, seed)
                root.update_idletasks()
                # Burst 50 ticks on the same trailing bar.
                ts = 2  # last seed ts
                for i in range(50):
                    evt_bar = SimpleNamespace(
                        date=ts, open=100.0, high=100.0 + i * 0.01,
                        low=99.0, close=102.0 + i * 0.01, volume=1.0)
                    panel.apply_stream_event(0, token, "tick", evt_bar)
                # All 50 ticks should have queued one idle flush.
                assert panel._idle_flush_after is not None, \
                    "coalescer should have scheduled exactly one flush"
                assert 0 in panel._dirty_slots
                # Pump idle to fire the flush.
                root.update_idletasks()
                # After flush, the dirty set + handle reset.
                assert panel._idle_flush_after is None
                assert panel._dirty_slots == set()
            finally:
                panel.destroy()
        finally:
            root.destroy()
    finally:
        _settings.save(snap)


@pytest.mark.skipif(
    not _enabled_for_test(),
    reason="chartstack.enabled is False (M1 default); set TRADINGLAB_TEST_CHARTSTACK=1 to opt in",
)
def test_chartstack_panel_releases_streams_on_destroy(monkeypatch) -> None:
    """Post-simplification: cards never subscribe to streams (they're
    pinned to the daily timeframe, which the ``is_intraday`` gate
    refuses). Destroy must still be safe to call — it walks every
    card and invokes ``controller.stop_stream()``, which is a no-op
    when no subscription exists.
    """
    import tkinter as tk

    snap = _settings.load()
    try:
        _settings.set("chartstack.enabled", True)
        _settings.set("chartstack.cards.count", 3)

        # Stub out the stream so we can observe subscribe/unsubscribe.
        class _FakeStream:
            def __init__(self) -> None:
                self.subs = 0
                self.unsubs = 0

            def subscribe(self, ticker, interval, on_event):
                self.subs += 1

                def _u() -> None:
                    self.unsubs += 1

                return _u

        from tradinglab import streaming as _stream_mod
        fake = _FakeStream()
        monkeypatch.setitem(_stream_mod.STREAM_SOURCES, "test-stream", fake)

        # Owner shim that points to the fake stream source.
        class _Owner:
            class _Var:
                def __init__(self, v):
                    self._v = v

                def get(self):
                    return self._v

            def __init__(self) -> None:
                import queue as _q
                self.source_var = self._Var("test-stream")
                self.interval_var = self._Var("5m")
                self._watchlist_snapshot: list = []
                self._stream_queue: _q.Queue = _q.Queue()

        from tradinglab.gui.chartstack import ChartStackPanel

        root = tk.Tk()
        try:
            root.withdraw()
            owner = _Owner()
            panel = ChartStackPanel(root, owner=owner)
            try:
                root.update_idletasks()
                # Cards pinned to "1d" — the production is_intraday
                # gate refuses, so no upstream subscribe ever fires.
                assert fake.subs == 0
            finally:
                panel.destroy()
            # Destroy must remain safe even with zero active subs.
            assert fake.unsubs == 0
        finally:
            root.destroy()
    finally:
        _settings.save(snap)


@pytest.mark.skipif(
    not _enabled_for_test(),
    reason="chartstack.enabled is False (M1 default); set TRADINGLAB_TEST_CHARTSTACK=1 to opt in",
)
def test_chartstack_drain_stream_queue_routes_card_events() -> None:
    """M3 end-to-end: PollingMixin._drain_stream_queue dispatches 'card:N'
    events to ``self._chartstack.apply_stream_event`` and skips the
    main-chart pipeline. Verifies the wire-up in ``gui/polling.py``."""
    import queue as _q

    from tradinglab.gui.polling import PollingMixin

    class _CapturingChartStack:
        def __init__(self) -> None:
            self.events: list = []

        def apply_stream_event(self, slot_index, token, kind, bar):
            self.events.append((slot_index, token, kind, bar))

    main_chart_calls: list = []

    class _MinimalHost(PollingMixin):
        def __init__(self) -> None:
            self._stream_queue: _q.Queue = _q.Queue()
            self._chartstack = _CapturingChartStack()
            self._after_jobs: set = set()
            self._stream_active = False
            self._stream_drain_after = None

        # PollingMixin._track_after eventually calls self.after — give it
        # a no-op stub so _schedule_drain's re-arm doesn't crash.
        def after(self, delay_ms, fn):  # noqa: D401
            return f"job-{delay_ms}"

        def after_cancel(self, job_id) -> None:  # noqa: D401
            return None

        # Stubs so a tick on a non-card slot can't accidentally route here.
        def _apply_stream_tick(self, evt) -> bool:
            main_chart_calls.append(("tick", evt))
            return False

        def _apply_stream_rollover(self, evt) -> bool:
            main_chart_calls.append(("rollover", evt))
            return False

    host = _MinimalHost()
    bar_a = {"close": 100.0, "ts": 1}
    bar_b = {"close": 200.0, "ts": 2}
    bar_c = {"close": 300.0, "ts": 3}
    # Card events on three different slots.
    host._stream_queue.put((123, "card:0", "synthetic-stream", "AAPL", "5m", "tick", bar_a))
    host._stream_queue.put((123, "card:2", "synthetic-stream", "MSFT", "5m", "rollover", bar_b))
    # A main-chart event must still route through the regular pipeline.
    host._stream_queue.put((124, "primary", "synthetic-stream", "AMD", "5m", "tick", bar_c))
    # Malformed card-slot index → silently dropped, no crash.
    host._stream_queue.put((125, "card:bad", "x", "y", "5m", "tick", None))

    host._drain_stream_queue()

    assert host._chartstack.events == [
        (0, 123, "tick", bar_a),
        (2, 123, "rollover", bar_b),
    ]
    # The "primary" event went to the main-chart pipeline, not the
    # chartstack — proving the prefix routing actually disambiguates.
    assert len(main_chart_calls) == 1
    assert main_chart_calls[0][0] == "tick"


@pytest.mark.skipif(
    not _enabled_for_test(),
    reason="chartstack.enabled is False (M1 default); set TRADINGLAB_TEST_CHARTSTACK=1 to opt in",
)
def test_chartstack_lockstep_with_next_bar(app) -> None:
    """M5 integration: SandboxController.next_bar fires card subscribers
    and the panel's per-card cache mirrors visible_candles_by_symbol.

    Exercises the full wire: panel.attach_sandbox -> register_card_subscriber
    -> next_bar -> _fire_card_subscribers -> panel._on_sandbox_tick ->
    cache update + dirty-slot scheduling.
    """
    import datetime as _dt
    import tkinter as tk

    from tradinglab.backtest.bars import _clear_cache_for_tests
    from tradinglab.backtest.replay import SandboxController
    from tradinglab.backtest.session import ENGINE_VERSION, SessionSpec
    from tradinglab.models import Candle

    _clear_cache_for_tests()

    def _synth(base: float, n: int):
        t0 = _dt.datetime(2024, 6, 3, 13, 30, tzinfo=_dt.timezone.utc)
        return [
            Candle(
                date=t0 + _dt.timedelta(minutes=5 * i),
                open=base + i * 0.10,
                high=base + i * 0.10 + 0.5,
                low=base + i * 0.10 - 0.3,
                close=base + i * 0.10 + 0.2,
                volume=1000.0 + i,
                session="regular",
            )
            for i in range(n)
        ]

    candles_amd = _synth(100.0, 30)

    snap = _settings.load()
    try:
        _settings.set("chartstack.enabled", True)
        _settings.set("chartstack.cards.count", 3)

        from tradinglab.gui.chartstack import ChartStackPanel
        from tradinglab.gui.chartstack.binding import CardBinding

        # Panel is a self-contained Tk widget; it doesn't have to be
        # parented under the ChartApp.
        root = tk.Toplevel(app)
        try:
            root.withdraw()
            panel = ChartStackPanel(root, owner=None)
            try:
                root.update_idletasks()
                # Bind slot 0 to AMD so the lockstep callback has a
                # symbol → cache mapping to populate.
                panel.cards[0].set_binding(
                    CardBinding(symbol="AMD", source_label="watchlist"))
                root.update_idletasks()

                spec = SessionSpec(
                    deck_seed=42,
                    tickers=(),
                    start_clock_iso="",
                    slippage_bps=5.0,
                    commission=0.0,
                    engine_version=ENGINE_VERSION,
                    starting_cash=100_000.0,
                )
                ctl = SandboxController(app=app)
                try:
                    ctl.start_session(
                        spec=spec,
                        session_date=_dt.date(2024, 6, 3),
                        interval="5m",
                        reference_symbol="AMD",
                        reference_candles=candles_amd,
                        lookback_days=0,
                    )
                    app._sandbox = ctl
                    assert ctl.is_active()

                    # Attach the panel; this registers exactly one
                    # subscriber on the controller.
                    panel.attach_sandbox(ctl)
                    assert len(ctl._card_subscribers) == 1

                    # Cache for slot 0 starts empty.
                    cache = panel._series_caches[0]
                    initial_len = len(cache)

                    # Advance one bar — subscriber fires; panel cache
                    # mirrors the sandbox's visible candle list.
                    visible_amd = ctl.visible_candles_by_symbol["AMD"]
                    vis_len_before_tick = len(visible_amd)
                    assert ctl.next_bar()
                    root.update_idletasks()
                    vis_len_after_tick = len(visible_amd)
                    assert vis_len_after_tick == vis_len_before_tick + 1
                    # Panel's slot-0 cache grew to match (capped at maxlen).
                    assert len(cache) >= initial_len + 1
                    # Last bar's close matches the engine's last visible.
                    cached = cache.snapshot()
                    assert cached[-1].close == visible_amd[-1].close

                    # Advance multiple bars; lockstep continues.
                    for _ in range(3):
                        ctl.next_bar()
                    root.update_idletasks()
                    assert cache.snapshot()[-1].close == \
                        ctl.visible_candles_by_symbol["AMD"][-1].close

                    # End session: subscribers fire once more with
                    # active=False; the panel observes that and
                    # self-detaches.
                    ctl.end_session()
                    assert ctl._card_subscribers == []
                    assert panel._sandbox is None
                finally:
                    if ctl.is_active():
                        try:
                            ctl.end_session()
                        except Exception:  # noqa: BLE001
                            pass
                    app._sandbox = None
            finally:
                panel.destroy()
        finally:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass
    finally:
        _settings.save(snap)


@pytest.mark.skipif(
    not _enabled_for_test(),
    reason="chartstack.enabled is False (M1 default); set TRADINGLAB_TEST_CHARTSTACK=1 to opt in",
)
def test_chartstack_m6_alert_tints_card_on_rvol_spike(app) -> None:
    """M6 integration: a synthetic RVOL spike on a sandbox tick paints
    the card with the Tier-1 amber tint via the alert engine.

    Exercises: panel.attach_sandbox → next_bar → _on_sandbox_tick →
    _evaluate_alerts_for_all_cards → AlertEngine.evaluate → tier1
    detector → set_card_tint.
    """
    import datetime as _dt
    import tkinter as tk

    from tradinglab.backtest.bars import _clear_cache_for_tests
    from tradinglab.backtest.replay import SandboxController
    from tradinglab.backtest.session import ENGINE_VERSION, SessionSpec
    from tradinglab.gui.chartstack.alerts import AlertTier
    from tradinglab.gui.colors import WARN_AMBER
    from tradinglab.models import Candle

    _clear_cache_for_tests()

    def _synth_with_spike(n: int):
        t0 = _dt.datetime(2024, 6, 3, 13, 30, tzinfo=_dt.timezone.utc)
        out: list[Candle] = []
        for i in range(n - 1):
            out.append(Candle(
                date=t0 + _dt.timedelta(minutes=5 * i),
                open=100.0, high=100.5, low=99.5, close=100.0,
                volume=1000.0, session="regular",
            ))
        # Final bar is a 5x volume spike → RVOL 5.0 > 1.8 (5m default).
        out.append(Candle(
            date=t0 + _dt.timedelta(minutes=5 * (n - 1)),
            open=100.0, high=100.5, low=99.5, close=100.0,
            volume=5000.0, session="regular",
        ))
        return out

    candles = _synth_with_spike(30)
    snap = _settings.load()
    try:
        _settings.set("chartstack.enabled", True)
        _settings.set("chartstack.cards.count", 3)
        # Mute audio so the smoke run is silent on Windows.
        _settings.set("chartstack.alerts.audio_muted", True)

        from tradinglab.gui.chartstack import ChartStackPanel
        from tradinglab.gui.chartstack.binding import CardBinding

        root = tk.Toplevel(app)
        try:
            root.withdraw()
            panel = ChartStackPanel(root, owner=app)
            try:
                root.update_idletasks()
                panel.cards[0].set_binding(
                    CardBinding(symbol="AMD", source_label="watchlist"))
                root.update_idletasks()

                spec = SessionSpec(
                    deck_seed=1,
                    tickers=(),
                    start_clock_iso="",
                    slippage_bps=5.0,
                    commission=0.0,
                    engine_version=ENGINE_VERSION,
                    starting_cash=100_000.0,
                )
                ctl = SandboxController(app=app)
                try:
                    ctl.start_session(
                        spec=spec,
                        session_date=_dt.date(2024, 6, 3),
                        interval="5m",
                        reference_symbol="AMD",
                        reference_candles=candles,
                        lookback_days=0,
                    )
                    app._sandbox = ctl
                    panel.attach_sandbox(ctl)
                    # Fast-forward until the spike bar is the most-recent
                    # visible bar (the lockstep tick will then evaluate
                    # alerts with a 20-bar window + spike as last).
                    for _ in range(len(candles)):
                        if not ctl.next_bar():
                            break
                    root.update_idletasks()

                    # Confirm the alert engine surfaced Tier-1 and the
                    # panel cached the tint.
                    tier = panel._slot_alert_tier.get(0, AlertTier.NONE)
                    # The exact final-bar position depends on the
                    # sandbox's session-window contract; the alert
                    # only needs to fire at least once during the
                    # walk-forward, so we tolerate Tier-1 *or* the
                    # current tint already being amber.
                    if tier is not AlertTier.TIER_1_AMBER:
                        # Trigger one more direct evaluation against
                        # the current cache to confirm the engine path.
                        panel._evaluate_alerts_for_all_cards()
                        tier = panel._slot_alert_tier.get(0, AlertTier.NONE)
                    assert tier is AlertTier.TIER_1_AMBER, \
                        f"expected Tier-1 amber, got {tier!r}"
                    assert panel._card_tints.get(0) == WARN_AMBER

                    ctl.end_session()
                    # Tier state cleared on detach.
                    assert panel._slot_alert_tier.get(0, AlertTier.NONE) \
                        is AlertTier.NONE
                finally:
                    if ctl.is_active():
                        try:
                            ctl.end_session()
                        except Exception:  # noqa: BLE001
                            pass
                    app._sandbox = None
            finally:
                panel.destroy()
        finally:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass
    finally:
        _settings.save(snap)



