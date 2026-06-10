# core/risk_gate.py — Spec

## Purpose
Pre-submit risk gate consulted by the entry evaluator just before an `EntrySignal` is sent to the paper broker. Returns a structured `RiskBlock` to refuse with auditable metadata, or `None` to allow.

## Public API
- `@dataclass(frozen=True) RiskBlock(gate: str, reason: str, meta: Dict[str, Any] = {})` — immutable record of a refusal. `gate` names the rule; `reason` is human-readable for the audit log; `meta` carries structured numbers (current value, limit, etc.) so the UI can render rich messages without reparsing.
- `class RiskGate(Protocol)` — `check(signal, *, tracker, clock) -> Optional[RiskBlock]`.
- `class AllowAllRiskGate` — trivial gate that approves everything. For tests / opt-out.
- `@dataclass class DefaultRiskGate` — the five v1-essential gates. All limits optional (`None` disables that specific check):
  - `daily_loss_limit: Optional[float]` — refuse when `sum(realized + unrealized) <= limit` (limit is a negative number).
  - `max_concurrent: Optional[int]` — refuse when `len(tracker.list_open()) >= limit`.
  - `max_position_notional: Optional[float]` — refuse when `qty * ref_price > limit`.
  - `no_new_entries_after: Optional[dtime]` — refuse when `clock().time() >= cutoff` (local clock).
  - `per_symbol_max_notional: Optional[float]` — refuse when existing exposure in symbol + new exposure > limit.

## Dependencies
- Internal (TYPE_CHECKING only, to avoid an `entries → core → entries` cycle): `..entries.signals.EntrySignal`, `..positions.tracker.PositionTracker`.
- External: stdlib only (`dataclasses`, `datetime`, `typing`).

## Design Decisions
- **Gates are pure** — no side effects beyond reading the tracker and the injected clock. Lets each rule be unit-tested in isolation with a stub `PositionTracker` and a fixed clock.
- **Protocol, not ABC**: any duck-typed `check()` is accepted. Apps that want to compose multiple gates can stack them with a small wrapper that returns the first non-None.
- **Limits are individually optional**: setting `daily_loss_limit=None` disables that check without affecting the others. Lets users adopt gates incrementally.
- **`RiskBlock.meta` is intentionally untyped (`Dict[str, Any]`)**: each gate decides what numbers to surface. The audit log persists the dict verbatim; the UI can format on demand.
- **`_ref_price` priority**: `signal.price` (LIMIT/STOP_LIMIT) → `signal.stop_price` (STOP) → `signal.extra["ref_price"]` (falling back to legacy `signal.meta["ref_price"]`; MARKET/INDICATOR/SCANNER_ALERT triggers stuff `bar.close` here) → `0.0`. A 0.0 fallback no-ops notional gates rather than crashing.
- **`AllowAllRiskGate`** is provided so the engine can be constructed with a non-`None` default — callers don't need to special-case "no gate".

## Invariants
- A `RiskBlock` is immutable and safe to log directly.
- A gate's `check()` returns `None` to allow, or a `RiskBlock` to refuse — never raises on its happy paths.
- `DefaultRiskGate` evaluates checks in the documented order; the first refusal short-circuits (the order is part of the public contract because it affects which message users see when multiple rules would block).

## Testing
- Covered indirectly via integration smoke tests; `tests/unit/` placement: a per-gate truth-table is recommended (covered indirectly today).
