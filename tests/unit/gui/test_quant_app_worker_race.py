"""Quant fetch worker race: detached results + Tk-thread apply.

Regression suite for the P1 "``_fetch_quant_last`` worker-thread race":

``_fetch_quant_last`` runs on the ``_fetch_executor`` worker thread and used
to call ``_apply_watchlist_snapshot_from_bars`` directly — mutating the
shared ``_watchlist_snapshot`` (``setdefault`` + in-place cell updates)
while the Tk thread reads it in ``_paint_quant_last_values`` /
``_populate_watchlist_tab`` / ``_watchlist_cell_text``, and reading
``_full_cache`` / queueing a refresh off-thread. It now posts
``("quant_result", (symbol, src, bars))`` on ``_worker_inbox``
(including copies of mutable Candle fields); the Tk-thread
``_drain_worker_inbox`` applies snapshot and cache via ``_apply_quant_fetch_result``.
On the Tk thread itself (synchronous test shims) the apply runs directly,
matching ``_preload_one_last``'s fast-path.

The tests below capture the downstream order effects: the worker must never
apply off-thread, snapshot and cache must land in the same drain,
the debounced refresh the apply queues must reach the next drain,
and the posted bars must be insulated from later reuse of the fetcher's
buffer.

See ``gui/quant_app.spec.md`` and ``gui/polling.spec.md``.
"""
from __future__ import annotations

