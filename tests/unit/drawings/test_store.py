"""Unit tests for :class:`tradinglab.drawings.store.DrawingStore`."""
from __future__ import annotations

import pytest

from tradinglab.core.thread_guard import TkThreadViolation
from tradinglab.drawings import (
    Drawing,
    DrawingStore,
    make_hline_drawing,
)

# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

def _mk(ticker: str = "AMD", price: float = 100.0, **kw) -> Drawing:
    return make_hline_drawing(ticker, price, **kw)


def _events(store: DrawingStore) -> list[tuple]:
    """Subscribe + return the collected event list (mutable)."""
    out: list[tuple] = []

    def _cb(kind, ticker, drawing):
        out.append((kind, ticker, None if drawing is None else drawing.id))

    store.subscribe(_cb)
    return out


# ---------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------

@pytest.fixture
def store():
    # autosave=False so the store doesn't touch the filesystem
    # during the in-memory tests. Persistence is exercised
    # separately in test_persistence.py.
    return DrawingStore(autosave=False)


# ---------------------------------------------------------------
# add / list / get / len
# ---------------------------------------------------------------

class TestAddAndAccessors:
    def test_add_and_list(self, store):
        d = _mk()
        store.add(d)
        listed = store.list("AMD")
        assert len(listed) == 1
        assert listed[0].id == d.id

    def test_add_normalizes_ticker_bucket(self, store):
        d = _mk(ticker="amd")  # already normalized by factory
        store.add(d)
        assert store.list("amd") == store.list("AMD")
        # Returned drawing has normalized ticker
        assert store.list("AMD")[0].ticker == "AMD"

    def test_add_drawing_with_stale_ticker_rebuckets(self, store):
        # Caller built a Drawing instance directly with a
        # lowercase ticker (bypassing the factory).
        d = Drawing(kind="hline", id="x", ticker="amd",
                    price=1.0, color="#000", width=1.0, style="solid")
        store.add(d)
        assert store.list("AMD")[0].ticker == "AMD"
        assert store.list("amd") == store.list("AMD")

    def test_get(self, store):
        d = _mk()
        store.add(d)
        result = store.get(d.id)
        assert result is not None
        ticker, found = result
        assert ticker == "AMD"
        assert found.id == d.id

    def test_get_missing(self, store):
        assert store.get("no-such-id") is None

    def test_len(self, store):
        assert len(store) == 0
        store.add(_mk("AMD", 1.0))
        store.add(_mk("AMD", 2.0))
        store.add(_mk("MSFT", 3.0))
        assert len(store) == 3

    def test_tickers(self, store):
        store.add(_mk("AMD", 1.0))
        store.add(_mk("MSFT", 1.0))
        assert set(store.tickers()) == {"AMD", "MSFT"}

    def test_all_returns_shallow_copy(self, store):
        store.add(_mk("AMD", 1.0))
        snap = store.all()
        snap["AMD"].clear()
        # Internal state intact.
        assert len(store.list("AMD")) == 1

    def test_list_returns_shallow_copy(self, store):
        store.add(_mk("AMD", 1.0))
        snap = store.list("AMD")
        snap.clear()
        assert len(store.list("AMD")) == 1


# ---------------------------------------------------------------
# events
# ---------------------------------------------------------------

