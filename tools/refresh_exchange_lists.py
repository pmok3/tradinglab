"""Refresh the NYSE and NASDAQ snapshot CSVs from nasdaqtrader.com.

Fetches the canonical exchange-listing files from NASDAQ Trader
(``nasdaqlisted.txt`` and ``otherlisted.txt``), applies a strict
"common stock only" filter, and writes normalized 4-column CSVs:

* ``tools/nasdaq.csv`` — NASDAQ-listed common stock.
* ``tools/nyse.csv``   — NYSE-proper (Big Board) common stock.

Both CSVs share the schema::

    Symbol,Name,Exchange,SnapshotDate
    AAPL,Apple Inc.,NASDAQ,2026-05-21
    ...

The schema is deliberately decoupled from NASDAQ Trader's pipe-delimited
column set so ``baskets.py`` doesn't depend on a vendor format that has
drifted historically (a column was added in 2018, the header line was
added in 2021). Keeping a normalized 4-column CSV means future basket
loaders (Russell 3000, sector ETFs, ...) can reuse the same shape.

Filter rules (applied at refresh time, baked into the shipped CSV):

* ``Test Issue == "N"`` — drop NASDAQ test tickers (``ZAZZT``, ``ZBZZT``).
* ``Financial Status == "N"`` (NASDAQ only) — drop deficient (``D``),
  delinquent (``E``), bankrupt (``Q``), grace-period (``G``), halted
  (``H``).
* ``ETF == "N"`` — drop ETFs/ETNs; this universe is common-stock only.
* Drop symbols whose 5th character is a NASDAQ class-encoding for
  non-common (``R`` = rights, ``U`` = units, ``W`` = warrants).
* Drop symbols containing ``$`` (preferred-stock series).
* Drop NYSE symbols with ``.`` suffixes that classify as non-common per
  ``Security Name`` (Preferred, Warrant, Right, Unit, When Issued).
  Class A/B (``BRK.A``, ``BRK.B``) ARE kept and dot-to-dash munged to
  yfinance form (``BRK-A``, ``BRK-B``).
* NYSE basket = ``Exchange == "N"`` only (Big Board). NYSE American
  (``A``), NYSE Arca (``P``, mostly ETFs), and Cboe BZX (``Z``) are
  excluded — they are noisier and structurally different universes.

Usage::

    python tools/refresh_exchange_lists.py
    python tools/refresh_exchange_lists.py --dry-run
    python tools/refresh_exchange_lists.py --out tools

On success, also updates ``NYSE_LAST_REFRESHED`` and
``NASDAQ_LAST_REFRESHED`` in ``src/tradinglab/baskets.py`` (in-place
ISO-date replacement).

Refuses to overwrite an existing CSV with fewer than 1,000 symbols —
a defensive guard against an empty / malformed upstream response.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import re
import ssl
import sys
import urllib.request
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

# nasdaqtrader.com serves these as plain-text pipe-delimited files
# (HTTPS).  The FTP endpoint is the canonical mirror but firewalls /
# corporate proxies often block FTP, so we use HTTPS by default.
_NASDAQ_URL = (
    "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
)
_OTHER_URL = (
    "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
)

# Defensive minimum: refuse to overwrite either CSV with fewer than
# this many rows.  Real NYSE / NASDAQ common-stock counts are ~2,200
# and ~3,000 respectively; anything below 1,000 indicates the upstream
# response is malformed (e.g. a 503 page served as text).
_MIN_ROWS = 1000

#: Cap on the bytes read from each pipe-delimited feed. The real files
#: are < 1 MB; the cap exists so a hostile / misconfigured upstream
#: cannot stream gigabytes into RAM during a manual refresh.
_MAX_FEED_BYTES = 16 * 1024 * 1024

#: Characters whose presence at the start of a CSV cell turns the
#: cell into a formula on import into Excel / Google Sheets / Numbers.
#: A symbol or company name beginning with one of these would, on
#: opening the CSV in a spreadsheet, get evaluated — potentially
#: hitting the DDE channel and exfiltrating cell references. The
#: defence is to prefix the cell with a single quote, which every
#: major spreadsheet treats as "render as literal text". The single
#: quote is invisible in the cell display but is preserved by
#: ``csv.reader`` so a downstream Python consumer also sees it.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe_csv_cell(value: str) -> str:
    """Return ``value`` defanged against CSV-formula-injection.

    If the first character is one of :data:`_CSV_FORMULA_PREFIXES`,
    prefix a single quote so the spreadsheet engine renders the
    cell as literal text. Non-string inputs are coerced via
    :func:`str` first. Empty / whitespace-only strings are returned
    unchanged (no leading char to flag).
    """
    if not isinstance(value, str):
        value = str(value)
    if not value:
        return value
    if value[0] in _CSV_FORMULA_PREFIXES:
        return "'" + value
    return value


# Output CSV schema — keep stable; baskets.py reads ``Symbol`` only,
# but the extra columns are useful for future tooling and for the
# refresh diff summary.
_OUTPUT_HEADER = ("Symbol", "Name", "Exchange", "SnapshotDate")


def _http_get(url: str, *, timeout: float = 30.0) -> str:
    """Fetch a URL as UTF-8 text with explicit cert validation.

    Uses ``certifi``'s bundled CA store because Windows' default trust
    store is sometimes incomplete in PyInstaller / venv environments.
    The response body is capped at :data:`_MAX_FEED_BYTES` (16 MB) to
    bound memory use against a runaway or hostile upstream.
    """
    try:
        import certifi  # type: ignore[import-untyped]
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url, headers={"User-Agent": "tradinglab-refresh/1.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read(_MAX_FEED_BYTES).decode("utf-8")


# ---------------------------------------------------------------------------
# Parsing + filtering
# ---------------------------------------------------------------------------


def _parse_pipe_table(text: str) -> Tuple[List[str], List[List[str]]]:
    """Split a NASDAQ Trader pipe-delimited table into (header, rows).

    Drops the trailing ``File Creation Time: ...`` footer line that
    nasdaqtrader.com appends. Blank lines and lines that don't contain
    a pipe are also dropped (defensive).
    """
    lines = [ln for ln in text.splitlines() if ln and "|" in ln]
    if not lines:
        return [], []
    if lines and lines[-1].startswith("File Creation Time"):
        lines = lines[:-1]
    header = lines[0].split("|")
    rows = [ln.split("|") for ln in lines[1:]]
    return header, rows


def _row_to_dict(header: Sequence[str], row: Sequence[str]) -> dict:
    """Zip a row against the header, tolerating short / over-long rows."""
    return {h: (row[i] if i < len(row) else "") for i, h in enumerate(header)}


_NONCOMMON_SUFFIX_5TH = frozenset("RUW")
"""NASDAQ 5th-character codes for non-common securities.

