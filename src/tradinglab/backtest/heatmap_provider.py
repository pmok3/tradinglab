"""Classification + historical-shares provider for the sandbox heatmap.

Supplies, per S&P 500 symbol:

* **sector / industry** — from the shipped GICS columns in
  ``tools/sp500.csv`` (offline; no Finviz scraping, no per-symbol
  ``.info`` calls). yfinance ``.info`` remains the fallback for
  non-S&P universes (v2).
* **Date added** + **CIK** — from the same CSV, for the point-in-time
  membership filter (``heatmap.members_asof``) and rename-safe
  resolution.
* **historical shares outstanding** — yfinance ``get_shares_full``, the
  only network-sourced field, disk-cached. Snapped to the replay clock
  with carry-back before the series start (see ``docs/SANDBOX_HEATMAP.md``).

The pure helpers (:func:`parse_date_added`, :func:`shares_at_from_series`,
:func:`load_sp500_meta`) are headless-testable; the network fetch is
injected (``shares_fetcher``) so tests run offline.
"""

from __future__ import annotations

import csv
import json
import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .heatmap import Classification

#: One symbol's historical shares series: ascending ``(epoch_seconds, shares)``.
SharesSeries = list[tuple[int, float]]
SharesFetcher = Callable[[str], SharesSeries]

_MS_THRESHOLD = 1e12


def _to_seconds(ts: float) -> float:
    t = float(ts)
    return t / 1000.0 if t >= _MS_THRESHOLD else t


# ---------------------------------------------------------------------------
# Pure helpers (offline, headless-testable)
# ---------------------------------------------------------------------------


