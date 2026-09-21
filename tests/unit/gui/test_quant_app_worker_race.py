"""Quant fetch worker race: snapshot-at-handoff + Tk-thread apply.

Regression suite for the P1 "``_fetch_quant_last`` worker-thread race":

``_fetch_quant_last`` runs on the ``_fetch_executor`` worker thread and used
to call ``_apply_watchlist_snapshot_from_bars`` directly — mutating the
shared ``_watchlist_snapshot`` (``setdefault`` + in-place cell updates)
while the Tk thread reads it in ``_paint_quant_last_values`` /
``_populate_watchlist_tab`` / ``_watchlist_cell_text``, and reading
``_full_cache`` / queueing a refresh off-thread. It now posts
``("quant_snapshot", (symbol, src, interval, bars))`` on ``_worker_inbox``
(bars snapshotted with ``list(bars)`` at handoff); the Tk-thread
``_drain_worker_inbox`` applies it via ``_apply_quant_snapshot_from_bars``.
On the Tk thread itself (synchronous test shims) the apply runs directly,
matching ``_preload_one_last``'s fast-path.

The tests below capture the downstream order effects: the worker must never
apply off-thread, the posted handoff must precede the ``("stash", …)``
item, the debounced refresh the apply queues must reach the next drain,
and the posted bars must be insulated from later reuse of the fetcher's
buffer.

See ``gui/quant_app.spec.md`` and ``gui/polling.spec.md``.
"""
from __future__ import annotations

import queue
import threading
from typing import Any

import pytest

from tradinglab.gui import polling as _polling
from tradinglab.gui.quant_app import QUANT_LAST_INTERVAL, QuantAppMixin
from tradinglab.gui.watchlist_tab import WatchlistTabMixin
from tradinglab.models import Candle


def _daily_bars(state: int, n: int = 10) -> list[Candle]:
    """``n`` daily bars whose closes identify ``state``: ``1000*state + i``."""
    import datetime as _dt

    out = []
    base = _dt.datetime(2026, 6, 8, tzinfo=_dt.timezone.utc)
    for i in range(n):
        px = 1000.0 * state + i
        out.append(Candle(date=base + _dt.timedelta(days=i),
                          open=px, high=px + 0.5, low=px - 0.5,
                          close=px, volume=1000))
    return out


def _state_closes(state: int, n: int = 10) -> list[float]:
    return [1000.0 * state + i for i in range(n)]


class _RecordingFetcher:
    """Stub fetcher returning a SHARED buffer, like a vendor reusing one."""

    def __init__(self, bars: list[Candle]) -> None:
        self.bars = bars
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, symbol: str, interval: str) -> list[Candle]:
        self.calls.append((threading.current_thread().name, symbol, interval))
        return self.bars


class _App(QuantAppMixin, WatchlistTabMixin):
    """Minimal harness satisfying both mixin attribute contracts (no Tk)."""

    def __init__(self) -> None:
        self._watchlist_snapshot: dict[str, dict[str, Any]] = {}
        self._full_cache: dict[tuple, list[Candle]] = {}
        self._worker_inbox: queue.Queue = queue.Queue()
        self._quant_fetch_inflight: set[str] = set()
        self._sandbox = None
        self.apply_threads: list[str] = []
        self.stashed: list[tuple] = []
        self.queued_refresh_calls = 0

    # -- mixin seams -------------------------------------------------
    def _is_sandbox_active(self) -> bool:
        return False

    def _queue_watchlist_snapshot_refresh(self) -> None:
        self.queued_refresh_calls += 1
        try:
            self._worker_inbox.put_nowait(("refresh", None))
        except Exception:  # noqa: BLE001
            pass

    def _stash_full_cache(self, key, bars) -> None:  # noqa: ANN001, ANN202
        self.stashed.append((key, bars))

    def _apply_watchlist_snapshot_from_bars(  # noqa: ANN202
        self, ticker, src, itv, bars, **kwargs  # noqa: ANN001, ANN003
    ):
        # Record the calling thread — the race under test — then run the
        # real seam so the Tk-thread apply path is genuinely exercised.
        self.apply_threads.append(threading.current_thread().name)
        return WatchlistTabMixin._apply_watchlist_snapshot_from_bars(
            self, ticker, src, itv, bars, **kwargs)


