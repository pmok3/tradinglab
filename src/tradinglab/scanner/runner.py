"""Scanner runner: drive evaluation across a universe with per-tick semantics.

Pure layer between :mod:`scanner.engine` (single-symbol evaluation) and
the GUI (per-scan tabs + match tables). The runner has two
``(BarsBuffer, IndicatorMemo)``-source modes:

1. **Local-state path** (default, ``bars_registry=None``). The runner
   owns a persistent per-symbol :class:`BarsBuffer` and
   :class:`IndicatorMemo` keyed by symbol. On each tick, it
   *reconciles* each symbol's cached state against the latest candle
   list using a lightweight fingerprint (``(id, len, last_ts,
   last_close)``) — appending new bars when growth is contiguous,
   rebuilding on list replacement, shrink, or last-bar mutation.
   This is the slot-1 v1 behavior; every existing test exercises it.

2. **Registry path** (opt-in, ``bars_registry=BarsRegistry(...)``).
   The runner consults
   :class:`tradinglab.core.bars_registry.BarsRegistry` to acquire
   ``(bars, memo)`` pairs for ``(symbol, scan_interval)`` instead of
   building them locally. Symbols not yet present in the registry
   (lazy-load pending) are skipped silently — no row, no crash. This
   is the seam that lets the runner share its memos with the future
   ``ExitEvaluator`` and that lets cross-interval scans
   resolve indicator references via :class:`BarsRegistry.get_view`.

Across both modes the runner:

3. Dispatches **per-symbol** work units (not per-(scan,symbol)) onto a
   :class:`ThreadPoolExecutor`: one worker evaluates every saved scan
   for one symbol sequentially using a single shared
   :class:`EvaluationContext`. This both shares one ``Bars`` view per
   symbol/tick AND eliminates the pre-existing race on
   :class:`IndicatorMemo` (multiple ``(scan, symbol)`` futures could
   previously hit the same memo with no lock).
4. Tracks per-scan :class:`MatchHistory` so the GUI's "New" view can
   render edge-triggered matches without re-deriving from raw rows.
5. Tags each :class:`ScanResult` with the caller's ``tick_id`` so a
   late-arriving result from an old tick can be discarded by the GUI
   without ambiguity.
6. Exposes :meth:`stats` for benchmarking the per-tick reuse vs
   rebuild ratio (the seam we'll need once incremental indicators
   land).

Deliberately excluded
---------------------

- *Cooperative cancellation.* When a new tick arrives mid-evaluation,
  the runner does NOT try to interrupt in-flight tasks. Instead the
  GUI compares ``result.tick_id`` against its current tick and drops
  stale results at drain. Indicator computes are CPU-bound NumPy and
  finish in milliseconds; the cost of cancellation infrastructure is
  not justified for v1.
- *Thread safety of MatchHistory.* The runner serializes history
  updates on the calling thread (after futures complete), so the
  history dict is only ever mutated from one thread.
- *Concurrent run() calls.* The persistent ``_states`` (local-state
  path) and the registry's ``_memos`` (registry path) are NOT
  locked. Callers MUST serialize :meth:`run` calls per runner
  instance (today the GUI calls it from the Tk main thread only).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..core.bars_buffer import BarsBuffer
from ..models import Candle
from .engine import (
    EvaluationContext,
    IndicatorMemo,
    evaluate_field,
    evaluate_scan,
    make_context,
)
from .model import Condition, MatchEvidence, ScanDefinition

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass
class MatchRow:
    """One row in a scan's result table for the current tick."""

    symbol: str
    matched: bool | None               # True / False / None (insufficient data)
    values: dict[str, float | None]    # condition_id → LHS field value
    rank_value: float | None           # scan.rank_by, or None
    is_new: bool                          # edge-triggered: was not matched on prior tick
    error: str | None = None           # symbol-level evaluation error, if any
    is_forming: bool = False              # this row is from a forming (in-progress) bar; never sets is_new
    evidence: list[MatchEvidence] = field(default_factory=list)
    """Per-leaf within-last-N-bars match evidence collected during evaluation.

    Each entry pins the node (Condition or Group) that fired, how many
    bars back it triggered, the bar's timestamp, and (for Conditions)
    the LHS scalar at the trigger bar. Empty for plain ``within_last_bars=0``
    matches — populated only when a look-back walk actually fires.
    Surfaced by the scanner Treeview tooltip, entries / exits audit
    panes, and replay overlay markers.
    """


