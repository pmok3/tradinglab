# core/render_context.py — Spec

## Purpose
Thread-local opt-in context for indicators that need info the `compute_arr(bars)` protocol doesn't surface. Today the only consumer is the RRVOL family, which needs:

- **interval** (`"5m"`, `"1d"`, …) — the render layer knows it; `Bars` doesn't carry it.
- **data source** (`"yfinance"`, `"synthetic"`, …) — required to scope cross-symbol reference caches so source switches don't reuse stale references.
- **primary symbol** — used by RRVOL to detect "primary is SPY" and short-circuit to self-divided 1.0.

The render layer pushes a thread-local context around each compute; interested indicators consult `current_context()` and degrade gracefully on missing keys. Avoids rippling a kwarg through every indicator + the scanner engine + the cache.

## Public API
- `current_context() -> Dict[str, Any]` — copy of the active context dict, or `{}` if nothing pushed. Keys are optional.
- `render_context(*, interval=None, source=None, primary_symbol=None)` — context manager. Pushes/restores the prior context on exit (nesting supported). `None` keys are omitted from the dict so callers don't shadow with spurious `None`. `primary_symbol` is upper-cased at push.

## Dependencies
Stdlib only: `threading`, `contextlib`.

## Design
- **Thread-local** (`threading.local()`): scanner workers, Tk render thread, tournament tools each see their own stack. No locks.
- **Push/restore prior context**, not "clear on exit" — supports drilldown render inside parent render on the same thread.
- **Opt-in**: indicators that don't care never read; cost is one threading-local access per compute (sub-µs). Chosen over a protocol kwarg to avoid breaking every indicator, the cache key, the scanner engine, and tests.
- **Drop `None` keys** at push so RRVOL distinguishes "key missing" (degrade to NaN) from "key present and unknown" (lookup miss).

## Invariants
- `current_context()` always returns a `dict` (never `None`).
- Context manager restores prior context even if the `with` body raises.
- A thread that never enters `render_context` sees `{}`.