def parse_date_added(value: str) -> int | None:
    """Parse an ``sp500.csv`` ``Date added`` cell to UTC epoch seconds.

    Accepts ``YYYY-MM-DD`` (the Wikipedia-derived format); trailing text
    after the date is ignored. Empty / unparseable -> ``None`` (treated
    as "unknown -> include" by ``heatmap.members_asof``).
    """
    v = (value or "").strip()
    if len(v) < 10:
        return None
    try:
        dt = datetime.strptime(v[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(dt.timestamp())


def load_sp500_meta(csv_path: Path | None = None) -> dict[str, dict]:
    """Parse ``tools/sp500.csv`` -> ``{symbol: {sector, industry, cik, date_added_ts}}``.

    Symbols are dot-munged (``BRK.B`` -> ``BRK-B``) to match yfinance /
    the rest of the app. Defaults to the shipped CSV via
    :func:`tradinglab._resources.resource_path`.
    """
    if csv_path is None:
        from .._resources import resource_path

        csv_path = resource_path("tools", "sp500.csv")
    out: dict[str, dict] = {}
    with open(csv_path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            sym = (row.get("Symbol") or "").strip().replace(".", "-")
            if not sym:
                continue
            out[sym] = {
                "sector": (row.get("GICS Sector") or "").strip(),
                "industry": (row.get("GICS Sub-Industry") or "").strip(),
                "cik": (row.get("CIK") or "").strip(),
                "date_added_ts": parse_date_added(row.get("Date added") or ""),
            }
    return out


def shares_at_from_series(
    series: Sequence[tuple[int, float]], ts: int
) -> tuple[float | None, bool]:
    """Snap a shares series to ``ts``; return ``(shares, approx)``.

    * Empty series -> ``(None, True)``.
    * ``ts`` before the series start -> **carry back** the earliest
      known count (nearest-in-time), flagged approximate.
    * Otherwise -> the most-recent count at or before ``ts`` (exact).

    ``series`` must be ascending by timestamp. ``ts`` is normalized
    (ms -> s) so either unit works.
    """
    if not series:
        return (None, True)
    cutoff = _to_seconds(ts)
    first_ts, first_val = series[0]
    if cutoff < _to_seconds(first_ts):
        return (float(first_val), True)
    val = float(first_val)
    for pts, pv in series:
        if _to_seconds(pts) <= cutoff:
            val = float(pv)
        else:
            break
    return (val, False)


def _yf_shares_fetcher(symbol: str) -> SharesSeries:
    """Default fetcher: yfinance ``get_shares_full`` -> ascending series.

    Best-effort: any failure (network, missing method, bad data) yields
    an empty series, so the caller degrades to carry-back / no-size.
    """
    try:
        import yfinance as yf

        s = yf.Ticker(symbol).get_shares_full(start="2000-01-01")
    except Exception:
        return []
    if s is None or len(s) == 0:
        return []
    out: SharesSeries = []
    try:
        for idx, val in s.items():
            if val is None:
                continue
            fv = float(val)
            if fv != fv or fv <= 0.0:  # NaN or nonpositive
                continue
            out.append((int(idx.timestamp()), fv))
    except Exception:
        return []
    out.sort(key=lambda t: t[0])
    return out


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


@dataclass
class HeatmapProvider:
    """Caches classification / membership / shares for the heatmap window.

    Classification + membership are loaded once from ``sp500.csv``
    (offline). Shares are fetched lazily per symbol via
    ``shares_fetcher`` and persisted to ``cache_dir/shares_cache.json``.
    """

    meta: dict[str, dict] | None = None
    shares_fetcher: SharesFetcher = _yf_shares_fetcher
    cache_dir: Path | None = None
    _shares: dict[str, SharesSeries] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.meta is None:
            self.meta = load_sp500_meta()
        if self.cache_dir is None:
            try:
                from ..paths import app_data_dir

                self.cache_dir = app_data_dir() / "heatmap"
            except Exception:
                self.cache_dir = None
        self._load_disk_cache()

    # -- classification / membership (offline) --

    def symbols(self) -> list[str]:
        return list(self.meta.keys())

    def classification(self) -> dict[str, Classification]:
        return {
            sym: Classification(m.get("sector") or "", m.get("industry") or "")
            for sym, m in self.meta.items()
        }

    def date_added(self) -> dict[str, int | None]:
        return {sym: m.get("date_added_ts") for sym, m in self.meta.items()}

    def cik(self, symbol: str) -> str:
        return (self.meta.get(symbol) or {}).get("cik") or ""

    # -- historical shares --

    def shares_series(self, symbol: str) -> SharesSeries:
        cached = self._shares.get(symbol)
        if cached is not None:
            return cached
        series = self.shares_fetcher(symbol) or []
        self._shares[symbol] = series
        self._save_disk_cache()
        return series

    def shares_at(self, symbol: str, ts: int) -> tuple[float | None, bool]:
        return shares_at_from_series(self.shares_series(symbol), ts)

    def peek_shares_at(self, symbol: str, ts: int) -> tuple[float | None, bool]:
        """Non-blocking ``shares_at``: cache-only, never fetches.

        Returns ``(None, True)`` when the symbol's series isn't cached
        yet — the caller renders an approximate sliver and can
        :meth:`prime` in the background.
        """
        series = self._shares.get(symbol)
        if series is None:
            return (None, True)
        return shares_at_from_series(series, ts)

    def prime(self, symbols: Iterable[str] | None = None) -> None:
        """Fetch + cache shares for ``symbols`` (default: all); single save.

        Safe to run on a background thread — it only fetches symbols not
        already cached and persists once at the end.
        """
        changed = False
        for sym in symbols if symbols is not None else self.symbols():
            if sym in self._shares:
                continue
            self._shares[sym] = self.shares_fetcher(sym) or []
            changed = True
        if changed:
            self._save_disk_cache()

    # -- disk cache (best-effort JSON) --

    def _cache_file(self) -> Path | None:
        return None if self.cache_dir is None else self.cache_dir / "shares_cache.json"

    def _load_disk_cache(self) -> None:
        f = self._cache_file()
        if f is None or not f.exists():
            return
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            for sym, series in raw.items():
                self._shares.setdefault(
                    sym, [(int(a), float(b)) for a, b in series]
                )
        except Exception:
            pass

    def _save_disk_cache(self) -> None:
        f = self._cache_file()
        if f is None:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._shares), encoding="utf-8")
            os.replace(tmp, f)
        except Exception:
            pass


__all__ = (
    "SharesSeries",
    "SharesFetcher",
    "parse_date_added",
    "load_sp500_meta",
    "shares_at_from_series",
    "HeatmapProvider",
)
