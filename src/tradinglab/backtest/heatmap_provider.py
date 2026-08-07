"""Classification + membership + shares/split provider for the heatmap.

Supplies, per S&P 500 symbol:

* **sector / industry** — from the shipped GICS columns in
  ``tools/sp500.csv`` (offline; no Finviz scraping, no per-symbol
  ``.info`` calls). yfinance ``.info`` remains the fallback for
  non-S&P universes (v2).
* **Date added** + **CIK** — from the same CSV, for the point-in-time
  membership filter (``heatmap.members_asof``), rename-safe resolution,
  and to resolve a symbol to its SEC filer without a network lookup.
* **historical shares outstanding** — from an **injected** provider
  (``shares_fetcher``). The concrete vendor is chosen by the
  ``shares_data_source`` tunable and resolved by a higher-level caller
  via :func:`data.shares_sources.resolve_shares_fetcher`; this module
  never imports one, so the source can be swapped without touching it.
* **split history** — from the price vendor, because a split is a
  price-series concern and the shares lift must match the basis the
  prices are on.

The pure helpers (:func:`parse_date_added`, :func:`shares_at_from_series`,
:func:`load_sp500_meta`) are headless-testable; every network fetch is
injected so tests run offline.
"""

from __future__ import annotations

import csv
import json
import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..core.timezones import normalize_epoch_to_seconds
from ..data.shares_sources import SharesFact, SharesFetcher, null_shares_fetcher
from .heatmap import Classification, split_factor_after

#: One symbol's shares history: ascending :class:`SharesFact` records.
SharesSeries = list[SharesFact]

#: One symbol's split history: ascending ``(epoch_seconds, ratio)``.
#: ``[]`` means "no splits, known"; ``None`` means the fetch failed and
#: the basis is unknown — the caller must degrade, not assume 1.0.
SplitsSeries = list[tuple[int, float]]
SplitsFetcher = Callable[[str], "SplitsSeries | None"]

# Epoch ms/s normalization lives in core.timezones (single definition).


def _to_seconds(ts: float) -> float:
    return normalize_epoch_to_seconds(ts)


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
    series: Sequence[SharesFact], ts: int
) -> tuple[float | None, bool]:
    """Snap a shares series to ``ts``; return ``(shares, approx)``.

    See :func:`shares_at_detail_from_series` for the full rule.
    """
    shares, approx, _observed = shares_at_detail_from_series(series, ts)
    return (shares, approx)


def shares_at_detail_from_series(
    series: Sequence[SharesFact], ts: int
) -> tuple[float | None, bool, int | None]:
    """Point-in-time snap; returns ``(shares, approx, as_of_ts)``.

    **Only facts already filed at ``ts`` are eligible.** A share count
    describes a date (``as_of_ts``) but does not become public until it
    is filed (``filed_ts``), typically two weeks later — so selecting on
    the as-of date alone sizes a replay tile from a number nobody had
    yet. Among the eligible facts the most recently *reported* one wins
    (greatest ``as_of_ts``), which is what a trader would have had in
    front of them.

    * Empty / nothing filed yet -> **carry back** the earliest known
      count (nearest-in-time), flagged approximate; ``(None, True, None)``
      when the series is empty.
    * The returned ``as_of_ts`` is the basis anchor for the split lift:
      the count is on *that* date's basis, not the clock's.

    ``ts`` is normalized (ms -> s) so either unit works.
    """
    if not series:
        return (None, True, None)
    cutoff = _to_seconds(ts)
    best: SharesFact | None = None
    for fact in series:
        if _to_seconds(fact.filed_ts) > cutoff:
            continue
        if best is None or fact.as_of_ts > best.as_of_ts:
            best = fact
    if best is None:
        # Nothing had been filed yet — the clock precedes this company's
        # first report. Carry back the earliest known count rather than
        # rendering nothing, and flag it.
        first = min(series, key=lambda f: f.as_of_ts)
        return (float(first.shares), True, int(first.as_of_ts))
    return (float(best.shares), False, int(best.as_of_ts))


