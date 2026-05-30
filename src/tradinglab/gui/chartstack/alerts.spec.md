# `chartstack/alerts.py` — four-tier alert engine (M6)

## Purpose
Owns the per-card ChartStack alert evaluation, audio rate-limiter,
and time-of-day gating described in §2.4 of the synthesis. Pure
Python with one Windows-only soft dependency on ``winsound`` for
chimes (no-ops everywhere else).

## Public API

### Class `AlertTier(Enum)`
Severity tiers ordered by ``.value`` so the engine can keep "the
max so far" as a running int comparison:

| Member | Value | Visual | Audio |
|---|---|---|---|
| `NONE` | 0 | — | — |
| `TIER_4_YELLOW` | 1 | Yellow badge | — |
| `TIER_1_AMBER` | 2 | Amber border tint | — |
| `TIER_2_BLUE` | 3 | Blue border tint | 1 chime |
| `TIER_3_RED` | 4 | Red border tint | Double-ping every 5 s |

### Class `AlertResult` (dataclass, frozen)
- `tier: AlertTier` — highest-severity tier this cycle.
- `rule_ids: tuple[str, ...]` — stable ids of every fired rule.
- `badge: Optional[str]` — short text for Tier-4 (``"T-1"`` /
  ``"EX-DIV"``).
- `color` property — hex string via the ``_TIER_COLOR`` table
  (``CAUTION_YELLOW`` / ``WARN_AMBER`` / ``INFO_BLUE`` /
  ``ERROR_RED``).
- `is_active` property — ``tier is not AlertTier.NONE``.

### Class `AlertEngine`
Constructor: `AlertEngine(*, clock=None, play_chime=None)`. Both
kwargs are dependency-injection seams used by tests.

- `evaluate(slot_index, *, bars, interval_minutes, position,
  scanner_row, days_to_earnings, is_exdiv_today, now_utc=None)`
  → `AlertResult`. Runs every detector, applies the time-of-day
  gate (returns the highest tier or `NONE`). Side-effects:
  plays at most two chimes via the rate limiter; updates the
  per-slot edge-detection state (PMH/PML, previous unrealized
  P&L, tier-3 pacing).
- `reset(slot_index=None)` — clears one slot's state, or all
  slots + the global rate-limit window. Called on binding swap
  and on sandbox detach.

### Pure-function detectors
Each returns `Optional[str]` (the rule id when fired). All are
side-effect-free so unit tests can call them directly:

- `evaluate_tier1_rvol_spike(bars, *, interval_minutes,
  rvol_1m_threshold, rvol_5m_threshold)`
- `evaluate_tier1_atr_expansion(bars, *, atr_threshold)`
- `evaluate_tier2_pmh_pml_break(bars)`
- `evaluate_tier2_new_scanner_edge(scanner_row)`
- `evaluate_tier3_stop_proximity(bars, *, position, atr_window)`
- `evaluate_tier3_pnl_zero_cross(*, position, prev_unrealized)`
- `evaluate_tier3_mae_one_r(*, position)`
- `evaluate_tier4_earnings_t1(*, days_to_earnings)`
- `evaluate_tier4_exdiv_today(*, is_exdiv_today)`

## Locked design decisions

### Threshold sources
All thresholds come from `settings_adapter.get(...)`:

| Setting key | Default | Used by |
|---|---|---|
| `chartstack.alerts.audio_muted` | `False` | global mute |
| `chartstack.alerts.rvol_1m` | `2.5` | tier-1 RVOL on 1m bars |
| `chartstack.alerts.rvol_5m` | `1.8` | tier-1 RVOL on 5m+ bars |
| `chartstack.alerts.atr_expansion` | `1.8` | tier-1 TR/ATR(14) |

Tier-3 ATR-window (`0.3`) is hardcoded in `evaluate` until a future
settings-dialog round adds a key.

### Time-of-day gate
| Window (ET) | Factor | Effect |
|---|---|---|
| 09:30–09:35 | `None` | Tier-1 evaluators skipped (opening melt-up is all "extreme") |
| 09:35–10:00 | `2.0` | Tier-1 thresholds doubled |
| 10:00–close | `1.0` | Defaults |
| Outside RTH | `1.0` | Defaults (tier-1 filters to regular bars anyway) |

The factor scales only Tier 1. Tiers 2/3/4 fire on their own primitives
(PMH break, stop touch, earnings T-1) and are trader-meaningful at any time.

### Audio rate-limit
Sliding 10-second window with a 2-chime cap. Tier-3 bypasses the cap (a
stop is one tap away — we want every ping). The cap is engine-global (not
per-slot) so a Tier-2 storm across three cards still burns ≤2 chimes /
10 s. `audio_muted` silences everything including Tier-3. Tier-3 also has
a 5-second per-slot pacing window so a stop-camping position doesn't
ping every tick. The double-ping signals "stop alert, not entry alert".

### Edge detection
Tier-2 (PMH/PML break, new scanner edge) is edge-triggered: must
transition from "not firing" to "firing" before the chime. Per-slot
`_prev_pmh_break` / `_prev_pml_break` flags; scanner edge sources from
`MatchRow.is_new` which is itself edge-triggered upstream.

### Best-effort owner state
The engine never imports ChartApp. `evaluate` takes snapshot inputs
(bars / position / scanner row / event flags) as kwargs; the panel's
`_evaluate_alerts_for_slot` brokers reads via `owner_state.py` so unit
tests can hand deterministic inputs.

### Audio fallback
`winsound` is imported lazily inside `_play_chime` under a guarded `try`.
Non-Windows runners get a silent no-op. On Windows we use `SND_NODEFAULT`
so a missing system sound alias falls back to silence (not the jarring
default `MessageBeep`).
