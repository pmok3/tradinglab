"""Unit tests for `gui/sandbox_heatmap.py` (pure helpers + Tk/Agg window)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tradinglab.backtest.heatmap import HeatmapTile, scaled_cap
from tradinglab.backtest.heatmap_provider import HeatmapProvider
from tradinglab.gui.sandbox_heatmap import compute_size_pct, tile_at


def _epoch(y, m, d) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


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
        shares_fetcher=lambda s: [(_epoch(2015, 1, 1), 1000.0)],
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
