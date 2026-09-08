"""Chart capability resolution and credential-driven shared stream lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .base import STREAM_SOURCES, StreamSource, register_stream
from .intraday import CHART_INTERVALS
from .quotes import QUOTE_SOURCES, register_quote_source, unregister_quote_source
from .schwab import SchwabStreamSource
from .schwab_quotes import make_source


@runtime_checkable
class CloseableStream(Protocol):
    def close(self) -> None: ...


@dataclass(frozen=True)
class ChartStreamCapability:
    stream_name: str
    native_intervals: frozenset[str]
    adapted_intervals: frozenset[str] = frozenset()
    equities_only: bool = False


@dataclass(frozen=True)
class ChartStreamSelection:
    source: StreamSource
    provider: str
    native_interval: str


_SCHWAB = ChartStreamCapability("schwab-stream", frozenset({"1m"}), CHART_INTERVALS, True)
CHART_STREAM_CAPABILITIES = {"schwab": _SCHWAB, "schwab-stream": _SCHWAB}
_QUOTE_STREAM_OWNERS = {"schwab-quotes": "schwab-stream"}
_registered_identity: tuple[str | None, str | None, str | None] | None = None
_oauth_disconnected = False


def quote_registry_binding(name: str) -> tuple[object, object]:
    """Identity of a quote factory and any lazily resolved shared transport."""
    owner = _QUOTE_STREAM_OWNERS.get(name)
    return QUOTE_SOURCES.get(name), STREAM_SOURCES.get(owner) if owner else None


def resolve_chart_stream(
    source_name: str, ticker: str, interval: str,
    stream_sources: Mapping[str, StreamSource] = STREAM_SOURCES,
) -> ChartStreamSelection | None:
    """Resolve provider capability, keeping event/cache names independent."""
    from ..data.auto_source import AUTO_SOURCE_NAME, last_resolved_source, resolve_auto_source
    from ..data.index_aliases import canonical_index_name
    from ..data.ratio_source import is_quotient_ratio, is_scaled_symbol

    provider = source_name
    if provider == AUTO_SOURCE_NAME:
        provider = resolve_auto_source()
        if last_resolved_source() != provider:
            return None
    capability = CHART_STREAM_CAPABILITIES.get(provider)
    if capability is None:
        source = stream_sources.get(provider)
        return ChartStreamSelection(source, provider, interval) if source is not None else None
    if capability.equities_only and (
        is_scaled_symbol(ticker) or is_quotient_ratio(ticker)
        or "/" in ticker or canonical_index_name(ticker) is not None
        or ticker.startswith(("^", "$", "I:", "/"))
    ):
        return None
    if interval in capability.native_intervals:
        native_interval = interval
    elif interval in capability.adapted_intervals:
        native_interval = "1m"
    else:
        return None
    source = stream_sources.get(capability.stream_name)
    return ChartStreamSelection(source, provider, native_interval) if source is not None else None


def close_vendor_streams() -> bool:
    """Terminally close and unregister both Schwab capabilities."""
    source = STREAM_SOURCES.get("schwab-stream")
    if source is not None:
        if not isinstance(source, CloseableStream):
            raise TypeError("Registered Schwab stream does not implement close()")
        source.close()
        STREAM_SOURCES.pop("schwab-stream", None)
    removed_quote = unregister_quote_source("schwab-quotes")
    return source is not None or removed_quote


def reconcile_vendor_streams(*, reset: bool = False, oauth_connected: bool | None = None) -> bool:
    """Reconcile presence without network I/O; preserve unchanged singleton state.

    Token invalidation belongs to the credential/OAuth caller. ``reset`` only
    closes the old transport: newly saved OAuth tokens must not be deleted.
    """
    from ..data.credentials import get_credentials

    global _registered_identity, _oauth_disconnected
    credentials = get_credentials().schwab
    identity = (credentials.app_key, credentials.app_secret, credentials.redirect_uri)
    identity_changed = _registered_identity is not None and identity != _registered_identity
    if identity_changed:
        _oauth_disconnected = False
    if oauth_connected is not None:
        _oauth_disconnected = not oauth_connected
    source = STREAM_SOURCES.get("schwab-stream")
    changed = False
    if reset or identity_changed or not credentials.is_configured() or _oauth_disconnected:
        changed = close_vendor_streams()
        source = None
    if credentials.is_configured() and not _oauth_disconnected:
        if source is None:
            register_stream("schwab-stream", SchwabStreamSource())
            changed = True
        if "schwab-quotes" not in QUOTE_SOURCES:
            register_quote_source("schwab-quotes", make_source)
            changed = True
    else:
        changed = unregister_quote_source("schwab-quotes") or changed
    _registered_identity = identity
    return changed
