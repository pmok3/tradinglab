"""Unit tests for M5 ChartStack sandbox lockstep.

These tests focus on the headless API surface:

* :meth:`SandboxController.register_card_subscriber` — registration,
  release callable, exception-safety, idempotent unregister. Tested
  via direct call to :meth:`_fire_card_subscribers` to avoid the
  full ``start_session`` setup (covered by smoke).

* :class:`CardController` halt API — ``mark_halted`` / ``clear_halt``
  / ``halt_index`` / ``is_halted``, plus reset on ``bind()`` / ``stop()``.

* :class:`ChartStackPanel` pin + sandbox-attach contract — pin list
  semantics, sandbox attach/detach, pin snapshot/restore, auto-detach
  on ``end_session``.
"""

from __future__ import annotations

import tkinter as tk
from types import SimpleNamespace
from typing import Any

import pytest

from tradinglab.backtest.replay import SandboxController
from tradinglab.gui.chartstack import ChartStackPanel
from tradinglab.gui.chartstack.binding import CardBinding
from tradinglab.gui.chartstack.controller import (
    CardController,
    CardState,
)


# ---------------------------------------------------------------------------
# CardController halt API
# ---------------------------------------------------------------------------


def test_card_controller_starts_unhalted():
    c = CardController(slot_index=0, owner_app=None)
    assert c.halt_index is None
    assert not c.is_halted
    assert c.state == CardState.IDLE


def test_card_controller_mark_halted_sets_index_and_state():
    c = CardController(slot_index=0, owner_app=None)
    c.mark_halted(7)
    assert c.halt_index == 7
    assert c.is_halted
    assert c.state == CardState.HALTED


def test_card_controller_mark_halted_clamps_negative_to_zero():
    c = CardController(slot_index=0, owner_app=None)
    c.mark_halted(-3)
    assert c.halt_index == 0


def test_card_controller_mark_halted_coerces_int_like_input():
    c = CardController(slot_index=0, owner_app=None)
    c.mark_halted("5")  # type: ignore[arg-type]
    assert c.halt_index == 5


def test_card_controller_clear_halt_returns_to_ready_without_stream():
    c = CardController(slot_index=0, owner_app=None)
    c.mark_halted(3)
    c.clear_halt()
    assert c.halt_index is None
    assert not c.is_halted
    # Without an active stream subscription, falls back to READY.
    assert c.state == CardState.READY


def test_card_controller_clear_halt_no_op_when_not_halted():
    c = CardController(slot_index=0, owner_app=None)
    c.clear_halt()  # no-op
    assert c.halt_index is None
    assert c.state == CardState.IDLE


def test_card_controller_bind_clears_halt():
    c = CardController(slot_index=0, owner_app=None)
    c.mark_halted(2)
    c.bind(CardBinding(symbol="MSFT", source_label="watchlist"))
    assert c.halt_index is None


def test_card_controller_stop_clears_halt():
    c = CardController(slot_index=0, owner_app=None)
    c.mark_halted(2)
    c.stop()
    assert c.halt_index is None


# ---------------------------------------------------------------------------
# SandboxController.register_card_subscriber
# ---------------------------------------------------------------------------


def _make_bare_controller() -> SandboxController:
    """Construct a :class:`SandboxController` with a stub app.

    No session is started — these tests exercise the registration
    API directly via :meth:`_fire_card_subscribers`. The
    integration with ``next_bar`` + ``end_session`` is covered
    by the smoke-suite chartstack lockstep test.
    """
    app = SimpleNamespace()
    return SandboxController(app=app)


def test_register_card_subscriber_returns_release_callable():
    ctl = _make_bare_controller()
    release = ctl.register_card_subscriber(lambda: None)
    assert callable(release)
    assert len(ctl._card_subscribers) == 1


def test_register_card_subscriber_rejects_non_callable():
    ctl = _make_bare_controller()
    with pytest.raises(TypeError):
        ctl.register_card_subscriber("nope")  # type: ignore[arg-type]


def test_fire_card_subscribers_invokes_in_registration_order():
    ctl = _make_bare_controller()
    calls: list[str] = []
    ctl.register_card_subscriber(lambda: calls.append("a"))
    ctl.register_card_subscriber(lambda: calls.append("b"))
    ctl.register_card_subscriber(lambda: calls.append("c"))
    ctl._fire_card_subscribers()
    assert calls == ["a", "b", "c"]


