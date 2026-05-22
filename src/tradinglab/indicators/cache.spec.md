# indicators/cache.py — Spec

## Purpose
Identity-keyed memoization of indicator compute results across pan,
zoom, theme swap, and streaming-tick re-renders. Keyed by
`(id(candles), config_hash)` with content-fingerprint fallback and
identity verification on lookup.

## Public API
- `IndicatorCache(capacity=64)` — `get(candles, hash_key) ->
  Optional[Dict[str, ndarray]]`, `put(candles, hash_key, result)`,
  `get_or_compute(candles, hash_key, compute_fn)`,
  `get_or_compute_incremental(candles, hash_key, indicator, bars)`,
  `bars_for(candles) -> Bars`,
  `invalidate_for_candles(candles)`, `clear()`, `__len__`.
- `config_hash(kind_id, params) -> str` — short stable hash (16-hex
  prefix of SHA-1). Only compute-affecting fields participate (no
  style / scopes / intervals / visibility). JSON-serialized with
  `sort_keys=True` so dict ordering is irrelevant.

## Dependencies
- External: `hashlib`, `json`, `collections.OrderedDict`, `numpy`.
- Internal: `..models.Candle`.

## Design Decisions
- **Dual-store keying: identity primary + content fingerprint
  fallback.** Each entry is indexed under BOTH `(id(candles),
  hash_key)` AND `(fingerprint(candles), hash_key)` where fingerprint
  is `(first.date, first OHLCV, last.date, last OHLCV, n)`. Identity
  is the fast path; fingerprint fallback survives list-recreate (disk
  reload, ticker switch, fresh `Bars.from_candles`). On a fingerprint
  hit the entry is re-keyed under the new `id(candles)` so subsequent
  lookups take the fast path. Both stores share the same `_Entry`
  NamedTuple — memory cost is two dict pointers per entry.
- **Identity key + back-ref verify.** Python may reuse `id()` after
  GC. `_Entry` stores `(result_dict, candles)`; lookup verifies
  `cached_candles is candles`, evicting on mismatch and falling
  through to the fingerprint store.
- **Incremental protocol hook (`get_or_compute_incremental`).**
  Indicators implementing `inc_init` / `inc_step` (currently SMA +
  EMA) get an O(k) tail-recompute on same-id-grow. The entry stashes
  `inc_init` state at full-compute; on next call with same
  `id(candles)` and `len(candles) > prev_len`, the cache calls
  `indicator.inc_step(state, bars, prev_len=prev_len)` and writes
  extended arrays back. Shrink, id collision, or indicators without
  the protocol fall through to full `compute(...)`.
- **Bars-view memo (`bars_for`).** Building a `Bars` is O(N). N
  indicators sharing one candles list pay it once per render pass.
  Same dual-store pattern with **length-mismatch eviction** —
  critical so the sandbox-tick path (grows list by 1 per tick)
  doesn't feed the incremental hook a stale Bars view.
- **Strong reference to candles in the value.** Renderer needs them
  anyway; LRU eviction drops the back-ref too.
- **LRU via OrderedDict + `move_to_end` on hit.** Capacity 64 is
  ample for typical interactive use.
- **Per-app instance, not module-global.**
- **Internal `_*` keys are opaque to the cache.** Callers (notably
  `render.py` on the gap-aware compute path) extend hashed `params`
  with private keys like `_gapfp` (gap-mask fingerprint that prevents
  compare-mode padded outputs from leaking into a non-compare render).
  Treated like any other param.
- **In-place candle mutation contract.** The back-ref verify only
  checks identity, so callers mutating candles in place must pick the
  right primitive:
  * **Pure append** (sandbox `next_bar`, intraday rollover):
    `get_or_compute_incremental` extends arrays via `inc_step` — no
    explicit invalidate needed.
  * **Forming-bar upsert** (`_apply_stream_tick_upsert`,
    `_apply_stream_rollover` equal-date upsert): last bar's OHLCV
    mutates while length is unchanged. The incremental hook can't
    detect this (length equal → returns stale arrays). Such callers
    **must** call `invalidate_for_candles(candles)` (or `clear()`).
  * **Fresh-fetch replacement** (`ChartApp._load_data` consuming
    `_prefetched_raw`): a provider reload can replace the visible list
    with a new list whose fingerprint still matches the previous list
    while interior bars changed. The app invalidates the prior visible
    primary/compare lists before rendering the replacement.
  `ChartApp._invalidate_focused_panels` (forming-bar and fresh-fetch
  replacement) and `_notify_focused_panels_appended` (pure-append) own
  this on the app side.

## Invariants
- Same `(candles, hash_key)` returns the same cached dict identity
  until eviction.
- A fresh `candles` list with byte-identical content hits via the
  fingerprint fallback and returns the same result object.
- Different `params` → different `hash_key` → guaranteed miss.
- `len(cache) <= capacity` after every `put` (per store).
- `invalidate_for_candles(c)` removes all entries whose candles is
  `c` from BOTH stores, and only those.
- `bars_for(candles)` never returns a Bars view whose length differs
  from `len(candles)`.

## Known limitations
- No size-aware eviction (entries, not bytes). Indicator outputs are
  small float64 arrays.
- Incremental protocol implemented for SMA + EMA only. RMA, RSI,
  Bollinger, ATR could benefit but each has its own state-init
  complexity.