class TestSubscriberEvents:
    def test_add_fires(self, store):
        events = _events(store)
        d = _mk()
        store.add(d)
        assert events == [("add", "AMD", d.id)]

    def test_remove_fires(self, store):
        d = _mk()
        store.add(d)
        events = _events(store)
        assert store.remove(d.id) is True
        assert events == [("remove", "AMD", d.id)]

    def test_remove_missing_no_event(self, store):
        events = _events(store)
        assert store.remove("ghost") is False
        assert events == []

    def test_update_fires(self, store):
        d = _mk()
        store.add(d)
        events = _events(store)
        nd = store.update(d.id, price=200.0)
        assert nd is not None
        assert nd.price == 200.0
        assert events == [("update", "AMD", d.id)]

    def test_update_missing_no_event(self, store):
        events = _events(store)
        assert store.update("ghost", price=1.0) is None
        assert events == []

    def test_update_moves_ticker(self, store):
        d = _mk("AMD", 1.0)
        store.add(d)
        events = _events(store)
        nd = store.update(d.id, ticker="MSFT")
        assert nd is not None
        assert nd.ticker == "MSFT"
        # Event reports the new ticker.
        assert events == [("update", "MSFT", d.id)]
        # Buckets updated.
        assert store.list("AMD") == []
        assert len(store.list("MSFT")) == 1

    def test_clear_symbol_fires_once(self, store):
        store.add(_mk("AMD", 1.0))
        store.add(_mk("AMD", 2.0))
        store.add(_mk("MSFT", 1.0))
        events = _events(store)
        removed = store.clear_symbol("AMD")
        assert removed == 2
        assert events == [("clear_symbol", "AMD", None)]
        assert store.list("AMD") == []
        assert len(store.list("MSFT")) == 1

    def test_clear_symbol_empty_no_event(self, store):
        events = _events(store)
        assert store.clear_symbol("NONE") == 0
        assert events == []

    def test_clear_all_fires_once(self, store):
        store.add(_mk("AMD", 1.0))
        store.add(_mk("MSFT", 1.0))
        events = _events(store)
        removed = store.clear_all()
        assert removed == 2
        assert events == [("clear_all", None, None)]
        assert len(store) == 0

    def test_clear_all_empty_no_event(self, store):
        events = _events(store)
        assert store.clear_all() == 0
        assert events == []

    def test_replace_all_fires_loaded(self, store):
        events = _events(store)
        d = _mk("AMD", 1.0)
        store.replace_all({"AMD": [d]})
        assert events == [("loaded", None, None)]
        assert len(store) == 1

    def test_replace_all_normalizes_buckets(self, store):
        d = _mk("amd", 1.0)  # factory already uppercases the drawing
        store.replace_all({"amd": [d]})
        assert store.list("AMD")[0].id == d.id
        assert "amd" not in store.all()

    def test_replace_all_drops_empty_buckets(self, store):
        store.replace_all({"AMD": [], "MSFT": [_mk("MSFT", 1.0)]})
        assert "AMD" not in store.all()
        assert "MSFT" in store.all()


class TestSubscribeUnsubscribe:
    def test_unsubscribe(self, store):
        seen: list[tuple] = []
        unsub = store.subscribe(
            lambda k, t, d: seen.append((k, t, None if d is None else d.id)))
        d = _mk()
        store.add(d)
        unsub()
        store.remove(d.id)
        assert seen == [("add", "AMD", d.id)]

    def test_unsubscribe_idempotent(self, store):
        unsub = store.subscribe(lambda *_: None)
        unsub()
        unsub()  # should not raise

    def test_broken_subscriber_doesnt_break_chain(self, store):
        ok_seen: list[str] = []

        def _broken(k, t, d):
            raise RuntimeError("oops")

        def _ok(k, t, d):
            ok_seen.append(k)

        store.subscribe(_broken)
        store.subscribe(_ok)
        store.add(_mk())
        assert ok_seen == ["add"]


# ---------------------------------------------------------------
# scheduler / autosave
# ---------------------------------------------------------------

class TestAutosaveCoalescing:
    def test_scheduler_coalesces_writes(self, tmp_path, monkeypatch):
        # Route writes through a temp dir so we don't touch the
        # user's actual data folder.
        from tradinglab.drawings import store as store_mod
        monkeypatch.setattr(
            store_mod, "drawings_file_path",
            lambda: tmp_path / "drawings.json")

        scheduled = []

        def _sched(fn):
            scheduled.append(fn)

        s = DrawingStore(scheduler=_sched, autosave=True)
        s.add(_mk("AMD", 1.0))
        s.add(_mk("AMD", 2.0))
        s.add(_mk("MSFT", 1.0))

        # Only ONE write scheduled, no matter how many mutations.
        assert len(scheduled) == 1
        # And nothing on disk until the scheduler fires.
        assert not (tmp_path / "drawings.json").exists()

        scheduled[0]()
        assert (tmp_path / "drawings.json").is_file()

    def test_no_scheduler_writes_synchronously(self, tmp_path, monkeypatch):
        from tradinglab.drawings import store as store_mod
        monkeypatch.setattr(
            store_mod, "drawings_file_path",
            lambda: tmp_path / "drawings.json")

        s = DrawingStore(autosave=True)  # no scheduler
        s.add(_mk("AMD", 1.0))
        assert (tmp_path / "drawings.json").is_file()

    def test_autosave_false_skips_writes(self, tmp_path, monkeypatch):
        from tradinglab.drawings import store as store_mod
        monkeypatch.setattr(
            store_mod, "drawings_file_path",
            lambda: tmp_path / "drawings.json")

        s = DrawingStore(autosave=False)
        s.add(_mk("AMD", 1.0))
        s.add(_mk("MSFT", 1.0))
        assert not (tmp_path / "drawings.json").exists()

        # Explicit flush works.
        s.flush()
        assert (tmp_path / "drawings.json").is_file()

    def test_replace_all_does_not_save(self, tmp_path, monkeypatch):
        from tradinglab.drawings import store as store_mod
        monkeypatch.setattr(
            store_mod, "drawings_file_path",
            lambda: tmp_path / "drawings.json")

        s = DrawingStore(autosave=True)
        s.replace_all({"AMD": [_mk("AMD", 1.0)]})
        # No file written — replace_all is the load path, not a save.
        assert not (tmp_path / "drawings.json").exists()

    def test_broken_scheduler_falls_back_to_sync(self, tmp_path, monkeypatch):
        from tradinglab.drawings import store as store_mod
        monkeypatch.setattr(
            store_mod, "drawings_file_path",
            lambda: tmp_path / "drawings.json")

        def _bad_sched(fn):
            raise RuntimeError("no mainloop")

        s = DrawingStore(scheduler=_bad_sched, autosave=True)
        s.add(_mk("AMD", 1.0))
        # Fell back to sync flush.
        assert (tmp_path / "drawings.json").is_file()