def test_fire_card_subscribers_fires_each_subscriber_once_per_call():
    ctl = _make_bare_controller()
    counter = {"n": 0}

    def _cb():
        counter["n"] += 1

    ctl.register_card_subscriber(_cb)
    ctl._fire_card_subscribers()
    ctl._fire_card_subscribers()
    ctl._fire_card_subscribers()
    assert counter["n"] == 3


def test_release_removes_subscription():
    ctl = _make_bare_controller()
    counter = {"n": 0}

    def _cb():
        counter["n"] += 1

    release = ctl.register_card_subscriber(_cb)
    ctl._fire_card_subscribers()
    release()
    ctl._fire_card_subscribers()
    ctl._fire_card_subscribers()
    assert counter["n"] == 1
    assert ctl._card_subscribers == []


def test_release_is_idempotent():
    ctl = _make_bare_controller()
    release = ctl.register_card_subscriber(lambda: None)
    release()
    release()  # must not raise
    assert ctl._card_subscribers == []


def test_release_only_removes_one_subscription_when_same_callable_twice():
    """Registering the same callable twice yields two slots; one
    release callable removes only the one it owns."""
    ctl = _make_bare_controller()
    counter = {"n": 0}

    def _cb():
        counter["n"] += 1

    rel_a = ctl.register_card_subscriber(_cb)
    ctl.register_card_subscriber(_cb)
    assert len(ctl._card_subscribers) == 2
    rel_a()
    assert len(ctl._card_subscribers) == 1
    ctl._fire_card_subscribers()
    assert counter["n"] == 1  # the surviving registration still fires


def test_one_bad_subscriber_does_not_block_other_subscribers():
    ctl = _make_bare_controller()
    calls_a: list[int] = []
    calls_b: list[int] = []

    def _bad():
        raise RuntimeError("oops")

    ctl.register_card_subscriber(lambda: calls_a.append(1))
    ctl.register_card_subscriber(_bad)
    ctl.register_card_subscriber(lambda: calls_b.append(1))
    ctl._fire_card_subscribers()
    assert calls_a == [1]
    assert calls_b == [1]


def test_subscriber_can_unregister_self_mid_fire():
    """The fan-out iterates a snapshot so a subscriber that
    unregisters itself during dispatch doesn't disturb the
    rest of the loop."""
    ctl = _make_bare_controller()
    calls: list[str] = []
    release_holder: dict[str, Any] = {"release": None}

    def _self_remove():
        calls.append("self_remove")
        release_holder["release"]()

    release_holder["release"] = ctl.register_card_subscriber(_self_remove)
    ctl.register_card_subscriber(lambda: calls.append("after"))
    ctl._fire_card_subscribers()
    assert calls == ["self_remove", "after"]
    # Self-removed; the survivor remains.
    assert len(ctl._card_subscribers) == 1


# ---------------------------------------------------------------------------
# ChartStackPanel manual-pin API
# ---------------------------------------------------------------------------


@pytest.fixture
def panel(root):
    """Yield a :class:`ChartStackPanel` under the per-test Toplevel."""
    p = ChartStackPanel(root, owner=None)
    yield p
    try:
        p.destroy()
    except Exception:  # noqa: BLE001
        pass


def test_panel_starts_with_no_manual_pins(panel):
    assert panel.get_manual_pins() == ()


def test_panel_pin_symbol_adds_to_list(panel):
    panel.pin_symbol("TSLA")
    assert panel.get_manual_pins() == ("TSLA",)


def test_panel_pin_symbol_is_idempotent(panel):
    panel.pin_symbol("TSLA")
    panel.pin_symbol("TSLA")
    panel.pin_symbol("TSLA")
    assert panel.get_manual_pins() == ("TSLA",)


def test_panel_pin_symbol_preserves_insertion_order(panel):
    for sym in ("AAPL", "MSFT", "TSLA"):
        panel.pin_symbol(sym)
    assert panel.get_manual_pins() == ("AAPL", "MSFT", "TSLA")


def test_panel_unpin_symbol_removes_from_list(panel):
    panel.pin_symbol("AAPL")
    panel.pin_symbol("MSFT")
    panel.unpin_symbol("AAPL")
    assert panel.get_manual_pins() == ("MSFT",)


def test_panel_unpin_unknown_symbol_is_noop(panel):
    panel.pin_symbol("AAPL")
    panel.unpin_symbol("NVDA")  # not pinned
    assert panel.get_manual_pins() == ("AAPL",)


def test_panel_clear_manual_pins_wipes_list(panel):
    panel.pin_symbol("AAPL")
    panel.pin_symbol("MSFT")
    panel.clear_manual_pins()
    assert panel.get_manual_pins() == ()


