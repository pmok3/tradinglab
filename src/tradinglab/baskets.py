"""Built-in basket / universe symbol lists.

Four concrete baskets are exposed today:

* ``sp500_symbols()`` — reads the S&P 500 constituent CSV that already
  ships in the repo at ``tools/sp500.csv``. The CSV is the
  Wikipedia-derived list maintained alongside the existing
  ``tools/universe_cache.py`` batch fetcher; this module promotes the
  loader so the GUI can consume the same source without reaching into
  the ``tools/`` tree.

* ``qqq_symbols()`` — hardcoded snapshot of the Nasdaq-100 (Invesco QQQ
  trust) constituents. Refreshed manually; the date is exposed as
  :data:`QQQ_LAST_REFRESHED` so callers can surface it in the UI and
  in failure summaries.

* ``nyse_symbols()`` — reads ``tools/nyse.csv``, a NASDAQ Trader-derived
  snapshot of NYSE-proper (Big Board) common stock, with non-common
  securities (preferreds, warrants, units, rights, ETFs, halted /
  deficient / bankrupt names) already filtered out at snapshot time.
  Date is :data:`NYSE_LAST_REFRESHED`. Refresh via
  ``python tools/refresh_exchange_lists.py``.

* ``nasdaq_symbols()`` — reads ``tools/nasdaq.csv``, the analogous
  NASDAQ-listed common-stock snapshot. Date is
  :data:`NASDAQ_LAST_REFRESHED`.

Neither resolver fetches from the network. Membership is deliberately
a static snapshot: the sandbox preload's per-symbol failure path is
what handles delisted / renamed tickers, and the survivorship-bias
caveat is documented in the prepare-universe dialog (with an amber
elevated banner for the full-exchange baskets specifically).

Future work (out of scope for the current sandbox preload feature):

* Date-aware historical membership for replays anchored on past dates.
* Movers / earnings / sector baskets fed by intraday data.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path

# Snapshot date for the QQQ list below. Surface this in the UI so a
# trader can tell at a glance whether the cached basket is stale.
QQQ_LAST_REFRESHED = "2026-05-04"

# Snapshot dates for the full-exchange CSVs in ``tools/``. Updated in
# place by ``tools/refresh_exchange_lists.py`` (regex replacement on
# these assignment statements — keep the format stable).
NYSE_LAST_REFRESHED = "2026-05-21"
NASDAQ_LAST_REFRESHED = "2026-05-21"


# Hardcoded Nasdaq-100 / QQQ constituent snapshot. Manually curated;
# refresh by editing this list and bumping ``QQQ_LAST_REFRESHED``.
# Order is alphabetical for diff-friendliness, not by index weight.
#
# Notes on edge cases:
#  * GOOG and GOOGL are both included (Alphabet has two share classes
#    in the index).
#  * Symbols that yfinance accepts directly; no dot-to-dash munging
#    needed (the SP500 loader does that for BRK.B etc, but no QQQ
#    member uses dots today).
_QQQ_2026_05_04: tuple = (
    "AAPL", "ADBE", "ADI",  "ADP",  "ADSK", "AEP",  "AMAT", "AMD",
    "AMGN", "AMZN", "ANSS", "APP",  "ARM",  "ASML", "AVGO", "AZN",
    "BIIB", "BKNG", "BKR",  "CCEP", "CDNS", "CDW",  "CEG",  "CHTR",
    "CMCSA","COST", "CPRT", "CRWD", "CSCO", "CSGP", "CSX",  "CTAS",
    "CTSH", "DASH", "DDOG", "DLTR", "DXCM", "EA",   "EXC",  "FANG",
    "FAST", "FTNT", "GEHC", "GFS",  "GILD", "GOOG", "GOOGL","HON",
    "IDXX", "INTC", "INTU", "ISRG", "KDP",  "KHC",  "KLAC", "LIN",
    "LRCX", "LULU", "MAR",  "MCHP", "MDB",  "MDLZ", "META", "MNST",
    "MRVL", "MSFT", "MSTR", "MU",   "NFLX", "NVDA", "NXPI", "ODFL",
    "ON",   "ORLY", "PANW", "PAYX", "PCAR", "PDD",  "PEP",  "PLTR",
    "PYPL", "QCOM", "REGN", "ROP",  "ROST", "SBUX", "SHOP", "SNDK",
    "SNPS", "STX",  "TEAM", "TMUS", "TRI",  "TSLA", "TTD",  "TTWO",
    "TXN",  "VRSK", "VRTX", "WBD",  "WDAY", "WDC",  "WMT",  "XEL",
    "ZS",
)


def qqq_symbols() -> list[str]:
    """Return the snapshot list of Nasdaq-100 constituents.

    Returns a fresh list each call so callers can mutate freely.
    Snapshot date is :data:`QQQ_LAST_REFRESHED`.
    """
    return list(_QQQ_2026_05_04)


def _sp500_csv_path() -> Path:
    """Locate ``tools/sp500.csv`` so the loader works in source and frozen modes.

    In a source / dev checkout the file lives at ``<repo>/tools/sp500.csv``.
    PyInstaller bundles the same file under ``_internal/tools/sp500.csv``
    relative to ``sys._MEIPASS`` — see ``TradingLab.spec``'s ``datas``
    list. :func:`tradinglab._resources.resource_path` handles both
    locations, so this resolver is a one-liner around it.

    If the file is missing (e.g. someone repackages ``src/`` without
    ``tools/``), :func:`sp500_symbols` raises a clear
    ``FileNotFoundError`` rather than failing silently.
    """
    from ._resources import resource_path
    return resource_path("tools", "sp500.csv")


def _exchange_csv_path(filename: str) -> Path:
    """Locate ``tools/<filename>`` for the NYSE / NASDAQ snapshot loaders.

    Mirrors :func:`_sp500_csv_path`'s frozen-mode behavior — uses
    :func:`tradinglab._resources.resource_path` so PyInstaller bundles
    pick up ``tools/nyse.csv`` and ``tools/nasdaq.csv`` from
    ``_internal/tools/``.
    """
    from ._resources import resource_path
    return resource_path("tools", filename)


def _load_symbols_csv(
    path: Path,
    *,
    label: str,
    munge_dots: bool = True,
) -> list[str]:
    """Read the ``Symbol`` column from a basket CSV.

    Shared by :func:`sp500_symbols`, :func:`nyse_symbols`, and
    :func:`nasdaq_symbols`. ``munge_dots`` controls the
    ``.``-to-``-`` translation: it is on by default so dual-class
    commons like ``BRK.B`` resolve to yfinance form ``BRK-B``. The
    NYSE refresher already pre-munges its CSV, so the call is a
    redundant-safe no-op there; SP500's CSV is not pre-munged so the
    on-load translation is the only place it happens.

    Raises:
        FileNotFoundError: if the CSV is not present at ``path``.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"{label} constituent list not found at {path}. "
            f"Run from a checkout that contains the tools/ directory."
        )
    syms: list[str] = []
    with path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            sym = (row.get("Symbol") or "").strip()
            if munge_dots:
                sym = sym.replace(".", "-")
            if sym:
                syms.append(sym)
    return syms


