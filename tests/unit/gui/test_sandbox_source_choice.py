"""Tests for the sandbox data-source choice (audit ``sandbox-data-source``).

Vendors are not interchangeable for replay: yfinance caps intraday at
~60 days but reports full consolidated volume, Alpaca's free feed
reaches years with IEX-only volume (~2-3% of the tape), Schwab/Polygon
go deep with full volume. Which trade-off is right depends on the
practice the trader is doing, so the choice is explicit — made in the
Start Sandbox dialog, remembered in ``sandbox_data_source``, and pinned
on the controller for the session's lifetime.

What's pinned here:

1. The dialog offers Auto + the caller's source list, resolves the
   selection, and hands it to the eligibility / fetch providers (depth
   differs per vendor, so the eligible-date pool genuinely changes).
2. ``start_session`` records the choice on the controller.
3. Mid-session loads read the session's pinned source instead of
   re-deriving a ranking — re-deriving was a latent vendor-mixing bug,
   because saving credentials mid-session changes the global order.
4. The heatmap prices from the session's source, not a hardcoded vendor.
"""

from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace

import pytest

from tradinglab.backtest.sandbox_app import _sandbox_preferred_src

# ---------------------------------------------------------------------------
# Mid-session loads follow the session, not the live ranking
# ---------------------------------------------------------------------------
#
# ``SandboxController.data_source`` round-tripping through
# ``start_session`` is pinned in
# ``tests/unit/backtest/test_replay_state_machine.py`` (that module owns
# the controller harness).


class _FakeVar:
    def __init__(self, initial: str = "") -> None:
        self._v = initial

    def get(self) -> str:
        return self._v

    def set(self, v: str) -> None:
        self._v = v


def _app(*, chart_source: str = "yfinance", session_source: str | None = None):
    sandbox = (
        None if session_source is None
        else SimpleNamespace(data_source=session_source)
    )
    return SimpleNamespace(source_var=_FakeVar(chart_source), _sandbox=sandbox)


def test_midsession_fetch_uses_the_sessions_pinned_source() -> None:
    # The chart source says yfinance and the global ranking might too —
    # the session's pin must still win, or a symbol loaded mid-replay
    # comes off a different tape than the timeline it trades against.
    app = _app(chart_source="yfinance", session_source="alpaca")
    assert _sandbox_preferred_src(app, "5m") == "alpaca"


def test_midsession_fetch_falls_back_to_ranking_without_a_session() -> None:
    got = _sandbox_preferred_src(_app(chart_source="yfinance"), "5m")
    assert got, "must resolve to something rather than empty"


def test_midsession_fetch_ignores_an_empty_pin() -> None:
    app = _app(chart_source="yfinance", session_source="")
    assert _sandbox_preferred_src(app, "5m")


def test_midsession_fetch_survives_a_broken_app() -> None:
    broken = SimpleNamespace(source_var=_FakeVar("yfinance"))
    assert _sandbox_preferred_src(broken, "5m") == "yfinance"


# ---------------------------------------------------------------------------
# Heatmap prices from the session's source
# ---------------------------------------------------------------------------

tk = pytest.importorskip("tkinter")
pytest.importorskip("matplotlib")
import matplotlib  # noqa: E402

matplotlib.use("Agg")

from tradinglab.gui.sandbox_dialog import SandboxStartDialog  # noqa: E402
from tradinglab.gui.sandbox_heatmap import SandboxHeatmapWindow  # noqa: E402


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


class _Ctl:
    def __init__(self, data_source: str = "", interval: str = "15m") -> None:
        self.data_source = data_source
        self.interval = interval
        self.blind = False
        self.focus_symbol = None

    def is_active(self) -> bool:
        return True

    def clock_ts(self):
        return None  # keeps the window in its empty state — no disk reads

    def current_session_date(self):
        return None


def test_heatmap_prices_from_the_session_source(root, tmp_path) -> None:
    from tradinglab.backtest.heatmap_provider import HeatmapProvider

    prov = HeatmapProvider(meta={}, shares_fetcher=lambda _s: [], cache_dir=tmp_path)
    win = SandboxHeatmapWindow(root, _Ctl("alpaca", "15m"), provider=prov)
    root.update()
    try:
        assert win._session_source() == "alpaca"
        assert win._session_interval() == "15m"
        assert win._session_prices is not None
        assert win._session_prices.source == "alpaca"
        assert win._session_prices.interval == "15m"
    finally:
        win.close()


