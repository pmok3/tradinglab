# core/thread_guard.py — Spec

## Purpose
Enforce the Tk-main-thread invariant on state-owning subsystems (PositionTracker, ExitEvaluator, AuditLog, PaperBrokerEngine, IndicatorManager, DrawingStore). Calling these from worker / stream-source threads races indicator memos, Treeview updates, drawing persistence, and the JSONL audit log.

## Public API
- `class TkThreadViolation(RuntimeError)` — raised when a guarded function is invoked off-thread.
- `require_tk_thread(fn) -> fn` — decorator. Raises `TkThreadViolation` unless `threading.current_thread() is threading.main_thread()`. Preserves wrapped name, docstring, signature via `functools.wraps`.
- `tk_thread_check_disabled() -> Iterator[None]` — context manager that flips the global check off for unit tests that legitimately drive a guarded method from a worker thread. Lock-protected; restores prior state on exit. Not re-entrant safe across threads (single-threaded test fixtures only).

## Dependencies
- External: stdlib only (`functools`, `threading`, `contextlib`).

## Design Decisions
- **Decorator, not per-method ad-hoc check**: keeps guarded methods uncluttered and the policy in one file.
- **Bypass is a context manager, not a global flag mutation**: scoped bypass is harder to leak. Tests should use the `with tk_thread_check_disabled():` block rather than monkey-patching `_check_enabled`.
- **Module-level lock around the flag**: prevents torn reads when one fixture flips the flag while another reads it. Not strictly needed under the GIL for a bool, but the lock also serialises the prior/restore in the contextmanager.
- **Production code paths leave the check enabled.**

## Invariants
- A function decorated with `@require_tk_thread` raises `TkThreadViolation` when called from any thread that is not `threading.main_thread()`, unless `tk_thread_check_disabled()` is active.
- `tk_thread_check_disabled()` always restores the prior state even when the body raises.
- The decorator does not alter the wrapped function's return value or signature.

## Testing
- Covered by dedicated unit tests for the decorator itself plus off-thread guard tests for `PositionTracker`, `IndicatorManager`, and `DrawingStore`.
- Other integration / smoke flows still run on the Tk main thread; tests that intentionally drive guarded methods from workers use `tk_thread_check_disabled()`.

