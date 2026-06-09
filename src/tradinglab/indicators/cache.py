"""Identity-keyed cache for indicator compute results.

Mirrors the existing ``_series_cache`` pattern in :mod:`app`: results
are keyed by ``(id(candles), config_hash)``. Because Python may reuse
``id()`` for a freshly-allocated object after an old one is GC'd, we
also stash a back-reference to the candles list and verify identity
on lookup (``cached_candles is candles``).

In addition to the id-based primary key, every entry is also indexed
under a **content fingerprint** ``(_candles_fingerprint(candles),
hash_key)``. The fallback lookup survives list-recreate (full reload,
ticker switch with disk-cache hit, fresh ``Bars.from_candles``
allocations) when the underlying OHLCV stream hasn't changed — a
common case that historically forced a full O(N) indicator recompute
of every kernel. On a fingerprint hit the entry is re-keyed under the
new ``id(candles)`` so subsequent calls take the fast path.

Indicators implementing the **incremental protocol** (``inc_init`` +
``inc_step``) — currently SMA, EMA — also stash their per-key state
in the entry. When the candle list grows in place (same ``id`` +
length increased), :meth:`get_or_compute_incremental` calls
``inc_step`` to extend the result in O(k) instead of recomputing the
full O(N) kernel. Mirrors the scanner-side ``IndicatorMemo`` /
``advance_for_append`` pattern but operating on the chart's per-app
cache.

Eviction: a small LRU keeps each store bounded. Indicator results are
small (handful of float64 arrays at N≈500), so 64 entries is plenty
for typical interactive use (a few tickers × a few intervals × a few
indicators).

The cache is **per-app-instance**, not module-global, so tests don't
need to clear shared state.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import Any, NamedTuple

import numpy as np

from ..core.reference_data import generation as _reference_generation
from ..models import Candle

# Indicator kinds whose ``compute_arr`` reads cross-symbol reference data
# (currently only RRVOL, via ``core.reference_data.get_reference_bars``).
# Their cache key MUST invalidate when fresh reference bars land, so we
# fold the reference-data generation counter into their config hash. Every
# OTHER indicator's hash is reference-independent and survives a reference
# arrival untouched — replacing the old "clear the whole cache on every
# reference arrival" thrash (see app.py ``_reference_data_redraw``).
_REFERENCE_DEPENDENT_KINDS = frozenset({"rrvol"})


class _Entry(NamedTuple):
    """Cache value: indicator output + a back-ref + optional inc state.

    ``result`` is the dict the renderer reads.  ``candles`` is the
    strong back-reference used by the id-recycle guard (and to keep
    the list alive while the entry is cached).  ``state`` is the
    incremental-protocol state captured by ``inc_init`` (None for
    indicators that don't implement the protocol or where
    ``inc_init`` raised).  ``prev_len`` is the candle count at the
    time the entry was written — :meth:`get_or_compute_incremental`
    compares ``len(candles)`` against this to decide between
    return-as-is (equal), incremental ``inc_step`` (grew), and full
    recompute (shrunk / id collision / no incremental support).
    """
    result: dict[str, np.ndarray]
    candles: list[Candle]
    state: dict[str, Any] | None
    prev_len: int


def _candles_fingerprint(candles: list[Candle] | None) -> tuple[Any, ...] | None:
    """Content-based fingerprint that survives list-identity changes.

    Returns ``None`` for empty/falsy input so callers know to fall
    back to id-only keying. Otherwise returns a tuple summarizing
    enough of the OHLCV stream that a collision between two distinct
    real series is vanishingly unlikely: the first bar's timestamp +
    open/close/volume, the last bar's timestamp + full OHLCV, and the
    length. Floats are ``repr()``'d so NaN values (gap candles) hash
    and compare consistently in dict keys (raw ``float('nan')`` has
    ``nan != nan`` semantics and would break lookup).
    """
    if not candles:
        return None
    first = candles[0]
    last = candles[-1]
    return (
        first.date, repr(first.open), repr(first.close), repr(first.volume),
        last.date, repr(last.open), repr(last.high), repr(last.low),
        repr(last.close), repr(last.volume),
        len(candles),
    )


def config_hash(kind_id: str, params: dict[str, Any]) -> str:
    """Stable hash of a config's compute-affecting fields.

    Style/visibility/scope/intervals do NOT participate — they affect
    rendering, not numerics. Only ``kind_id`` and ``params`` matter
    for the cache key.

    For reference-dependent kinds (RRVOL), the current
    ``core.reference_data`` generation counter is folded in so the entry
    invalidates — and recomputes against the freshly-arrived compare
    symbol — the moment new reference bars land, without disturbing any
    other indicator's cached result.
    """
    payload: dict[str, Any] = {"kind_id": kind_id, "params": params}
    if kind_id in _REFERENCE_DEPENDENT_KINDS:
        payload["_ref_gen"] = _reference_generation()
    payload_bytes = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=repr,
    ).encode("utf-8")
    return hashlib.sha1(payload_bytes).hexdigest()[:16]


class IndicatorCache:
    """Identity-keyed LRU of indicator compute results with
    fingerprint fallback and an incremental-extension hook.

    Capacity is the number of distinct ``(candles, config)`` pairs;
    typical usage stays well below the cap. ``_store`` (id-keyed) and
    ``_fp_store`` (fingerprint-keyed) hold parallel copies of every
    live entry — they share entry objects via ``NamedTuple`` so the
    memory cost is just two dict pointers per entry.
    """

    def __init__(self, capacity: int = 64) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        # Insertion-ordered OrderedDict; ``move_to_end`` on hit makes
        # this a textbook LRU.
        self._store: OrderedDict[tuple[int, str], _Entry] = OrderedDict()
        # Fingerprint-keyed fallback store. Same entry objects as
        # ``_store``; the fingerprint key survives list-recreate.
        self._fp_store: OrderedDict[tuple[tuple[Any, ...], str], _Entry] = (
            OrderedDict())
        # Per-candles ``Bars`` memo. Same id-recycle + fingerprint
        # pattern as the indicator store. Bounded to ``capacity``
        # entries (one per candle list typically corresponds to many
        # indicator entries, so this stays small).
        self._bars_store: OrderedDict[int, tuple[Any, list[Candle]]] = (
            OrderedDict())
        self._bars_fp_store: (
            OrderedDict[tuple[Any, ...], tuple[Any, list[Candle]]]
        ) = OrderedDict()

    # ---- queries ----

    def __len__(self) -> int:
        return len(self._store)

    # ---- internal helpers ----

    def _trim_store(self) -> None:
        while len(self._store) > self._capacity:
            self._store.popitem(last=False)

    def _trim_fp_store(self) -> None:
        while len(self._fp_store) > self._capacity:
            self._fp_store.popitem(last=False)

    def _record_entry(
        self,
        candles: list[Candle],
        hash_key: str,
        entry: _Entry,
    ) -> None:
        """Write ``entry`` to both the id-keyed and fingerprint-keyed stores."""
        id_key = (id(candles), hash_key)
        self._store[id_key] = entry
        self._store.move_to_end(id_key)
        self._trim_store()
        fp = _candles_fingerprint(candles)
        if fp is not None:
            fp_key = (fp, hash_key)
            self._fp_store[fp_key] = entry
            self._fp_store.move_to_end(fp_key)
            self._trim_fp_store()

    def get(self, candles: list[Candle], hash_key: str) -> dict[str, np.ndarray] | None:
        """Return cached result for (candles, hash_key) or None.

        Tries the id-keyed store first (back-ref verified to defend
        against id reuse after GC). On miss, falls back to the
        fingerprint-keyed store — a hit there means the same OHLCV
        content was previously cached under a now-dead list. The
        entry is re-keyed under the new ``id(candles)`` so subsequent
        calls take the fast path.
        """
        id_key = (id(candles), hash_key)
        entry = self._store.get(id_key)
        if entry is not None:
            if entry.candles is candles:
                self._store.move_to_end(id_key)
                return entry.result
            # id collision (Python recycled the address) — evict.
            del self._store[id_key]
        fp = _candles_fingerprint(candles)
        if fp is None:
            return None
        fp_key = (fp, hash_key)
        fp_entry = self._fp_store.get(fp_key)
        if fp_entry is None:
            return None
        # Re-key under the new id so future lookups take the fast
        # path. Update fingerprint entry's back-ref to the new list
        # too (the old list is presumably about to be GC'd).
        rebound = _Entry(
            fp_entry.result, candles, fp_entry.state, fp_entry.prev_len,
        )
        self._store[id_key] = rebound
        self._store.move_to_end(id_key)
        self._trim_store()
        self._fp_store[fp_key] = rebound
        self._fp_store.move_to_end(fp_key)
        return rebound.result

    def put(
        self,
        candles: list[Candle],
        hash_key: str,
        result: dict[str, np.ndarray],
    ) -> None:
        """Public put: cache ``result`` with no incremental state.

        Callers that want incremental-extension support should route
        through :meth:`get_or_compute_incremental` so ``inc_init`` is
        seeded as part of the compute path.
        """
        self._record_entry(
            candles, hash_key,
            _Entry(result, candles, None, len(candles)),
        )

    # ---- invalidation ----

    def invalidate_for_candles(self, candles: list[Candle]) -> int:
        """Drop every entry whose candles object is ``candles``.

        Used when the underlying data is mutated in place such that
        cached results can no longer be trusted (forming-bar upserts
        in the streaming path: the last bar's OHLCV mutates while the
        list ``id`` stays put). Pure-append growth (sandbox tick,
        rollover-append) does NOT need to invalidate — the
        incremental-extension hook in
        :meth:`get_or_compute_incremental` detects growth and routes
        through ``inc_step`` (forming-bar callers must invalidate
        because the bar's OHLCV changed, not just length).

        Returns the number of id-store entries dropped (does not
        count fingerprint-store entries — those share the same entry
        objects so they're always in sync).
        """
        target = id(candles)
        # id-keyed entries that point at this list
        keys = [k for k in list(self._store) if k[0] == target
                and self._store[k].candles is candles]
        for k in keys:
            del self._store[k]
        # Fingerprint-keyed entries that point at this list. We can't
        # filter purely on the fingerprint (the caller has already
        # mutated the bar so the new fingerprint differs from what
        # was stored) — walk the values instead and drop any whose
        # back-ref matches.
        fp_keys = [k for k, e in list(self._fp_store.items())
                   if e.candles is candles]
        for k in fp_keys:
            del self._fp_store[k]
        # Cached Bars views are also stale on in-place mutation.
        bentry = self._bars_store.get(target)
        if bentry is not None and bentry[1] is candles:
            del self._bars_store[target]
        bfp_keys = [k for k, v in list(self._bars_fp_store.items())
                    if v[1] is candles]
        for k in bfp_keys:
            del self._bars_fp_store[k]
        return len(keys)

    def clear(self) -> None:
        self._store.clear()
        self._fp_store.clear()
        self._bars_store.clear()
        self._bars_fp_store.clear()

    # ---- Bars view memo ----

    def bars_for(self, candles: list[Candle]):
        """Return a memoized :class:`Bars` view for ``candles``.

        Same id-recycle guard + fingerprint fallback as the indicator
        store. Building a Bars view is O(N) over the candle list;
        memoizing means N indicators sharing the same candle list
        build it exactly once per render pass, AND a fresh list with
        identical content (post-reload) reuses the cached Bars instead
        of rebuilding.

        Also detects in-place candle-list growth: the cache entry
        records ``len(bars)`` at insertion time, and a mismatch with
        the current ``len(candles)`` evicts the stale view. This is
        critical for the sandbox-tick path where the candle list
        identity is stable but its length grows by one each tick —
        without the length check, ``bars_for`` would return a stale
        N-element view for an N+1-element list and the incremental
        ``inc_step`` hook would receive mismatched bars.
        """
        from ..core.bars import Bars

        id_key = id(candles)
        n_now = len(candles)
        entry = self._bars_store.get(id_key)
        if entry is not None:
            cached_bars, cached_candles = entry
            if cached_candles is candles and len(cached_bars) == n_now:
                self._bars_store.move_to_end(id_key)
                return cached_bars
            del self._bars_store[id_key]
        fp = _candles_fingerprint(candles)
        if fp is not None:
            fp_entry = self._bars_fp_store.get(fp)
            if fp_entry is not None:
                cached_bars, _cached_candles = fp_entry
                if len(cached_bars) == n_now:
                    # Re-key under the new id for fast subsequent lookups.
                    self._bars_store[id_key] = (cached_bars, candles)
                    self._bars_store.move_to_end(id_key)
                    while len(self._bars_store) > self._capacity:
                        self._bars_store.popitem(last=False)
                    self._bars_fp_store[fp] = (cached_bars, candles)
                    self._bars_fp_store.move_to_end(fp)
                    return cached_bars
                # Length mismatch — evict the stale fp entry.
                del self._bars_fp_store[fp]
        bars = Bars.from_candles(candles)
        self._bars_store[id_key] = (bars, candles)
        self._bars_store.move_to_end(id_key)
        while len(self._bars_store) > self._capacity:
            self._bars_store.popitem(last=False)
        if fp is not None:
            self._bars_fp_store[fp] = (bars, candles)
            self._bars_fp_store.move_to_end(fp)
            while len(self._bars_fp_store) > self._capacity:
                self._bars_fp_store.popitem(last=False)
        return bars

    # ---- compute helpers ----

    def get_or_compute(
        self,
        candles: list[Candle],
        hash_key: str,
        compute_fn,
    ) -> dict[str, np.ndarray]:
        """Cache-aware wrapper around a compute callable.

        ``compute_fn() -> Dict[str, ndarray]`` is invoked only on miss
        (after both the id and fingerprint stores are checked). Used
        on the gap-aware path where the incremental protocol doesn't
        apply (compute is over a non-gap subset whose mask varies
        between calls).
        """
        cached = self.get(candles, hash_key)
        if cached is not None:
            return cached
        result = compute_fn()
        self.put(candles, hash_key, result)
        return result

    def get_or_compute_incremental(
        self,
        candles: list[Candle],
        hash_key: str,
        indicator: Any,
        bars: Any,
    ) -> dict[str, np.ndarray]:
        """Cache-aware compute with the incremental-protocol hook.

        Decision tree on the entry keyed by ``(id(candles), hash_key)``:

        * **Same-id exact-length hit** — return cached result.
        * **Same-id growth hit** with cached ``state`` AND
          ``indicator`` exposes a callable ``inc_step`` — call
          ``inc_step(state, bars, prev_len=...)`` to extend in O(k);
          update the entry; return new result.
        * **Same-id shrink / id collision / inc_step raised** — fall
          through to full compute.

        On full compute, if ``indicator`` exposes ``inc_init`` we
        capture its initial state so the NEXT growth tick can take
        the fast path. ``inc_init`` is authoritative for the result
        when it returns a well-formed state dict; the standalone
        ``compute_via_bars`` is the fallback. Indicators without the
        protocol go through ``compute_via_bars`` directly and store
        ``state=None`` (no inc_step possible).

        Fingerprint fallback is also tried on full-compute miss — a
        fresh candle list with identical content (e.g. after a disk
        cache reload) reuses the prior entry rather than recomputing.
        """
        from .base import compute_via_bars

        id_key = (id(candles), hash_key)
        new_len = len(candles)
        entry = self._store.get(id_key)
        inc_step = getattr(indicator, "inc_step", None)

        if entry is not None and entry.candles is candles:
            # Same-id hit. Decide based on length delta.
            if entry.prev_len == new_len:
                self._store.move_to_end(id_key)
                return entry.result
            if (entry.prev_len < new_len
                    and entry.state is not None
                    and callable(inc_step)):
                try:
                    new_state = inc_step(
                        entry.state, bars, prev_len=entry.prev_len,
                    )
                    if (isinstance(new_state, dict)
                            and isinstance(new_state.get("output"), dict)):
                        new_entry = _Entry(
                            new_state["output"], candles, new_state, new_len,
                        )
                        self._record_entry(candles, hash_key, new_entry)
                        return new_entry.result
                except Exception:  # noqa: BLE001
                    # inc_step refused (typical for forming-bar edge
                    # cases the kernel doesn't model); fall through.
                    pass
            # Shrink, no-state, no-inc_step, or inc_step failed —
            # full recompute. Evict the stale entry.
            del self._store[id_key]
        elif entry is not None:
            # id collision (entry.candles is some other list under
            # the same id). Evict and continue.
            del self._store[id_key]

        # Fingerprint fallback: maybe a fresh list with identical
        # content was already computed for a now-dead list. Only
        # accept an exact-length match — growth via fingerprint would
        # require re-seeding state from scratch, which is what the
        # full-compute path below does anyway.
        fp = _candles_fingerprint(candles)
        if fp is not None:
            fp_key = (fp, hash_key)
            fp_entry = self._fp_store.get(fp_key)
            if fp_entry is not None and fp_entry.prev_len == new_len:
                rebound = _Entry(
                    fp_entry.result, candles, fp_entry.state, fp_entry.prev_len,
                )
                self._record_entry(candles, hash_key, rebound)
                return rebound.result

        # Full compute. Capture ``inc_init`` state for future growth
        # ticks. If ``inc_init`` returns a well-formed state dict we
        # treat its ``output`` as authoritative (the protocol contract
        # is that ``inc_init`` and ``compute_arr`` produce equivalent
        # arrays for the same input; using ``inc_init`` here avoids
        # the rare drift risk and saves one compute pass).
        state: dict[str, Any] | None = None
        inc_init = getattr(indicator, "inc_init", None)
        result: dict[str, np.ndarray] | None = None
        if callable(inc_init):
            try:
                init_state = inc_init(bars)
                if (isinstance(init_state, dict)
                        and isinstance(init_state.get("output"), dict)):
                    state = init_state
                    result = init_state["output"]
            except Exception:  # noqa: BLE001
                state = None
                result = None
        if result is None:
            result = compute_via_bars(indicator, bars)
        self._record_entry(
            candles, hash_key, _Entry(result, candles, state, new_len),
        )
        return result
