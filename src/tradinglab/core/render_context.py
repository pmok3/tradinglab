"""Thread-local render context for cross-symbol indicators.

Some indicators (notably the RRVOL family) need information that the
indicator protocol's ``compute_arr(bars)`` signature does not surface:

* The active **interval** (``"5m"``, ``"1d"``, …) — the render layer
  knows it but ``Bars`` does not carry it.
* The active **data source** (``"yfinance"``, ``"synthetic"``, …) —
  required to scope cross-symbol caches so a source switch does not
  reuse stale reference data.
* The current **primary symbol** — used by RRVOL to detect "primary
  is SPY" and short-circuit to a self-divided 1.0.

Rather than break the indicator protocol with a new kwarg (which would
ripple through every indicator + the scanner engine + the cache), we
expose an opt-in thread-local context that any indicator may consult.

Usage
-----

The render path wraps its compute calls in a ``render_context`` block::

    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = compute_via_bars(ind, bars)

Inside ``ind.compute_arr`` an interested indicator reads
``current_context()`` and degrades gracefully when the keys are missing
(e.g. tests / tournament tools that don't bother setting one).

Thread-safety
-------------

State is held in :class:`threading.local` so the scanner's worker
threads, the Tk render thread, and tournament tools each see their own
stack. Nesting (e.g. drilldown rendering inside a parent render) is
supported; ``render_context`` saves and restores the prior context on
exit.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

_local = threading.local()


def current_context() -> Dict[str, Any]:
    """Return the current render context dict, or an empty dict.

    Callers should treat individual keys as optional; not every render
    site populates every field, and unit tests / tournament tools may
    bypass the context entirely.
    """
    ctx = getattr(_local, "ctx", None)
    if ctx is None:
        return {}
    return dict(ctx)


@contextmanager
def render_context(
    *,
    interval: Optional[str] = None,
    source: Optional[str] = None,
    primary_symbol: Optional[str] = None,
) -> Iterator[Dict[str, Any]]:
    """Push a render context for the duration of the ``with`` block.

    Any key passed as ``None`` is omitted from the resulting context
    dict so callers that only know one field don't shadow another with
    a spurious None.
    """
    payload: Dict[str, Any] = {}
    if interval is not None:
        payload["interval"] = interval
    if source is not None:
        payload["source"] = source
    if primary_symbol is not None:
        payload["primary_symbol"] = primary_symbol.upper()
    prev = getattr(_local, "ctx", None)
    _local.ctx = payload
    try:
        yield payload
    finally:
        _local.ctx = prev
