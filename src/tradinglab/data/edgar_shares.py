"""SEC EDGAR XBRL shares-outstanding provider.

Fetches historical common-shares-outstanding from the SEC's free XBRL
API (``data.sec.gov``), the authoritative source: it *is* what companies
report. Chosen over a price vendor's fundamentals feed for four reasons
measured against yfinance ``get_shares_full``:

* **One basis per fact.** The vendor series interleaves as-reported and
  already-split-adjusted values — sometimes several for the same date,
  sometimes double-adjusted (TSLA carried 0.186B, 0.932B *and* 4.659B on
  2020-08-31). EDGAR returns one clean as-reported value per filing.
* **Point-in-time.** Every fact carries ``filed`` — when the number
  became public — as well as ``end``, the date it describes. Sizing a
  replay tile from a count that had not been filed yet is look-ahead;
  only EDGAR gives us the means to avoid it.
* **Bounded staleness.** ~91-day median cadence, 98-day worst gap,
  versus 675 days from the vendor.
* **Coverage.** Every US filer (~10k), not just what one vendor happens
  to carry.

Depth is bounded by the XBRL mandate (~2009). Splits are *not* available
here — they stay with the price vendor, because a split is a price-series
concern and the lift has to match the basis the prices are on.

Network access is injected (``url_fetcher``) so every rule is testable
offline. See ``data/edgar_shares.spec.md``.
"""

from __future__ import annotations

import datetime as _dt
import json
import urllib.request
from collections.abc import Callable, Iterable
from typing import Any

from .shares_sources import SharesFact

#: SEC requires every automated client to identify itself with a contact
#: address and to stay under 10 requests/second; a browser-style
#: User-Agent is rejected with 403 by their WAF — as is, empirically, any
#: agent string containing ``github.com``, which rules out a repo or
#: noreply address. This default is overridable with the
#: ``sec_user_agent`` tunable so a user can attribute requests to
#: themselves. It is a public request header: never put a credential in
#: it.
USER_AGENT = "TradingLab/1.0 pmok3@gatech.edu"


def user_agent() -> str:
    """Active SEC User-Agent: the ``sec_user_agent`` tunable, else the default."""
    try:
        from .. import defaults as _defaults

        override = str(_defaults.get("sec_user_agent") or "").strip()
    except Exception:  # noqa: BLE001 - settings must never break a fetch
        override = ""
    return override or USER_AGENT

_CONCEPT_URL = (
    "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/dei/"
    "EntityCommonStockSharesOutstanding.json"
)
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

#: Injected network hook: ``(url) -> decoded JSON``. Raises on failure.
UrlFetcher = Callable[[str], Any]


def _default_url_fetcher(url: str) -> Any:  # pragma: no cover - network path
    req = urllib.request.Request(url, headers={"User-Agent": user_agent()})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _to_epoch(day: str) -> int | None:
    """``YYYY-MM-DD`` -> UTC epoch seconds; ``None`` when unparseable."""
    try:
        return int(
            _dt.datetime.strptime(day[:10], "%Y-%m-%d")
            .replace(tzinfo=_dt.timezone.utc)
            .timestamp()
        )
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Pure parsing (offline-testable)
# ---------------------------------------------------------------------------


def parse_company_concept(payload: Any) -> list[SharesFact]:
    """Parse an EDGAR ``companyconcept`` payload into ascending facts.

    Keeps one fact per ``end`` date: the one with the **latest** ``filed``,
    so a 10-K/A amendment supersedes the original rather than appearing
    beside it. Sorted by ``as_of_ts``. Malformed rows are skipped; a
    malformed payload yields ``[]`` rather than raising — a fundamentals
    outage must never take the chart down.
    """
    if not isinstance(payload, dict):
        return []
    units = payload.get("units")
    if not isinstance(units, dict):
        return []
    best: dict[int, SharesFact] = {}
    for rows in units.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            as_of = _to_epoch(str(row.get("end") or ""))
            filed = _to_epoch(str(row.get("filed") or ""))
            try:
                shares = float(row.get("val"))
            except (TypeError, ValueError):
                continue
            if as_of is None or filed is None:
                continue
            if shares != shares or shares <= 0.0:  # NaN or nonpositive
                continue
            prev = best.get(as_of)
            if prev is None or filed > prev.filed_ts:
                best[as_of] = SharesFact(as_of, filed, shares)
    return [best[k] for k in sorted(best)]