# ---------------------------------------------------------------
# duplicate id rejection (audit ``drawing-duplicate-id``)
# ---------------------------------------------------------------

class TestAddRejectsDuplicateId:
    def test_add_same_id_twice_raises(self, store):
        d1 = _mk("AMD", 100.0, drawing_id="dup-id")
        store.add(d1)
        d2 = _mk("AMD", 200.0, drawing_id="dup-id")
        with pytest.raises(ValueError, match="duplicate drawing id"):
            store.add(d2)
        # First survives unchanged.
        assert len(store.list("AMD")) == 1
        assert store.list("AMD")[0].price == 100.0

    def test_add_same_id_cross_ticker_raises(self, store):
        d1 = _mk("AMD", 100.0, drawing_id="cross-dup")
        store.add(d1)
        d2 = _mk("MSFT", 100.0, drawing_id="cross-dup")
        with pytest.raises(ValueError, match="duplicate drawing id"):
            store.add(d2)
        assert len(store.list("AMD")) == 1
        assert "MSFT" not in store._by_ticker

    def test_distinct_ids_no_collision(self, store):
        store.add(_mk("AMD", 100.0, drawing_id="a"))
        store.add(_mk("AMD", 200.0, drawing_id="b"))
        assert len(store.list("AMD")) == 2

    def test_no_event_fired_on_rejected_add(self, store):
        events = _events(store)
        d1 = _mk("AMD", 100.0, drawing_id="dup")
        store.add(d1)
        events.clear()
        d2 = _mk("AMD", 200.0, drawing_id="dup")
        with pytest.raises(ValueError):
            store.add(d2)
        # No "add" event for the rejected drawing.
        assert events == []

    def test_no_save_scheduled_on_rejected_add(self, tmp_path, monkeypatch):
        from tradinglab.drawings import store as store_mod
        monkeypatch.setattr(
            store_mod, "drawings_file_path",
            lambda: tmp_path / "drawings.json")

        scheduled = []

        def _sched(fn):
            scheduled.append(fn)

        s = DrawingStore(scheduler=_sched, autosave=True)
        s.add(_mk("AMD", 1.0, drawing_id="dup"))
        scheduled.clear()
        with pytest.raises(ValueError):
            s.add(_mk("AMD", 2.0, drawing_id="dup"))
        # The failed add must not schedule a redundant save.
        assert scheduled == []


# ---------------------------------------------------------------
# replace_all dedupes (audit ``drawing-duplicate-id`` belt-and-braces)
# ---------------------------------------------------------------

class TestReplaceAllDeduplicates:
    def test_within_same_ticker_first_wins(self, store):
        d1 = _mk("AMD", 100.0, drawing_id="dup")
        d2 = _mk("AMD", 200.0, drawing_id="dup")
        store.replace_all({"AMD": [d1, d2]})
        assert len(store.list("AMD")) == 1
        assert store.list("AMD")[0].price == 100.0

    def test_across_tickers_first_wins(self, store):
        d1 = _mk("AMD", 1.0, drawing_id="cross")
        d2 = _mk("MSFT", 2.0, drawing_id="cross")
        store.replace_all({"AMD": [d1], "MSFT": [d2]})
        # AMD bucket has the survivor; MSFT got the dup dropped → empty → omitted.
        assert "AMD" in store._by_ticker
        assert "MSFT" not in store._by_ticker

    def test_distinct_ids_unaffected(self, store):
        d1 = _mk("AMD", 1.0, drawing_id="a")
        d2 = _mk("AMD", 2.0, drawing_id="b")
        store.replace_all({"AMD": [d1, d2]})
        assert [d.id for d in store.list("AMD")] == ["a", "b"]


