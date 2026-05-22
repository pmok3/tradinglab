"""Universe-preload manifest: durable JSON sidecar + coverage queries.

A *manifest* records the result of a ``Prepare Universe Data`` run:
which symbols were resolved, which intervals were fetched, when, and
which underlying provider (``yfinance`` for now) the cache keys are
written under. The manifest is the single source of truth a sandbox
session reads at session-start to know which tickers are inside the
universe (strict-offline gating).

Storage layout::

    <_cache_dir()>/universes/
        sp500.json
        qqq.json
        watchlist__Mega Caps.json
        ...

Manifest IDs are stable strings:

* built-in baskets use the basket key (``sp500``, ``qqq``).
* custom watchlists use ``watchlist:<name>`` and the on-disk filename
  replaces the ``:`` and any path-unsafe character with ``__``.

Coverage queries (``coverage_for_date``) hit the existing
:mod:`tradinglab.disk_cache` to count, for a given session date,
how many manifest symbols actually have intraday bars on that date.
The sandbox start dialog renders this so a trader can see *"487/503
symbols cover 2024-03-15"* and decide whether to proceed.
"""

from __future__ import annotations

import datetime as _dt
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import disk_cache
from ..core.io_helpers import atomic_write_json

# ---------------------------------------------------------------------------
# Filesystem layout
# ---------------------------------------------------------------------------

_UNIVERSES_DIR_NAME = "universes"


def _universes_dir() -> Path:
    d = disk_cache._cache_dir() / _UNIVERSES_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_filename(uid: str) -> str:
    """Translate a manifest ID into a filesystem-safe filename stem.

    ``watchlist:Mega Caps`` -> ``watchlist__Mega Caps``. Spaces are
    kept (Windows tolerates them); only the genuinely path-hostile
    characters are munged.
    """
    bad = '<>:"/\\|?*'
    out = uid
    for ch in bad:
        out = out.replace(ch, "__")
    return out


def _path_for(uid: str) -> Path:
    return _universes_dir() / f"{_safe_filename(uid)}.json"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolEntry:
    """Per-symbol manifest record: which intervals were preloaded.

    ``intervals`` is the set of cache-key intervals successfully
    written (not necessarily all the ones the user asked for; partial
    success is normal — yfinance occasionally 429s on individual
    tickers). ``last_fetched`` is the wall-clock seconds-since-epoch
    at the moment the *latest* of those interval fetches completed.
    """
    symbol: str
    intervals: Tuple[str, ...]
    last_fetched: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": str(self.symbol),
            "intervals": list(self.intervals),
            "last_fetched": float(self.last_fetched),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SymbolEntry":
        return cls(
            symbol=str(d["symbol"]),
            intervals=tuple(d.get("intervals") or ()),
            last_fetched=float(d.get("last_fetched") or 0.0),
        )


@dataclass(frozen=True)
class UniverseManifest:
    """A prepared universe — symbols + intervals + provenance.

    ``id`` is the stable key used in :class:`SessionSpec.universe_id`
    and in the on-disk filename; pick it once, never rename it.
    ``kind`` distinguishes builtin baskets (``"basket"``) from custom
    watchlists (``"watchlist"``); used only for UI grouping.
    ``source`` is the data-source name (``"yfinance"``); the cache
    keys this manifest's symbols are persisted under all share this
    source — so :func:`coverage_for_date` can reconstruct the right
    ``(source, sym, interval)`` tuple without the caller passing it.
    ``intervals`` is the *intended* preload set (e.g. ``("5m","1d")``);
    actual per-symbol coverage may be a subset.
    """
    id: str
    name: str
    kind: str
    source: str
    intervals: Tuple[str, ...]
    symbols: Tuple[SymbolEntry, ...]
    prepared_at: float

    def symbol_set(self) -> "frozenset[str]":
        """Frozenset of symbol strings for O(1) membership testing."""
        return frozenset(e.symbol for e in self.symbols)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "name": str(self.name),
            "kind": str(self.kind),
            "source": str(self.source),
            "intervals": list(self.intervals),
            "symbols": [e.to_dict() for e in self.symbols],
            "prepared_at": float(self.prepared_at),
            "schema_version": 1,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "UniverseManifest":
        return cls(
            id=str(d["id"]),
            name=str(d.get("name") or d["id"]),
            kind=str(d.get("kind") or "basket"),
            source=str(d.get("source") or "yfinance"),
            intervals=tuple(d.get("intervals") or ()),
            symbols=tuple(
                SymbolEntry.from_dict(s) for s in (d.get("symbols") or ())),
            prepared_at=float(d.get("prepared_at") or 0.0),
        )


# ---------------------------------------------------------------------------
# Coverage report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageReport:
    """How many manifest symbols have bars on a given session date.

    ``covered`` ⊆ ``manifest.symbol_set()``: symbols whose disk_cache
    contains at least one bar whose calendar date matches the target.
    ``missing`` is the complement.
    """
    target_date: _dt.date
    interval: str
    covered: Tuple[str, ...]
    missing: Tuple[str, ...]

    @property
    def covered_count(self) -> int:
        return len(self.covered)

    @property
    def total_count(self) -> int:
        return len(self.covered) + len(self.missing)


