"""Tests for the pure helpers in ``tools/refresh_exchange_lists.py``.

The CLI itself is exercised end-to-end by running it against the live
NASDAQ Trader feed — out of scope for unit tests. Here we pin the
filter / parser logic that determines what ends up in the snapshot
CSVs.

No network. No filesystem (except a couple of tmp_path round-trips
for ``write_csv``).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Load the tools/ module by file path (it's not on the import path).
_TOOLS_DIR = Path(__file__).resolve().parents[3] / "tools"
_SPEC = importlib.util.spec_from_file_location(
    "refresh_exchange_lists", _TOOLS_DIR / "refresh_exchange_lists.py",
)
refresh = importlib.util.module_from_spec(_SPEC)
sys.modules["refresh_exchange_lists"] = refresh
_SPEC.loader.exec_module(refresh)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# _parse_pipe_table
# ---------------------------------------------------------------------------

def test_parse_pipe_table_returns_header_and_rows() -> None:
    text = "Symbol|Name\nAAPL|Apple Inc.\nMSFT|Microsoft Corp\n"
    header, rows = refresh._parse_pipe_table(text)
    assert header == ["Symbol", "Name"]
    assert rows == [["AAPL", "Apple Inc."], ["MSFT", "Microsoft Corp"]]


def test_parse_pipe_table_drops_file_creation_footer() -> None:
    text = (
        "Symbol|Name\n"
        "AAPL|Apple Inc.\n"
        "File Creation Time: 0521202611:00|\n"
    )
    header, rows = refresh._parse_pipe_table(text)
    assert header == ["Symbol", "Name"]
    assert rows == [["AAPL", "Apple Inc."]]


def test_parse_pipe_table_drops_blank_and_pipe_less_lines() -> None:
    text = "Symbol|Name\n\nNot a row\nAAPL|Apple\n"
    header, rows = refresh._parse_pipe_table(text)
    assert header == ["Symbol", "Name"]
    assert rows == [["AAPL", "Apple"]]


def test_parse_pipe_table_empty_input() -> None:
    header, rows = refresh._parse_pipe_table("")
    assert header == []
    assert rows == []


# ---------------------------------------------------------------------------
# _is_common_stock_by_name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Apple Inc. - Common Stock", True),
    ("Berkshire Hathaway Inc. Class B", True),
    ("Microsoft Corporation - Common Stock", True),
    # Reject preferreds
    ("Acme Corp Preferred Series A", False),
    # Warrants
    ("Foo Holdings Warrant", False),
    # Units
    ("Bar Acquisition Unit", False),
    # Rights
    ("Baz Inc Rights", False),
    # When-Issued
    ("Qux Co. When-Issued", False),
    # Depositary Shares (preferreds-on-trust)
    ("Quux Bank Depositary Shares", False),
    # Convertible / subordinated / notes — debt-like
    ("ABC Convertible Debenture", False),
    ("DEF Subordinated Notes", False),
    ("GHI 6.5% Notes due 2030", False),
])
def test_is_common_stock_by_name(name: str, expected: bool) -> None:
    assert refresh._is_common_stock_by_name(name) is expected


# ---------------------------------------------------------------------------
# _is_common_symbol_nasdaq
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("symbol,expected", [
    ("AAPL", True),       # 4-char common
    ("GOOGL", True),      # 5-char class-share (L=common class)
    ("BRKB", True),       # alternate spelling, still common
    ("ABCDR", False),     # 5th-char R = rights
    ("ABCDU", False),     # 5th-char U = units
    ("ABCDW", False),     # 5th-char W = warrants
    ("AAPL$A", False),    # $ = preferred series
    ("ABCDY", True),      # Y = ADR, treated as common
    ("ABCDF", True),      # F = foreign, treated as common
])
def test_is_common_symbol_nasdaq(symbol: str, expected: bool) -> None:
    assert refresh._is_common_symbol_nasdaq(symbol) is expected


# ---------------------------------------------------------------------------
# filter_nasdaq
# ---------------------------------------------------------------------------

_NASDAQ_HEADER = [
    "Symbol", "Security Name", "Market Category", "Test Issue",
    "Financial Status", "Round Lot Size", "ETF", "NextShares",
]


def _nasdaq_row(symbol="", name="", test="N", fin="N", etf="N"):
    return [symbol, name, "Q", test, fin, "100", etf, "N"]


def test_filter_nasdaq_keeps_common_stock() -> None:
    rows = [
        _nasdaq_row("AAPL", "Apple Inc. - Common Stock"),
        _nasdaq_row("MSFT", "Microsoft Corporation - Common Stock"),
    ]
    out = refresh.filter_nasdaq(_NASDAQ_HEADER, rows)
    assert [s for s, _ in out] == ["AAPL", "MSFT"]


def test_filter_nasdaq_drops_test_issues() -> None:
    rows = [_nasdaq_row("ZZZT", "Test Issue", test="Y")]
    assert refresh.filter_nasdaq(_NASDAQ_HEADER, rows) == []


def test_filter_nasdaq_drops_non_normal_financial_status() -> None:
    # 'D' = deficient (failing listing requirements). Real production hazard.
    rows = [_nasdaq_row("XYZ", "XYZ Corp - Common Stock", fin="D")]
    assert refresh.filter_nasdaq(_NASDAQ_HEADER, rows) == []


def test_filter_nasdaq_drops_etfs() -> None:
    rows = [_nasdaq_row("QQQ", "Invesco QQQ Trust", etf="Y")]
    assert refresh.filter_nasdaq(_NASDAQ_HEADER, rows) == []


def test_filter_nasdaq_drops_preferreds_by_name() -> None:
    rows = [_nasdaq_row("ABCDF", "ABCD Preferred Series A")]
    assert refresh.filter_nasdaq(_NASDAQ_HEADER, rows) == []


def test_filter_nasdaq_drops_warrants_by_5th_char() -> None:
    rows = [_nasdaq_row("ABCDW", "ABCD Common Stock")]
    assert refresh.filter_nasdaq(_NASDAQ_HEADER, rows) == []


def test_filter_nasdaq_dedupes_symbols() -> None:
    rows = [
        _nasdaq_row("AAPL", "Apple Inc. - Common Stock"),
        _nasdaq_row("AAPL", "Apple Inc. - Common Stock"),
    ]
    out = refresh.filter_nasdaq(_NASDAQ_HEADER, rows)
    assert len(out) == 1


def test_filter_nasdaq_returns_alphabetical_sorted() -> None:
    rows = [
        _nasdaq_row("ZZZ", "Z Corp - Common Stock"),
        _nasdaq_row("AAA", "A Corp - Common Stock"),
        _nasdaq_row("MMM", "M Corp - Common Stock"),
    ]
    out = refresh.filter_nasdaq(_NASDAQ_HEADER, rows)
    assert [s for s, _ in out] == ["AAA", "MMM", "ZZZ"]


# ---------------------------------------------------------------------------
# filter_nyse
# ---------------------------------------------------------------------------

_NYSE_HEADER = [
    "ACT Symbol", "Security Name", "Exchange", "CQS Symbol", "ETF",
    "Round Lot Size", "Test Issue", "NASDAQ Symbol",
]


def _nyse_row(symbol="", name="", exchange="N", test="N", etf="N"):
    return [symbol, name, exchange, symbol, etf, "100", test, ""]


def test_filter_nyse_keeps_only_exchange_n() -> None:
    """Big Board only. Reject NYSE American (A), Arca (P), Cboe (Z)."""
    rows = [
        _nyse_row("AAA", "AAA Corp - Common", exchange="N"),
        _nyse_row("BBB", "BBB Corp - Common", exchange="A"),
        _nyse_row("CCC", "CCC Corp - Common", exchange="P"),
        _nyse_row("DDD", "DDD Corp - Common", exchange="Z"),
    ]
    out = refresh.filter_nyse(_NYSE_HEADER, rows)
    assert [s for s, _ in out] == ["AAA"]


def test_filter_nyse_munges_dot_to_dash_for_dual_class() -> None:
    rows = [_nyse_row("BRK.B", "Berkshire Hathaway Class B Common")]
    out = refresh.filter_nyse(_NYSE_HEADER, rows)
    assert out == [("BRK-B", "Berkshire Hathaway Class B Common")]


def test_filter_nyse_drops_dot_w_dot_ws_dot_u_dot_r_suffixes() -> None:
    for suffix in (".W", ".WS", ".U", ".R", ".WI"):
        rows = [_nyse_row(f"ABC{suffix}", "ABC Corp")]
        assert refresh.filter_nyse(_NYSE_HEADER, rows) == [], (
            f"suffix {suffix!r} should have been dropped"
        )


def test_filter_nyse_drops_dollar_preferred_series() -> None:
    rows = [_nyse_row("ABC$A", "ABC Corp Preferred Series A")]
    assert refresh.filter_nyse(_NYSE_HEADER, rows) == []


def test_filter_nyse_drops_test_issues_and_etfs() -> None:
    rows = [
        _nyse_row("TEST", "Test Issue", test="Y"),
        _nyse_row("SPY", "SPDR S&P 500 ETF", etf="Y"),
    ]
    assert refresh.filter_nyse(_NYSE_HEADER, rows) == []


def test_filter_nyse_drops_preferreds_by_name() -> None:
    rows = [_nyse_row("ABCDF", "ABCD Preferred Series A")]
    assert refresh.filter_nyse(_NYSE_HEADER, rows) == []


# ---------------------------------------------------------------------------
# write_csv
# ---------------------------------------------------------------------------

def test_write_csv_writes_canonical_header_and_rows(tmp_path: Path) -> None:
    out = tmp_path / "x.csv"
    refresh.write_csv(
        out,
        [("AAPL", "Apple Inc."), ("MSFT", "Microsoft Corp")],
        exchange="NASDAQ",
        snapshot_date="2026-05-21",
    )
    text = out.read_text(encoding="utf-8").splitlines()
    assert text[0] == "Symbol,Name,Exchange,SnapshotDate"
    assert text[1] == "AAPL,Apple Inc.,NASDAQ,2026-05-21"
    assert text[2] == "MSFT,Microsoft Corp,NASDAQ,2026-05-21"


def test_write_csv_quotes_names_with_commas(tmp_path: Path) -> None:
    out = tmp_path / "x.csv"
    refresh.write_csv(
        out,
        [("ABC", "Some Co., Inc.")],
        exchange="NYSE",
        snapshot_date="2026-05-21",
    )
    text = out.read_text(encoding="utf-8")
    # The csv module quotes the cell because it contains a comma.
    assert '"Some Co., Inc."' in text
