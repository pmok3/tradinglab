"""Data-source protocol, type aliases, and registry helpers.

A *data source* is a plug-in that turns a ``(ticker, interval)`` pair
into a historical OHLCV series. Sources live in
``tradinglab.data.<provider>`` modules and register themselves into
:data:`DATA_SOURCES` at import time (see ``__init__`` of this package).

To add a new provider:

1. Create ``tradinglab/data/<name>_source.py`` exporting a function
   with the :data:`DataFetcher` signature.
2. Call :func:`register_source` (or add an entry to :data:`DATA_SOURCES`
   during module import).
3. Import the module from :mod:`tradinglab.data.__init__` so it's
   picked up on package load.
"""

from __future__ import annotations

from collections.abc import Callable

from ..models import Candle

# A source fetcher takes (ticker, interval) and returns candles or None
# on failure (import error, network error, empty result — all treated
# equivalently by the app).
DataFetcher = Callable[[str, str], list[Candle] | None]


# Global registry. Populated by submodules at import time. The dict
# preserves insertion order — the UI uses the first user-visible entry
# as the default selection.
DATA_SOURCES: dict[str, DataFetcher] = {}

# Subset of ``DATA_SOURCES`` keys that are present in the registry for
# internal use (smoke tests, sandbox replay, offline scaffolding) but
# MUST NOT be surfaced in user-facing UI surfaces — the source-selector
# combobox, the Settings → Startup parameters source dropdown, etc.
# Synthetic sources are registered as internal so the end user never
# sees an option they aren't meant to pick.
_INTERNAL_SOURCES: set[str] = set()


def register_source(
    name: str, fetcher: DataFetcher, *, internal: bool = False,
) -> None:
    """Register a new data source under ``name``.

    Idempotent: repeat registrations overwrite. This is intentional so
    smoke tests can stub real sources by calling
    ``register_source("yfinance", fake)``.

    Set ``internal=True`` for sources that should remain dispatchable
    (tests, sandbox replay, programmatic offline use) but be hidden from
    every user-facing combobox / dropdown. The synthetic data sources
    use this flag so they don't pollute the source-selector UI for
    discretionary traders who never need them.

    Re-registering an existing key clears any prior ``internal`` flag
    unless explicitly re-set — so a smoke test that stubs
    ``register_source("synthetic", fake)`` without ``internal=True``
    would un-hide synthetic. In practice the synthetic sources are
    only stubbed via direct ``DATA_SOURCES[...] = ...`` assignment by
    tests (which bypasses ``register_source`` entirely and therefore
    preserves the internal flag), so this is a non-issue.
    """
    DATA_SOURCES[name] = fetcher
    if internal:
        _INTERNAL_SOURCES.add(name)
    else:
        _INTERNAL_SOURCES.discard(name)


def is_internal_source(name: str) -> bool:
    """Return True if ``name`` is registered as an internal-only source."""
    return name in _INTERNAL_SOURCES


def user_visible_sources() -> list[str]:
    """Return the subset of ``DATA_SOURCES`` keys safe to show in UI.

    Preserves the registration order of the underlying dict (so the
    first user-visible entry remains the default selection). Excludes
    every key flagged with ``internal=True`` at registration time.

    Used by:
    - the toolbar source-selector combobox
    - the Settings → Startup parameters source dropdown
    - the ConfigManager source allow-list (so a hand-edited
      settings.json with ``source="synthetic"`` falls back to the
      builtin default rather than being silently honoured).
    """
    return [name for name in DATA_SOURCES if name not in _INTERNAL_SOURCES]
