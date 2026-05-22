"""Unit tests for ``tradinglab.exits.signals``.

The paper-engine integration test is split out so it can be unblocked
by a fake engine (here) and later re-validated against the real
``PaperBrokerEngine`` once that lands. The fake honors the same API
shape (``submit``/``cancel``/``cancel_all_for_position``) so the sink
contract is fully exercised either way.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from tradinglab.core.thread_guard import (
    TkThreadViolation,
    tk_thread_check_disabled,
)
from tradinglab.exits import audit as audit_mod
from tradinglab.exits.audit import AuditLog
from tradinglab.exits.model import OrderSide
from tradinglab.exits.signals import (
    ExitOrderKind,
    ExitSignal,
    ManualPaperSink,
    ManualSignalEvent,
    PaperBrokerSink,
    SchwabTraderNotConfigured,
    SchwabTraderSink,
)

# ---------------------------------------------------------------------------
# Fake paper engine — exercises PaperBrokerSink without depending on the
# real PaperBrokerEngine class (delivered by a parallel agent).
# ---------------------------------------------------------------------------


class _FakePaperEngine:
    """Minimal stand-in for ``PaperBrokerEngine``.

    Implements ``submit``/``cancel``/``cancel_all_for_position`` and
    records every call so tests can assert the translation contract.
    Does NOT enforce ``@require_tk_thread`` itself (we're testing the
    sink's own decoration).
    """

    def __init__(self) -> None:
        self.submitted: list[Any] = []  # PaperOrder instances
        self.cancelled_ids: list[str] = []
        self.cancel_all_calls: list[str] = []
        self._next_id = 0
        self._known: dict[str, Any] = {}  # order_id -> order

    def submit(self, order: Any) -> str:
        # The real engine has its own ids; we mint sequential ones
        oid = f"engine-{self._next_id}"
        self._next_id += 1
        self.submitted.append(order)
        self._known[oid] = order
        return oid

    def cancel(self, order_id: str) -> bool:
        self.cancelled_ids.append(order_id)
        if order_id in self._known:
            del self._known[order_id]
            return True
        return False

    def cancel_all_for_position(self, position_id: str) -> int:
        self.cancel_all_calls.append(position_id)
        n = 0
        for oid, order in list(self._known.items()):
            if getattr(order, "position_id", None) == position_id:
                del self._known[oid]
                n += 1
        return n


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "cache"
    root.mkdir()
    monkeypatch.setattr("tradinglab.disk_cache._cache_dir", lambda: root)
    monkeypatch.setattr(audit_mod, "_cache_dir", lambda: root)
    return root / "exits" / "audit"


def _signal(
    *,
    kind: ExitOrderKind = ExitOrderKind.MARKET,
    side: OrderSide = OrderSide.SELL,
    qty: float = 1.0,
    price: float | None = None,
    limit_price: float | None = None,
    position_id: str = "p1",
    leg_id: str = "l1",
    label: str = "",
) -> ExitSignal:
    return ExitSignal.new(
        strategy_id="s1",
        position_id=position_id,
        leg_id=leg_id,
        trigger_id="t1",
        kind=kind,
        side=side,
        qty=qty,
        price=price,
        limit_price=limit_price,
        label=label,
    )


# ---------------------------------------------------------------------------
# ExitSignal basics
# ---------------------------------------------------------------------------


def test_exit_signal_new_assigns_unique_id() -> None:
    s1 = _signal()
    s2 = _signal()
    assert s1.id != s2.id


def test_exit_signal_is_frozen() -> None:
    s = _signal()
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        s.qty = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# PaperBrokerSink translation
# ---------------------------------------------------------------------------


def test_paper_broker_sink_translates_market() -> None:
    """Skip if the real PaperOrderKind enum isn't available yet."""
    pytest.importorskip("tradinglab.exits.paper_engine")
    engine = _FakePaperEngine()
    sink = PaperBrokerSink(engine)
    sig = _signal(kind=ExitOrderKind.MARKET, qty=10.0)
    order_id = sink.submit(sig)
    assert order_id == "engine-0"
    submitted = engine.submitted[0]
    assert submitted.position_id == sig.position_id
    assert submitted.qty == 10.0
    # PaperOrderKind.MARKET enum value
    assert submitted.kind.value == "market"


def test_paper_broker_sink_translates_limit_with_price() -> None:
    pytest.importorskip("tradinglab.exits.paper_engine")
    engine = _FakePaperEngine()
    sink = PaperBrokerSink(engine)
    sig = _signal(kind=ExitOrderKind.LIMIT, qty=2.0, price=180.0)
    sink.submit(sig)
    submitted = engine.submitted[0]
    assert submitted.price == 180.0
    assert submitted.kind.value == "limit"


def test_paper_broker_sink_translates_stop_limit() -> None:
    pytest.importorskip("tradinglab.exits.paper_engine")
    engine = _FakePaperEngine()
    sink = PaperBrokerSink(engine)
    sig = _signal(kind=ExitOrderKind.STOP_LIMIT, price=180.0, limit_price=179.5)
    sink.submit(sig)
    submitted = engine.submitted[0]
    assert submitted.price == 180.0
    assert submitted.limit_price == 179.5
    assert submitted.kind.value == "stop_limit"


def test_paper_broker_sink_records_per_position_working_ids() -> None:
    pytest.importorskip("tradinglab.exits.paper_engine")
    engine = _FakePaperEngine()
    sink = PaperBrokerSink(engine)
    s1 = _signal(position_id="p1", leg_id="leg-stop")
    s2 = _signal(position_id="p1", leg_id="leg-target")
    s3 = _signal(position_id="p2", leg_id="leg-stop")
    sink.submit(s1)
    sink.submit(s2)
    sink.submit(s3)
    assert sorted(sink.working_order_ids_for_position("p1")) == ["engine-0", "engine-1"]
    assert sink.working_order_ids_for_position("p2") == ["engine-2"]
    assert sink.working_order_ids_for_position("p99") == []


def test_paper_broker_sink_cancel_drops_bookkeeping() -> None:
    pytest.importorskip("tradinglab.exits.paper_engine")
    engine = _FakePaperEngine()
    sink = PaperBrokerSink(engine)
    s = _signal(position_id="p1")
    oid = sink.submit(s)
    assert sink.cancel(oid) is True
    assert sink.working_order_ids_for_position("p1") == []
    # Cancelling a non-engine id is a no-op (False)
    assert sink.cancel("nonexistent") is False


def test_paper_broker_sink_cancel_all_for_position() -> None:
    pytest.importorskip("tradinglab.exits.paper_engine")
    engine = _FakePaperEngine()
    sink = PaperBrokerSink(engine)
    sink.submit(_signal(position_id="p1", leg_id="a"))
    sink.submit(_signal(position_id="p1", leg_id="b"))
    sink.submit(_signal(position_id="p2", leg_id="c"))
    n = sink.cancel_all_for_position("p1")
    assert n == 2
    assert sink.working_order_ids_for_position("p1") == []
    assert sink.working_order_ids_for_position("p2") == ["engine-2"]


def test_paper_broker_sink_methods_require_tk_thread() -> None:
    pytest.importorskip("tradinglab.exits.paper_engine")
    engine = _FakePaperEngine()
    sink = PaperBrokerSink(engine)
    captured: list[BaseException] = []

    def worker() -> None:
        try:
            sink.submit(_signal())
        except BaseException as exc:
            captured.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=2.0)
    assert len(captured) == 1
    assert isinstance(captured[0], TkThreadViolation)


