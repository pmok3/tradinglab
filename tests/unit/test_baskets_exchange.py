"""Tests for the NYSE / NASDAQ basket loaders added alongside the
sandbox-preload full-exchange feature.

These exercise both the CSV-on-disk shape (snapshot files shipped at
``tools/{nyse,nasdaq}.csv``) and the in-process loader / registry
surface. We deliberately read the real CSVs rather than mocking — the
loaders are tiny and the on-disk artefacts are what production runs
against.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from tradinglab import baskets


REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# CSV-on-disk shape
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["nyse.csv", "nasdaq.csv"])
def test_exchange_csv_files_exist(name: str) -> None:
    p = REPO_ROOT / "tools" / name
    assert p.is_file(), f"missing snapshot CSV: {p}"


@pytest.mark.parametrize("name", ["nyse.csv", "nasdaq.csv"])
def test_exchange_csv_header_is_canonical(name: str) -> None:
    p = REPO_ROOT / "tools" / name
    with p.open(encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
    assert header == ["Symbol", "Name", "Exchange", "SnapshotDate"]


@pytest.mark.parametrize("name,exchange_value", [
    ("nyse.csv", "NYSE"),
    ("nasdaq.csv", "NASDAQ"),
])
def test_exchange_csv_rows_share_one_exchange(
    name: str, exchange_value: str,
) -> None:
    """Each snapshot file should be homogeneous wrt the Exchange column —
    the refresh script's whole job is to scope NYSE-only or NASDAQ-only,
    so a row from the other exchange leaking in would be a bug."""
    p = REPO_ROOT / "tools" / name
    with p.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows, f"{name} is empty"
    bad = [r for r in rows if r["Exchange"] != exchange_value]
    assert not bad, f"unexpected Exchange values in {name}: {bad[:3]}"


@pytest.mark.parametrize("name", ["nyse.csv", "nasdaq.csv"])
def test_exchange_csv_snapshot_date_is_iso(name: str) -> None:
    p = REPO_ROOT / "tools" / name
    with p.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows
    dates = {r["SnapshotDate"] for r in rows}
    assert len(dates) == 1, f"snapshot date not uniform in {name}: {dates}"
    (date,) = dates
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date), date


@pytest.mark.parametrize("name", ["nyse.csv", "nasdaq.csv"])
def test_exchange_csv_minimum_row_count(name: str) -> None:
    """A populated CSV should have at least 1000 rows. The refresh script
    refuses to write fewer; this is a regression guard against an empty
    or accidentally-truncated checkout."""
    p = REPO_ROOT / "tools" / name
    with p.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) >= 1000, f"{name} has only {len(rows)} rows"


# ---------------------------------------------------------------------------
# Loader behaviour
# ---------------------------------------------------------------------------

def test_nyse_symbols_returns_nonempty_list_of_strings() -> None:
    syms = baskets.nyse_symbols()
    assert syms, "nyse_symbols() returned empty"
    assert all(isinstance(s, str) and s for s in syms)


def test_nasdaq_symbols_returns_nonempty_list_of_strings() -> None:
    syms = baskets.nasdaq_symbols()
    assert syms, "nasdaq_symbols() returned empty"
    assert all(isinstance(s, str) and s for s in syms)


def test_nyse_includes_dual_class_munged_brk() -> None:
    """BRK-A and BRK-B are the canonical dual-class NYSE pair. They
    must appear in the loader's output (the NYSE refresh script
    pre-munges ``.`` to ``-`` so the snapshot already uses yfinance
    form)."""
    syms = set(baskets.nyse_symbols())
    assert "BRK-A" in syms
    assert "BRK-B" in syms


def test_nyse_excludes_obvious_etf_tickers() -> None:
    """SPY (NYSE Arca-listed ETF) should NOT appear in NYSE-proper.
    This pins the refresh-script's ETF=N filter through the loader."""
    syms = set(baskets.nyse_symbols())
    assert "SPY" not in syms


def test_nasdaq_excludes_etfs_and_obvious_funds() -> None:
    """QQQ is a NASDAQ-listed ETF and must be filtered out. AAPL must
    survive as a common stock."""
    syms = set(baskets.nasdaq_symbols())
    assert "QQQ" not in syms
    assert "AAPL" in syms


def test_nasdaq_does_not_dot_munge() -> None:
    """NASDAQ's feed doesn't use ``.`` for dual-class — and the loader
    explicitly disables dot-munging. Any ticker containing ``.`` in
    the CSV would survive unchanged, but we expect none in practice."""
    syms = baskets.nasdaq_symbols()
    # We allow zero or more, but assert none contain '.' (would mean
    # a leaked preferred/warrant from the upstream feed).
    dotted = [s for s in syms if "." in s]
    assert not dotted, f"unexpected dot-suffixed NASDAQ symbols: {dotted[:5]}"


# ---------------------------------------------------------------------------
# Registry surface
# ---------------------------------------------------------------------------

def test_builtin_baskets_registry_contains_four_keys() -> None:
    assert set(baskets.BUILTIN_BASKETS) == {
        "sp500", "qqq", "nyse", "nasdaq"
    }


def test_full_exchange_baskets_contains_nyse_and_nasdaq() -> None:
    assert baskets.FULL_EXCHANGE_BASKETS == frozenset({"nyse", "nasdaq"})


def test_builtin_basket_labels_cover_every_basket() -> None:
    for key in baskets.BUILTIN_BASKETS:
        assert key in baskets.BUILTIN_BASKET_LABELS
        assert baskets.BUILTIN_BASKET_LABELS[key]  # non-empty


def test_builtin_basket_refreshed_dates_covers_dated_baskets() -> None:
    """SP500 ships without a baked-in date (Wikipedia-sourced) so it's
    intentionally absent. The other three must have ISO-format dates."""
    expected_dated = {"qqq", "nyse", "nasdaq"}
    assert set(baskets.BUILTIN_BASKET_REFRESHED_DATES) == expected_dated
    for date in baskets.BUILTIN_BASKET_REFRESHED_DATES.values():
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date), date


def test_nyse_last_refreshed_matches_csv() -> None:
    p = REPO_ROOT / "tools" / "nyse.csv"
    with p.open(encoding="utf-8") as fh:
        row = next(csv.DictReader(fh))
    assert row["SnapshotDate"] == baskets.NYSE_LAST_REFRESHED


def test_nasdaq_last_refreshed_matches_csv() -> None:
    p = REPO_ROOT / "tools" / "nasdaq.csv"
    with p.open(encoding="utf-8") as fh:
        row = next(csv.DictReader(fh))
    assert row["SnapshotDate"] == baskets.NASDAQ_LAST_REFRESHED


def test_registry_callables_are_callable_zero_arg() -> None:
    for key, fn in baskets.BUILTIN_BASKETS.items():
        out = fn()
        assert isinstance(out, list)
        assert out, f"basket {key!r} returned empty list"


def test_loader_raises_filenotfound_for_missing_csv(tmp_path: Path,
                                                    monkeypatch) -> None:
    """The loader must raise FileNotFoundError (not return []) when its
    CSV is missing — strict-offline gating relies on this to fail loud
    rather than silently advertise an empty universe."""
    missing = tmp_path / "ghost.csv"
    with pytest.raises(FileNotFoundError):
        baskets._load_symbols_csv(missing, label="ghost")