def coverage_for_date(
    manifest: UniverseManifest,
    target_date: _dt.date,
    interval: str,
) -> CoverageReport:
    """For each symbol in ``manifest``, check disk_cache at
    ``(manifest.source, sym, interval)`` for any bar landing on
    ``target_date``. Returns a :class:`CoverageReport`.

    Reads only — never fetches. Safe to call from the Tk thread for a
    100-ish-symbol manifest; for the 503-symbol SP500 the call takes
    a few hundred ms on cold disk cache.

    **Performance warning — full-exchange manifests.** At NYSE / NASDAQ
    scale (~2,000+ symbols) this performs O(N) pickle loads, each of
    which deserializes potentially thousands of `Candle` objects.
    Measured cost on a warm cache is 5–15 s; cold or virus-scanned
    Windows paths can push 30–60 s. Callers running against
    full-exchange manifests MUST invoke this off the Tk thread (via a
    worker thread + ``after()`` poller) to avoid freezing the UI.
    Lightweight callers can probe ``len(manifest.symbols) > 500`` as
    the threshold.
    """
    covered: List[str] = []
    missing: List[str] = []
    for entry in manifest.symbols:
        candles = disk_cache.load(
            manifest.source, entry.symbol, interval) or []
        hit = False
        for c in candles:
            try:
                d = c.date.date() if hasattr(c.date, "date") else c.date
            except Exception:  # noqa: BLE001
                continue
            if d == target_date:
                hit = True
                break
        if hit:
            covered.append(entry.symbol)
        else:
            missing.append(entry.symbol)
    return CoverageReport(
        target_date=target_date,
        interval=interval,
        covered=tuple(covered),
        missing=tuple(missing),
    )


# ---------------------------------------------------------------------------
# Persistence (atomic JSON write, lenient read)
# ---------------------------------------------------------------------------


def save(manifest: UniverseManifest) -> Path:
    """Atomic-write the manifest to its sidecar JSON.

    Mirrors :func:`disk_cache.save`'s ``temp + os.replace`` discipline
    so a crash mid-write can't leave a half-written manifest behind.
    """
    path = _path_for(manifest.id)
    atomic_write_json(path, manifest.to_dict(), indent=2, sort_keys=False)
    return path


def load(uid: str) -> Optional[UniverseManifest]:
    """Load one manifest by ID; returns None on missing / corrupt."""
    path = _path_for(uid)
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return UniverseManifest.from_dict(d)
    except Exception:  # noqa: BLE001
        return None


def load_all() -> List[UniverseManifest]:
    """Enumerate every manifest, freshest-first by ``prepared_at``.

    Corrupt files are skipped silently (matching the cache layer's
    "corrupt cache is non-fatal" contract).
    """
    out: List[UniverseManifest] = []
    d = _universes_dir()
    for p in sorted(d.glob("*.json")):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            out.append(UniverseManifest.from_dict(payload))
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda m: m.prepared_at, reverse=True)
    return out


def delete(uid: str) -> bool:
    """Remove a manifest sidecar. Returns True if a file was removed.

    Does NOT touch the disk_cache pickles — only the manifest sidecar
    is dropped. Symbol bar data persists across manifest deletions.
    """
    path = _path_for(uid)
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Convenience builder
# ---------------------------------------------------------------------------


def build_from_loaded(
    *,
    uid: str,
    name: str,
    kind: str,
    source: str,
    intervals: Tuple[str, ...],
    per_symbol: Dict[str, Tuple[str, ...]],
    previous: Optional[UniverseManifest] = None,
) -> UniverseManifest:
    """Construct a manifest from preload-service output.

    ``per_symbol`` maps each successfully-loaded symbol to the tuple
    of intervals that actually persisted (empty tuple = exclude).
    Symbols with empty interval tuples are dropped from the manifest
    entirely — strict-offline gating must not let them through.

    If ``previous`` is given (the manifest currently on disk for the
    same ``uid``), per-symbol interval sets are **unioned** with the
    prior run's. This protects against the "re-run with smaller
    interval set silently drops bars" failure mode: a user who first
    preloaded ``("5m", "1d")`` and then re-runs with only ``("1d",)``
    keeps the 5m coverage in their manifest, since the underlying
    pickles still exist on disk (the disk-cache short-circuit means
    the new run never touched them, so they remain valid).

    Symbols present only in ``previous`` are carried forward
    unconditionally; this matches how the disk-cache short-circuit
    treats prior runs as authoritative for already-fetched data.
    A subsequent run that legitimately wants a smaller universe can
    use ``manifest.delete(uid)`` first.

    ``intervals`` (the top-level field) is the intended union of all
    runs that contributed to this manifest, so the gating layer can
    see the full set without scanning per-symbol entries.
    """
    now = time.time()

    merged: Dict[str, set] = {}
    if previous is not None:
        for entry in previous.symbols:
            merged[entry.symbol] = set(entry.intervals)
    for sym, itvs in per_symbol.items():
        if not itvs:
            continue
        merged.setdefault(sym, set()).update(itvs)

    entries = tuple(
        SymbolEntry(
            symbol=sym,
            intervals=tuple(sorted(merged[sym])),
            last_fetched=now,
        )
        for sym in sorted(merged)
        if merged[sym]
    )

    if previous is not None:
        union_intervals = tuple(sorted(set(previous.intervals) | set(intervals)))
    else:
        union_intervals = tuple(intervals)

    return UniverseManifest(
        id=uid,
        name=name,
        kind=kind,
        source=source,
        intervals=union_intervals,
        symbols=entries,
        prepared_at=now,
    )