# ---------------------------------------------------------------------------
# ManualPaperSink behavior
# ---------------------------------------------------------------------------


def test_manual_sink_submit_emits_event() -> None:
    sink = ManualPaperSink()
    events: list[ManualSignalEvent] = []
    sink.subscribe(events.append)

    sig = _signal()
    oid = sink.submit(sig)
    assert oid.startswith("manual-")
    assert len(events) == 1
    assert events[0].kind == "submitted"
    assert events[0].order_id == oid
    assert events[0].signal is sig


def test_manual_sink_unsubscribe_handle_works() -> None:
    sink = ManualPaperSink()
    events: list[ManualSignalEvent] = []
    unsub = sink.subscribe(events.append)
    sink.submit(_signal())
    unsub()
    sink.submit(_signal())
    assert len(events) == 1


def test_manual_sink_subscriber_exception_is_isolated() -> None:
    sink = ManualPaperSink()
    received: list[ManualSignalEvent] = []

    def bad(_e: ManualSignalEvent) -> None:
        raise RuntimeError("boom")

    sink.subscribe(bad)
    sink.subscribe(received.append)
    sink.submit(_signal())
    assert len(received) == 1


def test_manual_sink_cancel_clears_working_set() -> None:
    sink = ManualPaperSink()
    sig = _signal(position_id="p1")
    oid = sink.submit(sig)
    assert sink.working_order_ids_for_position("p1") == [oid]
    assert sink.cancel(oid) is True
    assert sink.working_order_ids_for_position("p1") == []
    # Cancelling unknown id is False (idempotent contract)
    assert sink.cancel(oid) is False


