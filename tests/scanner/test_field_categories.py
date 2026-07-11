"""Field concept-category catalog — pure classification + grouping."""
from __future__ import annotations

from tradinglab.scanner import field_categories as FC
from tradinglab.scanner.fields import all_fields


def test_builtin_category_rules():
    assert FC.category_of("close", "builtin") == FC.CAT_PRICE
    assert FC.category_of("volume", "builtin") == FC.CAT_PRICE
    assert FC.category_of("gap_pct", "builtin") == FC.CAT_PRICE
    assert FC.category_of("hod", "builtin") == FC.CAT_SESSION
    assert FC.category_of("bars_since_open", "builtin") == FC.CAT_SESSION
    assert FC.category_of("ha_streak", "builtin") == FC.CAT_HEIKIN
    assert FC.category_of("key_bar_bull", "builtin") == FC.CAT_KEYBARS
    assert FC.category_of("last_bull_key_bar_high", "builtin") == FC.CAT_KEYBARS
    assert FC.category_of("bars_since_bull_key_bar", "builtin") == FC.CAT_KEYBARS


def test_indicator_category_map_and_default():
    assert FC.category_of("ema", "indicator") == FC.CAT_TREND
    assert FC.category_of("adx", "indicator") == FC.CAT_TREND
    assert FC.category_of("rsi", "indicator") == FC.CAT_MOMENTUM
    assert FC.category_of("rvol", "indicator") == FC.CAT_VOLUME
    assert FC.category_of("rrvol", "indicator") == FC.CAT_VOLUME
    assert FC.category_of("bbands", "indicator") == FC.CAT_VOLATILITY
    assert FC.category_of("atr", "indicator") == FC.CAT_VOLATILITY
    # Unknown / user-plugin indicators fall to Other (fail-open).
    assert FC.category_of("totally_custom_thing", "indicator") == FC.CAT_OTHER


def test_grouping_covers_every_field_and_follows_declared_order():
    for kind in ("builtin", "indicator"):
        grouped = FC.grouped_field_ids(kind)
        flat = [fid for _cat, ids in grouped for fid in ids]
        catalog = [s.id for s in all_fields() if s.kind == kind]
        assert sorted(flat) == sorted(catalog)  # nothing dropped or duplicated
        cats = [cat for cat, _ in grouped]
        idx = [FC.FIELD_CATEGORIES.index(c) for c in cats]
        assert idx == sorted(idx)  # category order is a subsequence of the canonical order


def test_grouped_combo_values_headers_precede_members():
    values, headers = FC.grouped_combo_values("builtin")
    assert headers
    assert all(FC.is_category_header(h) for h in headers)
    # First value is always a header (each section leads its members).
    assert FC.is_category_header(values[0])
    # Every non-header value is a real builtin field id, and all are present.
    members = [v for v in values if not FC.is_category_header(v)]
    catalog = {s.id for s in all_fields() if s.kind == "builtin"}
    assert set(members) == catalog


def test_is_category_header():
    hdr = "\u2015\u2015 Trend \u2015\u2015"
    assert FC.is_category_header(hdr)
    assert not FC.is_category_header("close")
    assert not FC.is_category_header("ema")