@dataclass
class ScanResult:
    """Aggregated rows for one scan at one tick."""

    scan_id: str
    tick_id: int
    timestamp: datetime
    interval: str
    rows: list[MatchRow] = field(default_factory=list)
    new_rows: list[MatchRow] = field(default_factory=list)

    def matched_rows(self) -> list[MatchRow]:
        return [r for r in self.rows if r.matched is True]


# ---------------------------------------------------------------------------
# Match history (edge detection for New view)
# ---------------------------------------------------------------------------


@dataclass
class MatchHistory:
    """Per-scan record of which symbols were last seen as a True match.

    Strictly edge-triggered: a row is "new" when matched flips from
    not-True (False / None / never seen) to True.

    ``last_matched_tick`` lets the GUI compute "ticks since" for the
    optional fading-color decoration; a separate fade is not the
    runner's concern.
    """

    last_matched_tick: dict[str, int] = field(default_factory=dict)
    last_matched: dict[str, bool] = field(default_factory=dict)

    def update(
        self,
        symbol: str,
        tick_id: int,
        matched: bool | None,
        *,
        forming: bool = False,
    ) -> bool:
        """Apply state transition; return True iff this is a *new* True match.

        ``forming=True`` indicates the match was evaluated on an
        in-progress (un-closed) bar. Forming matches are *provisional*:
        they NEVER set ``is_new`` and they do NOT mutate committed
        state. Closed bars (``forming=False``) are the sole source of
        truth for promotion and clearing. This means a stream of
        forming ticks will not spam the "New" view, and a forming
        match that fails to confirm at bar close has no lingering
        effect on history.
        """
        if forming:
            return False
        was_match = self.last_matched.get(symbol, False)
        is_new_match = bool(matched is True and not was_match)
        if matched is True:
            self.last_matched[symbol] = True
            self.last_matched_tick[symbol] = tick_id
        elif matched is False:
            self.last_matched[symbol] = False
        # matched is None → leave state untouched (insufficient data)
        return is_new_match


# ---------------------------------------------------------------------------
# Per-symbol context evaluation
# ---------------------------------------------------------------------------


def _empty_row(symbol: str) -> MatchRow:
    return MatchRow(
        symbol=symbol, matched=None, values={}, rank_value=None, is_new=False,
    )


def _evaluate_one_scan_in_ctx(
    scan: ScanDefinition,
    ctx: EvaluationContext,
    leaves: Sequence[Condition],
) -> MatchRow:
    """Evaluate ``scan`` against a pre-built ``EvaluationContext``."""
    # Reset per-scan evidence collector. The context is shared across
    # scans on the same tick; without a reset, evidence from earlier
    # scans on the same symbol would leak into later scans' rows.
    ctx.evidence = []
    matched = evaluate_scan(scan, ctx)
    # Snapshot evidence into a dedicated list before subsequent scans
    # mutate ``ctx.evidence``. Empty when no look-back walk fired.
    evidence = list(ctx.evidence)

    values: dict[str, float | None] = {}
    for cond in leaves:
        if not cond.enabled:
            continue
        try:
            values[cond.id] = evaluate_field(cond.left, ctx)
        except Exception:  # noqa: BLE001
            values[cond.id] = None

    rank_value: float | None = None
    if scan.rank_by is not None:
        try:
            rank_value = evaluate_field(scan.rank_by, ctx)
        except Exception:  # noqa: BLE001
            rank_value = None

    return MatchRow(
        symbol=ctx.symbol, matched=matched, values=values,
        rank_value=rank_value, is_new=False, evidence=evidence,
    )


