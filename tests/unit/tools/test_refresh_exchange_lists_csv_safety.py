"""Unit tests for refresh_exchange_lists.py CSV safety + read cap.

Two security fixes verified here:

* **L3 — CSV formula injection.** A symbol or company name beginning
  with ``=`` / ``+`` / ``-`` / ``@`` (or tab / CR) would be evaluated
  as a formula on opening the CSV in Excel / Google Sheets / Numbers.
  The fix prefixes such cells with a single quote so the spreadsheet
  engine renders them as literal text.

* **L4 — Read cap.** The HTTP feed reader caps the response at
  16 MB so a hostile or misconfigured upstream cannot stream
  gigabytes into RAM during a manual refresh.

The CSV-safety helper is tested in pure isolation; the read cap is
tested by inspecting the source for the `_MAX_FEED_BYTES` constant
(the actual HTTP call is at module import time and requires
network).
"""
from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

# Load the tools/ module by file path (it's not on the import path).
_TOOLS_DIR = Path(__file__).resolve().parents[3] / "tools"
_SPEC = importlib.util.spec_from_file_location(
    "refresh_exchange_lists_csvsafe", _TOOLS_DIR / "refresh_exchange_lists.py",
)
rel = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rel)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# _safe_csv_cell
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dangerous", ["=", "+", "-", "@", "\t", "\r"])
def test_safe_csv_cell_prefixes_dangerous_leading_chars(dangerous: str) -> None:
    out = rel._safe_csv_cell(dangerous + "SUM(A1:A99)")
    assert out.startswith("'")
    assert out[1:] == dangerous + "SUM(A1:A99)"


def test_safe_csv_cell_passes_safe_strings_unchanged() -> None:
    assert rel._safe_csv_cell("AAPL") == "AAPL"
    assert rel._safe_csv_cell("Apple Inc.") == "Apple Inc."
    assert rel._safe_csv_cell("BRK-A") == "BRK-A"  # leading B is fine
    assert rel._safe_csv_cell("") == ""


def test_safe_csv_cell_coerces_non_strings() -> None:
    assert rel._safe_csv_cell(42) == "42"
    assert rel._safe_csv_cell(3.14) == "3.14"


def test_safe_csv_cell_does_not_double_escape() -> None:
    # If a cell is ALREADY single-quote-prefixed, we don't add
    # another quote — the first char is now ``'``, which is not in
    # the formula-prefix set.
    out = rel._safe_csv_cell("'=SUM(A1)")
    assert out == "'=SUM(A1)"


# ---------------------------------------------------------------------------
# write_csv round-trip: dangerous cells are quoted
# ---------------------------------------------------------------------------


def test_write_csv_escapes_dangerous_company_name(tmp_path: Path) -> None:
    out_path = tmp_path / "out.csv"
    rel.write_csv(
        out_path,
        rows=[
            ("=SUM", "=cmd|'/c calc'!A0"),  # classic CSV-injection payload
            ("AAPL", "Apple Inc."),
        ],
        exchange="N",
        snapshot_date="2026-05-21",
    )
    # Read back as raw text (no csv parser) — verify the literal
    # leading-quote escape made it into the file.
    text = out_path.read_text(encoding="utf-8")
    assert "'=SUM" in text, "dangerous symbol must be prefixed with single quote"
    assert "'=cmd|" in text, "dangerous name must be prefixed with single quote"
    # Innocent rows untouched.
    assert "AAPL" in text
    assert "Apple Inc." in text


def test_write_csv_safe_row_has_no_escape(tmp_path: Path) -> None:
    out_path = tmp_path / "out.csv"
    rel.write_csv(
        out_path,
        rows=[("AAPL", "Apple Inc.")],
        exchange="N",
        snapshot_date="2026-05-21",
    )
    # Parse with csv.reader to verify the row round-trips cleanly.
    with out_path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["Symbol", "Name", "Exchange", "SnapshotDate"]
    assert rows[1] == ["AAPL", "Apple Inc.", "N", "2026-05-21"]


# ---------------------------------------------------------------------------
# Read-cap constant
# ---------------------------------------------------------------------------


def test_max_feed_bytes_is_capped() -> None:
    assert isinstance(rel._MAX_FEED_BYTES, int)
    assert rel._MAX_FEED_BYTES > 0
    # The audit recommended a 16 MB cap. A regression that bumps
    # this to (say) 1 GB should fail this test loudly.
    assert rel._MAX_FEED_BYTES <= 64 * 1024 * 1024
