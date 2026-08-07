"""Registry of historical shares-outstanding providers.

Mirrors the price-source registry in :mod:`data.base`: providers register
under a name, and consumers resolve a name to a fetcher rather than
importing a vendor module directly. The active choice is the
``shares_data_source`` tunable, resolved by a **higher-level** caller
(the sandbox heatmap window) and injected downstream — so no low-level
module hardcodes a vendor, and swapping EDGAR for a paid fundamentals
feed later is a registration plus a settings change, not a refactor.

A shares fetcher is ``(symbol) -> list[SharesFact]``, ascending by
``as_of_ts``. :class:`SharesFact` deliberately carries **both** dates:

* ``as_of_ts`` — the date the count describes. The split-basis lift is
  anchored here, because the count is on *that* date's basis.
* ``filed_ts`` — when the number became public. Consumers replaying
  history must filter on this: a count is not knowable before it is
  filed, and the gap is typically two weeks.

Returning ``[]`` is the contract for "no data" (non-filer, outage,
pre-XBRL); it must never raise. See ``data/shares_sources.spec.md``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple


class SharesFact(NamedTuple):
    """One reported shares-outstanding observation.

    JSON round-trips as ``[as_of_ts, filed_ts, shares]``.
    """

    as_of_ts: int
    filed_ts: int
    shares: float


#: ``(symbol) -> list[SharesFact]``.
SharesFetcher = Callable[[str], list[SharesFact]]

#: Name of the provider used when the tunable is empty / unknown.
DEFAULT_SHARES_SOURCE = "edgar"

#: Registered factories: ``name -> () -> SharesFetcher``. Factories (not
#: instances) so a provider can hold per-session caches without the
#: registry becoming shared mutable state.
SHARES_SOURCES: dict[str, Callable[..., SharesFetcher]] = {}


def register_shares_source(
    name: str, factory: Callable[..., SharesFetcher]
) -> None:
    """Register a shares provider factory under ``name``.

    Idempotent: repeat registrations overwrite, so tests can stub a real
    provider the same way ``data.base.register_source`` allows.
    """
    key = (name or "").strip().lower()
    if not key:
        raise ValueError("shares source name must be non-empty")
    SHARES_SOURCES[key] = factory


def unregister_shares_source(name: str) -> bool:
    """Remove ``name``; returns True when something was removed."""
    return SHARES_SOURCES.pop((name or "").strip().lower(), None) is not None


def available_shares_sources() -> list[str]:
    """Registered provider names, sorted."""
    return sorted(SHARES_SOURCES)


def null_shares_fetcher(_symbol: str) -> list[SharesFact]:
    """A fetcher that knows nothing. Never raises, never touches a network.

    This is the fallback when no provider is registered or the configured
    one is unknown — chosen deliberately over silently falling back to a
    concrete vendor, so a misconfiguration surfaces as "sizes unavailable"
    (tiles render approximate) rather than as plausible-looking numbers
    from a source nobody selected.
    """
    return []


def resolve_shares_fetcher(
    name: str | None = None, **kwargs: object
) -> tuple[str, SharesFetcher]:
    """Resolve a provider name to ``(resolved_name, fetcher)``.

    ``name`` defaults to the ``shares_data_source`` tunable, then to
    :data:`DEFAULT_SHARES_SOURCE`. An unknown or unregistered name
    resolves to :func:`null_shares_fetcher` rather than raising, so a bad
    setting degrades the heatmap instead of breaking session start.
    ``kwargs`` are forwarded to the factory (e.g. ``cik_lookup``).
    """
    key = (name or "").strip().lower()
    if not key:
        try:
            from .. import defaults as _defaults

            key = str(_defaults.get("shares_data_source") or "").strip().lower()
        except Exception:  # noqa: BLE001 - settings must never break resolution
            key = ""
    if not key:
        key = DEFAULT_SHARES_SOURCE
    factory = SHARES_SOURCES.get(key)
    if factory is None:
        return (key, null_shares_fetcher)
    try:
        return (key, factory(**kwargs))
    except Exception:  # noqa: BLE001 - a broken factory must not break start
        return (key, null_shares_fetcher)


__all__ = (
    "SharesFact",
    "SharesFetcher",
    "DEFAULT_SHARES_SOURCE",
    "SHARES_SOURCES",
    "register_shares_source",
    "unregister_shares_source",
    "available_shares_sources",
    "null_shares_fetcher",
    "resolve_shares_fetcher",
)
