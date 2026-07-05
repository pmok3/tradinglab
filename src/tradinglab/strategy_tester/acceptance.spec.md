# strategy_tester/acceptance.py — Spec

## Purpose
`AcceptanceToken` — a cancellation primitive shared between the GUI Stop button and worker threads driving the strategy tester's per-symbol engine fan-out. Thin wrapper over `threading.Event` so workers can poll a single bit cheaply without touching Tk.

## Public API
- `class AcceptanceToken` — `cancel()`, `is_cancelled() -> bool`, `raise_if_cancelled()` (raises `RunCancelled`).
- `class RunCancelled(RuntimeError)` — raised by `raise_if_cancelled()` for callers that want exception-style cancellation.

## Dependencies
- `threading` (stdlib only).

## Design Decisions
- **One-way flip** — once cancelled a token cannot be re-armed. A new Run mints a fresh token. Mirrors the Stop-button-is-final UX.
- **Default = not cancelled** — newly-constructed tokens are accepting (the name "acceptance" reads positively).
- **Duck-typed poll contract** — callers pass the token to runner, evaluator, screenshot, and export paths as any object exposing `is_cancelled() -> bool`. The Strategy Tester evaluator polls between batches of bars; the `SandboxEngine` itself remains cancellation-free.

## Invariants
- `cancel()` is idempotent.
- `is_cancelled()` is thread-safe (delegated to `threading.Event`).

## Testing
- `tests/unit/strategy_tester/test_acceptance.py` — basic state machine + RunCancelled raise.

## See also
- [runner](runner.spec.md) — consumer.
- `preload/service.py` — sibling cancel pattern using raw `threading.Event` (this module is the reified upgrade).
