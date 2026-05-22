"""Entry-strategy module — symbol-keyed entries that create new positions.

Mirrors the :mod:`tradinglab.exits` package pattern:
- :mod:`.model` — dataclasses + validation
- :mod:`.spec` — pure trigger-evaluation functions
- :mod:`.sizing` — qty computation from sizing rules
- :mod:`.storage` — JSON persistence
- :mod:`.signals` — EntrySignal + EntryPaperSink
- :mod:`.audit` — JSONL audit log (subsystem="entry")
- :mod:`.evaluator` — runtime that fans out across symbols and fires triggers

Entries differ from exits in two fundamental ways:

1. Lifecycle: exits modify an existing :class:`Position`; entries CREATE one.
   The fill chain is `evaluator -> signal -> paper engine -> tracker.open_from_fill`.
2. Universe: exits attach to a known position id; entries fan out across a
   `Universe` (explicit symbols list, scanner alert subscription, or the
   currently-charted symbol).

See ``files/entries_v1_plan.md`` in the session workspace for the full design.
"""

from __future__ import annotations

__all__: list[str] = []
