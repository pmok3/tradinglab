"""Record-ID minting — the one source of truth for generated identifiers.

Five subsystems (`entries`, `exits`, `scanner`, `strategy_tester`,
`positions`) each shipped a private ``_new_id()`` helper. The copies had
already **drifted into two incompatible on-disk formats**:

* ``uuid.uuid4().hex``      -> 32 chars, dash-less  (entries / exits / strategy_tester)
* ``str(uuid.uuid4())``     -> 36 chars, dashed     (scanner / positions)

Both formats are load-bearing: IDs are persisted inside saved strategies,
scans, runs, and open-position blobs, and are cross-referenced by other
records. Normalizing them to a single spelling would orphan every existing
saved file, so this module deliberately exposes **both** spellings rather
than picking a winner. What it removes is the *duplication* — the choice of
format is now an explicit, named call at each site instead of an accident of
which module the code was copied from.

Call sites keep their module-level ``_new_id`` alias so existing
monkeypatch seams (e.g. ``scanner.model._new_id``) keep working.
"""

from __future__ import annotations

import uuid

__all__ = ["new_id_dashed", "new_id_hex"]


def new_id_hex() -> str:
    """Return a dash-less 32-character UUID4 hex string.

    On-disk format for ``entries``, ``exits`` and ``strategy_tester``.
    """
    return uuid.uuid4().hex


def new_id_dashed() -> str:
    """Return a canonical dashed 36-character UUID4 string.

    On-disk format for ``scanner`` and ``positions``.
    """
    return str(uuid.uuid4())
