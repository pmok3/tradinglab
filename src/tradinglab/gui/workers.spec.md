# gui/workers.py — Spec

## Purpose
Mixin adding thread-pool lifecycle (`ThreadPoolExecutor`) sizing/apply behavior to `ChartApp`. Stateless — relies on attributes set by `ChartApp.__init__` (`_worker_count`, `_executor`, `_fetch_executor`) and class attrs `_WORKER_COUNT_MIN`/`_WORKER_COUNT_MAX`.

## Public API
- `class WorkerPoolMixin`:
  - `_clamp_worker_count(n)` (classmethod) — int-coerce, fallback 1 on bad input, clamp to `[_WORKER_COUNT_MIN, _WORKER_COUNT_MAX]`.
  - `_resolve_worker_count()` — precedence: existing `self._worker_count` if set → persisted `worker_count` tunable (`0` sentinel = auto-detect, audit `workers-persisted`) → `os.cpu_count()`. All clamped. A missing tunable or corrupt settings file falls through to auto-detect.
  - `_apply_worker_count(n)` — clamp, build a fresh `ThreadPoolExecutor`, assign to `self._executor`, then shutdown the old one with `wait=False`. **Does NOT touch `self._fetch_executor`** — that pool is separately constructed in `ChartApp.__init__` and dedicated to user-triggered loads (`_load_data_async`, `_next_bar_fetch_tick`); resizing only affects the background-preload pool. Persists the clamped value to `settings.json` via `settings.set("worker_count", count)` + `defaults.reload()` so the next launch starts with the same pool size (audit `workers-persisted`).
  - `set_worker_count(n)` — back-compat shim for `_apply_worker_count`.

## Dependencies
- Internal: `defaults` (lazy, for the `worker_count` tunable read in `_resolve_worker_count`); `settings` (lazy, for the persistence write in `_apply_worker_count`).
- External: `os`, `concurrent.futures.ThreadPoolExecutor`.

## Design Decisions
- **Mixin with no `__init__`**: state lives on `ChartApp`, not the mixin. Simplifies MRO — mixins don't need cooperative `super()` chaining, and state is visible in one place (`ChartApp.__init__`).
- **Old executor shutdown with `wait=False, cancel_futures=False`**: in-flight fetches stay alive on the doomed pool until they finish; new fetches go to the new pool. No abrupt cancellation that could lose a user's half-loaded chart.
- **`cancel_futures` kwarg fallback** via `TypeError`: older Python versions didn't have it; the helper degrades gracefully.
- **Two separate pools**: `_executor` (background preload, resizable via `_apply_worker_count`) and `_fetch_executor` (user-triggered HTTP fetches, constructed once in `ChartApp.__init__` at `app.py:761` with `max_workers=2`). The two pools are distinct ``ThreadPoolExecutor`` instances; they are NOT aliases of each other. `_fetch_executor`'s tiny pool is intentional — at most one primary + one compare fetch is ever in flight at a time, so two threads cover all foreground load paths without starving the system. This isolates user-perceived latency (a fetch in flight on `_fetch_executor`) from the resize churn that re-creates `_executor`.
- **Precedence order** (`_resolve_worker_count`): in-memory `self._worker_count` > persisted `worker_count` tunable (audit `workers-persisted`; `0` is the sentinel for auto-detect) > `os.cpu_count()`. An explicit user action (Settings OK) live-swaps the in-memory pool AND writes the same value to `settings.json` so the next launch reuses it.
- **Persistence on user-action only.** The `worker_count` tunable defaults to `0` (auto-detect). A fresh `defaults.get("worker_count")` returns `0` and `_resolve_worker_count` falls through to `os.cpu_count()`. Only an explicit `_apply_worker_count(positive_int)` writes a persistent override. Result: machines without explicit user input stay on auto-detect, while users who tuned the slider keep their preference across launches.
- **`_clamp_worker_count` coerces `None`/strings to 1** rather than raising. Defense-in-depth against typos in any future hand-edited config.

## Invariants
- After `_apply_worker_count(n)`: `self._worker_count == clamp(n)`, `self._executor` is the freshly-built pool, the old `_executor` is shutdown (non-blocking). `self._fetch_executor` is untouched.
- `_resolve_worker_count()` never raises — all read paths swallow exceptions.
- `_clamp_worker_count(x)` ∈ `[_WORKER_COUNT_MIN, _WORKER_COUNT_MAX]` for any `x`.

## Testing
- `check_70_fetch_executor` verifies the executor lifecycle and aliasing.

## Known limitations / Future work
- No finer-grained pool (one pool for fetches, another for CPU-bound normalization). Not currently needed — fetches dominate.