Per `NASDAQ Trader symbology
<https://www.nasdaqtrader.com/Trader.aspx?id=CQSsymbolconvention>`_::

    R = rights
    U = units
    W = warrants

Class-share codes (``A``, ``B``, ``C``, ``L``, etc.) are common stock
and are kept. ``Y``/``F`` (ADR/foreign) are kept — they trade on the
same venues as common.
"""

_NONCOMMON_NAME_PATTERNS = (
    re.compile(r"\bPreferred\b", re.IGNORECASE),
    re.compile(r"\bWarrant", re.IGNORECASE),
    re.compile(r"\bRights?\b", re.IGNORECASE),
    re.compile(r"\bUnits?\b", re.IGNORECASE),
    re.compile(r"\bWhen[- ]Issued\b", re.IGNORECASE),
    re.compile(r"\bDepositary Shares?\b", re.IGNORECASE),
    re.compile(r"\bSubordinated\b", re.IGNORECASE),
    re.compile(r"\bConvertible\b", re.IGNORECASE),
    re.compile(r"\bNotes?\b", re.IGNORECASE),
)


def _is_common_stock_by_name(name: str) -> bool:
    """Reject NYSE rows whose security name flags them as non-common.

    NASDAQ's symbology encodes type positionally; NYSE's
    ``otherlisted.txt`` does not — the ``Security Name`` text is the
    authoritative field. Patterns chosen to catch the bulk of preferreds
    / warrants / units / depositary shares / convertible notes without
    accidentally dropping common stock whose name happens to contain
    one of the keywords.
    """
    for pat in _NONCOMMON_NAME_PATTERNS:
        if pat.search(name):
            return False
    return True


def _is_common_symbol_nasdaq(symbol: str) -> bool:
    """NASDAQ symbol-suffix gate: drop 5th-char R/U/W and ``$`` series.

    Class-share codes (e.g. ``GOOGL`` 5th char ``L``) are kept.
    """
    if "$" in symbol:
        return False
    if len(symbol) >= 5 and symbol[4] in _NONCOMMON_SUFFIX_5TH:
        return False
    return True


def filter_nasdaq(header: Sequence[str], rows: Iterable[Sequence[str]]) -> List[Tuple[str, str]]:
    """Return ``[(symbol, name)]`` of NASDAQ-listed common stock.

    Drops test issues, non-Normal financial-status rows, ETFs,
    preferreds, warrants, units, rights. Symbols are emitted in
    upper-case yfinance form (no munging needed for NASDAQ).
    """
    out: List[Tuple[str, str]] = []
    seen: set = set()
    for row in rows:
        d = _row_to_dict(header, row)
        sym = (d.get("Symbol") or "").strip().upper()
        if not sym:
            continue
        if (d.get("Test Issue") or "").strip().upper() != "N":
            continue
        if (d.get("Financial Status") or "N").strip().upper() != "N":
            continue
        if (d.get("ETF") or "").strip().upper() != "N":
            continue
        if not _is_common_symbol_nasdaq(sym):
            continue
        name = (d.get("Security Name") or "").strip()
        if not _is_common_stock_by_name(name):
            continue
        if sym in seen:
            continue
        seen.add(sym)
        out.append((sym, name))
    return sorted(out, key=lambda t: t[0])


def filter_nyse(header: Sequence[str], rows: Iterable[Sequence[str]]) -> List[Tuple[str, str]]:
    """Return ``[(symbol, name)]`` of NYSE-proper common stock.

    Filter rules:

    * ``Exchange == "N"`` (NYSE Big Board only — excludes NYSE American,
      NYSE Arca, Cboe BZX).
    * ``Test Issue == "N"``, ``ETF == "N"``.
    * ``Security Name`` does not match any non-common pattern.
    * Symbols with ``$`` (preferred series) are dropped.
    * Symbols with ``.W``/``.WS``/``.U``/``.R``/``.WI`` suffixes are
      dropped (defense-in-depth alongside the name filter).
    * ``.``-to-``-`` munging applied to class-share symbols
      (``BRK.B`` -> ``BRK-B``) for yfinance compatibility.
    """
    out: List[Tuple[str, str]] = []
    seen: set = set()
    bad_dot_suffix = ("W", "WS", "U", "R", "WI")
    for row in rows:
        d = _row_to_dict(header, row)
        if (d.get("Exchange") or "").strip().upper() != "N":
            continue
        if (d.get("Test Issue") or "").strip().upper() != "N":
            continue
        if (d.get("ETF") or "").strip().upper() != "N":
            continue
        raw_sym = (d.get("ACT Symbol") or "").strip().upper()
        if not raw_sym or "$" in raw_sym:
            continue
        if "." in raw_sym:
            head, _, tail = raw_sym.rpartition(".")
            if tail in bad_dot_suffix:
                continue
            sym = f"{head}-{tail}"
        else:
            sym = raw_sym
        name = (d.get("Security Name") or "").strip()
        if not _is_common_stock_by_name(name):
            continue
        if sym in seen:
            continue
        seen.add(sym)
        out.append((sym, name))
    return sorted(out, key=lambda t: t[0])


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_csv(
    path: Path,
    rows: Sequence[Tuple[str, str]],
    *,
    exchange: str,
    snapshot_date: str,
) -> None:
    """Atomic-write a CSV with the normalized 4-column schema.

    Every cell is run through :func:`_safe_csv_cell` so a symbol or
    company name beginning with ``=`` / ``+`` / ``-`` / ``@`` (or
    tab / CR) is prefixed with a single quote and treated as text
    by spreadsheet importers. The defence applies to ALL columns,
    not just ``Name``, because a malicious upstream could in
    principle inject a leading-character symbol too.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_OUTPUT_HEADER)
        for sym, name in rows:
            writer.writerow([
                _safe_csv_cell(sym),
                _safe_csv_cell(name),
                _safe_csv_cell(exchange),
                _safe_csv_cell(snapshot_date),
            ])
    import os
    os.replace(tmp, path)


