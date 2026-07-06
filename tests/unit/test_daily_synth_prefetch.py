"""Self-heal prefetch in ``ChartApp._maybe_upsample_today_daily``.

Audit ``daily-today-upsample``. When the daily synth finds no cached
intraday for a symbol, it must kick a 5m companion prefetch so the synthetic
today-bar can form — even when the daily was served WARM from cache (the SPY
bug: SPY is preloaded as the default compare + ChartStack reference, so its
cold-path companion prefetch never fired and its 1d chart stuck on yesterday).

Bound to a ``SimpleNamespace`` stub (no Tk) so only the prefetch-decision
logic is exercised.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import tradinglab.app as app_mod
from tradinglab.data.today_upsample import _today_et
from tradinglab.models import Candle

# Anchor "today" to the SAME US-Eastern calendar date the production upsample
# code uses (``today_upsample._today_et``), NOT a naive ``datetime.now()``.
# On a UTC CI runner late in the ET day (e.g. 00:15 UTC = 20:15 ET the prior
# day) naive-now and ET-today disagree, so the synth/prefetch decision under
# test — which is made in ET — would mismatch the test's fixtures. Building
# tz-naive bars on the ET date keeps both sides aligned on every runner (and
# in the missing-tzdata fallback, both sides degrade identically).
_TODAY = dt.datetime.combine(_today_et(), dt.time(0, 0))


def _prev_weekday(d: dt.datetime) -> dt.datetime:
    d = d - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d = d - dt.timedelta(days=1)
    return d


_YESTERDAY = _prev_weekday(_TODAY)


def _daily(day: dt.datetime) -> Candle:
    return Candle(date=day.replace(hour=0, minute=0), open=1.0, high=1.0,
                  low=1.0, close=1.0, volume=1, session="regular")


def _five_min_today(n: int = 12) -> list[Candle]:
    out, t, px = [], _TODAY.replace(hour=9, minute=30), 200.0
    for _ in range(n):
        out.append(Candle(date=t, open=px, high=px + 0.2, low=px - 0.2,
                          close=px + 0.05, volume=1000, session="regular"))
        px += 0.05
        t = t + dt.timedelta(minutes=5)
    return out


def _stub(full_cache=None, *, session_open=True):
    calls: list[list[str]] = []
    stub = SimpleNamespace(
        _full_cache=dict(full_cache or {}),
        _intraday_session_open=lambda _now_s: session_open,
        _prefetch_companion_intervals=lambda syms: calls.append(list(syms)),
    )
    stub._maybe_upsample_today_daily = (
        app_mod.ChartApp._maybe_upsample_today_daily.__get__(stub)
    )
    return stub, calls


def test_kicks_prefetch_when_intraday_missing_and_synth_needed():
    stub, calls = _stub(session_open=True)
    daily = [_daily(_prev_weekday(_YESTERDAY)), _daily(_YESTERDAY)]
    out = stub._maybe_upsample_today_daily(
        daily, source="yfinance", symbol="SPY", interval="1d")
    assert calls == [["SPY"]], "must prefetch SPY's 5m companion"
    assert out is daily or out == daily  # unchanged (no intraday yet)


def test_no_prefetch_when_session_closed():
    stub, calls = _stub(session_open=False)
    daily = [_daily(_YESTERDAY)]
    stub._maybe_upsample_today_daily(
        daily, source="yfinance", symbol="SPY", interval="1d")
    assert calls == []


def test_no_prefetch_when_daily_already_has_today():
    stub, calls = _stub(session_open=True)
    daily = [_daily(_YESTERDAY), _daily(_TODAY)]
    stub._maybe_upsample_today_daily(
        daily, source="yfinance", symbol="SPY", interval="1d")
    assert calls == []


def test_no_prefetch_when_interval_not_daily():
    stub, calls = _stub(session_open=True)
    daily = [_daily(_YESTERDAY)]
    stub._maybe_upsample_today_daily(
        daily, source="yfinance", symbol="SPY", interval="5m")
    assert calls == []


def test_no_prefetch_when_no_symbol():
    stub, calls = _stub(session_open=True)
    daily = [_daily(_YESTERDAY)]
    stub._maybe_upsample_today_daily(
        daily, source="yfinance", symbol="", interval="1d")
    assert calls == []


def test_synth_applied_and_no_prefetch_when_intraday_present():
    full_cache = {("yfinance", "SPY", "5m"): _five_min_today()}
    stub, calls = _stub(full_cache=full_cache, session_open=True)
    daily = [_daily(_prev_weekday(_YESTERDAY)), _daily(_YESTERDAY)]
    out = stub._maybe_upsample_today_daily(
        daily, source="yfinance", symbol="SPY", interval="1d")
    assert calls == [], "no prefetch needed when intraday already cached"
    assert out[-1].date.date() == _TODAY.date(), "synth today-bar must be appended"
    assert len(out) == len(daily) + 1


def test_no_prefetch_when_allow_prefetch_false():
    """``allow_prefetch=False`` suppresses the self-heal companion prefetch.

    Regression for ``inbox-drain-livelock`` (d61 smoke hang). The
    prefetch-arrival refresh path passes ``allow_prefetch=False`` so that a
    synth which STILL can't form today's bar (stub / incomplete intraday
    during market hours) does NOT re-issue a companion prefetch — otherwise
    the arrival re-fires the refresh which re-prefetches, ad infinitum.
    """
    stub, calls = _stub(session_open=True)
    daily = [_daily(_prev_weekday(_YESTERDAY)), _daily(_YESTERDAY)]
    out = stub._maybe_upsample_today_daily(
        daily, source="yfinance", symbol="SPY", interval="1d",
        allow_prefetch=False)
    assert calls == [], "allow_prefetch=False must NOT kick a companion prefetch"
    assert out is daily or out == daily  # unchanged (no intraday yet)


def _refresh_stub(*, session_open=True, symbol="SPY"):
    """Stub exposing just what ``_refresh_daily_synth_for_active_view`` reads,
    with the REAL ``_maybe_upsample_today_daily`` bound so the call-site's
    ``allow_prefetch`` argument is exercised end-to-end."""
    calls: list[list[str]] = []
    daily = [_daily(_prev_weekday(_YESTERDAY)), _daily(_YESTERDAY)]
    stub = SimpleNamespace(
        interval_var=SimpleNamespace(get=lambda: "1d"),
        source_var=SimpleNamespace(get=lambda: "yfinance"),
        ticker_var=SimpleNamespace(get=lambda: symbol),
        compare_var=SimpleNamespace(get=lambda: False),
        compare_ticker_var=SimpleNamespace(get=lambda: ""),
        _full_cache={("yfinance", symbol, "1d"): list(daily)},
        _intraday_session_open=lambda _s: session_open,
        _prefetch_companion_intervals=lambda syms: calls.append(list(syms)),
        _apply_pair_filter_and_align=lambda p, c: (p, c or []),
        _set_data_state=lambda **k: None,
        _invalidate_focused_panels=lambda c: None,
        _render=lambda: None,
    )
    stub._maybe_upsample_today_daily = (
        app_mod.ChartApp._maybe_upsample_today_daily.__get__(stub)
    )
    stub._refresh_daily_synth_for_active_view = (
        app_mod.ChartApp._refresh_daily_synth_for_active_view.__get__(stub)
    )
    return stub, calls


def test_refresh_active_view_does_not_reprefetch():
    """The prefetch-arrival refresh path must NOT re-issue a prefetch.

    Pins the d61 ``inbox-drain-livelock`` root fix end-to-end:
    ``_refresh_daily_synth_for_active_view`` runs because a companion
    prefetch just landed; if its synth still can't form today's bar (market
    hours, incomplete intraday) it must NOT kick ANOTHER companion prefetch
    — that re-arrival would re-fire this refresh forever, livelocking
    ``app.update()`` (120s smoke timeout on fast CI runners). Without the
    ``allow_prefetch=False`` at the call site this would record a prefetch.
    """
    stub, calls = _refresh_stub(session_open=True)
    stub._refresh_daily_synth_for_active_view(prefetched_symbol="SPY")
    assert calls == [], (
        "refresh-on-arrival must not re-prefetch (would feed the "
        "prefetch->refresh->prefetch livelock)")


# ---------------------------------------------------------------------------
# Compare-panel preservation (audit ``daily-synth-compare-drop``).
# The prefetch-arrival refresh reads compare from the bounded in-memory
# ``_full_cache``; when the compare's daily bars were LRU-evicted it must NOT
# blank the on-screen compare panel. It falls back to disk, then preserves the
# currently-rendered ``_compare_raw``. Regression for the "compare chart
# disappears with Alpaca" bug (Alpaca's intraday keeps the daily-synth +
# companion-prefetch path busy, and the universe download churns the cache).
# ---------------------------------------------------------------------------

def _refresh_compare_stub(*, primary="AMD", compare="SPY",
                          compare_in_cache=False, compare_raw_existing=None):
    """Stub for the compare branch of ``_refresh_daily_synth_for_active_view``.

    Compare mode is ON. The primary's daily is always cached; the compare's
    daily is present in ``_full_cache`` only when ``compare_in_cache`` is set
    (the eviction case leaves it out). Captures the ``_set_data_state`` kwargs
    so the test can assert whether the compare was preserved or blanked.
    """
    captured: dict = {}
    warmed: list = []
    prim_daily = [_daily(_prev_weekday(_YESTERDAY)), _daily(_YESTERDAY)]
    comp_daily = [_daily(_prev_weekday(_YESTERDAY)), _daily(_YESTERDAY)]
    fc: dict = {("alpaca", primary, "1d"): list(prim_daily)}
    if compare_in_cache:
        fc[("alpaca", compare, "1d")] = list(comp_daily)

    class _FC(dict):
        def __setitem__(self, k, v):
            warmed.append(k)
            super().__setitem__(k, v)

    stub = SimpleNamespace(
        interval_var=SimpleNamespace(get=lambda: "1d"),
        source_var=SimpleNamespace(get=lambda: "alpaca"),
        ticker_var=SimpleNamespace(get=lambda: primary),
        compare_var=SimpleNamespace(get=lambda: True),
        compare_ticker_var=SimpleNamespace(get=lambda: compare),
        _full_cache=_FC(fc),
        _compare_raw=list(compare_raw_existing or []),
        _intraday_session_open=lambda _s: False,
        _prefetch_companion_intervals=lambda syms: None,
        _apply_pair_filter_and_align=lambda p, c: (list(p), list(c or [])),
        _set_data_state=lambda **k: captured.update(k),
        _invalidate_focused_panels=lambda c: None,
        _trim_full_cache=lambda: None,
        _render=lambda: None,
    )
    stub._maybe_upsample_today_daily = (
        app_mod.ChartApp._maybe_upsample_today_daily.__get__(stub)
    )
    stub._refresh_daily_synth_for_active_view = (
        app_mod.ChartApp._refresh_daily_synth_for_active_view.__get__(stub)
    )
    return stub, captured, warmed, comp_daily


def test_refresh_preserves_compare_via_disk_when_evicted(monkeypatch):
    """Compare's daily was LRU-evicted from memory but is on disk → the
    refresh must fall back to disk and keep the compare panel (and re-warm
    the memory cache)."""
    stub, captured, warmed, comp_daily = _refresh_compare_stub(
        compare_in_cache=False)
    monkeypatch.setattr(
        app_mod.disk_cache, "load",
        lambda src, sym, iv: list(comp_daily) if sym == "SPY" else None,
    )
    stub._refresh_daily_synth_for_active_view(prefetched_symbol="AMD")
    assert captured.get("compare"), (
        "compare panel must NOT be blanked on cache eviction — disk fallback")
    assert ("alpaca", "SPY", "1d") in warmed, (
        "disk-fallback compare should be re-warmed into the memory cache")


def test_refresh_preserves_compare_from_existing_when_no_disk(monkeypatch):
    """Compare not in memory AND not on disk, but currently on-screen
    (``_compare_raw`` non-empty) → preserve it rather than blank the panel."""
    existing = [_daily(_YESTERDAY)]
    stub, captured, _warmed, _cd = _refresh_compare_stub(
        compare_in_cache=False, compare_raw_existing=existing)
    monkeypatch.setattr(app_mod.disk_cache, "load", lambda *a, **k: None)
    stub._refresh_daily_synth_for_active_view(prefetched_symbol="AMD")
    assert captured.get("compare"), (
        "compare must be preserved from _compare_raw when memory+disk empty")


def test_refresh_blanks_compare_only_when_truly_empty(monkeypatch):
    """No compare data anywhere (memory, disk, or on-screen) → the compare
    is legitimately empty; the else-branch blank is correct."""
    stub, captured, _warmed, _cd = _refresh_compare_stub(
        compare_in_cache=False, compare_raw_existing=[])
    monkeypatch.setattr(app_mod.disk_cache, "load", lambda *a, **k: None)
    stub._refresh_daily_synth_for_active_view(prefetched_symbol="AMD")
    assert not captured.get("compare"), (
        "truly-empty compare stays empty (no phantom panel)")


def test_refresh_uses_memory_compare_without_disk_when_cached(monkeypatch):
    """When the compare IS in the in-memory cache the refresh must not touch
    disk at all (fast path unchanged)."""
    hits: list = []
    monkeypatch.setattr(
        app_mod.disk_cache, "load",
        lambda *a, **k: hits.append(a) or None,
    )
    stub, captured, _warmed, _cd = _refresh_compare_stub(compare_in_cache=True)
    stub._refresh_daily_synth_for_active_view(prefetched_symbol="AMD")
    assert captured.get("compare"), "cached compare must render"
    assert hits == [], "memory-cached compare must not hit disk"