def test_manual_sink_cancel_all_for_position() -> None:
    sink = ManualPaperSink()
    sink.submit(_signal(position_id="p1"))
    sink.submit(_signal(position_id="p1"))
    sink.submit(_signal(position_id="p2"))
    assert sink.cancel_all_for_position("p1") == 2
    assert sink.working_order_ids_for_position("p1") == []
    assert len(sink.working_order_ids_for_position("p2")) == 1


def test_manual_sink_acknowledge_fill_is_distinct_from_cancel(audit_root: Path) -> None:
    audit = AuditLog()
    sink = ManualPaperSink(audit=audit)
    events: list[ManualSignalEvent] = []
    sink.subscribe(events.append)

    sig = _signal()
    oid = sink.submit(sig)
    sink.acknowledge_fill(oid)
    audit.close()

    kinds = [e.kind for e in events]
    assert kinds == ["submitted", "ack-fill"]
    # Audit log records both: submit + fill
    records = audit.read_date(audit.list_dates()[0])
    record_kinds = [r["kind"] for r in records]
    assert record_kinds == ["submit", "fill"]


def test_manual_sink_acknowledge_fill_unknown_id_returns_false() -> None:
    sink = ManualPaperSink()
    assert sink.acknowledge_fill("manual-deadbeef") is False


def test_manual_sink_audit_disabled_path(audit_root: Path) -> None:
    """Sink works without an audit log; events still emitted."""
    sink = ManualPaperSink(audit=None)
    events: list[ManualSignalEvent] = []
    sink.subscribe(events.append)
    oid = sink.submit(_signal())
    assert sink.cancel(oid) is True
    assert [e.kind for e in events] == ["submitted", "cancelled"]


def test_manual_sink_methods_require_tk_thread() -> None:
    sink = ManualPaperSink()
    captured: list[BaseException] = []

    def worker() -> None:
        try:
            sink.submit(_signal())
        except BaseException as exc:
            captured.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=2.0)
    assert len(captured) == 1
    assert isinstance(captured[0], TkThreadViolation)


def test_manual_sink_working_order_ids_is_thread_safe() -> None:
    """Read-only API should NOT raise from a worker thread."""
    sink = ManualPaperSink()
    with tk_thread_check_disabled():
        sink.submit(_signal(position_id="p1"))

    captured: list[BaseException] = []
    result: list[list[str]] = []

    def worker() -> None:
        try:
            result.append(sink.working_order_ids_for_position("p1"))
        except BaseException as exc:
            captured.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=2.0)
    assert captured == []
    assert len(result[0]) == 1


# ---------------------------------------------------------------------------
# SchwabTraderSink stub
# ---------------------------------------------------------------------------


def test_schwab_sink_submit_raises_with_audit(audit_root: Path) -> None:
    audit = AuditLog()
    sink = SchwabTraderSink(audit=audit)
    with pytest.raises(SchwabTraderNotConfigured):
        sink.submit(_signal())
    audit.close()
    records = audit.read_date(audit.list_dates()[0])
    assert records[0]["meta"]["sink"] == "schwab"
    assert records[0]["meta"]["status"] == "not_configured"


def test_schwab_sink_cancel_raises() -> None:
    sink = SchwabTraderSink()
    with pytest.raises(SchwabTraderNotConfigured):
        sink.cancel("anything")


def test_schwab_sink_cancel_all_raises() -> None:
    sink = SchwabTraderSink()
    with pytest.raises(SchwabTraderNotConfigured):
        sink.cancel_all_for_position("p1")


def test_schwab_sink_working_orders_is_empty() -> None:
    sink = SchwabTraderSink()
    assert sink.working_order_ids_for_position("p1") == []


def test_schwab_sink_methods_require_tk_thread() -> None:
    sink = SchwabTraderSink()
    captured: list[BaseException] = []

    def worker() -> None:
        try:
            sink.submit(_signal())
        except BaseException as exc:
            captured.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=2.0)
    assert len(captured) == 1
    assert isinstance(captured[0], TkThreadViolation)
