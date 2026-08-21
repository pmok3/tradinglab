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

import functools
from collections.abc import Callable
from dataclasses import dataclass

from ..models import Candle

# A source fetcher takes (ticker, interval) and returns candles or None
# on failure (import error, network error, empty result — all treated
# equivalently by the app). Range-capable sources ALSO accept optional
# kw-only ``start`` / ``end`` datetimes (see :func:`fetch_range` + the
# ``supports_range`` registration flag) to fetch an explicit window instead
# of their default trailing one; ``Callable[...]`` keeps the alias
# back-compatible with the ``(ticker, interval)`` call sites.
DataFetcher = Callable[..., list[Candle] | None]


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

# Sources whose fetcher honours optional kw-only ``start`` / ``end`` datetimes
# — i.e. can fetch an explicit range on demand (targeted intraday fetch, see
# :func:`fetch_range`). Alpaca / Polygon; not yfinance/local/synthetic.
_RANGE_CAPABLE: set[str] = set()

# Per-source *page* fetchers: ``(ticker, interval, *, end, limit) -> list[Candle]``
# returning the most recent ``limit`` bars strictly before ``end`` (``end=None``
# → newest page) in ONE request = one rate-limiter token. Distinct capability
# from ``supports_range`` (which is ``[start,end)``): a source may support
# targeted ranges but not efficient backward pages, or vice versa (principal-SWE
# review). Drives the prefetch scheduler's ``RangeWindowPlanner`` deepening.
_PAGE_FETCHERS: dict[str, DataFetcher] = {}


def _ratio_aware(fetcher: DataFetcher, source_name: str = "") -> DataFetcher:
    """Wrap ``fetcher`` so ratio symbols and index aliases resolve per-source.

    A *ratio symbol* (``NUM/DEN`` — e.g. ``IGV/SMH``; see
    :mod:`tradinglab.data.ratio_source`) can't be fetched as a single
    vendor ticker: no data provider has a symbol literally named
    ``IGV/SMH``. Historically only the yfinance fetcher decomposed
    ratios (its own internal hook), so a ratio typed while a DIFFERENT
    source (Alpaca / Polygon) was active was passed through verbatim and
    failed with "Ratio '…' could not be loaded. Check that both legs are
    valid tickers" — even though each leg fetched fine on its own.

    Wrapping at registration makes ratio resolution **source-agnostic**:
    every fetcher in :data:`DATA_SOURCES` decomposes ``NUM/DEN`` into its
    two legs and fetches each from the SAME source, so ratios work on any
    source at every call site (main chart, compare, prefetch, watchlists,
    sandbox, strategy tester, targeted range fetch) with no per-site
    wiring. ``**kwargs`` (e.g. range-fetch ``start`` / ``end``) are
    forwarded to each leg so the targeted-range path works for ratios too.

    The same chokepoint also applies **index-symbol aliases** (see
    :mod:`tradinglab.data.index_aliases`) when ``source_name`` is given, so
    a bare ``VIX`` becomes ``^VIX`` on yfinance / ``$VIX`` on Schwab no
    matter which surface asked — including watchlist and scanner rows that
    never pass through the ticker box. Aliasing happens FIRST so ratio legs
    are already in vendor form by the time they are fetched, and a scale
    constant (``^VIX/15.87``) is never mistaken for a symbol.

    Both behaviours live in ONE wrapper on purpose: a second layer would
    break the documented ``DATA_SOURCES[name].__wrapped__ is fetcher``
    invariant that tests and callers rely on.

    Idempotent: an already-wrapped fetcher is returned unchanged, so
    re-registering ``DATA_SOURCES.get(name)`` never double-wraps. The
    original fetcher stays reachable via ``__wrapped__`` (``functools``).
    """
    if getattr(fetcher, "_tl_ratio_aware", False):
        return fetcher
    from .index_aliases import resolve_symbol
    from .ratio_source import fetch_ratio, parse_ratio_symbol

    @functools.wraps(fetcher)
    def wrapped(ticker: str, interval: str, **kwargs: object) -> list[Candle] | None:
        sym = resolve_symbol(ticker, source_name) if source_name else ticker
        if parse_ratio_symbol(sym) is not None:
            return fetch_ratio(
                sym, interval,
                leg_fetcher=lambda t, i: fetcher(t, i, **kwargs),
            )
        return fetcher(sym, interval, **kwargs)

    wrapped._tl_ratio_aware = True  # type: ignore[attr-defined]
    return wrapped


