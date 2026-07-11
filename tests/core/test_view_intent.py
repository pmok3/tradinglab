"""Unit tests for ``core.view_intent`` — the chart view-preservation controller."""
from __future__ import annotations

import pytest

from tradinglab.core.view_intent import (
    ViewController,
    ViewMode,
    is_one_shot,
    mode_to_flags,
)


# --------------------------------------------------------------- pure mapping
@pytest.mark.parametrize(
    "mode,expected",
    [
        (ViewMode.DEFAULT, (False, False, False)),
        (ViewMode.KEEP_BARS, (True, False, False)),
        (ViewMode.KEEP_DATES, (False, True, False)),
        (ViewMode.SNAP_RIGHT, (True, False, True)),
    ],
)
def test_mode_to_flags(mode, expected):
    assert mode_to_flags(mode) == expected


def test_is_one_shot():
    assert is_one_shot(ViewMode.KEEP_DATES)
    assert is_one_shot(ViewMode.SNAP_RIGHT)
    assert not is_one_shot(ViewMode.KEEP_BARS)
    assert not is_one_shot(ViewMode.DEFAULT)


# ------------------------------------------------------------------- request
def test_request_sets_triple():
    vc = ViewController()
    vc.request(ViewMode.KEEP_DATES)
    assert vc.snapshot() == (False, True, False, False)


def test_request_keep_bars_sticky_flag():
    vc = ViewController()
    vc.request(ViewMode.KEEP_BARS)
    assert vc.snapshot() == (True, False, False, False)


def test_request_load_pending_only_when_asked():
    vc = ViewController()
    vc.request(ViewMode.KEEP_DATES)
    assert vc.load_pending is False
    vc.request(ViewMode.KEEP_DATES, load_pending=True)
    assert vc.load_pending is True


def test_arm_keep_bars_sugar():
    vc = ViewController()
    vc.arm_keep_bars()
    assert vc.snapshot() == (True, False, False, False)


# --------------------------------------------------------- render_directives
def test_render_directives_default():
    vc = ViewController()
    vc.request(ViewMode.DEFAULT)
    assert vc.render_directives() == (False, False, False)


def test_render_directives_keep_bars_is_sticky():
    vc = ViewController()
    vc.request(ViewMode.KEEP_BARS)
    assert vc.render_directives() == (True, False, False)
    # Sticky: a second render keeps preserving the bar window.
    assert vc.render_directives() == (True, False, False)


def test_render_directives_slide_is_one_shot():
    vc = ViewController()
    vc.request(ViewMode.SNAP_RIGHT)
    # First render slides + preserves.
    assert vc.render_directives() == (True, False, True)
    # slide consumed; index-preserve remains sticky.
    assert vc.render_directives() == (True, False, False)


def test_render_directives_keep_dates_is_one_shot_and_reverts():
    vc = ViewController()
    vc.request(ViewMode.KEEP_DATES)
    assert vc.render_directives() == (False, True, False)
    # by_time consumed; reverts to no-preserve (DEFAULT-ish) — NOT sticky index.
    assert vc.render_directives() == (False, False, False)


def test_by_time_forces_index_preserve_off_even_when_both_set():
    """The source-switch 'jump to 2021' race: an index-preserve re-arm must NOT
    win over an active time-remap. render_directives forces preserve=False and
    clears the stored sticky index flag when by_time is applied."""
    vc = ViewController()
    vc.request(ViewMode.KEEP_DATES)          # by_time=True, preserve=False
    vc._preserve = True                       # a racing event re-armed index-preserve
    preserve, by_time, slide = vc.render_directives()
    assert (preserve, by_time, slide) == (False, True, False)
    # The stored sticky index flag was cleared too, so the NEXT render is clean.
    assert vc.snapshot()[0] is False


# ------------------------------------------------- durability across switches
def test_hold_during_pending_switch_consumes_nothing():
    vc = ViewController()
    vc.request(ViewMode.KEEP_DATES, load_pending=True)
    # An intervening render while the switch load is in flight: HOLD.
    assert vc.render_directives() == (False, False, False)
    # by_time was NOT consumed — it survives for the completing render.
    assert vc.snapshot()[1] is True


def test_hold_during_pending_preserves_current_index_view():
    vc = ViewController()
    vc.request(ViewMode.KEEP_DATES, load_pending=True)
    vc._preserve = True   # user had a zoom active before the switch
    # Intervening render HOLDs the current index view, still no consume.
    assert vc.render_directives() == (True, False, False)
    assert vc.snapshot()[1] is True  # by_time still pending


def test_completing_switch_applies_held_intent():
    vc = ViewController()
    vc.request(ViewMode.KEEP_DATES, load_pending=True)
    # Intervening renders HOLD.
    vc.render_directives()
    vc.render_directives()
    # A racing event re-armed index-preserve mid-switch.
    vc._preserve = True
    # The completing load lowers load_pending and reports it was a switch.
    assert vc.begin_completing_load() is True
    assert vc.load_pending is False
    # The completing render now applies KEEP_DATES and index-preserve loses.
    assert vc.render_directives() == (False, True, False)


def test_begin_completing_load_false_when_no_switch():
    vc = ViewController()
    vc.request(ViewMode.KEEP_BARS)  # no load_pending
    assert vc.begin_completing_load() is False


def test_interval_switch_snaps_right_on_completion():
    vc = ViewController()
    vc.request(ViewMode.DEFAULT, load_pending=True)  # interval change
    vc.render_directives()  # intervening HOLD
    assert vc.begin_completing_load() is True
    assert vc.render_directives() == (False, False, False)  # right-edge default


# ---------------------------------------------------------- snapshot/restore
def test_snapshot_restore_round_trip():
    vc = ViewController()
    vc.request(ViewMode.SNAP_RIGHT, load_pending=True)
    snap = vc.snapshot()
    vc.request(ViewMode.DEFAULT)
    vc.begin_completing_load()
    assert vc.snapshot() != snap
    vc.restore(snap)
    assert vc.snapshot() == snap
