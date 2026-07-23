# backtest/journal.py — Spec

## Purpose
Immutable journal records for explicit replay decisions, order submission, and position close.

## Public API
- `DECISION_ACTIONS = ("long", "short", "pass", "watch")` — canonical actions accepted by the controller and decision dialog.
- `@dataclass(frozen=True) class DecisionRecord` — `ts` (UTC epoch seconds), `symbol`, `action`, `setup_tag`, `confidence` (1–5 when captured through the controller), and optional `note`. It is controller-owned and does not imply or require an order.
- `@dataclass(frozen=True) class PreTradeEntry` — `order_id`, `ts`, `symbol`, `side` (`"buy"` / `"sell"`), `setup_tag`, `thesis`, `conviction` (int), `size`, `target` (Optional[float]), `notes`, plus six **event-proximity** fields all defaulting to safe values (additive, back-compat for legacy save files): `next_earnings_ts` (UTC ms, 0 when unknown / blind), `last_earnings_ts` (UTC ms, 0 when unknown), `last_dividend_ts` (UTC ms, 0 when unknown), `last_split_ts` (UTC ms, 0 when unknown), `earnings_proximity_tag` (`"earnings_pre_print"` / `"earnings_post_print"` / `""`), `dividend_proximity_tag` (`"ex_div_day"` / `"post_special_div"` / `""`). Populated by `SandboxController._compute_event_proximity` at submit-order time so post-session analysis can group trades by event proximity without re-fetching the event provider.
- `@dataclass(frozen=True) class PostTradeReview` — `symbol`, `entry_ts`, `exit_ts`, `entry_price`, `exit_price`, `quantity`, `side`, `pnl`, `pnl_pct`, `mae`, `mfe`, `mae_pct`, `mfe_pct`, `ref_pre_trade_id` (Optional[str]), `user_review` (str). `entry_ts` / `exit_ts` are engine UTC epoch seconds (not milliseconds).

## Dependencies
None beyond stdlib.

## Design Decisions
- **Frozen dataclasses**: the engine appends them to `SessionResult.pre_trades` / `post_trades` and never mutates them after. The post-trade modal "edits" by `dataclasses.replace`-ing the list entry in place (controller, not engine) — the original record is never touched in flight.
- **Decision records are explicit only**: no bar advance creates a record; `pass` means the trader deliberately logged a pass, not that no trade occurred.
- **`mae` / `mfe` are dollar-denominated excursions over the holding period**; `mae_pct` / `mfe_pct` are signed percentages of entry price. Both representations are kept because the Performance View columns surface percentages while the trade-table tooltip uses dollar values.
- **MAE / MFE formulas (signed, non-strict)** — for a long: `mae$ = (min(low) − entry) × qty` (so `mae ≤ 0`, equals zero when bar lows never breached entry); `mfe$ = (max(high) − entry) × qty` (`mfe ≥ 0`, equals zero analogously). For a short: `mae$ = (entry − max(high)) × |qty|`; `mfe$ = (entry − min(low)) × |qty|`. Adverse excursion is non-positive and favourable is non-negative on both sides. Percent variants normalise by `entry × |qty|`.
- **Excursion window is fill-bar inclusive on both ends** — the entry bar's full H/L is rolled in (entries fill at that bar's open, so its remaining H/L is reachable) and the exit bar is included up to and including its close.
- **`ref_pre_trade_id` points back at `PreTradeEntry.order_id`** (the same id the user-submitted order was filed under), letting `performance.build_trade_rows` join the two without a side table.
- **Engine emits `PostTradeReview` with `user_review=""`**; the `SandboxController` runs the post-trade callback and replaces the record. Headless callers (smoke, Strategy Tester) leave it empty, which is fine.

## Invariants
- `PreTradeEntry.order_id` is unique within a session result.
- For longs: `mae <= 0 <= mfe`. For shorts: same sign convention (mae is the dollar-loss-equivalent; mfe is the dollar-gain-equivalent).
- `PostTradeReview` for a flat-on-close round-trip has `entry_ts <= exit_ts`.
- `DecisionRecord.action` captured through `SandboxController.log_decision` is in `DECISION_ACTIONS`, and confidence is 1–5.

## Testing
- `check_f0_backtest_kernel` §E — `PostTradeReview` emitted on close with correct MAE/MFE.
- `check_g1_sandbox_phase1c` — controller's post-trade-callback path replaces `user_review` in place.

## See also
- [session](session.spec.md) (JSON round-trip), [performance](performance.spec.md) (join with trade rows).