def test_heatmap_falls_back_to_the_chart_source(root, tmp_path) -> None:
    from tradinglab.backtest.heatmap_provider import HeatmapProvider

    prov = HeatmapProvider(meta={}, shares_fetcher=lambda _s: [], cache_dir=tmp_path)
    app = SimpleNamespace(source_var=_FakeVar("polygon"))
    win = SandboxHeatmapWindow(root, _Ctl("", "5m"), provider=prov)
    win.app = app
    try:
        assert win._session_source() == "polygon"
    finally:
        win.close()


# ---------------------------------------------------------------------------
# Start dialog
# ---------------------------------------------------------------------------


def _dialog(root, *, sources=None, default_source="", calls=None):
    seen = calls if calls is not None else []

    def _eligible(itv: str, source: str) -> list[_dt.date]:
        seen.append(("eligible", itv, source))
        return [_dt.date(2025, 3, 4)]

    def _fetch(itv: str, source: str) -> bool:
        seen.append(("fetch", itv, source))
        return True

    dlg = SandboxStartDialog(
        root,
        reference_symbol="SPY",
        intervals=["1m", "5m", "15m"],
        eligible_dates_provider=_eligible,
        fetch_provider=_fetch,
        default_interval="5m",
        sources=list(sources if sources is not None else ["yfinance", "alpaca"]),
        default_source=default_source,
    )
    root.update()
    return dlg, seen


def test_dialog_offers_auto_plus_every_source(root) -> None:
    dlg, _ = _dialog(root)
    try:
        values = list(dlg._source_combo.cget("values"))
        assert values == [
            SandboxStartDialog.AUTO_SOURCE_LABEL, "yfinance", "alpaca"
        ], values
        assert dlg._selected_source() == "", "Auto must resolve to empty"
    finally:
        dlg.destroy()


def test_dialog_preselects_the_remembered_source(root) -> None:
    dlg, _ = _dialog(root, default_source="alpaca")
    try:
        assert dlg._source_var.get() == "alpaca"
        assert dlg._selected_source() == "alpaca"
    finally:
        dlg.destroy()


def test_dialog_ignores_an_unavailable_remembered_source(root) -> None:
    """A source that's no longer registered must not be silently used."""
    dlg, _ = _dialog(root, default_source="schwab")
    try:
        assert dlg._selected_source() == ""
        assert dlg._source_var.get() == SandboxStartDialog.AUTO_SOURCE_LABEL
    finally:
        dlg.destroy()


def test_dialog_passes_the_source_to_the_providers(root) -> None:
    dlg, seen = _dialog(root, default_source="alpaca")
    try:
        assert seen, "the dialog must probe eligibility on open"
        assert all(entry[2] == "alpaca" for entry in seen), seen
        seen.clear()
        dlg._source_var.set("yfinance")
        dlg._on_source_change()
        assert seen, "changing the source must re-probe eligibility"
        assert all(entry[2] == "yfinance" for entry in seen), seen
    finally:
        dlg.destroy()


def test_dialog_result_carries_the_source(root) -> None:
    dlg, _ = _dialog(root, default_source="alpaca")
    try:
        dlg._date_var.set("2025-03-04")
        dlg._on_start()
        assert dlg.result is not None
        assert dlg.result["data_source"] == "alpaca"
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


def test_dialog_hint_names_the_volume_tradeoff(root) -> None:
    """The hint has to state reach + volume tier — that IS the decision."""
    dlg, _ = _dialog(root, default_source="alpaca")
    try:
        hint = dlg._source_hint()
        assert "alpaca" in hint
        assert "intraday" in hint and "daily" in hint
        dlg._source_var.set(SandboxStartDialog.AUTO_SOURCE_LABEL)
        assert "Auto" in dlg._source_hint()
    finally:
        dlg.destroy()


def test_dialog_with_no_sources_still_works(root) -> None:
    dlg, _ = _dialog(root, sources=[])
    try:
        assert list(dlg._source_combo.cget("values")) == [
            SandboxStartDialog.AUTO_SOURCE_LABEL
        ]
        assert dlg._selected_source() == ""
    finally:
        dlg.destroy()