@pytest.fixture()
def _patch_data_sources(monkeypatch):
    """Install the recording fetcher under a stub source name."""
    fetcher = _RecordingFetcher(_daily_bars(7))
    monkeypatch.setattr("tradinglab.data.DATA_SOURCES", {"testsrc": fetcher})
    return fetcher


def _run_worker(app: _App, symbol: str, src: str) -> None:
    """Run ``_fetch_quant_last`` on a real worker thread."""
    errors: list[BaseException] = []

    def _target() -> None:
        try:
            app._fetch_quant_last(symbol, src)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=_target, name="quant-worker")
    t.start()
    t.join(timeout=30.0)
    assert not t.is_alive(), "quant worker hung"
    assert not errors, f"worker raised: {errors!r}"


def _drain_all(inbox: queue.Queue) -> list[tuple]:
    out = []
    try:
        while True:
            out.append(inbox.get_nowait())
    except queue.Empty:
        pass
    return out


# ---------------------------------------------------------------------------
# 1. The worker never applies off-thread; results travel via the inbox
# ---------------------------------------------------------------------------


def test_worker_posts_handoff_and_never_applies_off_thread(_patch_data_sources):
    """The race: the worker used to call the snapshot seam directly,
    mutating ``_watchlist_snapshot`` off the Tk thread. It must now post
    ``("quant_snapshot", …)`` and leave the shared snapshot untouched."""
    app = _App()
    sentinel = {"last": 42.0}
    app._watchlist_snapshot["^VIX"] = sentinel

    _run_worker(app, "^VIX", "testsrc")

    # The seam was never entered off-thread...
    assert app.apply_threads == []
    # ...so the pre-existing snapshot dict is the identical, untouched object.
    assert app._watchlist_snapshot["^VIX"] is sentinel
    assert sentinel == {"last": 42.0}
    # The handoff is on the inbox AHEAD of the cache stash (the same
    # relative order the old direct calls had), bars snapshotted at handoff.
    items = _drain_all(app._worker_inbox)
    assert [kind for kind, _ in items] == ["quant_snapshot", "stash"]
    sym, src, itv, bars = items[0][1]
    assert (sym, src, itv) == ("^VIX", "testsrc", QUANT_LAST_INTERVAL)
    assert [c.close for c in bars] == _state_closes(7)
    # The fetcher ran on the worker thread with the daily interval.
    fetcher = _patch_data_sources
    assert fetcher.calls == [("quant-worker", "^VIX", QUANT_LAST_INTERVAL)]
    # The inflight marker is released even though the snapshot went via inbox.
    assert "^VIX" not in app._quant_fetch_inflight


def test_main_thread_call_applies_directly_without_the_inbox(_patch_data_sources):
    """Synchronous test shims call the worker body on the Tk thread; the
    apply then runs directly (``_preload_one_last``'s fast-path), not via
    the inbox."""
    app = _App()

    app._fetch_quant_last("^VIX", "testsrc")

    assert app.apply_threads == [threading.main_thread().name]
    assert app._watchlist_snapshot["^VIX"]["last"] == _state_closes(7)[-1]
    # No snapshot handoff on the inbox in the fast path — only the
    # debounced refresh the apply queued.
    assert [k for k, _ in _drain_all(app._worker_inbox)] == ["refresh"]
    assert len(app.stashed) == 1
    key, bars = app.stashed[0]
    assert key == ("testsrc", "^VIX", QUANT_LAST_INTERVAL)
    assert [c.close for c in bars] == _state_closes(7)


# ---------------------------------------------------------------------------
# 2. The shared snapshot is only ever touched on the Tk thread
# ---------------------------------------------------------------------------


