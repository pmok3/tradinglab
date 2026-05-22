# `exits/model.py`

Pure-logic data model for exit strategies. Tk-free, side-effect-free.

## Aggregates

```
ExitStrategy
├── legs : List[ExitLeg]
│           └── triggers : List[ExitTrigger]      # OR within leg
└── oco_groups : List[OCOGroup]                   # disjoint
```

A `Position` optionally references an `ExitStrategy.id`; binding resolves at attach time and the strategy snapshot is **frozen onto the position** so subsequent template edits do NOT retroactively mutate live positions. Snapshot storage lives in positions-storage.

## TriggerKind

Eight kinds. Tagged-union: which `ExitTrigger` fields are *meaningful* depends on `kind`. `validate_strategy` enforces tagged-union completeness.

| Kind | Required fields | Notes |
|---|---|---|
| `market` | (none) | Fires on arming. |
| `limit` | price OR offset_pct OR offset_dollar | Touched/through. |
| `stop` | price OR offset_pct OR offset_dollar | Touched/through. |
| `stop_limit` | stop trigger + stop_limit_price OR stop_limit_offset | Two-price. |
| `trailing_stop` | trail_unit + trail_value | Optional activation_unit + activation_value gate; `trail_basis` controls intrabar vs close HWM. HWM anchored at entry. |
| `time_of_day` | time_of_day = "HH:MM" | Regular-session 24h. |
| `indicator` | condition (scanner.model.Group) | Optional `interval` override (None ⇒ position interval). `evaluate_intrabar` toggles forming-bar vs close-only. |
| `chandelier` | chandelier_lookback ≥ 1, chandelier_atr_period ≥ 2, chandelier_multiplier ∈ [0.5, 8.0], chandelier_ma_type ∈ {RMA,SMA,EMA,WMA} | LeBeau 1995. Rolling-high since entry capped at `chandelier_lookback`, minus `chandelier_multiplier × ATR` (longs; reversed for shorts). Always ratcheted. Touch trigger. Params **frozen at entry** by evaluator. Distinct from `trailing_stop` (since-entry HWM rather than rolling-window high). |

## OCOGroup

`leg_ids: Tuple[str, ...]` + `cancel_on: Literal["any_fire","full_closeout"]`.

- `"full_closeout"` (default, bracket-friendly): siblings cancel only when `position.qty_open == 0` after a fire — partial profit-take does NOT void the stop.
- `"any_fire"`: traditional OCO; first fire cancels all siblings.

Groups must be **disjoint** (every leg_id in at most one group).

## EOD kill switch

NOT a trigger kind. Strategy-level `eod_kill_switch: bool` + `eod_offset_min: int`. Evaluator fires a market exit for the full remaining qty at `session_close - eod_offset_min`. Default-on; default 5 min early.

## qty_pct semantics

`ExitTrigger.qty_pct` is resolved at **fire time** against the live `position.qty_open`. A 50% trigger fires for half of whatever is open right now, not a snapshot at attach time.

## JSON schema

`schema_version: 2` (current). `to_dict` / `from_dict` are sparse: trigger fields that are `None` are omitted. `migrate(d, from_version)` is the single seam for schema changes.

Schema history:
- **v1** — 7 trigger kinds (no chandelier).
- **v2** — adds `chandelier` + four chandelier-only fields. Purely additive — v1 strategies load cleanly (new fields fall back to defaults).

## Validation

`validate_strategy(strategy) -> List[str]` returns human-readable errors (empty = valid). Storage refuses to save invalid strategies; GUI surfaces them as inline red borders.