def register_source(
    name: str, fetcher: DataFetcher, *, internal: bool = False,
    supports_range: bool = False, page_fetcher: DataFetcher | None = None,
) -> None:
    """Register a new data source under ``name``.

    Idempotent: repeat registrations overwrite. This is intentional so
    smoke tests can stub real sources by calling
    ``register_source("yfinance", fake)``.

    The fetcher is wrapped by :func:`_ratio_aware` so it transparently
    resolves ratio pseudo-symbols (``NUM/DEN``) leg-by-leg through this
    same source — ratios therefore work on EVERY source, not just
    yfinance. The same wrapper applies this source's index-symbol
    aliases (``VIX`` → ``^VIX`` on yfinance), which is why ``name`` is
    handed to it. ``DATA_SOURCES[name]`` is that wrapper; the raw
    fetcher is reachable via ``DATA_SOURCES[name].__wrapped__``.

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
    DATA_SOURCES[name] = _ratio_aware(fetcher, name)
    if internal:
        _INTERNAL_SOURCES.add(name)
    else:
        _INTERNAL_SOURCES.discard(name)
    if supports_range:
        _RANGE_CAPABLE.add(name)
    else:
        _RANGE_CAPABLE.discard(name)
    if page_fetcher is not None:
        _PAGE_FETCHERS[name] = page_fetcher
    else:
        _PAGE_FETCHERS.pop(name, None)


def unregister_source(name: str) -> bool:
    """Remove ``name`` from the registry **and every capability side-table**.

    Returns ``True`` if the source was present. Used by the credential-gated
    vendor registration path (:func:`tradinglab.data.register_vendor_sources`)
    when a user clears their keys: leaving a stale entry in
    :data:`DATA_SOURCES` would keep a dead option in the source dropdown that
    fails every fetch.

    Clearing :data:`_INTERNAL_SOURCES` / :data:`_RANGE_CAPABLE` /
    :data:`_PAGE_FETCHERS` alongside the main dict is the point of this
    helper — a bare ``DATA_SOURCES.pop(name)`` would leave
    :func:`source_supports_range` reporting ``True`` for a source that is no
    longer dispatchable.
    """
    existed = DATA_SOURCES.pop(name, None) is not None
    _INTERNAL_SOURCES.discard(name)
    _RANGE_CAPABLE.discard(name)
    _PAGE_FETCHERS.pop(name, None)
    return existed


def source_supports_range(name: str) -> bool:
    """True if ``name``'s fetcher accepts kw-only ``start`` / ``end`` datetimes."""
    return name in _RANGE_CAPABLE

def source_supports_page(name: str) -> bool:
    """True if ``name`` registered a ``(ticker, interval, *, end, limit)`` page
    fetcher (newest-``limit``-bars-before-``end``; see :func:`fetch_page`)."""
    return name in _PAGE_FETCHERS


@dataclass(frozen=True)
class FetchPageResult:
    """Outcome of a :func:`fetch_page` call.

    Rich (vs :func:`fetch_range`'s ``(candles, status)`` tuple) so the prefetch
    scheduler — which owns retry / poison / AIMD — gets the raw ``error`` and any
    provider ``Retry-After`` seconds. ``status`` ∈ ``"ok" | "empty" |
    "unsupported" | "error"``. ``bars`` is ``None`` on unsupported/error,
    ``[]`` on empty, the ascending candle list on ok.
    """

    bars: list[Candle] | None
    status: str
    error: BaseException | None = None
    retry_after_s: float | None = None


def _retry_after_from_error(error: BaseException | None) -> float | None:
    """Best-effort parse of a standard HTTP ``Retry-After`` header off an error
    (e.g. ``urllib.error.HTTPError``). Returns seconds or ``None``."""
    headers = getattr(error, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("Retry-After")
    except AttributeError:
        return None
    if raw is None:
        return None
    try:
        val = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return val if val >= 0 else None


def fetch_page(
    source: str, ticker: str, interval: str,
    *, end_ts: float | None = None, limit: int = 10_000,
) -> FetchPageResult:
    """Fetch the most recent ``limit`` bars strictly before ``end_ts``.

    ``end_ts`` is epoch seconds (``None`` → newest page). One request = one
    rate-limiter token — the prefetch scheduler consumes the token in
    ``next_dispatch`` before dispatch, so the page fetcher must NOT re-acquire it
    (single-owner rate/retry, principal-SWE review). The fetcher performs ONE
    HTTP attempt and raises on error; this wrapper translates the outcome into a
    :class:`FetchPageResult` (never raises) so the scheduler owns retry / poison.
    """
    fetcher = _PAGE_FETCHERS.get(source)
    if fetcher is None:
        return FetchPageResult(None, "unsupported")
    from datetime import datetime, timezone
    end = (
        None if end_ts is None
        else datetime.fromtimestamp(float(end_ts), timezone.utc)
    )
    try:
        bars = fetcher(ticker, interval, end=end, limit=int(limit))
    except Exception as exc:  # noqa: BLE001 — network/parse; scheduler owns retry
        return FetchPageResult(
            None, "error", error=exc, retry_after_s=_retry_after_from_error(exc),
        )
    if not bars:
        return FetchPageResult([], "empty")
    return FetchPageResult(list(bars), "ok")


def fetch_range(
    source: str, ticker: str, interval: str, start_ts: int, end_ts: int,
) -> tuple[list[Candle] | None, str]:
    """Targeted range fetch of ``[start_ts, end_ts)`` (epoch seconds).

    Returns ``(candles, status)`` where ``status`` is ``"ok"`` (bars returned),
    ``"empty"`` (fetch succeeded, no bars in range — halt/holiday/edge),
    ``"unsupported"`` (source can't range-fetch — caller uses the trailing
    window instead), or ``"error"`` (missing source / fetch raised). Never
    raises. Timestamps are passed to the fetcher as aware-UTC datetimes.
    """
    fetcher = DATA_SOURCES.get(source)
    if fetcher is None:
        return None, "error"
    if source not in _RANGE_CAPABLE:
        return None, "unsupported"
    from datetime import datetime, timezone
    start = datetime.fromtimestamp(int(start_ts), timezone.utc)
    end = datetime.fromtimestamp(int(end_ts), timezone.utc)
    try:
        bars = fetcher(ticker, interval, start=start, end=end)
    except TypeError:  # fetcher didn't actually accept start/end — be safe
        return None, "unsupported"
    except Exception:  # noqa: BLE001 — network/parse; treat all as a soft error
        return None, "error"
    if not bars:
        return [], "empty"
    return bars, "ok"


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
