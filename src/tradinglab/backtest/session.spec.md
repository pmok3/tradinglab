# backtest/session.py — Spec

## Purpose
Reproducibility-grade record types for the sandbox kernel: `SessionSpec` is everything needed to deterministically replay a session; `SessionResult` is the full output (fills + journals + equity curve + final cash). Both round-trip through `to_dict` / `from_dict` with byte-stable JSON, locked in as the contract that Phase 2 leaderboards / walk-forward analysis will rely on.

## Public API
- `ENGINE_VERSION: str = "sandbox-1d"` — bumped intentionally on schema breaks.
- `@dataclass(frozen=True) class SessionSpec` — `deck_seed`, `tickers`, `start_clock_iso`, `slippage_bps`, `commission`, `engine_version` (default `ENGINE_VERSION`), `setup_tags`, `starting_cash` (default 100_000), `commission_per_share` (default 0.0), `include_extended` (default False), `auto_cycle` (default False), `cycle_dates` (default `()`), `universe_id` (default `""`), `universe_symbols` (default `()`), `strict_offline` (default False). `to_dict` / `from_dict` with stable key order.
- `@dataclass class SessionResult` — `spec`, `fills`, `pre_trades`, `post_trades`, `equity_curve: List[Tuple[int, float]]`, `final_cash`, `cash_adjustments: List[CashAdjustment]` (default empty), `quantity_adjustments: List[QuantityAdjustment]` (default empty). `to_dict` / `from_dict` round-trip both adjustment lists via `_cash_adj_to_dict` / `_qty_adj_to_dict` and their `_from_dict` inverses; legacy JSON without these keys hydrates with empty lists (additive default).

## Dependencies
- Internal: `.journal.PostTradeReview`, `.journal.PreTradeEntry`, `.orders.Fill`, `.orders.Side`.

## Design Decisions
- **Phase 1d defaults are back-compat optional in JSON**: `include_extended`, `auto_cycle`, `cycle_dates` all default to safe values in `from_dict` when absent, so a Phase 1c saved session loads cleanly.
- **Sandbox-preload defaults are back-compat optional in JSON**: `universe_id` (`""`), `universe_symbols` (`()`), and `strict_offline` (`False`) all default-safe in `from_dict` so saved sessions written before the universe-preload feature load cleanly. Empty `universe_id` means *legacy unrestricted mode* — the engine never enforced a universe, mid-session live fetches were allowed. The fields are persisted as authoritative metadata: a saved session that has `strict_offline=True` and a non-empty `universe_symbols` documents the exact membership that was allowed at the time, even if the user-side basket has since drifted.
- **`tickers` vs `universe_symbols` separation**: `tickers` records what the user actually loaded / traded during the session (lower-cardinality, derived from order activity). `universe_symbols` records the full set the user was *allowed* to trade (higher-cardinality, fixed at session start). The two are tracked separately so a 4-ticker trading session against the SP500 universe doesn't fold into a 4-element `tickers` list and lose the "this was an SP500 session" context.
- **`SessionSpec` is frozen, `SessionResult` is not**: the spec is intent (immutable for the run); the result accumulates over the session. The frozen-vs-mutable boundary makes "what did the user ask for vs. what did the engine do" explicit.
- **Canonical key order via explicit `to_dict` literals**: not `dataclasses.asdict` — that gives field-declaration order without guaranteeing `Side` enums are stringified. Manual `to_dict` lets us emit `side.value` directly.
- **`start_clock_iso` records intent, not authority**: the actual master timeline is derived from the bar data; this field captures what the user asked for (e.g. session-day cursor on `2025-04-29 09:30 ET`) so a saved session is human-readable.
- **`engine_version` travels in the spec, not the envelope**: a future Phase 2 spec format will bump it; loaders compare against `ENGINE_VERSION` to decide whether to fail loudly.
- **Blind mode — what's hidden, what isn't** — Future bars are not revealed to the chart, the price axis is anchored as if `now` were the right edge so the trader cannot peek at upcoming highs/lows by zooming out, and the date readout is suppressed (only time-of-day shows). Indicator values are recomputed bar-by-bar with no look-ahead. NOT hidden: time-of-day still shows (e.g. `09:35 ET`), so session-relative position can be inferred. Behaviour is mirrored in [`replay.spec.md`](replay.spec.md).
- **Eligibility is bar-count based, not exchange-calendar** — `min_bars_per_day` (default 20) is the only filter. Half-days, post-IPO first day, and partial-data holidays are rejected by the same threshold; non-standard hours that meet the threshold are not flagged. Cross-link: same rule lives in [`deck.spec.md`](deck.spec.md).
- **`starting_cash` (default $100k USD) is the only buying-power constraint** — no margin, no leverage, no PDT model. The engine does not pre-check cash before filling: orders that breach cash fill anyway and `Portfolio.cash` is allowed to go negative. No reject, no clip, no log. Sizing is the caller's responsibility.
- **`commission_per_share` is additive on top of flat `commission`** — total per-fill commission is `commission + commission_per_share * abs(quantity)`. Default-zero preserves back-compat: existing `sandbox-1d` saves load cleanly without bumping `ENGINE_VERSION` per the additive-fields decision. Introduced for the Strategy Tester to model brokers like Interactive Brokers ($0.005 / share).

## Invariants
- `SessionSpec.to_dict() → from_dict → to_dict` round-trips byte-identically.
- `SessionResult.to_dict()` produces the same JSON-string for two engine runs over the same `(SessionSpec, bars_by_symbol)` (Q-12 reproducibility).
- `equity_curve` elements are `(int64 epoch seconds, float64 equity)` in memory; encoded as `[[ts, value], ...]` (list of two-element lists, not tuples) so the JSON is plain.

## Testing
- `check_f1_session_reproducibility` — same spec replayed twice yields byte-identical `to_dict()` output.
- `check_b5_sandbox_save_load` — round-trip through `persistence.save_session` / `load_session`.

## See also
- [persistence](persistence.spec.md) for the on-disk envelope, [engine](engine.spec.md) for what populates the result.
