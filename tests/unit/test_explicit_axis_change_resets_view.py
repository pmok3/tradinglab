"""``_on_explicit_axis_change`` view-preservation policy.

Two axis-change cases with DIFFERENT correct behavior:

* **Interval change** — bar width changes, so both the bar-index and the
  calendar-window mappings from the previous interval are meaningless. Both
  preserve flags are dropped so ``_render`` snaps to the right-edge default
  ("switching to 5m must not land me in a window ~3 months ago with data
  still to the right of it").
* **Source-only change** (same ticker + interval, different provider) — the
  visible *dates* are still what the user wants, but the two providers can
  return different-length series (yfinance's 60-day 5m cap vs Alpaca's
  120-day deep history), so reusing the stale bar-INDEX window jumps the view
  to a different calendar day (the "switch source → jump to a month ago"
  bug). Preserve by TIME instead.

Also pins the ``_axis_switch_inflight`` race guard raised for the async load
window (checked by ``_next_bar_fetch_tick`` so a live poll can't re-arm
index-preservation mid-switch).

See ``app.spec.md`` invariant #6 + the ``source-switch-view-preserve`` audit.
"""

from __future__ import annotations

import types

from tradinglab.app import ChartApp
from tradinglab.core.view_intent import ViewController, ViewMode


class _Var:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


def _fake_app(*, source="yfinance", interval="1d",
              prev_source="yfinance", prev_interval="1d", **overrides):
    calls = {"load_async": 0, "sandbox_handle": 0}
    view = ViewController()
    # Pre-arm some intent so we can prove the sandbox branch leaves it alone.
    view.request(ViewMode.KEEP_BARS)
    fake = types.SimpleNamespace(
        _view=view,
        _drilldown_day="2026-06-09",
        _prev_axis_source=prev_source,
        _prev_axis_interval=prev_interval,
        source_var=_Var(source),
        interval_var=_Var(interval),
        _sandbox=None,
        _is_sandbox_active=lambda: False,
        _load_data_async=lambda: calls.__setitem__(
            "load_async", calls["load_async"] + 1),
        _sandbox_handle_interval_change=lambda: calls.__setitem__(
            "sandbox_handle", calls["sandbox_handle"] + 1),
        _prefetch_observe=lambda: None,
    )
    for k, v in overrides.items():
        setattr(fake, k, v)
    return fake, calls


def test_interval_change_snaps_to_right_edge():
    # Interval flipped 1d -> 5m: request DEFAULT → no index/time preserve.
    fake, calls = _fake_app(
        prev_source="yfinance", prev_interval="1d",
        source="yfinance", interval="5m",
    )

    ChartApp._on_explicit_axis_change(fake)

    assert fake._view.preserve is False
    assert fake._view.by_time is False
    assert fake._view.load_pending is True
    assert fake._drilldown_day is None
    assert calls["load_async"] == 1
    # Tracking advanced for the next classification.
    assert fake._prev_axis_interval == "5m"


def test_source_only_change_preserves_by_time():
    # Same interval, different provider (yfinance -> alpaca): request KEEP_DATES
    # so the visible DATE window is preserved across different-length series.
    fake, calls = _fake_app(
        prev_source="yfinance", prev_interval="5m",
        source="alpaca", interval="5m",
    )

    ChartApp._on_explicit_axis_change(fake)

    assert fake._view.by_time is True
    assert fake._view.preserve is False
    assert fake._view.load_pending is True
    assert fake._drilldown_day is None
    assert calls["load_async"] == 1
    assert fake._prev_axis_source == "alpaca"


def test_axis_change_raises_inflight_guard():
    # The async-load race guard (load_pending) must be raised so a live
    # _next_bar_fetch_tick can't re-arm index-preservation mid-switch.
    fake, _calls = _fake_app(source="alpaca", interval="5m",
                             prev_source="yfinance", prev_interval="5m")

    ChartApp._on_explicit_axis_change(fake)

    assert fake._view.load_pending is True


def test_explicit_axis_change_sandbox_active_routes_to_controller():
    # While a sandbox session is active the change is intercepted by the
    # sandbox handler and the view intent is left alone.
    fake, calls = _fake_app(
        _is_sandbox_active=lambda: True,
        _sandbox=object(),
    )

    ChartApp._on_explicit_axis_change(fake)

    assert calls["sandbox_handle"] == 1
    assert calls["load_async"] == 0
    # Untouched in the sandbox branch: the pre-armed KEEP_BARS intent stands
    # and no switch was marked in flight.
    assert fake._view.preserve is True
    assert fake._view.load_pending is False
    assert fake._drilldown_day == "2026-06-09"