def parse_ticker_map(payload: Any) -> dict[str, int]:
    """Parse ``company_tickers.json`` into ``{TICKER: cik}``.

    Symbols are dot-munged (``BRK.B`` -> ``BRK-B``) to match the rest of
    the app.
    """
    out: dict[str, int] = {}
    if not isinstance(payload, dict):
        return out
    for entry in payload.values():
        if not isinstance(entry, dict):
            continue
        ticker = str(entry.get("ticker") or "").strip().upper().replace(".", "-")
        try:
            cik = int(entry.get("cik_str"))
        except (TypeError, ValueError):
            continue
        if ticker:
            out[ticker] = cik
    return out


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------


class EdgarSharesFetcher:
    """Callable ``(symbol) -> list[SharesFact]`` backed by SEC EDGAR.

    Resolves a symbol to its CIK via an injected ``cik_lookup`` (the
    heatmap passes the CIK already shipped in ``tools/sp500.csv``),
    falling back to the SEC's own ticker map — fetched once and cached
    in-process — for universes the CSV doesn't cover.

    Returns ``[]`` on any failure so callers degrade to "unknown" rather
    than crashing; the empty result is indistinguishable from "no filings",
    which is the honest reading for a non-filer.
    """

    def __init__(
        self,
        *,
        url_fetcher: UrlFetcher | None = None,
        cik_lookup: Callable[[str], int | None] | None = None,
    ) -> None:
        self._fetch = url_fetcher or _default_url_fetcher
        self._cik_lookup = cik_lookup
        self._ticker_map: dict[str, int] | None = None

    def cik_for(self, symbol: str) -> int | None:
        sym = (symbol or "").strip().upper().replace(".", "-")
        if not sym:
            return None
        if self._cik_lookup is not None:
            try:
                cik = self._cik_lookup(sym)
            except Exception:
                cik = None
            if cik:
                return int(cik)
        if self._ticker_map is None:
            try:
                self._ticker_map = parse_ticker_map(self._fetch(_TICKERS_URL))
            except Exception:
                self._ticker_map = {}
        return self._ticker_map.get(sym)

    def __call__(self, symbol: str) -> list[SharesFact]:
        cik = self.cik_for(symbol)
        if not cik:
            return []
        try:
            payload = self._fetch(_CONCEPT_URL.format(cik=int(cik)))
        except Exception:
            return []
        return parse_company_concept(payload)


def make_fetcher(
    *,
    url_fetcher: UrlFetcher | None = None,
    cik_lookup: Callable[[str], int | None] | None = None,
) -> EdgarSharesFetcher:
    """Build an :class:`EdgarSharesFetcher` (registry entry point)."""
    return EdgarSharesFetcher(url_fetcher=url_fetcher, cik_lookup=cik_lookup)


def prefetch_quarter(
    period: str,
    *,
    url_fetcher: UrlFetcher | None = None,
    symbols: Iterable[str] | None = None,
) -> dict[int, float]:
    """Bulk-load one quarter for **every** filer in a single request.

    ``period`` is an EDGAR frame id such as ``"CY2020Q2I"``. Returns
    ``{cik: shares}``. One request covers ~4,800 companies, so a wide
    universe costs a handful of calls instead of one per symbol. Best
    effort: any failure yields ``{}`` and the caller falls back to
    per-symbol lookups. ``symbols`` is accepted for signature
    compatibility with future filtered variants and is ignored.
    """
    fetch = url_fetcher or _default_url_fetcher
    url = (
        "https://data.sec.gov/api/xbrl/frames/dei/"
        f"EntityCommonStockSharesOutstanding/shares/{period}.json"
    )
    try:
        payload = fetch(url)
    except Exception:
        return {}
    out: dict[int, float] = {}
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            cik = int(row.get("cik"))
            val = float(row.get("val"))
        except (TypeError, ValueError):
            continue
        if val == val and val > 0.0:
            out[cik] = val
    return out


__all__ = (
    "USER_AGENT",
    "user_agent",
    "UrlFetcher",
    "EdgarSharesFetcher",
    "make_fetcher",
    "parse_company_concept",
    "parse_ticker_map",
    "prefetch_quarter",
)