def _yf_splits_fetcher(symbol: str) -> SplitsSeries | None:
    """Default fetcher: yfinance ``Ticker.splits`` -> ascending series.

    Returns ``[]`` for a symbol that genuinely never split and ``None``
    when the lookup failed. The distinction is load-bearing: ``[]``
    means "factor 1.0, known", whereas ``None`` must flag the tile
    approximate rather than silently assuming no split — assuming 1.0
    on a failure reinstates the exact under-sizing this correction
    exists to remove.
    """
    try:
        import yfinance as yf

        s = yf.Ticker(symbol).splits
    except Exception:
        return None
    if s is None:
        return None
    out: SplitsSeries = []
    try:
        for idx, val in s.items():
            fv = float(val)
            if fv != fv or fv <= 0.0:  # NaN or nonpositive
                continue
            out.append((int(idx.timestamp()), fv))
    except Exception:
        return None
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
    #: Historical shares provider. **Injected** — this module never picks
    #: a vendor. The higher-level caller resolves the
    #: ``shares_data_source`` tunable via
    #: ``data.shares_sources.resolve_shares_fetcher`` and passes the
    #: result. The default knows nothing (and touches no network), so a
    #: caller that forgets to inject gets "sizes unavailable" rather than
    #: silent traffic to a vendor nobody selected.
    shares_fetcher: SharesFetcher = null_shares_fetcher
    splits_fetcher: SplitsFetcher = _yf_splits_fetcher
    cache_dir: Path | None = None
    #: Whether the price series this provider's counts will be multiplied
    #: against is back-adjusted for splits. True for every vendor by
    #: default; Alpaca in ``raw`` / ``dividend`` adjustment mode is not,
    #: and lifting the shares against an unadjusted price would over-size
    #: splitters by exactly the ratio the correction removes — the mirror
    #: image of the bug. Set from ``data.quality.is_split_adjusted``.
    price_split_adjusted: bool = True
    _shares: dict[str, SharesSeries] = field(default_factory=dict, init=False, repr=False)
    _splits: dict[str, SplitsSeries | None] = field(
        default_factory=dict, init=False, repr=False
    )

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

    def cik_int(self, symbol: str) -> int | None:
        """CIK as an int, for a shares provider that resolves SEC filers.

        Shipped in ``tools/sp500.csv``, so the S&P universe never pays a
        network ticker lookup — and it is rename/recycled-ticker safe,
        which a bare symbol is not.
        """
        raw = self.cik(symbol)
        try:
            return int(raw) if raw else None
        except (TypeError, ValueError):
            return None

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
        """Fetch + cache shares **and splits** for ``symbols``; single save.

        Safe to run on a background thread — it only fetches symbols not
        already cached and persists once at the end.
        """
        changed = False
        for sym in symbols if symbols is not None else self.symbols():
            if sym not in self._shares:
                self._shares[sym] = self.shares_fetcher(sym) or []
                changed = True
            if sym not in self._splits:
                try:
                    self._splits[sym] = self.splits_fetcher(sym)
                except Exception:
                    self._splits[sym] = None
                changed = True
        if changed:
            self._save_disk_cache()

    # -- split basis --

    def splits_series(self, symbol: str) -> SplitsSeries | None:
        """Cached split history, fetching once. ``None`` = unknown."""
        if symbol in self._splits:
            return self._splits[symbol]
        try:
            series = self.splits_fetcher(symbol)
        except Exception:
            series = None
        self._splits[symbol] = series
        self._save_disk_cache()
        return series

    def peek_splits_series(self, symbol: str) -> SplitsSeries | None:
        """Non-blocking :meth:`splits_series`: cache-only, never fetches."""
        return self._splits.get(symbol)

    def basis_shares_at(self, symbol: str, ts: int) -> tuple[float | None, bool]:
        """Share count at ``ts`` expressed on the **price series'** basis.

        When the price series is back-adjusted to today (the default for
        every vendor), the as-reported count is lifted by the splits that
        happened after its own **observation date** — not after the
        replay clock, since filings are quarterly and a split can land in
        between. When the price series is *not* split-adjusted
        (``price_split_adjusted=False``, e.g. Alpaca in ``raw`` mode)
        both legs are already as-reported and no lift is applied.

        Returns ``(shares, approx)``; ``approx`` is True when the count
        was carried back *or* the split history is unknown, so an
        unverifiable basis is surfaced instead of silently assumed.
        """
        if not self.price_split_adjusted:
            return shares_at_from_series(self.shares_series(symbol), ts)
        return self._basis_shares(
            self.shares_series(symbol), self.splits_series(symbol), ts
        )

    def peek_basis_shares_at(self, symbol: str, ts: int) -> tuple[float | None, bool]:
        """Non-blocking :meth:`basis_shares_at`: cache-only, never fetches."""
        series = self._shares.get(symbol)
        if series is None:
            return (None, True)
        if not self.price_split_adjusted:
            return shares_at_from_series(series, ts)
        return self._basis_shares(series, self.peek_splits_series(symbol), ts)

    @staticmethod
    def _basis_shares(
        shares_series: SharesSeries,
        splits: SplitsSeries | None,
        ts: int,
    ) -> tuple[float | None, bool]:
        shares, approx, observed = shares_at_detail_from_series(shares_series, ts)
        if shares is None:
            return (None, True)
        if splits is None:
            # Unknown basis. Return the unlifted count so the tile still
            # renders, but flag it — a wrong-by-the-split-ratio tile that
            # looks authoritative is worse than one marked approximate.
            return (shares, True)
        factor = split_factor_after(splits, observed if observed is not None else ts)
        return (shares * factor, approx)

    # -- disk cache (best-effort JSON) --

    def _cache_file(self) -> Path | None:
        return None if self.cache_dir is None else self.cache_dir / "shares_cache.json"

    def _splits_cache_file(self) -> Path | None:
        return None if self.cache_dir is None else self.cache_dir / "splits_cache.json"

    def _load_disk_cache(self) -> None:
        f = self._cache_file()
        if f is not None and f.exists():
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
                for sym, series in raw.items():
                    if not series:
                        continue  # never trust a cached empty as "known"
                    self._shares.setdefault(
                        sym,
                        [
                            SharesFact(int(a), int(b), float(c))
                            for a, b, c in series
                        ],
                    )
            except Exception:
                # A cache written by an older build used 2-tuples with no
                # filed date. There is no honest way to back-fill one, so
                # the whole file is discarded and refetched rather than
                # guessed at — a wrong filed date is a look-ahead.
                self._shares.clear()
        sf = self._splits_cache_file()
        if sf is not None and sf.exists():
            try:
                raw = json.loads(sf.read_text(encoding="utf-8"))
                for sym, series in raw.items():
                    if sym in self._splits:
                        continue
                    # Defensive: a ``null`` from an older build must not
                    # be trusted as "known"; only real lists are cached.
                    if series is None:
                        continue
                    self._splits[sym] = [(int(a), float(b)) for a, b in series]
            except Exception:
                pass

    def _save_disk_cache(self) -> None:
        # Persist only NON-EMPTY shares histories. An empty result is
        # indistinguishable from "fetch failed" or "no provider
        # configured", and writing it would make that permanent: every
        # later launch reloads it, skips the fetch, and sizes the tile at
        # zero forever. Same reasoning as the splits cache below; both
        # cost one request per session to re-try instead.
        self._write_json(
            self._cache_file(),
            {k: v for k, v in self._shares.items() if v},
        )
        # Persist only KNOWN split histories. A transient fetch failure
        # caches as ``None`` for the session (one attempt per run, so a
        # rate-limit storm doesn't retry 500 times), but writing that
        # ``None`` to disk would make it permanent: every later launch
        # would reload it, skip the fetch, and silently render the
        # unlifted count — reinstating exactly the under-sizing this
        # correction removes, forever, behind a hatched border.
        self._write_json(
            self._splits_cache_file(),
            {k: v for k, v in self._splits.items() if v is not None},
        )

    def _write_json(self, path: Path | None, payload: dict) -> None:
        if path is None or self.cache_dir is None:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            pass


__all__ = (
    "SharesSeries",
    "SharesFetcher",
    "SplitsSeries",
    "SplitsFetcher",
    "parse_date_added",
    "load_sp500_meta",
    "shares_at_from_series",
    "shares_at_detail_from_series",
    "HeatmapProvider",
)
