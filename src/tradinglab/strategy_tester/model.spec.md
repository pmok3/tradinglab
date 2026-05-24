# strategy_tester/model.py — Spec

## Purpose
Tk-free, side-effect-free pure-data model for the Strategy Tester feature. Owns the on-disk and in-memory schema for `UniverseSpec`, `CostModel`, `TestConfig`, and `TestRun`, plus the deterministic `make_run_id()` derivation that gives the feature its reproducibility envelope.

## Public API
- `CURRENT_SCHEMA_VERSION: int = 1` — bumped intentionally on schema breaks.
- `class UniverseKind(str, Enum)` — `SYMBOLS / WATCHLIST / PRESET`. Scanner / current-chart deliberately excluded per the design decision.
- `class RunStatus(str, Enum)` — `PENDING / RUNNING / DONE / CANCELLED / FAILED`.
- `class DatePreset(str, Enum)` — `YTD / LAST_1Y / LAST_3Y / LAST_5Y / LAST_10Y / MAX / CUSTOM`.
- `@dataclass(frozen=True) class UniverseSpec(kind, symbols, watchlist_name, preset_id)` — tagged-union; XOR enforced by `validate_config`.
- `@dataclass(frozen=True) class CostModel(slippage_bps, commission_per_trade, commission_per_share)` — defaults match consensus (5 bps / $0 / $0).
- `@dataclass(frozen=True) class TestConfig` — full envelope: entry_strategy_id, exit_strategy_id, universe, start/end_date, interval, starting_cash, cost_model, date_preset, rng_seed, schema_version, user_label, include_extended_hours.
- `@dataclass class TestRun` — runtime manifest record persisted as `manifest.json`.
- `validate_config(cfg) -> list[str]` — human-readable errors (empty = valid).
- `make_run_id(cfg, *, engine_version) -> str` — 12-hex-char prefix of SHA-256(canonical_json + "|" + engine_version).

## Dependencies
- `hashlib`, `json`, `time`, `uuid`, `dataclasses`, `enum`, `typing` (stdlib only).
- Does NOT import any Tk-coupled or app-side module — safe to import from worker threads.

## Design Decisions
- **`TestConfig` is frozen, `TestRun` is not** — config is intent (immutable for the run); the manifest accumulates state as symbols complete. Same boundary as `SessionSpec` / `SessionResult`.
- **Validation is a separate pass (`validate_config`), not in `__post_init__`** — same convention as `entries.model` / `exits.model`. The GUI builds half-edited drafts in-flight.
- **`make_run_id` derives from `canonical_json()` which EXCLUDES `user_label`** — relabelling a run does not change its run_id. Includes `rng_seed` so reseeding produces a different id.
- **Canonical JSON uses `sort_keys=True` + tight separators** — required for byte-stable hashing across Python versions / machines / dict-iteration orders.
- **Re-running identical config always creates a new on-disk Run** (per user decision) — the on-disk directory name appends an ISO timestamp; `run_id` is the config-fingerprint portion only. Two runs of identical configs share `run_id` but live in different directories.
- **`UniverseKind.SCANNER` and `from_attached_chart` are deliberately absent** — per the universe scoping decision, mechanical testing across symbols doesn't fit scanner-driven (presupposes live ticks) or single-symbol (not really a Strategy Tester) sources.
- **`commission_per_share` is on `CostModel`, threaded through to `SessionSpec.commission_per_share`** — additive field on the engine spec (no `ENGINE_VERSION` bump).
- **`date_preset` is persisted alongside resolved `start_date` / `end_date`** — when a user picks "Last 3Y" on Monday and the same config is re-rendered Tuesday the resolved dates are stable (no off-by-one bugs) but the preset label is shown in the GUI for clarity.
- **`include_extended_hours` defaults to `False` (RTH-only)** — premarket (04:00-09:30 ET) and postmarket (16:00-20:00 ET) bars otherwise leak into indicator math and skew EMA / SMA / RSI / VWAP at the open. The runner filters non-RTH bars before they reach the evaluator. The GUI exposes an opt-in checkbox with a warning label. Missing key in old JSON manifests deserialises to `False` for back-compat.

## Invariants
- `TestConfig.to_dict() → from_dict → to_dict` round-trips identically.
- `make_run_id(cfg, engine_version=v)` returns the same 12-char hex for two `TestConfig`s with identical `canonical_dict()` outputs.
- `validate_config(cfg) == []` is the gate for storage save and run submit.
- `UniverseSpec.kind` and the populated optional field always agree: `SYMBOLS` ↔ `symbols`, `WATCHLIST` ↔ `watchlist_name`, `PRESET` ↔ `preset_id`.

## Testing
- `tests/unit/strategy_tester/test_model.py` — round-trip JSON, validation error cases, `make_run_id` stability across instantiations, canonical_json excludes user_label.

## See also
- [universe](universe.spec.md) — resolves a `UniverseSpec` to a concrete symbol list.
- [storage](storage.spec.md) — persists `TestRun` + `TestConfig` to `%LOCALAPPDATA%\TradingLab\strategy_tests\<run_id>-<ts>\`.
- [runner](runner.spec.md) — consumer.
- `backtest/session.spec.md` — sibling reproducibility envelope (`SessionSpec`).