import queue
import threading
from concurrent.futures import ThreadPoolExecutor
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
    app._quant_fetch_inflight.add(symbol)

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
    ``("quant_result", …)`` and leave the shared snapshot untouched."""
    app = _App()
    sentinel = {"last": 42.0}
    app._watchlist_snapshot["^VIX"] = sentinel

    _run_worker(app, "^VIX", "testsrc")

    # The seam was never entered off-thread...
    assert app.apply_threads == []
    # ...so the pre-existing snapshot dict is the identical, untouched object.
    assert app._watchlist_snapshot["^VIX"] is sentinel
    assert sentinel == {"last": 42.0}
    items = _drain_all(app._worker_inbox)
    assert [kind for kind, _ in items] == ["quant_result"]
    sym, src, bars = items[0][1]
    assert (sym, src) == ("^VIX", "testsrc")
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
    # No result handoff on the inbox in the fast path — only the
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
                if k == "quant_result"]
    assert len(payloads) == 1

    # Tk-thread apply (the main thread in this harness).
    app._apply_quant_fetch_result(*payloads[0])

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
                if k == "quant_result"]
    app._apply_quant_fetch_result(*payloads[0])

    assert app._watchlist_snapshot["^VIX"]["last"] == _state_closes(7)[-1]


# ---------------------------------------------------------------------------
# 3. End-to-end: worker → inbox → real Tk-thread drain → snapshot + stash
# ---------------------------------------------------------------------------


class _DrainHarness(_App, _polling.PollingMixin):
    """Combined harness wiring the real ``_drain_worker_inbox`` to the
    real ``_apply_quant_fetch_result`` (no Tk interpreter)."""

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


def test_one_result_applies_snapshot_and_cache_before_worker_continues(
    _patch_data_sources,
):
    """A drain between worker posts must not publish half a fetch result."""
    h = _DrainHarness()
    posted = threading.Event()
    resume = threading.Event()

    class _PausedInbox(queue.Queue):
        def put_nowait(self, item):
            super().put_nowait(item)
            if item[0] == "quant_result":
                posted.set()
                assert resume.wait(10), "Tk drain did not release the worker"

    h._worker_inbox = _PausedInbox()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(h._fetch_quant_last, "^VIX", "testsrc")
        try:
            assert posted.wait(10), "worker did not post its result"
            h._drain_worker_inbox()
            assert h._watchlist_snapshot["^VIX"]["last"] == _state_closes(7)[-1]
            assert len(h.stashed) == 1
            assert h.stashed[0][1][-1].close == _state_closes(7)[-1]
        finally:
            resume.set()
            future.result(timeout=10)


def test_result_detaches_mutable_candle_fields(_patch_data_sources):
    """A list copy alone still aliases every mutable Candle in the result."""
    h = _DrainHarness()
    fetcher = _patch_data_sources
    expected = [(c.date, c.close, c.volume) for c in fetcher.bars]

    _run_worker(h, "^VIX", "testsrc")
    for candle in fetcher.bars:
        candle.date = expected[0][0]
        candle.close = -999.0
        candle.volume = 0
    h._drain_worker_inbox()

    snap = h._watchlist_snapshot["^VIX"]
    assert snap["last"] == expected[-1][1]
    assert snap["change_1d"] == pytest.approx(1.0)
    assert snap["pct_1d"] == pytest.approx(100 / expected[-2][1])
    assert [(c.date, c.close, c.volume) for c in h.stashed[0][1]] == expected


def test_result_uses_newer_intraday_last_at_tk_apply(_patch_data_sources):
    """Fetched daily bars must not replace a newer intraday Last."""
    h = _DrainHarness()
    bars = _patch_data_sources.bars
    bars[-2].close, bars[-1].close = 200.0, 250.0
    _run_worker(h, "^VIX", "testsrc")
    h._watchlist_snapshot["^VIX"] = {
        "last": 300.0, "_last_day": bars[-1].date.date(),
        "_last_source": "intraday", "note": "keep me",
    }

    h._drain_worker_inbox()

    snap = h._watchlist_snapshot["^VIX"]
    assert (snap["last"], snap["change_1d"], snap["pct_1d"]) == (300.0, 100.0, 50.0)
    assert snap["_last_source"] == "intraday"
    assert snap["note"] == "keep me"


def test_result_uses_sandbox_clock_at_tk_apply(_patch_data_sources):
    """Starting replay before the drain must not expose future daily closes."""
    from types import SimpleNamespace

    h = _DrainHarness()
    bars = _patch_data_sources.bars
    _run_worker(h, "^VIX", "testsrc")
    h._is_sandbox_active = lambda: True
    h._sandbox = SimpleNamespace(
        clock_ts=lambda: int(bars[-2].date.timestamp()),
        current_session_date=lambda: bars[-2].date.date(),
    )
    h._watchlist_snapshot["^VIX"] = {
        "last": 7100.0, "_last_day": bars[-2].date.date(),
        "_last_source": "intraday",
    }

    h._drain_worker_inbox()

    snap = h._watchlist_snapshot["^VIX"]
    prior = bars[-3].close
    assert snap["last"] == 7100.0
    assert snap["change_1d"] == pytest.approx(7100.0 - prior)
    assert snap["pct_1d"] == pytest.approx((7100.0 - prior) / prior * 100)
    assert snap["_daily_change_tail"][-1] == (bars[-3].date.date(), prior)


# ---------------------------------------------------------------------------
# 4. Failure isolation: inbox-full, teardown, seam failure, drain failure
# ---------------------------------------------------------------------------


class _FailingResultInbox(queue.Queue):
    """Inbox that rejects the ``quant_result`` handoff (as if full) but
    accepts everything else."""

    def put_nowait(self, item) -> None:  # noqa: ANN001
        if item[0] == "quant_result":
            raise queue.Full("inbox full")
        super().put_nowait(item)


def test_worker_handoff_failure_is_logged_and_stash_still_posts(
    _patch_data_sources, caplog,
):
    """The ``quant_result`` handoff is best-effort: when the inbox
    rejects it, the worker logs the failure, still
    posts the ``("stash", …)`` item, and releases the inflight marker."""
    app = _App()
    app._worker_inbox = _FailingResultInbox()

    with caplog.at_level("DEBUG", logger="tradinglab.gui.quant_app"):
        _run_worker(app, "^VIX", "testsrc")

    assert "^VIX" not in app._quant_fetch_inflight
    assert "Quant result handoff failed" in caplog.text
    assert app.apply_threads == []
    items = _drain_all(app._worker_inbox)
    assert [kind for kind, _ in items] == ["stash"]
    key, bars = items[0][1]
    assert key == ("testsrc", "^VIX", QUANT_LAST_INTERVAL)
    assert [c.close for c in bars] == _state_closes(7)


def test_worker_releases_inflight_when_teardown_rejects_all_posts(
    _patch_data_sources, caplog,
):
    class _ClosedInbox(queue.Queue):
        def put_nowait(self, item):
            raise RuntimeError("application closed")

    app = _App()
    app._worker_inbox = _ClosedInbox()
    with caplog.at_level("DEBUG", logger="tradinglab.gui.quant_app"):
        _run_worker(app, "^VIX", "testsrc")

    assert "^VIX" not in app._quant_fetch_inflight
    assert app._watchlist_snapshot == {}
    assert app.stashed == []
    assert "Quant last fetch failed" in caplog.text


def test_apply_quant_result_logs_seam_failure_and_still_stashes(caplog):
    """``_apply_quant_fetch_result`` is a defensive Tk-thread seam:
    a raising ``_apply_watchlist_snapshot_from_bars`` must not propagate
    to the drain."""

    app = _App()

    def _boom(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise RuntimeError("seam exploded")

    app._apply_watchlist_snapshot_from_bars = _boom

    with caplog.at_level("DEBUG", logger="tradinglab.gui.quant_app"):
        app._apply_quant_fetch_result("^VIX", "testsrc", _daily_bars(7))

    assert app._watchlist_snapshot == {}
    assert len(app.stashed) == 1
    assert "Quant snapshot apply failed" in caplog.text


def test_drain_logs_quant_result_apply_failure_and_continues(caplog):
    """The Tk-thread drain applies the posted handoff defensively: a
    raising ``_apply_quant_fetch_result`` is logged and the drain
    still re-arms its tick."""
    h = _DrainHarness()

    def _boom(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise RuntimeError("apply exploded")

    h._apply_quant_fetch_result = _boom
    h._worker_inbox.put_nowait(
        ("quant_result", ("^VIX", "testsrc", _daily_bars(7))))
    h._worker_inbox.put_nowait(("stash", (("other", "TLT", "1d"), _daily_bars(1))))

    with caplog.at_level("DEBUG", logger="tradinglab.gui.polling"):
        h._drain_worker_inbox()

    assert h._worker_inbox.empty()
    assert len(h.stashed) == 1
    assert h.stashed[0][0] == ("other", "TLT", "1d")
    assert "Quant result apply failed" in caplog.text
    assert h.after_calls and h.after_calls[-1][0] == 80