# Legacy single-symbol entry kept for ``run_scan_sync`` compatibility. It
# builds its own per-call context (no buffer reuse) — the simpler path
# tests use.
def _evaluate_one_symbol(
    scan: ScanDefinition,
    symbol: str,
    candles: list[Candle],
    interval: str,
    leaves: list[Condition],
    memos: dict[str, IndicatorMemo],
) -> MatchRow:
    """Build a context for one symbol and evaluate the scan + per-leaf values."""
    if not candles:
        return _empty_row(symbol)

    memo = memos.get(symbol)
    if memo is None:
        memo = IndicatorMemo(candles=candles)
        memos[symbol] = memo
    ctx = make_context(symbol, interval, candles, memo=memo)
    return _evaluate_one_scan_in_ctx(scan, ctx, leaves)


# ---------------------------------------------------------------------------
# Single-scan synchronous evaluation (test-friendly)
# ---------------------------------------------------------------------------


def run_scan_sync(
    scan: ScanDefinition,
    candles_by_symbol: Mapping[str, list[Candle]],
    *,
    interval: str,
    tick_id: int,
    history: MatchHistory | None = None,
    memos: dict[str, IndicatorMemo] | None = None,
    timestamp: datetime | None = None,
) -> ScanResult:
    """Evaluate ``scan`` over every symbol synchronously. No threads.

    ``memos`` (optional) is a ``{symbol: IndicatorMemo}`` dict shared
    across multiple scans on the same tick. The runner populates it
    lazily; the same memo seen by every scan on a given symbol means
    each indicator is computed at most once per tick per symbol no
    matter how many scans reference it.

    Returns one :class:`MatchRow` per symbol in ``candles_by_symbol``.
    Disabled scans return an empty ``ScanResult``.
    """
    ts = timestamp or datetime.now(timezone.utc)
    if memos is None:
        memos = {}
    result = ScanResult(
        scan_id=scan.id, tick_id=tick_id, timestamp=ts, interval=interval,
    )
    leaves = scan.all_conditions()

    universe = _filter_universe(scan, candles_by_symbol)

    for symbol in sorted(universe):
        candles = candles_by_symbol.get(symbol, [])
        try:
            row = _evaluate_one_symbol(
                scan, symbol, candles, interval, leaves, memos,
            )
        except Exception as e:  # noqa: BLE001
            LOG.exception("runner: scan %s failed on %s", scan.id, symbol)
            row = MatchRow(
                symbol=symbol, matched=None, values={}, rank_value=None,
                is_new=False, error=repr(e),
            )

        is_new = bool(history.update(symbol, tick_id, row.matched)) if history else False
        row.is_new = is_new
        result.rows.append(row)
        if is_new:
            result.new_rows.append(row)

    return result


def _filter_universe(
    scan: ScanDefinition,
    candles_by_symbol: Mapping[str, list[Candle]],
) -> list[str]:
    """Apply ``scan.universe_filter`` to the available symbol set."""
    uf = scan.universe_filter
    available = set(candles_by_symbol.keys())
    if uf.kind == "all":
        return sorted(available)
    if uf.kind == "symbols":
        wanted = {s.upper() for s in uf.symbols}
        return sorted(wanted & available)
    if uf.kind == "watchlist":
        # The runner doesn't resolve watchlists; that's the caller's
        # job (filter ``candles_by_symbol`` before passing in). Falling
        # back to "all" is safer than silently dropping every symbol.
        LOG.debug("runner: watchlist filter %r not pre-resolved; falling back to all",
                  uf.name)
        return sorted(available)
    return sorted(available)


# ---------------------------------------------------------------------------
# Multi-scan threaded runner
# ---------------------------------------------------------------------------


def _default_workers() -> int:
    try:
        from ..defaults import get as _defaults_get
        persisted = int(_defaults_get("worker_count") or 0)
        if persisted > 0:
            return max(1, min(persisted, 64))
    except Exception:  # noqa: BLE001 - settings are optional for scanner tests/scripts
        pass
    n = os.cpu_count() or 2
    return max(1, min(n - 1, 64))