# ---------------------------------------------------------------
# Thread-safety enforcement (audit ``drawing-thread-safety``)
# ---------------------------------------------------------------

class TestMutatingMethodsRejectWorkerThread:
    """All mutating methods must raise :class:`TkThreadViolation` when
    invoked off the Tk main thread. Read-only methods are not
    checked.
    """

    @staticmethod
    def _run_in_thread(fn):
        """Run ``fn`` in a worker thread and return any exception."""
        import threading
        captured: dict[str, BaseException | None] = {"exc": None}

        def _target():
            try:
                fn()
            except BaseException as e:  # noqa: BLE001
                captured["exc"] = e

        t = threading.Thread(target=_target, name="worker-thread-x")
        t.start()
        t.join(timeout=2.0)
        assert not t.is_alive(), "worker thread hung"
        return captured["exc"]

    def test_add_raises_off_main(self, store):
        d = _mk("AMD", 1.0)
        exc = self._run_in_thread(lambda: store.add(d))
        assert isinstance(exc, TkThreadViolation)
        assert "Tk main thread" in str(exc)
        assert "DrawingStore.add" in str(exc)

    def test_remove_raises_off_main(self, store):
        d = _mk("AMD", 1.0)
        store.add(d)  # on main thread (fine)
        exc = self._run_in_thread(lambda: store.remove(d.id))
        assert isinstance(exc, TkThreadViolation)
        assert "DrawingStore.remove" in str(exc)
        # State not corrupted by the rejected call.
        assert len(store.list("AMD")) == 1

    def test_update_raises_off_main(self, store):
        d = _mk("AMD", 1.0)
        store.add(d)
        exc = self._run_in_thread(
            lambda: store.update(d.id, price=99.0),
        )
        assert isinstance(exc, TkThreadViolation)
        assert "DrawingStore.update" in str(exc)
        # Price not changed.
        assert store.list("AMD")[0].price == 1.0

    def test_clear_symbol_raises_off_main(self, store):
        store.add(_mk("AMD", 1.0))
        exc = self._run_in_thread(lambda: store.clear_symbol("AMD"))
        assert isinstance(exc, TkThreadViolation)
        assert "DrawingStore.clear_symbol" in str(exc)
        assert len(store.list("AMD")) == 1

    def test_clear_all_raises_off_main(self, store):
        store.add(_mk("AMD", 1.0))
        exc = self._run_in_thread(store.clear_all)
        assert isinstance(exc, TkThreadViolation)
        assert "DrawingStore.clear_all" in str(exc)
        assert len(store) == 1

    def test_replace_all_raises_off_main(self, store):
        d = _mk("AMD", 1.0)
        exc = self._run_in_thread(
            lambda: store.replace_all({"AMD": [d]}),
        )
        assert isinstance(exc, TkThreadViolation)
        assert "DrawingStore.replace_all" in str(exc)
        # Store still empty.
        assert len(store) == 0

    def test_read_only_methods_allowed_off_main(self, store):
        d = _mk("AMD", 1.0)
        store.add(d)  # main-thread setup

        # list, get, len: must NOT raise off main.
        exc = self._run_in_thread(lambda: store.list("AMD"))
        assert exc is None
        exc = self._run_in_thread(lambda: store.get(d.id))
        assert exc is None
        exc = self._run_in_thread(lambda: len(store))
        assert exc is None

    def test_subscribe_allowed_off_main(self, store):
        # Subscribing isn't a state mutation that needs the
        # main-thread guard; the subscriber list is only walked
        # during ``_notify`` (which fires on the mutating thread).
        exc = self._run_in_thread(
            lambda: store.subscribe(lambda *_: None),
        )
        assert exc is None

    def test_main_thread_calls_unchanged(self, store):
        # Sanity: regular main-thread mutation flow still works.
        store.add(_mk("AMD", 1.0))
        store.update(store.list("AMD")[0].id, price=2.0)
        assert store.list("AMD")[0].price == 2.0
        store.clear_all()
        assert len(store) == 0