def test_panel_pin_none_is_noop(panel):
    panel.pin_symbol(None)
    assert panel.get_manual_pins() == ()


def test_panel_pin_dedupes_by_string_value(panel):
    """Pinning a rich object that stringifies to the same value
    as an already-pinned symbol is deduped."""
    class _RichSym:
        def __str__(self) -> str:
            return "AAPL"

    panel.pin_symbol("AAPL")
    panel.pin_symbol(_RichSym())  # same str → no-op
    assert len(panel.get_manual_pins()) == 1


# ---------------------------------------------------------------------------
# ChartStackPanel sandbox attach/detach
# ---------------------------------------------------------------------------


class _StubSandbox:
    """Minimal sandbox stand-in for panel attach/detach tests.

    Implements just the surface the panel calls:
    ``register_card_subscriber``, ``is_active``,
    ``visible_candles_by_symbol``. The panel's lockstep
    callback only reads these.
    """

    def __init__(self) -> None:
        self.subscribers: list[Any] = []
        self.active = True
        self.visible_candles_by_symbol: dict[str, list] = {}

    def register_card_subscriber(self, cb):
        if not callable(cb):
            raise TypeError("callback must be callable")
        self.subscribers.append(cb)

        def _release(_cb=cb, _self=self):
            try:
                _self.subscribers.remove(_cb)
            except ValueError:
                pass

        return _release

    def is_active(self) -> bool:
        return self.active

    def fire(self) -> None:
        for cb in list(self.subscribers):
            try:
                cb()
            except Exception:  # noqa: BLE001
                pass

    def end(self) -> None:
        self.active = False
        self.fire()
        self.subscribers.clear()


def test_panel_attach_sandbox_registers_subscriber(panel):
    sb = _StubSandbox()
    panel.attach_sandbox(sb)
    assert len(sb.subscribers) == 1


def test_panel_attach_sandbox_is_idempotent(panel):
    sb = _StubSandbox()
    panel.attach_sandbox(sb)
    panel.attach_sandbox(sb)
    panel.attach_sandbox(sb)
    assert len(sb.subscribers) == 1


def test_panel_detach_sandbox_unregisters_subscriber(panel):
    sb = _StubSandbox()
    panel.attach_sandbox(sb)
    panel.detach_sandbox()
    assert sb.subscribers == []


def test_panel_detach_when_not_attached_is_noop(panel):
    panel.detach_sandbox()  # no-op
    panel.detach_sandbox()


def test_panel_pins_restored_on_sandbox_detach(panel):
    """Pins added during a sandbox session are dropped on detach;
    pins that existed before attach are retained."""
    sb = _StubSandbox()
    panel.pin_symbol("AAPL")
    panel.attach_sandbox(sb)
    panel.pin_symbol("NVDA")
    assert panel.get_manual_pins() == ("AAPL", "NVDA")
    panel.detach_sandbox()
    assert panel.get_manual_pins() == ("AAPL",)


def test_panel_auto_detaches_when_sandbox_ends(panel):
    """``end_session`` fires subscribers one last time with
    ``active=False``; the panel uses that as its detach signal."""
    sb = _StubSandbox()
    panel.pin_symbol("AAPL")
    panel.attach_sandbox(sb)
    panel.pin_symbol("NVDA")
    sb.end()
    # Panel observed active=False and self-detached.
    assert panel.get_manual_pins() == ("AAPL",)


def test_panel_destroy_releases_sandbox_subscription(panel, root):
    """Destroying the panel mid-session releases the subscription."""
    sb = _StubSandbox()
    panel.attach_sandbox(sb)
    assert len(sb.subscribers) == 1
    panel.destroy()
    assert sb.subscribers == []


def test_panel_attach_swaps_to_new_sandbox(panel):
    """Attaching a different sandbox while already attached
    detaches the old one and registers with the new."""
    sb_old = _StubSandbox()
    sb_new = _StubSandbox()
    panel.attach_sandbox(sb_old)
    panel.attach_sandbox(sb_new)
    assert sb_old.subscribers == []
    assert len(sb_new.subscribers) == 1


def test_panel_attach_fires_one_initial_tick(panel):
    """Attach calls ``_on_sandbox_tick`` immediately so cards
    reflect freshly-advanced sandbox state without waiting for
    the next bar. With an empty ``visible_candles_by_symbol``
    the tick is a no-op but must not raise."""
    sb = _StubSandbox()
    panel.attach_sandbox(sb)
    # No exception is the assertion.
