"""Per-day watch-note journaling — model round-trip + day-grouping.

Locks in the pure (Tk-free) half of the "daily journal" feature:

* :class:`SessionResult` round-trips ``day_notes`` byte-stably and
  hydrates an empty map from legacy JSON that predates the field.
* :func:`build_day_groups` groups closed trades by UTC session date,
  attaches that day's watch note, surfaces "flat" (note-only) days,
  and assigns a chronological 1-based ordinal for the blind-mode label.

The controller-side capture wiring (``set_day_note`` /
``current_day_note`` / ``result()`` injection) is pinned separately in
``tests/unit/backtest/test_replay_state_machine.py::TestDayNotes``.
"""
from __future__ import annotations

import datetime as _dt

from tradinglab.backtest.journal import DecisionRecord, PostTradeReview
from tradinglab.backtest.performance import (
    DayGroup,
    build_day_groups,
    write_decisions_csv,
)
from tradinglab.backtest.session import SessionResult, SessionSpec


def _ts(year: int, month: int, day: int, hour: int = 14, minute: int = 30) -> int:
    """Epoch seconds for an explicit UTC wall-clock (default 14:30 UTC).

    14:30 UTC == 10:30 ET (EDT) / 09:30 ET (EST), so the UTC calendar
    date equals the trading-session date for regular-hours bars.
    """
    return int(_dt.datetime(year, month, day, hour, minute,
                            tzinfo=_dt.timezone.utc).timestamp())


def _spec() -> SessionSpec:
    return SessionSpec(
        deck_seed=1, tickers=("AAPL",), start_clock_iso="",
        slippage_bps=0.0, commission=0.0,
    )


def _post(entry_ts: int, pnl: float, *, symbol: str = "AAPL",
          ref: str = "o1") -> PostTradeReview:
    return PostTradeReview(
        symbol=symbol, entry_ts=entry_ts, exit_ts=entry_ts + 3600,
        entry_price=10.0, exit_price=10.0 + pnl / 100.0, quantity=100.0,
        side="buy", pnl=pnl, pnl_pct=pnl / 1000.0,
        mae=0.0, mfe=0.0, mae_pct=0.0, mfe_pct=0.0,
        ref_pre_trade_id=ref,
    )


# --------------------------------------------------------------- model round-trip

def test_session_result_day_notes_round_trip():
    r = SessionResult(spec=_spec())
    r.day_notes = {
        "2025-04-29": "SPY pulling back, NVDA holding RS — watching for entry",
        "2025-04-30": "market weak, stood aside",
    }
    r2 = SessionResult.from_dict(r.to_dict())
    assert r2.day_notes == r.day_notes
    # Byte-stable: dumping twice is identical.
    assert r.to_dict()["day_notes"] == r2.to_dict()["day_notes"]


def test_day_notes_back_compat_when_key_absent():
    r = SessionResult(spec=_spec())
    d = r.to_dict()
    del d["day_notes"]
    assert SessionResult.from_dict(d).day_notes == {}


def test_day_notes_default_is_empty_dict():
    assert SessionResult(spec=_spec()).day_notes == {}


def test_decisions_round_trip_and_legacy_default():
    decision = DecisionRecord(
        ts=_ts(2025, 4, 29, 15, 5),
        symbol="AAPL",
        action="watch",
        setup_tag="vwap reclaim",
        confidence=4,
        note="waiting for volume",
    )
    result = SessionResult(spec=_spec(), decisions=[decision])
    payload = result.to_dict()
    assert SessionResult.from_dict(payload).decisions == [decision]
    del payload["decisions"]
    assert SessionResult.from_dict(payload).decisions == []


# ------------------------------------------------------------- build_day_groups

def test_groups_trades_by_utc_date():
    r = SessionResult(
        spec=_spec(),
        post_trades=[
            _post(_ts(2025, 4, 29), 100.0, ref="a"),
            _post(_ts(2025, 4, 29), -40.0, ref="b"),
            _post(_ts(2025, 4, 30), 25.0, ref="c"),
        ],
    )
    groups = build_day_groups(r)
    assert [g.date_iso for g in groups] == ["2025-04-29", "2025-04-30"]
    assert [len(g.rows) for g in groups] == [2, 1]
    # Day-1 rollup: +100 and -40 ⇒ total 60, one win one loss.
    assert groups[0].total_pnl == 60.0
    assert groups[0].wins == 1
    assert groups[0].losses == 1


