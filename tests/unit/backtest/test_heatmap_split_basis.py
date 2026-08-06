"""Split-basis tests for heatmap tile sizing.

The bug these pin: tile area was `as-reported shares × back-adjusted
price`. Vendors back-adjust prices for splits *unconditionally* —
yfinance's `auto_adjust=False` only disables **dividend** adjustment —
while `get_shares_full` is genuinely as-reported. So every company that
split after the replay date was drawn smaller by exactly its cumulative
split ratio, and (because a treemap normalises to a unit square) every
company that didn't split inflated to absorb the freed area.

Measured on a 2020-06-01 replay before the fix: NVDA 40x under, AMZN and
GOOGL 20x, TSLA 15x, AAPL 4x — so NVDA drew 0.2% of the basket's area
against a true 3.7%, while MSFT drew 61% against a true 23%. The names
that mattered were slivers and the ones that didn't looked dominant.

The correction lifts the share count onto the price's basis with
`split_factor_after`. The governing property is the sizing analogue of
the no-future-leakage rule, and it is what
`test_split_after_the_clock_does_not_change_size` asserts: **a corporate
action after the replay clock must not change the map.**
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradinglab.backtest.heatmap import scaled_cap, split_factor_after
from tradinglab.backtest.heatmap_provider import (
    HeatmapProvider,
    shares_at_detail_from_series,
)


def _epoch(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


# ---------------------------------------------------------------------------
# split_factor_after
# ---------------------------------------------------------------------------


def test_only_splits_after_the_as_of_date_count() -> None:
    splits = [(_epoch(2019, 1, 1), 2.0), (_epoch(2021, 1, 1), 4.0)]
    assert split_factor_after(splits, _epoch(2020, 6, 1)) == 4.0
    assert split_factor_after(splits, _epoch(2018, 1, 1)) == 8.0
    assert split_factor_after(splits, _epoch(2022, 1, 1)) == 1.0


def test_boundary_split_is_excluded() -> None:
    """A split dated exactly at the observation is already in the count."""
    splits = [(_epoch(2020, 6, 1), 4.0)]
    assert split_factor_after(splits, _epoch(2020, 6, 1)) == 1.0


def test_empty_history_is_a_no_op() -> None:
    assert split_factor_after([], _epoch(2020, 6, 1)) == 1.0


def test_fractional_ratios_are_honoured() -> None:
    """Reverse splits and vendor-recorded spin-offs are ratios too."""
    splits = [(_epoch(2021, 1, 1), 0.25)]
    assert split_factor_after(splits, _epoch(2020, 1, 1)) == pytest.approx(0.25)


def test_garbage_entries_are_skipped_not_fatal() -> None:
    splits = [
        (_epoch(2021, 1, 1), 4.0),
        (_epoch(2021, 2, 1), 0.0),
        (_epoch(2021, 3, 1), -2.0),
        (_epoch(2021, 4, 1), float("nan")),
        ("bad", 2.0),
    ]
    assert split_factor_after(splits, _epoch(2020, 1, 1)) == 4.0


def test_ms_timestamps_are_normalized() -> None:
    splits = [(_epoch(2021, 1, 1) * 1000, 4.0)]
    assert split_factor_after(splits, _epoch(2020, 1, 1) * 1000) == 4.0


# ---------------------------------------------------------------------------
# Observation date, not the clock
# ---------------------------------------------------------------------------


def test_shares_detail_reports_the_observation_timestamp() -> None:
    series = [(_epoch(2020, 1, 1), 100.0), (_epoch(2020, 7, 1), 90.0)]
    assert shares_at_detail_from_series(series, _epoch(2020, 6, 1)) == (
        100.0, False, _epoch(2020, 1, 1)
    )
    # before the series -> carry back, and the observation is the first point
    assert shares_at_detail_from_series(series, _epoch(2015, 1, 1)) == (
        100.0, True, _epoch(2020, 1, 1)
    )
    assert shares_at_detail_from_series([], _epoch(2020, 6, 1)) == (None, True, None)


def _provider(tmp_path, *, shares, splits):
    return HeatmapProvider(
        meta={"X": {"sector": "S", "industry": "I", "cik": "1", "date_added_ts": 0}},
        shares_fetcher=lambda _s: list(shares),
        splits_fetcher=lambda _s: (None if splits is None else list(splits)),
        cache_dir=tmp_path,
    )


def test_factor_is_measured_from_the_filing_not_the_clock(tmp_path) -> None:
    """Filings are quarterly; a split can land between one and the clock.

    Measuring the factor from the replay clock would miss that split and
    leave the count a whole ratio short.
    """
    prov = _provider(
        tmp_path,
        shares=[(_epoch(2020, 1, 1), 100.0)],   # last filing
        splits=[(_epoch(2020, 4, 1), 4.0)],     # split AFTER the filing,
    )                                           # BEFORE the clock
    got, approx = prov.basis_shares_at("X", _epoch(2020, 6, 1))
    assert got == 400.0, "the split between filing and clock must be applied"
    assert approx is False


# ---------------------------------------------------------------------------
# The governing property
# ---------------------------------------------------------------------------


def test_split_after_the_clock_does_not_change_size(tmp_path) -> None:
    """A corporate action after the replay clock must not resize a tile.

    This is the property the old implementation failed. Note the
    *anchoring* half of the rule (factor measured from the filing, not
    the clock) is what
    ``test_factor_is_measured_from_the_filing_not_the_clock`` pins —
    this case alone would also pass under clock-anchoring.
    """
    clock = _epoch(2020, 6, 1)
    shares = [(_epoch(2020, 1, 1), 100.0)]
    price_before_split = 400.0  # price as the vendor served it pre-split

    unsplit = _provider(tmp_path / "a", shares=shares, splits=[])
    size_unsplit = scaled_cap(
        unsplit.basis_shares_at("X", clock)[0], price_before_split
    )

    # Now the same company 4:1 splits AFTER the clock. The vendor
    # back-adjusts its whole price history by 4, and the as-reported
    # share count for the pre-split filing is unchanged.
    split = _provider(
        tmp_path / "b", shares=shares, splits=[(_epoch(2020, 9, 1), 4.0)]
    )
    size_split = scaled_cap(
        split.basis_shares_at("X", clock)[0], price_before_split / 4.0
    )

    assert size_split == pytest.approx(size_unsplit), (
        "a split after the replay clock changed the tile's area"
    )
    assert size_split == pytest.approx(40_000.0)


@pytest.mark.parametrize(
    "symbol,raw_shares,adj_price,factor,true_cap_bn",
    [
        # Real values, replay date 2020-06-01 (yfinance-served adjusted
        # close x as-reported get_shares_full, with the actual split
        # calendar). Before the fix these came out 4x / 40x / 20x small.
        ("AAPL", 4.334e9, 78.27, 4.0, 1356.9),
        ("NVDA", 0.615e9, 8.78, 40.0, 216.0),
        ("AMZN", 0.499e9, 123.62, 20.0, 1233.2),
        ("MSFT", 7.583e9, 175.76, 1.0, 1332.8),  # never split — unchanged
    ],
)
def test_real_world_caps_come_out_right(
    tmp_path, symbol, raw_shares, adj_price, factor, true_cap_bn
) -> None:
    """Anchor the arithmetic to real 2020 market caps.

    The expected values are external facts (AAPL really was worth
    ~$1.36T on 2020-06-01), so dropping the lift fails these — which is
    exactly the regression. It does not test more than "the factor is
    applied"; the windowing rules are covered above.
    """
    prov = _provider(
        tmp_path / symbol,
        shares=[(_epoch(2020, 1, 1), raw_shares)],
        splits=([] if factor == 1.0 else [(_epoch(2020, 9, 1), factor)]),
    )
    lifted, _approx = prov.basis_shares_at(symbol, _epoch(2020, 6, 1))
    cap_bn = scaled_cap(lifted, adj_price) / 1e9
    assert cap_bn == pytest.approx(true_cap_bn, rel=0.01), (
        f"{symbol}: sized {cap_bn:.1f}B against a true {true_cap_bn:.1f}B"
    )


# ---------------------------------------------------------------------------
# Unknown basis is surfaced, never assumed
# ---------------------------------------------------------------------------


def test_unknown_split_history_flags_the_tile_approximate(tmp_path) -> None:
    """Assuming 1.0 on a failed fetch silently restores the old bug."""
    prov = _provider(
        tmp_path, shares=[(_epoch(2020, 1, 1), 100.0)], splits=None
    )
    got, approx = prov.basis_shares_at("X", _epoch(2020, 6, 1))
    assert got == 100.0, "still renders, unlifted"
    assert approx is True, "but must be marked approximate"


def test_no_splits_is_known_and_exact(tmp_path) -> None:
    prov = _provider(tmp_path, shares=[(_epoch(2020, 1, 1), 100.0)], splits=[])
    assert prov.basis_shares_at("X", _epoch(2020, 6, 1)) == (100.0, False)


def test_peek_never_fetches_and_reports_approximate(tmp_path) -> None:
    calls: list[str] = []

    prov = HeatmapProvider(
        meta={"X": {"sector": "S", "industry": "I", "cik": "1", "date_added_ts": 0}},
        shares_fetcher=lambda s: (calls.append(s) or [(_epoch(2020, 1, 1), 100.0)]),
        splits_fetcher=lambda s: (calls.append(s) or []),
        cache_dir=tmp_path,
    )
    assert prov.peek_basis_shares_at("X", _epoch(2020, 6, 1)) == (None, True)
    assert calls == [], "peek must not hit the network"
    prov.prime(["X"])
    assert prov.peek_basis_shares_at("X", _epoch(2020, 6, 1)) == (100.0, False)


def test_carry_back_still_flags_approximate_after_lifting(tmp_path) -> None:
    prov = _provider(
        tmp_path,
        shares=[(_epoch(2020, 1, 1), 100.0)],
        splits=[(_epoch(2020, 9, 1), 4.0)],
    )
    got, approx = prov.basis_shares_at("X", _epoch(2015, 1, 1))
    assert got == 400.0
    assert approx is True, "pre-series carry-back stays approximate"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_splits_cache_round_trips_known_history(tmp_path) -> None:
    prov = HeatmapProvider(
        meta={"X": {"sector": "S", "industry": "I", "cik": "1", "date_added_ts": 0}},
        shares_fetcher=lambda _s: [(_epoch(2020, 1, 1), 100.0)],
        splits_fetcher=lambda _s: [(_epoch(2021, 1, 1), 4.0)],
        cache_dir=tmp_path,
    )
    prov.prime(["X"])
    assert (tmp_path / "splits_cache.json").exists()

    def boom(_sym):
        raise AssertionError("should not fetch; disk cache present")

    fresh = HeatmapProvider(
        meta=prov.meta, shares_fetcher=boom, splits_fetcher=boom, cache_dir=tmp_path
    )
    assert fresh.peek_splits_series("X") == [(_epoch(2021, 1, 1), 4.0)]
    assert fresh.basis_shares_at("X", _epoch(2020, 6, 1)) == (400.0, False)


def test_a_failed_split_fetch_is_never_persisted(tmp_path) -> None:
    """An unknown basis must be retried next launch, not cached forever.

    Persisting the failure would skip the fetch on every future run and
    silently render the unlifted count — reinstating the exact
    under-sizing this correction removes, permanently, behind nothing
    louder than a hatched border. `Ticker.splits` pulls a full per-symbol
    history, so a 500-name prime is very rate-limit-prone; one 429 storm
    must not poison the cache.
    """
    meta = {"X": {"sector": "S", "industry": "I", "cik": "1", "date_added_ts": 0}}
    failing = HeatmapProvider(
        meta=meta,
        shares_fetcher=lambda _s: [(_epoch(2020, 1, 1), 100.0)],
        splits_fetcher=lambda _s: None,          # transient failure
        cache_dir=tmp_path,
    )
    failing.prime(["X"])
    assert failing.basis_shares_at("X", _epoch(2020, 6, 1)) == (100.0, True)

    import json as _json

    cache = tmp_path / "splits_cache.json"
    if cache.exists():
        assert "X" not in _json.loads(cache.read_text(encoding="utf-8")), (
            "an unknown split history must not reach disk"
        )

    # A fresh process with a working fetcher must retry and get it right.
    recovered = HeatmapProvider(
        meta=meta,
        shares_fetcher=lambda _s: [(_epoch(2020, 1, 1), 100.0)],
        splits_fetcher=lambda _s: [(_epoch(2021, 1, 1), 4.0)],
        cache_dir=tmp_path,
    )
    assert recovered.basis_shares_at("X", _epoch(2020, 6, 1)) == (400.0, False)


def test_a_legacy_null_on_disk_is_ignored_not_trusted(tmp_path) -> None:
    (tmp_path / "splits_cache.json").write_text('{"X": null}', encoding="utf-8")
    prov = HeatmapProvider(
        meta={"X": {"sector": "S", "industry": "I", "cik": "1", "date_added_ts": 0}},
        shares_fetcher=lambda _s: [(_epoch(2020, 1, 1), 100.0)],
        splits_fetcher=lambda _s: [(_epoch(2021, 1, 1), 4.0)],
        cache_dir=tmp_path,
    )
    assert prov.basis_shares_at("X", _epoch(2020, 6, 1)) == (400.0, False)


# ---------------------------------------------------------------------------
# The lift only applies when the price series is split-adjusted
# ---------------------------------------------------------------------------


def test_no_lift_when_the_price_series_is_not_split_adjusted(tmp_path) -> None:
    """Alpaca in ``raw`` / ``dividend`` mode serves as-reported prices.

    Lifting the shares against those would over-size every splitter by
    exactly the ratio the correction removes — the mirror image of the
    bug, same magnitude.
    """
    prov = HeatmapProvider(
        meta={"X": {"sector": "S", "industry": "I", "cik": "1", "date_added_ts": 0}},
        shares_fetcher=lambda _s: [(_epoch(2020, 1, 1), 100.0)],
        splits_fetcher=lambda _s: [(_epoch(2021, 1, 1), 4.0)],
        cache_dir=tmp_path,
        price_split_adjusted=False,
    )
    assert prov.basis_shares_at("X", _epoch(2020, 6, 1)) == (100.0, False)
    assert prov.peek_basis_shares_at("X", _epoch(2020, 6, 1)) == (100.0, False)


def test_split_adjustment_flag_tracks_the_source() -> None:
    from tradinglab.data import quality

    assert quality.is_split_adjusted("yfinance") is True
    assert quality.is_split_adjusted("polygon") is True
    assert quality.is_split_adjusted("synthetic") is True
    # unknown / BYOD sources default to adjusted (every real vendor is)
    assert quality.is_split_adjusted("some-future-vendor") is True
