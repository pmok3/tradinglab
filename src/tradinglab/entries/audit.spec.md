# entries/audit.py — Spec

## Purpose

Append-only JSONL audit log for the entry-strategies subsystem. Mirrors `exits.audit` precisely — same atomic-write pattern, Tk-thread invariant, on-disk schema. Differences: directory (`entries/audit`), `KNOWN_KINDS` whitelist (entry-flavored kinds), and `symbol` / `order_id` columns (entries resolve symbol explicitly).

## File layout

`<cache_dir>/entries/audit/<YYYY-MM-DD>.jsonl` — one file per UTC day, one JSON record per line.

```json
{"ts":"2025-01-15T13:40:11.123+00:00","kind":"entry_fire",
 "strategy_id":"...","symbol":"AAPL","position_id":null,
 "trigger_id":"...","order_id":null,"qty":100,"price":180.25,
 "meta":{"evidence":[...]}}
```

## Public API

```python
class AuditLog:
    def __init__(self, root: Path = audit_dir(),
                 clock: Callable[[], datetime] = _utc_now)

    @require_tk_thread
    def append(
        self, kind: str, *,           # kind must be in KNOWN_KINDS
        strategy_id: Optional[str] = None,
        symbol: Optional[str] = None,
        position_id: Optional[str] = None,
        trigger_id: Optional[str] = None,
        order_id: Optional[str] = None,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        meta: Optional[Dict[str, Any]] = None,
        ts: Optional[datetime] = None,
    ) -> Dict[str, Any]

    def tail(self, n) -> List[Dict[str, Any]]        # any thread
    def list_dates() -> List[str]                    # any thread
    def read_date(date_str) -> List[Dict[str, Any]]  # any thread
    def close() -> None                              # idempotent

KNOWN_KINDS = frozenset({
    "entry_arm", "entry_disarm", "entry_disarm_all",
    "entry_fire", "entry_submit", "entry_fill", "entry_cancel",
    "entry_blocked", "entry_cooldown", "entry_dedup_skipped",
    "entry_bind_failed", "entry_modal_requested",
    "entry_broken_strategy_load",
})
```

## Design Decisions

- **Single-writer Tk-thread invariant.** `append` raises `TkThreadViolation` off-thread. Record-level atomicity follows: single writer flushes after every line; partial writes only possible on hard crash mid-line — readers skip.
- **`KNOWN_KINDS` whitelist.** Typo → `ValueError`. New kinds require editing this module.
- **Day rotation by UTC date.** First `append` of a new day closes the previous handle. Tests inject a fake `clock`.
- **Reader resilience.** `tail` / `read_date` delegate line parsing to `core.io_helpers.read_jsonl`; corrupt / non-object lines are skipped with a logged warning.
- **Duplicate, don't share.** Copy of `exits.audit`; promoting to `core/audit_log.py` would touch exits-v1 with too much blast radius.

## Invariants

- Records always include `ts`, `kind`, and the five optional-id columns (`null` when not provided).
- `qty` / `price` present iff caller supplied them.
- `tail(n)` returns up to `n` records oldest-first.
- `list_dates()` returns YYYY-MM-DD sorted newest-first.

## See also

- Mirror: [`exits/audit.spec.md`](../exits/audit.spec.md).
