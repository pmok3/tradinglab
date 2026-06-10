"""``_on_explicit_axis_change`` snaps to the right edge on interval change.

Regression for "switching to 5m takes me to a window ~3 months ago with
data still to the right of it". ``_preserve_xlim_on_render`` is a sticky
flag set by every pan / zoom / scroll / poll-tick and only cleared by
explicit-user-intent paths. An explicit source / interval / pre-post
change re-bases the x-axis: bar-index coordinates from the previous
interval (e.g. the last 200 *daily* bars, index ~[300, 500]) are
meaningless on the new series (e.g. ~11k 5m bars) and would land the
view months in the past. ``_on_explicit_axis_change`` must drop the
preserve flags so ``_render`` falls through to the right-edge default
window.

See ``app.spec.md`` invariant #6.
"""

from __future__ import annotations

import types

from tradinglab.app import ChartApp


def _fake_app(**overrides):
    calls = {"load_async": 0, "sandbox_handle": 0}
    fake = types.SimpleNamespace(
        _preserve_xlim_on_render=True,
        _preserve_xlim_by_time_on_render=True,
        _drilldown_day="2026-06-09",
        _sandbox=None,
        _is_sandbox_active=lambda: False,
        _load_data_async=lambda: calls.__setitem__(
            "load_async", calls["load_async"] + 1),
        _sandbox_handle_interval_change=lambda: calls.__setitem__(
            "sandbox_handle", calls["sandbox_handle"] + 1),
    )
    for k, v in overrides.items():
        setattr(fake, k, v)
    return fake, calls


def test_explicit_axis_change_clears_preserve_flags_and_reloads():
    fake, calls = _fake_app()

    ChartApp._on_explicit_axis_change(fake)

    # Bar-index xlim from the previous interval must NOT be preserved.
    assert fake._preserve_xlim_on_render is False
    assert fake._preserve_xlim_by_time_on_render is False
    # Drill-down is invalidated by an explicit axis change.
    assert fake._drilldown_day is None
    # The reload was triggered.
    assert calls["load_async"] == 1


def test_explicit_axis_change_sandbox_active_routes_to_controller():
    # While a sandbox session is active the change is intercepted by the
    # sandbox handler and the preserve flags / drill-down are left alone.
    fake, calls = _fake_app(
        _is_sandbox_active=lambda: True,
        _sandbox=object(),
    )

    ChartApp._on_explicit_axis_change(fake)

    assert calls["sandbox_handle"] == 1
    assert calls["load_async"] == 0
    # Untouched in the sandbox branch.
    assert fake._preserve_xlim_on_render is True
    assert fake._drilldown_day == "2026-06-09"
