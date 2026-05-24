"""Tests for the CSV column layout in :mod:`backtest.performance`.

Locks in:
* The human-readable timestamp columns (``entry_iso`` / ``exit_iso``)
  use the prose-style Eastern-Time format ``Month Dth, HH:MM ET``,
  NOT the old integer-epoch ``entry_ts`` / ``exit_ts`` columns and
  NOT the old machine-readable ``YYYY-MM-DDTHH:MM:SS+00:00`` ISO.
* ``CSV_COLUMNS`` no longer contains the legacy integer-second columns
  (``entry_ts`` / ``exit_ts``) — the user-facing report only emits the
  human-readable strings.
* ``_human_et`` returns "" for invalid timestamps, "Month Dth, HH:MM ET"
  otherwise, and the ordinal suffix follows English rules
  (1st/2nd/3rd/4th, 21st/22nd/23rd, 11th/12th/13th).
"""

from __future__ import annotations

from tradinglab.backtest.journal import PostTradeReview
from tradinglab.backtest.performance import (
    CSV_COLUMNS,
    TradeRow,
    _human_et,
    _ordinal,
    trade_row_to_csv_record,
)


def _post(entry_ts: int, exit_ts: int) -> PostTradeReview:
    return PostTradeReview(
        symbol="AMD",
        entry_ts=entry_ts,
        exit_ts=exit_ts,
        entry_price=200.0,
        exit_price=201.0,
        quantity=100.0,
        side="buy",
        pnl=100.0,
        pnl_pct=0.005,
        mae=10.0, mae_pct=-0.0005,
        mfe=50.0, mfe_pct=0.0025,
        ref_pre_trade_id=None,
    )


# ---------------------------------------------------------------------------
# _ordinal — English ordinal suffix rules
# ---------------------------------------------------------------------------


def test_ordinal_basic_units() -> None:
    assert _ordinal(1) == "1st"
    assert _ordinal(2) == "2nd"
    assert _ordinal(3) == "3rd"
    assert _ordinal(4) == "4th"
    assert _ordinal(10) == "10th"


def test_ordinal_teens_always_th() -> None:
    """The 11/12/13/111/112/113 family always uses ``th``, never st/nd/rd."""
    assert _ordinal(11) == "11th"
    assert _ordinal(12) == "12th"
    assert _ordinal(13) == "13th"
    assert _ordinal(111) == "111th"
    assert _ordinal(112) == "112th"
    assert _ordinal(113) == "113th"


def test_ordinal_twenties_through_thirties() -> None:
    assert _ordinal(21) == "21st"
    assert _ordinal(22) == "22nd"
    assert _ordinal(23) == "23rd"
    assert _ordinal(24) == "24th"
    assert _ordinal(31) == "31st"


# ---------------------------------------------------------------------------
# _human_et — prose-style ET formatter
# ---------------------------------------------------------------------------


def test_human_et_renders_known_epoch_second() -> None:
    """1772203800 = 2026-02-27 09:50 ET (winter, EST = UTC-5).

    14:50 UTC → 09:50 ET."""
    s = _human_et(1772203800)
    assert s == "February 27th, 09:50 ET", s


def test_human_et_renders_dst_epoch_second() -> None:
    """A summer timestamp must also render with the same 'ET' suffix —
    we don't switch to 'EDT' during DST (it's deliberately stable).

    1784044800 = 2026-07-14 16:00 UTC → 2026-07-14 12:00 ET (EDT).
    """
    s = _human_et(1784044800)
    assert s == "July 14th, 12:00 ET", s


def test_human_et_returns_empty_on_garbage() -> None:
    assert _human_et(-(10**18)) == ""


def test_human_et_does_not_treat_seconds_as_milliseconds() -> None:
    """Regression: an epoch-second timestamp must NOT be divided by 1000
    or interpreted as milliseconds — that would put every trade in 1970.
    """
    # A ~year-2026 timestamp in seconds (~1.77e9).
    s = _human_et(1772203800)
    assert "1970" not in s, f"timestamp accidentally treated as ms: {s}"
    assert "2026" not in s, "year should NOT appear in human format"
    assert s.startswith("February"), s


# ---------------------------------------------------------------------------
# CSV_COLUMNS layout
# ---------------------------------------------------------------------------


def test_csv_columns_no_longer_contains_integer_second_columns() -> None:
    """User asked: "none of this 1e9 business" — the raw integer-epoch
    columns must be gone from the CSV. Only human-readable strings remain.
    """
    assert "entry_ts" not in CSV_COLUMNS, (
        "entry_ts column removed — was a raw epoch-second integer "
        "('1772203800') the user explicitly asked to drop"
    )
    assert "exit_ts" not in CSV_COLUMNS, (
        "exit_ts column removed for the same reason"
    )


def test_csv_columns_still_has_iso_columns() -> None:
    """The user wants human-readable strings under the existing
    ``entry_iso``/``exit_iso`` column names — chosen so existing
    spreadsheets / clipboard pastes don't shift column positions
    relative to the order_id / holding_seconds anchors."""
    assert "entry_iso" in CSV_COLUMNS
    assert "exit_iso" in CSV_COLUMNS


def test_csv_columns_first_three_are_id_entry_exit() -> None:
    """Column order: id, entry time, exit time, then the rest."""
    assert CSV_COLUMNS[:3] == ("order_id", "entry_iso", "exit_iso")


# ---------------------------------------------------------------------------
# trade_row_to_csv_record — end-to-end CSV record shape
# ---------------------------------------------------------------------------


def test_csv_record_has_human_readable_entry_and_exit() -> None:
    """A real PostTradeReview with second-precision timestamps must
    produce a CSV row whose entry_iso/exit_iso are the human-readable
    Eastern-Time strings, not ISO-8601 and not raw integers."""
    row = TradeRow(post=_post(1772203800, 1772218200))
    rec = trade_row_to_csv_record(row, index=0)
    assert rec["entry_iso"] == "February 27th, 09:50 ET", rec["entry_iso"]
    assert rec["exit_iso"] == "February 27th, 13:50 ET", rec["exit_iso"]
    # The legacy keys must NOT appear in the record:
    assert "entry_ts" not in rec
    assert "exit_ts" not in rec


def test_csv_record_holding_seconds_still_machine_readable() -> None:
    """holding_seconds is a numeric column (seconds delta) — that's
    legitimately useful to spreadsheet users and isn't the "1e9 business"
    the user complained about, so it stays as an integer string."""
    row = TradeRow(post=_post(1772203800, 1772218200))  # 14400s = 4h
    rec = trade_row_to_csv_record(row, index=0)
    assert rec["holding_seconds"] == "14400"
