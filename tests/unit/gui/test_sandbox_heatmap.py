"""Unit tests for `gui/sandbox_heatmap.py` (pure helpers + Tk/Agg window)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tradinglab.backtest.heatmap import HeatmapTile, scaled_cap
from tradinglab.backtest.heatmap_provider import HeatmapProvider
from tradinglab.data.shares_sources import SharesFact
from tradinglab.gui.sandbox_heatmap import compute_size_pct, tile_at


def _epoch(y, m, d) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


def _shares_fact(as_of: int, shares: float) -> SharesFact:
    return SharesFact(as_of, as_of + 14 * 86400, shares)


def _ps(_sym, _clock):
    return (110.0, 100.0)  # +10% vs prior close


def _provider(tmp_path):
    meta = {
        "AAA": {"sector": "Tech", "industry": "Software", "cik": "1", "date_added_ts": _epoch(2010, 1, 1)},
        "BBB": {"sector": "Financials", "industry": "Banks", "cik": "2", "date_added_ts": _epoch(2010, 1, 1)},
        "NEW": {"sector": "Tech", "industry": "Hardware", "cik": "3", "date_added_ts": _epoch(2023, 1, 1)},
    }
    return HeatmapProvider(
        meta=meta,
        shares_fetcher=lambda s: [_shares_fact(_epoch(2015, 1, 1), 1000.0)],
        splits_fetcher=lambda _s: [],
        cache_dir=tmp_path,
    )


# ---------------------------------------------------------------------------
# pure helpers (no Tk)
# ---------------------------------------------------------------------------


def test_tile_at():
    t = HeatmapTile("AAA", "S", "I", 1.0, False, 0.0, 0.0, 0.5, 0.5)
    tiles = (t,)
    assert tile_at(tiles, 0.25, 0.25) is t
    assert tile_at(tiles, 0.9, 0.9) is None
    assert tile_at(tiles, None, 0.1) is None


def test_compute_size_pct_exact_and_carryback(tmp_path):
    prov = _provider(tmp_path)
    size_by, pct_by, approx = compute_size_pct(prov, _ps, ["AAA", "BBB"], _epoch(2020, 6, 1))
    assert pct_by["AAA"] == pytest.approx(10.0)
    assert size_by["AAA"] == pytest.approx(scaled_cap(1000.0, 110.0))
    assert approx == set()  # 2020 is after the 2015 series start -> exact
    # before the series start -> carry-back -> approx
    _s, _p, approx2 = compute_size_pct(prov, _ps, ["AAA"], _epoch(2010, 1, 1))
    assert "AAA" in approx2


def test_compute_size_pct_peek_is_approx_until_primed(tmp_path):
    prov = _provider(tmp_path)
    # peek never fetches -> uncached symbols are approx with size 0
    size_by, _pct, approx = compute_size_pct(
        prov, _ps, ["AAA"], _epoch(2020, 6, 1), shares_at=prov.peek_shares_at
    )
    assert size_by["AAA"] == 0.0
    assert "AAA" in approx


# ---------------------------------------------------------------------------
# Tk / Agg window
# ---------------------------------------------------------------------------

tk = pytest.importorskip("tkinter")
pytest.importorskip("matplotlib")
import matplotlib  # noqa: E402

matplotlib.use("Agg")

from tradinglab.gui.sandbox_heatmap import SandboxHeatmapWindow  # noqa: E402


class _Ctl:
    def __init__(self, clock, *, blind=False, active=True):
        self._clock = clock
        self.blind = blind
        self._active = active
        self.focus_symbol = "AAA"
        self.engine = SimpleNamespace(clock=SimpleNamespace(index=42))

    def is_active(self):
        return self._active

    def clock_ts(self):
        return self._clock

    def current_session_date(self):
        return "2020-06-01"

    def positions_snapshot(self):
        return [{"symbol": "AAA", "quantity": 100.0, "avg_cost": 10.0}]

    def set_focus(self, sym):
        self.focus_symbol = sym


@pytest.fixture(scope="module")
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no Tk display")
    r.withdraw()
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


def _make_window(root, tmp_path, **ctl_kw):
    prov = _provider(tmp_path)
    prov.prime(["AAA", "BBB"])  # populate cache so peek returns real sizes
    ctl = _Ctl(_epoch(2020, 6, 1), **ctl_kw)
    win = SandboxHeatmapWindow(root, ctl, provider=prov, price_source=_ps)
    root.update()
    return win, ctl


def test_window_renders_filters_lookahead_and_hittest(root, tmp_path):
    win, _ctl = _make_window(root, tmp_path)
    # NEW (added 2023) is look-ahead at 2020 -> excluded
    assert {t.symbol for t in win._tiles} == {"AAA", "BBB"}
    aaa = next(t for t in win._tiles if t.symbol == "AAA")
    hit = tile_at(win._tiles, aaa.x + aaa.w / 2.0, aaa.y + aaa.h / 2.0)
    assert hit is not None and hit.symbol == "AAA"
    win.close()


def test_window_blind_title_hides_date(root, tmp_path):
    win, _ctl = _make_window(root, tmp_path, blind=True)
    text = win._header.cget("text")
    assert "Replay Bar 43" in text
    assert "2020-06-01" not in text  # no calendar date leaked in blind mode
    win.close()


def test_window_click_loads_symbol_on_chart(root, tmp_path):
    win, ctl = _make_window(root, tmp_path)
    bbb = next(t for t in win._tiles if t.symbol == "BBB")
    event = SimpleNamespace(
        inaxes=win._ax, xdata=bbb.x + bbb.w / 2.0, ydata=bbb.y + bbb.h / 2.0
    )
    win._on_click(event)
    assert ctl.focus_symbol == "BBB"
    win.close()


def test_window_empty_when_no_clock(root, tmp_path):
    prov = _provider(tmp_path)
    ctl = _Ctl(None)
    win = SandboxHeatmapWindow(root, ctl, provider=prov, price_source=_ps)
    root.update()
    assert win._tiles == ()
    win.close()


# ---------------------------------------------------------------------------
# Tile-area basis
# ---------------------------------------------------------------------------


@pytest.fixture
def no_settings_writes(monkeypatch):
    """Keep a basis change out of the process-wide settings store.

    ``_on_size_basis_change`` persists the choice, and `defaults` caches
    resolved values for the process — so without this a test that flips
    the basis silently changes the default every later test sees.
    """
    import tradinglab.defaults as _defaults
    import tradinglab.settings as _settings

    monkeypatch.setattr(_settings, "set", lambda *_a, **_k: None)
    monkeypatch.setattr(_settings, "get", lambda *_a, **_k: "")
    monkeypatch.setattr(_defaults, "reload", lambda: None)


def test_equal_weight_gives_every_tile_the_same_area(
    root, tmp_path, no_settings_writes
):
    """Size stops being a variable; the map becomes pure breadth."""
    win, _ctl = _make_window(root, tmp_path)
    try:
        win._size_var.set("Equal weight")
        win._on_size_basis_change()
        root.update()
        areas = {t.symbol: t.w * t.h for t in win._tiles}
        assert len(areas) == 2
        a, b = areas.values()
        assert a == pytest.approx(b, rel=1e-6), areas
    finally:
        win.close()


def test_dollar_volume_basis_uses_traded_value_not_cap(root, tmp_path, no_settings_writes):
    """Dollar volume needs no share count — the robust basis for thin names."""
    win, _ctl = _make_window(root, tmp_path)
    try:
        win._session_prices = SimpleNamespace(
            dollar_volume_at=lambda sym, _clk: {"AAA": 1.0, "BBB": 9.0}.get(sym),
            stale_symbols=lambda: set(),
        )
        win._size_var.set("Dollar volume")
        win._on_size_basis_change()
        root.update()
        areas = {t.symbol: t.w * t.h for t in win._tiles}
        assert areas["BBB"] > areas["AAA"] * 5, areas
    finally:
        win.close()


def test_symbol_without_volume_is_flagged_not_invented(root, tmp_path, no_settings_writes):
    win, _ctl = _make_window(root, tmp_path)
    try:
        win._session_prices = SimpleNamespace(
            dollar_volume_at=lambda sym, _clk: 5.0 if sym == "AAA" else None,
            stale_symbols=lambda: set(),
        )
        win._size_var.set("Dollar volume")
        win._on_size_basis_change()
        root.update()
        flagged = {t.symbol for t in win._tiles if t.approx_size}
        assert flagged == {"BBB"}, "no volume must be surfaced, not guessed"
    finally:
        win.close()


def test_basis_is_recorded_on_the_layout_for_the_legend(root, tmp_path, no_settings_writes):
    win, _ctl = _make_window(root, tmp_path)
    try:
        assert win._layout.size_basis == "historical_market_cap"
        win._size_var.set("Equal weight")
        win._on_size_basis_change()
        root.update()
        assert win._layout.size_basis == "equal_weight"
        assert "Equal weight" in win._footer.cget("text")
    finally:
        win.close()


def test_an_unknown_remembered_basis_falls_back_to_cap(root, tmp_path, monkeypatch):
    import tradinglab.defaults as _defaults

    monkeypatch.setattr(_defaults, "get", lambda k: "no-such-basis")
    win, _ctl = _make_window(root, tmp_path)
    try:
        assert win._size_basis == "historical_market_cap"
    finally:
        win.close()


# ---------------------------------------------------------------------------
# Per-tick fast path — everything the old full redraw refreshed implicitly
# must be refreshed explicitly (see _update_colors).
# ---------------------------------------------------------------------------


def test_focus_outline_moves_instead_of_accumulating(root, tmp_path):
    """Focus changes mid-session via click-to-chart, without a relayout.

    The fast path sets the highlight but must also clear it from the
    previously-focused tile, or every clicked tile stays ringed as
    "currently on the chart".
    """
    win, ctl = _make_window(root, tmp_path)
    try:
        assert ctl.focus_symbol == "AAA"
        win.on_replay_tick()
        root.update()
        assert win._patches["AAA"].get_linewidth() == pytest.approx(2.0)
        ctl.focus_symbol = "BBB"
        win.on_replay_tick()
        root.update()
        assert win._patches["BBB"].get_linewidth() == pytest.approx(2.0)
        assert win._patches["AAA"].get_linewidth() == pytest.approx(0.4), (
            "the previously-focused tile must lose its outline"
        )
    finally:
        win.close()


def test_position_badges_track_the_portfolio_within_a_session(root, tmp_path):
    """Opening / closing a position mid-session doesn't relayout.

    Badges therefore have to be re-read on the fast path; otherwise a
    new position shows nothing and a closed one leaves a stale L/S until
    the next session roll.
    """
    win, ctl = _make_window(root, tmp_path)
    try:
        win.on_replay_tick()
        root.update()
        assert win._badges["AAA"].get_text() == "L"
        assert win._badges["BBB"].get_text() == ""
        # Flip: close AAA, open a short in BBB.
        ctl.positions_snapshot = lambda: [
            {"symbol": "BBB", "quantity": -50.0, "avg_cost": 20.0}
        ]
        win.on_replay_tick()
        root.update()
        assert win._badges["AAA"].get_text() == ""
        assert win._badges["BBB"].get_text() == "S"
    finally:
        win.close()


def test_session_roll_requeues_a_prime_that_arrived_mid_flight(root, tmp_path):
    """A roll during a running prime must not be dropped.

    Parsing a universe takes seconds; an auto-cycle roll landing in that
    window used to be discarded, leaving the new session with a snapshot
    that answers nothing — a neutral, equal-sliver map until the *next*
    roll.
    """
    win, _ctl = _make_window(root, tmp_path)
    try:
        started: list[bool] = []
        win._start_prime = (  # type: ignore[method-assign]
            lambda members, clock, force=False: started.append(force)
        )
        win._priming = True
        win._prime_done = True
        win._pending_prime = (["AAA"], 123)
        win._poll_prime()
        assert started == [True], "the queued rebuild must be re-issued"
        assert win._pending_prime is None
    finally:
        win.close()