def diff_summary(
    label: str,
    old_path: Path,
    new_rows: Sequence[Tuple[str, str]],
) -> str:
    """Return a human-readable additions / removals summary."""
    new_syms = {s for s, _ in new_rows}
    old_syms: set = set()
    if old_path.exists():
        with old_path.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                s = (r.get("Symbol") or "").strip()
                if s:
                    old_syms.add(s)
    added = sorted(new_syms - old_syms)
    removed = sorted(old_syms - new_syms)
    lines = [
        f"{label}: {len(new_syms)} symbols ({len(new_syms) - len(old_syms):+d} vs prior).",
    ]
    if added:
        head = ", ".join(added[:8])
        more = f" (+{len(added) - 8} more)" if len(added) > 8 else ""
        lines.append(f"  added:   {head}{more}")
    if removed:
        head = ", ".join(removed[:8])
        more = f" (+{len(removed) - 8} more)" if len(removed) > 8 else ""
        lines.append(f"  removed: {head}{more}")
    return "\n".join(lines)


def update_baskets_constants(
    baskets_py: Path,
    *,
    nyse_date: str,
    nasdaq_date: str,
) -> None:
    """Rewrite NYSE_LAST_REFRESHED / NASDAQ_LAST_REFRESHED in baskets.py.

    Uses regex replacement on the assignment statements; no AST. The
    constants must already exist in the file — this script will not
    add them, only update.
    """
    src = baskets_py.read_text(encoding="utf-8")
    new = re.sub(
        r'(NYSE_LAST_REFRESHED\s*=\s*)"[^"]*"',
        rf'\1"{nyse_date}"',
        src,
    )
    new = re.sub(
        r'(NASDAQ_LAST_REFRESHED\s*=\s*)"[^"]*"',
        rf'\1"{nasdaq_date}"',
        new,
    )
    if new != src:
        baskets_py.write_text(new, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and parse but don't write any files.")
    p.add_argument(
        "--out", default="tools",
        help="Output directory for the CSVs (default: tools/).")
    p.add_argument(
        "--source-nasdaq", default=_NASDAQ_URL,
        help="Override NASDAQ symbol-directory URL.")
    p.add_argument(
        "--source-other", default=_OTHER_URL,
        help="Override otherlisted (NYSE/American/Arca) URL.")
    args = p.parse_args(argv)

    today = _dt.date.today().isoformat()
    out_dir = Path(args.out)

    print(f"Fetching NASDAQ symbol directory from {args.source_nasdaq} ...")
    nasdaq_text = _http_get(args.source_nasdaq)
    n_header, n_rows = _parse_pipe_table(nasdaq_text)
    nasdaq_filtered = filter_nasdaq(n_header, n_rows)
    print(f"  raw rows: {len(n_rows)} -> filtered: {len(nasdaq_filtered)}")

    print(f"Fetching otherlisted (NYSE etc.) from {args.source_other} ...")
    other_text = _http_get(args.source_other)
    o_header, o_rows = _parse_pipe_table(other_text)
    nyse_filtered = filter_nyse(o_header, o_rows)
    print(f"  raw rows: {len(o_rows)} -> filtered: {len(nyse_filtered)}")

    if len(nyse_filtered) < _MIN_ROWS or len(nasdaq_filtered) < _MIN_ROWS:
        print(
            f"ERROR: filtered counts below safety floor "
            f"({_MIN_ROWS}). Refusing to overwrite.\n"
            f"NYSE={len(nyse_filtered)}, NASDAQ={len(nasdaq_filtered)}",
            file=sys.stderr,
        )
        return 1

    nyse_path = out_dir / "nyse.csv"
    nasdaq_path = out_dir / "nasdaq.csv"

    print()
    print(diff_summary("NYSE   ", nyse_path, nyse_filtered))
    print(diff_summary("NASDAQ ", nasdaq_path, nasdaq_filtered))

    if args.dry_run:
        print("\n--dry-run: no files written.")
        return 0

    write_csv(nyse_path, nyse_filtered, exchange="NYSE", snapshot_date=today)
    write_csv(nasdaq_path, nasdaq_filtered, exchange="NASDAQ", snapshot_date=today)
    print(f"\nWrote {nyse_path} ({len(nyse_filtered)} rows).")
    print(f"Wrote {nasdaq_path} ({len(nasdaq_filtered)} rows).")

    baskets_py = Path("src") / "tradinglab" / "baskets.py"
    if baskets_py.exists():
        update_baskets_constants(
            baskets_py, nyse_date=today, nasdaq_date=today)
        print(f"Updated NYSE_LAST_REFRESHED / NASDAQ_LAST_REFRESHED "
              f"in {baskets_py}.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
