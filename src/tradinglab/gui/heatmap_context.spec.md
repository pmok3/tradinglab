# gui/heatmap_context.py — Spec

## Purpose
The clock + session context the heatmap window runs against, in two
flavours: replay (sandbox controller) and live (wall clock).

The window was written against a `SandboxController` but only ever reads
a handful of loosely-coupled attributes, and reads every one through
`getattr(..., default)`. That tolerance is what makes de-sandboxing
cheap: an object exposing the same names **is** a controller as far as
the window is concerned, so live mode is a different context object
rather than a branch through the render path.

## Public API
- `MARKET_STATES = ("pre", "regular", "post", "closed")`.
- `market_state_at(ts=None) -> str` — classify epoch seconds (default
  now) into a market state.
- `class SandboxHeatmapContext(controller)` — `is_live = False`;
  delegates unknown attributes to the wrapped controller;
  `market_state()` is always `"regular"`; `is_active()` guards the
  controller's own.
- `class LiveHeatmapContext(app, *, clock=time.time)` — `is_live = True`,
  `blind = False`; `clock_ts()`, `market_state()`,
  `current_session_date()` (**UTC** — see Design Decisions),
  `is_active()`, and the controller-shaped properties `data_source`,
  `interval`, `focus_symbol`, `positions_snapshot()`, `set_focus()`.

## Dependencies
- Internal: [`core/session_calendar`](../core/session_calendar.spec.md)
  (`classify_session`), [`core/timezones`](../core/timezones.spec.md)
  (`to_et` — §7.23), `constants.is_intraday` (late import).
- External: stdlib only. No Tk, no matplotlib.

## Design Decisions
- **The live clock is never clamped.** An earlier sketch pinned the
  clock to the last completed session outside market hours. That is
  backwards: the clock is not the uncertain thing, the *data* is.
  Clamping would make a Saturday map claim to be Friday-at-the-close,
  hiding that nothing has updated in two days. Reporting the true clock
  and labelling the state keeps staleness visible, and the price source
  already degrades correctly — with no intraday bars for a weekend
  "session" it falls back to the last two completed daily sessions,
  which is exactly the right picture.
- **`current_session_date()` is UTC, deliberately, despite everything
  else here being exchange-local.** It exists only to detect a session
  roll, and the thing it must agree with is `SessionPriceSource`'s
  per-session snapshot, which is keyed on
  `backtest.heatmap.session_date_of` (UTC) — the same key
  `SandboxController.current_session_date()` uses. Returning the ET date
  instead is a real bug, not a cosmetic mismatch: between 00:00 UTC and
  00:00 ET (19:00 ET in EST, 20:00 in EDT) the two keys disagree, so the
  snapshot's validity window lapses and every symbol returns
  `(None, None)` while the roll detector still sees "same day" and never
  re-primes. The map would sit fully unpriced — every tile neutral,
  every size floored — for about five hours every evening, and only in
  the "left it open through the afternoon" case.
- **`market_state` exists on both, but means different things.** Replay
  is always mid-session by construction (the engine only steps through
  bars that exist), so it answers `"regular"` rather than pretending to
  classify a historical wall clock. Live actually classifies.
- **Overnight is `closed`, not `pre`.** `classify_session` folds
  everything outside `[09:30, 20:00)` into `"pre"` because that is the
  right *bar* tag. A live map needs 08:00 (tradeable extended prints)
  distinguished from 02:00 (nothing), so this module narrows `pre` to
  `[04:00, 09:30)`.
- **Holidays are not enforced.** The app has no exchange calendar, and
  inventing one here would create a second source of truth against the
  data layer. A holiday reads as its weekday state with no bars behind
  it, which the window's staleness treatment surfaces honestly anyway.
- **A daily chart interval falls back to `5m`.** The live context feeds
  the *price* leg, and a daily bar carries the settled close while being
  stamped at the open — the exact look-ahead the sandbox path had to be
  hardened against (`backtest/heatmap.spec.md` Invariant 6). Reading the
  user's daily interval here would reintroduce it.
- **`SandboxHeatmapContext` delegates via `__getattr__`** rather than
  enumerating the controller surface, so `engine`, `set_focus` and
  anything added later stay reachable without this class tracking them.
- **`LiveHeatmapContext.set_focus` is a no-op.** Live has no replay
  focus; the window's app-level load path handles click-to-chart.

## Invariants
- `market_state_at` returns a member of `MARKET_STATES` and never
  raises (a bad timestamp reads as `"closed"`).
- `LiveHeatmapContext.current_session_date()` equals
  `backtest.heatmap.session_date_of(clock)` at **every** hour of the
  day — pinned by a parametrised test across both EST and EDT, because
  the failure only manifests in the UTC/ET gap.
- Neither context raises from any accessor; failures degrade to `""`,
  `None`, or an empty list.
- `LiveHeatmapContext.interval` is always an intraday interval.
- ET conversion goes through `core.timezones` (§7.23), never a direct
  `ZoneInfo`.

## Testing
`tests/unit/gui/test_heatmap_context.py` — state classification across
the session boundaries and the weekend, overnight vs pre-market, bad
timestamps, the daily→5m interval fallback, sandbox delegation, and
that neither context raises when the wrapped object is missing
attributes.

## Known limitations / Future work
- No exchange holiday calendar, and no half-day (13:00 close) handling.
  Both belong in `core/session_calendar` if they land, not here.

## Recent history
- Introduced when the heatmap gained a live mode, so the window could
  run outside a replay session without branching its render path.
