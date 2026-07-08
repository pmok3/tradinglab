# `exits/audit.py` — append-only JSONL audit log

## Purpose

Records every state-changing event in the exit-strategies subsystem
so the user can answer "what did the engine do, and when?" via
`tail -f` or the Exits-tab footer. Canonical record for post-mortems
on missed/duplicate fills, OCO behavior, EOD kill switch, panic
flatten.

## File layout

`<cache_dir>/exits/audit/<YYYY-MM-DD>.jsonl` — one file per UTC day,
one JSON record per line.

```json
{"ts":"2025-01-15T12:34:56.789012+00:00","kind":"fire","strategy_id":"...","position_id":"...","leg_id":"...","trigger_id":"...","qty":1.0,"price":180.5,"meta":{"reason":"limit-touched"}}
```

## Single-writer invariant (rule N3)

`AuditLog.append` is `@require_tk_thread`. Off-thread call raises
`TkThreadViolation`. Record-level atomicity follows: single writer
flushes after every line, partial writes only possible on a hard
crash mid-line — readers skip such trailing junk.

## API

```python
class AuditLog:
    def __init__(self, root: Path = audit_dir(), clock: Callable[[], datetime] = _utc_now)

    @require_tk_thread
    def append(
        self,
        kind: str,
        *,
        strategy_id: Optional[str] = None,
        position_id: Optional[str] = None,
        leg_id: Optional[str] = None,
        trigger_id: Optional[str] = None,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        meta: Optional[Dict[str, Any]] = None,
        ts: Optional[datetime] = None,
    ) -> Dict[str, Any]

    def tail(self, n: int) -> List[Dict[str, Any]]      # any thread
    def list_dates() -> List[str]                       # any thread
    def read_date(date_str) -> List[Dict[str, Any]]     # any thread
    def close() -> None
```

`KNOWN_KINDS` whitelists the 13 spec events; `append` raises
`ValueError` on typo. New kinds require editing this module so the
on-disk vocabulary stays auditable.

## Day rotation

Writer caches the open handle for the current UTC day. On first
`append` of a new day, the previous handle is closed and a fresh one
opened against `<new-day>.jsonl`. Tests inject a fake `clock`.

## Reader path

`tail(n)` walks date files newest-first until `n` records gathered,
reverses for oldest-first output. `list_dates` returns YYYY-MM-DD
newest-first; `read_date` returns that day's records oldest-first.
Readers use `core.io_helpers.read_jsonl` and tolerate corrupt /
non-object lines by skipping them with a logged warning.
