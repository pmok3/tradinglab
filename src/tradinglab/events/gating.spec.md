# events/gating.py — Spec

## Purpose
Pure (no Tk/mpl) sandbox gating layer for events. Mirrors `SandboxController.daily_visible_for`'s strictly-less-than discipline: only events at-or-before the current clock are visible, and blind-mode replays receive a *relative* "in N trading days" badge instead of an absolute forward date.

## Public API
- `@dataclass(frozen=True) class ForwardEarningsBadge(trading_days_until, when)` — blind-safe forward descriptor.
- `@dataclass class EventsView(past_earnings=[], past_dividends=[], forward_earnings=[], forward_dividends=[], forward_badges=[])` — gated per-symbol view.
- `events_visible_for(bundle, clock_ts, *, blind, forward_window_days=30) -> EventsView`.

## Dependencies
Internal: `.base`. External: `math`, `bisect`, `dataclasses`.

## Design Decisions
- **`clock_ts` is in milliseconds**, matching `EarningsRecord.ts` / `DividendRecord.ex_ts`. Engine clock is seconds — caller converts at boundary.
- **Forward window cap (default 30 days)** keeps tooltips terse and prevents leaking earnings cadence even via relative counts. Beyond 30 days no badge.
- **Past-records defensive NaN-wipe.** Past earnings have `eps_actual` re-set to NaN if the source row's `eps_actual` was NaN — defence against a provider mis-stamping a future row as past.
- **Blind-mode contract.** Forward earnings records (with absolute ts) are NOT included; only the badge is. Forward dividends similarly omitted. Past records remain visible.
- **5/7 trading-day approximation.** Holiday calendars would add a pandas-market-calendars dep for one cosmetic badge digit. Off-by-one on Memorial Day acceptable.

## Invariants
- `past_earnings` sorted ascending by ts.
- `past_dividends` sorted ascending by ex_ts.
- In blind mode `forward_earnings` is always empty.
- `forward_badges` count ≤ forward earnings count inside the window.

## Algorithm
1. Bisect `bundle.earnings.ts` against `clock_ts` to split past vs forward.
2. NaN-wipe future fields on past records.
3. For each forward earnings within `forward_window_ms`, compute `trading_days_until` and emit a badge. Non-blind also emits a redacted-actuals `EarningsRecord`.
4. Bisect `bundle.dividends.ex_ts` against `clock_ts`. Past divs verbatim. Forward divs exposed only in non-blind, capped to same window.