# Fingerprint shape: ``(id_of_list, length, last_ts_ns, last_open,
# last_high, last_low, last_close, last_volume)``. Cheap to compute,
# sufficient to detect every meaningful change a same-length tick can
# carry — including forming-bar updates that change volume / high /
# low without affecting close (RVOL, ATR, key-bar all care). List
# replacement (id changes) and length growth are detected by the
# first two fields alone. Empty lists fingerprint to all-zeros.
_Fingerprint = tuple[int, int, int, float, float, float, float, float]


def _fingerprint(candles: Sequence[Candle]) -> _Fingerprint:
    n = len(candles)
    if n == 0:
        return (0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)
    last = candles[-1]
    try:
        ts_ns = int(last.date.timestamp() * 1_000_000_000)
    except (AttributeError, OSError, ValueError):
        ts_ns = 0
    def _f(name: str) -> float:
        try:
            return float(getattr(last, name))
        except (AttributeError, TypeError, ValueError):
            return 0.0
    return (
        id(candles), n, ts_ns,
        _f("open"), _f("high"), _f("low"), _f("close"), _f("volume"),
    )


def _prefix_match(
    prev_fp: _Fingerprint,
    candles: Sequence[Candle],
    prev_len: int,
) -> bool:
    """True iff ``candles[:prev_len]`` agrees with ``prev_fp`` on identity
    and (by extension) is plausibly an append-only extension.

    With contiguous appends from the SAME source list, ``id`` is
    preserved and the prefix length matches the cached length. If the
    list is the same object and the length only grew, we trust the
    prefix. This is good enough for the sandbox driver pattern (which
    only ever appends to its in-place lists) and any future streaming
    bar-builder that follows the same convention. Mismatched ids force
    a full rebuild.
    """
    same_id = prev_fp[0] == id(candles)
    return same_id and len(candles) >= prev_len


@dataclass
class _SymbolState:
    """Per-symbol cached state held by :class:`ScanRunner`."""

    buffer: BarsBuffer
    memo: IndicatorMemo
    fingerprint: _Fingerprint