def test_tk_thread_apply_derives_last_from_posted_bars(_patch_data_sources):
    """Applying the posted handoff on the Tk thread derives the Last/Change
    cells from the posted bars, preserving pre-existing snapshot keys."""
    app = _App()
    app._watchlist_snapshot["^VIX"] = {"note": "keep me"}

    _run_worker(app, "^VIX", "testsrc")
    payloads = [p for k, p in _drain_all(app._worker_inbox)
                if k == "quant_snapshot"]
    assert len(payloads) == 1

    # Tk-thread apply (the main thread in this harness).
    app._apply_quant_snapshot_from_bars(*payloads[0])

    assert app.apply_threads == [threading.main_thread().name]
    snap = app._watchlist_snapshot["^VIX"]
    assert snap["last"] == _state_closes(7)[-1]
    assert snap["_last_source"] == "daily"
    assert snap["change_1d"] == pytest.approx(1.0)
    assert snap["note"] == "keep me"
    # The apply queues the debounced watchlist repaint.
    assert app.queued_refresh_calls == 1


def test_posted_bars_are_insulated_from_later_buffer_reuse(_patch_data_sources):
    """The handoff snapshots the fetched bars: a vendor reusing its buffer
    after the post must not corrupt what the Tk thread later applies."""
    app = _App()
    fetcher = _patch_data_sources

    _run_worker(app, "^VIX", "testsrc")
    # The vendor reuses its buffer for the next symbol AFTER the handoff.
    fetcher.bars[:] = _daily_bars(99)

    payloads = [p for k, p in _drain_all(app._worker_inbox)
                if k == "quant_snapshot"]
    app._apply_quant_snapshot_from_bars(*payloads[0])

    assert app._watchlist_snapshot["^VIX"]["last"] == _state_closes(7)[-1]


# ---------------------------------------------------------------------------
# 3. End-to-end: worker → inbox → real Tk-thread drain → snapshot + stash
# ---------------------------------------------------------------------------


class _DrainHarness(_App, _polling.PollingMixin):
    """Combined harness wiring the real ``_drain_worker_inbox`` to the
    real ``_apply_quant_snapshot_from_bars`` (no Tk interpreter)."""

    def __init__(self) -> None:
        _App.__init__(self)
        self._after_jobs: set[str] = set()
        self.after_calls: list[tuple[int, Any]] = []
        self.scheduled_refresh_calls = 0

    def _schedule_watchlist_tab_refresh(self) -> None:
        self.scheduled_refresh_calls += 1

    def after(self, delay_ms: int, fn):  # noqa: ANN001, ANN202
        self.after_calls.append((delay_ms, fn))
        return f"job{len(self.after_calls)}"

    def after_cancel(self, jid: str) -> None:  # noqa: ARG002
        pass


def test_end_to_end_worker_inbox_drain_applies_quant_snapshot(_patch_data_sources):
    """Downstream order: worker → inbox → real Tk-thread drain → snapshot
    + stash land together; the debounced refresh the apply queued is
    dispatched by the NEXT drain (the drain is bounded) and the drain
    re-arms its 80ms tick."""
    h = _DrainHarness()

    _run_worker(h, "^VIX", "testsrc")

    # Tk-thread drain (the main thread in this harness).
    h._drain_worker_inbox()

    # The snapshot handoff applied on the Tk thread...
    assert h.apply_threads == [threading.main_thread().name]
    assert h._watchlist_snapshot["^VIX"]["last"] == _state_closes(7)[-1]
    # ...the bars were stashed...
    assert len(h.stashed) == 1
    assert h.stashed[0][0] == ("testsrc", "^VIX", QUANT_LAST_INTERVAL)
    # ...and the drain re-armed itself for the next tick.
    assert h.after_calls and h.after_calls[-1][0] == 80
    # The apply's debounced refresh was deferred past the bounded drain;
    # the next drain dispatches it to the watchlist repaint.
    assert h.queued_refresh_calls == 1
    assert h.scheduled_refresh_calls == 0
    h._drain_worker_inbox()
    assert h.scheduled_refresh_calls == 1
    assert h._worker_inbox.empty()