def sp500_symbols() -> list[str]:
    """Return the S&P 500 constituent symbols from ``tools/sp500.csv``.

    Mirrors the ticker-munging done by the existing
    ``tools/universe_cache.load_sp500_symbols`` (``.`` -> ``-`` so
    ``BRK.B`` becomes ``BRK-B`` for yfinance compatibility).

    Raises:
        FileNotFoundError: if ``tools/sp500.csv`` is not present
            under the repo root.
    """
    return _load_symbols_csv(_sp500_csv_path(), label="S&P 500")


def nyse_symbols() -> list[str]:
    """Return NYSE-proper (Big Board) common-stock symbols.

    Reads ``tools/nyse.csv`` — a normalized 4-column snapshot
    (``Symbol,Name,Exchange,SnapshotDate``) produced by
    ``tools/refresh_exchange_lists.py`` from NASDAQ Trader's
    ``otherlisted.txt`` feed. Common-stock filtering (drop
    preferreds, warrants, units, rights, ETFs, halted /
    deficient / bankrupt names) is applied at refresh time, so
    this loader simply reads the ``Symbol`` column.

    Snapshot date is :data:`NYSE_LAST_REFRESHED`. Re-run the refresh
    CLI quarterly (or when needed) to update the snapshot.

    Raises:
        FileNotFoundError: if ``tools/nyse.csv`` is missing.
    """
    return _load_symbols_csv(_exchange_csv_path("nyse.csv"), label="NYSE")


def nasdaq_symbols() -> list[str]:
    """Return NASDAQ-listed common-stock symbols.

    Same shape as :func:`nyse_symbols`; reads ``tools/nasdaq.csv``
    populated from NASDAQ Trader's ``nasdaqlisted.txt`` feed. Snapshot
    date is :data:`NASDAQ_LAST_REFRESHED`.

    Raises:
        FileNotFoundError: if ``tools/nasdaq.csv`` is missing.
    """
    return _load_symbols_csv(
        _exchange_csv_path("nasdaq.csv"), label="NASDAQ", munge_dots=False)


# Keyed registry of built-in baskets. The GUI uses this to populate
# the "Universe" radio in the prepare-universe dialog. Each value is
# a zero-arg callable returning a fresh ``List[str]``.
BUILTIN_BASKETS: dict[str, Callable[[], list[str]]] = {
    "sp500": sp500_symbols,
    "qqq": qqq_symbols,
    "nyse": nyse_symbols,
    "nasdaq": nasdaq_symbols,
}


# Display labels for the GUI. Kept separate from ``BUILTIN_BASKETS``
# so renaming a label doesn't churn the manifest ID space.
BUILTIN_BASKET_LABELS: dict[str, str] = {
    "sp500": "S&P 500",
    "qqq": "Nasdaq-100 (QQQ)",
    "nyse": "NYSE — all common stocks",
    "nasdaq": "NASDAQ — all common stocks",
}


# Per-basket snapshot dates so the dialog can render a "refreshed
# YYYY-MM-DD" suffix per radio without each call-site reaching into
# the module-level constants. SP500 ships from a Wikipedia-derived
# CSV without a baked-in date, so it's intentionally absent here —
# the dialog skips the suffix when a key is missing.
BUILTIN_BASKET_REFRESHED_DATES: dict[str, str] = {
    "qqq": QQQ_LAST_REFRESHED,
    "nyse": NYSE_LAST_REFRESHED,
    "nasdaq": NASDAQ_LAST_REFRESHED,
}


# Full-exchange baskets need extra UI treatment (amber survivorship
# banner, etc.). The dialog reads this set rather than hardcoding
# the two keys, so future full-exchange baskets get the treatment
# automatically.
FULL_EXCHANGE_BASKETS: frozenset = frozenset({"nyse", "nasdaq"})