def test_note_attaches_to_its_day_only():
    r = SessionResult(
        spec=_spec(),
        post_trades=[
            _post(_ts(2025, 4, 29), 10.0, ref="a"),
            _post(_ts(2025, 4, 30), 10.0, ref="b"),
        ],
        day_notes={"2025-04-29": "day-1 thesis"},
    )
    groups = {g.date_iso: g for g in build_day_groups(r)}
    assert groups["2025-04-29"].note == "day-1 thesis"
    assert groups["2025-04-30"].note == ""


def test_flat_note_only_day_appears_with_no_trades():
    r = SessionResult(
        spec=_spec(),
        post_trades=[_post(_ts(2025, 4, 29), 10.0, ref="a")],
        day_notes={
            "2025-04-29": "took AAPL long",
            "2025-05-01": "watched all day, nothing set up — correctly flat",
        },
    )
    groups = {g.date_iso: g for g in build_day_groups(r)}
    assert set(groups) == {"2025-04-29", "2025-05-01"}
    flat = groups["2025-05-01"]
    assert flat.rows == ()
    assert flat.total_pnl == 0.0
    assert flat.note == "watched all day, nothing set up — correctly flat"


def test_ordinal_is_chronological_and_one_based():
    # Notes given out of order; groups must come back sorted with
    # ordinals 1..N in date order.
    r = SessionResult(
        spec=_spec(),
        day_notes={
            "2025-05-02": "c",
            "2025-04-28": "a",
            "2025-04-30": "b",
        },
    )
    groups = build_day_groups(r)
    assert [(g.date_iso, g.ordinal) for g in groups] == [
        ("2025-04-28", 1),
        ("2025-04-30", 2),
        ("2025-05-02", 3),
    ]


def test_empty_result_yields_no_groups():
    assert build_day_groups(SessionResult(spec=_spec())) == []


def test_rows_within_a_day_are_entry_ordered():
    r = SessionResult(
        spec=_spec(),
        post_trades=[
            _post(_ts(2025, 4, 29, 15, 45), 1.0, ref="late"),
            _post(_ts(2025, 4, 29, 9, 40), 2.0, ref="early"),
        ],
    )
    (group,) = build_day_groups(r)
    assert [row.post.ref_pre_trade_id for row in group.rows] == ["early", "late"]
    assert isinstance(group, DayGroup)


def test_decision_only_day_appears_and_decisions_are_ordered():
    late = DecisionRecord(
        ts=_ts(2025, 5, 1, 15, 45),
        symbol="AAPL",
        action="pass",
        setup_tag="late breakout",
        confidence=2,
    )
    early = DecisionRecord(
        ts=_ts(2025, 5, 1, 14, 35),
        symbol="AAPL",
        action="watch",
        setup_tag="opening range",
        confidence=4,
    )
    (group,) = build_day_groups(
        SessionResult(spec=_spec(), decisions=[late, early]))
    assert group.date_iso == "2025-05-01"
    assert group.rows == ()
    assert group.decisions == (early, late)


def test_decisions_csv_is_auditable_and_human_readable(tmp_path):
    decision = DecisionRecord(
        ts=_ts(2025, 4, 29, 15, 5),
        symbol="AAPL",
        action="long",
        setup_tag="vwap reclaim",
        confidence=5,
        note="line one\nline two",
    )
    path = write_decisions_csv(
        [decision], csv_path=tmp_path / "decisions.csv")
    text = path.read_text(encoding="utf-8")
    assert "timestamp,symbol,action,setup_tag,confidence,note" in text
    assert "2025-04-29T15:05:00+00:00" in text
    assert "AAPL,long,vwap reclaim,5,line one line two" in text
    assert str(decision.ts) not in text
