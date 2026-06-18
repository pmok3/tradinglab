"""Historical data sources.

Each source module calls :func:`register_source` at import time to
populate :data:`DATA_SOURCES`. The app consults that dict to populate
the source-selector combobox and to dispatch fetches.

Public API (re-exported here for backward compatibility with the old
``tradinglab.data_sources`` module)::

    DATA_SOURCES       — registry {name: fetcher}
    DataFetcher        — the (ticker, interval) -> candles callable type
    register_source    — imperative registration helper
    fetch_live_data    — yfinance-backed fetcher
    fetch_synthetic_data / fetch_synthetic_stream_bootstrap — offline fetchers

Normalization + parallelism helpers (shared across providers)::

    candles_from_dataframe — vectorized pandas-DataFrame → List[Candle]
    CandleArrays           — numpy side-channel produced by the above
    pop_prebuilt_arrays    — consume the side-channel in _build_series_safe
    fetch_chunks_parallel  — I/O-parallel fetch primitive for chunked providers
"""

from .alpaca_source import candles_from_alpaca_response, fetch_alpaca_data
from .base import (
    DATA_SOURCES,
    DataFetcher,
    is_internal_source,
    register_source,
    user_visible_sources,
)
from .controller import DataController
from .credentials import (
    AlpacaCredentials,
    Credentials,
    PolygonCredentials,
    SchwabCredentials,
    get_credentials,
)
from .fetch_service import FetchService
from .local_source import discover_subsources, make_local_fetcher
from .normalize import (
    CandleArrays,
    candles_from_dataframe,
    candles_from_json_rows,
    pop_prebuilt_arrays,
    stash_arrays,
)
from .parallel import fetch_chunks_parallel
from .polygon_source import candles_from_polygon_response, fetch_polygon_data
from .ratio_source import (
    RATIO_DELIMITER,
    RATIO_PRESETS,
    RATIO_SYMBOLS,
    canonical_ratio_symbol,
    compute_ratio_candles,
    fetch_ratio,
    is_ratio_symbol,
    parse_ratio_symbol,
    ratio_display_label,
)
from .schwab_source import candles_from_schwab_response, fetch_schwab_data
from .synthetic_source import fetch_synthetic_data, fetch_synthetic_stream_bootstrap
from .yfinance_source import fetch_live_data

# Register the built-ins in the same order the old flat module did — the
# UI's default source selection keys off the first user-visible entry
# (``yfinance``). The synthetic sources stay in DATA_SOURCES so smoke
# tests / sandbox replay / offline scaffolding can still dispatch to
# them programmatically (via ``DATA_SOURCES["synthetic"]``), but the
# ``internal=True`` flag keeps them out of the source-selector
# combobox and the Settings → Startup parameters source dropdown so
# the end user never sees an option meant for internal use.
register_source("yfinance", fetch_live_data)
register_source("synthetic", fetch_synthetic_data, internal=True)
register_source("synthetic-stream", fetch_synthetic_stream_bootstrap, internal=True)

# Register OAuth/API-key vendors only when credentials are present, so
# the source-selector dropdown stays uncluttered for users who only
# configured a subset. Toggling registration this way is a deliberate
# UX choice — surfacing a "schwab" entry that fails on every fetch
# would be worse than not showing it at all.
_creds = get_credentials()
# Schwab REST `_http_get_pricehistory` is not yet implemented — see
# ``schwab_source._http_get_pricehistory``. Registration is gated off
# even when credentials are configured so the source-selector dropdown
# never offers a "schwab" option that would silently return no data.
# Re-enable the registration line below once the price-history GET is
# wired up (and remove this comment block).
# if _creds.schwab.is_configured():
#     register_source("schwab", fetch_schwab_data)
if _creds.alpaca.is_configured():
    register_source("alpaca", fetch_alpaca_data)
if _creds.polygon.is_configured():
    register_source("polygon", fetch_polygon_data)


def register_local_sources() -> list[str]:
    """Read ``local_data`` settings and (re-)register all BYOD subsources.

    Called once at import time below, and again from the Configure Local
    Data dialog after the user adds / removes / edits a root. Each
    subdirectory of a configured root becomes one combobox entry named
    ``"<root_name>-<subdir>"`` (see
    :func:`local_source.discover_subsources`).

    Returns the list of source keys that were registered, so callers can
    refresh GUI surfaces that show source counts.

    Local registration is gated on ``settings.local_data.enabled``; an
    explicit boolean opt-in avoids accidentally enumerating an unrelated
    directory the user happens to have configured.
    """
    from .. import defaults
    from .. import disk_cache as _disk_cache

    # Drop previously-registered local-source opt-outs before re-marking.
    # Re-registration happens after the user adds/removes a root; if a
    # source key from a removed root were to persist in _NO_PERSIST it
    # wouldn't cause incorrect behaviour (the source would just be
    # un-cached) but would slowly leak set entries across edits.
    _disk_cache.clear_no_persist()

    try:
        cfg = defaults.get("local_data")
    except KeyError:
        return []
    if not isinstance(cfg, dict) or not cfg.get("enabled"):
        return []
    roots = cfg.get("roots") or []
    if not isinstance(roots, list):
        return []
    from pathlib import Path as _P
    registered: list[str] = []
    for entry in roots:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("name") or "").strip()
        path_str = (entry.get("path") or "").strip()
        if not name or not path_str:
            continue
        for key, _subdir, fetcher in discover_subsources(_P(path_str), name):
            register_source(key, fetcher)
            _disk_cache.mark_no_persist(key)
            registered.append(key)
    return registered


# Register local sources at package-import time, after the network
# sources, so the source-selector combobox shows yfinance first.
register_local_sources()

__all__ = [
    "DATA_SOURCES",
    "DataFetcher",
    "DataController",
    "FetchService",
    "register_source",
    "is_internal_source",
    "user_visible_sources",
    "register_local_sources",
    "make_local_fetcher",
    "discover_subsources",
    "fetch_live_data",
    "fetch_synthetic_data",
    "fetch_synthetic_stream_bootstrap",
    "fetch_schwab_data",
    "fetch_alpaca_data",
    "fetch_polygon_data",
    "candles_from_dataframe",
    "candles_from_json_rows",
    "candles_from_schwab_response",
    "candles_from_alpaca_response",
    "candles_from_polygon_response",
    "CandleArrays",
    "pop_prebuilt_arrays",
    "stash_arrays",
    "fetch_chunks_parallel",
    "RATIO_DELIMITER",
    "RATIO_PRESETS",
    "RATIO_SYMBOLS",
    "canonical_ratio_symbol",
    "compute_ratio_candles",
    "fetch_ratio",
    "is_ratio_symbol",
    "parse_ratio_symbol",
    "ratio_display_label",
    "Credentials",
    "SchwabCredentials",
    "AlpacaCredentials",
    "PolygonCredentials",
    "get_credentials",
]