class ScanRunner:
    """Multi-scan, per-symbol-task runner with persistent memos.

    The runner owns:

    - one :class:`ThreadPoolExecutor` (lazy: only spun up on first use)
    - one :class:`MatchHistory` per scan id (lazy)
    - one :class:`_SymbolState` per symbol seen, lazily built and
      reconciled on each :meth:`run` call.

    Stats counters expose how often each per-symbol state was reused
    vs rebuilt — useful for benchmarking and for verifying that
    persistence is actually doing work.
    """

    def __init__(
        self,
        max_workers: int | None = None,
        *,
        bars_registry: Any | None = None,
    ) -> None:
        """Construct a runner.

        ``max_workers`` (optional) sizes the per-symbol
        :class:`ThreadPoolExecutor`. Defaults to
        ``min(cpu_count - 1, 4)``.

        ``bars_registry`` (optional, keyword-only) opts into the
        registry path. When provided, the runner pulls
        ``(bars, memo)`` pairs from the
        :class:`tradinglab.core.bars_registry.BarsRegistry` instead
        of building them locally. Symbols not yet present in the
        registry (lazy-load pending) are skipped on this tick — no
        crash, no row. When ``None`` (the default), the historical
        local-state path is used and behavior is unchanged.
        """
        self.max_workers = max_workers if max_workers is not None else _default_workers()
        self._executor: ThreadPoolExecutor | None = None
        self._histories: dict[str, MatchHistory] = {}
        # Local-state path: per-symbol cached state. Populated lazily
        # in ``_reconcile`` and reused / rebuilt per fingerprint logic.
        # Only used when ``self._bars_registry is None``; the registry
        # path leaves this dict empty.
        self._states: dict[str, _SymbolState] = {}
        self._bars_registry = bars_registry
        self._stats: dict[str, int] = {
            "memo_builds":      0,
            "memo_reuses":      0,
            "buffer_rebuilds":  0,
            "buffer_appends":   0,
            "stale_evictions":  0,
            "forming_updates":  0,
            "incremental_steps":     0,
            "incremental_falls_back": 0,
            "registry_skips":   0,
        }
        # Subscribers are notified (on the caller thread) after each
        # ``run()`` finishes assembling its ``ScanResult`` map. Each
        # callback receives ``(scan_id, ScanResult)`` per scan that
        # produced ``new_rows`` (edge-triggered: forming-bar matches
        # never trigger). See ``subscribe`` / ``_dispatch_to_subscribers``.
        self._subscribers: list[Callable[[str, ScanResult], None]] = []

    # -- executor lifecycle --------------------------------------------------

    def _exec(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self.max_workers,
                thread_name_prefix="scan-runner",
            )
        return self._executor

    def shutdown(self, wait: bool = True) -> None:
        """Tear down the worker pool. Idempotent."""
        if self._executor is not None:
            self._executor.shutdown(wait=wait)
            self._executor = None

    def history_for(self, scan_id: str) -> MatchHistory:
        h = self._histories.get(scan_id)
        if h is None:
            h = MatchHistory()
            self._histories[scan_id] = h
        return h

    def reset_history(self, scan_id: str | None = None) -> None:
        """Clear match history for one scan, or all if ``scan_id`` is None."""
        if scan_id is None:
            self._histories.clear()
        else:
            self._histories.pop(scan_id, None)

    # -- subscriber API -----------------------------------------------------

    def subscribe(
        self, callback: Callable[[str, ScanResult], None],
    ) -> Callable[[], None]:
        """Register ``callback`` to be invoked on every match-producing run.

        The callback is invoked once per scan that produced at least one
        ``new_row`` during the just-completed :meth:`run`. It receives
        the ``scan_id`` and the corresponding :class:`ScanResult`.

        Callbacks fire on the **caller thread** (the thread that called
        :meth:`run`), AFTER all per-symbol futures have drained and
        results are fully assembled. The runner does not invoke
        callbacks from worker threads — this is the contract that lets
        the entries-v1 evaluator subscribe directly without marshalling
        back to Tk.

        A subscriber that raises is caught and logged; one bad
        subscriber doesn't break the others. Returns an unsub callable.
        """
        self._subscribers.append(callback)

        def _unsub() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return _unsub

    def _dispatch_to_subscribers(
        self, results: dict[str, ScanResult],
    ) -> None:
        """Fan out :class:`ScanResult` values to subscribers.

        Only scans whose ``new_rows`` list is non-empty are dispatched —
        a "tick with no edge" is uninteresting to subscribers and would
        be just noise on the entries side.
        """
        if not self._subscribers:
            return
        # Frozen-tuple snapshot so subscribe/unsubscribe during dispatch
        # doesn't corrupt iteration.
        subs = tuple(self._subscribers)
        for scan_id, res in results.items():
            if not res.new_rows:
                continue
            for sub in subs:
                try:
                    sub(scan_id, res)
                except Exception:  # noqa: BLE001
                    LOG.exception(
                        "scanner subscriber raised; continuing"
                    )

    # -- memo / buffer lifecycle --------------------------------------------

    def invalidate(self, symbol: str) -> None:
        """Drop cached state for ``symbol`` (next ``run`` will rebuild)."""
        self._states.pop(symbol, None)

    def invalidate_all(self) -> None:
        """Drop all cached state. Histories untouched."""
        self._states.clear()

    # Alias preserved for callers that just want to drop indicator caches.
    clear_memos = invalidate_all

    def stats(self) -> dict[str, int]:
        """Shallow copy of per-symbol reconcile counters."""
        return dict(self._stats)

    def stats_text(self) -> str:
        """Compact one-line summary of per-tick reconcile activity.

        Format: ``"reuse 82% (123/150) · appends 47 · rebuilds 3 · forming 12"``.
        ``reuse %`` is ``memo_reuses / (memo_reuses + memo_builds)`` with
        zero-denominator handled. Useful for status-bar display.
        """
        s = self._stats
        total = s["memo_reuses"] + s["memo_builds"]
        pct = (100 * s["memo_reuses"] // total) if total else 0
        return (
            f"reuse {pct}% ({s['memo_reuses']}/{total}) "
            f"· appends {s['buffer_appends']} "
            f"· rebuilds {s['buffer_rebuilds']} "
            f"· forming {s['forming_updates']}"
        )

    def _reconcile(
        self, symbol: str, candles: Sequence[Candle], interval: str,
        *, forming: bool = False,
    ) -> EvaluationContext | None:
        """Update the cached state for ``symbol`` and return a ready context.

        Two modes:

        * **Registry path** (``self._bars_registry is not None``):
          Delegate to :meth:`BarsRegistry.get_view` for the
          ``(symbol, interval)`` pair; the registry owns the
          ``(buffer, memo)`` lifecycle. Returns ``None`` if the
          registry has no buffer yet (lazy-load pending) — the
          caller skips the symbol gracefully (no row, no crash).
          Local ``_states`` is NOT touched in this mode.

        * **Local-state path** (default, ``bars_registry is None``):
          Reconcile this runner's per-symbol cached
          ``BarsBuffer`` + ``IndicatorMemo`` against ``candles``
          using the fingerprint decision tree:

          - **No cached state** → fresh ``BarsBuffer.from_candles`` +
            fresh ``IndicatorMemo`` (``buffer_rebuilds``,
            ``memo_builds``).
          - **Same fingerprint** (full OHLCV match) → reuse buffer
            and memo as-is (``memo_reuses``).
          - **Same id, length grew, prefix unchanged** →
            ``buffer.append`` for the new tail; rebuild memo (no
            incremental indicator protocol yet).
            (``buffer_appends`` += k, ``memo_builds``).
          - **Same id, same length, ``forming=True``, last-bar OHLCV
            differs** → ``buffer.update_last`` in place; rebuild
            memo (incremental indicators land in slice 2).
            (``forming_updates`` += 1, ``memo_builds``).
          - **Anything else** (list replacement, shrink, last-bar
            mutation without forming flag) → full rebuild
            (``buffer_rebuilds``, ``memo_builds``).
        """
        if self._bars_registry is not None:
            view = self._bars_registry.get_view(symbol, interval)
            if view is None:
                # Lazy-load pending (or invalidated). Caller skips this
                # symbol on this tick — no row, no crash.
                self._stats["registry_skips"] += 1
                return None
            # Pass the memo's own candles list (not a copy) so
            # ``make_context``'s ``memo.candles is not candles``
            # check skips the cache-clear branch — the memo and the
            # candles list match by identity.
            ctx = make_context(
                symbol, interval, view.memo.candles,
                memo=view.memo, bars=view.bars,
                bars_registry=self._bars_registry,
            )
            return ctx

        new_fp = _fingerprint(candles)
        st = self._states.get(symbol)

        if st is None:
            buf = BarsBuffer.from_candles(candles)
            memo = IndicatorMemo(candles=list(candles))
            self._states[symbol] = _SymbolState(buffer=buf, memo=memo, fingerprint=new_fp)
            self._stats["buffer_rebuilds"] += 1
            self._stats["memo_builds"] += 1
            ctx = make_context(symbol, interval, list(candles),
                               memo=memo, bars=buf.view(candles=list(candles)))
            return ctx

        prev_fp = st.fingerprint
        prev_len = prev_fp[1]
        cur_len = len(candles)

        # Hot path: identical state, no work.
        if new_fp == prev_fp:
            self._stats["memo_reuses"] += 1
            view = st.buffer.view(candles=list(candles))
            ctx = make_context(symbol, interval, list(candles),
                               memo=st.memo, bars=view)
            return ctx

        # Append-only growth on the SAME list object: extend buffer.
        # Try incremental advance on the existing memo (closed-bar
        # appends only — forming branch below still rebuilds). Each
        # cached indicator that supports the inc protocol steps in
        # place; others drop and recompute on next access.
        if (
            _prefix_match(prev_fp, candles, prev_len)
            and cur_len > prev_len
        ):
            tail = list(candles[prev_len:])
            for c in tail:
                st.buffer.append(c)
            self._stats["buffer_appends"] += len(tail)
            candles_copy = list(candles)
            view = st.buffer.view(candles=candles_copy)
            st.memo.advance_for_append(
                view, prev_len=prev_len, stats_sink=self._stats,
            )
            st.memo.candles = candles_copy
            st.fingerprint = new_fp
            ctx = make_context(symbol, interval, candles_copy,
                               memo=st.memo, bars=view)
            return ctx

        # Forming-bar fast path: same id, same length, last-bar OHLCV
        # differs and caller asserts the bar is still in progress.
        # ``update_last`` mutates the slot in place; subsequent
        # ``view()`` re-reads through the same arrays.
        if (
            forming
            and prev_fp[0] == id(candles)
            and cur_len == prev_len
            and cur_len > 0
        ):
            st.buffer.update_last(candles[-1])
            # Memo cache holds outputs computed against the previous
            # last-bar values; drop it. Phase 3 will replace this with
            # incremental_step + same-length forming update.
            st.memo = IndicatorMemo(candles=list(candles))
            self._stats["memo_builds"] += 1
            self._stats["forming_updates"] += 1
            st.fingerprint = new_fp
            view = st.buffer.view(candles=list(candles))
            ctx = make_context(symbol, interval, list(candles),
                               memo=st.memo, bars=view)
            return ctx

        # Anything else (different list, shrink, or last-bar mutation): full rebuild.
        st.buffer = BarsBuffer.from_candles(candles)
        st.memo = IndicatorMemo(candles=list(candles))
        st.fingerprint = new_fp
        self._stats["buffer_rebuilds"] += 1
        self._stats["memo_builds"] += 1
        view = st.buffer.view(candles=list(candles))
        ctx = make_context(symbol, interval, list(candles),
                           memo=st.memo, bars=view)
        return ctx

    def _evict_stale(self, alive_symbols: set) -> None:
        """Drop cached state for symbols no longer in the live universe."""
        stale = [s for s in self._states if s not in alive_symbols]
        for s in stale:
            del self._states[s]
        self._stats["stale_evictions"] += len(stale)

    # -- evaluation ----------------------------------------------------------

    def run(
        self,
        scans: list[ScanDefinition],
        candles_by_symbol: Mapping[str, list[Candle]],
        *,
        interval: str,
        tick_id: int,
        timestamp: datetime | None = None,
        last_bar_forming: bool = False,
    ) -> dict[str, ScanResult]:
        """Evaluate every scan against the universe. Returns ``{scan_id: result}``.

        ``last_bar_forming`` (default False) marks this tick as a
        forming-bar update — i.e. the last candle in every symbol's
        list is provisional and may still receive further updates
        before bar close. When True:

        * The reconcile path uses ``BarsBuffer.update_last`` for
          same-id same-length last-bar mutations (instead of full
          rebuild), since the bar bucket is unchanged.
        * Every emitted :class:`MatchRow` carries ``is_forming=True``.
        * :class:`MatchHistory` is informed of forming status so
          provisional matches do NOT promote to ``is_new`` and do NOT
          mutate committed history. Closed bars (``forming=False``)
          remain the sole source of truth for new-match transitions.

        Sandbox replay always passes ``forming=False`` since each
        ``next_bar`` call IS a closed bar. The flag is for live tick
        drivers that emit intrabar updates.

        Threading model: **one task per symbol** (not per ``(scan,
        symbol)``). Each task evaluates every scan for its symbol
        sequentially against a pre-built :class:`EvaluationContext`.
        """
        if not scans:
            return {}
        ts = timestamp or datetime.now(timezone.utc)

        results: dict[str, ScanResult] = {
            s.id: ScanResult(scan_id=s.id, tick_id=tick_id, timestamp=ts,
                             interval=interval)
            for s in scans
        }
        # Pre-compute per-scan leaf lists (cheap; reused across symbols).
        leaves_by_scan: dict[str, list[Condition]] = {
            s.id: s.all_conditions() for s in scans
        }
        # Pre-compute per-scan filtered universes.
        universes_by_scan: dict[str, list[str]] = {
            s.id: _filter_universe(s, candles_by_symbol) for s in scans
        }

        # Universe union: every symbol that any scan cares about.
        universe_union = sorted({sym for u in universes_by_scan.values() for sym in u})
        # Local-state path tracks per-symbol caches; the registry path
        # delegates ownership to the registry, so eviction here is a
        # no-op when no local state is held.
        if self._bars_registry is None:
            self._evict_stale(set(universe_union))

        # Pre-reconcile every symbol on the calling thread (single writer
        # to ``_states``). Builds per-symbol contexts that worker threads
        # will read against. In registry mode, ``candles_by_symbol`` is
        # consulted only for universe membership — the actual bars come
        # from the registry's per-(sym, interval) buffer.
        ctx_by_symbol: dict[str, EvaluationContext] = {}
        for sym in universe_union:
            candles = candles_by_symbol.get(sym, [])
            if self._bars_registry is None and not candles:
                continue
            ctx = self._reconcile(sym, candles, interval, forming=last_bar_forming)
            if ctx is not None:
                ctx_by_symbol[sym] = ctx

        # Per-symbol membership in each scan (cheap precompute).
        scans_for_symbol: dict[str, list[ScanDefinition]] = {sym: [] for sym in universe_union}
        for scan in scans:
            for sym in universes_by_scan[scan.id]:
                scans_for_symbol[sym].append(scan)

        # Worker: evaluate every scan that wants this symbol against the
        # shared context, return ``{scan_id: MatchRow}``.
        def _evaluate_symbol(
            sym: str,
            ctx: EvaluationContext | None,
            wanted_scans: list[ScanDefinition],
        ) -> dict[str, MatchRow]:
            out: dict[str, MatchRow] = {}
            for scan in wanted_scans:
                if ctx is None:
                    out[scan.id] = _empty_row(sym)
                    continue
                try:
                    out[scan.id] = _evaluate_one_scan_in_ctx(
                        scan, ctx, leaves_by_scan[scan.id],
                    )
                except Exception as e:  # noqa: BLE001
                    LOG.exception("runner.run: scan %s failed on %s", scan.id, sym)
                    out[scan.id] = MatchRow(
                        symbol=sym, matched=None, values={}, rank_value=None,
                        is_new=False, error=repr(e),
                    )
            return out

        executor = self._exec()
        # Dispatch one future per symbol that has a context. In
        # registry mode, symbols absent from ``ctx_by_symbol`` are
        # those the registry hasn't loaded yet — skip them entirely
        # (no row, no crash). In local-state mode, symbols absent
        # from ``ctx_by_symbol`` had empty candles; the legacy
        # contract emits an insufficient-data row for them, so we
        # still dispatch a no-context future to preserve that.
        registry_mode = self._bars_registry is not None
        if registry_mode:
            dispatch_symbols = [s for s in universe_union if s in ctx_by_symbol]
        else:
            dispatch_symbols = list(universe_union)
        future_by_symbol = {
            sym: executor.submit(
                _evaluate_symbol,
                sym,
                ctx_by_symbol.get(sym),
                scans_for_symbol[sym],
            )
            for sym in dispatch_symbols
        }

        # Drain in stable symbol order (preserves the previous
        # alphabetical ordering of MatchRows within each ScanResult).
        for sym in dispatch_symbols:
            try:
                rows_by_scan = future_by_symbol[sym].result()
            except Exception as e:  # noqa: BLE001
                LOG.exception("runner.run: per-symbol task failed for %s", sym)
                rows_by_scan = {
                    scan.id: MatchRow(
                        symbol=sym, matched=None, values={}, rank_value=None,
                        is_new=False, error=repr(e),
                    )
                    for scan in scans_for_symbol[sym]
                }
            for scan in scans_for_symbol[sym]:
                row = rows_by_scan.get(scan.id) or _empty_row(sym)
                row.is_forming = last_bar_forming
                history = self.history_for(scan.id)
                row.is_new = bool(history.update(sym, tick_id, row.matched, forming=last_bar_forming))
                res = results[scan.id]
                res.rows.append(row)
                if row.is_new:
                    res.new_rows.append(row)

        # Fan out to subscribers on the caller thread (now that results
        # are fully assembled). Only scans with edge-triggered new_rows
        # are dispatched — see ``_dispatch_to_subscribers`` docstring.
        self._dispatch_to_subscribers(results)

        return results


__all__ = [
    "MatchRow",
    "ScanResult",
    "MatchHistory",
    "ScanRunner",
    "run_scan_sync",
]
